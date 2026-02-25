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
    GenerateRunListResponse,
    GenerateRunResponse,
    LogFileListResponse,
    LogFileResponse,
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

    output_dir = str(config.intodoc_output_dir / req.project_id)
    background_tasks.add_task(
        _run_scan_background, req.project_id, local_path, platform, run_id, log_mgr, output_dir
    )

    return StatusResponse(status="scan_triggered", message=f"Scan started for {req.project_id}")


async def _run_scan_background(
    project_id: str,
    local_path: str,
    platform: str,
    run_id: int,
    log_mgr,
    output_dir: str,
):
    """Background task: run scan and update DB."""
    scanner = ScannerService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)

    result = await scanner.run_scan(
        project_id=project_id,
        repo_path=local_path,
        platform=platform,
        output_dir=output_dir,
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
        run = await registry.create_generate_run(req.project_id, triggered_by="api")
        await db.commit()
        run_id = run.id

    background_tasks.add_task(_run_generate_background, req.project_id, run_id, log_mgr)
    return StatusResponse(
        status="generation_triggered",
        message=f"Doc generation started for {req.project_id}",
    )


async def _run_generate_background(project_id: str, run_id: int, log_mgr):
    """Background task: spawn Claude Code for doc generation."""
    claude = ClaudeService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)
    result = await claude.generate_docs(project_id)

    success = result.get("success", False)

    # Count generated docs
    docs_dir = config.project_root / "output" / project_id / "docs"
    doc_count = len(list(docs_dir.glob("*.md"))) if docs_dir.exists() else 0

    # Get log file path
    log_path = log_mgr.get_current_log_path(project_id)
    log_file = log_path.name if log_path else None

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if success:
            await registry.update_status(project_id, "ready")
            await registry.update_generate_result(project_id, doc_count=doc_count)
        else:
            await registry.update_status(project_id, "error", error_message="Doc generation failed")

        await registry.complete_generate_run(
            run_id,
            status="completed" if success else "failed",
            cost_usd=result.get("cost_usd"),
            duration_ms=result.get("duration_ms"),
            duration_api_ms=result.get("duration_api_ms"),
            input_tokens=result.get("input_tokens"),
            output_tokens=result.get("output_tokens"),
            num_turns=result.get("num_turns"),
            session_id=result.get("session_id"),
            doc_count=doc_count,
            error_message=None if success else "Doc generation failed",
            log_file=log_file,
        )
        await db.commit()

    # Signal SSE subscribers that generation is done
    await log_mgr.emit_done(project_id)


@router.post("/push-to-rag", response_model=StatusResponse)
async def push_to_rag(req: PushToRagRequest, request: Request):
    """Push generated .md files from output dir to RAG pipeline."""
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")

        docs_dir = config.project_root / "output" / req.project_id / "docs"
        if not docs_dir.exists():
            raise HTTPException(status_code=404, detail=f"No docs found at {docs_dir}")
        bridge = RagBridge(log_manager=log_mgr)
        result = await bridge.push_docs(project_id=req.project_id, output_dir=str(docs_dir))

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


def _run_to_response(r) -> GenerateRunResponse:
    return GenerateRunResponse(
        id=r.id,
        project_id=r.project_id,
        triggered_by=r.triggered_by,
        status=r.status,
        started_at=r.started_at,
        completed_at=r.completed_at,
        cost_usd=r.cost_usd,
        duration_ms=r.duration_ms,
        duration_api_ms=r.duration_api_ms,
        input_tokens=r.input_tokens,
        output_tokens=r.output_tokens,
        num_turns=r.num_turns,
        session_id=r.session_id,
        doc_count=r.doc_count,
        error_message=r.error_message,
        log_file=r.log_file,
    )


@router.get("/generate-runs/{project_id}", response_model=GenerateRunListResponse)
async def list_generate_runs(project_id: str):
    """List all generation runs for a project (newest first)."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        return GenerateRunListResponse(
            runs=[_run_to_response(r) for r in runs],
            total=len(runs),
        )


@router.get("/logs/{project_id}", response_model=LogFileListResponse)
async def list_log_files(project_id: str):
    """List available log files for a project."""
    logs_dir = config.logs_dir
    files: list[LogFileResponse] = []
    for log_path in sorted(logs_dir.glob(f"{project_id}_*.log"), reverse=True):
        # Extract timestamp from filename: {project_id}_{YYYYMMDD_HHMMSS}.log
        name = log_path.stem  # e.g. "myproject_20260224_143000"
        ts_part = name[len(project_id) + 1:]  # e.g. "20260224_143000"
        files.append(LogFileResponse(
            filename=log_path.name,
            project_id=project_id,
            timestamp=ts_part,
            size_bytes=log_path.stat().st_size,
        ))
    return LogFileListResponse(files=files, total=len(files))


@router.get("/logs/{project_id}/{filename}")
async def get_log_content(project_id: str, filename: str):
    """Return the content of a specific log file."""
    # Sanitize filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    log_path = config.logs_dir / filename
    if not log_path.exists() or not filename.startswith(project_id):
        raise HTTPException(status_code=404, detail="Log file not found")
    content = log_path.read_text(encoding="utf-8", errors="replace")
    return {"filename": filename, "content": content}
