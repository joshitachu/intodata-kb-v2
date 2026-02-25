"""Conversational RAG pipeline with session-based memory."""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import tiktoken
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, ConversationMessage
from src.generation.intent_classifier import Action, QueryType, analyze_query
from src.generation.llm_client import generate_answer, generate_answer_stream
from src.generation.map_reduce import resolve_document_id, run_map_reduce
from src.generation.prompt_builder import build_memory_context, build_no_results_messages
from src.config import settings
from src.memory.hindsight_client import recall_user_memory, retain_interaction
from src.retrieval.search import (
    merge_sources_by_document,
    search_and_rerank,
    search_multi_query,
    search_similar,
    search_similar_hybrid,
)
from src.schemas import DocumentSource, MessageResponse, Source

logger = logging.getLogger(__name__)

ENCODING_NAME = "cl100k_base"
MAX_HISTORY_TOKENS = 2000
MAX_CONTEXT_TOKENS = 3000
MAX_HISTORY_MESSAGES = 10

CONVERSATIONAL_SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "You have access to the conversation history and retrieved context. "
    "Answer the user's question based ONLY on the provided context. "
    "If the context does not contain enough information, say so clearly.\n\n"
    "## Formatting rules\n"
    "- Use **markdown** in your answers: headings, bullet lists, bold, code blocks where appropriate.\n"
    "- Cite sources using numbered references like [1], [2], etc. Only cite a source if you actually use information from it.\n"
    "- Do NOT cite sources for basic questions unrelated to the knowledge base.\n\n"
    "For follow-up questions, use conversation context to understand references "
    "like 'it', 'that', 'the previous topic'."
)

CONTEXT_FIRST_SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "You have access to the conversation history and retrieved context. "
    "Answer the user's question based ONLY on the provided context. "
    "If the provided context does not contain enough information to answer "
    "the question, respond with exactly [NEED_SEARCH] and nothing else.\n\n"
    "## Formatting rules\n"
    "- Use **markdown** in your answers: headings, bullet lists, bold, code blocks where appropriate.\n"
    "- Cite sources using numbered references like [1], [2], etc. Only cite a source if you actually use information from it.\n"
    "- Do NOT cite sources for basic questions unrelated to the knowledge base.\n\n"
    "For follow-up questions, use conversation context to understand references "
    "like 'it', 'that', 'the previous topic'."
)


def get_previous_sources(messages: list[ConversationMessage]) -> list[DocumentSource]:
    """Get sources from the most recent assistant message that has them."""
    for msg in reversed(messages):
        if msg.role == "assistant" and msg.sources:
            return [DocumentSource(**s) for s in msg.sources]
    return []


def needs_new_search(answer: str) -> bool:
    """Check if the LLM response indicates it needs more information."""
    return "[NEED_SEARCH]" in answer


def build_context_from_document_sources(
    doc_sources: list[DocumentSource],
    max_tokens: int = MAX_CONTEXT_TOKENS,
) -> str:
    """Build context string from DocumentSource objects (previous sources)."""
    enc = tiktoken.get_encoding(ENCODING_NAME)
    parts: list[str] = []
    total_tokens = 0

    for ds in doc_sources:
        header = f"[Reference {ds.reference_number}: {ds.filename}]"
        block = f"{header}\n{ds.text}\n"
        block_tokens = len(enc.encode(block))

        if total_tokens + block_tokens > max_tokens:
            break
        parts.append(block)
        total_tokens += block_tokens

    return "\n".join(parts)


def build_conversational_messages_with_context(
    query: str,
    context: str,
    history: list[ConversationMessage],
    memories: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build chat messages for the context-first attempt.

    Uses CONTEXT_FIRST_SYSTEM_PROMPT which includes the [NEED_SEARCH] instruction,
    so the LLM can signal when it needs fresh search results.
    """
    messages: list[dict[str, str]] = []
    messages.append({"role": "system", "content": CONTEXT_FIRST_SYSTEM_PROMPT})

    memory_block = build_memory_context(memories) if memories else ""
    if memory_block:
        messages.append({"role": "system", "content": memory_block})

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

    Takes the most recent messages first, working backwards until we hit
    the token limit. Returns messages in chronological order.
    """
    enc = tiktoken.get_encoding(ENCODING_NAME)

    # Take last N messages first
    recent = messages[-max_messages:] if len(messages) > max_messages else messages

    # Work backwards from most recent, collecting messages within token budget
    selected: list[dict[str, str]] = []
    total_tokens = 0

    for msg in reversed(recent):
        msg_tokens = len(enc.encode(msg.content))
        if total_tokens + msg_tokens > max_tokens:
            break
        selected.append({"role": msg.role, "content": msg.content})
        total_tokens += msg_tokens

    # Reverse to get chronological order
    selected.reverse()
    return selected


def build_context(
    sources: list[Source],
    max_tokens: int = MAX_CONTEXT_TOKENS,
    reference_numbers: dict[int, int] | None = None,
) -> str:
    """Concatenate source texts, truncating to stay within token budget."""
    enc = tiktoken.get_encoding(ENCODING_NAME)
    parts: list[str] = []
    total_tokens = 0

    for src in sources:
        if reference_numbers and src.document_id in reference_numbers:
            ref_num = reference_numbers[src.document_id]
            header = f"[Reference {ref_num}: {src.filename}]"
        else:
            header = f"[Source: {src.filename}, chunk {src.chunk_index}]"
        block = f"{header}\n{src.text}\n"
        block_tokens = len(enc.encode(block))

        if total_tokens + block_tokens > max_tokens:
            break
        parts.append(block)
        total_tokens += block_tokens

    return "\n".join(parts)


def build_conversational_messages(
    query: str,
    sources: list[Source],
    history: list[ConversationMessage],
    reference_numbers: dict[int, int] | None = None,
    memories: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build chat messages with system prompt, context, history, and current query.

    Message structure:
    1. System prompt
    2. User memories from Hindsight (optional)
    3. Context from retrieved sources (as system message)
    4. Truncated conversation history (user/assistant messages)
    5. Current user query
    """
    messages: list[dict[str, str]] = []

    # 1. System prompt
    messages.append({"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT})

    # 2. User memories (Hindsight)
    memory_block = build_memory_context(memories) if memories else ""
    if memory_block:
        messages.append({"role": "system", "content": memory_block})

    # 3. Context from sources
    if sources:
        context = build_context(sources, reference_numbers=reference_numbers)
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


@dataclass
class RAGContext:
    """Result of the RAG preparation step (search + routing)."""

    llm_messages: list[dict[str, str]]
    document_sources: list[DocumentSource]
    precomputed_answer: str | None
    """Non-None when the answer was already computed (RESPOND / SUMMARY / context-first hit)."""
    user_memories: list[str] | None = None
    """Recalled Hindsight memories for this user (if any)."""


async def _prepare_rag_context(
    conversation: Conversation,
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    top_k: int = 3,
    threshold: float = 0.4,
    user_id: str | None = None,
) -> RAGContext:
    """Run intent routing, search, and context building.

    Returns a RAGContext. When ``precomputed_answer`` is set the caller should
    use it directly (RESPOND / SUMMARY paths, or context-first hit).  Otherwise
    the caller should generate from ``llm_messages`` (streaming or not).
    """
    existing_messages = list(conversation.messages)
    has_history = len(existing_messages) > 0

    # 0. Recall user memories from Hindsight (non-blocking, graceful degradation)
    user_memories: list[str] = []
    if user_id and settings.hindsight_enabled:
        user_memories = await recall_user_memory(user_id, query)
        if user_memories:
            logger.info("Recalled %d user memories for %s", len(user_memories), user_id)

    # 1. Route query via heuristics (no LLM call)
    analysis = analyze_query(query)
    document_sources: list[DocumentSource] = []

    if analysis.action == Action.RESPOND:
        # Chitchat — skip search, respond via unified no-results prompt
        history = truncate_history(existing_messages) if has_history else None
        messages = build_no_results_messages(query, history=history)
        answer = await generate_answer(messages)
        return RAGContext(
            llm_messages=messages,
            document_sources=[],
            precomputed_answer=answer,
            user_memories=user_memories or None,
        )

    if analysis.query_type == QueryType.SUMMARY:
        # SUMMARY — map-reduce pipeline for full-document summarization
        logger.info("Summary query detected in conversation — resolving document")
        resolved = await resolve_document_id(qdrant, analysis.cleaned_query, user_groups)
        if resolved:
            doc_id, filename = resolved
            answer, doc_source = await run_map_reduce(qdrant, doc_id, filename, user_groups)
            return RAGContext(
                llm_messages=[],
                document_sources=[doc_source],
                precomputed_answer=answer,
                user_memories=user_memories or None,
            )
        else:
            logger.info("Could not resolve document for summary — falling back to no-results")
            history = truncate_history(existing_messages) if has_history else None
            messages = build_no_results_messages(query, history=history)
            answer = await generate_answer(messages)
            return RAGContext(
                llm_messages=messages,
                document_sources=[],
                precomputed_answer=answer,
                user_memories=user_memories or None,
            )

    # QA — try context-first, search only if needed
    previous_doc_sources = get_previous_sources(existing_messages)

    if previous_doc_sources:
        # Try answering from previous context + history
        logger.info("Trying to answer from previous context (no search)")
        context = build_context_from_document_sources(previous_doc_sources)
        llm_messages = build_conversational_messages_with_context(
            query, context, existing_messages, memories=user_memories or None,
        )
        answer = await generate_answer(llm_messages)

        if not needs_new_search(answer):
            # LLM answered from context — done, no search needed
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=previous_doc_sources,
                precomputed_answer=answer,
                user_memories=user_memories or None,
            )
        else:
            # LLM needs more info — fall through to search
            logger.info("[NEED_SEARCH] detected — running semantic search")
            previous_doc_sources = []  # clear so we enter search below

    if not previous_doc_sources:
        # Search path (first message, or LLM requested search)
        if settings.query_expansion_enabled:
            sources = await search_multi_query(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
            )
        elif settings.reranker_enabled:
            sources = await search_and_rerank(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
            )
        elif settings.hybrid_search_enabled:
            sources = await search_similar_hybrid(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
            )
        else:
            sources = await search_similar(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
            )

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
            ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
            llm_messages = build_conversational_messages(
                query, sources, existing_messages, reference_numbers=ref_map,
                memories=user_memories or None,
            )
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=document_sources,
                precomputed_answer=None,
                user_memories=user_memories or None,
            )
        else:
            # No results — let LLM respond helpfully
            history = truncate_history(existing_messages) if has_history else None
            llm_messages = build_no_results_messages(query, history=history)
            answer = await generate_answer(llm_messages)
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=[],
                precomputed_answer=answer,
                user_memories=user_memories or None,
            )


async def run_conversational_rag(
    conversation: Conversation,
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    top_k: int = 3,
    threshold: float = 0.4,
    user_id: str | None = None,
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

    ctx = await _prepare_rag_context(
        conversation, query, user_groups, qdrant, top_k, threshold,
        user_id=user_id,
    )

    if ctx.precomputed_answer is not None:
        answer = ctx.precomputed_answer
    else:
        answer = await generate_answer(ctx.llm_messages)

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Safety: strip any [NEED_SEARCH] marker that leaked into the final answer
    answer = answer.replace("[NEED_SEARCH]", "").strip()

    # Save user message
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
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
    )
    db.add(assistant_msg)

    # Auto-generate title if this is the first message pair
    existing_messages = list(conversation.messages)
    if len(existing_messages) == 0 and not conversation.title:
        conversation.title = query[:50] + "..." if len(query) > 50 else query

    # Flush to get the ID assigned
    await db.flush()

    logger.info(
        f"Conversational RAG completed in {latency_ms}ms, "
        f"{len(document_sources)} document sources, conversation {conversation.id}"
    )

    # Retain interaction in Hindsight user memory (fire-and-forget, non-blocking)
    if user_id and settings.hindsight_enabled:
        summary = answer[:500] if len(answer) > 500 else answer
        await retain_interaction(user_id, query, summary, conversation_id=conversation.id)

    return MessageResponse(
        id=assistant_msg.id,
        role="assistant",
        content=answer,
        sources=[DocumentSource(**s) for s in sources_data] if sources_data else None,
        latency_ms=latency_ms,
        created_at=assistant_msg.created_at,
    )


async def run_conversational_rag_stream(
    conversation: Conversation,
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    top_k: int = 3,
    threshold: float = 0.4,
    user_id: str | None = None,
) -> AsyncGenerator[tuple[str, str], None]:
    """Streaming version of conversational RAG.

    Yields (event_type, data_json) tuples for SSE:
    - ("sources", json_array) — document sources
    - ("token", json_string)  — individual LLM tokens
    - ("done", json_object)   — {message_id, latency_ms}

    Saves user + assistant messages to DB after streaming completes.
    """
    start = time.perf_counter()

    # Save user message before streaming starts
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
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
        user_id=user_id,
    )

    import json

    # Emit sources
    sources_data = [s.model_dump() for s in ctx.document_sources] if ctx.document_sources else []
    yield ("sources", json.dumps(sources_data))

    # Stream LLM tokens (or emit precomputed answer as single token)
    accumulated = ""
    if ctx.precomputed_answer is not None:
        token = ctx.precomputed_answer.replace("[NEED_SEARCH]", "").strip()
        accumulated = token
        yield ("token", json.dumps(token))
    else:
        async for token in generate_answer_stream(ctx.llm_messages):
            accumulated += token
            yield ("token", json.dumps(token))

    # Safety: strip any [NEED_SEARCH] marker
    accumulated = accumulated.replace("[NEED_SEARCH]", "").strip()

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Save assistant message
    sources_db = [s.model_dump() for s in ctx.document_sources] if ctx.document_sources else None
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=accumulated,
        sources=sources_db,
        latency_ms=latency_ms,
    )
    db.add(assistant_msg)
    await db.flush()

    logger.info(
        f"Conversational RAG (stream) completed in {latency_ms}ms, "
        f"{len(ctx.document_sources)} document sources, conversation {conversation.id}"
    )

    # Retain interaction in Hindsight user memory (fire-and-forget, non-blocking)
    if user_id and settings.hindsight_enabled:
        summary = accumulated[:500] if len(accumulated) > 500 else accumulated
        await retain_interaction(user_id, query, summary, conversation_id=conversation.id)

    yield ("done", json.dumps({"message_id": assistant_msg.id, "latency_ms": latency_ms}))
