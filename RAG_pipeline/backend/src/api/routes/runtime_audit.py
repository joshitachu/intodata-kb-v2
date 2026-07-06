from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from src.api.dependencies import DBSession, RuntimeSourceReader
from src.db.models import AuditLog

router = APIRouter(prefix="/runtime/audit", tags=["runtime-sources"])


class RuntimeAuditEvent(BaseModel):
    id: int
    user_id: int | None = None
    username: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict | None = None
    created_at: datetime | None = None


class RuntimeAuditResponse(BaseModel):
    events: list[RuntimeAuditEvent]
    total: int


@router.get("", response_model=RuntimeAuditResponse)
async def list_runtime_audit(
    _user: RuntimeSourceReader,
    db: DBSession,
    source_id: int | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> RuntimeAuditResponse:
    stmt = select(AuditLog).where(AuditLog.resource_type == "runtime_source")
    if source_id is not None:
        stmt = stmt.where(AuditLog.resource_id == str(source_id))
    if event_type is not None:
        stmt = stmt.where(AuditLog.action == event_type)
    stmt = stmt.order_by(AuditLog.id.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    events = [
        RuntimeAuditEvent(
            id=row.id,
            user_id=row.user_id,
            username=row.username,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            details=row.details,
            created_at=getattr(row, "created_at", None),
        )
        for row in rows
    ]
    return RuntimeAuditResponse(events=events, total=len(events))
