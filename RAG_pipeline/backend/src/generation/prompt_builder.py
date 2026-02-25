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

NO_RESULTS_PROMPT = (
    "You are a friendly, conversational knowledge-base assistant for a company. "
    "No relevant documents were found for the user's message. "
    "Respond naturally and helpfully:\n"
    "- If the user is greeting you or making small talk, respond warmly and offer to help them find information in the knowledge base.\n"
    "- If the user asked a question, let them know you couldn't find relevant documentation and suggest they rephrase or be more specific.\n"
    "- If the user is thanking you, acknowledge warmly.\n"
    "NEVER make up information or answer from general knowledge. "
    "Keep your response concise (1-3 sentences). "
    "Do NOT mention 'sources', 'documents', or 'search results' — just respond naturally."
)


def build_messages(
    query: str,
    sources: list[Source],
    reference_numbers: dict[int, int] | None = None,
) -> list[dict[str, str]]:
    """Build the chat messages list: system prompt + context + user query."""
    context = _build_context(sources, reference_numbers=reference_numbers)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
    ]



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


def build_memory_context(memories: list[str]) -> str:
    """Format recalled user memories into a system message block."""
    if not memories:
        return ""
    lines = "\n".join(f"- {m}" for m in memories[:10])
    return (
        "## What you remember about this user\n"
        f"{lines}\n\n"
        "Use these memories to personalize your response where relevant, "
        "but do not mention them explicitly unless the user asks."
    )


def build_no_results_messages(
    query: str,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Build LLM messages for when search returns no results."""
    messages: list[dict[str, str]] = [{"role": "system", "content": NO_RESULTS_PROMPT}]

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": query})
    return messages
