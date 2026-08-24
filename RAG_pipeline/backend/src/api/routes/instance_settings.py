"""Admin endpoints for per-instance model settings.

GET returns the current override map (Owner only).
PUT replaces the override set (Owner only, validated against registered keys).
Every PUT is written to audit_logs with before/after diff.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.dependencies import DBSession, OwnerUser
from src.services import instance_settings_service as svc
from src.services.audit_service import AuditService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class InstanceSettingResponse(BaseModel):
    key: str
    value: str
    label: str | None = None
    description: str | None = None


class InstanceSettingsResponse(BaseModel):
    settings: dict[str, str]


class InstanceSettingsUpdateRequest(BaseModel):
    settings: dict[str, str | None] = Field(
        ...,
        description="Full key→value map. Empty string or null clears the override.",
    )


@router.get("/instance-settings", response_model=InstanceSettingsResponse)
async def get_instance_settings(
    _owner: OwnerUser,
    db: DBSession,
):
    """Return the current instance setting overrides (Owner only)."""
    overrides = await svc.get_all(db)
    return InstanceSettingsResponse(settings=overrides)


@router.put("/instance-settings", response_model=InstanceSettingsResponse)
async def update_instance_settings(
    request: Request,
    body: InstanceSettingsUpdateRequest,
    owner: OwnerUser,
    db: DBSession,
):
    """Replace the instance setting overrides (Owner only).

    Validates every key against the registered set. Empty / null values delete
    the row so the system falls back to env / pydantic defaults. Each successful
    PUT is written to audit_logs with a before/after diff per key.
    """
    try:
        normalized = svc.normalize_payload(body.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    before = await svc.get_all(db)
    try:
        await svc.set_many(
            db,
            normalized,
            actor_user_id=owner.id,
            actor_username=owner.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    after = await svc.get_all(db)

    diff: dict[str, dict[str, str | None]] = {}
    for key in set(before) | set(after):
        if before.get(key) != after.get(key):
            diff[key] = {"before": before.get(key), "after": after.get(key)}

    audit = AuditService(db)
    await audit.log(
        user_id=owner.id,
        username=owner.username,
        action="update_instance_settings",
        resource_type="instance_settings",
        resource_id=None,
        details={"diff": diff, "keys": sorted(normalized.keys())},
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()

    logger.info(
        "instance_settings updated by %s: %d keys changed",
        owner.username,
        len(diff),
    )

    return InstanceSettingsResponse(settings=after)
