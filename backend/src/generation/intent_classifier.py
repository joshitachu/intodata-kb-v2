"""LLM-based query router for routing user queries.

Uses a small LLM to decide whether a query needs KB search or is chitchat,
and simultaneously fixes typos/normalizes the query for better search results.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum

import ollama as ollama_lib

from src.config import settings

logger = logging.getLogger(__name__)


class Action(str, Enum):
    SEARCH = "search"
    RESPOND = "respond"


@dataclass
class QueryAnalysis:
    action: Action
    cleaned_query: str


ROUTER_PROMPT = (
    "You are a query router for a company knowledge base. "
    "Analyze the user's message and respond with ONLY a JSON object.\n\n"
    "Decide:\n"
    '- "search" if the user is asking a question or requesting information '
    "that could be in company documents\n"
    '- "respond" if the user is just greeting, thanking, saying goodbye, '
    "or making small talk\n\n"
    "Also fix any typos or grammatical issues in the query to improve search results.\n\n"
    "Respond with ONLY this JSON (no markdown, no explanation):\n"
    '{"action": "search" or "respond", "cleaned_query": "corrected query text"}'
)


def _parse_router_response(raw: str, original_query: str) -> QueryAnalysis:
    """Parse the JSON response from the router LLM.

    Falls back to Action.SEARCH with the original query if parsing fails.
    """
    try:
        # Strip markdown code fences if the model wraps its response
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
        action_str = data.get("action", "search").lower().strip()
        cleaned = data.get("cleaned_query", original_query).strip()

        action = Action.RESPOND if action_str == "respond" else Action.SEARCH

        if not cleaned:
            cleaned = original_query

        return QueryAnalysis(action=action, cleaned_query=cleaned)
    except (json.JSONDecodeError, KeyError, AttributeError) as e:
        logger.warning(f"Router JSON parse failed: {e} | raw={raw!r}")
        return QueryAnalysis(action=Action.SEARCH, cleaned_query=original_query)


async def analyze_query(query: str) -> QueryAnalysis:
    """Route a user query using a small LLM.

    Returns a QueryAnalysis with:
    - action: SEARCH (needs retrieval) or RESPOND (chitchat, answer directly)
    - cleaned_query: typo-corrected/normalized version of the query
    """
    stripped = query.strip()
    if not stripped:
        return QueryAnalysis(action=Action.RESPOND, cleaned_query=stripped)

    try:
        client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
        response = await client.chat(
            model=settings.ollama_router_model,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": stripped},
            ],
        )
        raw = response["message"]["content"]
        logger.debug(f"Router raw response: {raw!r}")
        return _parse_router_response(raw, stripped)
    except Exception as e:
        logger.warning(f"Router LLM call failed: {e} — falling back to SEARCH")
        return QueryAnalysis(action=Action.SEARCH, cleaned_query=stripped)
