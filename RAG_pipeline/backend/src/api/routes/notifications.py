"""In-app notification endpoints (bell icon + dropdown)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.dependencies import CurrentUser, DBSession
from src.db.models import Notification
from src.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    id: int
    type: str
    title: str
    message: str
    is_read: bool
    meta: dict | None = None
    created_at: str


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    unread_count: int


class UnreadCountResponse(BaseModel):
    unread_count: int


def _to_dto(n: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=n.id,
        type=n.type,
        title=n.title,
        message=n.message,
        is_read=n.is_read,
        meta=n.meta,
        created_at=n.created_at.isoformat(),
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    user: CurrentUser,
    db: DBSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
):
    svc = NotificationService(db)
    notifs = await svc.list_for_user(
        user.id, limit=limit, offset=offset, unread_only=unread_only
    )
    unread = await svc.unread_count(user.id)
    return NotificationListResponse(
        notifications=[_to_dto(n) for n in notifs],
        unread_count=unread,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(user: CurrentUser, db: DBSession):
    svc = NotificationService(db)
    return UnreadCountResponse(unread_count=await svc.unread_count(user.id))


@router.put("/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(notification_id: int, user: CurrentUser, db: DBSession):
    svc = NotificationService(db)
    ok = await svc.mark_read(user.id, notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")

    notifs = await svc.list_for_user(user.id, limit=1, offset=0)
    matching = [n for n in notifs if n.id == notification_id]
    if matching:
        return _to_dto(matching[0])
    # Fallback — refetch
    from sqlalchemy import select
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user.id,
        )
    )
    n = result.scalar_one_or_none()
    if n is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _to_dto(n)


@router.put("/read-all")
async def mark_all_read(user: CurrentUser, db: DBSession):
    svc = NotificationService(db)
    updated = await svc.mark_all_read(user.id)
    return {"updated": updated}
