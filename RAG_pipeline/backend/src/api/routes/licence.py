"""Subscription licence status for this instance.

Readable by any authenticated user, including while the licence is expired:
the frontend needs it to explain *why* chat is refused (402) and to show the
near-expiry banner. It never returns the token itself.

Generation usage (used/allowed) is owned by the MCP server, which counts the
runs; the frontend reads that from MCP's own licence status endpoint.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.api.dependencies import CurrentUser
from src.licensing.runtime import get_policy

router = APIRouter(prefix="/licence", tags=["licence"])


class LicenceStatusResponse(BaseModel):
    state: Literal["valid", "expired", "not_yet_valid", "missing", "invalid"]
    reason: str | None = None
    plan: str | None = None
    features: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)
    period_start: str | None = None
    period_end: str | None = None
    days_remaining: int | None = None
    near_expiry: bool = False
    #: ``off`` means nothing is refused whatever ``state`` says (dev only;
    #: production refuses to start with it).
    enforcement: Literal["enforce", "off"]
    #: A pushed renewal waiting for its period to start (ADR-038), else null.
    next_licence: dict[str, object] | None = None


@router.get("/status", response_model=LicenceStatusResponse, summary="Current licence state")
async def licence_status(_user: CurrentUser) -> LicenceStatusResponse:
    policy = get_policy()
    queued = policy.next_status()
    return LicenceStatusResponse(
        **policy.status().to_public_dict(),
        enforcement="enforce" if policy.enforce else "off",
        next_licence=queued.to_public_dict() if queued is not None else None,
    )
