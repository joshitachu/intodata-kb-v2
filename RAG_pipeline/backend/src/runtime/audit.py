"""Audit helpers for runtime source lifecycle events."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.services.audit_service import AuditService

RESOURCE_TYPE = "runtime_source"


async def log_runtime_source_event(
    db: AsyncSession,
    *,
    user: User | None,
    action: str,
    source_id: int,
    details: dict | None = None,
) -> None:
    safe_details = _strip_sensitive(details or {})
    await AuditService(db).log(
        user_id=user.id if user else None,
        username=user.username if user else None,
        action=action,
        resource_type=RESOURCE_TYPE,
        resource_id=str(source_id),
        details=safe_details,
    )


def _strip_sensitive(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _is_sensitive_key(key) else _strip_sensitive(inner)
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_strip_sensitive(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in ("token", "secret", "password", "api_key", "credential"))
