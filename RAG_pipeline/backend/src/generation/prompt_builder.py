from __future__ import annotations

import logging

import tiktoken
from src.schemas import Source

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 6144
ENCODING_NAME = "cl100k_base"

SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "Answer the user's question based ONLY on the provided context. "
    "If the context does not contain enough information, say so clearly.\n\n"
    "## Formatting rules\n"
    "- Use **markdown** in your answers: headings, bullet lists, bold, code blocks where appropriate.\n"
    "- Cite sources using numbered references like [1], [2], etc. Only cite a source if you actually use information from it.\n"
    "- Do NOT cite sources for basic questions unrelated to the knowledge base.\n\n"
    "## Example\n"
    "User: What is the onboarding process?\n\n"
    "Assistant: The onboarding process involves several steps:\n\n"
    "1. **HR orientation** on the first day [1]\n"
    "2. **Team introduction** and workspace setup [1]\n"
    "3. **Technical onboarding** with IT department [2]\n\n"
    "User: What is the reason to live?\n\n"
    "Assistant: I'm sorry, but the provided context does not contain information "
    "regarding the philosophical reason to live. My knowledge base is limited to "
    "company documentation."
)

METADATA_SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "You have access to structured system data (projects, documents, statistics) "
    "and optionally retrieved document content.\n\n"
    "Answer using the provided system data. If document content is also provided, "
    "incorporate it. Use markdown: tables for listings, bullet lists, bold for emphasis.\n"
    "Do NOT make up information beyond what is provided."
)

NO_RESULTS_PROMPT = (
    "You are a friendly, conversational knowledge-base assistant for a company. "
    "No relevant documents were found for the user's message.\n\n"
    "If you have been provided memory context about the user (in a follow-up system message), "
    "use it to answer personal or contextual questions — memory context is trusted information, not general knowledge.\n\n"
    "Otherwise, respond naturally and helpfully:\n"
    "- If the user is greeting you or making small talk, respond warmly and offer to help them find information in the knowledge base.\n"
    "- If the user asked a question, let them know you couldn't find relevant documentation and suggest they rephrase or be more specific.\n"
    "- If the user is thanking you, acknowledge warmly.\n"
    "NEVER make up information or answer from general knowledge. "
    "Keep your response concise (1-3 sentences). "
    "Do NOT mention 'sources', 'documents', or 'search results' — just respond naturally."
)


CORRECTION_EXTRACTION_PROMPT = (
    "The user corrected the assistant's previous answer. "
    "Extract a concise correction statement.\n\n"
    "Previous assistant answer:\n{previous_answer}\n\n"
    "User's correction:\n{user_message}\n\n"
    "Respond with ONLY a single concise sentence describing the correct information. "
    "Format: '<topic> is/has/does <correct info>, not <wrong info>'. "
    "Do not include any other text."
)


def build_correction_extraction_messages(
    user_message: str,
    previous_answer: str,
) -> list[dict[str, str]]:
    """Build messages for extracting a structured correction from user input."""
    return [
        {
            "role": "system",
            "content": "You extract factual corrections from conversations. "
            "Respond with only the correction statement, nothing else.",
        },
        {
            "role": "user",
            "content": CORRECTION_EXTRACTION_PROMPT.format(
                previous_answer=previous_answer[:500],
                user_message=user_message,
            ),
        },
    ]


def build_messages(
    query: str,
    sources: list[Source],
    reference_numbers: dict[int, int] | None = None,
    memory_context: str | None = None,
    system_knowledge: str | None = None,
) -> list[dict[str, str]]:
    """Build the chat messages list: system prompt + knowledge + memory context + context + user query."""
    context = _build_context(sources, reference_numbers=reference_numbers)
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if system_knowledge:
        messages.append({"role": "system", "content": f"System knowledge: {system_knowledge}"})
    if memory_context:
        messages.append({"role": "system", "content": memory_context})
    messages.append({"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"})
    return messages



def _build_context(
    sources: list[Source],
    reference_numbers: dict[int, int] | None = None,
) -> str:
    """Concatenate source texts, truncating per-source to stay within MAX_CONTEXT_TOKENS.

    Instead of dropping entire sources when the budget is exceeded, each source
    that would overflow is truncated to fit the remaining budget (with ``[...]``
    appended).  This ensures all retrieved sources are represented in the context.
    """
    enc = tiktoken.get_encoding(ENCODING_NAME)
    parts: list[str] = []
    total_tokens = 0
    truncated_sources = 0

    for src in sources:
        if reference_numbers and src.document_id in reference_numbers:
            ref_num = reference_numbers[src.document_id]
            header = f"[Reference {ref_num}: {src.filename}]"
        else:
            header = f"[Source: {src.filename}, chunk {src.chunk_index}]"

        block = f"{header}\n{src.text}\n"
        block_tokens = len(enc.encode(block))
        remaining = MAX_CONTEXT_TOKENS - total_tokens

        if remaining <= 0:
            break

        if block_tokens <= remaining:
            parts.append(block)
            total_tokens += block_tokens
        else:
            # Truncate this source's text to fit the remaining budget
            header_line = f"{header}\n"
            header_tokens = len(enc.encode(header_line))
            suffix = " [...]"
            suffix_tokens = len(enc.encode(suffix))
            text_budget = remaining - header_tokens - suffix_tokens
            if text_budget > 0:
                text_tokens = enc.encode(src.text)
                truncated_text = enc.decode(text_tokens[:text_budget])
                block = f"{header_line}{truncated_text}{suffix}\n"
                parts.append(block)
                total_tokens += len(enc.encode(block))
                truncated_sources += 1

    logger.info(
        f"Context: {len(parts)} sources used ({total_tokens}/{MAX_CONTEXT_TOKENS} tokens), "
        f"{truncated_sources} truncated, {len(sources) - len(parts)} dropped"
    )
    return "\n".join(parts)


def build_no_results_messages(
    query: str,
    history: list[dict[str, str]] | None = None,
    memory_context: str | None = None,
    system_knowledge: str | None = None,
) -> list[dict[str, str]]:
    """Build LLM messages for when search returns no results."""
    messages: list[dict[str, str]] = [{"role": "system", "content": NO_RESULTS_PROMPT}]

    if system_knowledge:
        messages.append({"role": "system", "content": f"System knowledge: {system_knowledge}"})

    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": query})
    return messages


def build_metadata_messages(
    query: str,
    metadata_context: str,
    sources: list[Source] | None = None,
    reference_numbers: dict[int, int] | None = None,
    memory_context: str | None = None,
) -> list[dict[str, str]]:
    """Build LLM messages for metadata queries (projects, documents, stats).

    Always includes metadata_context. Optionally includes vector search sources
    for hybrid queries that need both structured data and document content.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": METADATA_SYSTEM_PROMPT}]

    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    # Build user content with metadata + optional document context
    parts = [f"System data:\n{metadata_context}"]

    if sources:
        doc_context = _build_context(sources, reference_numbers=reference_numbers)
        parts.append(f"\nRetrieved document content:\n{doc_context}")

    parts.append(f"\nQuestion: {query}")
    messages.append({"role": "user", "content": "\n".join(parts)})

    return messages
