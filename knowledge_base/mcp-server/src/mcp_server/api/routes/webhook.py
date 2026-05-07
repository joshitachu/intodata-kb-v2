"""GitHub webhook handler - HMAC-verified, no JWT required."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.git_reingestion import (
    ChangedFile,
    parse_name_status_diff as _parse_name_status_diff,
    resolve_changed_groups as _resolve_changed_groups,
    setup_incremental_staging as _setup_incremental_staging,
)
from mcp_server.services.git_diff_patcher import run_webhook_diff_update
from mcp_server.services.scanner_service import ScannerService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])


@router.post("/webhook/github/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle GitHub push/pull_request events.

    Verifies HMAC-SHA256 signature, looks up the project by clone_url, then
    triggers a background scan for pull requests or diff-only doc patch for pushes.
    """
    # Read raw bytes first; HMAC must be computed before JSON parsing.
    raw_body = await request.body()

    # Verify GitHub signature
    secret = config.github_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    expected_sig = "sha256=" + hmac.new(
        secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    actual_sig = request.headers.get("X-GitHub-Signature-256", "")
    if not hmac.compare_digest(actual_sig, expected_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    body = json.loads(raw_body)
    repo = body.get("repository", {})
    clone_url = repo.get("clone_url", "")

    if not clone_url:
        return {"status": "ignored", "reason": "No clone_url in payload"}

    event = request.headers.get("X-GitHub-Event", "")

    if event == "ping":
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await _find_project_by_clone_url(registry, clone_url)

        if project:
            log_mgr = request.app.state.log_manager
            await log_mgr.emit(
                project.id,
                f"GitHub webhook ping received for {clone_url}",
                level="info",
            )
            return {
                "status": "ping_received",
                "registered": True,
                "project_id": project.id,
            }

        return {"status": "ping_received", "registered": False}

    if event not in ("push", "pull_request"):
        return {"status": "ignored", "reason": f"Unsupported event: {event}"}

    # Find project by repo_url
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await _find_project_by_clone_url(registry, clone_url)

        if not project:
            return {"status": "ignored", "reason": f"No project registered for {clone_url}"}

        project_id = project.id
        local_path = project.local_path
        platform = project.platform
        if event == "pull_request":
            await registry.update_status(project.id, "scanning")
            run = await registry.create_scan_run(project.id, triggered_by="webhook")
            await db.commit()
            run_id = run.id
        else:
            run_id = 0

    # For push events, capture the before/after SHAs for diff analysis
    is_push = event == "push"
    before_sha = body.get("before", "") if is_push else ""
    after_sha = body.get("after", "") if is_push else ""
    is_first_push = before_sha == "0" * 40

    log_mgr = request.app.state.log_manager
    background_tasks.add_task(
        _webhook_background,
        project_id, local_path, platform, run_id, log_mgr,
        is_push=is_push,
        before_sha=before_sha,
        after_sha=after_sha,
        is_first_push=is_first_push,
    )

    return {"status": "webhook_triggered", "project_id": project_id}


async def _find_project_by_clone_url(registry: ProjectRegistry, clone_url: str):
    """Find a registered project whose repo URL matches GitHub's clone_url."""
    projects = await registry.list_projects()
    normalized_clone_url = clone_url.removesuffix(".git")
    for project in projects:
        if not project.repo_url:
            continue
        normalized_repo_url = project.repo_url.removesuffix(".git")
        if project.repo_url == clone_url or normalized_repo_url == normalized_clone_url:
            return project
    return None


async def _webhook_background(
    project_id: str,
    local_path: str,
    platform: str,
    run_id: int,
    log_mgr: LogManager,
    *,
    is_push: bool,
    before_sha: str,
    after_sha: str,
    is_first_push: bool,
):
    """Background: pull request scan or push diff-only doc patch."""
    if is_push:
        await run_webhook_diff_update(
            project_id=project_id,
            before_sha=before_sha,
            after_sha=after_sha,
            log_mgr=log_mgr,
        )
        return

    # Pull request events keep the existing scan-only behavior; code is not merged.
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
