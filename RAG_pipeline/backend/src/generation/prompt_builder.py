from __future__ import annotations

import logging

import tiktoken
from src.schemas import Source

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 16000
ENCODING_NAME = "cl100k_base"

SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "Answer the user's question based ONLY on the provided context.\n\n"
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
    "- Do NOT cite sources for greetings or small talk."
)

CODE_ASSISTANT_SYSTEM_PROMPT = (
    "You are Nexus Code, a practical coding assistant for company project documentation. "
    "Answer the user's question based ONLY on the provided indexed project documentation.\n\n"
    "## CRITICAL RULES\n"
    "1. Use only the retrieved context, system data, and trusted memory/corrections. "
    "Never invent files, functions, endpoints, SQL, configs, or implementation details that are not present.\n"
    "2. Prefer concrete engineering evidence: filenames, modules, jobs, components, configs, schemas, "
    "chunk references, data flows, and relationships between parts.\n"
    "3. For debugging or failure-analysis questions, structure the answer with: "
    "**Finding**, **Evidence**, **Likely Cause**, **Fix**, and **Verification**. "
    "Skip sections only when the documents do not support them.\n"
    "4. For architecture/how-it-works questions, explain the flow step by step and call out boundaries, "
    "inputs, outputs, and operational impact when the context supports it.\n"
    "5. If the indexed docs are incomplete, say exactly what is missing and suggest using Deep Dive "
    "to inspect the codebase directly. Do not fill the gap with general knowledge.\n"
    "6. Every factual claim must cite a source reference [N].\n\n"
    "## Formatting rules\n"
    "- Use concise markdown with headings, bullets, and code blocks when helpful.\n"
    "- Cite sources with [1], [2], etc. Only cite sources you actually use.\n"
    "- Keep recommendations actionable and verification-oriented."
)

METADATA_SYSTEM_PROMPT = (
    "You are a helpful knowledge-base assistant for a company. "
    "You have access to structured system data (projects, documents, statistics) "
    "and optionally retrieved document content.\n\n"
    "When document content is provided, use it as your PRIMARY source — "
    "it contains the detailed analysis, scores, and findings. "
    "Supplement with system data for structural details (status, timestamps, counts).\n"
    "When only system data is available, answer from that.\n"
    "Use markdown: tables for listings, bullet lists, bold for emphasis.\n"
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

CODE_NO_RESULTS_PROMPT = (
    "You are Nexus Code, a practical coding assistant for company project documentation. "
    "No relevant indexed project documentation was found for the user's message.\n\n"
    "Respond briefly and directly:\n"
    "- Say that the indexed docs do not contain enough evidence to answer safely.\n"
    "- Suggest switching to Deep Dive if the user wants direct codebase inspection.\n"
    "- Do not answer from general knowledge or invent implementation details."
)


CONVERSATION_SUMMARY_PROMPT = (
    "You are a helpful knowledge-base assistant. "
    "The user wants a summary of the conversation so far. "
    "Review the history and provide a clear, well-structured summary.\n\n"
    "## Formatting rules\n"
    "- Use markdown: headings, bullet lists, bold where appropriate.\n"
    "- Organize by topic/theme, not chronologically.\n"
    "- Highlight key findings, answers, and unresolved questions.\n"
    "- Be comprehensive but concise.\n"
    "- Do NOT add information not discussed in the conversation."
)


def build_conversation_summary_messages(
    query: str,
    history: list[dict[str, str]],
    memory_context: str | None = None,
) -> list[dict[str, str]]:
    """Build LLM messages for summarizing the conversation history."""
    messages: list[dict[str, str]] = [{"role": "system", "content": CONVERSATION_SUMMARY_PROMPT}]

    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": query})
    return messages


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
    answer_style: str = "assistant",
) -> list[dict[str, str]]:
    """Build the chat messages list: system prompt + knowledge + memory context + context + user query."""
    context = _build_context(sources, reference_numbers=reference_numbers)
    system_prompt = CODE_ASSISTANT_SYSTEM_PROMPT if answer_style == "code" else SYSTEM_PROMPT
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
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
    context = "\n".join(parts)
    context += "\n\n---\nThe above is ALL available information. Do NOT add details beyond what is shown."
    return context


def build_no_results_messages(
    query: str,
    history: list[dict[str, str]] | None = None,
    memory_context: str | None = None,
    system_knowledge: str | None = None,
    answer_style: str = "assistant",
) -> list[dict[str, str]]:
    """Build LLM messages for when search returns no results."""
    prompt = CODE_NO_RESULTS_PROMPT if answer_style == "code" else NO_RESULTS_PROMPT
    messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]

    if system_knowledge:
        messages.append({"role": "system", "content": f"System knowledge: {system_knowledge}"})

    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    if history:
        messages.extend(history)

    messages.append({"role": "user", "content": query})
    return messages


_CODE_QUERY_TERMS = (
    "architecture", "data flow", "flow", "debug", "bug", "error", "exception",
    "failure", "fix", "implement", "implementation", "function", "class",
    "module", "endpoint", "api", "config", "configuration", "sql", "query",
    "schema", "table", "job", "component", "talend", "auth", "authentication",
    "permission", "stream", "route", "service", "pipeline",
)


def should_use_code_assistant(query: str, project_ids: list[str] | None = None) -> bool:
    """Heuristic for selecting the Nexus Code answer contract.

    This keeps the public API stable: project-scoped engineering questions get
    code-assistant formatting, while general knowledge-base questions keep the
    normal assistant persona.
    """
    lowered = query.lower()
    if any(term in lowered for term in _CODE_QUERY_TERMS):
        return True
    if project_ids and any(token in lowered for token in ("how", "why", "where", "what changes", "which files")):
        return True
    return False


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
