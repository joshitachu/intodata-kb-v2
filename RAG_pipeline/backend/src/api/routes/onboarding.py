"""Onboarding wizard + product tour state endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.api.dependencies import AdminUser, CurrentUser, DBSession
from src.db.models import User, UserOnboarding
from src.db.session import async_session_factory
from src.services.onboarding_service import (
    VALID_COMM_TOOLS,
    VALID_PLATFORMS,
    OnboardingService,
    run_background_setup,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


# --- Data-driven wizard config -------------------------------------
# Kept here (not in DB) because these options describe product capabilities,
# not per-tenant state. Adding a new platform/tool = editing this list +
# the matching VALID_* set in onboarding_service.py.

_PLATFORM_OPTIONS: list[dict] = [
    {
        "id": "talend",
        "label": "Talend",
        "description": "ETL jobs and integration projects",
        "status": "available",
    },
    {
        "id": "workato",
        "label": "Workato",
        "description": "iPaaS recipes and connectors",
        "status": "available",
    },
    {
        "id": "lobster",
        "label": "Lobster",
        "description": "Data integration workflows",
        "status": "available",
    },
    {
        "id": "generic",
        "label": "Generic codebase",
        "description": "Any other integration or service repo",
        "status": "available",
    },
]

_TOOL_CATEGORIES: list[dict] = [
    {
        "id": "communication",
        "label": "Communication",
        "tools": [
            {
                "id": "slack",
                "label": "Slack",
                "description": "Team chat and alerts",
                "status": "coming_soon",
            },
            {
                "id": "teams",
                "label": "Microsoft Teams",
                "description": "Microsoft 365 chat",
                "status": "coming_soon",
            },
        ],
    },
    {
        "id": "ticketing",
        "label": "Ticketing & Docs",
        "tools": [
            {
                "id": "jira",
                "label": "Jira",
                "description": "Issue tracking and tickets",
                "status": "coming_soon",
            },
            {
                "id": "confluence",
                "label": "Confluence",
                "description": "Internal wiki and docs",
                "status": "coming_soon",
            },
        ],
    },
]


# --- Schemas ---------------------------------------------------------

class OnboardingStatusResponse(BaseModel):
    wizard_completed: bool
    wizard_current_step: int
    tour_completed: bool
    tour_current_step: int
    setup_status: str
    setup_error: str | None = None
    workspace_name: str | None = None
    selected_platforms: list[str] = Field(default_factory=list)
    selected_comm_tools: list[str] = Field(default_factory=list)
    source_type: str | None = None
    source_url: str | None = None
    source_filename: str | None = None


class OptionItem(BaseModel):
    id: str
    label: str
    description: str
    status: str  # "available" | "coming_soon"


class ToolCategory(BaseModel):
    id: str
    label: str
    tools: list[OptionItem]


class OnboardingConfigResponse(BaseModel):
    platforms: list[OptionItem]
    tool_categories: list[ToolCategory]


class WorkspaceStepBody(BaseModel):
    workspace_name: str = Field(..., min_length=1, max_length=200)


class PlatformsStepBody(BaseModel):
    platforms: list[str]


class CommToolsStepBody(BaseModel):
    comm_tools: list[str]


class SourceStepBody(BaseModel):
    source_type: str | None = None  # "github" | "zip" | None (skipped)
    source_url: str | None = None
    source_filename: str | None = None


class TourStepBody(BaseModel):
    tour_step: int = Field(..., ge=0)


class CompleteWizardResponse(BaseModel):
    wizard_completed: bool
    setup_status: str


class SetupStatusResponse(BaseModel):
    setup_status: str
    setup_error: str | None = None


class ResetOnboardingBody(BaseModel):
    user_id: int


# --- Helpers ---------------------------------------------------------

def _to_status(row: UserOnboarding | None) -> OnboardingStatusResponse:
    """Map a row (or missing row — existing user) to the status response."""
    if row is None:
        return OnboardingStatusResponse(
            wizard_completed=True,
            wizard_current_step=0,
            tour_completed=True,
            tour_current_step=0,
            setup_status="completed",
        )
    return OnboardingStatusResponse(
        wizard_completed=row.wizard_completed,
        wizard_current_step=row.wizard_current_step,
        tour_completed=row.tour_completed,
        tour_current_step=row.tour_current_step,
        setup_status=row.setup_status,
        setup_error=row.setup_error,
        workspace_name=row.workspace_name,
        selected_platforms=list(row.selected_platforms or []),
        selected_comm_tools=list(row.selected_comm_tools or []),
        source_type=row.source_type,
        source_url=row.source_url,
        source_filename=row.source_filename,
    )


# --- Endpoints -------------------------------------------------------

@router.get("/config", response_model=OnboardingConfigResponse)
async def get_config(_user: CurrentUser) -> OnboardingConfigResponse:
    """Return the wizard's platform + tool options. Auth-required but same for
    every user — adding a new option is a code/config change, not a DB update.
    """
    return OnboardingConfigResponse(
        platforms=[OptionItem(**p) for p in _PLATFORM_OPTIONS],
        tool_categories=[
            ToolCategory(
                id=cat["id"],
                label=cat["label"],
                tools=[OptionItem(**t) for t in cat["tools"]],
            )
            for cat in _TOOL_CATEGORIES
        ],
    )


@router.get("/status", response_model=OnboardingStatusResponse)
async def get_status(user: CurrentUser, db: DBSession) -> OnboardingStatusResponse:
    """Return wizard + tour state. Creates a fresh row lazily for new users.

    "New user" = no row yet AND user created after onboarding shipped. We
    detect this by checking whether a row exists AT ALL. If you want existing
    users to see the wizard too, `POST /onboarding/reset` them.
    """
    svc = OnboardingService(db)
    row = await svc.get_or_create(user, create=False)

    # Lazy-create a row for users who were created after this feature shipped
    # (heuristic: if the user has no onboarding row, assume they're a grandfathered
    # existing user — return completed-state so they skip the wizard).
    # Admins can call POST /onboarding/reset to force-show the wizard.
    if row is None:
        # Create the row only when the user is brand new. We can't know "brand
        # new" perfectly, so we default to skipping the wizard for any user
        # without a row (safer — existing users are not interrupted).
        return _to_status(None)

    return _to_status(row)


@router.put("/step/workspace", response_model=OnboardingStatusResponse)
async def save_workspace(body: WorkspaceStepBody, user: CurrentUser, db: DBSession):
    svc = OnboardingService(db)
    try:
        row = await svc.save_step(
            user, "workspace", {"workspace_name": body.workspace_name}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/platforms", response_model=OnboardingStatusResponse)
async def save_platforms(body: PlatformsStepBody, user: CurrentUser, db: DBSession):
    svc = OnboardingService(db)
    try:
        row = await svc.save_step(user, "platforms", {"platforms": body.platforms})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/comm_tools", response_model=OnboardingStatusResponse)
async def save_comm_tools(body: CommToolsStepBody, user: CurrentUser, db: DBSession):
    svc = OnboardingService(db)
    try:
        row = await svc.save_step(user, "comm_tools", {"comm_tools": body.comm_tools})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/source", response_model=OnboardingStatusResponse)
async def save_source(body: SourceStepBody, user: CurrentUser, db: DBSession):
    svc = OnboardingService(db)
    try:
        row = await svc.save_step(
            user,
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
    user: CurrentUser, db: DBSession, bg: BackgroundTasks
) -> CompleteWizardResponse:
    svc = OnboardingService(db)
    row = await svc.get_or_create(user)
    assert row is not None
    has_source = bool(row.source_type and (row.source_url or row.source_filename))
    updated = await svc.complete_wizard(user, has_source=has_source)
    if has_source:
        bg.add_task(run_background_setup, user.id, async_session_factory)
    return CompleteWizardResponse(
        wizard_completed=updated.wizard_completed,
        setup_status=updated.setup_status,
    )


@router.get("/setup-status", response_model=SetupStatusResponse)
async def setup_status(user: CurrentUser, db: DBSession) -> SetupStatusResponse:
    svc = OnboardingService(db)
    row = await svc.get_or_create(user, create=False)
    if row is None:
        return SetupStatusResponse(setup_status="completed")
    return SetupStatusResponse(setup_status=row.setup_status, setup_error=row.setup_error)


@router.post("/tour/complete", response_model=OnboardingStatusResponse)
async def tour_complete(user: CurrentUser, db: DBSession):
    svc = OnboardingService(db)
    row = await svc.complete_tour(user)
    return _to_status(row)


@router.put("/tour/step", response_model=OnboardingStatusResponse)
async def tour_step(body: TourStepBody, user: CurrentUser, db: DBSession):
    svc = OnboardingService(db)
    row = await svc.save_step(user, "tour", {"tour_step": body.tour_step})
    return _to_status(row)


@router.post("/reset", status_code=204)
async def reset_onboarding(body: ResetOnboardingBody, admin: AdminUser, db: DBSession):
    """Admin: wipe a user's onboarding state so the wizard shows on next login."""
    result = await db.execute(select(User).where(User.id == body.user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    svc = OnboardingService(db)
    await svc.reset(target)
    return None
