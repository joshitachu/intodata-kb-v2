"""Hindsight memory client for the RAG pipeline.

Two memory banks:
  - Per-user bank (user-{user_id}): preferences, search patterns, Q&A history
  - Quality bank (rag-quality): shared coverage gaps, source usage, latency signals
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from hindsight_client import Hindsight

from src.config import settings

logger = logging.getLogger(__name__)

QUALITY_BANK = "rag-quality"
MAX_USER_MEMORIES = 5
MAX_QUALITY_INSIGHTS = 3

_client: Hindsight | None = None


def _get_client() -> Hindsight:
    """Lazy-init the Hindsight client singleton."""
    global _client
    if _client is None:
        _client = Hindsight(base_url=settings.hindsight_url, timeout=50.0)
    return _client


def _user_bank(user_id: str) -> str:
    return f"user-{user_id}"


# ---------------------------------------------------------------------------
# Bank lifecycle
# ---------------------------------------------------------------------------

async def ensure_user_bank(user_id: str) -> None:
    """Create user memory bank if it doesn't already exist (idempotent)."""
    if not settings.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.acreate_bank(
            bank_id=_user_bank(user_id),
            name=f"Memory for user {user_id}",
            mission=(
                "Remember user preferences, topics of interest, question patterns, "
                "and conversation history for this specific user. Track which projects "
                "and topics they care about, their preferred answer style, and prior Q&A context."
            ),
        )
    except Exception:
        logger.debug("ensure_user_bank: bank may already exist for %s", user_id)


async def ensure_quality_bank() -> None:
    """Create the shared RAG quality bank (idempotent)."""
    if not settings.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.acreate_bank(
            bank_id=QUALITY_BANK,
            name="RAG Quality Tracking",
            mission=(
                "Track RAG pipeline quality signals: zero-result queries (coverage gaps), "
                "most-cited documents, latency by topic, query reformulation patterns, "
                "and source usage statistics. Use this to identify improvements."
            ),
        )
    except Exception:
        logger.debug("ensure_quality_bank: bank may already exist")


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------

async def recall_user_memory(user_id: str, query: str) -> list[str]:
    """Recall relevant memories for a user given their current query.

    Returns up to MAX_USER_MEMORIES fact strings, empty list on failure or if disabled.
    """
    if not settings.hindsight_enabled:
        logger.info("Hindsight recall (user %s): disabled", user_id)
        return []
    try:
        client = _get_client()
        response = await client.arecall(
            bank_id=_user_bank(user_id),
            query=query,
            max_tokens=settings.hindsight_recall_max_tokens,
            budget=settings.hindsight_recall_budget,
        )
        memories = [r.text for r in response.results[:MAX_USER_MEMORIES]]
        logger.info("Hindsight recall (user %s): %d memories returned", user_id, len(memories))
        return memories
    except Exception as e:
        logger.warning("Hindsight recall failed for user %s [%s]: %s", user_id, type(e).__name__, e, exc_info=True)
        return []


async def recall_quality_insights(query: str) -> list[str]:
    """Recall quality insights from the shared bank.

    Returns up to MAX_QUALITY_INSIGHTS hints, empty list on failure or if disabled.
    """
    if not settings.hindsight_enabled:
        logger.info("Hindsight recall (quality): disabled")
        return []
    try:
        client = _get_client()
        response = await client.arecall(
            bank_id=QUALITY_BANK,
            query=query,
            max_tokens=settings.hindsight_recall_max_tokens,
            budget=settings.hindsight_recall_budget,
        )
        insights = [r.text for r in response.results[:MAX_QUALITY_INSIGHTS]]
        logger.info("Hindsight recall (quality): %d insights returned", len(insights))
        return insights
    except Exception as e:
        logger.warning("Hindsight recall (quality) failed [%s]: %s", type(e).__name__, e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Retain
# ---------------------------------------------------------------------------

async def _retain_user_bank(
    user_id: str,
    query: str,
    answer: str,
    sources_used: list[str],
    intent: str,
    ts: str,
    doc_id: str | None,
) -> None:
    """Retain interaction to the per-user memory bank."""
    try:
        client = _get_client()
        user_content = (
            f"User asked: {query}\n"
            f"Assistant answered: {answer[:500]}\n"
            f"Sources: {', '.join(sources_used) if sources_used else 'none'}\n"
            f"Intent: {intent}"
        )
        await client.aretain(
            bank_id=_user_bank(user_id),
            content=user_content,
            context="rag-conversation",
            timestamp=ts,
            document_id=doc_id,
        )
        logger.info("Hindsight retain (user %s): user bank stored", user_id)
    except Exception as e:
        logger.warning("Hindsight retain (user %s) failed [%s]: %s", user_id, type(e).__name__, e, exc_info=True)


async def _retain_quality_bank(
    user_id: str,
    query: str,
    sources_used: list[str],
    intent: str,
    search_result_count: int,
    latency_ms: int,
    ts: str,
    doc_id: str | None,
) -> None:
    """Retain interaction to the shared RAG quality bank."""
    try:
        client = _get_client()
        quality_content = (
            f"Query: {query}\n"
            f"Intent: {intent}\n"
            f"Search results: {search_result_count}\n"
            f"Sources cited: {', '.join(sources_used) if sources_used else 'none'}\n"
            f"Latency: {latency_ms}ms"
        )
        if search_result_count == 0:
            quality_content += "\nFLAG: Zero search results — potential coverage gap"
        await client.aretain(
            bank_id=QUALITY_BANK,
            content=quality_content,
            context="rag-quality-signal",
            timestamp=ts,
            document_id=doc_id,
        )
        logger.info("Hindsight retain (user %s): quality bank stored", user_id)
    except Exception as e:
        logger.warning("Hindsight retain (quality) failed [%s]: %s", type(e).__name__, e, exc_info=True)


async def retain_interaction(
    user_id: str,
    query: str,
    answer: str,
    sources_used: list[str],
    conversation_id: int | str | None = None,
    search_result_count: int = 0,
    latency_ms: int = 0,
    intent: str = "qa",
) -> None:
    """Store an interaction into BOTH the user bank AND the quality bank.

    Fire-and-forget: callers should use asyncio.create_task() to avoid blocking.
    Both banks are retained concurrently via asyncio.gather().
    """
    if not settings.hindsight_enabled:
        logger.info("Hindsight retain (user %s): disabled", user_id)
        return

    logger.info("Hindsight retain (user %s): storing interaction", user_id)
    doc_id = f"conv-{conversation_id}" if conversation_id else None
    ts = datetime.now(timezone.utc).isoformat()

    await asyncio.gather(
        _retain_user_bank(user_id, query, answer, sources_used, intent, ts, doc_id),
        _retain_quality_bank(user_id, query, sources_used, intent, search_result_count, latency_ms, ts, doc_id),
        return_exceptions=True,
    )
