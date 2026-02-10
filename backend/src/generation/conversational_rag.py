"""Conversational RAG pipeline with session-based memory."""
from __future__ import annotations

import logging
import re
import time

import tiktoken
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Conversation, ConversationMessage
from src.generation.intent_classifier import Action, analyze_query
from src.generation.llm_client import generate_answer
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

# Pronouns that might refer to previous context
PRONOUN_PATTERNS = [
    r"\bit\b",
    r"\bthis\b",
    r"\bthat\b",
    r"\bthese\b",
    r"\bthose\b",
    r"\bthey\b",
    r"\bthem\b",
]


def expand_query(query: str, messages: list[ConversationMessage]) -> str:
    """Expand query with context from recent messages if it contains pronouns.

    If the query contains pronouns like 'it', 'that', etc., prepend the last
    few user messages to help the retrieval understand the context.
    """
    if not messages:
        return query

    # Check if query contains pronouns
    query_lower = query.lower()
    has_pronoun = any(re.search(pattern, query_lower) for pattern in PRONOUN_PATTERNS)

    if not has_pronoun:
        return query

    # Get the last 2 user messages for context
    recent_user_messages = [
        m.content for m in messages if m.role == "user"
    ][-2:]

    if not recent_user_messages:
        return query

    # Prepend context
    context_prefix = " ".join(recent_user_messages)
    expanded = f"Context: {context_prefix}. Current question: {query}"
    logger.debug(f"Expanded query: {expanded}")
    return expanded


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
    """Run the conversational RAG pipeline.

    1. Expand query using conversation context (handle pronouns)
    2. Retrieve fresh sources (always re-retrieve, topics may change)
    3. Build prompt with history + context
    4. Generate answer
    5. Save both user message and assistant response
    6. Auto-generate title if first message

    Returns the assistant's response as a MessageResponse.
    """
    start = time.perf_counter()

    # Get existing messages (already loaded if lazy="selectin" or explicitly loaded)
    existing_messages = list(conversation.messages)
    has_history = len(existing_messages) > 0

    # 1. Route query via LLM
    analysis = await analyze_query(query)
    sources: list[Source] = []

    if analysis.action == Action.RESPOND:
        # Chitchat — skip search, respond via unified no-results prompt
        history = truncate_history(existing_messages) if has_history else None
        messages = build_no_results_messages(query, history=history)
        answer = await generate_answer(messages)
    else:
        # SEARCH — run search pipeline with cleaned query
        # 2. Expand query using conversation context
        search_query = expand_query(analysis.cleaned_query, existing_messages)

        # 3. Retrieve fresh sources
        sources = await search_similar(
            client=qdrant,
            query=search_query,
            user_groups=user_groups,
            top_k=top_k,
            threshold=threshold,
        )

        # 4. Build prompt with history + context + current query
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
            messages = build_conversational_messages(
                query, sources, existing_messages, reference_numbers=ref_map,
            )
            answer = await generate_answer(messages)
        else:
            # No results — let LLM respond helpfully
            history = truncate_history(existing_messages) if has_history else None
            messages = build_no_results_messages(query, history=history)
            answer = await generate_answer(messages)

    latency_ms = int((time.perf_counter() - start) * 1000)

    # Build document-level sources (empty for chitchat/no-results)
    if not sources:
        document_sources = []

    # 4. Save user message
    user_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=query,
    )
    db.add(user_msg)

    # 5. Save assistant message
    sources_data = [s.model_dump() for s in document_sources] if document_sources else None
    assistant_msg = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sources=sources_data,
        latency_ms=latency_ms,
    )
    db.add(assistant_msg)

    # 6. Auto-generate title if this is the first message pair
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
