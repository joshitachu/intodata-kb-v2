"""Mission Control inbound control endpoints.

Allows the Mission Control service to trigger scans and doc generation on this
VPS without JWT cookie auth. Uses a dedicated Bearer key (MCP_OPS_CONTROL_KEY).
Only registered if ops_control_key is set in config.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from mcp_server.config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ops/control", tags=["ops-control"])
_bearer = HTTPBearer()


def _require_control_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    if not config.ops_control_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ops control not configured on this instance",
        )
    if credentials.credentials != config.ops_control_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid control key")


class TriggerResponse(BaseModel):
    status: str
    message: str


@router.post(
    "/scan/{project_id}",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_control_auth)],
)
async def trigger_scan(project_id: str, request: Request, background_tasks: BackgroundTasks) -> TriggerResponse:
    """Trigger a scan on this VPS for the given project. Called by Mission Control."""
    from mcp_server.db.session import async_session_factory
    from mcp_server.services.project_registry import ProjectRegistry
    from mcp_server.services.scanner_service import ScannerService

    log_mgr = request.app.state.log_manager

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        await registry.update_status(project_id, "scanning")
        run = await registry.create_scan_run(project_id, triggered_by="mission_control")
        await db.commit()
        local_path = project.local_path
        platform = project.platform
        run_id = run.id

    output_dir = str(config.intodoc_output_dir / project_id)

    async def _run() -> None:
        scanner = ScannerService(log_manager=log_mgr)
        log_mgr.reset_log_file(project_id)
        result = await scanner.run_scan(
            project_id=project_id,
            repo_path=local_path,
            platform=platform,
            output_dir=output_dir,
        )
        async with async_session_factory() as db:
            reg = ProjectRegistry(db)
            if result["success"]:
                await reg.update_scan_result(
                    project_id,
                    scan_result_path=result["scan_result_path"],
                    output_dir=result["output_dir"],
                    job_count=result["job_count"],
                )
                await reg.complete_scan_run(run_id, status="completed", job_count=result["job_count"])
            else:
                await reg.update_status(project_id, "error", error_message=result.get("error"))
                await reg.complete_scan_run(run_id, status="failed")
            await db.commit()

    background_tasks.add_task(_run)
    logger.info("Mission Control triggered scan project=%s", project_id)
    return TriggerResponse(status="scan_triggered", message=f"Scan started for {project_id}")


@router.post(
    "/generate/{project_id}",
    response_model=TriggerResponse,
    dependencies=[Depends(_require_control_auth)],
)
async def trigger_generate(project_id: str, request: Request, background_tasks: BackgroundTasks) -> TriggerResponse:
    """Trigger doc generation on this VPS for the given project. Called by Mission Control."""
    from mcp_server.api.routes.projects import _currently_generating, _run_generate_background
    from mcp_server.db.session import async_session_factory
    from mcp_server.services.project_registry import ProjectRegistry

    log_mgr = request.app.state.log_manager

    if project_id in _currently_generating:
        raise HTTPException(status_code=409, detail=f"Generation already running for {project_id}")

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
        run = await registry.create_generate_run(project_id, triggered_by="mission_control")
        await db.commit()
        run_id = run.id

    _currently_generating.add(project_id)
    background_tasks.add_task(
        _run_generate_background,
        project_id,
        run_id,
        log_mgr,
        resume=False,
        enterprise_mode=config.doc_generation_enterprise_mode_default,
    )
    logger.info("Mission Control triggered generate project=%s", project_id)
    return TriggerResponse(status="generate_triggered", message=f"Doc generation started for {project_id}")
