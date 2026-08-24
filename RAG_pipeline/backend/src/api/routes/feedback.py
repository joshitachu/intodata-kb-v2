"""User feedback on assistant messages (thumbs-up / thumbs-down / correction).

Append-only ingest. Latest row per (user_id, message_id) reflects the user's
current reaction. Powers the Week-3 knowledge-gaps admin view and the
chat->docs learning loop (rationale was in docs/PRE_SALES_AUDIT.md, archived
2026-08-24 to ../intodata-kb-archive/business-docs/).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.api.dependencies import CurrentUser, DBSession
from src.api.routes.auth import limiter
from src.db.models import Conversation, ConversationMessage, Feedback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

ALLOWED_KINDS: set[str] = {"up", "down", "correction", "cleared"}


class FeedbackBody(BaseModel):
    kind: str = Field(..., max_length=20)
    conversation_id: int | None = Field(None, ge=1)
    message_id: int | None = Field(None, ge=1)
    text: str | None = Field(None, max_length=2000)
    citations_snapshot: list[dict[str, Any]] | None = None


class FeedbackRow(BaseModel):
    id: int
    kind: str
    conversation_id: int | None
    message_id: int | None
    text: str | None
    created_at: str


@router.post("", status_code=204)
@limiter.limit("120/minute")
async def submit_feedback(
    request: Request,
    body: FeedbackBody,
    user: CurrentUser,
    db: DBSession,
) -> None:
    """Record a single feedback event. Fire-and-forget from the client."""
    if body.kind not in ALLOWED_KINDS:
        # Be quiet about rejections so clients don't retry.
        return None

    # Verify message_id belongs to a conversation this user owns (prevents
    # writing feedback for someone else's message). Feedback with no
    # message_id is allowed for future free-form comments.
    if body.message_id is not None:
        owner_id = await db.scalar(
            select(Conversation.user_id)
            .join(
                ConversationMessage,
                ConversationMessage.conversation_id == Conversation.id,
            )
            .where(
                ConversationMessage.id == body.message_id,
                ConversationMessage.deleted_at.is_(None),
            )
        )
        if owner_id is None or owner_id != user.id:
            # Quiet no-op on missing / cross-user attempts
            return None

    # citations_snapshot sanity cap
    snapshot = body.citations_snapshot
    if snapshot is not None and len(str(snapshot)) > 8000:
        snapshot = snapshot[:20]  # keep first N entries rather than truncating bytes

    fb = Feedback(
        user_id=user.id,
        conversation_id=body.conversation_id,
        message_id=body.message_id,
        kind=body.kind,
        text=body.text,
        citations_snapshot=snapshot,
    )
    db.add(fb)
    await db.commit()
    return None


@router.get("/mine", response_model=list[FeedbackRow])
async def my_recent_feedback(
    user: CurrentUser,
    db: DBSession,
    limit: int = 50,
) -> list[FeedbackRow]:
    """Return the authenticated user's recent feedback. Useful for rehydrating
    thumbs state when a conversation is reopened."""
    limit = max(1, min(limit, 200))
    res = await db.execute(
        select(Feedback)
        .where(Feedback.user_id == user.id)
        .order_by(Feedback.created_at.desc())
        .limit(limit)
    )
    rows = res.scalars().all()
    return [
        FeedbackRow(
            id=r.id,
            kind=r.kind,
            conversation_id=r.conversation_id,
            message_id=r.message_id,
            text=r.text,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
