"""Onboarding wizard + product tour state endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, DBSession, OwnerUser
from src.db.models import ProjectAssignment, User, UserInvite, UserOnboarding
from src.db.session import async_session_factory
from src.rbac.persona import resolve_persona
from src.services.onboarding_service import OnboardingService, run_background_setup
from src.services.system_config_service import SystemConfigService

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

class InviteContext(BaseModel):
    """How this member came to have access, for a personal first-run welcome."""

    approved_by: str | None = None
    assigned_role: str | None = None
    assigned_project_ids: list[str] = Field(default_factory=list)
    welcome_note: str | None = None


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
    # Company-level flag — frontend uses this to decide which wizard to show
    company_wizard_completed: bool = False

    # Which walkthrough the user was actually shown, and the step ids they got
    # through. Resolved server-side so the wizard, the tour and /guide cannot
    # disagree about who the user is.
    persona: str = "developer"
    tour_variant: str | None = None
    tour_seen_steps: list[str] = Field(default_factory=list)
    # Set only while the walkthrough is still the user's first run, so the
    # welcome can name the person who approved them instead of greeting them
    # anonymously. Cleared once the wizard is complete.
    invite_context: InviteContext | None = None


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
    # Step id and variant are optional so the older integer-only caller shape
    # stays valid; both are needed for a role-filtered tour to resume correctly.
    step_id: str | None = Field(default=None, max_length=100)
    variant: str | None = Field(default=None, max_length=32)


class TourCompleteBody(BaseModel):
    variant: str | None = Field(default=None, max_length=32)


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
        tour_variant=row.tour_variant,
        tour_seen_steps=[str(s) for s in (row.tour_seen_steps or [])],
    )


async def _owns_any_project(db: AsyncSession, user: User) -> bool:
    """True when the user owns at least one project.

    Project ownership is an assignment flag rather than a role, so it cannot be
    read off the user row and needs its own lookup.
    """
    found = await db.execute(
        select(ProjectAssignment.id)
        .where(
            ProjectAssignment.user_id == user.id,
            ProjectAssignment.is_owner.is_(True),
        )
        .limit(1)
    )
    return found.scalar_one_or_none() is not None


async def _invite_context(db: AsyncSession, user: User) -> InviteContext | None:
    """Return how this member was granted access, if they arrived via invite.

    Looked up by ``accepted_user_id`` rather than email so a later address
    change does not break the link, and returns None for accounts that were
    created directly or through SSO — those have no approver to name.
    """
    invite = (
        await db.execute(
            select(UserInvite)
            .where(UserInvite.accepted_user_id == user.id)
            .order_by(UserInvite.accepted_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if invite is None:
        return None

    approver: str | None = None
    if invite.created_by is not None:
        approver = (
            await db.execute(
                select(User.username).where(User.id == invite.created_by)
            )
        ).scalar_one_or_none()

    return InviteContext(
        approved_by=approver,
        assigned_role=invite.assigned_role.name if invite.assigned_role else None,
        assigned_project_ids=list(invite.assigned_project_ids or []),
        welcome_note=invite.welcome_note,
    )


# --- Endpoints -------------------------------------------------------

@router.get("/config", response_model=OnboardingConfigResponse, summary="Get wizard platform and tool options")
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


@router.get("/status", response_model=OnboardingStatusResponse, summary="Get the current user's onboarding state")
async def get_status(user: CurrentUser, db: DBSession) -> OnboardingStatusResponse:
    """Return wizard + tour state, plus the company-level wizard flag.

    Existing users with no onboarding row are treated as completed (grandfathered).
    Admins can call POST /onboarding/reset to force-show the wizard again.
    """
    svc = OnboardingService(db)
    row = await svc.get_or_create(user, create=False)

    status = _to_status(row)

    # Embed company wizard state so the frontend needs only one status call
    company_svc = SystemConfigService(db)
    company_row = await company_svc.get(create=False)
    status.company_wizard_completed = (
        company_row.company_wizard_completed if company_row is not None else False
    )

    # Persona is resolved here, not in the browser, so the wizard, the tour and
    # the product guide cannot disagree about which walkthrough this user gets.
    status.persona = resolve_persona(
        user, owns_projects=await _owns_any_project(db, user)
    )

    # Only surface the approver while onboarding is still the user's first run.
    # Afterwards it is stale trivia, and the welcome should not keep naming
    # someone who may have since left.
    if row is not None and not row.wizard_completed:
        status.invite_context = await _invite_context(db, user)

    return status


@router.put("/step/workspace", response_model=OnboardingStatusResponse, summary="Save workspace name step")
async def save_workspace(body: WorkspaceStepBody, user: CurrentUser, db: DBSession):
    """Persist the workspace name entered in the onboarding wizard step."""
    svc = OnboardingService(db)
    try:
        row = await svc.save_step(
            user, "workspace", {"workspace_name": body.workspace_name}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/platforms", response_model=OnboardingStatusResponse, summary="Save selected integration platforms step")
async def save_platforms(body: PlatformsStepBody, user: CurrentUser, db: DBSession):
    """Persist the platform selections from the onboarding wizard step."""
    svc = OnboardingService(db)
    try:
        row = await svc.save_step(user, "platforms", {"platforms": body.platforms})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/comm_tools", response_model=OnboardingStatusResponse, summary="Save communication tools step")
async def save_comm_tools(body: CommToolsStepBody, user: CurrentUser, db: DBSession):
    """Persist the communication tool selections from the onboarding wizard step."""
    svc = OnboardingService(db)
    try:
        row = await svc.save_step(user, "comm_tools", {"comm_tools": body.comm_tools})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_status(row)


@router.put("/step/source", response_model=OnboardingStatusResponse, summary="Save source repository or file step")
async def save_source(body: SourceStepBody, user: CurrentUser, db: DBSession):
    """Persist the source URL or filename from the onboarding wizard step."""
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


@router.post("/complete", response_model=CompleteWizardResponse, summary="Mark the onboarding wizard as complete")
async def complete_wizard(
    user: CurrentUser, db: DBSession, bg: BackgroundTasks
) -> CompleteWizardResponse:
    svc = OnboardingService(db)
    row = await svc.get_or_create(user)
    assert row is not None
    # Source upload + background processing only applies to the admin wizard.
    # Non-admin users complete a slim welcome wizard with no source step.
    has_source = (
        user.is_superadmin
        and bool(row.source_type and (row.source_url or row.source_filename))
    )
    updated = await svc.complete_wizard(user, has_source=has_source)
    if has_source:
        bg.add_task(run_background_setup, user.id, async_session_factory)
    return CompleteWizardResponse(
        wizard_completed=updated.wizard_completed,
        setup_status=updated.setup_status,
    )


@router.get("/setup-status", response_model=SetupStatusResponse, summary="Poll background setup progress")
async def setup_status(user: CurrentUser, db: DBSession) -> SetupStatusResponse:
    """Return the current background ingest/setup status for the onboarded user."""
    svc = OnboardingService(db)
    row = await svc.get_or_create(user, create=False)
    if row is None:
        return SetupStatusResponse(setup_status="completed")
    return SetupStatusResponse(setup_status=row.setup_status, setup_error=row.setup_error)


@router.post("/tour/complete", response_model=OnboardingStatusResponse, summary="Mark the product tour as complete")
async def tour_complete(user: CurrentUser, db: DBSession, body: TourCompleteBody | None = None):
    """Flip tour_completed to true, recording which walkthrough was finished."""
    svc = OnboardingService(db)
    row = await svc.complete_tour(user, variant=(body.variant if body else None))
    return _to_status(row)


@router.put("/tour/step", response_model=OnboardingStatusResponse, summary="Save the current tour step position")
async def tour_step(body: TourStepBody, user: CurrentUser, db: DBSession):
    """Persist tour progress so a reload resumes where the user stopped."""
    svc = OnboardingService(db)
    row = await svc.save_step(
        user,
        "tour",
        {
            "tour_step": body.tour_step,
            "tour_step_id": body.step_id,
            "tour_variant": body.variant,
        },
    )
    return _to_status(row)


@router.post(
    "/reset",
    response_model=OnboardingStatusResponse,
    summary="Send a user back to their first run",
)
async def reset_onboarding(
    body: ResetOnboardingBody, owner: OwnerUser, db: DBSession
) -> OnboardingStatusResponse:
    """Owner: clear a user's wizard and walkthrough state so both show again.

    Returns the resulting state, so an operator running this by hand can see
    that the flags actually flipped. It previously returned 204 with no body,
    which made a silently-ineffective reset indistinguishable from a working
    one — and the reset was in fact ineffective (see `OnboardingService.reset`).
    """
    result = await db.execute(select(User).where(User.id == body.user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    svc = OnboardingService(db)
    row = await svc.reset(target)

    status = _to_status(row)
    company_svc = SystemConfigService(db)
    company_row = await company_svc.get(create=False)
    status.company_wizard_completed = (
        company_row.company_wizard_completed if company_row is not None else False
    )
    status.persona = resolve_persona(
        target, owns_projects=await _owns_any_project(db, target)
    )
    return status
