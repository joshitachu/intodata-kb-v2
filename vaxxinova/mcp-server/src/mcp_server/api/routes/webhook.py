"""GitHub webhook handler."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request

from mcp_server.db.session import async_session_factory
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.scanner_service import ScannerService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])


@router.post("/github/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle GitHub push/pull_request events.

    Looks up project by repository clone_url and triggers a background scan.
    """
    event = request.headers.get("X-GitHub-Event", "")
    if event not in ("push", "pull_request"):
        return {"status": "ignored", "reason": f"Unsupported event: {event}"}

    body = await request.json()
    repo = body.get("repository", {})
    clone_url = repo.get("clone_url", "")

    if not clone_url:
        return {"status": "ignored", "reason": "No clone_url in payload"}

    # Find project by repo_url
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        projects = await registry.list_projects()
        project = None
        for p in projects:
            if p.repo_url and (p.repo_url == clone_url or p.repo_url == clone_url.rstrip(".git")):
                project = p
                break

        if not project:
            return {"status": "ignored", "reason": f"No project registered for {clone_url}"}

        await registry.update_status(project.id, "scanning")
        run = await registry.create_scan_run(project.id, triggered_by="webhook")
        await db.commit()

        project_id = project.id
        local_path = project.local_path
        platform = project.platform
        run_id = run.id

    log_mgr = request.app.state.log_manager
    background_tasks.add_task(
        _webhook_scan_background, project_id, local_path, platform, run_id, log_mgr
    )

    return {"status": "scan_triggered", "project_id": project_id}


async def _webhook_scan_background(
    project_id: str,
    local_path: str,
    platform: str,
    run_id: int,
    log_mgr: LogManager,
):
    """Background: pull latest code and re-scan."""
    # Pull latest if it's a git repo
    import asyncio
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull",
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.wait()
    except Exception as e:
        logger.warning(f"Git pull failed for {project_id}: {e}")

    # Run scan
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
