"""Hindsight memory client for the RAG pipeline.

Memory banks:
  - Per-user bank (user-{user_id}): preferences, search patterns, Q&A history
  - Per-project bank (project-{project_id}): project-level decisions, patterns, topics
  - Quality bank (rag-quality): shared coverage gaps, source usage, latency signals
  - Corrections bank (rag-corrections): user-provided factual corrections
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

try:
    from hindsight_client import Hindsight
except ImportError:
    Hindsight = None

from src.config import settings

logger = logging.getLogger(__name__)

QUALITY_BANK = "rag-quality"
CORRECTIONS_BANK = "rag-corrections"
PROJECT_BANK_PREFIX = "project-"

MAX_USER_MEMORIES = 5
MAX_QUALITY_INSIGHTS = 3
MAX_CORRECTIONS = 5

# Signals that a user message is correcting a previous assistant answer.
# Only checked when there is a prior assistant message in the conversation.
CORRECTION_SIGNALS = [
    "no,", "no that's", "that's wrong", "that's incorrect", "actually,",
    "it should be", "it's actually", "not correct", "wrong,", "incorrect,",
    "that's not right", "the correct answer", "correction:", "update:",
    "it's not", "they changed", "it was updated", "no longer",
]

_client: Hindsight | None = None


def _get_client() -> Hindsight | None:
    """Lazy-init the Hindsight client singleton."""
    global _client
    if Hindsight is None:
        return None
    if _client is None:
        _client = Hindsight(base_url=settings.hindsight_url, timeout=settings.hindsight_timeout)
    return _client


def _user_bank(user_id: str) -> str:
    return f"user-{user_id}"


def _project_bank(project_id: str) -> str:
    return f"{PROJECT_BANK_PREFIX}{project_id}"


# ---------------------------------------------------------------------------
# Retention gating
# ---------------------------------------------------------------------------

class RetentionLevel(str, Enum):
    """What to retain for an interaction."""
    FULL = "full"          # user + project + quality banks
    QUALITY_ONLY = "quality_only"  # quality bank only (coverage gap or structural)
    SKIP = "skip"          # retain nothing


@dataclass
class RetentionDecision:
    level: RetentionLevel
    reason: str


def decide_retention(
    action: str,
    query_type: str,
    search_result_count: int,
    answer_length: int,
) -> RetentionDecision:
    """Decide what to retain based on intent classifier output.

    - RESPOND / CONVERSATION_SUMMARY -> SKIP (chitchat/recap)
    - METADATA with no search results -> QUALITY_ONLY (structural only)
    - Zero search results -> QUALITY_ONLY (coverage gap signal)
    - Short answers (<50 chars) -> QUALITY_ONLY (not substantive)
    - Everything else (including metadata+results) -> FULL
    """
    if action in ("respond", "conversation_summary"):
        return RetentionDecision(level=RetentionLevel.SKIP, reason=f"action={action}")

    # Metadata with no search results → quality only (pure structural query)
    if action == "metadata" and search_result_count == 0:
        return RetentionDecision(level=RetentionLevel.QUALITY_ONLY, reason="metadata query (no search results)")

    # Zero search results → quality only (coverage gap signal)
    if search_result_count == 0:
        return RetentionDecision(level=RetentionLevel.QUALITY_ONLY, reason="zero search results")

    if answer_length < 50:
        return RetentionDecision(level=RetentionLevel.QUALITY_ONLY, reason="short answer")

    # Everything else with results (including metadata/hybrid) → FULL
    return RetentionDecision(level=RetentionLevel.FULL, reason="substantive search interaction")


# ---------------------------------------------------------------------------
# Tag + entity helpers
# ---------------------------------------------------------------------------

def _build_tags(
    project_ids: list[str] | None = None,
    action: str | None = None,
    topic: str | None = None,
) -> list[str]:
    """Build standardized tags for Hindsight retention.

    Tag format: namespace:value (e.g. project:talend, intent:search, topic:job-orchestration)
    """
    tags: list[str] = []
    if project_ids:
        for pid in project_ids:
            tags.append(f"project:{pid}")
    if action:
        tags.append(f"intent:{action}")
    if topic:
        tags.append(f"topic:{topic}")
    return tags


def extract_topic_tag(query: str, sources: list[str]) -> str | None:
    """Extract a topic tag from source filenames.

    Looks at source filenames for patterns like 'talend_job_orchestration.md'
    and extracts 'job-orchestration' as the topic.
    """
    if not sources:
        return None

    # Try to extract from the first source filename
    for source in sources:
        # Strip project prefix and extension
        name = source.rsplit("/", 1)[-1] if "/" in source else source
        name = re.sub(r"\.\w+$", "", name)  # remove extension
        # Remove common prefixes
        name = re.sub(r"^(talend|workato|generic)_", "", name, flags=re.IGNORECASE)
        if name and len(name) > 2:
            # Convert underscores/camelCase to kebab-case
            topic = re.sub(r"[_\s]+", "-", name).lower()
            topic = re.sub(r"([a-z])([A-Z])", r"\1-\2", topic).lower()
            return topic[:50]  # cap length
    return None


def extract_entities_from_sources(sources: list[str]) -> list[dict[str, str]]:
    """Extract project names and document names as entities for Hindsight.

    Source filenames are in format 'project_id/filename.ext'.
    Returns dicts with 'text' and 'type' keys as required by hindsight_client.
    """
    entities: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        if "/" in source:
            project, filename = source.split("/", 1)
            if project not in seen:
                entities.append({"text": project, "type": "project"})
                seen.add(project)
            doc_name = re.sub(r"\.\w+$", "", filename)
            if doc_name not in seen:
                entities.append({"text": doc_name, "type": "document"})
                seen.add(doc_name)
        else:
            doc_name = re.sub(r"\.\w+$", "", source)
            if doc_name not in seen:
                entities.append({"text": doc_name, "type": "document"})
                seen.add(doc_name)
    return entities[:10]  # cap at 10 entities


# ---------------------------------------------------------------------------
# Content distillation
# ---------------------------------------------------------------------------

DISTILL_SYSTEM_PROMPT = (
    "You are a knowledge distiller. Transform a Q&A interaction into a concise, "
    "reusable knowledge statement. Focus on the factual answer, not the question format.\n\n"
    "Rules:\n"
    "- Output a single paragraph (2-4 sentences max)\n"
    "- State the knowledge as a fact, not as 'the user asked...'\n"
    "- Include key details (names, values, relationships)\n"
    "- If sources are mentioned, note which document(s) contain this info\n"
    "- Write in present tense"
)


async def distill_interaction(
    query: str,
    answer: str,
    sources: list[str],
) -> str:
    """Distill a Q&A pair into a reusable knowledge statement.

    Uses the LLM to transform 'User asked X, got Y' into a fact-based statement.
    Falls back to a structured format if LLM fails or distillation is disabled.
    """
    if not settings.hindsight_distill_before_retain:
        return _structured_fallback(query, answer, sources)

    try:
        from src.generation.llm_client import generate_answer

        user_content = (
            f"Q: {query}\n"
            f"A: {answer[:800]}\n"
            f"Sources: {', '.join(sources[:5]) if sources else 'none'}"
        )
        messages = [
            {"role": "system", "content": DISTILL_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        distilled = await generate_answer(messages)
        return distilled.strip()[:1000]
    except Exception as e:
        logger.warning("Distillation failed, using structured fallback: %s", e)
        return _structured_fallback(query, answer, sources)


def _structured_fallback(query: str, answer: str, sources: list[str]) -> str:
    """Structured format when distillation is disabled or fails."""
    source_str = ", ".join(sources[:3]) if sources else "none"
    return f"[Knowledge from a past session] {answer[:500]} (topic: {query[:100]}, sources: {source_str})"


# ---------------------------------------------------------------------------
# Bank lifecycle
# ---------------------------------------------------------------------------

async def ensure_user_bank(user_id: str) -> None:
    """Create user memory bank if it doesn't already exist (idempotent)."""
    if not settings.hindsight_enabled:
        return
    client = _get_client()
    if client is None:
        logger.warning("ensure_user_bank: hindsight package not installed")
        return
    try:
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


async def ensure_corrections_bank() -> None:
    """Create the shared corrections bank (idempotent)."""
    if not settings.hindsight_enabled:
        return
    try:
        client = _get_client()
        await client.acreate_bank(
            bank_id=CORRECTIONS_BANK,
            name="RAG Corrections",
            mission=(
                "Store factual corrections that users provide about document content. "
                "These corrections should be recalled with high priority when relevant "
                "queries are made, as they represent verified updates to source material."
            ),
        )
    except Exception:
        logger.debug("ensure_corrections_bank: bank may already exist")


async def ensure_project_bank(project_id: str) -> None:
    """Create a project-scoped memory bank (idempotent)."""
    if not settings.hindsight_enabled or not settings.hindsight_project_banks_enabled:
        return
    try:
        client = _get_client()
        await client.acreate_bank(
            bank_id=_project_bank(project_id),
            name=f"Project memory: {project_id}",
            mission=(
                f"Track knowledge, decisions, and patterns specific to project '{project_id}'. "
                "Remember frequently asked topics, key architectural patterns, important document "
                "sections, and recurring questions about this project. Use observations to "
                "consolidate repeated topics into reusable knowledge."
            ),
        )
    except Exception:
        logger.debug("ensure_project_bank: bank may already exist for %s", project_id)


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------

async def recall_user_memory(
    user_id: str,
    query: str,
    project_ids: list[str] | None = None,
) -> list[str]:
    """Recall relevant memories for a user given their current query.

    When project_ids are provided, includes project tags for scoped recall.
    Returns up to MAX_USER_MEMORIES fact strings, empty list on failure or if disabled.
    """
    if not settings.hindsight_enabled:
        logger.info("Hindsight recall (user %s): disabled", user_id)
        return []
    try:
        client = _get_client()
        kwargs: dict = {
            "bank_id": _user_bank(user_id),
            "query": query,
            "max_tokens": settings.hindsight_recall_max_tokens,
            "budget": settings.hindsight_recall_budget,
        }
        if project_ids:
            kwargs["tags"] = [f"project:{pid}" for pid in project_ids]
            kwargs["tags_match"] = "any"  # match any of the project tags
        response = await client.arecall(**kwargs)
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


async def recall_corrections(
    query: str,
    project_ids: list[str] | None = None,
) -> list[str]:
    """Recall relevant corrections from the shared corrections bank.

    When project_ids are provided, includes project tags for scoped recall.
    Returns up to MAX_CORRECTIONS fact strings, empty list on failure or if disabled.
    """
    if not settings.hindsight_enabled:
        logger.info("Hindsight recall (corrections): disabled")
        return []
    try:
        client = _get_client()
        kwargs: dict = {
            "bank_id": CORRECTIONS_BANK,
            "query": query,
            "max_tokens": settings.hindsight_recall_max_tokens,
            "budget": settings.hindsight_recall_budget,
        }
        if project_ids:
            kwargs["tags"] = [f"project:{pid}" for pid in project_ids]
            kwargs["tags_match"] = "any"
        response = await client.arecall(**kwargs)
        corrections = [r.text for r in response.results[:MAX_CORRECTIONS]]
        logger.info("Hindsight recall (corrections): %d corrections returned", len(corrections))
        return corrections
    except Exception as e:
        logger.warning("Hindsight recall (corrections) failed [%s]: %s", type(e).__name__, e, exc_info=True)
        return []


async def recall_project_memory(
    project_id: str,
    query: str,
) -> list[str]:
    """Recall memories from a project-scoped bank.

    Uses tags_match="any_strict" to prevent cross-project leakage.
    Requests observations first (consolidated patterns), then raw facts.
    Returns up to hindsight_max_project_memories results.
    """
    if not settings.hindsight_enabled or not settings.hindsight_project_banks_enabled:
        return []
    try:
        client = _get_client()
        response = await client.arecall(
            bank_id=_project_bank(project_id),
            query=query,
            max_tokens=settings.hindsight_recall_max_tokens,
            budget=settings.hindsight_recall_budget,
            tags=[f"project:{project_id}"],
            tags_match="any_strict",
            types=["observation", "world"],
        )
        memories = [r.text for r in response.results[:settings.hindsight_max_project_memories]]
        logger.info("Hindsight recall (project %s): %d memories returned", project_id, len(memories))
        return memories
    except Exception as e:
        logger.warning("Hindsight recall (project %s) failed [%s]: %s", project_id, type(e).__name__, e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Retain
# ---------------------------------------------------------------------------

async def _retain_user_bank(
    user_id: str,
    content: str,
    ts: str,
    doc_id: str | None,
    tags: list[str] | None = None,
    entities: list[str] | None = None,
) -> None:
    """Retain content to the per-user memory bank."""
    try:
        client = _get_client()
        kwargs: dict = {
            "bank_id": _user_bank(user_id),
            "content": content,
            "context": "rag-conversation",
            "timestamp": ts,
            "document_id": doc_id,
        }
        if tags:
            kwargs["tags"] = tags
        if entities:
            kwargs["entities"] = entities
        await client.aretain(**kwargs)
        logger.info("Hindsight retain (user %s): user bank stored", user_id)
    except Exception as e:
        logger.warning("Hindsight retain (user %s) failed [%s]: %s", user_id, type(e).__name__, e, exc_info=True)


async def _retain_quality_bank(
    query: str,
    sources_used: list[str],
    action: str,
    search_result_count: int,
    latency_ms: int,
    ts: str,
    doc_id: str | None,
    tags: list[str] | None = None,
) -> None:
    """Retain interaction to the shared RAG quality bank."""
    try:
        client = _get_client()
        quality_content = (
            f"Query: {query}\n"
            f"Action: {action}\n"
            f"Search results: {search_result_count}\n"
            f"Sources cited: {', '.join(sources_used) if sources_used else 'none'}\n"
            f"Latency: {latency_ms}ms"
        )
        if search_result_count == 0:
            quality_content += "\nFLAG: Zero search results -- potential coverage gap"
        kwargs: dict = {
            "bank_id": QUALITY_BANK,
            "content": quality_content,
            "context": "rag-quality-signal",
            "timestamp": ts,
            "document_id": doc_id,
        }
        if tags:
            kwargs["tags"] = tags
        await client.aretain(**kwargs)
        logger.info("Hindsight retain (quality): quality bank stored")
    except Exception as e:
        logger.warning("Hindsight retain (quality) failed [%s]: %s", type(e).__name__, e, exc_info=True)


async def _retain_project_bank(
    project_id: str,
    content: str,
    ts: str,
    doc_id: str | None,
    tags: list[str] | None = None,
    entities: list[str] | None = None,
) -> None:
    """Retain content to a project-scoped memory bank with observation scopes."""
    if not settings.hindsight_project_banks_enabled:
        return
    try:
        client = _get_client()
        kwargs: dict = {
            "bank_id": _project_bank(project_id),
            "content": content,
            "context": "rag-project-knowledge",
            "timestamp": ts,
            "document_id": doc_id,
            "observation_scopes": "per_tag",
        }
        if tags:
            kwargs["tags"] = tags
        if entities:
            kwargs["entities"] = entities
        await client.aretain(**kwargs)
        logger.info("Hindsight retain (project %s): project bank stored", project_id)
    except Exception as e:
        logger.warning("Hindsight retain (project %s) failed [%s]: %s", project_id, type(e).__name__, e, exc_info=True)


async def retain_interaction(
    user_id: str,
    query: str,
    answer: str,
    sources_used: list[str],
    conversation_id: int | str | None = None,
    search_result_count: int = 0,
    latency_ms: int = 0,
    action: str = "search",
    query_type: str = "qa",
    project_ids: list[str] | None = None,
) -> None:
    """Store an interaction into memory banks with intelligent gating.

    Fire-and-forget: callers should use asyncio.create_task() to avoid blocking.

    Flow:
    1. decide_retention() — gate based on intent
    2. Build tags + extract topic/entities from sources
    3. FULL retention: distill content, then retain to user + project + quality banks
    4. QUALITY_ONLY: retain quality signal only (with tags)
    5. SKIP: log and return
    """
    if not settings.hindsight_enabled:
        logger.info("Hindsight retain (user %s): disabled", user_id)
        return

    # 1. Retention gating
    decision = decide_retention(action, query_type, search_result_count, len(answer))
    if decision.level == RetentionLevel.SKIP:
        logger.info("Hindsight retain (user %s): skipped (%s)", user_id, decision.reason)
        return

    logger.info("Hindsight retain (user %s): %s (%s)", user_id, decision.level.value, decision.reason)

    doc_id = f"conv-{conversation_id}" if conversation_id else None
    ts = datetime.now(timezone.utc).isoformat()

    # 2. Build tags + entities
    topic = extract_topic_tag(query, sources_used)
    tags = _build_tags(project_ids=project_ids, action=action, topic=topic)
    entities = extract_entities_from_sources(sources_used)

    if decision.level == RetentionLevel.FULL:
        # 3. Distill content, then retain to all relevant banks
        distilled = await distill_interaction(query, answer, sources_used)

        tasks: list = [
            _retain_user_bank(user_id, distilled, ts, doc_id, tags=tags, entities=entities),
            _retain_quality_bank(query, sources_used, action, search_result_count, latency_ms, ts, doc_id, tags=tags),
        ]

        # Retain to each project bank
        if project_ids and settings.hindsight_project_banks_enabled:
            for pid in project_ids:
                project_tags = _build_tags(project_ids=[pid], action=action, topic=topic)
                tasks.append(
                    _retain_project_bank(pid, distilled, ts, doc_id, tags=project_tags, entities=entities)
                )

        await asyncio.gather(*tasks, return_exceptions=True)

    elif decision.level == RetentionLevel.QUALITY_ONLY:
        # 4. Quality signal only
        await _retain_quality_bank(
            query, sources_used, action, search_result_count,
            latency_ms, ts, doc_id, tags=tags,
        )


# ---------------------------------------------------------------------------
# Correction detection & storage
# ---------------------------------------------------------------------------

def is_likely_correction(user_message: str, has_prior_assistant_msg: bool) -> bool:
    """Quick heuristic check if the user is correcting the chatbot.

    Only triggers when there is a prior assistant message in the conversation.
    """
    if not has_prior_assistant_msg:
        return False
    lower = user_message.lower().strip()
    return any(lower.startswith(s) or s in lower for s in CORRECTION_SIGNALS)


async def retain_correction(
    query: str,
    original_answer: str,
    correction_text: str,
    user_id: str,
    conversation_id: int | str | None = None,
) -> None:
    """Store a user correction in the shared corrections bank.

    Fire-and-forget: callers should use asyncio.create_task().
    """
    if not settings.hindsight_enabled:
        return
    try:
        client = _get_client()
        content = (
            f"CORRECTION: {correction_text}\n"
            f"Original claim: {original_answer[:300]}\n"
            f"Source: user correction in conversation (user {user_id})."
        )
        ts = datetime.now(timezone.utc).isoformat()
        doc_id = f"correction-conv-{conversation_id}" if conversation_id else None
        await client.aretain(
            bank_id=CORRECTIONS_BANK,
            content=content,
            context="rag-correction",
            timestamp=ts,
            document_id=doc_id,
        )
        logger.info("Hindsight retain (correction): stored correction from user %s", user_id)
    except Exception as e:
        logger.warning("Hindsight retain (correction) failed [%s]: %s", type(e).__name__, e, exc_info=True)


# ---------------------------------------------------------------------------
# Phase 3: Reflect + Mental Models
# ---------------------------------------------------------------------------

async def reflect_project_patterns(project_id: str, query: str | None = None) -> str:
    """Use Hindsight's reflect for agentic pattern analysis on a project bank.

    Not called per-query; available for admin/analytics.
    Returns the reflection text, or an error message on failure.
    """
    if not settings.hindsight_enabled or not settings.hindsight_project_banks_enabled:
        return "Hindsight or project banks not enabled"
    try:
        client = _get_client()
        reflect_query = query or (
            f"What are the recurring topics, patterns, and knowledge gaps "
            f"for project {project_id}? What questions come up most often?"
        )
        response = await client.areflect(
            bank_id=_project_bank(project_id),
            query=reflect_query,
        )
        return response.text if hasattr(response, "text") else str(response)
    except Exception as e:
        logger.warning("Hindsight reflect (project %s) failed [%s]: %s", project_id, type(e).__name__, e)
        return f"Reflect failed: {e}"


# ---------------------------------------------------------------------------
# Phase 4: Mental Models
# ---------------------------------------------------------------------------

async def create_project_mental_model(
    project_id: str,
    name: str,
    source_query: str,
    tags: list[str] | None = None,
) -> dict:
    """Create a mental model (pre-computed answer) for a project bank.

    Mental models are checked first during reflect calls, providing instant
    consistent answers for FAQ-like questions.
    """
    if not settings.hindsight_enabled or not settings.hindsight_project_banks_enabled:
        return {"error": "Hindsight or project banks not enabled"}
    try:
        client = _get_client()
        model_tags = [f"project:{project_id}"]
        if tags:
            model_tags.extend(tags)
        response = await client.mental_models.create_mental_model(
            bank_id=_project_bank(project_id),
            name=name,
            source_query=source_query,
            tags=model_tags,
        )
        logger.info("Created mental model '%s' for project %s", name, project_id)
        return {"status": "created", "name": name, "project_id": project_id}
    except Exception as e:
        logger.warning("Mental model creation failed for project %s [%s]: %s", project_id, type(e).__name__, e)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Phase 5: Admin read helpers
# ---------------------------------------------------------------------------

async def get_bank_stats(bank_id: str) -> dict:
    """Fetch stats for a memory bank via Hindsight's agent stats endpoint.

    Returns a dict with bank_id, exists, total_nodes, nodes_by_fact_type,
    total_observations, last_consolidated_at.  On failure, returns exists=False.
    """
    try:
        client = _get_client()
        if client is None:
            return {"bank_id": bank_id, "exists": False, "total_nodes": 0,
                    "nodes_by_fact_type": {}, "total_observations": 0,
                    "last_consolidated_at": None}
        response = await client.banks.get_agent_stats(bank_id=bank_id)
        return {
            "bank_id": bank_id,
            "exists": True,
            "total_nodes": getattr(response, "total_nodes", 0),
            "nodes_by_fact_type": getattr(response, "nodes_by_fact_type", {}) or {},
            "total_observations": getattr(response, "total_observations", 0),
            "last_consolidated_at": getattr(response, "last_consolidated_at", None),
        }
    except Exception as e:
        logger.debug("get_bank_stats(%s) failed [%s]: %s", bank_id, type(e).__name__, e)
        return {"bank_id": bank_id, "exists": False, "total_nodes": 0,
                "nodes_by_fact_type": {}, "total_observations": 0,
                "last_consolidated_at": None}


async def list_bank_memories(
    bank_id: str,
    limit: int = 50,
    offset: int = 0,
    memory_type: str | None = None,
) -> dict:
    """List memories stored in a bank with pagination.

    Returns {"memories": [...], "bank_id": str, "total": int} on success,
    or {"memories": [], "bank_id": str, "total": 0, "error": str} on failure.
    """
    try:
        client = _get_client()
        if client is None:
            return {"memories": [], "bank_id": bank_id, "total": 0,
                    "error": "Hindsight client not available"}
        kwargs: dict = {
            "bank_id": bank_id,
            "limit": limit,
            "offset": offset,
        }
        if memory_type:
            kwargs["type"] = memory_type
        response = await client.memory.list_memories(**kwargs)
        items = getattr(response, "items", []) or []
        memories = []
        for item in items:
            if isinstance(item, dict):
                mem = item
            else:
                mem = {
                    "content": getattr(item, "content", ""),
                    "timestamp": getattr(item, "timestamp", ""),
                    "context": getattr(item, "context", ""),
                    "metadata": getattr(item, "metadata", {}),
                    "document_id": getattr(item, "document_id", None),
                    "entities": getattr(item, "entities", []),
                    "tags": getattr(item, "tags", []),
                }
            memories.append(mem)
        return {
            "memories": memories,
            "bank_id": bank_id,
            "total": getattr(response, "total", len(memories)),
        }
    except Exception as e:
        logger.warning("list_bank_memories(%s) failed [%s]: %s", bank_id, type(e).__name__, e)
        return {"memories": [], "bank_id": bank_id, "total": 0, "error": str(e)}
