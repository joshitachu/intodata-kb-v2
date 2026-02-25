"""Fast heuristic query router — zero LLM calls.

Classifies queries using keyword matching for summary detection.
All real queries go through SEARCH; Qdrant + NO_RESULTS_PROMPT handles chitchat naturally.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

_SUMMARY_PATTERN = re.compile(
    r"\b(summarize|summarise|summary|overview|recap|rundown|give me a rundown|brief me)\b",
    re.IGNORECASE,
)


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


def _detect_query_type(query: str) -> QueryType:
    """Detect if the query is asking for a summary via keyword matching."""
    if _SUMMARY_PATTERN.search(query):
        return QueryType.SUMMARY
    return QueryType.QA


def analyze_query(query: str, conversation_context: str | None = None) -> QueryAnalysis:
    """Classify query type using fast heuristics. No LLM call.

    Returns a QueryAnalysis with:
    - action: SEARCH (always for real queries) or RESPOND (empty query only)
    - cleaned_query: the stripped query text
    - query_type: QA or SUMMARY (detected via keywords)

    The conversation_context parameter is accepted for API compatibility
    but not used — heuristic classification doesn't need it.
    """
    stripped = query.strip()
    if not stripped:
        return QueryAnalysis(action=Action.RESPOND, cleaned_query=stripped)

    query_type = _detect_query_type(stripped)

    logger.debug(
        f"Heuristic classify: {stripped[:80]!r} -> action=SEARCH, type={query_type.value}"
    )

    # Always SEARCH — let Qdrant + NO_RESULTS_PROMPT handle chitchat naturally
    return QueryAnalysis(action=Action.SEARCH, cleaned_query=stripped, query_type=query_type)
