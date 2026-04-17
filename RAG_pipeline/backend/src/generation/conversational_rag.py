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

from src.db.models import Conversation, ConversationMessage, QueryUsage
from src.generation.intent_classifier import Action, MetadataIntent, QueryAnalysis, QueryType, analyze_query_async
from src.generation.prompt_builder import build_conversation_summary_messages
from src.generation.llm_client import generate_answer, generate_answer_stream, get_last_usage
from src.retrieval.embedder import get_last_embed_tokens
from src.generation.map_reduce import resolve_document_id, run_map_reduce
from src.generation.prompt_builder import build_correction_extraction_messages, build_metadata_messages, build_no_results_messages
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
MAX_HISTORY_TOKENS = 8000
MAX_CONTEXT_TOKENS = 8192
MAX_HISTORY_MESSAGES = 20

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
    "references like 'it', 'that', 'the previous topic'."
)

CONTEXT_FIRST_SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "You have access to the conversation history and retrieved context.\n\n"
    "## CRITICAL RULES\n"
    "1. Answer ONLY using information from the retrieved context below. "
    "NEVER use your general knowledge or training data to fill gaps.\n"
    "2. If the context does not contain the answer, respond with exactly "
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
    "references like 'it', 'that', 'the previous topic'."
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
    parts: list[str] = []
    if project_memories:
        parts.append("## Project knowledge")
        for mem in project_memories:
            parts.append(f"- {mem}")
    if user_memories:
        parts.append("## What I remember about you")
        for mem in user_memories:
            parts.append(f"- {mem}")
    if corrections:
        parts.append("## Known corrections")
        parts.append("These corrections come from users and override document content:")
        for corr in corrections:
            parts.append(f"- {corr}")
    if quality_insights:
        parts.append("## Quality insights")
        for insight in quality_insights:
            parts.append(f"- {insight}")
    return "\n".join(parts) if parts else None


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
                    if any(ds.filename.startswith(f"{pid}/") for pid in project_ids)
                ]
            return doc_sources
    return []


_CANT_ANSWER_PATTERNS = re.compile(
    r"(?:I (?:could not|couldn't|cannot|can't|don't|do not) find|"
    r"(?:no|not enough) (?:relevant |available )?(?:information|documents?|context|data)|"
    r"not (?:available|found|present) in (?:the|my) (?:available |retrieved )?(?:context|documents?|sources?))",
    re.IGNORECASE,
)


def needs_new_search(answer: str) -> bool:
    """Check if the LLM response indicates it needs more information."""
    if "[NEED_SEARCH]" in answer:
        return True
    # Safety net: catch "I don't know" style responses that should have been [NEED_SEARCH]
    stripped = answer.strip()
    if len(stripped) < 200 and _CANT_ANSWER_PATTERNS.search(stripped):
        return True
    return False


def build_context_from_document_sources(
    doc_sources: list[DocumentSource],
    max_tokens: int = MAX_CONTEXT_TOKENS,
) -> str:
    """Build context string from DocumentSource objects (previous sources)."""
    enc = tiktoken.get_encoding(ENCODING_NAME)
    parts: list[str] = []
    total_tokens = 0

    for ds in doc_sources:
        # Extract project name from scoped filename (format: project_id/filename)
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
) -> list[dict[str, str]]:
    """Build chat messages for the context-first attempt.

    Uses CONTEXT_FIRST_SYSTEM_PROMPT which includes the [NEED_SEARCH] instruction,
    so the LLM can signal when it needs fresh search results.
    """
    messages: list[dict[str, str]] = []
    messages.append({"role": "system", "content": CONTEXT_FIRST_SYSTEM_PROMPT})

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
        # Extract project name from scoped filename (format: project_id/filename)
        fname_parts = src.filename.split("/", 1)
        if len(fname_parts) == 2:
            display_name = f"{fname_parts[1]} (Project: {fname_parts[0]})"
        else:
            display_name = src.filename

        if reference_numbers and src.document_id in reference_numbers:
            ref_num = reference_numbers[src.document_id]
            header = f"[Reference {ref_num}: {display_name}]"
        else:
            header = f"[Source: {display_name}, chunk {src.chunk_index}]"
        block = f"{header}\n{src.text}\n"
        block_tokens = len(enc.encode(block))

        if total_tokens + block_tokens > max_tokens:
            break
        parts.append(block)
        total_tokens += block_tokens

    context = "\n".join(parts)
    context += "\n\n---\nThe above is ALL available information. Do NOT add details beyond what is shown."
    return context


def build_conversational_messages(
    query: str,
    sources: list[Source],
    history: list[ConversationMessage],
    reference_numbers: dict[int, int] | None = None,
    memory_context: str | None = None,
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
    messages.append({"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT})

    # 2. Memory context
    if memory_context:
        messages.append({"role": "system", "content": memory_context})

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
    analysis: QueryAnalysis | None = None
    """Intent classifier output, passed through to retention for gating decisions."""


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
) -> RAGContext:
    """Run intent routing, search, and context building.

    Returns a RAGContext. When ``precomputed_answer`` is set the caller should
    use it directly (RESPOND / SUMMARY paths, or context-first hit).  Otherwise
    the caller should generate from ``llm_messages`` (streaming or not).
    """
    existing_messages = list(conversation.messages)
    has_history = len(existing_messages) > 0

    # 0. Recall memories from Hindsight (non-blocking, empty when disabled)
    user_memories: list[str] = []
    quality_insights: list[str] = []
    corrections: list[str] = []
    project_memories: list[str] = []
    if user_id:
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
                analysis=analysis,
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
            )

    # QA — try context-first, search only if needed
    previous_doc_sources = get_previous_sources(existing_messages, project_ids=project_ids)

    if previous_doc_sources and precompute:
        # Try answering from previous context + history (non-streaming only)
        # Skipped when precompute=False (streaming) to avoid blocking non-streaming call
        logger.info("Trying to answer from previous context (no search)")
        context = build_context_from_document_sources(previous_doc_sources)
        llm_messages = build_conversational_messages_with_context(
            query, context, existing_messages,
            memory_context=memory_context,
        )
        answer = await generate_answer(llm_messages)

        if not needs_new_search(answer):
            # LLM answered from context — done, no search needed
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=previous_doc_sources,
                precomputed_answer=answer,
                analysis=analysis,
            )
        else:
            # LLM needs more info — fall through to search
            logger.info("[NEED_SEARCH] detected — running semantic search")
            previous_doc_sources = []  # clear so we enter search below
    elif previous_doc_sources and not precompute:
        # Streaming path: run context-first check (same as non-streaming)
        logger.info("Streaming mode — running context-first check")
        context = build_context_from_document_sources(previous_doc_sources)
        llm_messages = build_conversational_messages_with_context(
            query, context, existing_messages,
            memory_context=memory_context,
        )
        answer = await generate_answer(llm_messages)
        if not needs_new_search(answer):
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=previous_doc_sources,
                precomputed_answer=answer,
                analysis=analysis,
            )
        else:
            logger.info("[NEED_SEARCH] detected in streaming — running search")
            previous_doc_sources = []

    if not previous_doc_sources:
        # Search path (first message, or LLM requested search)
        if settings.query_expansion_enabled:
            sources = await search_multi_query(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
                project_ids=project_ids,
            )
        elif settings.reranker_enabled:
            sources = await search_and_rerank(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
                project_ids=project_ids,
            )
        elif settings.hybrid_search_enabled:
            sources = await search_similar_hybrid(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
                project_ids=project_ids,
            )
        else:
            sources = await search_similar(
                client=qdrant,
                query=analysis.cleaned_query,
                user_groups=user_groups,
                top_k=top_k,
                threshold=threshold,
                project_ids=project_ids,
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
                memory_context=memory_context,
            )
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=document_sources,
                precomputed_answer=None,
                analysis=analysis,
            )
        else:
            # No results — let LLM respond helpfully
            history = truncate_history(existing_messages) if has_history else None
            llm_messages = build_no_results_messages(query, history=history, memory_context=memory_context, system_knowledge=system_knowledge)
            if precompute:
                answer = await generate_answer(llm_messages)
            else:
                answer = None
            return RAGContext(
                llm_messages=llm_messages,
                document_sources=[],
                precomputed_answer=answer,
                analysis=analysis,
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

    ctx = await _prepare_rag_context(
        conversation, query, user_groups, qdrant, top_k, threshold,
        user_id=user_id, project_ids=project_ids, db=db,
    )

    if ctx.precomputed_answer is not None:
        answer = ctx.precomputed_answer
    else:
        answer = await generate_answer(ctx.llm_messages, images=images)

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Persist token usage
    usage = get_last_usage()
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
    answer = answer.replace("[NEED_SEARCH]", "").strip()

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
    )
    db.add(assistant_msg)

    # Auto-generate title if this is the first message pair
    existing_messages = list(conversation.messages)
    if len(existing_messages) == 0 and not conversation.title:
        conversation.title = query[:50] + "..." if len(query) > 50 else query

    # Flush to get the ID assigned
    await db.flush()

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
            intent="qa",
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
        user_id=user_id, precompute=False, project_ids=project_ids, db=db,
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
        async for token in generate_answer_stream(ctx.llm_messages, images=images):
            accumulated += token
            yield ("token", json.dumps(token))

    # Safety: strip any [NEED_SEARCH] marker
    accumulated = accumulated.replace("[NEED_SEARCH]", "").strip()

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Emit usage event (token counts + cost)
    usage = get_last_usage()
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
    )
    db.add(assistant_msg)
    await db.flush()

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
            intent="qa",
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

    yield ("done", json.dumps({"message_id": assistant_msg.id, "latency_ms": latency_ms}))
