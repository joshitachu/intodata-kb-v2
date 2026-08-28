"""Wizard telemetry — raw event ingest + Owner aggregate.

Goal: see which platforms / tools users actually pick so we can prioritise
which integration to build end-to-end next. Fire-and-forget from the frontend;
errors never block UX.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.api.dependencies import CurrentUser, DBSession, OwnerUser
from src.api.routes.auth import limiter
from src.db.models import WizardEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# Whitelist so an end-user can't write arbitrary event_type strings into the
# table (keeps the data set analysable and prevents abuse).
ALLOWED_EVENT_TYPES: set[str] = {
    "wizard_step_viewed",
    "wizard_platform_selected",
    "wizard_platform_deselected",
    "wizard_tool_selected",
    "wizard_tool_deselected",
    "wizard_source_method",
    "wizard_completed",
    "tour_started",
    "tour_skipped",
    # Walkthrough outcomes. "tour_skipped" only ever recorded a refusal at the
    # launcher, so there was no way to tell a finished tour from one abandoned
    # halfway, and no signal for a step that could not be shown at all.
    "tour_completed",
    "tour_dismissed",
    "tour_step_unavailable",
}


class WizardEventBody(BaseModel):
    event_type: str = Field(..., max_length=64)
    step: int | None = Field(None, ge=0, le=20)
    payload: dict | None = None


class WizardSummaryCount(BaseModel):
    key: str
    count: int


class WizardSummaryResponse(BaseModel):
    window_days: int
    totals_by_event_type: list[WizardSummaryCount]
    platform_selections: list[WizardSummaryCount]
    tool_selections: list[WizardSummaryCount]
    source_methods: list[WizardSummaryCount]
    tour_started: int
    tour_skipped: int
    unique_users: int


@router.post("/wizard-event", status_code=204)
@limiter.limit("120/minute")
async def ingest_wizard_event(
    request: Request,
    body: WizardEventBody,
    user: CurrentUser,
    db: DBSession,
) -> None:
    """Record a single wizard telemetry event. Fire-and-forget from the client."""
    if body.event_type not in ALLOWED_EVENT_TYPES:
        # Be quiet about rejections so clients don't retry — just no-op.
        return None

    # Payload size sanity check — telemetry should be small
    payload = body.payload or None
    if payload is not None and len(str(payload)) > 2000:
        payload = None

    evt = WizardEvent(
        user_id=user.id,
        event_type=body.event_type,
        step=body.step,
        payload=payload,
    )
    db.add(evt)
    await db.commit()
    return None


@router.get("/wizard/summary", response_model=WizardSummaryResponse)
async def wizard_summary(
    _owner: OwnerUser,
    db: DBSession,
    window_days: int = 30,
) -> WizardSummaryResponse:
    """Admin-only aggregate over the last `window_days`.

    Surfaces the numbers we actually care about:
    - How many users picked each platform / each tool
    - How many used GitHub vs ZIP vs skipped the source step
    - Tour kick-off vs skip rate
    """
    window_days = max(1, min(window_days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=window_days)

    # Totals by event type
    res = await db.execute(
        select(WizardEvent.event_type, func.count())
        .where(WizardEvent.created_at >= since)
        .group_by(WizardEvent.event_type)
        .order_by(func.count().desc())
    )
    totals_by_event_type = [
        WizardSummaryCount(key=row[0], count=int(row[1])) for row in res.all()
    ]

    # Platform selections — group by payload->>'platform' on platform_selected events
    platform_key = WizardEvent.payload["platform"].astext
    res = await db.execute(
        select(platform_key, func.count())
        .where(
            WizardEvent.created_at >= since,
            WizardEvent.event_type == "wizard_platform_selected",
            platform_key.isnot(None),
        )
        .group_by(platform_key)
        .order_by(func.count().desc())
    )
    platform_selections = [
        WizardSummaryCount(key=row[0] or "(unknown)", count=int(row[1]))
        for row in res.all()
    ]

    # Tool selections
    tool_key = WizardEvent.payload["tool"].astext
    res = await db.execute(
        select(tool_key, func.count())
        .where(
            WizardEvent.created_at >= since,
            WizardEvent.event_type == "wizard_tool_selected",
            tool_key.isnot(None),
        )
        .group_by(tool_key)
        .order_by(func.count().desc())
    )
    tool_selections = [
        WizardSummaryCount(key=row[0] or "(unknown)", count=int(row[1]))
        for row in res.all()
    ]

    # Source methods (github / zip / skip)
    method_key = WizardEvent.payload["method"].astext
    res = await db.execute(
        select(method_key, func.count())
        .where(
            WizardEvent.created_at >= since,
            WizardEvent.event_type == "wizard_source_method",
            method_key.isnot(None),
        )
        .group_by(method_key)
        .order_by(func.count().desc())
    )
    source_methods = [
        WizardSummaryCount(key=row[0] or "(unknown)", count=int(row[1]))
        for row in res.all()
    ]

    tour_started = next(
        (c.count for c in totals_by_event_type if c.key == "tour_started"), 0
    )
    tour_skipped = next(
        (c.count for c in totals_by_event_type if c.key == "tour_skipped"), 0
    )

    res = await db.execute(
        select(func.count(func.distinct(WizardEvent.user_id))).where(
            WizardEvent.created_at >= since
        )
    )
    unique_users = int(res.scalar() or 0)

    return WizardSummaryResponse(
        window_days=window_days,
        totals_by_event_type=totals_by_event_type,
        platform_selections=platform_selections,
        tool_selections=tool_selections,
        source_methods=source_methods,
        tour_started=tour_started,
        tour_skipped=tour_skipped,
        unique_users=unique_users,
    )
