"""LLM-powered query router with safe fallback.

Uses the LLM provider chain (Kimi -> Ollama) to classify queries into:
- SEARCH: document content questions -> Qdrant vector search
- METADATA: structured data questions -> PostgreSQL
- RESPOND: empty/invalid queries -> skip everything

On LLM failure, defaults to SEARCH (no regressions).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

_SUMMARY_PATTERN = re.compile(
    r"\b(summarize|summarise|summary|overview|recap|rundown|give me a rundown|brief me)\b",
    re.IGNORECASE,
)

CLASSIFIER_SYSTEM_PROMPT = """\
You are an intent classifier for a knowledge-base system. The system has:
- **Projects**: registered source code projects (with metadata like platform, status, scan history)
- **Documents**: ingested files (PDF, DOCX, etc.) stored in a vector database for search
- **Statistics**: counts, summaries of what's in the system

Classify the user's query into ONE of these categories:

1. **search** — The user wants to find information IN document content. Questions about topics, concepts, processes, technical details. This is the default.
2. **project_list** — The user asks what projects exist, which projects are available, how many projects there are.
3. **project_detail** — The user asks about a SPECIFIC project's metadata (status, platform, scan history, details).
4. **document_stats** — The user asks how many documents/files there are, document counts, statistics.
5. **document_list** — The user asks to list or show documents/files.
6. **system_overview** — The user asks what's in the system/knowledge base, what data is available, a general overview of available information.
7. **hybrid** — The user asks a content question that also references a specific project (e.g. "what Talend jobs does project X have?" needs both project metadata AND document search).
8. **my_profile** — The user asks about their own identity, role, permissions, or project access. Examples: "what's my role?", "what are my permissions?", "which projects can I access?", "who am I?", "what can I do?"
9. **summary** — The user wants to summarize a specific document (e.g. "summarize the HR policy", "give me an overview of document X").
10. **conversation_summary** — The user wants to summarize or recap the current conversation/chat (e.g. "summarize our chat", "what have we discussed?", "recap everything", "summarize all your answers"). Only use this when a conversation context note is provided.

Also extract:
- **project**: If the query mentions a specific project name, extract it (just the name, lowercase). Null if no specific project mentioned.

Note: When a "[Context: ...]" note is appended to the message indicating an active conversation, consider whether the user is referring to the conversation itself vs. a specific document.

Note: Questions asking "why", "how", "what is this based on", "explain the methodology", or "what are the details" about a project's scores, findings, or assessments are **search** queries (they need document content), not **project_detail** (which is for structural metadata like platform, status, scan dates).

Respond with ONLY a JSON object, no markdown, no explanation:
{"action": "search|project_list|project_detail|document_stats|document_list|system_overview|hybrid|my_profile|summary|conversation_summary", "project": "name_or_null"}
"""


class Action(str, Enum):
    SEARCH = "search"
    RESPOND = "respond"
    METADATA = "metadata"
    CONVERSATION_SUMMARY = "conversation_summary"


class QueryType(str, Enum):
    QA = "qa"
    SUMMARY = "summary"


class MetadataIntent(str, Enum):
    PROJECT_LIST = "project_list"
    PROJECT_DETAIL = "project_detail"
    DOCUMENT_STATS = "document_stats"
    DOCUMENT_LIST = "document_list"
    SYSTEM_OVERVIEW = "system_overview"
    HYBRID = "hybrid"
    MY_PROFILE = "my_profile"


_METADATA_ACTIONS = {
    "project_list", "project_detail", "document_stats",
    "document_list", "system_overview", "hybrid", "my_profile",
}


@dataclass
class QueryAnalysis:
    action: Action
    cleaned_query: str
    query_type: QueryType = QueryType.QA
    metadata_intent: MetadataIntent | None = None
    extracted_project: str | None = None


def _detect_query_type(query: str) -> QueryType:
    """Detect if the query is asking for a summary via keyword matching."""
    if _SUMMARY_PATTERN.search(query):
        return QueryType.SUMMARY
    return QueryType.QA


def _parse_classifier_response(raw: str) -> tuple[str, str | None]:
    """Parse JSON response from the LLM classifier.

    Returns (action_str, project_name_or_none).
    Raises ValueError if unparseable.
    """
    # Strip markdown fences if the LLM wrapped it
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    data = json.loads(cleaned)
    action = data.get("action", "search").lower().strip()
    project = data.get("project")
    if project and isinstance(project, str):
        project = project.strip().lower()
        if project in ("null", "none", ""):
            project = None
    else:
        project = None
    return action, project


async def _llm_classify(query: str, conversation_context: str | None = None) -> tuple[str, str | None]:
    """Call the LLM to classify the query. Returns (action_str, project)."""
    import httpx
    from src.config import settings

    user_content = query
    if conversation_context:
        user_content += f"\n\n[Context: {conversation_context}]"

    messages = [
        {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    # Try Kimi first (fastest, ~0.5-1s for short responses)
    for provider_name in settings.llm_providers:
        try:
            if provider_name == "kimi" and settings.kimi_api_key:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(
                    base_url="https://api.moonshot.ai/v1",
                    api_key=settings.kimi_api_key,
                    timeout=httpx.Timeout(10.0, connect=3.0),
                    max_retries=0,
                )
                response = await client.chat.completions.create(
                    model=settings.kimi_llm_model,
                    messages=messages,
                    temperature=settings.kimi_temperature,
                    max_tokens=64,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                raw = response.choices[0].message.content
                return _parse_classifier_response(raw)

            elif provider_name == "ollama":
                import ollama as ollama_lib
                client = ollama_lib.AsyncClient(
                    host=settings.ollama_base_url,
                    timeout=httpx.Timeout(15.0, connect=3.0),
                )
                response = await client.chat(
                    model=settings.ollama_router_model,
                    messages=messages,
                    format="json",
                )
                raw = response["message"]["content"]
                return _parse_classifier_response(raw)

        except Exception as e:
            logger.warning("Intent classifier %s failed: [%s] %s",
                           provider_name, type(e).__name__, e)
            continue

    raise RuntimeError("All intent classifier providers failed")


def analyze_query(query: str, conversation_context: str | None = None) -> QueryAnalysis:
    """Synchronous fast-path for empty queries only.

    For real queries, use analyze_query_async() which calls the LLM.
    This function is kept for backward compatibility in non-async contexts.
    """
    stripped = query.strip()
    if not stripped:
        return QueryAnalysis(action=Action.RESPOND, cleaned_query=stripped)

    query_type = _detect_query_type(stripped)
    return QueryAnalysis(action=Action.SEARCH, cleaned_query=stripped, query_type=query_type)


async def analyze_query_async(query: str, conversation_context: str | None = None) -> QueryAnalysis:
    """Classify query using LLM. Falls back to SEARCH on failure.

    Uses the LLM provider chain to understand intent in any language or phrasing.
    """
    stripped = query.strip()
    if not stripped:
        return QueryAnalysis(action=Action.RESPOND, cleaned_query=stripped)

    query_type = _detect_query_type(stripped)

    from src.config import settings
    if not settings.metadata_queries_enabled:
        return QueryAnalysis(action=Action.SEARCH, cleaned_query=stripped, query_type=query_type)

    try:
        t0 = time.perf_counter()
        action_str, project = await _llm_classify(stripped, conversation_context)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if action_str == "conversation_summary":
            logger.info(
                "LLM classify (%dms): %r -> CONVERSATION_SUMMARY",
                elapsed_ms, stripped[:80],
            )
            return QueryAnalysis(
                action=Action.CONVERSATION_SUMMARY,
                cleaned_query=stripped,
            )
        elif action_str == "summary":
            logger.info(
                "LLM classify (%dms): %r -> SEARCH/SUMMARY",
                elapsed_ms, stripped[:80],
            )
            return QueryAnalysis(
                action=Action.SEARCH,
                cleaned_query=stripped,
                query_type=QueryType.SUMMARY,
            )
        elif action_str in _METADATA_ACTIONS:
            intent = MetadataIntent(action_str)
            logger.info(
                "LLM classify (%dms): %r -> METADATA/%s, project=%s",
                elapsed_ms, stripped[:80], intent.value, project,
            )
            return QueryAnalysis(
                action=Action.METADATA,
                cleaned_query=stripped,
                metadata_intent=intent,
                extracted_project=project,
            )
        else:
            logger.info(
                "LLM classify (%dms): %r -> SEARCH, type=%s",
                elapsed_ms, stripped[:80], query_type.value,
            )
            return QueryAnalysis(
                action=Action.SEARCH,
                cleaned_query=stripped,
                query_type=query_type,
            )

    except Exception as e:
        logger.warning("Intent classification failed, defaulting to SEARCH: %s", e)
        return QueryAnalysis(action=Action.SEARCH, cleaned_query=stripped, query_type=query_type)
