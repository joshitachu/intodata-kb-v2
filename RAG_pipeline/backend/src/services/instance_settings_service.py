"""Per-instance model settings service.

Reads and writes the `instance_settings` table. Each row is one key → value,
where key is a registered constant and value is the model id chosen by a
superadmin. Wire-through callers should use `get_value(key)` which returns
None when no override is set, so they can fall back to env / pydantic defaults.

This module deliberately keeps the catalogue of *valid* values centralized so
the dashboard PUT endpoint can validate incoming model ids against the same
list the runtime supports. A bad id is rejected with HTTP 400 before reaching
the DB.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import InstanceSetting


# Registered keys — keep in sync with the dashboard ModelsPage rows.
SETTING_KEYS: tuple[str, ...] = (
    "rag_chat_model",          # RAG chat LLM (kimi / ollama) — overrides settings.kimi_llm_model + settings.ollama_llm_model
    "rag_embed_model",         # Embedder model — overrides settings.voyage_embed_model + settings.ollama_embed_model
    "deep_dive_model",         # MCP deep dive repo agent — overrides config.deep_dive_model
    "doc_gen_analysis_model",  # MCP doc generation analysis — overrides config.doc_generation_model
    "doc_gen_write_model",     # MCP doc generation write — overrides config.doc_generation_write_model
)


@dataclass(frozen=True)
class SettingKey:
    key: str
    label: str
    description: str
    # Whether this key accepts the empty string as "unset" (i.e. delete the
    # row). All do — an empty value means "fall back to env var".
    accepts_empty: bool = True


SETTING_REGISTRY: dict[str, SettingKey] = {
    "rag_chat_model": SettingKey(
        key="rag_chat_model",
        label="RAG chat model",
        description="LLM used for RAG chat answers and tool-aware runtime calls.",
    ),
    "rag_embed_model": SettingKey(
        key="rag_embed_model",
        label="RAG embedding model",
        description="Embedder used for document indexing and query embedding.",
    ),
    "deep_dive_model": SettingKey(
        key="deep_dive_model",
        label="Deep dive model",
        description="LLM used by the MCP deep-dive repo agent.",
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
    """Validate keys and coerce values.

    - Unknown keys → ValueError.
    - None or empty string values are allowed (deletes the row on PUT).
    - Non-string values → ValueError.
    """
    out: dict[str, str] = {}
    for key, value in payload.items():
        if key not in SETTING_REGISTRY:
            raise ValueError(f"Unknown setting key: {key!r}")
        if value is None:
            out[key] = ""
            continue
        if not isinstance(value, str):
            raise ValueError(f"Value for {key!r} must be a string, got {type(value).__name__}")
        if len(value) > 500:
            raise ValueError(f"Value for {key!r} exceeds 500 chars")
        out[key] = value
    return out


async def get_all(db: AsyncSession) -> dict[str, str]:
    """Return all current overrides as {key: value}, omitting unset rows."""
    result = await db.execute(select(InstanceSetting))
    rows = result.scalars().all()
    return {row.key: (row.value or "") for row in rows}


async def get_value(db: AsyncSession, key: str) -> str | None:
    """Return the override for one key, or None when unset."""
    if key not in SETTING_REGISTRY:
        raise ValueError(f"Unknown setting key: {key!r}")
    result = await db.execute(
        select(InstanceSetting).where(InstanceSetting.key == key)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return row.value


async def set_many(
    db: AsyncSession,
    payload: dict[str, str],
    *,
    actor_user_id: int | None,
    actor_username: str | None,
) -> list[InstanceSetting]:
    """Replace the override set with `payload`.

    Empty-string values delete the row. Unknown keys are rejected by
    `normalize_payload` before this is called.
    """
    normalized = normalize_payload(payload)
    keys: Iterable[str] = list(normalized.keys())
    # Delete-then-insert keeps semantics obvious. Single-row-per-key primary
    # key means upsert would be equivalent but harder to audit.
    await db.execute(
        delete(InstanceSetting).where(InstanceSetting.key.in_(keys))
    )
    rows: list[InstanceSetting] = []
    for key, value in normalized.items():
        if value == "":
            continue  # empty value clears the override
        row = InstanceSetting(
            key=key,
            value=value,
            updated_by_user_id=actor_user_id,
            updated_by_username=actor_username,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows