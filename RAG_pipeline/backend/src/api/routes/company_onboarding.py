"""Company-level onboarding wizard endpoints (Owner only for writes)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from src.api.dependencies import CurrentUser, DBSession, OwnerUser
from src.db.session import async_session_factory
from src.services.system_config_service import (
    SystemConfigService,
    run_company_background_setup,
)
from src.db.models.system_config import SystemConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding/company", tags=["onboarding"])


# --- Schemas ---------------------------------------------------------

class CompanyStatusResponse(BaseModel):
    company_wizard_completed: bool
    wizard_current_step: int
    workspace_name: str | None = None
    selected_platforms: list[str] = Field(default_factory=list)
    selected_comm_tools: list[str] = Field(default_factory=list)
    source_type: str | None = None
    source_url: str | None = None
    source_filename: str | None = None
    setup_status: str
    setup_error: str | None = None


class WorkspaceStepBody(BaseModel):
    workspace_name: str = Field(..., min_length=1, max_length=200)


class PlatformsStepBody(BaseModel):
    platforms: list[str]


class CommToolsStepBody(BaseModel):
    comm_tools: list[str]


class SourceStepBody(BaseModel):
    source_type: str | None = None
    source_url: str | None = None
    source_filename: str | None = None


class CompleteWizardResponse(BaseModel):
    company_wizard_completed: bool
    setup_status: str


class SetupStatusResponse(BaseModel):
    setup_status: str
    setup_error: str | None = None


# --- Helpers ---------------------------------------------------------

def _to_status(row: SystemConfig | None) -> CompanyStatusResponse:
    if row is None:
        return CompanyStatusResponse(
            company_wizard_completed=False,
            wizard_current_step=0,
            setup_status="pending",
        )
    return CompanyStatusResponse(
        company_wizard_completed=row.company_wizard_completed,
        wizard_current_step=row.wizard_current_step,
        workspace_name=row.workspace_name,
        selected_platforms=list(row.selected_platforms or []),
        selected_comm_tools=list(row.selected_comm_tools or []),
        source_type=row.source_type,
        source_url=row.source_url,
        source_filename=row.source_filename,
        setup_status=row.setup_status,
        setup_error=row.setup_error,
    )


# --- Endpoints -------------------------------------------------------

@router.get("/status", response_model=CompanyStatusResponse)
async def get_company_status(_user: CurrentUser, db: DBSession) -> CompanyStatusResponse:
    """Return company wizard state. Readable by all authenticated users."""
    svc = SystemConfigService(db)
    row = await svc.get(create=False)
    return _to_status(row)


@router.put("/step/workspace", response_model=CompanyStatusResponse)
async def save_workspace(body: WorkspaceStepBody, _owner: OwnerUser, db: DBSession):
    svc = SystemConfigService(db)
    try:
        row = await svc.save_step("workspace", {"workspace_name": body.workspace_name})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/platforms", response_model=CompanyStatusResponse)
async def save_platforms(body: PlatformsStepBody, _owner: OwnerUser, db: DBSession):
    svc = SystemConfigService(db)
    try:
        row = await svc.save_step("platforms", {"platforms": body.platforms})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/comm_tools", response_model=CompanyStatusResponse)
async def save_comm_tools(body: CommToolsStepBody, _owner: OwnerUser, db: DBSession):
    svc = SystemConfigService(db)
    try:
        row = await svc.save_step("comm_tools", {"comm_tools": body.comm_tools})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/source", response_model=CompanyStatusResponse)
async def save_source(body: SourceStepBody, _owner: OwnerUser, db: DBSession):
    svc = SystemConfigService(db)
    try:
        row = await svc.save_step(
            "source",
            {
                "source_type": body.source_type,
                "source_url": body.source_url,
                "source_filename": body.source_filename,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.post("/complete", response_model=CompleteWizardResponse)
async def complete_wizard(
    _owner: OwnerUser, db: DBSession, bg: BackgroundTasks
) -> CompleteWizardResponse:
    svc = SystemConfigService(db)
    row = await svc.get()
    assert row is not None
    has_source = bool(row.source_type and (row.source_url or row.source_filename))
    updated = await svc.complete_wizard(has_source=has_source)
    if has_source:
        bg.add_task(run_company_background_setup, async_session_factory)
    return CompleteWizardResponse(
        company_wizard_completed=updated.company_wizard_completed,
        setup_status=updated.setup_status,
    )


@router.get("/setup-status", response_model=SetupStatusResponse)
async def setup_status(_user: CurrentUser, db: DBSession) -> SetupStatusResponse:
    svc = SystemConfigService(db)
    row = await svc.get(create=False)
    if row is None:
        return SetupStatusResponse(setup_status="pending")
    return SetupStatusResponse(setup_status=row.setup_status, setup_error=row.setup_error)


@router.post("/reset", status_code=204)
async def reset_company_onboarding(_owner: OwnerUser, db: DBSession):
    """Owner: wipe company onboarding so the company wizard shows again."""
    svc = SystemConfigService(db)
    await svc.reset()
    return None
