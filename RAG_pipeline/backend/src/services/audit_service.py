"""Audit logging service — records user actions for compliance and debugging."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        *,
        user_id: int | None = None,
        username: str | None = None,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            user_id=user_id,
            username=username,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
        )
        self.db.add(entry)
        await self.db.flush()
        logger.info(
            "Audit: user=%s action=%s resource=%s/%s",
            username or user_id,
            action,
            resource_type,
            resource_id,
        )
        return entry
