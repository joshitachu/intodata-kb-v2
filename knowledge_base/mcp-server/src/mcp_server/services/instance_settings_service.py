"""Per-instance model settings service (MCP side).

Mirror of the RAG backend's `instance_settings_service` — both processes read
and write the same `instance_settings` Postgres table, so a save through the
dashboard's Models tab is visible to both processes.

MCP readers use their owning keys, including a dedicated ``pack_author_model``
slot. Pack authoring is deliberately isolated from RAG chat model selection;
each conversational turn is one compact design request followed by deterministic
server-side file rendering and validation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import InstanceSetting  # type: ignore[attr-defined]
from mcp_server.db.session import async_session_factory

logger = logging.getLogger(__name__)


SETTING_KEYS: tuple[str, ...] = (
    "rag_chat_model",
    "rag_embed_model",
    "pack_author_model",
    "doc_gen_analysis_model",
    "doc_gen_write_model",
)


@dataclass(frozen=True)
class SettingKey:
    key: str
    label: str
    description: str


SETTING_REGISTRY: dict[str, SettingKey] = {
    "rag_chat_model": SettingKey(
        key="rag_chat_model",
        label="RAG chat model",
        description="LLM used for RAG chat answers.",
    ),
    "rag_embed_model": SettingKey(
        key="rag_embed_model",
        label="RAG embedding model",
        description="Embedder used for document indexing and query embedding.",
    ),
    "pack_author_model": SettingKey(
        key="pack_author_model",
        label="Pack author model",
        description="Dedicated model used for one compact Pack design per conversational turn.",
    ),
    "doc_gen_analysis_model": SettingKey(
        key="doc_gen_analysis_model",
        label="Doc gen analysis model",
        description="LLM used for Phase 1 / Phase 2 analysis during doc generation.",
    ),
    "doc_gen_write_model": SettingKey(
        key="doc_gen_write_model",
        label="Doc gen write model",
        description="LLM used for Phase 3 prose writing during doc generation.",
    ),
}


def normalize_payload(payload: dict[str, str | None]) -> dict[str, str]:
    """Validate keys and coerce values. Mirrors the RAG-side contract."""
    out: dict[str, str] = {}
    for key, value in payload.items():
        if key not in SETTING_REGISTRY:
            raise ValueError(f"Unknown setting key: {key!r}")
        if value is None:
            out[key] = ""
            continue
        if not isinstance(value, str):
            raise ValueError(
                f"Value for {key!r} must be a string, got {type(value).__name__}"
            )
        if len(value) > 500:
            raise ValueError(f"Value for {key!r} exceeds 500 chars")
        out[key] = value
    return out


async def get_all(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(InstanceSetting))
    rows = result.scalars().all()
    return {row.key: (row.value or "") for row in rows}


async def get_value(key: str, *, default: str | None = None) -> str | None:
    if key not in SETTING_REGISTRY:
        raise ValueError(f"Unknown setting key: {key!r}")
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(InstanceSetting).where(InstanceSetting.key == key)
            )
            row = result.scalar_one_or_none()
    except Exception as exc:
        # DB unavailable → fall back to env default. Never block a request on
        # the settings table.
        logger.warning("instance_settings get_value(%s) failed: %s", key, exc)
        return default
    if row is None:
        return default
    return row.value or default


async def set_many(
    payload: dict[str, str],
    *,
    actor_user_id: int | None,
    actor_username: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Write `payload` and return (before, after) override maps for audit."""
    normalized = normalize_payload(payload)
    async with async_session_factory() as db:
        before = await get_all(db)
        await db.execute(
            delete(InstanceSetting).where(
                InstanceSetting.key.in_(list(normalized.keys()))
            )
        )
        for key, value in normalized.items():
            if value == "":
                continue
            row = InstanceSetting(
                key=key,
                value=value,
                updated_by_user_id=actor_user_id,
                updated_by_username=actor_username,
            )
            db.add(row)
        await db.commit()
        after = await get_all(db)
    return before, after
