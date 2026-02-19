"""Query expansion via LLM-generated alternative phrasings."""
from __future__ import annotations

import logging

import ollama as ollama_lib

from src.config import settings

logger = logging.getLogger(__name__)

EXPAND_PROMPT = (
    "You are a search query expansion assistant. Given a user's search query, "
    "generate exactly 2 alternative phrasings that capture the same intent "
    "but use different keywords or vocabulary.\n\n"
    "Rules:\n"
    "- Each alternative should be 3-10 words\n"
    "- Use synonyms, expand abbreviations, and rephrase\n"
    "- Do NOT add new topics — stay on the same intent\n"
    "- Output ONLY the 2 alternatives, one per line, no numbering\n\n"
    "Query: {query}\n\n"
    "Alternatives:"
)


def should_expand_query(query: str) -> bool:
    """Heuristic: decide if a query benefits from expansion.

    Expand short/ambiguous queries. Skip long, specific ones.
    """
    words = query.strip().split()
    # Short queries (1-4 words) or queries with common abbreviations benefit most
    if len(words) <= 4:
        return True
    # Queries with abbreviations (all-caps words)
    if any(w.isupper() and len(w) >= 2 for w in words):
        return True
    # Long specific queries don't need expansion
    if len(words) >= 8:
        return False
    return True


async def expand_query(query: str) -> list[str]:
    """Generate alternative query phrasings using LLM.

    Returns a list of 1-3 queries (original + alternatives).
    Falls back to [original] if LLM fails.
    """
    if not settings.query_expansion_enabled:
        return [query]

    if not should_expand_query(query):
        logger.info(f"Skipping query expansion for specific query: {query[:60]!r}")
        return [query]

    try:
        client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
        response = await client.chat(
            model=settings.ollama_router_model,
            messages=[
                {"role": "user", "content": EXPAND_PROMPT.format(query=query)},
            ],
        )
        raw = response["message"]["content"].strip()

        # Parse alternatives (one per line)
        alternatives = [
            line.strip().lstrip("0123456789.-) ")
            for line in raw.split("\n")
            if line.strip() and len(line.strip()) > 3
        ][:2]  # max 2 alternatives

        if alternatives:
            result = [query] + alternatives
            logger.info(f"Expanded query: {query!r} → {result}")
            return result

    except Exception as e:
        logger.warning(f"Query expansion failed, using original: {e}")

    return [query]
