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


class QueryType(str, Enum):
    QA = "qa"
    SUMMARY = "summary"


@dataclass
class QueryAnalysis:
    action: Action
    cleaned_query: str
    query_type: QueryType = QueryType.QA


ROUTER_PROMPT = (
    "You are a query router for a company knowledge base. "
    "Analyze the user's message and respond with ONLY a JSON object.\n\n"
    "Decide:\n"
    '- "action": "search" if the user is asking a question or requesting information '
    "that could be in company documents\n"
    '- "action": "respond" if the user is just greeting, thanking, saying goodbye, '
    "or making small talk\n\n"
    "Also determine the query type:\n"
    '- "query_type": "summary" if the user wants a summary, overview, or recap of a document '
    "(e.g. 'summarize the HR policy', 'give me an overview of the onboarding guide')\n"
    '- "query_type": "qa" for all other questions (specific facts, procedures, comparisons)\n\n'
    "Also fix any typos or grammatical issues in the query to improve search results.\n\n"
    "Respond with ONLY this JSON (no markdown, no explanation):\n"
    '{"action": "search" or "respond", "query_type": "qa" or "summary", "cleaned_query": "corrected query text"}'
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
        query_type_str = data.get("query_type", "qa").lower().strip()

        action = Action.RESPOND if action_str == "respond" else Action.SEARCH
        query_type = QueryType.SUMMARY if query_type_str == "summary" else QueryType.QA

        if not cleaned:
            cleaned = original_query

        return QueryAnalysis(action=action, cleaned_query=cleaned, query_type=query_type)
    except (json.JSONDecodeError, KeyError, AttributeError) as e:
        logger.warning(f"Router JSON parse failed: {e} | raw={raw!r}")
        return QueryAnalysis(action=Action.SEARCH, cleaned_query=original_query)


async def analyze_query(query: str, conversation_context: str | None = None) -> QueryAnalysis:
    """Route a user query using a small LLM.

    Returns a QueryAnalysis with:
    - action: SEARCH (needs retrieval) or RESPOND (chitchat, answer directly)
    - cleaned_query: typo-corrected/normalized version of the query
    - conversation_context: optional recent conversation history to resolve
      pronouns like 'it', 'that', 'the previous document'
    """
    stripped = query.strip()
    if not stripped:
        return QueryAnalysis(action=Action.RESPOND, cleaned_query=stripped)

    user_content = stripped
    if conversation_context:
        user_content = (
            f"Recent conversation:\n{conversation_context}\n\n"
            f"Current message: {stripped}"
        )

    try:
        client = ollama_lib.AsyncClient(host=settings.ollama_base_url)
        response = await client.chat(
            model=settings.ollama_router_model,
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        raw = response["message"]["content"]
        logger.debug(f"Router raw response: {raw!r}")
        return _parse_router_response(raw, stripped)
    except Exception as e:
        logger.warning(f"Router LLM call failed: {e} — falling back to SEARCH")
        return QueryAnalysis(action=Action.SEARCH, cleaned_query=stripped)
