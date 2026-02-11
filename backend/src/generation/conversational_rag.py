"""Conversational RAG pipeline with session-based memory."""
from __future__ import annotations

import logging
import time

import tiktoken
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, ConversationMessage
from src.generation.intent_classifier import Action, QueryType, analyze_query
from src.generation.llm_client import generate_answer
from src.generation.map_reduce import resolve_document_id, run_map_reduce
from src.generation.prompt_builder import build_no_results_messages
from src.retrieval.search import merge_sources_by_document, search_similar
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
) -> list[dict[str, str]]:
    """Build chat messages for the context-first attempt.

    Uses CONTEXT_FIRST_SYSTEM_PROMPT which includes the [NEED_SEARCH] instruction,
    so the LLM can signal when it needs fresh search results.
    """
    messages: list[dict[str, str]] = []
    messages.append({"role": "system", "content": CONTEXT_FIRST_SYSTEM_PROMPT})

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
) -> list[dict[str, str]]:
    """Build chat messages with system prompt, context, history, and current query.

    Message structure:
    1. System prompt
    2. Context from retrieved sources (as system message)
    3. Truncated conversation history (user/assistant messages)
    4. Current user query
    """
    messages: list[dict[str, str]] = []

    # 1. System prompt
    messages.append({"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT})

    # 2. Context from sources
    if sources:
        context = build_context(sources, reference_numbers=reference_numbers)
        messages.append({
            "role": "system",
            "content": f"Retrieved context:\n{context}",
        })

    # 3. Truncated history
    history_messages = truncate_history(history)
    messages.extend(history_messages)

    # 4. Current query
    messages.append({"role": "user", "content": query})

    return messages


async def run_conversational_rag(
    conversation: Conversation,
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    top_k: int = 3,
    threshold: float = 0.4
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

    # Get existing messages (already loaded if lazy="selectin" or explicitly loaded)
    existing_messages = list(conversation.messages)
    has_history = len(existing_messages) > 0

    # 1. Route query via LLM (with conversation context for pronoun resolution)
    conversation_context = None
    if has_history:
        recent = existing_messages[-4:]  # last 2 exchanges
        conversation_context = "\n".join(
            f"{m.role}: {m.content}" for m in recent
        )
    analysis = await analyze_query(query, conversation_context=conversation_context)
    document_sources: list[DocumentSource] = []

    if analysis.action == Action.RESPOND:
        # Chitchat — skip search, respond via unified no-results prompt
        history = truncate_history(existing_messages) if has_history else None
        messages = build_no_results_messages(query, history=history)
        answer = await generate_answer(messages)
    elif analysis.query_type == QueryType.SUMMARY:
        # SUMMARY — map-reduce pipeline for full-document summarization
        logger.info("Summary query detected in conversation — resolving document")
        resolved = await resolve_document_id(qdrant, analysis.cleaned_query, user_groups)
        if resolved:
            doc_id, filename = resolved
            answer, doc_source = await run_map_reduce(qdrant, doc_id, filename, user_groups)
            document_sources = [doc_source]
        else:
            logger.info("Could not resolve document for summary — falling back to no-results")
            history = truncate_history(existing_messages) if has_history else None
            messages = build_no_results_messages(query, history=history)
            answer = await generate_answer(messages)
    else:
        # QA — try context-first, search only if needed
        previous_doc_sources = get_previous_sources(existing_messages)

        if previous_doc_sources:
            # Try answering from previous context + history
            logger.info("Trying to answer from previous context (no search)")
            context = build_context_from_document_sources(previous_doc_sources)
            llm_messages = build_conversational_messages_with_context(
                query, context, existing_messages
            )
            answer = await generate_answer(llm_messages)

            if not needs_new_search(answer):
                # LLM answered from context — done, no search needed
                document_sources = previous_doc_sources
            else:
                # LLM needs more info — fall through to search
                logger.info("[NEED_SEARCH] detected — running semantic search")
                previous_doc_sources = []  # clear so we enter search below

        if not previous_doc_sources:
            # Search path (first message, or LLM requested search)
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
                )
                answer = await generate_answer(llm_messages)
            else:
                # No results — let LLM respond helpfully
                history = truncate_history(existing_messages) if has_history else None
                llm_messages = build_no_results_messages(query, history=history)
                answer = await generate_answer(llm_messages)

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
    if len(existing_messages) == 0 and not conversation.title:
        conversation.title = query[:50] + "..." if len(query) > 50 else query

    # Flush to get the ID assigned
    await db.flush()

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
