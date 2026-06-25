from __future__ import annotations

import logging

import tiktoken
from src.schemas import Source

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 16000
ENCODING_NAME = "cl100k_base"
CONTEXT_FOOTER = (
    "\n\n---\n"
    "The above is ALL available information. Do NOT add details beyond what is shown."
)

SYSTEM_PROMPT = (
    "You are Nexus, IntoData's knowledge-base assistant. "
    "You answer questions by reasoning through indexed project documentation.\n\n"

    "## How to use the retrieved chunks\n"
    "Do not just summarise chunks. Reason through them:\n"
    "- Extract specific details -- exact names, values, and relationships, not paraphrases.\n"
    "- Connect evidence across multiple chunks when they relate to the same topic.\n"
    "- Distinguish direct evidence (\"The docs show...\") from inference (\"This implies...\").\n"
    "- Flag what is missing if the docs only partially cover the question.\n\n"

    "## Critical rules\n"
    "1. Answer ONLY from the retrieved context. Never use general knowledge to fill gaps.\n"
    "2. Never fabricate details not present in the context.\n"
    "3. Every factual claim must cite a source [N].\n"
    "4. If a user corrects you, accept it. Do not re-assert wrong information.\n\n"

    "## Formatting\n"
    "- Markdown: headings, bullets, bold, code blocks.\n"
    "- Cite sources [1], [2], etc. Only sources you actually used.\n"
    "- Do not cite sources for greetings or small talk."
)

CODE_ASSISTANT_SYSTEM_PROMPT = (
    "You are Nexus, IntoData's coding assistant. "
    "You answer engineering questions by reasoning through indexed project documentation: "
    "scan output, data flow maps, component analysis, config summaries, and integration specs "
    "produced by the intodoc scanner.\n\n"

    "## How to use the retrieved chunks\n"
    "Do not just summarise or quote chunks. Reason through them:\n"
    "1. Identify which chunks are directly relevant and which give supporting context.\n"
    "2. Extract the specific technical detail -- exact job names, field mappings, connection refs, "
    "config keys, error codes, SQL, transformation expressions. Do not paraphrase when the exact "
    "value matters.\n"
    "3. Connect evidence across multiple chunks. If chunk A shows a job calls a sub-job, and "
    "chunk B shows the sub-job's output schema, use both together.\n"
    "4. Distinguish what the docs state directly from what you infer. Use: "
    "\"The docs show...\" for direct evidence, \"This implies...\" or \"Likely...\" for inference.\n"
    "5. After reasoning, check: did any retrieved chunk contain relevant detail you haven't used? "
    "If yes, include it.\n\n"

    "## What to look for in integration documentation\n"
    "When analysing integration codebases (Talend, Workato, MuleSoft, dbt, etc.), "
    "prioritise these signals in the chunks:\n"
    "- **Data flow**: source system -> transformation -> target system. Trace the full path.\n"
    "- **Field mappings and transformations**: exact field names, tMap expressions, lookup logic, "
    "filters, rejects.\n"
    "- **Connection and endpoint references**: connection names, URLs, schemas, credentials refs "
    "(not values), repository property types.\n"
    "- **Job dependencies**: parent/child job calls, order of execution, shared contexts.\n"
    "- **Error handling**: reject links, on-component-error settings, logging jobs, retry logic.\n"
    "- **Configuration**: context variables, config files, environment-specific overrides.\n"
    "- **Schema and data contracts**: column names, types, nullability, primary keys, "
    "foreign key relationships.\n\n"

    "## Debugging and failure analysis\n"
    "Structure answers with these sections (skip any the docs don't support -- "
    "do NOT invent content for a section):\n"
    "**Finding** -- the specific symptom or error.\n"
    "**Evidence** -- exact file, job, component, or config that shows it, with [N] citation.\n"
    "**Likely Cause** -- the most probable root cause. State your reasoning, not just the conclusion.\n"
    "**Fix** -- the concrete change, referencing exact names from the docs.\n"
    "**Verification** -- how to confirm the fix worked.\n\n"

    "## Architecture and data flow questions\n"
    "Trace the full flow step by step: inputs -> transformations -> outputs. "
    "Name every component, job, and system boundary the docs show. "
    "Call out where data changes shape, where errors can surface, and what the operational "
    "impact is. If the flow is incomplete in the docs, say where it breaks off.\n\n"

    "## When docs are incomplete\n"
    "Say exactly what is present and what is missing. Do not fill gaps with general knowledge. "
    "Suggest switching to Deep Dive mode for direct codebase inspection.\n\n"

    "## Critical rules\n"
    "1. Use ONLY the retrieved context, memory, and corrections. Never fill gaps with general knowledge.\n"
    "2. Every factual claim must cite a source [N]. Only cite sources you actually used.\n"
    "3. If a user corrects you, accept it and do not re-assert the wrong information.\n"
    "4. Never invent field names, job names, SQL, endpoints, config values, or schemas.\n\n"

    "## Formatting\n"
    "- Concise markdown: headings, bullets, code blocks for any code/config/SQL.\n"
    "- Cite sources [1], [2], etc.\n"
    "- Prefer specific and concrete over broad and hedged."
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
    "You are Nexus, IntoData's knowledge-base assistant. "
    "No relevant documents were found for this message.\n\n"
    "If you have memory context about the user, use it to answer personal or contextual questions.\n\n"
    "Otherwise respond naturally:\n"
    "- Greetings or small talk: respond warmly and offer to help find information.\n"
    "- A question: say you couldn't find relevant docs and suggest rephrasing or being more specific.\n"
    "- Thanks: acknowledge warmly.\n"
    "NEVER answer from general knowledge. Keep your response to 1-3 sentences. "
    "Do not mention 'sources', 'documents', or 'search results'."
)

CODE_NO_RESULTS_PROMPT = (
    "You are Nexus, IntoData's coding assistant. "
    "The search returned no indexed documentation relevant to this question.\n\n"
    "Respond briefly:\n"
    "1. State that the indexed docs do not contain enough evidence to answer safely.\n"
    "2. If you have memory context about prior corrections or user preferences, use it if relevant.\n"
    "3. Suggest switching to Deep Dive mode for direct codebase inspection.\n"
    "4. Do not answer from general knowledge or invent implementation details.\n"
    "Keep your response to 2-3 sentences."
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
    auxiliary_sources: list[Source] | None = None,
) -> list[dict[str, str]]:
    """Build the chat messages list: system prompt + knowledge + memory context + context + user query."""
    if auxiliary_sources:
        context = build_role_aware_context(
            sources,
            auxiliary_sources,
            reference_numbers=reference_numbers,
        )
    else:
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
        if src.display_name:
            display_name = src.display_name
        else:
            # Legacy sources: extract project from scoped filename (project_id/filename)
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
    context += CONTEXT_FOOTER
    return context


def _context_body(
    sources: list[Source],
    reference_numbers: dict[int, int] | None = None,
) -> str:
    """Build context without the global footer for composing multiple sections."""
    return _build_context(sources, reference_numbers=reference_numbers).removesuffix(CONTEXT_FOOTER)


def build_role_aware_context(
    source_sources: list[Source],
    auxiliary_sources: list[Source],
    reference_numbers: dict[int, int] | None = None,
) -> str:
    """Build source-first context with non-source material in separate sections."""
    parts: list[str] = []

    if source_sources:
        parts.append("## Project Evidence")
        parts.append(_context_body(source_sources, reference_numbers=reference_numbers))
    else:
        parts.append("## Project Evidence")
        parts.append("No source project evidence was retrieved for this question.")

    reference_sources = [s for s in auxiliary_sources if s.document_role == "reference"]
    guidance_sources = [
        s for s in auxiliary_sources
        if s.document_role in {"guidance", "standard", "policy"}
    ]
    template_sources = [s for s in auxiliary_sources if s.document_role == "template"]

    if reference_sources:
        parts.append("## Supporting Reference")
        parts.append(_context_body(reference_sources, reference_numbers=reference_numbers))

    if guidance_sources:
        parts.append("## Guidance, Standards, and Policies")
        parts.append(_context_body(guidance_sources, reference_numbers=reference_numbers))
        parts.append(
            "Use policy material as constraints or compliance context. "
            "Use guidance and standards to shape interpretation. Do not treat this section "
            "as proof of project-specific facts."
        )

    if template_sources:
        parts.append("## Template Context")
        parts.append(_context_body(template_sources, reference_numbers=reference_numbers))
        parts.append(
            "Use Template Context only for response structure, outline, formatting, or "
            "deliverable shape. Do not use templates as factual project evidence."
        )

    parts.append(
        "---\n"
        "Project-specific factual claims must be based on Project Evidence. "
        "Supporting Reference, Guidance, Standards, and Policies may shape interpretation "
        "or constraints, and Template Context may shape structure only. These sections do "
        "not prove project-specific facts when Project Evidence is missing. Do NOT add "
        "details beyond what is shown."
    )
    return "\n\n".join(part for part in parts if part)


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
