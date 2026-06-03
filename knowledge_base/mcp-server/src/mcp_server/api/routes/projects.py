"""Project management REST endpoints."""

from __future__ import annotations

import datetime
import asyncio
import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Per-process set of project IDs currently running generation.
# Prevents concurrent generate calls for the same project in the same process.
# Multi-worker deployments rely on the DB status check as the cross-process guard.
_currently_generating: set[str] = set()
_generation_tasks: dict[str, asyncio.Task] = {}

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select, text

from mcp_server.api.permissions import require_project_access
from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.db.models import GenerateRun, Project
from mcp_server.schemas import (
    AddRepoRequest,
    AssessmentGenerateResponse,
    DeepDiveProject,
    DeepDiveProjectsResponse,
    DeepDiveRequest,
    DeleteProjectResponse,
    DocFileInfo,
    DocListResponse,
    DocReview,
    GenerateRequest,
    GenerateRunListResponse,
    GenerateRunResponse,
    GenerateSummariesResponse,
    GraphConnectionInfo,
    JobDetailResponse,
    JobDocSection,
    JobScanMetadata,
    LogFileListResponse,
    LogFileResponse,
    PdfGenerateResponse,
    ProjectGraphResponse,
    ProjectListResponse,
    ProjectResponse,
    ProjectUsageResponse,
    PushToRagRequest,
    ResetStatusRequest,
    ReviewAggregateResponse,
    ScanRequest,
    ScanStatusResponse,
    ScanRunListResponse,
    ScanRunResponse,
    StatusResponse,
    UpdateGithubTokenRequest,
    UpdateProjectRequest,
)
from mcp_server.services.atomic_swap import swap_docs
from mcp_server.services.claude_service import DocGenerationCoordinator
from mcp_server.services.deep_dive_service import DeepDiveService
from mcp_server.services.auto_doc_update import AutoDocUpdateService
from mcp_server.services.pack_loader import PackLoader
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.project_paths import repair_project_repo_path
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.utils.error_sanitizer import sanitize_git_error
from mcp_server.services.scanner_service import ScannerService
from mcp_server.services.summary_service import SummaryService
from mcp_server.services import validation_service

router = APIRouter(tags=["projects"])


def _project_to_response(p) -> ProjectResponse:
    webhook_status: str | None = None
    if p.source_type == "git" and p.repo_url:
        if p.github_webhook_id:
            webhook_status = "active"
        else:
            webhook_status = "not_registered"
    return ProjectResponse(
        id=p.id,
        display_name=p.display_name,
        repo_url=p.repo_url,
        local_path=p.local_path,
        platform=p.platform,
        doc_generation_provider=p.doc_generation_provider,
        source_type=p.source_type,
        status=p.status,
        last_scan_at=p.last_scan_at,
        last_generate_at=p.last_generate_at,
        last_rag_push_at=p.last_rag_push_at,
        job_count=p.job_count,
        doc_count=p.doc_count,
        error_message=sanitize_git_error(p.error_message) if p.error_message else p.error_message,
        last_ingested_commit_sha=p.last_ingested_commit_sha,
        auto_reingest_enabled=p.auto_reingest_enabled,
        auto_reingest_last_checked_at=p.auto_reingest_last_checked_at,
        auto_reingest_error=sanitize_git_error(p.auto_reingest_error) if p.auto_reingest_error else p.auto_reingest_error,
        git_update_summary=p.git_update_summary,
        has_github_token=bool(getattr(p, "github_token_enc", None)),
        webhook_status=webhook_status,
        instruction_pack_dir=getattr(p, "instruction_pack_dir", None),
        plugin_pack_id=getattr(p, "plugin_pack_id", None),
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.post("/add-repo", response_model=ProjectResponse)
async def add_repo(req: AddRepoRequest, request: Request):
    """Register a project (URL, local path, or 'zip')."""
    url = req.url
    path = Path(url)

    if path.exists():
        resolved = path.resolve()
        _allowed_roots = [
            Path(r.strip()).resolve()
            for r in config.allowed_local_roots.split(",")
            if r.strip()
        ]
        if _allowed_roots and not any(resolved.is_relative_to(r) for r in _allowed_roots):
            raise HTTPException(
                status_code=400,
                detail="Local path is not within an allowed root. Set MCP_ALLOWED_LOCAL_ROOTS to permit it.",
            )
        source_type = "local"
        local_path = str(resolved)
        repo_url = None
    elif url.startswith(("http://", "https://", "git@")):
        # Validate URL to prevent command injection
        if not re.match(r"^https?://[^\s;|&]+$", url) and not re.match(r"^git@[^\s;|&]+$", url):
            raise HTTPException(status_code=400, detail="Invalid git URL format")
        source_type = "git"
        # Strip GitHub tree/blob browser suffixes so git clone gets the bare repo URL
        url = re.sub(r"/(tree|blob)/[^/].*$", "", url.rstrip("/"))
        repo_url = url
        clone_dir = config.repos_dir / req.project_id
        if not clone_dir.exists():
            from mcp_server.utils.encryption import inject_token as _inject_token
            _token_enc_tmp: str | None = None
            if req.github_token:
                from mcp_server.utils.encryption import encrypt_token as _encrypt_token
                try:
                    _token_enc_tmp = _encrypt_token(req.github_token)
                except Exception:
                    _token_enc_tmp = None
            clone_url = _inject_token(url, _token_enc_tmp)
            _clone_proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--", clone_url, str(clone_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"},
            )
            try:
                _, _stderr = await asyncio.wait_for(_clone_proc.communicate(), timeout=120.0)
            except asyncio.TimeoutError:
                _clone_proc.kill()
                await _clone_proc.communicate()
                raise HTTPException(status_code=400, detail="Git clone timed out")
            if _clone_proc.returncode != 0:
                logger.warning("Git clone failed for %s: %s", req.project_id, _stderr.decode().strip())
                raise HTTPException(status_code=400, detail="Git clone failed — check the URL and credentials")
        local_path = str(clone_dir)
    elif url == "zip":
        source_type = "zip"
        local_path = str(config.repos_dir / req.project_id)
        repo_url = None
    else:
        raise HTTPException(status_code=400, detail=f"Invalid URL/path: {url}")

    # Encrypt and store the PAT if provided
    token_enc: str | None = None
    if source_type == "git" and req.github_token:
        from mcp_server.utils.encryption import encrypt_token as _encrypt_token
        try:
            token_enc = _encrypt_token(req.github_token)
        except Exception as exc:
            logger.warning("Failed to encrypt GitHub token for %s: %s", req.project_id, exc)

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

        if token_enc:
            project.github_token_enc = token_enc

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
        await db.refresh(project)

    # Auto-register webhook after project is committed (best-effort, non-blocking)
    if source_type == "git" and req.github_token and repo_url and config.public_url and config.github_webhook_secret:
        webhook_url = config.public_url.rstrip("/") + "/webhook/github/webhook"
        try:
            from mcp_server.utils.github_api import register_webhook as _register_webhook
            webhook_id = await _register_webhook(repo_url, req.github_token, webhook_url, config.github_webhook_secret)
            async with async_session_factory() as db2:
                from sqlalchemy import update as _update
                from mcp_server.db.models import Project as _Project
                await db2.execute(
                    _update(_Project)
                    .where(_Project.id == req.project_id)
                    .values(github_webhook_id=webhook_id)
                )
                await db2.commit()
        except Exception as exc:
            logger.warning(
                "Webhook auto-registration failed for %s (project registered without webhook): %s",
                req.project_id, exc,
            )

    async with async_session_factory() as db3:
        registry3 = ProjectRegistry(db3)
        project = await registry3.get_project(req.project_id)
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
    from mcp_server.config import SUPPORTED_PLATFORMS
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(400, f"Unsupported platform: {platform!r}. Must be one of {sorted(SUPPORTED_PLATFORMS)}")

    # Enforce upload size limit before reading the full body
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > config.max_upload_zip_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large (max {config.max_upload_zip_bytes // 1024 // 1024} MB)",
        )

    # Save ZIP
    zip_path = config.uploads_dir / f"{project_id}.zip"
    content = await file.read()
    if len(content) > config.max_upload_zip_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Upload too large (max {config.max_upload_zip_bytes // 1024 // 1024} MB)",
        )
    zip_path.write_bytes(content)

    # Unpack — validate member paths before extraction to prevent zip-slip
    repo_dir = config.repos_dir / project_id
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                member_parts = Path(member.filename).parts
                if member.filename.startswith("/") or ".." in member_parts:
                    raise HTTPException(status_code=400, detail="ZIP contains unsafe path")
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
        await db.refresh(project)
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

        repaired_path = await repair_project_repo_path(project)
        if repaired_path:
            await db.flush()

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

    if req.project_id in _currently_generating:
        raise HTTPException(
            status_code=409,
            detail=f"Generation is already running for project {req.project_id!r}. "
                   "Wait for it to complete or reset the status.",
        )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        result = await db.execute(
            select(Project).where(Project.id == req.project_id).with_for_update()
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {req.project_id}")
        running_count = await db.scalar(
            select(func.count(GenerateRun.id)).where(
                GenerateRun.project_id == req.project_id,
                GenerateRun.status == "running",
            )
        )
        if project.status == "generating" or int(running_count or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Generation is already running for project {req.project_id!r}.",
            )
        await registry.update_status(req.project_id, "generating")
        run = await registry.create_generate_run(req.project_id, triggered_by="api")
        await db.commit()
        run_id = run.id

    _currently_generating.add(req.project_id)
    background_tasks.add_task(
        _run_generate_background, req.project_id, run_id, log_mgr,
        investigation_model=req.investigation_model,
        model=req.model,
        write_model=req.write_model,
        resume=req.resume,
        run_id_for_lock=run_id,
        doc_generation_provider=project.doc_generation_provider,
        instruction_pack_dir=project.instruction_pack_dir,
        enterprise_mode=req.mode == "enterprise",
    )
    return StatusResponse(
        status="generation_triggered",
        message=f"Doc generation started for {req.project_id}",
    )


@router.post("/projects/{project_id}/git-updates/repair-latest", response_model=StatusResponse)
async def repair_latest_git_update(project_id: str, request: Request, background_tasks: BackgroundTasks):
    """Replay the latest stored Git diff through the diff-only patcher."""
    await require_project_access(request, project_id)
    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "git":
            raise HTTPException(status_code=400, detail="Repair latest Git update is only available for git projects")

        summary = project.git_update_summary or {}
        before_sha = str(summary.get("before") or "")
        after_sha = str(summary.get("after") or "")
        if not before_sha or not after_sha:
            raise HTTPException(status_code=400, detail="No stored Git diff is available to repair")

    background_tasks.add_task(
        AutoDocUpdateService(log_mgr=log_mgr).run,
        project_id=project_id,
        before_sha=before_sha,
        after_sha=after_sha,
        trigger="repair_latest",
    )
    return StatusResponse(
        status="repair_triggered",
        message=f"Diff-only repair started for {project_id}",
    )


@router.patch("/projects/{project_id}/github-token", response_model=ProjectResponse)
async def update_github_token(project_id: str, req: UpdateGithubTokenRequest, request: Request):
    """Store a new GitHub PAT for a project and re-register the webhook."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import encrypt_token as _encrypt_token, decrypt_token as _decrypt_token
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    try:
        token_enc = _encrypt_token(req.github_token)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Token encryption failed: {exc}")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if project.source_type != "git":
            raise HTTPException(status_code=400, detail="GitHub token is only applicable to git projects")

        # Delete old webhook if we have one
        if project.github_webhook_id and project.repo_url:
            try:
                from mcp_server.utils.github_api import delete_webhook as _delete_webhook
                await _delete_webhook(project.repo_url, req.github_token, project.github_webhook_id)
            except Exception as exc:
                logger.warning("Failed to delete old webhook %s: %s", project.github_webhook_id, exc)

        await db.execute(
            _update(_Project)
            .where(_Project.id == project_id)
            .values(github_token_enc=token_enc, github_webhook_id=None)
        )
        await db.commit()

    # Re-register webhook
    repo_url = project.repo_url
    if repo_url and config.public_url and config.github_webhook_secret:
        webhook_url = config.public_url.rstrip("/") + "/webhook/github/webhook"
        try:
            from mcp_server.utils.github_api import register_webhook as _register_webhook
            webhook_id = await _register_webhook(repo_url, req.github_token, webhook_url, config.github_webhook_secret)
            async with async_session_factory() as db2:
                await db2.execute(
                    _update(_Project).where(_Project.id == project_id).values(github_webhook_id=webhook_id)
                )
                await db2.commit()
        except Exception as exc:
            logger.warning("Webhook re-registration failed for %s: %s", project_id, exc)

    async with async_session_factory() as db3:
        registry3 = ProjectRegistry(db3)
        project = await registry3.get_project(project_id)
        return _project_to_response(project)


@router.post("/projects/{project_id}/github-webhook/register", response_model=ProjectResponse)
async def register_github_webhook(project_id: str, request: Request):
    """(Re-)register the GitHub webhook using the stored PAT."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import decrypt_token as _decrypt_token
    from mcp_server.utils.github_api import register_webhook as _register_webhook
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        if not project.github_token_enc:
            raise HTTPException(status_code=400, detail="No GitHub token stored. Add one via PATCH /projects/{id}/github-token first.")
        if not project.repo_url:
            raise HTTPException(status_code=400, detail="Project has no repo URL")
        if not config.public_url:
            raise HTTPException(status_code=503, detail="MCP_PUBLIC_URL is not configured")
        if not config.github_webhook_secret:
            raise HTTPException(status_code=503, detail="MCP_GITHUB_WEBHOOK_SECRET is not configured")

    try:
        token = _decrypt_token(project.github_token_enc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Token decryption failed: {exc}")

    webhook_url = config.public_url.rstrip("/") + "/webhook/github/webhook"
    try:
        webhook_id = await _register_webhook(project.repo_url, token, webhook_url, config.github_webhook_secret)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    async with async_session_factory() as db2:
        from sqlalchemy import update as _update2
        from mcp_server.db.models import Project as _Project2
        await db2.execute(
            _update2(_Project2).where(_Project2.id == project_id).values(github_webhook_id=webhook_id)
        )
        await db2.commit()

    async with async_session_factory() as db3:
        registry3 = ProjectRegistry(db3)
        project = await registry3.get_project(project_id)
        return _project_to_response(project)


@router.delete("/projects/{project_id}/github-webhook", response_model=StatusResponse)
async def delete_github_webhook(project_id: str, request: Request):
    """Delete the GitHub webhook and clear the stored webhook ID."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import decrypt_token as _decrypt_token
    from mcp_server.utils.github_api import delete_webhook as _delete_webhook
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        if project.github_webhook_id and project.repo_url and project.github_token_enc:
            try:
                token = _decrypt_token(project.github_token_enc)
                await _delete_webhook(project.repo_url, token, project.github_webhook_id)
            except Exception as exc:
                logger.warning("Failed to delete webhook from GitHub: %s", exc)

        await db.execute(
            _update(_Project).where(_Project.id == project_id).values(github_webhook_id=None)
        )
        await db.commit()

    return StatusResponse(status="deleted", message=f"Webhook removed for project '{project_id}'")


@router.get("/projects/{project_id}/github-webhook/status")
async def get_github_webhook_status(project_id: str, request: Request):
    """Check live webhook status against the GitHub API."""
    await require_project_access(request, project_id)
    from mcp_server.utils.encryption import decrypt_token as _decrypt_token
    from mcp_server.utils.github_api import check_webhook as _check_webhook
    from sqlalchemy import update as _update
    from mcp_server.db.models import Project as _Project

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    if not project.github_webhook_id:
        return {"project_id": project_id, "webhook_status": "not_registered", "webhook_id": None}

    if not project.github_token_enc or not project.repo_url:
        return {"project_id": project_id, "webhook_status": "unknown", "webhook_id": project.github_webhook_id}

    try:
        token = _decrypt_token(project.github_token_enc)
        is_active = await _check_webhook(project.repo_url, token, project.github_webhook_id)
        status = "active" if is_active else "not_registered"
    except Exception as exc:
        logger.warning("Failed to check webhook for %s: %s", project_id, exc)
        status = "unknown"

    # If GitHub says the hook is gone, clear our stored ID
    if status == "not_registered":
        async with async_session_factory() as db2:
            await db2.execute(
                _update(_Project).where(_Project.id == project_id).values(github_webhook_id=None)
            )
            await db2.commit()

    return {"project_id": project_id, "webhook_status": status, "webhook_id": project.github_webhook_id}


_PERMANENT_HTTP_CODES = {400, 401, 403, 404, 422}
_HTTP_STATUS_RE = re.compile(r"\b([45]\d{2})\b")


def _has_permanent_error(errors: list[str]) -> bool:
    """True if any error message contains a non-retryable HTTP status code."""
    for e in errors:
        for m in _HTTP_STATUS_RE.finditer(e):
            if int(m.group(1)) in _PERMANENT_HTTP_CODES:
                return True
    return False


async def _push_with_retry(
    bridge,
    project_id: str,
    output_dir,
    filenames: set[str] | None = None,
    extra_doc_names: set[str] | None = None,
    max_attempts: int = 3,
):
    """Push docs to RAG with exponential backoff retry. Returns (result, errors_or_None)."""
    import asyncio as _asyncio
    last_errors = None
    result: dict = {}
    for attempt in range(max_attempts):
        if attempt > 0:
            await _asyncio.sleep(2 ** attempt)
        result = await bridge.push_docs(
            project_id=project_id,
            output_dir=str(output_dir),
            filenames=filenames,
            extra_doc_names=extra_doc_names,
        )
        if not result.get("errors"):
            return result, None
        last_errors = result["errors"]
        # Permanent errors (auth, CSRF, bad request) won't succeed on retry
        if _has_permanent_error(last_errors):
            break
    return result, last_errors


async def _current_git_head(local_path: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
    except Exception as e:
        logger.warning("Failed reading git HEAD for %s: %s", local_path, e)
    return None


def _read_public_doc_texts(docs_dir: Path) -> dict[str, str]:
    """Return top-level generated docs eligible for RAG indexing."""
    if not docs_dir.exists():
        return {}
    docs: dict[str, str] = {}
    for md_file in sorted(docs_dir.glob("*.md")):
        if not md_file.name[0].isdigit():
            continue
        try:
            docs[md_file.name] = md_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed reading generated doc %s: %s", md_file, e)
    return docs


async def _auto_compare_run(project_id: str, run_id: int) -> None:
    """After a run completes, compare against previous completed run and store verdict."""
    try:
        from mcp_server.api.routes.generate_runs import build_run_comparison
        from sqlalchemy import update as sa_update
        from mcp_server.db.models import GenerateRun

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            runs = await registry.list_generate_runs(project_id)
            baseline = next(
                (r for r in runs if r.status == "completed" and r.id != run_id),
                None,
            )
            if baseline is None:
                return  # first ever completed run — nothing to compare against

        comparison = await build_run_comparison(project_id, baseline.id, run_id)
        verdict = comparison.get("verdict")

        async with async_session_factory() as db:
            await db.execute(
                sa_update(GenerateRun)
                .where(GenerateRun.id == run_id)
                .values(auto_verdict=verdict, auto_baseline_run_id=baseline.id)
            )
            await db.commit()
    except Exception as e:
        logger.warning("Auto-comparison failed for run %s/%s: %s", project_id, run_id, e)


def _diff_generated_docs(old_docs_dir: Path, new_docs_dir: Path) -> tuple[set[str], set[str]]:
    """Compare generated docs and return (added_or_changed, removed) filenames."""
    old_docs = _read_public_doc_texts(old_docs_dir)
    new_docs = _read_public_doc_texts(new_docs_dir)

    changed = {
        filename
        for filename, content in new_docs.items()
        if old_docs.get(filename) != content
    }
    removed = set(old_docs) - set(new_docs)
    return changed, removed


def _load_enterprise_gate_results(base_dir: Path) -> dict | None:
    for candidate in (
        base_dir / "_assessment" / "enterprise_gates.json",
        base_dir / "_audit" / "enterprise_gates.json",
    ):
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            return None
    return None


async def _run_generate_background(
    project_id: str,
    run_id: int,
    log_mgr,
    *,
    investigation_model: str | None = None,
    model: str | None = None,
    write_model: str | None = None,
    resume: bool = True,
    run_id_for_lock: int | None = None,
    doc_generation_provider: str | None = None,
    instruction_pack_dir: str | None = None,
    partial_rag_sync: bool = False,
    enterprise_mode: bool = True,
):
    """Background task: spawn Claude Code for doc generation.

    Uses a staging directory (docs_staging/) so that existing docs survive
    if generation fails.  Only on success is the staging dir swapped in.
    """
    claude = DocGenerationCoordinator(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)
    current_task = asyncio.current_task()
    if current_task is not None:
        _generation_tasks[project_id] = current_task
    try:
        result = await claude.generate_docs(
            project_id, investigation_model=investigation_model, model=model,
            write_model=write_model, resume=resume, run_id=run_id_for_lock or run_id,
            doc_generation_provider=doc_generation_provider,
            instruction_pack_dir=instruction_pack_dir,
            enterprise_mode=enterprise_mode,
        )
    except asyncio.CancelledError:
        await log_mgr.emit(project_id, "Generation cancelled by user.", level="warning")
        result = {
            "success": False,
            "error": "Generation cancelled by user",
            "model_name": model,
            "write_model_name": write_model,
        }
    except Exception as exc:
        logger.exception("Doc generation background task crashed for %s", project_id)
        result = {"success": False, "error": str(exc), "model_name": model, "write_model_name": write_model}

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
        gate_results = _load_enterprise_gate_results(staging_dir) if staging_dir.exists() else None

        if doc_count > 0:
            changed_rag_docs: set[str] | None = None
            removed_rag_docs: set[str] = set()
            if partial_rag_sync:
                changed_rag_docs, removed_rag_docs = _diff_generated_docs(
                    docs_dir, staging_dir
                )

            # Atomic swap with rollback + `docs_prev/` backup kept only during
            # the swap itself (see services/atomic_swap.py). A crash between
            # the two renames is recoverable on next startup via
            # recover_stale_backups().
            swap_docs(output_dir)

            # Preserve Phase 2 analysis files for incremental webhook regen.
            # After the swap, docs/_analysis/ holds the freshest group summaries.
            _analysis_src = docs_dir / "_analysis"
            if _analysis_src.exists():
                _cache = output_dir / "analysis_cache"
                if _cache.exists():
                    shutil.rmtree(_cache)
                shutil.copytree(_analysis_src, _cache)

            # Push new docs to RAG server-side
            bridge = RagBridge(log_manager=log_mgr)
            for filename in sorted(removed_rag_docs):
                try:
                    await bridge.delete_document(project_id, filename)
                    await log_mgr.emit(
                        project_id,
                        f"Deleted stale RAG doc: {filename}",
                        level="info",
                    )
                except Exception as e:
                    await log_mgr.emit(
                        project_id,
                        f"Failed to delete stale RAG doc {filename}: {e}",
                        level="error",
                    )
            # Pack-generated docs use non-numeric names (e.g. TABLE_INVENTORY.md).
            # Include them explicitly so they aren't filtered out by the digit-prefix gate.
            pack_doc_names = {
                f"{name}.md" for name in (result.get("required_docs") or [])
                if not name[0].isdigit()
            }
            _rag_result, rag_errors = await _push_with_retry(
                bridge,
                project_id=project_id,
                output_dir=docs_dir,
                filenames=changed_rag_docs,
                extra_doc_names=pack_doc_names or None,
            )
            if rag_errors:
                await log_mgr.emit(
                    project_id,
                    f"RAG push failed after retries (docs are saved): {rag_errors}",
                    level="error",
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
                    level="error",
                )

            # Score the generated docs and write run_metrics.json
            quality_score: float | None = None
            try:
                from mcp_server.services.doc_quality_scorer import score_project
                quality_report = score_project(project_id, docs_dir=docs_dir)
                quality_score = quality_report.overall

                metrics = {
                    "run_id": run_id,
                    "started_at": result.get("started_at"),
                    "analysis_model": result.get("model_name"),
                    "write_model": result.get("write_model_name"),
                    "provider": doc_generation_provider,
                    "input_tokens": result.get("input_tokens"),
                    "output_tokens": result.get("output_tokens"),
                    "cost_usd": result.get("cost_usd"),
                    "duration_ms": result.get("duration_ms"),
                    "num_turns": result.get("num_turns"),
                    "doc_quality": quality_report.to_dict(),
                }
                # Write to docs/ (latest run, always overwritten)
                (docs_dir / "run_metrics.json").write_text(
                    json.dumps(metrics, indent=2, default=str), encoding="utf-8"
                )
                # Write to logs/ with run_id for historical comparison
                run_metrics_log = config.project_root / "output" / project_id / "logs" / f"run_metrics_{run_id}.json"
                run_metrics_log.parent.mkdir(parents=True, exist_ok=True)
                run_metrics_log.write_text(
                    json.dumps(metrics, indent=2, default=str), encoding="utf-8"
                )
                await log_mgr.emit(
                    project_id,
                    f"Quality score: {quality_score:.2f}/5.0 ({len(quality_report.docs)} docs graded)",
                    level="info",
                )
            except Exception as e:
                logger.error("Doc quality scoring failed: %s", e)

            # Build rag_error_message to persist if push failed
            rag_error_message: str | None = (
                f"RAG push failed after retries: {rag_errors}" if rag_errors else None
            )
            run_verdict = (
                "draft"
                if not enterprise_mode
                else "published_with_rag_error"
                if rag_errors
                else "client_ready"
                if gate_results and gate_results.get("client_ready")
                else "published"
            )
        else:
            # Generation reported success but produced no docs — treat as failure
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            await log_mgr.emit(
                project_id,
                "Generation produced no documents — marking as failed.",
                level="error",
            )
            async with async_session_factory() as db:
                registry = ProjectRegistry(db)
                await registry.update_status(
                    project_id, "error",
                    error_message="Generation produced no documents",
                )
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
                    doc_count=0,
                    model_name=result.get("model_name"),
                    write_model_name=result.get("write_model_name"),
                    quality_score=None,
                    error_message="Generation produced no documents",
                    log_file=log_file,
                    run_verdict="failed",
                    gate_results=_load_enterprise_gate_results(staging_dir) if staging_dir.exists() else None,
                )
                await db.commit()
            _currently_generating.discard(project_id)
            _generation_tasks.pop(project_id, None)
            await log_mgr.emit_done(project_id)
            return

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_status(
                project_id, "ready", error_message=rag_error_message
            )
            await registry.update_generate_result(project_id, doc_count=doc_count)
            if rag_error_message is None and _rag_result.get("pushed", 0) > 0:
                await registry.update_rag_push(project_id)
            project = await registry.get_project(project_id)
            if project and project.source_type == "git" and rag_error_message is None:
                head_sha = await _current_git_head(project.local_path)
                if head_sha:
                    await registry.update_last_ingested_commit(project_id, head_sha)
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
                model_name=result.get("model_name"),
                write_model_name=result.get("write_model_name"),
                quality_score=quality_score,
                error_message=rag_error_message,
                log_file=log_file,
                run_verdict=run_verdict,
                gate_results=gate_results,
            )
            await db.commit()
        await _auto_compare_run(project_id, run_id)
    else:
        # Generation failed — preserve staging for resume (old docs/ remains intact)
        if staging_dir.exists():
            logger.info(
                "Generation failed — staging preserved for resume: %s", staging_dir,
            )
            await log_mgr.emit(
                project_id,
                "Generation failed but staging is preserved. Click Generate again to resume from checkpoint.",
                level="warning",
            )

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_status(
                project_id, "error", error_message=result.get("error") or "Doc generation failed"
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
                doc_count=0,
                model_name=result.get("model_name"),
                write_model_name=result.get("write_model_name"),
                error_message=result.get("error") or "Doc generation failed",
                log_file=log_file,
                run_verdict="failed",
                gate_results=_load_enterprise_gate_results(staging_dir) if staging_dir.exists() else None,
            )
            await db.commit()

    # Signal SSE subscribers that generation is done
    _currently_generating.discard(project_id)
    _generation_tasks.pop(project_id, None)
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
        # Include any non-numbered docs (plugin pack outputs like TABLE_INVENTORY.md)
        pack_docs = {
            f.name for f in docs_dir.glob("*.md")
            if not f.name[0].isdigit() and not f.name.startswith("_")
        }
        bridge = RagBridge(log_manager=log_mgr)
        result = await bridge.push_docs(
            project_id=req.project_id,
            output_dir=str(docs_dir),
            extra_doc_names=pack_docs or None,
        )

        if result["pushed"] > 0:
            await registry.update_rag_push(req.project_id)

        await db.commit()

    return StatusResponse(
        status="completed",
        message=f"Pushed {result['pushed']} docs to RAG. Errors: {len(result['errors'])}",
    )


@router.get("/status/{project_id}", response_model=ScanStatusResponse)
async def get_status(project_id: str, request: Request):
    """Get project scan status."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        cache_age = None
        if project.last_scan_at:
            delta = datetime.datetime.now(datetime.timezone.utc) - project.last_scan_at
            cache_age = round(delta.total_seconds() / 60, 1)

        # Job-count delta: previous completed scan's job_count (if any).
        # Current job_count lives on project.job_count, which is the latest
        # completed scan. Find the one before that.
        previous_job_count: int | None = None
        job_delta: int | None = None
        from mcp_server.db.models import ScanRun  # noqa: WPS433 -- local import keeps startup cost down
        from sqlalchemy import select

        prev_rows = (
            await db.execute(
                select(ScanRun.job_count)
                .where(ScanRun.project_id == project_id)
                .where(ScanRun.status == "completed")
                .order_by(ScanRun.started_at.desc())
                .limit(2)
            )
        ).scalars().all()
        if len(prev_rows) >= 2:
            previous_job_count = int(prev_rows[1])
            job_delta = int(prev_rows[0]) - previous_job_count

        # Read warnings from the most recent scan_result.json, if present.
        warnings: list[dict] = []
        warnings_count = 0
        try:
            scan_result_path = config.intodoc_output_dir / project_id / "scan_result.json"
            if scan_result_path.is_file():
                data = json.loads(scan_result_path.read_text(encoding="utf-8"))
                raw_warnings = data.get("warnings") or []
                if isinstance(raw_warnings, list):
                    # Full count reflects reality; list is capped at 50 for payload size.
                    warnings_count = len(raw_warnings)
                    warnings = [w for w in raw_warnings if isinstance(w, dict)][:50]
        except (OSError, json.JSONDecodeError):
            # Never let a broken scan_result.json hide the rest of the status payload.
            warnings = []
            warnings_count = 0

        return ScanStatusResponse(
            project_id=project.id,
            status=project.status,
            last_scan_at=project.last_scan_at,
            cache_age_minutes=cache_age,
            job_count=project.job_count,
            doc_count=project.doc_count,
            error_message=project.error_message,
            previous_job_count=previous_job_count,
            job_delta=job_delta,
            warnings=warnings,
            warnings_count=warnings_count,
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
            doc_generation_provider=req.doc_generation_provider,
            instruction_pack_dir=req.instruction_pack_dir,
        )
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        await db.commit()
        await db.refresh(project)
        return _project_to_response(project)


@router.delete("/projects/{project_id}", response_model=DeleteProjectResponse)
async def delete_project(project_id: str, request: Request):
    """Permanently delete a project and all associated data.

    Cleans up: RAG documents/vectors (best-effort), filesystem artifacts, DB records.
    Blocked if project is currently scanning or generating.
    """
    await require_project_access(request, project_id)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

        if project.status in ("scanning", "generating"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete project while status is '{project.status}'. "
                       "Reset the status first or wait for the operation to complete.",
            )

        # 1. RAG cleanup (best-effort)
        rag_cleaned = False
        rag_error = None
        try:
            bridge = RagBridge()
            result = await bridge.delete_project_documents(project_id)
            rag_cleaned = True
            logger.info("RAG cleanup for %s: %s", project_id, result)
        except Exception as e:
            rag_error = str(e)
            logger.warning("RAG cleanup failed for %s (continuing): %s", project_id, e)

        # 2. Filesystem cleanup
        files_cleaned = True
        dirs_to_remove = [
            config.repos_dir / project_id,
            config.intodoc_output_dir / project_id,
            config.project_root / "output" / project_id,
        ]
        for d in dirs_to_remove:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                logger.info("Removed directory: %s", d)

        # Remove log files matching {project_id}_*.log
        for log_file in config.logs_dir.glob(f"{project_id}_*.log"):
            try:
                log_file.unlink()
                logger.info("Removed log file: %s", log_file)
            except OSError as e:
                logger.warning("Failed to remove log file %s: %s", log_file, e)
                files_cleaned = False

        # 3. DB cleanup (cascade handles scan_runs, generate_runs)
        deleted = await registry.delete_project(project_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        await db.commit()

    logger.info("Deleted project %s (rag=%s, files=%s)", project_id, rag_cleaned, files_cleaned)
    return DeleteProjectResponse(
        status="deleted",
        message=f"Project '{project_id}' has been permanently deleted.",
        details={
            "rag_cleaned": rag_cleaned,
            "files_cleaned": files_cleaned,
            "rag_error": rag_error,
        },
    )


@router.get("/projects/{project_id}/usage", response_model=ProjectUsageResponse)
async def get_project_usage(project_id: str, request: Request):
    """Get aggregated usage stats (tokens, cost, run counts) for a project."""
    await require_project_access(request, project_id)
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
async def list_scan_runs(project_id: str, request: Request):
    """List all scan runs for a project (newest first)."""
    await require_project_access(request, project_id)
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
async def get_doc_content(
    project_id: str,
    filename: str,
    request: Request,
    source: str = Query("published", pattern="^(published|staging)$"),
):
    """Return raw markdown content for inline preview."""
    await require_project_access(request, project_id)
    if not filename.endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md files can be previewed")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    docs_dir = config.project_root / "output" / project_id / ("docs_staging" if source == "staging" else "docs")
    file_path = docs_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    content = file_path.read_text(encoding="utf-8", errors="replace")
    return {"filename": filename, "content": content}


@router.post("/reset-status", response_model=StatusResponse)
async def reset_status(req: ResetStatusRequest, request: Request):
    """Reset a stuck generating/scanning project back to error state."""
    await require_project_access(request, req.project_id)
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

        was_generating = project.status == "generating"
        cancelled_live_task = False
        if was_generating:
            task = _generation_tasks.pop(req.project_id, None)
            if task is not None and not task.done():
                task.cancel()
                cancelled_live_task = True
            _currently_generating.discard(req.project_id)

        await registry.update_status(
            req.project_id,
            "error",
            error_message="Generation cancelled by user" if was_generating else "Manually reset by user",
        )

        # Mark any running GenerateRun/ScanRun records as failed
        from sqlalchemy import update
        from mcp_server.db.models import GenerateRun, ScanRun

        now = datetime.datetime.now(datetime.timezone.utc)
        await db.execute(
            update(GenerateRun)
            .where(GenerateRun.project_id == req.project_id, GenerateRun.status == "running")
            .values(
                status="failed",
                completed_at=now,
                error_message="Generation cancelled by user" if was_generating else "Manually reset by user",
                run_verdict="failed",
            )
        )
        await db.execute(
            update(ScanRun)
            .where(ScanRun.project_id == req.project_id, ScanRun.status == "running")
            .values(status="failed", completed_at=now)
        )

        await db.commit()

    if cancelled_live_task:
        log_mgr = request.app.state.log_manager
        await log_mgr.emit(req.project_id, "Generation cancellation requested. Stopping active task.", level="warning")
        await log_mgr.emit_done(req.project_id)
        return StatusResponse(status="cancelled", message=f"Generation for '{req.project_id}' was cancelled.")
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
        model_name=getattr(r, "model_name", None),
        write_model_name=getattr(r, "write_model_name", None),
        quality_score=getattr(r, "quality_score", None),
        auto_verdict=getattr(r, "auto_verdict", None),
        auto_baseline_run_id=getattr(r, "auto_baseline_run_id", None),
        run_verdict=getattr(r, "run_verdict", None),
        gate_results=getattr(r, "gate_results", None),
    )


@router.get("/generate-runs/{project_id}", response_model=GenerateRunListResponse)
async def list_generate_runs(project_id: str, request: Request):
    """List all generation runs for a project (newest first)."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        return GenerateRunListResponse(
            runs=[_run_to_response(r) for r in runs],
            total=len(runs),
        )


@router.get("/generate-run-detail/{project_id}/{run_id}")
async def get_generate_run(project_id: str, run_id: int, request: Request):
    """Return a single generation run with per-doc quality breakdown from run_metrics.json."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        runs = await registry.list_generate_runs(project_id)
        run = next((r for r in runs if r.id == run_id), None)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found for project {project_id}")

    base = _run_to_response(run).model_dump()

    metrics_path = config.project_root / "output" / project_id / "logs" / f"run_metrics_{run_id}.json"
    if metrics_path.exists():
        try:
            base["run_metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    return base


@router.get("/compare-runs/{project_id}")
async def compare_generate_runs(
    project_id: str,
    run_a: int,
    run_b: int,
    request: Request,
    quality_drop_pct: float = 5.0,
    size_change_pct: float = 10.0,
):
    """Side-by-side comparison of two generation runs by quality, cost, and token usage."""
    await require_project_access(request, project_id)
    from mcp_server.api.routes.generate_runs import build_run_comparison

    return await build_run_comparison(
        project_id,
        run_a,
        run_b,
        quality_drop_pct=quality_drop_pct,
    )


_QUALITY_REGRESSION_THRESHOLD_PCT = 5.0
_COST_REGRESSION_THRESHOLD_PCT = 20.0
_DURATION_REGRESSION_THRESHOLD_PCT = 20.0


@router.get("/projects/{project_id}/quality-history")
async def get_quality_history(project_id: str, request: Request):
    """Return time-series quality/cost/duration metrics for all completed runs of a project."""
    await require_project_access(request, project_id)
    metrics_dir = config.project_root / "output" / project_id / "logs"
    entries = []
    if metrics_dir.exists():
        for path in sorted(metrics_dir.glob("run_metrics_*.json")):
            try:
                m = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries.append({
                "run_id": m.get("run_id"),
                "started_at": m.get("started_at"),
                "analysis_model": m.get("analysis_model"),
                "write_model": m.get("write_model"),
                "quality": (m.get("doc_quality") or {}).get("overall"),
                "cost_usd": m.get("cost_usd"),
                "duration_ms": m.get("duration_ms"),
                "regression": False,
                "quality_delta_pct": None,
                "cost_regression": False,
                "cost_delta_pct": None,
                "duration_regression": False,
                "duration_delta_pct": None,
            })

    for i, e in enumerate(entries):
        if i == 0:
            continue
        prev = entries[i - 1]

        prev_q = prev["quality"]
        curr_q = e["quality"]
        if prev_q is not None and curr_q is not None and prev_q > 0:
            drop_pct = ((prev_q - curr_q) / prev_q) * 100
            e["regression"] = drop_pct > _QUALITY_REGRESSION_THRESHOLD_PCT
            e["quality_delta_pct"] = round(-drop_pct, 2)

        prev_c = prev["cost_usd"]
        curr_c = e["cost_usd"]
        if prev_c is not None and curr_c is not None and prev_c > 0:
            cost_change_pct = ((curr_c - prev_c) / prev_c) * 100
            e["cost_regression"] = cost_change_pct > _COST_REGRESSION_THRESHOLD_PCT
            e["cost_delta_pct"] = round(cost_change_pct, 2)

        prev_d = prev["duration_ms"]
        curr_d = e["duration_ms"]
        if prev_d is not None and curr_d is not None and prev_d > 0:
            dur_change_pct = ((curr_d - prev_d) / prev_d) * 100
            e["duration_regression"] = dur_change_pct > _DURATION_REGRESSION_THRESHOLD_PCT
            e["duration_delta_pct"] = round(dur_change_pct, 2)

    return {"project_id": project_id, "history": entries, "total": len(entries)}


# --- Validation (US-105) ---

@router.get("/projects/{project_id}/questionnaire")
async def get_questionnaire(project_id: str, request: Request, run_id: int = 0):
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
    pack_dir = getattr(project, "instruction_pack_dir", None) if project else None
    return validation_service.generate_questionnaire(project_id, run_id, instruction_pack_dir=pack_dir)


@router.post("/projects/{project_id}/reviews", status_code=201)
async def submit_review(project_id: str, review: DocReview, request: Request):
    await require_project_access(request, project_id)
    if not (1 <= review.overall <= 10):
        raise HTTPException(status_code=422, detail="overall must be 1–10")
    for q in review.questions:
        if not (1 <= q.score <= 5):
            raise HTTPException(status_code=422, detail=f"score for '{q.id}' must be 1–5")
    output_root = config.project_root / "output"
    path = validation_service.save_review(output_root, project_id, review)
    return {"status": "saved", "path": str(path)}


@router.get("/projects/{project_id}/reviews", response_model=ReviewAggregateResponse)
async def get_reviews(project_id: str, request: Request, run_id: int | None = None):
    await require_project_access(request, project_id)
    output_root = config.project_root / "output"
    return validation_service.aggregate_reviews(output_root, project_id, run_id)


@router.get("/instruction-pack")
async def get_instruction_pack_metadata():
    """Return active instruction pack metadata for dashboard visibility."""
    pack_dir = config.project_root / config.instruction_pack_dir
    pack = PackLoader(pack_dir).load()
    return {
        "path": str(pack.path),
        "relative_path": config.instruction_pack_dir,
        "version": pack.version,
        "exists": pack.path.exists(),
        "is_local": pack.path.is_dir(),
        "supported": pack.path.exists() and pack.path.is_dir(),
        "preambles": [
            {
                "key": key,
                "bytes": len(value.encode("utf-8")),
                "chars": len(value),
            }
            for key, value in sorted(pack.preambles.items())
        ],
    }


@router.get("/logs/{project_id}", response_model=LogFileListResponse)
async def list_log_files(project_id: str, request: Request):
    """List available log files for a project."""
    await require_project_access(request, project_id)
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
async def get_log_content(project_id: str, filename: str, request: Request):
    """Return the content of a specific log file."""
    await require_project_access(request, project_id)
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
async def list_project_docs(
    project_id: str,
    request: Request,
    source: str = Query("published", pattern="^(published|staging)$"),
):
    """List all documentation files for a project with metadata."""
    await require_project_access(request, project_id)
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    if source == "published":
        from mcp_server.services.pdf_service import PdfService

        svc = PdfService()
        docs = svc.list_docs(project_id)
    else:
        docs_dir = config.project_root / "output" / project_id / "docs_staging"
        docs = []
        if docs_dir.is_dir():
            for md_file in sorted(docs_dir.glob("*.md")):
                if md_file.name.startswith("_"):
                    continue
                stat = md_file.stat()
                docs.append({
                    "name": md_file.name,
                    "type": "md",
                    "size_bytes": stat.st_size,
                    "has_pdf": False,
                    "modified_at": datetime.datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=datetime.timezone.utc,
                    ).isoformat(),
                })
    return DocListResponse(
        project_id=project_id,
        docs=[DocFileInfo(**d) for d in docs],
        total=len(docs),
    )


@router.get("/projects/{project_id}/docs/{filename}")
async def download_doc(project_id: str, filename: str, request: Request):
    """Download a specific doc file (md or pdf)."""
    await require_project_access(request, project_id)
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
async def generate_pdfs(project_id: str, request: Request):
    """Generate PDF files for all markdown docs in a project."""
    await require_project_access(request, project_id)
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
async def generate_assessment(project_id: str, request: Request):
    """Generate a self-contained HTML assessment page from project docs."""
    await require_project_access(request, project_id)
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
async def get_assessment(project_id: str, request: Request):
    """Serve the generated assessment HTML page."""
    await require_project_access(request, project_id)
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


# --- Project Graph (Topology) ---


def _derive_systems_from_job_json(job_data: dict, platform: str) -> list[str]:
    """Derive external systems from per-job JSON at request time.

    Used for old scans that predate the job_external_systems manifest field.
    """
    from mcp_server.platforms import get_platform_strategy

    try:
        return get_platform_strategy(platform).derive_external_systems(job_data)
    except ValueError:
        return []


def _derive_direction(path: str, job_name: str) -> str:
    """Derive data-flow direction from job path or naming conventions."""
    path_lower = path.lower().replace("\\", "/")
    # Path-based: Account/In/... or Account/Out/...
    if "/in/" in path_lower:
        return "OnRamp"
    if "/out/" in path_lower:
        return "OffRamp"
    # Name-based conventions
    name_lower = job_name.lower()
    is_on = "onramp" in name_lower or "_onramp_" in name_lower
    is_off = (
        "offramp" in name_lower
        or "_offramp_" in name_lower
        or "_return_" in name_lower
    )
    if is_on and is_off:
        return "Bidirectional"
    if is_on:
        return "OnRamp"
    if is_off:
        return "OffRamp"
    return ""


def _derive_node_summary(
    job_name: str,
    component_types: list[str],
    external_systems: list[str],
    direction: str,
) -> str:
    """Lightweight summary for graph nodes (no file I/O)."""
    parts: list[str] = []
    dir_label = {
        "OnRamp": "Reads from",
        "OffRamp": "Writes to",
        "Bidirectional": "Syncs with",
    }.get(direction, "Connects to")

    if external_systems:
        systems = " and ".join(external_systems[:3])
        parts.append(f"{dir_label} {systems}")
    if component_types:
        notable = [t for t in component_types if t.startswith("t") and t[1:2].isupper()]
        if notable:
            parts.append(f"using {', '.join(notable[:3])}")
    if not parts:
        return ""
    return ". ".join(parts) + "."


_DOC_SUMMARY_HEADINGS = {
    "data flow", "data flows", "lineage", "architecture", "inventory",
    "business logic", "business rules", "overview", "purpose",
}

_MD_NOISE_RE = re.compile(r"\*\*|^\|.*\|$|^[-=]+$|^#+\s*", re.MULTILINE)


def _derive_job_summary(
    job_name: str,
    doc_sections: list[JobDocSection],
    component_types: list[str],
    external_systems: list[str],
) -> str:
    """Rich summary from doc sections for the detail panel."""
    # Try to find a relevant paragraph in doc sections
    for section in doc_sections:
        heading_lower = section.heading.lower()
        if not any(kw in heading_lower for kw in _DOC_SUMMARY_HEADINGS):
            continue
        # Find paragraphs mentioning the job name
        paragraphs = section.content.split("\n\n")
        for para in paragraphs:
            if job_name in para or job_name.lower() in para.lower():
                clean = _MD_NOISE_RE.sub("", para).strip()
                clean = re.sub(r"\s+", " ", clean)
                if len(clean) < 20:
                    continue
                # Take first 2-3 sentences
                sentences = re.split(r"(?<=[.!?])\s+", clean)
                return " ".join(sentences[:3]).strip()

    # Second pass: any section, first substantive paragraph mentioning the job
    for section in doc_sections:
        paragraphs = section.content.split("\n\n")
        for para in paragraphs:
            if job_name not in para and job_name.lower() not in para.lower():
                continue
            clean = _MD_NOISE_RE.sub("", para).strip()
            clean = re.sub(r"\s+", " ", clean)
            if len(clean) < 20:
                continue
            sentences = re.split(r"(?<=[.!?])\s+", clean)
            return " ".join(sentences[:3]).strip()

    # Fallback: synthesize from component data
    if external_systems:
        systems = ", ".join(external_systems[:4])
        return f"Integration job connecting to {systems}."
    if component_types:
        types = ", ".join(component_types[:4])
        return f"Processing job using {types}."
    return ""


# Matches a "**Purpose**: ..." line and captures content up to the next blank
# line or the next bold field (e.g. "**Component Count**:").
_PURPOSE_LINE_RE = re.compile(
    r"^\s*\*\*Purpose\*?\*?\s*:?\s*(.+?)(?=\n\s*\n|\n\s*\*\*|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _extract_job_purpose_from_inventory(
    project_id: str, job_name: str
) -> str | None:
    """Return the verbatim ``**Purpose**`` line for a job from
    ``02_COMPONENT_INVENTORY.md``, or ``None`` if not found.

    Per-job descriptions live in the inventory doc as
    ``#### {job_name}`` followed by a ``**Purpose**: ...`` paragraph.
    Substring-grepping paragraphs misses this because the Purpose line
    does not repeat the job name. We must parse the heading boundary.
    """
    docs_dir = config.project_root / "output" / project_id / "docs"
    if not docs_dir.exists():
        docs_dir = config.project_root / "output" / project_id / "docs_staging"
    inventory_path = docs_dir / "02_COMPONENT_INVENTORY.md"
    if not inventory_path.exists():
        return None
    try:
        content = inventory_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Find the job's heading at any depth (####, ###, ##) and capture the
    # content from the heading up to the next sibling-or-higher heading.
    name_re = re.escape(job_name)
    section_re = re.compile(
        rf"^(#{{2,4}})\s*{name_re}\s*$(.*?)(?=^#{{1,4}}\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = section_re.search(content)
    if not match:
        return None
    body = match.group(2)

    purpose_match = _PURPOSE_LINE_RE.search(body)
    if not purpose_match:
        return None
    purpose = purpose_match.group(1).strip()
    purpose = re.sub(r"\s+", " ", purpose)
    # Keep the first 2-3 sentences max for brevity
    sentences = re.split(r"(?<=[.!?])\s+", purpose)
    return " ".join(sentences[:3]).strip() or None


def _extract_job_doc_sections(
    project_id: str, job_name: str
) -> list[JobDocSection]:
    """Return doc sections whose heading matches the job name exactly,
    bounded by the next sibling-or-higher heading.

    Replaces the previous ``## ``-only split which dragged in the entire
    inventory section for any job click.
    """
    docs_dir = config.project_root / "output" / project_id / "docs"
    if not docs_dir.exists():
        docs_dir = config.project_root / "output" / project_id / "docs_staging"
    if not docs_dir.exists():
        return []

    name_re = re.escape(job_name)
    # Match a heading whose line is the job name (allowing trailing junk)
    section_re = re.compile(
        rf"^(#{{2,4}})\s*{name_re}\b(.*)$",
        re.MULTILINE,
    )
    any_heading_re = re.compile(r"^#{1,6}\s", re.MULTILINE)

    sections: list[JobDocSection] = []
    for md_path in sorted(docs_dir.glob("*.md")):
        try:
            content = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for m in section_re.finditer(content):
            depth = len(m.group(1))
            heading_line = m.group(0).lstrip("#").strip()
            # Find the end: next heading at same depth or shallower
            tail = content[m.end():]
            end = len(tail)
            for h in any_heading_re.finditer(tail):
                hash_count = 0
                pos = h.start()
                while pos < len(tail) and tail[pos] == "#":
                    hash_count += 1
                    pos += 1
                if hash_count <= depth:
                    end = h.start()
                    break
            body = tail[:end].strip()
            if body:
                doc_label = md_path.stem.replace("_", " ").lstrip("0123456789 ")
                sections.append(
                    JobDocSection(
                        heading=f"{doc_label} - {heading_line}",
                        content=body,
                    )
                )

    return sections


@router.get("/projects/{project_id}/graph", response_model=ProjectGraphResponse)
async def get_project_graph(project_id: str, request: Request):
    """Return manifest + connections data for topology visualization.

    Reads external systems from manifest.job_external_systems (populated by
    the scanner). Falls back to runtime derivation for old scans.
    """
    await require_project_access(request, project_id)

    # Look up platform for fallback derivation on old scans
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
    platform = project.platform if project else "talend"

    scan_dir = config.intodoc_output_dir / project_id / "scan"
    manifest_path = scan_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"No scan data for project: {project_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Load connections.json
    connections: list[GraphConnectionInfo] = []
    connections_path = scan_dir / "connections.json"
    if connections_path.exists():
        raw = json.loads(connections_path.read_text(encoding="utf-8"))
        seen: set[str] = set()
        for c in raw:
            name = c.get("name", "")
            if name and name not in seen:
                seen.add(name)
                connections.append(GraphConnectionInfo(
                    name=name,
                    connection_type=c.get("connection_type", ""),
                    host=c.get("host", ""),
                    database=c.get("database", ""),
                ))

    # Check if docs exist for this project
    docs_dir = config.project_root / "output" / project_id / "docs"
    has_docs = docs_dir.exists() and any(docs_dir.glob("*.md"))

    # Read external systems from manifest (populated by scanner)
    job_external_systems: dict[str, list[str]] = manifest.get("job_external_systems", {})
    needs_runtime_derivation = not job_external_systems

    # Build job_connections_used / connection_used_by from external systems
    # if the manifest doesn't already have connection edges
    job_connections_used: dict[str, list[str]] = manifest.get("job_connections_used", {})
    connection_used_by: dict[str, list[str]] = manifest.get("connection_used_by", {})
    derive_edges = not job_connections_used

    # Load AI-generated summaries (empty dict if not yet generated)
    ai_summaries = SummaryService.load_summaries(project_id)

    job_groups: dict[str, str] = manifest.get("job_groups", {})
    jobs_dir = scan_dir / "jobs"
    job_metadata: dict[str, JobScanMetadata] = {}
    all_system_names: set[str] = set()

    for job_name, group in job_groups.items():
        ext_systems = job_external_systems.get(job_name, [])

        # Extract component types from per-job JSON
        comp_types: list[str] = []
        comp_count = 0
        purpose = ""
        author = ""
        modification_date = ""
        job_data: dict | None = None

        job_path = jobs_dir / group / f"{job_name}.json"
        if job_path.exists():
            try:
                job_data = json.loads(job_path.read_text(encoding="utf-8"))
                purpose = job_data.get("purpose", "")
                author = job_data.get("author", "")
                modification_date = job_data.get("modification_date", "")
                components = job_data.get("components", [])
                comp_count = len(components)
                seen_types: set[str] = set()
                for comp in components:
                    ctype = comp.get("component_type", "")
                    if ctype and ctype not in seen_types:
                        seen_types.add(ctype)
                        comp_types.append(ctype)
            except (json.JSONDecodeError, OSError):
                pass

        # Fallback: derive systems at request time for old scans
        if needs_runtime_derivation and not ext_systems and job_data:
            ext_systems = _derive_systems_from_job_json(job_data, platform)

        job_path_str = str(job_path) if job_path.exists() else ""
        direction = _derive_direction(job_path_str, job_name)
        # Prefer AI summary, fall back to heuristic
        node_summary = ai_summaries.get(job_name) or _derive_node_summary(
            job_name, comp_types, ext_systems, direction,
        )

        job_metadata[job_name] = JobScanMetadata(
            purpose=purpose,
            author=author,
            modification_date=modification_date,
            component_count=comp_count,
            component_types=comp_types,
            subgroup=group,
            has_docs=has_docs,
            external_systems=ext_systems,
            summary=node_summary,
            direction=direction,
        )

        # Synthesize connection edges from external systems
        if derive_edges and ext_systems:
            job_connections_used[job_name] = ext_systems
            for sys_name in ext_systems:
                all_system_names.add(sys_name)
                if sys_name not in connection_used_by:
                    connection_used_by[sys_name] = []
                if job_name not in connection_used_by[sys_name]:
                    connection_used_by[sys_name].append(job_name)

    # If we derived edges, update manifest and add synthetic connection entries
    if derive_edges and all_system_names:
        manifest["job_connections_used"] = job_connections_used
        manifest["connection_used_by"] = connection_used_by

        existing_conn_names = {c.name for c in connections}
        for sys_name in sorted(all_system_names):
            if sys_name not in existing_conn_names:
                connections.append(GraphConnectionInfo(
                    name=sys_name,
                    connection_type="derived",
                ))

    return ProjectGraphResponse(
        project_id=project_id,
        manifest=manifest,
        connections=connections,
        job_metadata=job_metadata,
    )


@router.get("/projects/{project_id}/jobs/{job_name}/detail", response_model=JobDetailResponse)
async def get_job_detail(project_id: str, job_name: str, request: Request):
    """Return full scan data and relevant doc sections for a specific job."""
    await require_project_access(request, project_id)

    # Sanitize job_name
    if "/" in job_name or "\\" in job_name or ".." in job_name:
        raise HTTPException(status_code=400, detail="Invalid job name")

    scan_dir = config.intodoc_output_dir / project_id / "scan"
    manifest_path = scan_dir / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"No scan data for project: {project_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    job_groups: dict[str, str] = manifest.get("job_groups", {})
    group = job_groups.get(job_name, "")

    if not group:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_name}")

    # Read job JSON
    job_path = scan_dir / "jobs" / group / f"{job_name}.json"
    scan_data: dict = {}
    if job_path.exists():
        try:
            scan_data = json.loads(job_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Find doc sections whose heading matches this job exactly
    doc_sections = _extract_job_doc_sections(project_id, job_name)

    # Derive direction and summary
    job_path_str = str(job_path) if job_path.exists() else ""
    direction = _derive_direction(job_path_str, job_name)

    comp_types = list({
        c.get("component_type", "")
        for c in scan_data.get("components", [])
        if c.get("component_type")
    })
    ext_systems = list({
        s for c in scan_data.get("components", [])
        for s in ([c.get("external_system")] if c.get("external_system") else [])
    })
    # Prefer the structured Purpose line from 02_COMPONENT_INVENTORY.md.
    # Fall back to the legacy heuristic for jobs missing from the inventory.
    summary = (
        _extract_job_purpose_from_inventory(project_id, job_name)
        or _derive_job_summary(job_name, doc_sections, comp_types, ext_systems)
        or ""
    )

    return JobDetailResponse(
        project_id=project_id,
        job_name=job_name,
        group=group,
        scan_data=scan_data,
        doc_sections=doc_sections,
        summary=summary,
        direction=direction,
    )


# --- AI Summaries ---


@router.post(
    "/projects/{project_id}/generate-summaries",
    response_model=GenerateSummariesResponse,
)
async def generate_summaries(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Generate AI-powered job summaries using Ollama (runs in background)."""
    await require_project_access(request, project_id)

    scan_dir = config.intodoc_output_dir / project_id / "scan"
    if not (scan_dir / "manifest.json").exists():
        return GenerateSummariesResponse(
            project_id=project_id, status="no_scan",
        )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
    platform = project.platform if project else "talend"

    background_tasks.add_task(_run_summaries_background, project_id, platform)

    return GenerateSummariesResponse(
        project_id=project_id, status="started",
    )


async def _run_summaries_background(project_id: str, platform: str) -> None:
    """Background task: generate AI summaries for all jobs."""
    svc = SummaryService()
    try:
        result = await svc.generate_project_summaries(project_id, platform)
        logger.info(
            "Summary generation for %s: %d summaries, %d errors",
            project_id, result["summary_count"], result["error_count"],
        )
    except Exception as e:
        logger.error("Summary generation failed for %s: %s", project_id, e)


# --- Deep Dive ---


@router.post("/deep-dive")
async def deep_dive_query(req: DeepDiveRequest, request: Request):
    """Stream deep-dive analysis via Claude Code CLI (SSE)."""
    await require_project_access(request, req.project_id)
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
                        provider=data.get("provider", "anthropic"),
                        query_text=req.question[:500] if req.question else None,
                    )
                except Exception:
                    pass  # Don't break the SSE stream

            # JSON-encode all SSE payloads, including strings. Deep Dive answers
            # often contain markdown newlines; raw multiline data breaks the
            # frontend's line-based SSE parser.
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/deep-dive/projects", response_model=DeepDiveProjectsResponse)
async def deep_dive_projects(request: Request):
    """List projects available for deep dive, filtered by user access."""
    auth_payload = getattr(request.state, "auth_payload", None)

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        all_projects = await registry.list_projects()

        scanned = [
            p for p in all_projects
            if p.status in ("ready", "scanned") and p.job_count > 0
        ]

        if auth_payload:
            user_id = int(auth_payload.get("sub", 0))
            is_superadmin = await _get_user_is_superadmin(db, user_id)
            if not is_superadmin:
                perms = await _get_user_permission_codenames(db, user_id)
                if "view_all_projects" not in perms:
                    assigned_ids = await _get_assigned_project_ids(db, user_id)
                    scanned = [p for p in scanned if p.id in assigned_ids]

        available = [
            DeepDiveProject(
                id=p.id,
                display_name=p.display_name,
                platform=p.platform,
                job_count=p.job_count,
                last_scan_at=p.last_scan_at,
            )
            for p in scanned
        ]

        return DeepDiveProjectsResponse(projects=available, total=len(available))
