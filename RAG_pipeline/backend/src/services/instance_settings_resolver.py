"""Resolve effective per-instance model values.

Used by the RAG-side LLM/embedder code paths (`llm_client`, `embedder`) to
honor superadmin overrides set through the Models dashboard without restarting
the service. Values come from the `instance_settings` table; falls back to the
pydantic-settings defaults (`settings.kimi_llm_model` etc.) when no override
is set.

Single-row indexed lookups are cheap, but caching keeps the hot path off the
DB. Cache TTL is short (5s) so dashboard saves are visible quickly without
forcing a hot restart.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from sqlalchemy import select

from src.config import settings
from src.db.models import InstanceSetting
from src.db.session import async_session_factory

logger = logging.getLogger(__name__)


_CACHE: dict[str, str | None] = {}
_CACHE_LOADED_AT: float = 0.0
_CACHE_TTL_S: float = 5.0
_CACHE_LOCK = asyncio.Lock()


async def _refresh_cache() -> None:
    """Reload the full override map from the DB into the in-process cache."""
    global _CACHE_LOADED_AT
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(InstanceSetting))
            rows = result.scalars().all()
        _CACHE.clear()
        for row in rows:
            _CACHE[row.key] = row.value
        _CACHE_LOADED_AT = time.monotonic()
    except Exception as exc:
        logger.warning("instance_settings cache refresh failed: %s", exc)


async def get_value(key: str, *, default: str | None = None) -> str | None:
    """Return the override value for `key` or `default` when no override.

    Cache TTL is 5s. Reads from env var of the same name when no override and
    no explicit default — handy for keys whose canonical default lives in env
    (e.g. ``KIMI_LLM_MODEL``).
    """
    if _CACHE_LOADED_AT == 0.0 or (time.monotonic() - _CACHE_LOADED_AT) > _CACHE_TTL_S:
        async with _CACHE_LOCK:
            if _CACHE_LOADED_AT == 0.0 or (time.monotonic() - _CACHE_LOADED_AT) > _CACHE_TTL_S:
                await _refresh_cache()
    value = _CACHE.get(key)
    if value:
        return value
    if default is not None:
        return default
    # Last-resort: read env var directly. Keeps this module agnostic of which
    # pydantic setting corresponds to a given key, without forcing callers to
    # pass a default for every call site.
    return os.environ.get(key.upper()) or None


async def get_kimi_llm_model() -> str:
    val = await get_value("rag_chat_model", default=settings.kimi_llm_model)
    return val or settings.kimi_llm_model


async def get_ollama_llm_model() -> str:
    val = await get_value("rag_chat_model", default=settings.ollama_llm_model)
    return val or settings.ollama_llm_model


async def get_voyage_embed_model() -> str:
    val = await get_value("rag_embed_model", default=settings.voyage_embed_model)
    return val or settings.voyage_embed_model


async def get_ollama_embed_model() -> str:
    val = await get_value("rag_embed_model", default=settings.ollama_embed_model)
    return val or settings.ollama_embed_model


def reset_cache_for_tests() -> None:
    """Clear cache state. Tests call this between cases to force a refresh."""
    global _CACHE_LOADED_AT
    _CACHE.clear()
    _CACHE_LOADED_AT = 0.0