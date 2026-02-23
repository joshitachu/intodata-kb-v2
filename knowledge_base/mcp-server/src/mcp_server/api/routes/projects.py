"""Project management REST endpoints."""

from __future__ import annotations

import datetime
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File, Form

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.schemas import (
    AddRepoRequest,
    GenerateRequest,
    ProjectListResponse,
    ProjectResponse,
    PushToRagRequest,
    ScanRequest,
    ScanStatusResponse,
    StatusResponse,
)
from mcp_server.services.claude_service import ClaudeService
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.scanner_service import ScannerService

router = APIRouter(tags=["projects"])


def _project_to_response(p) -> ProjectResponse:
    return ProjectResponse(
        id=p.id,
        display_name=p.display_name,
        repo_url=p.repo_url,
        local_path=p.local_path,
        platform=p.platform,
        source_type=p.source_type,
        status=p.status,
        last_scan_at=p.last_scan_at,
        last_generate_at=p.last_generate_at,
        last_rag_push_at=p.last_rag_push_at,
        job_count=p.job_count,
        doc_count=p.doc_count,
        error_message=p.error_message,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.post("/add-repo", response_model=ProjectResponse)
async def add_repo(req: AddRepoRequest):
    """Register a project (URL, local path, or 'zip')."""
    url = req.url
    path = Path(url)

    if path.exists():
        source_type = "local"
        local_path = str(path.resolve())
        repo_url = None
    elif url.startswith(("http://", "https://", "git@")):
        source_type = "git"
        repo_url = url
        clone_dir = config.repos_dir / req.project_id
        if not clone_dir.exists():
            import subprocess
            try:
                subprocess.run(
                    ["git", "clone", url, str(clone_dir)],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                raise HTTPException(status_code=400, detail=f"Git clone failed: {e.stderr.decode()}")
        local_path = str(clone_dir)
    elif url == "zip":
        source_type = "zip"
        local_path = str(config.repos_dir / req.project_id)
        repo_url = None
    else:
        raise HTTPException(status_code=400, detail=f"Invalid URL/path: {url}")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.add_project(
            project_id=req.project_id,
            local_path=local_path,
            display_name=req.display_name,
            repo_url=repo_url,
            platform=req.platform,
            source_type=source_type,
        )
        await db.commit()
        return _project_to_response(project)


@router.post("/upload-zip", response_model=ProjectResponse)
async def upload_zip(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    platform: str = Form("talend"),
):
    """Upload a ZIP file, unpack, register, and trigger scan."""
    project_id = project_id.strip()
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip")

    # Save ZIP
    zip_path = config.uploads_dir / f"{project_id}.zip"
    content = await file.read()
    zip_path.write_bytes(content)

    # Unpack
    repo_dir = config.repos_dir / project_id
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(repo_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.add_project(
            project_id=project_id,
            local_path=str(repo_dir),
            platform=platform,
            source_type="zip",
        )
        await db.commit()
        return _project_to_response(project)


@router.post("/scan", response_model=StatusResponse)
async def scan_project(req: ScanRequest, request: Request, background_tasks: BackgroundTasks):
    """Trigger an `intodoc scan` for a project (runs in background)."""
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")

        await registry.update_status(req.project_id, "scanning")
        run = await registry.create_scan_run(req.project_id, triggered_by="api")
        await db.commit()

        local_path = project.local_path
        platform = project.platform
        run_id = run.id

    background_tasks.add_task(
        _run_scan_background, req.project_id, local_path, platform, run_id, log_mgr
    )

    return StatusResponse(status="scan_triggered", message=f"Scan started for {req.project_id}")


async def _run_scan_background(
    project_id: str,
    local_path: str,
    platform: str,
    run_id: int,
    log_mgr,
):
    """Background task: run scan and update DB."""
    scanner = ScannerService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)

    result = await scanner.run_scan(
        project_id=project_id,
        repo_path=local_path,
        platform=platform,
    )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if result["success"]:
            await registry.update_scan_result(
                project_id,
                scan_result_path=result["scan_result_path"],
                output_dir=result["output_dir"],
                job_count=result["job_count"],
            )
            await registry.complete_scan_run(
                run_id, status="completed", job_count=result["job_count"]
            )
        else:
            await registry.update_status(
                project_id, "error", error_message=result.get("error")
            )
            await registry.complete_scan_run(run_id, status="failed")
        await db.commit()


@router.post("/generate", response_model=StatusResponse)
async def generate_docs(req: GenerateRequest, request: Request, background_tasks: BackgroundTasks):
    """Spawn Claude Code CLI to generate docs (runs in background)."""
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")
        await registry.update_status(req.project_id, "generating")
        await db.commit()

    background_tasks.add_task(_run_generate_background, req.project_id, log_mgr)
    return StatusResponse(
        status="generation_triggered",
        message=f"Doc generation started for {req.project_id}",
    )


async def _run_generate_background(project_id: str, log_mgr):
    """Background task: spawn Claude Code for doc generation."""
    claude = ClaudeService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)
    success = await claude.generate_docs(project_id)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if success:
            await registry.update_status(project_id, "ready")
        else:
            await registry.update_status(project_id, "error", error_message="Doc generation failed")
        await db.commit()


@router.post("/push-to-rag", response_model=StatusResponse)
async def push_to_rag(req: PushToRagRequest, request: Request):
    """Push generated .md files from output dir to RAG pipeline."""
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")

        output_dir = project.output_dir or str(config.intodoc_output_dir)
        bridge = RagBridge(log_manager=log_mgr)
        result = await bridge.push_docs(project_id=req.project_id, output_dir=output_dir)

        if result["pushed"] > 0:
            await registry.update_rag_push(req.project_id)

        await db.commit()

    return StatusResponse(
        status="completed",
        message=f"Pushed {result['pushed']} docs to RAG. Errors: {len(result['errors'])}",
    )


@router.get("/status/{project_id}", response_model=ScanStatusResponse)
async def get_status(project_id: str):
    """Get project scan status."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        cache_age = None
        if project.last_scan_at:
            delta = datetime.datetime.now(datetime.timezone.utc) - project.last_scan_at
            cache_age = round(delta.total_seconds() / 60, 1)

        return ScanStatusResponse(
            project_id=project.id,
            status=project.status,
            last_scan_at=project.last_scan_at,
            cache_age_minutes=cache_age,
            job_count=project.job_count,
            doc_count=project.doc_count,
            error_message=project.error_message,
        )


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects():
    """List all registered projects."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        projects = await registry.list_projects()
        return ProjectListResponse(
            projects=[_project_to_response(p) for p in projects],
            total=len(projects),
        )
