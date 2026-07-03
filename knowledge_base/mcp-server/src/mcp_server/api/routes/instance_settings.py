"""Admin endpoints for per-instance model settings.

Mirrors the RAG-side `instance_settings` route. Both processes write to the
same Postgres table so a save through the dashboard Models tab is visible
across both services. Every PUT is appended to the shared `audit_logs` table
with a before/after diff per key.

The validation set is built from the `metadata.models.list_models()` catalogue
for MCP-controlled keys (deep_dive, doc gen analysis/write). The RAG-side
keys (rag_chat_model, rag_embed_model) are validated by the RAG endpoint
against its own static catalogue, so MCP just stores what it receives for
those keys.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from mcp_server.db.models import InstanceSetting
from mcp_server.db.session import async_session_factory
from mcp_server.metadata.models import list_models as list_catalogue_models
from mcp_server.services.instance_settings_service import (
    SETTING_KEYS,
    SETTING_REGISTRY,
    get_all,
    normalize_payload,
    set_many,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# Catalogue of valid model ids. Built lazily so tests that mock `list_models()`
# see the override.
def _catalogue_models():
    return list_catalogue_models(
        default_analysis_model="",
        default_investigation_model=None,
        default_writing_model="",
    )


def _valid_mcp_model_ids() -> set[str]:
    """All model ids the MCP server knows how to talk to right now."""
    return {m.id for m in _catalogue_models()}


def _valid_rag_chat_model_ids() -> set[str]:
    """Catalogue ids the RAG chat surface can actually run: RAG-capable and with
    credentials present (or local). Prevents persisting an un-runnable selection."""
    return {m.id for m in _catalogue_models() if m.rag_capable and m.available}


# Keys the MCP route validates against the doc-generation catalogue.
MCP_VALIDATED_KEYS = {"deep_dive_model", "doc_gen_analysis_model", "doc_gen_write_model"}


class InstanceSettingsResponse(BaseModel):
    settings: dict[str, str]


class InstanceSettingsUpdateRequest(BaseModel):
    settings: dict[str, str | None] = Field(
        ...,
        description="Full key→value map. Empty string or null clears the override.",
    )


async def _require_superadmin(request: Request) -> int:
    """Returns the acting user id. 403 if not superadmin."""
    payload = getattr(request.state, "auth_payload", None)
    if not payload:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = int(payload.get("sub", 0))
    async with async_session_factory() as db:
        row = await db.execute(
            text("SELECT is_superadmin, username FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        user_row = row.fetchone()
    if user_row is None or not user_row[0]:
        raise HTTPException(status_code=403, detail="Superadmin required")
    return user_id


async def _audit(
    actor_user_id: int,
    actor_username: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict,
    ip_address: str | None,
) -> None:
    """Write to the shared audit_logs table via raw SQL."""
    async with async_session_factory() as db:
        await db.execute(
            text(
                """
                INSERT INTO audit_logs (
                    user_id, username, action, resource_type, resource_id,
                    details, ip_address, created_at
                ) VALUES (
                    :user_id, :username, :action, :resource_type, :resource_id,
                    :details, :ip_address, :created_at
                )
                """
            ),
            {
                "user_id": actor_user_id,
                "username": actor_username,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "details": json.dumps(details),
                "ip_address": ip_address,
                "created_at": datetime.now(timezone.utc),
            },
        )
        await db.commit()


@router.get("/instance-settings", response_model=InstanceSettingsResponse)
async def get_instance_settings(request: Request):
    await _require_superadmin(request)
    async with async_session_factory() as db:
        settings = await get_all(db)
    return InstanceSettingsResponse(settings=settings)


@router.put("/instance-settings", response_model=InstanceSettingsResponse)
async def update_instance_settings(
    request: Request,
    body: InstanceSettingsUpdateRequest,
):
    user_id = await _require_superadmin(request)
    try:
        normalized = normalize_payload(body.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Validate MCP-controlled (doc-gen) keys against the runtime catalogue.
    valid_ids = _valid_mcp_model_ids()
    invalid = [
        f"{key}={value}"
        for key, value in normalized.items()
        if key in MCP_VALIDATED_KEYS and value != "" and value not in valid_ids
    ]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown model id(s): {invalid}. "
                f"Valid ids: {sorted(valid_ids)}"
            ),
        )

    # Validate the RAG chat model against what the RAG backend can actually run
    # (RAG-capable + credentials present). Closes the gap that let an un-runnable
    # selection like a keyless cloud model persist and 404 at chat time.
    rag_chat_value = normalized.get("rag_chat_model", "")
    if rag_chat_value:
        runnable = _valid_rag_chat_model_ids()
        if rag_chat_value not in runnable:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"RAG chat model {rag_chat_value!r} is not runnable "
                    f"(unknown, not chat-capable, or missing credentials). "
                    f"Runnable: {sorted(runnable)}"
                ),
            )

    payload = getattr(request.state, "auth_payload", None)
    actor_username = payload.get("preferred_username") if payload else None

    before, after = await set_many(
        normalized,
        actor_user_id=user_id,
        actor_username=actor_username,
    )

    diff: dict[str, dict[str, str | None]] = {}
    for key in set(before) | set(after):
        if before.get(key) != after.get(key):
            diff[key] = {"before": before.get(key), "after": after.get(key)}

    await _audit(
        actor_user_id=user_id,
        actor_username=actor_username,
        action="update_instance_settings",
        resource_type="instance_settings",
        resource_id=None,
        details={"diff": diff, "keys": sorted(normalized.keys())},
        ip_address=request.client.host if request.client else None,
    )

    logger.info(
        "instance_settings updated by user_id=%s: %d keys changed",
        user_id,
        len(diff),
    )

    return InstanceSettingsResponse(settings=after)