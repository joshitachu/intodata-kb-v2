"""Project management REST endpoints."""

from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import text

from mcp_server.api.permissions import require_project_access
from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.schemas import (
    AddRepoRequest,
    AssessmentGenerateResponse,
    DeepDiveProject,
    DeepDiveProjectsResponse,
    DeepDiveRequest,
    DocFileInfo,
    DocListResponse,
    GenerateRequest,
    GenerateRunListResponse,
    GenerateRunResponse,
    LogFileListResponse,
    LogFileResponse,
    PdfGenerateResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectUsageResponse,
    PushToRagRequest,
    ResetStatusRequest,
    ScanRequest,
    ScanStatusResponse,
    ScanRunListResponse,
    ScanRunResponse,
    StatusResponse,
    UpdateProjectRequest,
)
from mcp_server.services.claude_service import ClaudeService
from mcp_server.services.deep_dive_service import DeepDiveService
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
async def add_repo(req: AddRepoRequest, request: Request):
    """Register a project (URL, local path, or 'zip')."""
    url = req.url
    path = Path(url)

    if path.exists():
        source_type = "local"
        local_path = str(path.resolve())
        repo_url = None
    elif url.startswith(("http://", "https://", "git@")):
        # Validate URL to prevent command injection
        if not re.match(r"^https?://[^\s;|&]+$", url) and not re.match(r"^git@[^\s;|&]+$", url):
            raise HTTPException(status_code=400, detail="Invalid git URL format")
        source_type = "git"
        repo_url = url
        clone_dir = config.repos_dir / req.project_id
        if not clone_dir.exists():
            import subprocess
            try:
                subprocess.run(
                    ["git", "clone", "--", url, str(clone_dir)],
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

        # Auto-assign creator to the new project so they can see it
        auth_payload = getattr(request.state, "auth_payload", None)
        if auth_payload:
            user_id = int(auth_payload.get("sub", 0))
            if user_id:
                await db.execute(
                    text(
                        "INSERT INTO project_assignments (user_id, project_id, assigned_at, created_at, updated_at) "
                        "VALUES (:uid, :pid, NOW(), NOW(), NOW()) "
                        "ON CONFLICT (user_id, project_id) DO NOTHING"
                    ),
                    {"uid": user_id, "pid": req.project_id},
                )

        await db.commit()
        return _project_to_response(project)


@router.post("/upload-zip", response_model=ProjectResponse)
async def upload_zip(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    platform: str = Form("talend"),
):
    """Upload a ZIP file, unpack, register, and trigger scan."""
    project_id = project_id.strip()
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", project_id) or len(project_id) > 100:
        raise HTTPException(400, "Invalid project_id: alphanumeric, hyphens, underscores only")
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

        # Auto-assign creator so they can see the project immediately
        auth_payload = getattr(request.state, "auth_payload", None)
        if auth_payload:
            user_id = int(auth_payload.get("sub", 0))
            if user_id:
                await db.execute(
                    text(
                        "INSERT INTO project_assignments (user_id, project_id, assigned_at, created_at, updated_at) "
                        "VALUES (:uid, :pid, NOW(), NOW(), NOW()) "
                        "ON CONFLICT (user_id, project_id) DO NOTHING"
                    ),
                    {"uid": user_id, "pid": project_id},
                )

        await db.commit()
        return _project_to_response(project)


@router.post("/scan", response_model=StatusResponse)
async def scan_project(req: ScanRequest, request: Request, background_tasks: BackgroundTasks):
    """Trigger an `intodoc scan` for a project (runs in background)."""
    await require_project_access(request, req.project_id)
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
    await require_project_access(request, req.project_id)
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
    """Background task: spawn Claude Code for doc generation.

    Uses a staging directory (docs_staging/) so that existing docs survive
    if generation fails.  Only on success is the staging dir swapped in.
    """
    claude = ClaudeService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)
    result = await claude.generate_docs(project_id)

    success = result.get("success", False)

    output_dir = config.project_root / "output" / project_id
    staging_dir = output_dir / "docs_staging"
    docs_dir = output_dir / "docs"

    # Get log file path
    log_path = log_mgr.get_current_log_path(project_id)
    log_file = log_path.name if log_path else None

    if success:
        # Count docs produced in staging
        doc_count = len(list(staging_dir.glob("*.md"))) if staging_dir.exists() else 0

        if doc_count > 0:
            # Atomic swap: remove old docs, rename staging → docs
            if docs_dir.exists():
                shutil.rmtree(docs_dir, ignore_errors=True)
            staging_dir.rename(docs_dir)

            # Push new docs to RAG server-side
            bridge = RagBridge(log_manager=log_mgr)
            try:
                await bridge.push_docs(project_id=project_id, output_dir=str(docs_dir))
            except Exception as e:
                await log_mgr.emit(
                    project_id,
                    f"RAG push failed (docs are saved): {e}",
                    level="warning",
                )

            # Persist token usage to RAG backend
            try:
                await bridge.post_usage(
                    interface="doc_generation",
                    project_id=project_id,
                    input_tokens=result.get("input_tokens"),
                    output_tokens=result.get("output_tokens"),
                    cost_usd=result.get("cost_usd"),
                    latency_ms=result.get("duration_ms"),
                    model_name=result.get("model_name", "claude"),
                    provider="anthropic",
                )
            except Exception as e:
                logger.warning("Failed to post doc_generation usage: %s", e)

            # Generate assessment HTML from the new docs
            try:
                from mcp_server.services.assessment_service import AssessmentService

                svc = AssessmentService()
                await svc.generate(project_id)
            except Exception as e:
                await log_mgr.emit(
                    project_id,
                    f"Assessment generation failed: {e}",
                    level="warning",
                )
        else:
            # Generation "succeeded" but produced no docs — clean staging
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_status(project_id, "ready")
            await registry.update_generate_result(project_id, doc_count=doc_count)
            await registry.complete_generate_run(
                run_id,
                status="completed",
                cost_usd=result.get("cost_usd"),
                duration_ms=result.get("duration_ms"),
                duration_api_ms=result.get("duration_api_ms"),
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                num_turns=result.get("num_turns"),
                session_id=result.get("session_id"),
                doc_count=doc_count,
                error_message=None,
                log_file=log_file,
            )
            await db.commit()
    else:
        # Generation failed — clean staging, preserve old docs
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_status(
                project_id, "error", error_message="Doc generation failed"
            )
            # Keep existing doc_count — old docs are still on disk
            await registry.complete_generate_run(
                run_id,
                status="failed",
                cost_usd=result.get("cost_usd"),
                duration_ms=result.get("duration_ms"),
                duration_api_ms=result.get("duration_api_ms"),
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                num_turns=result.get("num_turns"),
                session_id=result.get("session_id"),
                doc_count=None,
                error_message="Doc generation failed",
                log_file=log_file,
            )
            await db.commit()

    # Signal SSE subscribers that generation is done
    await log_mgr.emit_done(project_id)


@router.post("/push-to-rag", response_model=StatusResponse)
async def push_to_rag(req: PushToRagRequest, request: Request):
    """Push generated .md files from output dir to RAG pipeline."""
    await require_project_access(request, req.project_id)
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


async def _get_user_permission_codenames(db, user_id: int) -> set[str]:
    """Fetch permission codenames for a user via raw SQL (avoids importing RAG models)."""
    result = await db.execute(text("""
        SELECT DISTINCT p.codename
        FROM permissions p
        JOIN role_permissions rp ON rp.permission_id = p.id
        JOIN user_roles ur ON ur.role_id = rp.role_id
        WHERE ur.user_id = :uid
    """), {"uid": user_id})
    return {row[0] for row in result.fetchall()}


async def _get_user_is_superadmin(db, user_id: int) -> bool:
    result = await db.execute(
        text("SELECT is_superadmin FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    row = result.fetchone()
    return bool(row and row[0])


async def _get_assigned_project_ids(db, user_id: int) -> set[str]:
    result = await db.execute(
        text("SELECT project_id FROM project_assignments WHERE user_id = :uid"),
        {"uid": user_id},
    )
    return {row[0] for row in result.fetchall()}


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(request: Request):
    """List projects, filtered by assignment if user lacks view_all_projects."""
    auth_payload = getattr(request.state, "auth_payload", None)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        all_projects = await registry.list_projects()

        # If no auth payload, return all (shouldn't happen due to require_auth)
        if not auth_payload:
            return ProjectListResponse(
                projects=[_project_to_response(p) for p in all_projects],
                total=len(all_projects),
            )

        user_id = int(auth_payload.get("sub", 0))
        is_superadmin = await _get_user_is_superadmin(db, user_id)

        if is_superadmin:
            return ProjectListResponse(
                projects=[_project_to_response(p) for p in all_projects],
                total=len(all_projects),
            )

        perms = await _get_user_permission_codenames(db, user_id)
        if "view_all_projects" in perms:
            return ProjectListResponse(
                projects=[_project_to_response(p) for p in all_projects],
                total=len(all_projects),
            )

        # Filter to assigned projects only
        assigned_ids = await _get_assigned_project_ids(db, user_id)
        filtered = [p for p in all_projects if p.id in assigned_ids]
        return ProjectListResponse(
            projects=[_project_to_response(p) for p in filtered],
            total=len(filtered),
        )


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, req: UpdateProjectRequest, request: Request):
    """Update editable project fields (display_name, platform)."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.update_project(
            project_id,
            display_name=req.display_name,
            platform=req.platform,
        )
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        await db.commit()
        return _project_to_response(project)


@router.get("/projects/{project_id}/usage", response_model=ProjectUsageResponse)
async def get_project_usage(project_id: str):
    """Get aggregated usage stats (tokens, cost, run counts) for a project."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        # Aggregate generate runs
        gen_result = await db.execute(text("""
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE status = 'completed')::int AS completed,
                COALESCE(SUM(input_tokens), 0)::bigint AS input_tokens,
                COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
                COALESCE(SUM(cost_usd), 0.0) AS cost_usd
            FROM generate_runs
            WHERE project_id = :pid
        """), {"pid": project_id})
        gen_row = gen_result.fetchone()

        # Aggregate scan runs
        scan_result = await db.execute(text("""
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE status = 'completed')::int AS completed
            FROM scan_runs
            WHERE project_id = :pid
        """), {"pid": project_id})
        scan_row = scan_result.fetchone()

        return ProjectUsageResponse(
            project_id=project_id,
            total_generate_runs=gen_row[0] if gen_row else 0,
            completed_generate_runs=gen_row[1] if gen_row else 0,
            total_input_tokens=int(gen_row[2]) if gen_row else 0,
            total_output_tokens=int(gen_row[3]) if gen_row else 0,
            total_cost_usd=float(gen_row[4]) if gen_row else 0.0,
            total_scan_runs=scan_row[0] if scan_row else 0,
            completed_scan_runs=scan_row[1] if scan_row else 0,
        )


@router.get("/scan-runs/{project_id}", response_model=ScanRunListResponse)
async def list_scan_runs(project_id: str):
    """List all scan runs for a project (newest first)."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_scan_runs(project_id)
        return ScanRunListResponse(
            runs=[
                ScanRunResponse(
                    id=r.id,
                    project_id=r.project_id,
                    triggered_by=r.triggered_by,
                    status=r.status,
                    started_at=r.started_at,
                    completed_at=r.completed_at,
                    job_count=r.job_count,
                    log_file=r.log_file,
                )
                for r in runs
            ],
            total=len(runs),
        )


@router.get("/projects/{project_id}/docs/{filename}/content")
async def get_doc_content(project_id: str, filename: str):
    """Return raw markdown content for inline preview."""
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files can be previewed")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    docs_dir = config.project_root / "output" / project_id / "docs"
    file_path = docs_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    content = file_path.read_text(encoding="utf-8", errors="replace")
    return {"filename": filename, "content": content}


@router.post("/reset-status", response_model=StatusResponse)
async def reset_status(req: ResetStatusRequest):
    """Reset a stuck generating/scanning project back to error state."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")

        if project.status not in ("generating", "scanning"):
            raise HTTPException(
                status_code=400,
                detail=f"Project status is '{project.status}', not generating/scanning. Nothing to reset.",
            )

        await registry.update_status(
            req.project_id, "error", error_message="Manually reset by user"
        )

        # Mark any running GenerateRun/ScanRun records as failed
        from sqlalchemy import update
        from mcp_server.db.models import GenerateRun, ScanRun

        now = datetime.datetime.now(datetime.timezone.utc)
        await db.execute(
            update(GenerateRun)
            .where(GenerateRun.project_id == req.project_id, GenerateRun.status == "running")
            .values(status="failed", completed_at=now, error_message="Manually reset by user")
        )
        await db.execute(
            update(ScanRun)
            .where(ScanRun.project_id == req.project_id, ScanRun.status == "running")
            .values(status="failed", completed_at=now)
        )

        await db.commit()

    return StatusResponse(status="reset", message=f"Project '{req.project_id}' status reset to error.")


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


# --- Documentation (PDF) ---


@router.get("/projects/{project_id}/docs", response_model=DocListResponse)
async def list_project_docs(project_id: str):
    """List all documentation files for a project with metadata."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.pdf_service import PdfService

    svc = PdfService()
    docs = svc.list_docs(project_id)
    return DocListResponse(
        project_id=project_id,
        docs=[DocFileInfo(**d) for d in docs],
        total=len(docs),
    )


@router.get("/projects/{project_id}/docs/{filename}")
async def download_doc(project_id: str, filename: str):
    """Download a specific doc file (md or pdf)."""
    # Validate extension
    if not filename.endswith((".md", ".pdf")):
        raise HTTPException(status_code=400, detail="Only .md and .pdf files are supported")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.pdf_service import PdfService

    svc = PdfService()
    path = svc.get_doc_path(project_id, filename)
    if not path:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    media_type = "application/pdf" if filename.endswith(".pdf") else "text/markdown; charset=utf-8"
    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=filename,
    )


@router.post("/projects/{project_id}/generate-pdfs", response_model=PdfGenerateResponse)
async def generate_pdfs(project_id: str):
    """Generate PDF files for all markdown docs in a project."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.pdf_service import PdfService

    svc = PdfService()
    result = svc.convert_project(project_id)

    status = "completed" if result["failed"] == 0 else "partial"
    if result["converted"] == 0 and result["failed"] > 0:
        status = "failed"

    return PdfGenerateResponse(
        project_id=project_id,
        status=status,
        converted=result["converted"],
        failed=result["failed"],
        errors=result["errors"],
    )


# --- Assessment ---


@router.post(
    "/projects/{project_id}/generate-assessment",
    response_model=AssessmentGenerateResponse,
)
async def generate_assessment(project_id: str):
    """Generate a self-contained HTML assessment page from project docs."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from mcp_server.services.assessment_service import AssessmentService

    svc = AssessmentService()
    result = await svc.generate(project_id)

    return AssessmentGenerateResponse(**result)


@router.get("/projects/{project_id}/assessment")
async def get_assessment(project_id: str):
    """Serve the generated assessment HTML page."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    html_path = config.project_root / "output" / project_id / "assessment" / "index.html"
    if not html_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Assessment not generated yet. Call POST /generate-assessment first.",
        )

    return FileResponse(
        path=str(html_path),
        media_type="text/html",
    )


# --- Deep Dive ---


@router.post("/deep-dive")
async def deep_dive_query(req: DeepDiveRequest):
    """Stream deep-dive analysis via Claude Code CLI (SSE)."""
    # Validate project exists and has scan data
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")
        if project.status not in ("ready", "scanned") or project.job_count == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Project '{req.project_id}' has no scan data (status={project.status}, jobs={project.job_count})",
            )

    service = DeepDiveService()
    bridge = RagBridge()

    async def event_stream():
        async for event in service.query(req.project_id, req.question, session_id=req.session_id):
            event_type = event.get("type", "message")
            data = event.get("data", "")

            # Intercept usage events to persist token counts
            if event_type == "usage" and isinstance(data, dict):
                try:
                    await bridge.post_usage(
                        interface="deep_dive",
                        project_id=req.project_id,
                        input_tokens=data.get("input_tokens"),
                        output_tokens=data.get("output_tokens"),
                        cost_usd=data.get("cost_usd"),
                        latency_ms=data.get("duration_ms") or data.get("latency_ms"),
                        model_name=data.get("model", "claude"),
                        provider="anthropic",
                        query_text=req.question[:500] if req.question else None,
                    )
                except Exception:
                    pass  # Don't break the SSE stream

            if isinstance(data, dict):
                data = json.dumps(data)
            yield f"event: {event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/deep-dive/projects", response_model=DeepDiveProjectsResponse)
async def deep_dive_projects():
    """List projects available for deep dive (scanned with job data)."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        all_projects = await registry.list_projects()

        available = [
            DeepDiveProject(
                id=p.id,
                display_name=p.display_name,
                platform=p.platform,
                job_count=p.job_count,
                last_scan_at=p.last_scan_at,
            )
            for p in all_projects
            if p.status in ("ready", "scanned") and p.job_count > 0
        ]

        return DeepDiveProjectsResponse(projects=available, total=len(available))
