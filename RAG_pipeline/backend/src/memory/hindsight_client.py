"""Thin wrapper around the Hindsight SDK for user memory in the RAG pipeline."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from hindsight_client import Hindsight

from src.config import settings

logger = logging.getLogger(__name__)

_client: Hindsight | None = None


def _get_client() -> Hindsight:
    """Lazy-init the Hindsight client singleton."""
    global _client
    if _client is None:
        _client = Hindsight(base_url=settings.hindsight_url, timeout=10.0)
    return _client


def _bank_id(user_id: str) -> str:
    return f"user-{user_id}"


async def ensure_user_bank(user_id: str) -> None:
    """Create user memory bank if it doesn't already exist (idempotent)."""
    if not settings.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.acreate_bank(
            bank_id=_bank_id(user_id),
            name=f"Memory for user {user_id}",
            mission=(
                "Remember user preferences, topics of interest, question patterns, "
                "and conversation history for this specific user. Track which projects "
                "and topics they care about, their preferred answer style, and prior Q&A context."
            ),
        )
    except Exception:
        # Bank may already exist, or Hindsight may be unreachable
        logger.debug("ensure_user_bank: bank may already exist for %s", user_id)


async def recall_user_memory(user_id: str, query: str) -> list[str]:
    """Recall relevant memories for a user given their current query.

    Returns a list of fact strings, empty list on failure or if disabled.
    """
    if not settings.hindsight_enabled:
        return []
    try:
        client = _get_client()
        response = await client.arecall(
            bank_id=_bank_id(user_id),
            query=query,
            max_tokens=1024,
            budget="low",
        )
        return [r.text for r in response.results]
    except Exception:
        logger.warning("Hindsight recall failed for user %s", user_id, exc_info=True)
        return []


async def retain_interaction(
    user_id: str,
    query: str,
    answer_summary: str,
    conversation_id: int | str | None = None,
) -> None:
    """Store a summary of the user's interaction into their memory bank.

    Called after the RAG pipeline returns a response.
    """
    if not settings.hindsight_enabled:
        return
    try:
        client = _get_client()
        content = (
            f"User asked: {query}\n"
            f"Assistant answered: {answer_summary}"
        )
        doc_id = f"conv-{conversation_id}" if conversation_id else None
        await client.aretain(
            bank_id=_bank_id(user_id),
            content=content,
            context="rag-conversation",
            timestamp=datetime.now(timezone.utc).isoformat(),
            document_id=doc_id,
        )
    except Exception:
        logger.warning("Hindsight retain failed for user %s", user_id, exc_info=True)
