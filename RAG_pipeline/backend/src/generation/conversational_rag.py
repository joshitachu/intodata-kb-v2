"""Conversational RAG pipeline with session-based memory."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import tiktoken
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, ConversationMessage, DocGapEvent, QueryUsage
from src.document_roles import AUXILIARY_DOCUMENT_ROLES, TEMPLATE_DOCUMENT_ROLES, has_template_intent
from src.generation.agent_loop import (
    RuntimeAnswerChunk,
    RuntimeLoopOutcome,
    RuntimeProgressEvent,
    build_runtime_tool_summary,
    run_runtime_tool_loop,
    run_runtime_tool_loop_stream,
)
from src.generation.agent_orchestrator import AgentStep, AgentTool, AgentTrace
from src.generation.intent_classifier import (
    Action,
    MetadataIntent,
    QueryAnalysis,
    QueryType,
    analyze_query_async,
    detect_runtime_keywords,
)
from src.generation.prompt_builder import build_conversation_summary_messages
from src.generation.llm_client import generate_answer, generate_answer_stream, get_last_usage
from src.retrieval.embedder import get_last_embed_tokens
from src.generation.map_reduce import resolve_document_id, run_map_reduce
from src.generation.prompt_builder import (
    CODE_ASSISTANT_SYSTEM_PROMPT,
    _build_context,
    build_correction_extraction_messages,
    build_metadata_messages,
    build_no_results_messages,
    build_role_aware_context,
    should_use_code_assistant,
)
from src.retrieval.metadata_retriever import fetch_metadata, get_system_knowledge_summary
from src.config import settings
from src.memory.hindsight_client import (
    ensure_corrections_bank,
    ensure_project_bank,
    ensure_quality_bank,
    ensure_user_bank,
    is_likely_correction,
    recall_corrections,
    recall_project_memory,
    recall_quality_insights,
    recall_user_memory,
    retain_correction,
    retain_interaction,
)
from src.retrieval.entity_matcher import match_query_entities, matched_unit_names
from src.retrieval.search import (
    merge_sources_by_document,
    search_and_rerank,
    search_layered,
    search_multi_query,
    search_similar,
    search_similar_hybrid,
)
from src.runtime.scope import RequestScope
from src.schemas import DocumentSource, MessageResponse, Source

logger = logging.getLogger(__name__)

ENCODING_NAME = "cl100k_base"
MAX_HISTORY_TOKENS = 24000
MAX_CONTEXT_TOKENS = 16000
MAX_HISTORY_MESSAGES = 40

# Appended to (or streamed after) an answer when the active LLM provider has no
# structured tool-call support, so a runtime-relevant turn degrades visibly to a
# documents-only answer instead of silently dropping the runtime dimension.
_RUNTIME_UNAVAILABLE_NOTE = (
    "_Note: live runtime tools are unavailable for the active model, so this "
    "answer is based on indexed documents only._"
)


def build_context(
    sources: list[Source],
    reference_numbers: dict[int, int] | None = None,
    max_tokens: int = MAX_CONTEXT_TOKENS,
) -> str:
    """Backward-compatible context helper for tests and legacy imports."""
    if not sources:
        return ""

    enc = tiktoken.get_encoding(ENCODING_NAME)
    parts: list[str] = []
    total_tokens = 0

    for src in sources:
        fname_parts = src.filename.split("/", 1)
        display_name = (
            f"{fname_parts[1]} (Project: {fname_parts[0]})"
            if len(fname_parts) == 2
            else src.filename
        )
        if reference_numbers and src.document_id in reference_numbers:
            header = f"[Reference {reference_numbers[src.document_id]}: {display_name}]"
        else:
            header = f"[Source: {display_name}, chunk {src.chunk_index}]"

        block = f"{header}\n{src.text}\n"
        block_tokens = len(enc.encode(block))
        if total_tokens + block_tokens > max_tokens:
            break
        parts.append(block)
        total_tokens += block_tokens

    return "\n".join(parts)

CONVERSATIONAL_SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "You have access to the conversation history and retrieved context.\n\n"
    "## CRITICAL RULES\n"
    "1. Answer ONLY using information from the retrieved context below. "
    "NEVER use your general knowledge or training data to fill gaps.\n"
    "2. If the context only partially covers the answer, state what you found and what is missing: "
    "'Based on the available documents, I found [X]. The documents do not contain [Y].' "
    "Never fabricate details to fill gaps.\n"
    "3. NEVER invent details (SQL queries, API endpoints, config values, etc.) "
    "that are not explicitly in the context.\n"
    "4. Every factual claim must cite a source reference [N].\n"
    "5. Be consistent with your previous answers. When asked about your sources or reasoning, "
    "refer back to the [N] references you cited. Do not retract correctly cited information.\n\n"
    "## Formatting rules\n"
    "- Use **markdown**: headings, bullet lists, bold, code blocks.\n"
    "- Cite sources with [1], [2], etc. Only cite sources you actually use.\n"
    "- Do NOT cite sources for greetings or small talk.\n\n"
    "For follow-up questions, use conversation context to understand "
    "references like 'it', 'that', 'the previous topic'.\n\n"
    "IMPORTANT: Any system message labeled '## Background knowledge from previous sessions' "
    "contains facts from PAST sessions — do NOT describe that content as part of the current conversation."
)

CONTEXT_FIRST_SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "You have access to the conversation history and retrieved context.\n\n"
    "## CRITICAL RULES\n"
    "1. Answer ONLY using information from the retrieved context below. "
    "NEVER use your general knowledge or training data to fill gaps.\n"
    "2. If the context does not contain the answer, your response MUST begin "
    "with [NEED_SEARCH] as the very first word -- respond with exactly "
    "[NEED_SEARCH] and nothing else.\n"
    "3. NEVER invent details (SQL queries, API endpoints, config values, etc.) "
    "that are not explicitly in the context.\n"
    "4. Every factual claim must cite a source reference [N].\n"
    "5. Be consistent with your previous answers. When asked about your sources or reasoning, "
    "refer back to the [N] references you cited. Do not retract correctly cited information.\n\n"
    "## Formatting rules\n"
    "- Use **markdown**: headings, bullet lists, bold, code blocks.\n"
    "- Cite sources with [1], [2], etc. Only cite sources you actually use.\n"
    "- Do NOT cite sources for greetings or small talk.\n\n"
    "For follow-up questions, use conversation context to understand "
    "references like 'it', 'that', 'the previous topic'.\n\n"
    "IMPORTANT: Any system message labeled '## Background knowledge from previous sessions' "
    "contains facts from PAST sessions — do NOT describe that content as part of the current conversation."
)


def _build_memory_context(
    user_memories: list[str],
    quality_insights: list[str],
    corrections: list[str] | None = None,
    project_memories: list[str] | None = None,
) -> str | None:
    """Format recalled memories as a system message section.

    Returns None if no memories are available.
    """
    has_any = bool(project_memories or user_memories or corrections or quality_insights)
    if not has_any:
        return None
    parts: list[str] = [
        "## Background knowledge from previous sessions",
        "The following was learned in PAST sessions. "
        "It is NOT part of the current conversation and should NOT be described as such.",
    ]
    if project_memories:
        parts.append("### Project knowledge (past sessions)")
        for mem in project_memories:
            parts.append(f"- {mem}")
    if user_memories:
        parts.append("### Remembered facts about this user (past sessions)")
        for mem in user_memories:
            parts.append(f"- {mem}")
    if corrections:
        parts.append("### Known corrections (override document content)")
        for corr in corrections:
            parts.append(f"- {corr}")
    if quality_insights:
        parts.append("### RAG quality signals (system-internal, not conversation)")
        for insight in quality_insights:
            parts.append(f"- {insight}")
    return "\n".join(parts)


def get_previous_sources(
    messages: list[ConversationMessage],
    project_ids: list[str] | None = None,
) -> list[DocumentSource]:
    """Get sources from the most recent assistant message that has them."""
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.sources:
            doc_sources = [DocumentSource(**s) for s in msg.sources]
            if project_ids is not None:
                doc_sources = [
                    ds for ds in doc_sources
                    if (ds.project_id in project_ids)
                    or (
                        ds.project_id is None
                        and any(ds.filename.startswith(f"{pid}/") for pid in project_ids)
                    )
                ]
            return doc_sources
    return []


def get_latest_runtime_tool_summary(
    messages: list[ConversationMessage],
) -> list[dict] | None:
    """Most recent prior runtime_tool_summary in this conversation, for replay.

    Read ONLY from the current conversation's messages (route already enforces user
    ownership) so replay never crosses conversations. Returns None when no prior turn
    recorded one."""
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.runtime_tool_summary:
            return msg.runtime_tool_summary
    return None


_CANT_ANSWER_PATTERNS = re.compile(
    r"(?:I (?:could not|couldn't|cannot|can't|don't|do not) find|"
    r"(?:no|not enough) (?:relevant |available )?(?:information|documents?|context|data)|"
    r"not (?:available|found|present) in (?:the|my) (?:available |retrieved )?(?:context|documents?|sources?))",
    re.IGNORECASE,
)

_NEED_SEARCH_MARKER = "[NEED_SEARCH]"


def needs_new_search(answer: str) -> bool:
    """Check if the LLM response indicates it needs more information."""
    if _NEED_SEARCH_MARKER in answer:
        return True
    # Safety net: catch "I don't know" style responses that should have been [NEED_SEARCH]
    stripped = answer.strip()
    if len(stripped) < 200 and _CANT_ANSWER_PATTERNS.search(stripped):
        return True
    return False


# --- Streaming sniff-buffer (Slice 2) --------------------------------------
#
# Restores real token-by-token delivery for the document-QA streaming path.
# generate_answer_stream() is awaited only up to _SNIFF_BUFFER_CHARS (plus a
# look-ahead guard so the [NEED_SEARCH] marker can never be split across the
# buffer boundary) before a sufficiency verdict is made using the same
# semantics as needs_new_search(). On success the still-open stream is handed
# back so the caller can keep draining it for genuine token-by-token delivery
# after the sniffed prefix.

_SNIFF_BUFFER_CHARS = 200


def _partial_marker_suffix_len(buffer: str) -> int:
    """Length of the longest suffix of ``buffer`` that is a non-empty proper
    prefix of _NEED_SEARCH_MARKER (e.g. buffer ending in "...context. [NEED_SEA"
    has a 7-char suffix match), or 0 when the tail cannot be the start of a
    still-arriving marker. Bounded search (at most len(marker) - 1
    comparisons) -- not an infinite-loop risk, just a few extra characters of
    buffering while the marker might be mid-flight across a token boundary."""
    max_check = min(len(_NEED_SEARCH_MARKER) - 1, len(buffer))
    for i in range(max_check, 0, -1):
        if buffer.endswith(_NEED_SEARCH_MARKER[:i]):
            return i
    return 0


def _ends_with_partial_marker(buffer: str) -> bool:
    """True when the tail of ``buffer`` might be the start of a
    _NEED_SEARCH_MARKER occurrence still arriving across a token boundary."""
    return _partial_marker_suffix_len(buffer) > 0


@dataclass
class _SniffResult:
    """Result of sniffing a streamed answer for sufficiency."""

    insufficient: bool
    prefix: str
    remainder: AsyncGenerator[str, None] | None
    """The same still-open generate_answer_stream() generator to keep
    draining. None when the stream was exhausted while sniffing (nothing
    left) or when insufficient is True (caller must not resume -- the
    marker case has already been closed by this helper; the heuristic case
    has nothing left to close since the stream was already exhausted)."""


async def _sniff_stream_sufficiency(
    messages: list[dict[str, str]],
    images: list[str] | None = None,
    buffer_chars: int = _SNIFF_BUFFER_CHARS,
) -> _SniffResult:
    """Stream an answer via generate_answer_stream(), but only await up to
    ``buffer_chars`` characters (extended if needed so [NEED_SEARCH] is never
    split across the boundary) before deciding sufficiency -- same semantics
    as needs_new_search(). Hands back the still-open stream on success so the
    caller can keep draining it for real token-by-token delivery.

    Invariant: every "insufficient after all" branch must close the stream
    (either here, via .aclose(), or by the caller once it is done with the
    returned remainder) to avoid leaking an open provider HTTP stream.
    """
    stream = generate_answer_stream(messages, images=images)
    buffer = ""
    async for token in stream:
        buffer += token
        if len(buffer) >= buffer_chars and not _ends_with_partial_marker(buffer):
            break
    else:
        # Loop completed without break: stream exhausted inside the buffer
        # window -- the buffer IS the whole answer.
        if _NEED_SEARCH_MARKER in buffer:
            return _SniffResult(insufficient=True, prefix=buffer, remainder=None)
        stripped = buffer.strip()
        if len(stripped) < 200 and _CANT_ANSWER_PATTERNS.search(stripped):
            return _SniffResult(insufficient=True, prefix=buffer, remainder=None)
        return _SniffResult(insufficient=False, prefix=buffer, remainder=None)

    # Broke out early: buffer >= buffer_chars, stream still has more.
    if _NEED_SEARCH_MARKER in buffer:
        await stream.aclose()
        return _SniffResult(insufficient=True, prefix=buffer, remainder=None)
    return _SniffResult(insufficient=False, prefix=buffer, remainder=stream)


async def _drain_stripping_late_marker(
    remainder: AsyncGenerator[str, None],
) -> AsyncGenerator[str, None]:
    """Drain a still-open sniffed stream, stripping a late-arriving
    [NEED_SEARCH] marker (one the sniff buffer already missed because
    sufficiency was validated before it appeared) so it never reaches the
    user.

    The marker is looked for in the *entire* pending buffer on every token,
    not just in the portion about to be released -- checking only the
    about-to-be-released slice would miss a marker whose halves straddle
    that cut point when a chunk is larger than the lookback window (e.g. a
    marker spanning from the middle of one large provider chunk into the
    next). Only a genuine partial-marker suffix (one that could still
    complete into a full marker with more incoming text) is ever held back
    from release, bounded to at most len(marker) - 1 characters. Logs a
    single WARNING if a late marker was found; the rest of the answer is
    delivered normally."""
    pending = ""
    late_marker_seen = False
    async for token in remainder:
        pending += token
        if _NEED_SEARCH_MARKER in pending:
            late_marker_seen = True
            pending = pending.replace(_NEED_SEARCH_MARKER, "")
        hold_back = _partial_marker_suffix_len(pending)
        safe_len = len(pending) - hold_back
        if safe_len > 0:
            safe, pending = pending[:safe_len], pending[safe_len:]
            yield safe
    if _NEED_SEARCH_MARKER in pending:
        late_marker_seen = True
        pending = pending.replace(_NEED_SEARCH_MARKER, "")
    if pending:
        yield pending
    if late_marker_seen:
        logger.warning(
            "Late [NEED_SEARCH] marker arrived after the streaming sniff "
            "buffer had already flushed a partial answer; stripped from "
            "output, partial answer kept (accepted edge case)."
        )


# --- Retrieval ladder (Slice 4) -------------------------------------------
#
# Rung 1 is the default corpus partition (canonical docs + legacy chunks).
# Rung 2 descends to internal support layers, selected deterministically from
# the query wording. Order matters: first matching route wins.

_RUNG2_ROUTES: list[tuple[re.Pattern[str], list[str]]] = [
    (
        re.compile(r"\b(risk|secur|vulnerab|quality|score|audit|complian|hardcod)", re.IGNORECASE),
        ["scorecard", "support_maps", "technical_analysis"],
    ),
    (
        re.compile(r"\b(field|column|table|schema|mapping|endpoint|api\b|payload)", re.IGNORECASE),
        ["technical_analysis", "evidence_slices"],
    ),
    (
        re.compile(r"\b(config|connection|context variable|environment|runtime|credential|parameter|setting)", re.IGNORECASE),
        ["technical_analysis", "support_maps"],
    ),
    (
        re.compile(r"\b(depend|lineage|upstream|downstream|impact|calls\b|invokes?\b)", re.IGNORECASE),
        ["technical_analysis", "support_maps"],
    ),
]
_RUNG2_DEFAULT_LAYERS = ["technical_analysis"]

# Identifier-like tokens: snake_case, camelCase/PascalCase with an internal
# capital, or long ALLCAPS names. Common format acronyms are not identifiers.
_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:"
    r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"
    r"|[a-z]+[A-Z][A-Za-z0-9]*"
    r"|[A-Z][a-z]+[A-Z][A-Za-z0-9]*"
    r"|[A-Z]{4,}[0-9]*"
    r")\b"
)
_IDENTIFIER_STOPWORDS = {"json", "http", "https", "rest", "html", "xml", "csv", "yaml", "sql"}


def _route_internal_layers(query: str) -> list[str]:
    """Deterministic rung-2 layer selection from query wording."""
    for pattern, layers in _RUNG2_ROUTES:
        if pattern.search(query):
            return layers
    return list(_RUNG2_DEFAULT_LAYERS)


def _extract_query_identifiers(query: str) -> list[str]:
    """Identifier-like tokens from the query (no alias-table lookup; Slice 5)."""
    seen: dict[str, str] = {}
    for match in _IDENTIFIER_PATTERN.findall(query):
        key = match.lower()
        if key not in seen and key not in _IDENTIFIER_STOPWORDS:
            seen[key] = match
    return list(seen.values())


def _identifiers_missing_from_sources(identifiers: list[str], sources: list[Source]) -> bool:
    """True when none of the asked-about identifiers appear in the source text."""
    if not identifiers or not sources:
        return False
    blob = " ".join(s.text for s in sources).lower()
    return not any(ident.lower() in blob for ident in identifiers)


def build_context_from_document_sources(
    doc_sources: list[DocumentSource],
    max_tokens: int = MAX_CONTEXT_TOKENS,
) -> str:
    """Build context string from DocumentSource objects (previous sources)."""
    if not doc_sources:
        return ""

    enc = tiktoken.get_encoding(ENCODING_NAME)
    parts: list[str] = []
    total_tokens = 0

    for ds in doc_sources:
        if ds.display_name:
            display_name = ds.display_name
        else:
            # Legacy sources: extract project from scoped filename (project_id/filename)
            fname_parts = ds.filename.split("/", 1)
            if len(fname_parts) == 2:
                display_name = f"{fname_parts[1]} (Project: {fname_parts[0]})"
            else:
                display_name = ds.filename

        header = f"[Reference {ds.reference_number}: {display_name}]"
        block = f"{header}\n{ds.text}\n"
        block_tokens = len(enc.encode(block))

        if total_tokens + block_tokens > max_tokens:
            break
        parts.append(block)
        total_tokens += block_tokens

    context = "\n".join(parts)
    context += "\n\n---\nThe above is ALL available information. Do NOT add details beyond what is shown."
    return context


def build_conversational_messages_with_context(
    query: str,
    context: str,
    history: list[ConversationMessage],
    memory_context: str | None = None,
    answer_style: str = "assistant",
) -> list[dict[str, str]]:
    """Build chat messages for the context-first attempt.

    Uses CONTEXT_FIRST_SYSTEM_PROMPT which includes the [NEED_SEARCH] instruction,
    so the LLM can signal when it needs fresh search results.
    """
    messages: list[dict[str, str]] = []
    system_prompt = (
        CODE_ASSISTANT_SYSTEM_PROMPT
        if answer_style == "code"
        else CONTEXT_FIRST_SYSTEM_PROMPT
    )
    if answer_style == "code":
        system_prompt += (
            "\n\nIf the previous context does not contain enough evidence, "
            "your response MUST begin with [NEED_SEARCH] as the very first "
            "word -- respond with exactly [NEED_SEARCH] and nothing else."
        )
    messages.append({"role": "system", "content": system_prompt})

    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    if context:
        messages.append({
            "role": "system",
            "content": f"Retrieved context:\n{context}",
        })

    history_messages = truncate_history(history)
    messages.extend(history_messages)

    messages.append({"role": "user", "content": query})
    return messages


def truncate_history(
    messages: list[ConversationMessage],
    max_tokens: int = MAX_HISTORY_TOKENS,
    max_messages: int = MAX_HISTORY_MESSAGES,
) -> list[dict[str, str]]:
    """Truncate conversation history to fit within token budget.

    Always keeps the first user+assistant pair (anchors the conversation
    topic), then fills remaining budget with the most recent messages.
    Returns messages in chronological order.
    """
    enc = tiktoken.get_encoding(ENCODING_NAME)
    messages = [m for m in messages if getattr(m, "status", "completed") != "failed"]

    if len(messages) <= 2:
        return [{"role": m.role, "content": m.content} for m in messages]

    # Always keep first exchange (anchors conversation topic)
    first_pair = messages[:2]
    first_pair_msgs = [{"role": m.role, "content": m.content} for m in first_pair]
    first_pair_tokens = sum(len(enc.encode(m.content)) for m in first_pair)

    remaining_budget = max_tokens - first_pair_tokens
    remaining_count = max_messages - 2

    if remaining_budget <= 0:
        return first_pair_msgs

    # Fill with most recent messages (backwards)
    recent = messages[2:]
    recent = recent[-remaining_count:] if len(recent) > remaining_count else recent

    selected: list[dict[str, str]] = []
    total_tokens = 0
    for msg in reversed(recent):
        msg_tokens = len(enc.encode(msg.content))
        if total_tokens + msg_tokens > remaining_budget:
            break
        selected.append({"role": msg.role, "content": msg.content})
        total_tokens += msg_tokens
    selected.reverse()

    return first_pair_msgs + selected



def build_conversational_messages(
    query: str,
    sources: list[Source],
    history: list[ConversationMessage],
    reference_numbers: dict[int, int] | None = None,
    memory_context: str | None = None,
    answer_style: str = "assistant",
    context_first: bool = False,
    auxiliary_sources: list[Source] | None = None,
) -> list[dict[str, str]]:
    """Build chat messages with system prompt, context, history, and current query.

    Message structure:
    1. System prompt
    2. Memory context (from Hindsight, if available)
    3. Context from retrieved sources (as system message)
    4. Truncated conversation history (user/assistant messages)
    5. Current user query
    """
    messages: list[dict[str, str]] = []

    # 1. System prompt
    if answer_style == "code":
        system_prompt = CODE_ASSISTANT_SYSTEM_PROMPT
        if context_first:
            system_prompt += (
                "\n\nIf the retrieved context does not contain enough evidence, "
                "your response MUST begin with [NEED_SEARCH] as the very first "
                "word -- respond with exactly [NEED_SEARCH] and nothing else."
            )
    else:
        system_prompt = CONTEXT_FIRST_SYSTEM_PROMPT if context_first else CONVERSATIONAL_SYSTEM_PROMPT
    messages.append({"role": "system", "content": system_prompt})

    # 2. Memory context
    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    # 3. Context from sources
    if sources or auxiliary_sources:
        if auxiliary_sources:
            context = build_role_aware_context(
                sources,
                auxiliary_sources,
                reference_numbers=reference_numbers,
            )
        else:
            context = _build_context(sources, reference_numbers=reference_numbers)
        messages.append({
            "role": "system",
            "content": f"Retrieved context:\n{context}",
        })

    # 4. Truncated history
    history_messages = truncate_history(history)
    messages.extend(history_messages)

    # 5. Current query
    messages.append({"role": "user", "content": query})

    return messages


async def _search_qa_sources(
    qdrant: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int,
    threshold: float,
    project_ids: list[str] | None,
    document_roles: list[str],
) -> list[Source]:
    """Run the configured QA search path with a narrowing document-role filter."""
    if settings.query_expansion_enabled:
        return await search_multi_query(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=threshold,
            project_ids=project_ids,
            document_roles=document_roles,
        )
    if settings.reranker_enabled:
        return await search_and_rerank(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=threshold,
            project_ids=project_ids,
            document_roles=document_roles,
        )
    if settings.hybrid_search_enabled:
        return await search_similar_hybrid(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=threshold,
            project_ids=project_ids,
            document_roles=document_roles,
        )
    return await search_similar(
        client=qdrant,
        query=query,
        user_groups=user_groups,
        top_k=top_k,
        threshold=threshold,
        project_ids=project_ids,
        document_roles=document_roles,
    )


def _add_doc_gap_event(
    db: AsyncSession,
    ctx: "RAGContext",
    query: str,
    user_id: str | None,
    conversation_id: int,
    message_id: int | None,
) -> None:
    """Persist a DocGapEvent for a rung-2 answer. No-op unless ctx.doc_gap."""
    if not ctx.doc_gap or not ctx.doc_gap_details:
        return
    details = ctx.doc_gap_details
    db.add(DocGapEvent(
        project_id=details["project_id"],
        run_id=details.get("run_id"),
        user_id=int(user_id) if user_id else None,
        conversation_id=conversation_id,
        message_id=message_id,
        query_text=query,
        detected_entities=details.get("detected_entities"),
        insufficiency_reason=details["insufficiency_reason"],
        primary_source_layer=ctx.primary_source_layer,
        layers_used=details.get("layers_used"),
        supporting_artifacts=details.get("supporting_artifacts"),
        supporting_chunk_ids=details.get("supporting_chunk_ids"),
    ))


@dataclass
class RAGContext:
    """Result of the RAG preparation step (search + routing)."""

    llm_messages: list[dict[str, str]]
    document_sources: list[DocumentSource]
    precomputed_answer: str | None
    """Non-None when the answer was already computed (RESPOND / SUMMARY / context-first hit)."""
    allow_runtime_tools: bool = False
    """True for QA document-backed answers that may be augmented with live runtime
    tools in the same turn. Terminal precomputed answers (chitchat, metadata,
    conversation/document summary, no-results) leave this False so they short-circuit
    without invoking the runtime tool loop."""
    analysis: QueryAnalysis | None = None
    """Intent classifier output, passed through to retention for gating decisions."""
    agent_steps: list[AgentStep] | None = None
    """Read-only agent tool steps used to prepare the answer."""
    primary_source_layer: str | None = None
    """Corpus layer the answer was grounded in (None for non-document answers)."""
    fallback_used: bool = False
    """True when the retrieval ladder descended to rung 2 (internal layers)."""
    doc_gap: bool = False
    """True when internal material answered what the final docs could not."""
    doc_gap_details: dict | None = None
    """DocGapEvent payload (reason, layers, entities, artifacts) when doc_gap is True."""
    stream_sniffed: bool = False
    """True when precomputed_answer is None specifically because the streaming
    sniff-buffer already validated sufficiency and started delivering tokens
    live; stream_prefix/stream_remainder carry the partially-consumed answer.
    A RAGContext with stream_sniffed=True must be consumed exactly once by the
    streaming caller -- stream_remainder is a live, partially-drained async
    generator and cannot be replayed or cached across turns."""
    stream_prefix: str | None = None
    """Sniffed prefix already generated; flush as the first streamed chunk(s)
    when stream_sniffed is True."""
    stream_remainder: AsyncGenerator[str, None] | None = None
    """The same still-open generate_answer_stream() call to keep draining for
    the rest of the answer. None when the whole answer already fit inside the
    sniff buffer (nothing left to drain) even though stream_sniffed is True."""


async def _prepare_rag_context(
    conversation: Conversation,
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    user_id: str | None = None,
    precompute: bool = True,
    project_ids: list[str] | None = None,
    db: AsyncSession | None = None,
    images: list[str] | None = None,
) -> RAGContext:
    """Run intent routing, search, and context building.

    Returns a RAGContext. When ``precomputed_answer`` is set the caller should
    use it directly (RESPOND / SUMMARY paths, or context-first hit).  Otherwise
    the caller should generate from ``llm_messages`` (streaming or not).
    """
    existing_messages = list(conversation.messages)
    has_history = len(existing_messages) > 0
    trace = AgentTrace()

    # 0. Recall memories from Hindsight (non-blocking, empty when disabled)
    user_memories: list[str] = []
    quality_insights: list[str] = []
    corrections: list[str] = []
    project_memories: list[str] = []
    if user_id:
        trace.complete(AgentTool.RECALL_MEMORY)
        # Ensure banks exist (idempotent)
        bank_tasks = [ensure_user_bank(user_id), ensure_quality_bank(), ensure_corrections_bank()]
        if project_ids and settings.hindsight_project_banks_enabled:
            for pid in project_ids:
                bank_tasks.append(ensure_project_bank(pid))
        await asyncio.gather(*bank_tasks, return_exceptions=True)

        # Recall from all banks in parallel
        recall_tasks: list = [
            recall_user_memory(user_id, query, project_ids=project_ids),
            recall_quality_insights(query),
            recall_corrections(query, project_ids=project_ids),
        ]
        if project_ids and settings.hindsight_project_banks_enabled:
            for pid in project_ids:
                recall_tasks.append(recall_project_memory(pid, query))

        results = await asyncio.gather(*recall_tasks, return_exceptions=True)
        # Unpack: first 3 are user, quality, corrections; rest are project memories
        user_memories = results[0] if not isinstance(results[0], Exception) else []
        quality_insights = results[1] if not isinstance(results[1], Exception) else []
        corrections = results[2] if not isinstance(results[2], Exception) else []
        for r in results[3:]:
            if not isinstance(r, Exception) and r:
                project_memories.extend(r)

    memory_context = _build_memory_context(
        user_memories, quality_insights, corrections,
        project_memories=project_memories,
    )
    answer_style = "code" if should_use_code_assistant(query, project_ids) else "assistant"
    if memory_context:
        logger.info(
            "Injecting %d user memories + %d quality insights + %d corrections + %d project memories",
            len(user_memories), len(quality_insights), len(corrections), len(project_memories),
        )

    # 1. Route query via LLM classifier
    conv_ctx = (
        f"The user is in an active conversation with {len(existing_messages)} previous messages."
        if has_history else None
    )
    analysis = await analyze_query_async(query, conversation_context=conv_ctx)
    document_sources: list[DocumentSource] = []

    # Runtime-intent gate: single source of truth for whether this turn enters
    # the runtime tool loop (classifier signal OR keyword backstop). Computed
    # once per turn so the streaming and non-streaming entry points agree.
    keyword_hit = detect_runtime_keywords(query)
    runtime_gate = analysis.runtime_intent or keyword_hit
    logger.info(
        "Runtime gate: %s (classifier=%s, keyword=%s) query=%r",
        "IN" if runtime_gate else "OUT", analysis.runtime_intent, keyword_hit, query[:80],
    )

    # Fetch system knowledge for injection
    system_knowledge: str | None = None
    if db and settings.system_knowledge_injection:
        try:
            system_knowledge = await get_system_knowledge_summary(db, project_ids)
        except Exception as e:
            logger.warning("Conversational: Failed to fetch system knowledge: %s", e)

    if analysis.action == Action.METADATA and db:
        # Metadata query — answer from PostgreSQL
        logger.info("Conversational metadata query: intent=%s, project=%s",
                     analysis.metadata_intent, analysis.extracted_project)
        trace.complete(AgentTool.FETCH_METADATA)
        metadata = await fetch_metadata(
            db, analysis.metadata_intent, project_ids, analysis.extracted_project,
            user_id=int(user_id) if user_id else None,
        )

        # Enrich metadata with document content for content-relevant intents
        hybrid_sources: list[Source] = []
        content_relevant = analysis.metadata_intent not in (
            MetadataIntent.MY_PROFILE,
            MetadataIntent.DOCUMENT_LIST,
        )
        if content_relevant:
            # Use targeted query for project_detail to find overview docs
            if analysis.metadata_intent == MetadataIntent.PROJECT_DETAIL:
                project_name = analysis.extracted_project or "project"
                enrichment_query = f"{project_name} executive summary overview architecture assessment"
            else:
                enrichment_query = analysis.cleaned_query

            if settings.reranker_enabled:
                hybrid_sources = await search_and_rerank(
                    client=qdrant,
                    query=enrichment_query,
                    user_groups=user_groups,
                    top_k=top_k,
                    threshold=threshold,
                    project_ids=project_ids,
                )
            else:
                hybrid_sources = await search_similar(
                    client=qdrant,
                    query=enrichment_query,
                    user_groups=user_groups,
                    top_k=top_k,
                    threshold=threshold,
                    project_ids=project_ids,
                )

        document_sources = []
        if hybrid_sources:
            document_sources = merge_sources_by_document(hybrid_sources)
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            messages = build_metadata_messages(
                query, metadata.text,
                sources=hybrid_sources, reference_numbers=ref_map,
                memory_context=memory_context,
            )
        else:
            messages = build_metadata_messages(
                query, metadata.text, memory_context=memory_context,
            )

        if precompute:
            answer = await generate_answer(messages)
        else:
            answer = None

        return RAGContext(
            llm_messages=messages,
            document_sources=document_sources,
            precomputed_answer=answer,
            analysis=analysis,
            agent_steps=trace.steps,
        )

    if analysis.action == Action.RESPOND:
        # Chitchat — skip search, respond via unified no-results prompt
        history = truncate_history(existing_messages) if has_history else None
        messages = build_no_results_messages(query, history=history, memory_context=memory_context, system_knowledge=system_knowledge)
        if precompute:
            answer = await generate_answer(messages)
        else:
            answer = None
        return RAGContext(
            llm_messages=messages,
            document_sources=[],
            precomputed_answer=answer,
            analysis=analysis,
            agent_steps=trace.steps,
        )

    if analysis.action == Action.CONVERSATION_SUMMARY:
        # Conversation summary — build from chat history, no document search
        logger.info("Conversation summary requested — building from history")
        history = truncate_history(
            existing_messages,
            max_tokens=MAX_HISTORY_TOKENS * 2,
            max_messages=MAX_HISTORY_MESSAGES * 2,
        )
        messages = build_conversation_summary_messages(query, history, memory_context=memory_context)
        answer = await generate_answer(messages) if precompute else None
        return RAGContext(
            llm_messages=messages,
            document_sources=[],
            precomputed_answer=answer,
            analysis=analysis,
            agent_steps=trace.steps,
        )

    if analysis.query_type == QueryType.SUMMARY:
        # SUMMARY — map-reduce pipeline for full-document summarization
        logger.info("Summary query detected in conversation — resolving document")
        trace.complete(AgentTool.SUMMARIZE_DOCUMENT)
        resolved = await resolve_document_id(
            qdrant,
            analysis.cleaned_query,
            user_groups,
            project_ids=project_ids,
        )
        if resolved:
            doc_id, filename = resolved
            answer, doc_source = await run_map_reduce(qdrant, doc_id, filename, user_groups)
            return RAGContext(
                llm_messages=[],
                document_sources=[doc_source],
                precomputed_answer=answer,
                analysis=analysis,
                agent_steps=trace.steps,
            )
        elif has_history:
            logger.info("Document not found for summary — falling back to conversation summary")
            history = truncate_history(
                existing_messages,
                max_tokens=MAX_HISTORY_TOKENS * 2,
                max_messages=MAX_HISTORY_MESSAGES * 2,
            )
            messages = build_conversation_summary_messages(query, history, memory_context=memory_context)
            answer = await generate_answer(messages) if precompute else None
            return RAGContext(
                llm_messages=messages,
                document_sources=[],
                precomputed_answer=answer,
                analysis=analysis,
                agent_steps=trace.steps,
            )
        else:
            logger.info("Could not resolve document for summary — falling back to no-results")
            messages = build_no_results_messages(query, memory_context=memory_context, system_knowledge=system_knowledge)
            if precompute:
                answer = await generate_answer(messages)
            else:
                answer = None
            return RAGContext(
                llm_messages=messages,
                document_sources=[],
                precomputed_answer=answer,
                analysis=analysis,
                agent_steps=trace.steps,
            )

    # Runtime-gated turns skip the document-sufficiency ladder entirely: the
    # tool loop (not doc sufficiency) decides whether more info is needed, and
    # a pure runtime question must still reach the tool loop even when
    # retrieval finds zero matching documents (D3/D1, .plans/02_prd.md).
    if runtime_gate:
        trace.complete(AgentTool.SEARCH_DOCUMENTS)
        sources = await _search_qa_sources(
            qdrant=qdrant,
            query=analysis.cleaned_query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=threshold,
            project_ids=project_ids,
            document_roles=["source"],
        )
        auxiliary_sources = await _search_qa_sources(
            qdrant=qdrant,
            query=analysis.cleaned_query,
            user_groups=user_groups,
            top_k=max(1, min(4, top_k)),
            threshold=threshold,
            project_ids=project_ids,
            document_roles=list(AUXILIARY_DOCUMENT_ROLES),
        )
        all_sources = [*sources, *auxiliary_sources]
        document_sources = merge_sources_by_document(all_sources) if all_sources else []
        ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
        llm_messages = build_conversational_messages(
            query, sources, existing_messages, reference_numbers=ref_map,
            memory_context=memory_context,
            answer_style=answer_style,
            auxiliary_sources=auxiliary_sources,
        )
        return RAGContext(
            llm_messages=llm_messages,
            document_sources=document_sources,
            precomputed_answer=None,
            allow_runtime_tools=True,
            analysis=analysis,
            agent_steps=trace.steps,
            primary_source_layer=document_sources[0].source_layer if document_sources else None,
        )

    # QA — try context-first, search only if needed
    previous_doc_sources = get_previous_sources(existing_messages, project_ids=project_ids)

    if previous_doc_sources and precompute:
        # Try answering from previous context + history (non-streaming only)
        # Skipped when precompute=False (streaming) to avoid blocking non-streaming call
        # memory_context excluded: this check only needs to know if prior docs cover the follow-up;
        # injecting memory here causes a three-way topic collision that triggers hallucination.
        logger.info("Trying to answer from previous context (no search)")
        trace.complete(AgentTool.REUSE_PREVIOUS_SOURCES)
        context = build_context_from_document_sources(previous_doc_sources)
        llm_messages = build_conversational_messages_with_context(
            query, context, existing_messages,
            memory_context=None,
            answer_style=answer_style,
        )
        answer = await generate_answer(llm_messages)

        if not needs_new_search(answer):
            # LLM answered from context — done, no search needed
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=previous_doc_sources,
                precomputed_answer=answer,
                analysis=analysis,
                agent_steps=trace.steps,
            )
        else:
            # LLM needs more info — fall through to search
            logger.info("[NEED_SEARCH] detected — running semantic search")
            previous_doc_sources = []  # clear so we enter search below
    elif previous_doc_sources and not precompute:
        # Streaming path: run context-first check (same as non-streaming), but
        # via the sniff-buffer so a sufficient answer streams live instead of
        # blocking on the full generation (Slice 2).
        # memory_context excluded for the same reason as the non-streaming path above.
        logger.info("Streaming mode — running context-first check")
        trace.complete(AgentTool.REUSE_PREVIOUS_SOURCES)
        context = build_context_from_document_sources(previous_doc_sources)
        llm_messages = build_conversational_messages_with_context(
            query, context, existing_messages,
            memory_context=None,
            answer_style=answer_style,
        )
        sniff = await _sniff_stream_sufficiency(llm_messages, images=None)
        if not sniff.insufficient:
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=previous_doc_sources,
                precomputed_answer=None,
                stream_sniffed=True,
                stream_prefix=sniff.prefix,
                stream_remainder=sniff.remainder,
                analysis=analysis,
                agent_steps=trace.steps,
            )
        else:
            logger.info("[NEED_SEARCH] detected in streaming — running search")
            previous_doc_sources = []

    if not previous_doc_sources:
        # Search path (first message, or LLM requested search)
        trace.complete(AgentTool.SEARCH_DOCUMENTS)
        sources = await _search_qa_sources(
            qdrant=qdrant,
            query=analysis.cleaned_query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=threshold,
            project_ids=project_ids,
            document_roles=["source"],
        )
        auxiliary_sources = await _search_qa_sources(
            qdrant=qdrant,
            query=analysis.cleaned_query,
            user_groups=user_groups,
            top_k=max(1, min(4, top_k)),
            threshold=threshold,
            project_ids=project_ids,
            document_roles=list(AUXILIARY_DOCUMENT_ROLES),
        )
        if has_template_intent(query):
            auxiliary_sources.extend(
                await _search_qa_sources(
                    qdrant=qdrant,
                    query=analysis.cleaned_query,
                    user_groups=user_groups,
                    top_k=max(1, min(2, top_k)),
                    threshold=threshold,
                    project_ids=project_ids,
                    document_roles=list(TEMPLATE_DOCUMENT_ROLES),
                )
            )

        # --- Retrieval ladder rung 1: default corpus partition ---
        # Entity detection: the per-project alias table is the primary source
        # (deterministic, no PascalCase false positives). The regex heuristic
        # remains only for projects that were never alias-synced.
        entity_matches: list = []
        alias_backed = False
        if db is not None:
            try:
                match_result = await match_query_entities(
                    db, query, project_ids, limit=settings.rung2_entity_match_limit,
                )
                entity_matches = match_result.matches
                alias_backed = match_result.alias_rows_present
            except Exception as e:
                logger.warning("Entity alias matching failed; using heuristic: %s", e, exc_info=True)
        if alias_backed:
            identifiers = [m.alias for m in entity_matches]
        else:
            identifiers = _extract_query_identifiers(query)
        matched_units = matched_unit_names(entity_matches)

        insufficiency: str | None = None
        rung1_answer: str | None = None
        rung1_messages: list[dict[str, str]] = []
        rung1_stream_sniff: _SniffResult | None = None

        if sources:
            logger.info("--- Retrieved Chunks ---")
            for i, s in enumerate(sources, 1):
                logger.info(f"  #{i} [{s.filename}] score={s.score:.4f}")

            logger.info("--- LLM Input Preview ---")
            for i, s in enumerate(sources, 1):
                preview = s.text[:100].replace("\n", " ")
                logger.info(f"  Chunk {i}: {preview!r}...")

            enc = tiktoken.get_encoding(ENCODING_NAME)
            total_context_tokens = sum(len(enc.encode(s.text)) for s in sources)
            logger.info(
                f"Logic trace: Retrieved {len(sources)} chunks; "
                f"Threshold {threshold} met; Sending ~{total_context_tokens} context tokens to LLM"
            )

            document_sources = merge_sources_by_document(sources)
            if auxiliary_sources:
                document_sources = merge_sources_by_document([*sources, *auxiliary_sources])
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            rung1_messages = build_conversational_messages(
                query, sources, existing_messages, reference_numbers=ref_map,
                memory_context=memory_context,
                answer_style=answer_style,
                context_first=True,
                auxiliary_sources=auxiliary_sources,
            )
            # The ladder gate needs to judge sufficiency before returning. On
            # the REST (precompute=True) path this still requires the full
            # blocking answer. On the streaming path (Slice 2), the
            # sniff-buffer judges sufficiency from only the first ~200 chars
            # and hands back the still-open stream for real token-by-token
            # delivery of the rest.
            if precompute:
                rung1_answer = await generate_answer(rung1_messages, images=images)
                if needs_new_search(rung1_answer):
                    insufficiency = "need_search"
                elif _identifiers_missing_from_sources(identifiers, sources):
                    insufficiency = "missing_identifiers"
            else:
                rung1_stream_sniff = await _sniff_stream_sufficiency(rung1_messages, images=images)
                if rung1_stream_sniff.insufficient:
                    insufficiency = "need_search"
                elif _identifiers_missing_from_sources(identifiers, sources):
                    insufficiency = "missing_identifiers"
                    if rung1_stream_sniff.remainder is not None:
                        await rung1_stream_sniff.remainder.aclose()
        else:
            insufficiency = "no_sources"

        if insufficiency is None:
            if rung1_stream_sniff is not None:
                return RAGContext(
                    llm_messages=rung1_messages,
                    document_sources=document_sources,
                    precomputed_answer=None,
                    stream_sniffed=True,
                    stream_prefix=rung1_stream_sniff.prefix,
                    stream_remainder=rung1_stream_sniff.remainder,
                    analysis=analysis,
                    agent_steps=trace.steps,
                    primary_source_layer=document_sources[0].source_layer or "canonical_docs",
                )
            return RAGContext(
                llm_messages=rung1_messages,
                document_sources=document_sources,
                precomputed_answer=rung1_answer,
                analysis=analysis,
                agent_steps=trace.steps,
                primary_source_layer=document_sources[0].source_layer or "canonical_docs",
            )

        # --- Retrieval ladder rung 2: explicit internal layers ---
        rung2_layers = _route_internal_layers(query)
        logger.info(
            "Retrieval ladder rung 2: reason=%s layers=%s identifiers=%s",
            insufficiency, rung2_layers, identifiers,
        )
        internal_sources = await search_layered(
            qdrant,
            analysis.cleaned_query,
            user_groups,
            source_layers=rung2_layers,
            top_k=top_k,
            threshold=threshold,
            project_ids=project_ids,
            matched_units=matched_units or None,
        )

        if internal_sources:
            internal_docs = merge_sources_by_document(internal_sources)
            ref_map = {doc.document_id: doc.reference_number for doc in internal_docs}
            llm_messages = build_conversational_messages(
                query, internal_sources, existing_messages, reference_numbers=ref_map,
                memory_context=memory_context,
                answer_style=answer_style,
                context_first=True,
            )
            answer = await generate_answer(llm_messages, images=images)
            if not needs_new_search(answer):
                gap_project = (
                    internal_docs[0].project_id
                    or internal_docs[0].filename.split("/", 1)[0]
                )
                return RAGContext(
                    llm_messages=llm_messages,
                    document_sources=internal_docs,
                    precomputed_answer=answer,
                    analysis=analysis,
                    agent_steps=trace.steps,
                    primary_source_layer=internal_docs[0].source_layer or rung2_layers[0],
                    fallback_used=True,
                    doc_gap=True,
                    doc_gap_details={
                        "project_id": gap_project,
                        "run_id": internal_docs[0].run_id,
                        "insufficiency_reason": insufficiency,
                        "detected_entities": (
                            [m.canonical_name for m in entity_matches]
                            if alias_backed else identifiers
                        ),
                        "layers_used": rung2_layers,
                        "supporting_artifacts": sorted({s.filename for s in internal_sources}),
                        "supporting_chunk_ids": [
                            f"{s.document_id}:{s.chunk_index}" for s in internal_sources
                        ],
                    },
                )

        if insufficiency == "no_sources" and auxiliary_sources:
            document_sources = merge_sources_by_document(auxiliary_sources)
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            llm_messages = build_conversational_messages(
                query,
                [],
                existing_messages,
                reference_numbers=ref_map,
                memory_context=memory_context,
                answer_style=answer_style,
                context_first=True,
                auxiliary_sources=auxiliary_sources,
            )
            answer = await generate_answer(llm_messages, images=images) if precompute else None
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=document_sources,
                precomputed_answer=answer,
                analysis=analysis,
                agent_steps=trace.steps,
                fallback_used=True,
            )

        # Rung 2 unsupported — refuse cleanly, no doc-gap event
        history = truncate_history(existing_messages) if has_history else None
        llm_messages = build_no_results_messages(
            query,
            history=history,
            memory_context=memory_context,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
        )
        if precompute:
            answer = await generate_answer(llm_messages)
        else:
            answer = None
        return RAGContext(
            llm_messages=llm_messages,
            document_sources=[],
            precomputed_answer=answer,
            analysis=analysis,
            agent_steps=trace.steps,
            fallback_used=True,
        )


async def _detect_and_store_correction(
    query: str,
    answer: str,
    existing_messages: list[ConversationMessage],
    user_id: str,
    conversation_id: int | str | None,
) -> None:
    """Check if the user is correcting the assistant and store the correction.

    Runs a heuristic check first (cheap). If triggered, calls the LLM to extract
    a structured correction, then stores it in the shared corrections bank.
    This is fire-and-forget — failures are logged but never block the response.
    """
    # Find the most recent assistant message
    prev_assistant_msg: str | None = None
    for msg in reversed(existing_messages):
        if msg.role == "assistant":
            prev_assistant_msg = msg.content
            break

    if not is_likely_correction(query, prev_assistant_msg is not None):
        return

    logger.info("Correction heuristic triggered for user %s — extracting correction", user_id)
    try:
        messages = build_correction_extraction_messages(query, prev_assistant_msg or "")
        correction_text = await generate_answer(messages)
        await retain_correction(
            query=query,
            original_answer=prev_assistant_msg or "",
            correction_text=correction_text.strip(),
            user_id=user_id,
            conversation_id=conversation_id,
        )
    except Exception as e:
        logger.warning("Correction extraction/storage failed [%s]: %s", type(e).__name__, e)


async def run_conversational_rag(
    conversation: Conversation,
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    user_id: str | None = None,
    project_ids: list[str] | None = None,
    images: list[str] | None = None,
) -> MessageResponse:
    """Run the conversational RAG pipeline with context-first search.

    Flow:
    1. Route query via intent classifier
    2. For QA: try answering from previous sources first
    3. If LLM responds with [NEED_SEARCH] or no previous sources → search
    4. Save messages and return response

    Returns the assistant's response as a MessageResponse.
    """
    start = time.perf_counter()
    scope = RequestScope.from_project_ids(project_ids)

    ctx = await _prepare_rag_context(
        conversation, query, user_groups, qdrant, top_k, threshold,
        user_id=user_id, project_ids=scope.project_ids, db=db, images=images,
    )

    runtime_progress: list = []
    # Terminal precomputed answers (chitchat, metadata, summaries, no-results) are
    # used as-is. QA document-backed answers (allow_runtime_tools) and turns with no
    # precomputed answer go through the runtime tool loop so live logs and indexed
    # docs can be combined in one turn.
    if ctx.precomputed_answer is not None and not ctx.allow_runtime_tools:
        answer = ctx.precomputed_answer
        usage = get_last_usage()
    else:
        loop_outcome = await run_runtime_tool_loop(
            ctx.llm_messages, images=images, project_scope=scope.project_scope, db=db,
            prior_runtime_summary=get_latest_runtime_tool_summary(conversation.messages),
        )
        runtime_progress = loop_outcome.progress_events
        if loop_outcome.final_answer is not None:
            answer = loop_outcome.final_answer
            usage = loop_outcome.usage or get_last_usage()
            if loop_outcome.used_runtime_data:
                logger.info(
                    "Runtime tool loop answered conversation %s with %d step(s)",
                    conversation.id, len(loop_outcome.progress_events),
                )
        elif ctx.precomputed_answer is not None:
            # No structured tool support: fall back to the prepared document answer,
            # but surface the degradation rather than dropping runtime silently.
            answer = f"{ctx.precomputed_answer}\n\n{_RUNTIME_UNAVAILABLE_NOTE}"
            usage = get_last_usage()
        else:
            generated = await generate_answer(ctx.llm_messages, images=images)
            answer = f"{generated}\n\n{_RUNTIME_UNAVAILABLE_NOTE}"
            usage = get_last_usage()

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Persist token usage
    if usage:
        try:
            entry = QueryUsage(
                user_id=int(user_id) if user_id else None,
                interface="conversation",
                query_text=query[:500],
                model_name=usage.model,
                provider=usage.provider,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                embedding_tokens=get_last_embed_tokens(),
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                llm_ms=usage.latency_ms,
                conversation_id=conversation.id,
            )
            db.add(entry)
        except Exception as db_err:
            logger.warning("Failed to persist conversation usage: %s", db_err)

    # Safety: strip any [NEED_SEARCH] marker that leaked into the final answer
    answer = answer.replace(_NEED_SEARCH_MARKER, "").strip()

    # Save user message
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
        image_data=images,
    )
    db.add(user_msg)

    # Save assistant message
    document_sources = ctx.document_sources
    sources_data = [s.model_dump() for s in document_sources] if document_sources else None
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sources=sources_data,
        latency_ms=latency_ms,
        primary_source_layer=ctx.primary_source_layer,
        fallback_used=ctx.fallback_used,
        doc_gap=ctx.doc_gap,
        runtime_tool_summary=build_runtime_tool_summary(runtime_progress),
    )
    db.add(assistant_msg)

    # Auto-generate title if this is the first message pair
    existing_messages = list(conversation.messages)
    if len(existing_messages) == 0 and not conversation.title:
        conversation.title = query[:50] + "..." if len(query) > 50 else query

    # Flush to get the ID assigned
    await db.flush()

    _add_doc_gap_event(db, ctx, query, user_id, conversation.id, assistant_msg.id)

    # Retain interaction to Hindsight (fire-and-forget — does not block response)
    if user_id:
        source_names = [s.filename for s in document_sources] if document_sources else []
        asyncio.create_task(retain_interaction(
            user_id=user_id,
            query=query,
            answer=answer,
            sources_used=source_names,
            conversation_id=conversation.id,
            search_result_count=len(document_sources),
            latency_ms=latency_ms,

            action=ctx.analysis.action.value if ctx.analysis else "search",
            query_type=ctx.analysis.query_type.value if ctx.analysis else "qa",
            project_ids=project_ids,
        ))
        # Detect and store corrections (fire-and-forget)
        asyncio.create_task(_detect_and_store_correction(
            query=query,
            answer=answer,
            existing_messages=existing_messages,
            user_id=user_id,
            conversation_id=conversation.id,
        ))

    logger.info(
        f"Conversational RAG completed in {latency_ms}ms, "
        f"{len(document_sources)} document sources, conversation {conversation.id}"
    )

    return MessageResponse(
        id=assistant_msg.id,
        role="assistant",
        content=answer,
        sources=[DocumentSource(**s) for s in sources_data] if sources_data else None,
        latency_ms=latency_ms,
        primary_source_layer=ctx.primary_source_layer,
        fallback_used=ctx.fallback_used,
        doc_gap=ctx.doc_gap,
        created_at=assistant_msg.created_at,
    )


async def run_conversational_rag_stream(
    conversation: Conversation,
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    user_id: str | None = None,
    project_ids: list[str] | None = None,
    images: list[str] | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """Streaming version of conversational RAG.

    Yields (event_type, data_json) tuples for SSE:
    - ("step", json_object)            — bounded read-only agent trace step
    - ("sources", json_array)          — document sources
    - ("runtime_tool_call", json_object)   — backend runtime tool-use loop requested a tool (EP-004 slice 3)
    - ("runtime_tool_result", json_object) — sanitized result of that tool call (EP-004 slice 3)
    - ("token", json_string)           — individual LLM tokens, streamed live as the runtime tool
      loop's tool calls resolve; delivered as a single token only when no tool ran that turn
    - ("usage", json_object)           — token counts + cost
    - ("done", json_object)            — {message_id, latency_ms}

    Saves user + assistant messages to DB after streaming completes.
    """
    start = time.perf_counter()
    scope = RequestScope.from_project_ids(project_ids)

    # Save user message before streaming starts
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
        image_data=images,
    )
    db.add(user_msg)
    await db.flush()

    # Auto-generate title if this is the first message pair
    existing_messages = list(conversation.messages)
    # Exclude the user_msg we just added (it's in the session but not yet in existing_messages)
    prior_messages = [m for m in existing_messages if m.id != user_msg.id]
    if len(prior_messages) == 0 and not conversation.title:
        conversation.title = query[:50] + "..." if len(query) > 50 else query

    ctx = await _prepare_rag_context(
        conversation, query, user_groups, qdrant, top_k, threshold,
        user_id=user_id, precompute=False, project_ids=scope.project_ids, db=db,
        images=images,
    )

    import json

    # Emit bounded read-only agent steps before the answer stream. Existing
    # clients can ignore this optional event type.
    for step in ctx.agent_steps or []:
        yield ("step", json.dumps(step.model_dump()))

    # Emit sources
    sources_data = [s.model_dump() for s in ctx.document_sources] if ctx.document_sources else []
    yield ("sources", json.dumps(sources_data))

    # Stream LLM tokens (or emit precomputed answer as single token)
    accumulated = ""
    loop_usage = None
    runtime_progress: list = []
    # allow_runtime_tools is the single gate for entering the tool loop (set by
    # the runtime-intent gate in _prepare_rag_context, not by whether an answer
    # was precomputed). Turns that never gated in stream directly: the already-
    # generated answer as-is, or a fresh generate_answer_stream call when
    # nothing was precomputed (chitchat/metadata/summary on the streaming path).
    if not ctx.allow_runtime_tools:
        if ctx.stream_sniffed:
            # Sniff-buffer already validated sufficiency (Slice 2): flush the
            # sniffed prefix immediately, then keep draining the same still-
            # open provider stream for real token-by-token delivery of the
            # rest, stripping a late marker if one shows up past the buffer.
            if ctx.stream_prefix:
                accumulated = ctx.stream_prefix
                yield ("token", json.dumps(ctx.stream_prefix))
            if ctx.stream_remainder is not None:
                async for chunk in _drain_stripping_late_marker(ctx.stream_remainder):
                    accumulated += chunk
                    yield ("token", json.dumps(chunk))
        elif ctx.precomputed_answer is not None:
            token = ctx.precomputed_answer.replace(_NEED_SEARCH_MARKER, "").strip()
            accumulated = token
            yield ("token", json.dumps(token))
        else:
            async for token in generate_answer_stream(ctx.llm_messages, images=images):
                accumulated += token
                yield ("token", json.dumps(token))
    else:
        # Genuinely runtime-gated turn: logs and docs can combine; on a
        # provider without tool support this degrades to the documents-only
        # answer with a visible note appended to the stream. Tool-call/result
        # events and the synthesized final answer are consumed live as they
        # happen, not batched after the whole loop finishes.
        loop_outcome: RuntimeLoopOutcome | None = None
        any_answer_chunk = False
        async for event in run_runtime_tool_loop_stream(
            ctx.llm_messages, images=images, project_scope=scope.project_scope, db=db,
            prior_runtime_summary=get_latest_runtime_tool_summary(conversation.messages),
        ):
            if isinstance(event, RuntimeProgressEvent):
                yield (event.event_type, json.dumps(event.payload))
            elif isinstance(event, RuntimeAnswerChunk):
                any_answer_chunk = True
                accumulated += event.text
                yield ("token", json.dumps(event.text))
            elif isinstance(event, RuntimeLoopOutcome):
                loop_outcome = event

        runtime_progress = loop_outcome.progress_events if loop_outcome else []
        if loop_outcome is not None and loop_outcome.final_answer is not None:
            loop_usage = loop_outcome.usage
            if not any_answer_chunk:
                # No tool was ever called: single-block delivery, preserved
                # exactly as before -- not a bug (see run_runtime_tool_loop_stream
                # docstring, the "no tools ran" exception).
                token = loop_outcome.final_answer.replace(_NEED_SEARCH_MARKER, "").strip()
                accumulated = token
                yield ("token", json.dumps(token))
        elif ctx.precomputed_answer is not None:
            token = ctx.precomputed_answer.replace(_NEED_SEARCH_MARKER, "").strip()
            note = f"\n\n{_RUNTIME_UNAVAILABLE_NOTE}"
            accumulated = token + note
            yield ("token", json.dumps(token))
            yield ("token", json.dumps(note))
        else:
            async for token in generate_answer_stream(ctx.llm_messages, images=images):
                accumulated += token
                yield ("token", json.dumps(token))
            note = f"\n\n{_RUNTIME_UNAVAILABLE_NOTE}"
            accumulated += note
            yield ("token", json.dumps(note))

    # Safety: strip any [NEED_SEARCH] marker
    accumulated = accumulated.replace(_NEED_SEARCH_MARKER, "").strip()

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Emit usage event (token counts + cost)
    usage = loop_usage or get_last_usage()
    usage_data = {
        "input_tokens": usage.input_tokens if usage else 0,
        "output_tokens": usage.output_tokens if usage else 0,
        "model": usage.model if usage else "",
        "provider": usage.provider if usage else "",
        "cost_usd": usage.cost_usd if usage else 0.0,
        "latency_ms": latency_ms,
    }
    yield ("usage", json.dumps(usage_data))

    # Save assistant message
    sources_db = [s.model_dump() for s in ctx.document_sources] if ctx.document_sources else None
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=accumulated,
        sources=sources_db,
        latency_ms=latency_ms,
        primary_source_layer=ctx.primary_source_layer,
        fallback_used=ctx.fallback_used,
        doc_gap=ctx.doc_gap,
        runtime_tool_summary=build_runtime_tool_summary(runtime_progress),
    )
    db.add(assistant_msg)
    await db.flush()

    _add_doc_gap_event(db, ctx, query, user_id, conversation.id, assistant_msg.id)

    # Persist usage to DB
    if usage:
        try:
            from src.db.models import QueryUsage
            entry = QueryUsage(
                user_id=int(user_id) if user_id else None,
                interface="conversation",
                query_text=query[:500],
                model_name=usage.model,
                provider=usage.provider,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                embedding_tokens=get_last_embed_tokens(),
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                llm_ms=usage.latency_ms,
                conversation_id=conversation.id,
            )
            db.add(entry)
        except Exception as db_err:
            logger.warning("Failed to persist conversation usage: %s", db_err)

    # Retain interaction to Hindsight (fire-and-forget — does not block response)
    if user_id:
        source_names = [s.filename for s in ctx.document_sources] if ctx.document_sources else []
        asyncio.create_task(retain_interaction(
            user_id=user_id,
            query=query,
            answer=accumulated,
            sources_used=source_names,
            conversation_id=conversation.id,
            search_result_count=len(ctx.document_sources),
            latency_ms=latency_ms,

            action=ctx.analysis.action.value if ctx.analysis else "search",
            query_type=ctx.analysis.query_type.value if ctx.analysis else "qa",
            project_ids=project_ids,
        ))
        # Detect and store corrections (fire-and-forget)
        asyncio.create_task(_detect_and_store_correction(
            query=query,
            answer=accumulated,
            existing_messages=prior_messages,
            user_id=user_id,
            conversation_id=conversation.id,
        ))

    logger.info(
        f"Conversational RAG (stream) completed in {latency_ms}ms, "
        f"{len(ctx.document_sources)} document sources, conversation {conversation.id}"
    )

    yield ("done", json.dumps({
        "message_id": assistant_msg.id,
        "latency_ms": latency_ms,
        "primary_source_layer": ctx.primary_source_layer,
        "fallback_used": ctx.fallback_used,
        "doc_gap": ctx.doc_gap,
    }))
