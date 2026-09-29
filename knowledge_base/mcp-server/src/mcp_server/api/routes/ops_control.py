"""Mission Control inbound control endpoints.

Lets Mission Control trigger scans and doc generation, and install a pushed
subscription licence, on this VPS without JWT cookie auth. A dedicated Bearer
key (``MCP_OPS_CONTROL_KEY``) authenticates the caller. The router is always
registered. While the key is unset, every endpoint answers 503, so an instance
that was never given a key cannot be driven remotely.

At the edge, nginx routes ``/ops/control/`` straight to mcp-server with no
superadmin gate (this is a machine caller), optionally behind an IP allowlist
(``OPS_CONTROL_ALLOWED_IPS``).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from mcp_server.config import config
from mcp_server.licensing.install import (
    MAX_TOKEN_BYTES,
    LicenceInstallService,
    LicenceRejected,
    LicenceStoreUnwritable,
    LicenceTooLarge,
)
from mcp_server.licensing.runtime import get_policy

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
    # Constant-time: a plain ``!=`` leaks the matching prefix length through timing.
    if not hmac.compare_digest(
        credentials.credentials.encode("utf-8"), config.ops_control_key.encode("utf-8")
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid control key")


class TriggerResponse(BaseModel):
    status: str
    message: str


class LicenceInstallRequest(BaseModel):
    token: str


class LicenceInstallResponse(BaseModel):
    installed: bool
    #: ``current`` (the licence in force was replaced) or ``next`` (a gapless
    #: renewal was queued behind a still-valid licence).
    slot: Literal["current", "next"]
    #: The licence in force after the install.
    licence: dict[str, Any]
    #: The queued renewal, or ``None`` when nothing is queued.
    next_licence: dict[str, Any] | None


@router.post(
    "/licence",
    response_model=LicenceInstallResponse,
    dependencies=[Depends(_require_control_auth)],
)
def install_licence(body: LicenceInstallRequest) -> LicenceInstallResponse:
    """Install a licence pushed by Mission Control (verify first, then store).

    A plain ``def``, so FastAPI runs the blocking file I/O and fsyncs in its
    threadpool rather than on the event loop. Replies with the state the
    process policy reads back from the slot files, never with the token.
    """
    try:
        result = LicenceInstallService.from_config().install(body.token)
    except LicenceTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail={"code": "licence_too_large", "max_bytes": MAX_TOKEN_BYTES,
                    "reason": str(exc)},
        ) from exc
    except LicenceRejected as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "licence_rejected", "state": exc.status.state.value,
                    "reason": exc.status.reason or None},
        ) from exc
    except LicenceStoreUnwritable as exc:
        logger.error("licence install failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "licence_store_unwritable", "reason": str(exc)},
        ) from exc
    policy = get_policy()
    queued = policy.next_status()
    return LicenceInstallResponse(
        installed=True,
        slot=result.slot,
        licence=policy.status().to_public_dict(),
        next_licence=queued.to_public_dict() if queued is not None else None,
    )


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
