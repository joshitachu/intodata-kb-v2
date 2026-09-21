"""Read-only evidence capability execution for the Nexus harness."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final, NamedTuple

from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models.document import Document
from src.db.models.document_text import (
    DOCUMENT_TEXT_SOURCE_RECONSTRUCTED,
    DocumentTextRow,
)
from src.harness.models import (
    EVIDENCE_SUMMARY_MAX_CHARS,
    CONVERSATION_SUMMARY_FACTS_CAPABILITY,
    DOCUMENT_FULL_TEXT_CAPABILITY,
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENTATION_ANSWER_CAPABILITY,
    GENERATED_DOCUMENT_READ_CAPABILITY,
    LOG_SEARCH_EVIDENCE_CAPABILITY,
    METADATA_QUERY_CAPABILITY,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
)
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.providers.mcp_docs import McpDocumentationProvider, McpDocumentationProviderError
from src.harness.trusted_answer import (
    AnswerDeltaSink,
    TrustedAnswerSink,
    TrustedRagAnswer,
)
from src.generation.rag_chain import _execute_search, run_rag_query
from src.generation.llm_client import get_documentation_provider_trace
from src.schemas import QueryRequest
from src.retrieval.search import search_and_rerank, search_similar
from src.retrieval.topology_hints import (
    TopologyRetrievalHints,
    resolve_topology_retrieval_hints,
)
from src.retrieval.metadata_retriever import (
    get_document_list,
    get_document_stats,
    get_project_detail,
    get_project_list,
    get_system_overview,
    get_user_profile,
)
from src.runtime.providers import RuntimeProviderContext, RuntimeProviderError
from src.runtime.providers.stored import StoredRuntimeProvider
from src.runtime.registry import SearchLogsInput
from src.runtime.insights.parser import redact_credentials
from src.runtime.models import (
    DetectFailuresInput,
    ListExecutionsInput,
    ListRuntimeHealthInput,
    ListRuntimeInventoryInput,
    ListRuntimeSyncStatusInput,
    RuntimeExecutionState,
)


logger = logging.getLogger(__name__)


#: The document roles `search_documentation` actually searches.
#:
#: Named rather than inlined at the call site because it is now also reported
#: back to the caller in ``scope``. It is the single most surprising property of
#: this capability: the model asks it to "search the documentation" and it
#: searches the SOURCE corpus only, so a policy, a standard or a template is
#: invisible to it. An empty result has therefore never meant "the project does
#: not say this", and nothing in the result used to say so.
DOCUMENTATION_SEARCH_ROLES: tuple[str, ...] = ("source",)


def _search_scope(
    *,
    project_ids: list[str] | None,
    documents_in_scope: int | None,
    returned: int,
    top_k: int,
) -> dict[str, Any]:
    """Describe what a similarity search covered, in the model's own terms.

    Includes ``searched_what`` as prose rather than leaving the caller to
    assemble it from the keys. The consumer is a language model reading a JSON
    blob in a tool result, and a sentence it can quote back to itself when it
    writes "I did not find X" is worth more than three fields it has to
    recombine correctly under pressure.

    ``exhaustive`` is the field that matters and is always ``False`` here.
    Similarity retrieval returns the best ``top_k``, never all matches, so no
    result from this path can support "there is no such thing".
    """

    scope: dict[str, Any] = {
        "projects": list(project_ids) if project_ids is not None else "every authorized project",
        "document_roles": list(DOCUMENTATION_SEARCH_ROLES),
        "retrieval": "similarity",
        "exhaustive": False,
        "results_returned": returned,
        "results_requested": top_k,
    }
    if documents_in_scope is not None:
        scope["documents_in_scope"] = documents_in_scope
    where = (
        f"{documents_in_scope} document(s)"
        if documents_in_scope is not None
        else "the documents"
    )
    projects = ", ".join(project_ids) if project_ids else "every authorized project"
    scope["searched_what"] = (
        f"Top-{top_k} similarity match over {where} with role "
        f"{'/'.join(DOCUMENTATION_SEARCH_ROLES)} in {projects}. Documents with any "
        "other role -- policies, standards, templates, guidance -- were NOT searched, "
        "and similarity returns the best matches rather than all of them. An empty "
        "result means this search did not find it, not that the estate does not "
        "contain it."
    )
    return scope


async def _documents_in_scope(
    db: AsyncSession | None,
    *,
    project_ids: list[str] | None,
    document_roles: tuple[str, ...],
) -> int | None:
    """How many documents the search could possibly have matched.

    Returned as part of ``scope`` so an empty result is interpretable: "nothing
    matched across the 37 source documents in this project" is a usable
    statement, "nothing matched" on its own is the one that got published to a
    client as a fact about their estate.

    ``None`` means the count is unknown -- no session, or the count failed --
    and is deliberately distinguishable from ``0``. Never raises: this is a
    label on a result that has already been produced, and losing the label must
    not lose the result.
    """

    if db is None:
        return None
    statement = select(func.count(Document.id)).where(
        Document.deleted_at.is_(None),
        Document.document_role.in_(list(document_roles)),
    )
    if project_ids is not None:
        statement = statement.where(Document.project_id.in_(project_ids))
    try:
        return int((await db.execute(statement)).scalar_one())
    except SQLAlchemyError:
        logger.warning(
            "Could not count documents in search scope for projects=%s roles=%s",
            project_ids,
            document_roles,
            exc_info=True,
        )
        return None


async def search_document_evidence(
    *,
    qdrant: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    project_ids: list[str] | None,
    top_k: int = 5,
    threshold: float | None = None,
    db: AsyncSession | None = None,
) -> EvidenceQueryResult:
    """Search indexed generated/RAG docs through the existing retriever.

    ``db`` is optional and used only to count the documents the search covered.
    Optional because the count is a label on the answer, not part of it: a
    caller without a session still gets the same evidence, with ``scope``
    reporting the filters and omitting the count.
    """
    min_score = settings.default_threshold if threshold is None else threshold
    # Use the same operator-configured retrieval selector as the established
    # /query RAG path: query expansion, reranking, hybrid retrieval, or dense
    # retrieval. The conversational capability is an agent boundary around the
    # existing RAG pipeline, not a second reduced search implementation.
    sources = await _execute_search(
        query,
        user_groups,
        qdrant,
        top_k,
        min_score,
        None,
        project_ids,
        document_roles=list(DOCUMENTATION_SEARCH_ROLES),
    )

    return EvidenceQueryResult(
        capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
        status="succeeded",
        scope=_search_scope(
            project_ids=project_ids,
            documents_in_scope=await _documents_in_scope(
                db, project_ids=project_ids, document_roles=DOCUMENTATION_SEARCH_ROLES
            ),
            returned=min(len(sources), top_k),
            top_k=top_k,
        ),
        items=[
            EvidenceItem(
                id=f"doc:{source.document_id}:{source.chunk_index}",
                capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
                source_type="rag_document",
                title=source.display_name or source.filename,
                summary=_preview(source.text),
                uri=source.artifact_path or source.filename,
                score=source.score,
                metadata={
                    "document_id": source.document_id,
                    "chunk_index": source.chunk_index,
                    "filename": source.filename,
                    "source_layer": source.source_layer,
                    "project_id": source.project_id,
                },
            )
            for source in sources[:top_k]
        ],
    )


async def answer_document_question_evidence(
    *,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    query: str,
    user_groups: list[str],
    user_id: int,
    project_ids: list[str] | None,
    top_k: int = 8,
    trusted_answer_sink: TrustedAnswerSink,
    answer_delta_sink: AnswerDeltaSink | None = None,
    topology_hints: TopologyRetrievalHints | None = None,
) -> EvidenceQueryResult:
    """Answer one documentation question through the existing deterministic RAG pipeline.

    Delegates to ``run_rag_query`` -- the same retrieval-and-generation path used
    by the explicit ``/query`` REST endpoint -- so the answer and its sources are
    produced without any model self-reported citation. The answer is additionally
    handed to ``trusted_answer_sink`` so the read-only turn loop can adopt it
    directly as the turn's final output, bypassing the grounding-validation step
    for this capability only.

    ``answer_delta_sink`` streams that answer as it is generated. This is the only
    capability allowed to do so, and for the same reason the validator is skipped:
    the answer is already deterministically cited, so nothing downstream rewrites
    it and the streamed text is byte-identical to the persisted one. The equality
    is asserted below rather than assumed.
    """
    request = QueryRequest(query=query, top_k=top_k)
    streaming = answer_delta_sink is not None and answer_delta_sink.enabled
    try:
        response = await run_rag_query(
            request,
            user_groups=user_groups,
            user_id=user_id,
            qdrant=qdrant,
            db=db,
            project_ids=project_ids,
            on_delta=answer_delta_sink.emit if streaming else None,
            topology_hints=topology_hints,
        )
    finally:
        # This sink is request-scoped. Capturing in ``finally`` preserves the
        # exhausted-provider audit even when no trusted answer was produced.
        trusted_answer_sink.documentation_provider_trace = (
            get_documentation_provider_trace()
        )
    if streaming:
        assert answer_delta_sink is not None
        if answer_delta_sink.streamed != response.answer:
            # Defensive: the streamed text is the concatenation of the same chunks
            # the answer is built from, so this cannot happen without a provider
            # or batching bug. Log loudly -- the authoritative replayable deltas
            # emitted after persistence will correct the display either way.
            logger.error(
                "Streamed answer diverged from the persisted answer "
                "(streamed %d chars, answer %d chars)",
                len(answer_delta_sink.streamed),
                len(response.answer),
            )
    trusted_answer_sink.answer = TrustedRagAnswer(
        content=response.answer, document_sources=response.sources
    )
    return EvidenceQueryResult(
        capability_id=DOCUMENTATION_ANSWER_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"doc:{source.document_id}:{'-'.join(str(i) for i in source.chunk_indexes)}",
                capability_id=DOCUMENTATION_ANSWER_CAPABILITY.id,
                source_type="rag_document",
                title=source.display_name or source.filename,
                summary=_preview(source.text),
                uri=source.artifact_path or source.filename,
                score=source.score,
                metadata={
                    "document_id": source.document_id,
                    "filename": source.filename,
                    "source_layer": source.source_layer,
                    "project_id": source.project_id,
                },
            )
            for source in response.sources[:top_k]
        ],
        answer_draft=response.answer[:8_000],
    )


async def search_within_document_evidence(
    *,
    qdrant: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    project_id: str,
    document_id: int,
    max_chunks: int = 8,
) -> EvidenceQueryResult:
    """Similarity-search the chunks of one authorized document.

    Renamed from ``read_document_evidence``, and the rename is the fix rather
    than a tidy-up: this has never read a document. It embeds the query,
    searches that document's chunks and returns the top ``max_chunks`` by score.
    A model told it had "read the document" will answer completeness questions
    from eight ranked fragments, which is how a 31-row mapping table was
    reported to a client as 30 fields.

    The existing retriever owns embedding, hybrid/vector fallback, reranking,
    document-group visibility, and project filtering. Both document and project
    filters are mandatory so a model-supplied document ID cannot widen scope.
    """

    if settings.reranker_enabled:
        sources = await search_and_rerank(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=max_chunks,
            threshold=0.0,
            document_ids=[document_id],
            project_ids=[project_id],
        )
    else:
        sources = await search_similar(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            top_k=max_chunks,
            threshold=0.0,
            document_ids=[document_id],
            project_ids=[project_id],
        )
    return EvidenceQueryResult(
        capability_id=DOCUMENT_READ_CAPABILITY.id,
        status="succeeded",
        scope={
            "projects": [project_id],
            "document_id": document_id,
            "retrieval": "similarity",
            "exhaustive": False,
            "results_returned": min(len(sources), max_chunks),
            "results_requested": max_chunks,
            "searched_what": (
                f"Top-{max_chunks} similarity match over the chunks of document "
                f"{document_id}. This is a ranked search inside the document, not a "
                "read of it: the parts that did not match are not here and nothing "
                "here says how much was left out. For a count, a complete list, or "
                "anything that must not miss an entry, use read_document."
            ),
        },
        items=[
            EvidenceItem(
                id=f"doc:{source.document_id}:{source.chunk_index}",
                capability_id=DOCUMENT_READ_CAPABILITY.id,
                source_type="rag_document_passage",
                title=source.display_name or source.filename,
                summary=_preview(source.text, limit=2_000),
                uri=source.artifact_path or source.filename,
                score=source.score,
                metadata={
                    "project_id": source.project_id or project_id,
                    "document_id": source.document_id,
                    "chunk_index": source.chunk_index,
                    "filename": source.filename,
                    "source_layer": source.source_layer,
                },
            )
            for source in sources[:max_chunks]
        ],
    )


# --- Whole-document reads ---------------------------------------------------
#
# Everything below serves one capability: `rag.docs.read_document_text`, the
# only path that reads a document in DOCUMENT order rather than in relevance
# order. Kept together and kept pure where it can be -- the splitting is a
# string operation and is unit-testable without a database.


#: Headings h1-h6. Wider than the chunker's `HEADING_RE`, which stops at h4
#: because deeper headings make poor embedding units. An outline has the
#: opposite concern: it should show the structure the author actually wrote, and
#: a document whose detail lives at h5 would otherwise look like one flat block.
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t]*#*$")

#: Ceiling on one evidence item's ``summary``.
#:
#: The serializer in :mod:`src.harness.capability_handlers` truncates at
#: ``EVIDENCE_SUMMARY_MAX_CHARS`` with no marker, so text handed over in larger
#: pieces than this would be silently cut -- which is the whole failure mode
#: this capability exists to remove. Splitting deliberately below the limit
#: leaves room for the part header each piece carries.
_TEXT_PART_MAX_CHARS: Final = EVIDENCE_SUMMARY_MAX_CHARS - 200

#: Ceiling on one ``mode="full"`` read, in characters. Roughly 5k tokens.
#:
#: Not a limit on what may be READ -- every section of a larger document is
#: still reachable one at a time -- but on what may be read in a single
#: unconsidered call. Above it the call returns the outline and says why, so the
#: model spends its next call on the part it actually needs instead of a quarter
#: of its context window on a document it will then have to summarize anyway.
_FULL_READ_MAX_CHARS: Final = 20_000

#: Ceiling on one batched ``sections`` read, in characters. Roughly 15k tokens.
#:
#: Three times the ``full`` limit, which only looks inconsistent. The two limits
#: price different things. ``full`` is an unconsidered whole-document dump, and
#: holding it low is what forces the cheap outline-then-choose shape. A named set
#: of sections has already been chosen against an outline, so the expensive part
#: -- deciding what is worth reading -- has happened.
#:
#: Pricing a chosen set low does not save the tokens, it fragments them. In the
#: run that motivated this, the agent answered one integration question by
#: reading sixteen sections of a 32-section document across sixteen calls,
#: roughly 63k characters in total. The per-call limit bought nothing on bytes
#: and cost fifteen extra round trips, each one re-sending the whole
#: conversation: cache reads were 933,504 tokens for a 34-call run against
#: 170,368 for a 19-call run of the same question. Call count, not bytes read,
#: dominates the spend, so the budget is set where a considered read of a real
#: document fits in one call.
#: The ceiling is on the SECTION TEXT, but what has to fit is the serialized
#: evidence payload, and the envelope is not free. Measured against document
#: 1137 (the one the motivating run read), the JSON wrapper -- per-item
#: metadata, ids, scope -- inflates section text by about 1.55x:
#:
#:     16 sections   26,671 chars of text   40,291 serialized
#:     22 sections   33,224 chars of text   51,477 serialized
#:
#: OpenCode truncates a tool result above roughly 50k and spills it to a file,
#: telling the model to go and grep it. That is observably worse than not
#: batching at all: a 22-section call came back as 272 characters and a file
#: path, and recovering from it cost four further calls. A 47,820-byte payload
#: went through intact, so the inline limit sits between those two.
#:
#: 28,000 characters of text is therefore about 43k serialized -- under the
#: largest payload measured to survive, with room for the envelope to grow. The
#: number is deliberately derived from the transport, not from what a document
#: happens to need: exceeding it names the omitted sections so the model asks
#: for the rest in a second batch, which is two calls rather than the sixteen
#: this capability exists to prevent.
_SECTIONS_READ_MAX_CHARS: Final = 28_000

#: How many outline rows to render before collapsing the rest into a count.
_OUTLINE_MAX_SECTIONS: Final = 150

#: Characters per token, for the size signal only. The same crude ratio
#: :func:`src.harness.guidance.models.estimate_tokens` uses; it is here to tell
#: a model "this costs about 30k tokens", not to bill anyone.
_CHARS_PER_TOKEN: Final = 4

#: How many documents an ambiguous ``filename`` lookup names before it stops.
#:
#: The candidate list exists so the next call can be exact, and a handful of ids
#: does that as well as two hundred would. Bounded because the corpus shape is
#: not ours to assume: one real project already stores eleven documents whose
#: last path segment is ``01_ARCHITECTURE_MAP.md``, one per scanned sub-project,
#: and a corpus foldered per customer could produce an order of magnitude more.
_FILENAME_CANDIDATE_LIMIT: Final = 20


class DocumentSection(NamedTuple):
    """One heading-delimited span of a document, in document order."""

    #: Full heading path, e.g. ``"Interface contract > Field mapping"``. Empty
    #: string for content that precedes the first heading.
    path: str
    #: The deepest heading's own text, which is what a model will name.
    heading: str
    #: 1-6, or 0 for content before the first heading.
    level: int
    #: The section verbatim, heading line included.
    text: str


def split_document_sections(text: str) -> list[DocumentSection]:
    """Split a document into heading-delimited sections, in document order.

    Pure, and lossless in the sense that matters: concatenating every
    ``section.text`` reproduces the input exactly. That property is what lets
    ``mode="section"`` and ``mode="full"`` be the same read at two granularities
    rather than two implementations that can disagree.

    A document with no headings at all returns one section covering it. That is
    the honest answer -- a flat text has one section -- and it keeps every
    caller on one code path instead of branching on "is this markdown".
    """

    if not text:
        return []

    lines = text.splitlines(keepends=True)
    sections: list[DocumentSection] = []
    # Heading text by level, so a level-3 heading can name its level-2 parent.
    # Deeper entries are dropped when a shallower heading arrives, which is what
    # keeps the path from carrying a sibling's children.
    ancestors: dict[int, str] = {}
    current: list[str] = []
    current_meta: tuple[str, str, int] = ("", "", 0)

    def flush() -> None:
        if not current:
            return
        path, heading, level = current_meta
        sections.append(
            DocumentSection(path=path, heading=heading, level=level, text="".join(current))
        )

    for line in lines:
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if match is None:
            current.append(line)
            continue
        flush()
        level = len(match.group(1))
        heading = match.group(2).strip()
        for deeper in [key for key in ancestors if key >= level]:
            del ancestors[deeper]
        ancestors[level] = heading
        path = " > ".join(ancestors[key] for key in sorted(ancestors))
        current = [line]
        current_meta = (path, heading, level)

    flush()
    return sections


def _split_text_parts(text: str, limit: int = _TEXT_PART_MAX_CHARS) -> list[str]:
    """Split text into pieces no larger than ``limit``, on line boundaries.

    Line boundaries, not character boundaries, because the documents this is
    used on are full of tables and lists: cutting mid-row turns one field into
    two half-fields and reintroduces the miscount the capability exists to
    prevent. A single line longer than the limit is still hard-split -- there is
    nothing better to do with it -- and that is the only case where a piece can
    end mid-token.
    """

    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    buffer = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if buffer:
                parts.append(buffer)
                buffer = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(buffer) + len(line) > limit:
            parts.append(buffer)
            buffer = ""
        buffer += line
    if buffer:
        parts.append(buffer)
    return parts


def _estimated_tokens(char_count: int) -> int:
    return char_count // _CHARS_PER_TOKEN


def _outline_text(sections: list[DocumentSection]) -> str:
    """Render the outline a model chooses a section from."""

    if not sections:
        return "(the document is empty)"
    lines = ["Section outline (heading -- characters):"]
    for index, section in enumerate(sections[:_OUTLINE_MAX_SECTIONS], start=1):
        label = section.path or "(text before the first heading)"
        indent = "  " * max(0, section.level - 1)
        lines.append(f"{indent}- [s{index}] {label} -- {len(section.text)} chars")
    remaining = len(sections) - _OUTLINE_MAX_SECTIONS
    if remaining > 0:
        lines.append(f"- ... and {remaining} further section(s), not listed here.")
    return "\n".join(lines)


def _text_items(
    *,
    capability_id: str,
    document_id: int,
    project_id: str,
    filename: str,
    label: str,
    body: str,
    item_key: str | None = None,
) -> list[EvidenceItem]:
    """One item per part, each naming which part of what it is.

    The part header is not decoration. A model handed six items with no ordering
    information will read them in whatever order the serializer produced, which
    for a procedure or a numbered mapping is exactly as wrong as reading ranked
    chunks.

    ``item_key`` disambiguates the identifiers when one result carries several
    bodies. A batched section read calls this once per section, and without a key
    every section's first part would be ``doctext:{document_id}:1``. It is
    optional so that the single-body callers keep the identifiers they already
    emit, which are the ones any stored trace refers to.
    """

    parts = _split_text_parts(body)
    total = len(parts)
    items: list[EvidenceItem] = []
    for index, part in enumerate(parts, start=1):
        header = (
            f"{label} (part {index} of {total}, verbatim, in document order)\n\n"
            if total > 1
            else f"{label} (verbatim, in document order)\n\n"
        )
        metadata: dict[str, Any] = {
            "project_id": project_id,
            "document_id": document_id,
            "filename": filename,
            "section": label,
            "part": index,
            "part_count": total,
            "char_count": len(part),
        }
        if item_key is not None:
            metadata["section_id"] = item_key
        items.append(
            EvidenceItem(
                id=(
                    f"doctext:{document_id}:{index}"
                    if item_key is None
                    else f"doctext:{document_id}:{item_key}:{index}"
                ),
                capability_id=capability_id,
                source_type="rag_document_text",
                title=filename,
                summary=header + part,
                uri=f"/projects/{project_id}/documents/{document_id}",
                metadata=metadata,
            )
        )
    return items


def _match_section(sections: list[DocumentSection], wanted: str) -> int | None:
    """Resolve a model-supplied section name to its position in the outline.

    Three attempts, widening: the full path, the deepest heading on its own,
    then the tail of a path. A model that saw ``A > B > C`` in the outline will
    variously ask for ``A > B > C``, for ``C``, and for ``B > C``, and refusing
    two of those costs a call to learn a spelling rule rather than to read
    anything.

    The position is returned rather than the section because position is what
    every caller needs next: the outline id ``sN`` is this index plus one, and a
    batched read sorts its matches back into document order. Recovering the
    position afterwards with ``list.index`` would resolve by equality, so two
    sections that happen to carry identical text -- a repeated boilerplate
    heading, say -- would collapse onto the first one's number.
    """

    needle = " ".join(wanted.split()).casefold()
    if not needle:
        return None
    if re.fullmatch(r"s[1-9]\d*", needle):
        index = int(needle[1:]) - 1
        return index if index < len(sections) else None
    for index, section in enumerate(sections):
        if section.path.casefold() == needle:
            return index
    for index, section in enumerate(sections):
        if section.heading.casefold() == needle:
            return index
    for index, section in enumerate(sections):
        if section.path.casefold().endswith(needle):
            return index
    return None


def _section_label(sections: list[DocumentSection], index: int) -> str:
    """Name one section the way the outline named it, id included.

    One spelling for every place a section is referred to -- the outline of what
    exists, the list of what was read, the header on the text itself -- so that
    a model can copy an identifier straight back into its next request.
    """

    return f"s{index + 1}: {sections[index].path or '(text before the first heading)'}"


def _available_section_labels(sections: list[DocumentSection]) -> list[str]:
    """The sections a failed lookup could have meant, capped like the outline."""

    return [
        _section_label(sections, index)
        for index in range(min(len(sections), _OUTLINE_MAX_SECTIONS))
    ]


class SectionSelection(NamedTuple):
    """The outcome of resolving a batched ``sections`` request."""

    #: Positions to return, deduplicated and in document order.
    indexes: list[int]
    #: Identifiers that matched nothing, in the order they were asked for.
    unmatched: list[str]
    #: Positions that matched but did not fit the budget, in document order.
    over_budget: list[int]
    #: Characters held by the sections named in ``indexes``.
    returned_chars: int


def _select_sections(
    sections: list[DocumentSection],
    wanted: list[str],
    *,
    budget: int = _SECTIONS_READ_MAX_CHARS,
) -> SectionSelection:
    """Resolve a batch of section identifiers and fit it to the budget.

    Pure, so the ordering, deduplication and truncation rules are testable
    without a database -- they are the part of the batched read that can be
    subtly wrong while every individual section still looks right.

    Two rules earn their keep:

    * One bad identifier never sinks the batch. Failing twelve resolved sections
      because a thirteenth was misspelled would push the caller back to reading
      one section per call, which is the cost this whole path exists to remove.
    * Truncation happens between sections, never inside one. A half section is
      exactly the silent fragment that made completeness questions unanswerable,
      so the first match is returned whole even when it alone exceeds the budget
      -- ``mode="section"`` would have handed it over regardless.
    """

    matched: list[int] = []
    unmatched: list[str] = []
    seen: set[int] = set()
    for identifier in wanted:
        index = _match_section(sections, identifier)
        if index is None:
            unmatched.append(identifier)
            continue
        if index in seen:
            continue
        seen.add(index)
        matched.append(index)
    # Document order regardless of the order asked for: a caller reading a
    # procedure or a numbered mapping must not have to reassemble it.
    matched.sort()

    selected: list[int] = []
    over_budget: list[int] = []
    used = 0
    for position, index in enumerate(matched):
        size = len(sections[index].text)
        if selected and used + size > budget:
            over_budget = matched[position:]
            break
        selected.append(index)
        used += size
    return SectionSelection(
        indexes=selected,
        unmatched=unmatched,
        over_budget=over_budget,
        returned_chars=used,
    )


def _batched_sections_result(
    *,
    document_sections: list[DocumentSection],
    wanted: list[str],
    scope: dict[str, Any],
    document_id: int,
    project_id: str,
    filename: str,
) -> EvidenceQueryResult:
    """Return several complete sections of one document in a single result.

    Separated from :func:`read_document_text_evidence` only for length: the
    database read, the authorization and the failure paths are that function's,
    and everything here is a pure projection of an already-loaded document.

    ``scope`` is mutated in place and returned, which is how the surrounding
    function builds every other mode's scope too. The fields it adds are the
    ones the caller needs to decide whether a second call is warranted: what
    came back, what was misspelled, and what did not fit.
    """

    selection = _select_sections(document_sections, wanted)
    # `mode` names the read that actually happened. Reporting the mode argument
    # here would tell the caller "outline" while handing it twelve sections.
    scope["mode"] = "sections"
    scope["requested_sections"] = list(wanted)

    if not selection.indexes:
        # Nothing resolved, so there is nothing to return and no partial result
        # to be honest about. Succeeding with zero items and no explanation is
        # the absence-without-a-reason shape that produced a wrong client-facing
        # answer once already.
        scope["complete"] = False
        scope["sections_not_found"] = list(selection.unmatched)
        scope["available_sections"] = _available_section_labels(document_sections)
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message=(
                f"None of the requested sections exist in {filename}: "
                + ", ".join(repr(item) for item in selection.unmatched)
                + ". The sections that do exist are listed in the scope of this "
                "result; ask again for the ones you need, all in one call."
            ),
            scope=scope,
        )

    read_ids = [f"s{index + 1}" for index in selection.indexes]
    scope["complete"] = not selection.unmatched and not selection.over_budget
    scope["sections_read"] = [
        _section_label(document_sections, index) for index in selection.indexes
    ]
    scope["returned_chars"] = selection.returned_chars
    searched_what = (
        f"{len(selection.indexes)} section(s) of {filename} -- {', '.join(read_ids)} "
        "-- each verbatim, complete and in document order. Nothing inside them was "
        "left out. The rest of the document was not read."
    )
    if selection.unmatched:
        scope["sections_not_found"] = list(selection.unmatched)
        scope["available_sections"] = _available_section_labels(document_sections)
        searched_what += (
            " Not read because no such section exists: "
            + ", ".join(repr(item) for item in selection.unmatched)
            + " -- 'available_sections' lists what the document does contain."
        )
    if selection.over_budget:
        scope["sections_read_limit_chars"] = _SECTIONS_READ_MAX_CHARS
        scope["sections_omitted_for_size"] = [
            _section_label(document_sections, index) for index in selection.over_budget
        ]
        searched_what += (
            f" Stopped at the {_SECTIONS_READ_MAX_CHARS}-character budget for one "
            "call, so these were requested and not read: "
            + ", ".join(f"s{index + 1}" for index in selection.over_budget)
            + ". Ask for all of them together in one further call."
        )
    scope["searched_what"] = searched_what

    items: list[EvidenceItem] = []
    for index in selection.indexes:
        label = _section_label(document_sections, index)
        items.extend(
            _text_items(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                document_id=document_id,
                project_id=project_id,
                filename=filename,
                label=label,
                body=document_sections[index].text,
                item_key=f"s{index + 1}",
            )
        )
    return EvidenceQueryResult(
        capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
        status="succeeded",
        scope=scope,
        items=items,
    )


def _path_segments(name: str) -> list[str]:
    """Split a stored or requested filename into comparable path segments.

    Backslashes fold onto forward slashes because the stored names arrive from
    scanner output and from uploads made on Windows alike, and a caller copying
    a path out of a document reproduces whichever separator that document used.
    Segments are stripped and empty ones dropped, so neither surrounding
    whitespace nor a doubled separator can make two spellings of the same path
    compare unequal.
    """

    return [
        stripped
        for segment in name.replace("\\", "/").split("/")
        if (stripped := segment.strip())
    ]


def _normalized_path(name: str) -> str:
    """One canonical spelling of a path, for equality only."""

    return "/".join(_path_segments(name)).casefold()


def _without_extension(segment: str) -> str:
    """Drop a trailing extension, leaving a name that has none untouched.

    ``.gitignore`` keeps its leading dot: a name that is nothing but an
    extension has no stem, and returning the empty string would make it match
    every other extensionless segment in the project.
    """

    stem, dot, _extension = segment.rpartition(".")
    return stem if dot and stem else segment


def _filename_matches(stored: str, wanted: str) -> bool:
    """Does one stored filename answer to the name a caller used?

    Stored names in this corpus carry a path -- ``VW_FULL_LANDSCAPE/13_VELDINDEX.md``,
    ``werkorder/VQ_MQ2RHM/01_ARCHITECTURE_MAP.md`` -- but a document that refers
    to another document writes the bare name, so the bare name has to resolve.

    The rule is a segment-aligned suffix: every segment the caller supplied must
    equal the corresponding trailing segment of the stored path, with the last
    one comparable either as written or without its extension. Segment-aligned
    rather than a plain string suffix because ``INDEX.md`` must not match
    ``FIELD_INDEX.md`` -- a silent near-miss here hands over a different
    document's mappings with full confidence, which is worse than the dead end
    this replaces. Supplying more than one segment is how a caller narrows a
    name that several sub-projects share.

    Comparison is case-insensitive throughout: the corpus mixes
    ``13_VELDINDEX.md`` with ``01_ARCHITECTURE_MAP.md`` and prose refers to both
    in whatever case the sentence needed.
    """

    stored_segments = [segment.casefold() for segment in _path_segments(stored)]
    wanted_segments = [segment.casefold() for segment in _path_segments(wanted)]
    if not wanted_segments or len(wanted_segments) > len(stored_segments):
        return False
    tail = stored_segments[-len(wanted_segments) :]
    if tail == wanted_segments:
        return True
    return (
        tail[:-1] == wanted_segments[:-1]
        and _without_extension(tail[-1]) == wanted_segments[-1]
    )


def _resolve_filename_candidates(
    documents: Sequence[tuple[int, str]], wanted: str
) -> list[tuple[int, str]]:
    """Every document one name could mean, from the most specific tier only.

    Pure, so the rule that decides which document a model's words open is
    testable without a database -- it is the part of this path that can be
    subtly wrong while every individual read still looks right.

    An exact whole-path match outranks a suffix match rather than competing with
    it. Without that tier, a document stored at ``VW/13_VELDINDEX.md`` becomes
    unreachable by its own full name the moment anything is stored at
    ``archive/VW/13_VELDINDEX.md``, because the full name is also a suffix of
    the other. Ranking is not the same as silently choosing: within a tier,
    several matches still fail as ambiguous.
    """

    target = _normalized_path(wanted)
    exact = [entry for entry in documents if _normalized_path(entry[1]) == target]
    if exact:
        return exact
    return [entry for entry in documents if _filename_matches(entry[1], wanted)]


class ResolvedDocument(NamedTuple):
    """A document identifier, or the failure that explains why there is none."""

    #: Set only when exactly one document answered to the name.
    document_id: int | None
    #: Set whenever ``document_id`` is not: the caller returns it unchanged.
    failure: EvidenceQueryResult | None


async def _resolve_document_by_filename(
    db: AsyncSession, *, project_id: str, filename: str
) -> ResolvedDocument:
    """Find the one document in this project that answers to ``filename``.

    Scoped to ``project_id`` and to rows that are not soft-deleted, exactly as
    the identifier path is. A name is a weaker key than an id, so widening the
    query to compensate would be the one change that turns a convenience into a
    cross-project read.

    The candidate filenames are matched in Python rather than by a SQL ``LIKE``.
    Two reasons, and the first is a correctness one: these filenames are full of
    underscores, which ``LIKE`` treats as a single-character wildcard, so the
    predicate would need hand-escaping and an unescaped ``01_ARCHITECTURE_MAP.md``
    would happily match ``01XARCHITECTURE_MAP.md``. The second is that the rule
    itself -- segment-aligned suffix, optional extension, exact-path precedence
    -- is not one ``LIKE``. The cost is reading one indexed column for one
    project; the largest project in the current corpus holds 268 rows.
    """

    try:
        rows = (
            await db.execute(
                select(Document.id, Document.filename).where(
                    Document.project_id == project_id,
                    Document.deleted_at.is_(None),
                )
            )
        ).all()
    except SQLAlchemyError:
        logger.error(
            "Filename lookup failed for %r in project %s",
            filename,
            project_id,
            exc_info=True,
        )
        return ResolvedDocument(
            document_id=None,
            failure=EvidenceQueryResult(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                status="failed",
                error_message="The document store could not be read for this request.",
            ),
        )

    documents = [(int(row[0]), str(row[1])) for row in rows]
    matches = _resolve_filename_candidates(documents, filename)
    how = (
        "the whole last path segment of every stored filename, case-insensitively, "
        "with or without its extension"
    )

    if not matches:
        return ResolvedDocument(
            document_id=None,
            failure=EvidenceQueryResult(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                status="failed",
                error_message=(
                    f"No document named {filename!r} in project {project_id}. List "
                    "the project's documents to see the names it does hold, or search "
                    "for the content you need."
                ),
                scope={
                    "projects": [project_id],
                    "filename_requested": filename,
                    "documents_searched": len(documents),
                    "searched_what": (
                        f"Matched {filename!r} against {how}, across all "
                        f"{len(documents)} document(s) in project {project_id}. No "
                        "document in THIS project answers to that name. That is a "
                        "statement about this project's document list and nothing "
                        "else: the file may be indexed under a different name, belong "
                        "to another project, or never have been ingested. Do not "
                        "report it as evidence that the content does not exist."
                    ),
                },
            ),
        )

    if len(matches) > 1:
        shown = matches[:_FILENAME_CANDIDATE_LIMIT]
        # One string per candidate, not one object. `_safe_scope_value` in
        # :mod:`src.harness.capability_handlers` carries strings, numbers and
        # lists of those to the model and returns ``None`` for anything else, so
        # a list of ``{"document_id": ..., "filename": ...}`` objects would
        # arrive as an empty list -- the ids silently gone from the one result
        # whose entire purpose is to hand them over.
        candidates = [f"document_id {identifier}: {name}" for identifier, name in shown]
        omitted = len(matches) - len(shown)
        listed = "; ".join(candidates)
        if omitted > 0:
            listed += f"; and {omitted} further match(es) not listed"
        return ResolvedDocument(
            document_id=None,
            failure=EvidenceQueryResult(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                status="failed",
                error_message=(
                    # The pointer to 'candidates' comes before the list itself:
                    # the handler bounds an error message at 500 characters, and
                    # a dozen candidates can exceed that. Scope carries them in
                    # full, so a reader whose message was cut still knows where
                    # the remaining ids are.
                    f"{filename!r} names {len(matches)} documents in project "
                    f"{project_id}, so nothing was read. Call again with the "
                    "'document_id' of the one you want; they are listed in the "
                    f"'candidates' field of this result: {listed}."
                ),
                scope={
                    "projects": [project_id],
                    "filename_requested": filename,
                    "documents_searched": len(documents),
                    "candidates": candidates,
                    "candidates_omitted": omitted,
                    "searched_what": (
                        f"Matched {filename!r} against {how}, across all "
                        f"{len(documents)} document(s) in project {project_id}, and "
                        f"{len(matches)} of them answer to it. Nothing was read and no "
                        "document was picked: these files differ, and returning one of "
                        "them as though the name were unambiguous would present the "
                        "wrong document's content as the answer. 'candidates' carries "
                        "the identifier of each; choose one and read it by id."
                    ),
                },
            ),
        )

    return ResolvedDocument(document_id=matches[0][0], failure=None)


async def read_document_text_evidence(
    *,
    db: AsyncSession,
    project_id: str,
    document_id: int | None = None,
    project_scope: list[str] | None,
    mode: str = "outline",
    section: str | None = None,
    sections: list[str] | None = None,
    filename: str | None = None,
) -> EvidenceQueryResult:
    """Read one authorized document's stored text: outline, sections, or all.

    Authorization is enforced here and not inherited. The executor already
    checks that ``project_id`` is inside the caller's scope, but this reads
    Postgres by primary key rather than Qdrant through an ``allowed_groups``
    filter, so an authorized project id paired with someone else's document id
    would otherwise be a working cross-project read. The query therefore joins
    ``documents`` and requires the document to belong to that project, and the
    scope check is repeated below rather than assumed.

    Every return path carries ``scope``, including the failures. "I could not
    read that document" is only useful to a model if it also says why -- not
    stored, too large, wrong project -- because each of those has a different
    next step and the model has to pick one without asking the user.

    ``sections`` overrides ``mode`` and is the normal way to read more than one
    part of a document. ``section``, the singular, still resolves exactly as it
    did; it is persisted in conversation history and a batched read is not a
    reason to invalidate calls a model already made.

    The document is named by ``document_id`` or by ``filename``, never by both.
    ``filename`` exists because one document referring to another by name was a
    dead end: a measured run was told the field table it needed was in
    ``13_VELDINDEX.md``, had no way to open it, guessed three similarity queries
    and reported a gap that did not exist. Once the name resolves, every mode
    behaves exactly as it does for an identifier -- resolution is addressing,
    not a second way to read.
    """

    if project_scope is not None and project_id not in project_scope:
        # Defence in depth: the executor denies this before it reaches here.
        # Kept because this function is also reachable from tests and from any
        # future caller that builds its own context. First, before the arguments
        # are even inspected, so no unauthorized project can learn anything at
        # all -- including how this tool reacts to what it was asked.
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message="That project is not in the authorized scope for this turn.",
        )

    # Blank is not a name. Folding it to "not supplied" here means a caller that
    # passes an empty string gets the argument error that tells it what to send,
    # rather than a lookup failure for the empty name.
    if filename is not None and not filename.strip():
        filename = None

    if document_id is not None and filename is not None:
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message=(
                "Name the document once: pass 'document_id' or 'filename', not both. "
                "They can resolve to different documents and there is no safe way to "
                "choose between them. Send 'document_id' alone when you have the "
                "identifier."
            ),
            scope={
                "projects": [project_id],
                "searched_what": (
                    "Nothing was read: the request named the document twice, by "
                    "identifier and by filename."
                ),
            },
        )

    if document_id is None and filename is None:
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message=(
                "Name the document to read: pass 'document_id' when you have the "
                "identifier, or 'filename' when another document referred to a file "
                "by name (for example filename='13_VELDINDEX.md'). If you have "
                "neither, list the project's documents first."
            ),
            scope={
                "projects": [project_id],
                "searched_what": (
                    "Nothing was read: the request named no document, by either "
                    "identifier or filename."
                ),
            },
        )

    if filename is not None:
        resolved = await _resolve_document_by_filename(
            db, project_id=project_id, filename=filename
        )
        if resolved.document_id is None:
            # The resolver pairs every missing id with the failure that explains
            # it; the fallback is the type checker's branch, not a real state.
            return resolved.failure or EvidenceQueryResult(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                status="failed",
                error_message=f"{filename!r} could not be resolved to a document.",
            )
        document_id = resolved.document_id

    try:
        row = (
            await db.execute(
                select(
                    Document.filename,
                    Document.document_role,
                    DocumentTextRow.text,
                    DocumentTextRow.char_count,
                    DocumentTextRow.source,
                )
                .outerjoin(DocumentTextRow, DocumentTextRow.document_id == Document.id)
                .where(
                    Document.id == document_id,
                    Document.project_id == project_id,
                    Document.deleted_at.is_(None),
                )
            )
        ).first()
    except SQLAlchemyError:
        logger.error(
            "Stored text lookup failed for document %s in project %s",
            document_id,
            project_id,
            exc_info=True,
        )
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message="The document store could not be read for this request.",
        )

    if row is None:
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message=(
                f"No document {document_id} exists in project {project_id}. List the "
                "project's documents to get the right identifier."
            ),
            scope={
                "projects": [project_id],
                "document_id": document_id,
                "searched_what": (
                    "Looked for one document by identifier inside this project and "
                    "found none. This says nothing about the project's other "
                    "documents."
                ),
            },
        )

    stored_filename, role, text, char_count, text_source = row
    if not text:
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message=(
                f"Document {document_id} ({stored_filename}) is indexed but its text is not "
                "stored, so it cannot be read whole. Search inside it instead, and say "
                "in your answer that you could not read it end to end."
            ),
            scope={
                "projects": [project_id],
                "document_id": document_id,
                "filename": stored_filename,
                "document_role": role,
                "text_stored": False,
                "searched_what": (
                    "This document predates whole-document storage or exceeded the "
                    "storage ceiling. Only its indexed chunks exist, so no complete "
                    "reading of it is available from any tool."
                ),
            },
        )

    # `document_sections` is what the document is made of; `sections` is what the
    # caller asked for. The two are deliberately not the same name.
    document_sections = split_document_sections(text)
    total_chars = int(char_count or len(text))
    scope: dict[str, Any] = {
        "projects": [project_id],
        "document_id": document_id,
        "filename": stored_filename,
        "document_role": role,
        "text_stored": True,
        "text_source": text_source,
        "total_chars": total_chars,
        "estimated_tokens": _estimated_tokens(total_chars),
        "section_count": len(document_sections),
        "mode": mode,
        # The one field that distinguishes this capability from every other
        # documentation read. `complete` is set per mode below.
        "retrieval": "document order",
    }
    if filename is not None:
        # `filename` is still the name the CALLER used -- the stored one is
        # `stored_filename`, and the two are deliberately not one variable: they
        # were, and the scope then reported every read as resolved from a name.
        # Both are published, so a caller that meant a different file sees the
        # difference here rather than in the content.
        scope["resolved_from_filename"] = filename
    if text_source == DOCUMENT_TEXT_SOURCE_RECONSTRUCTED:
        scope["provenance_warning"] = (
            "This text was reassembled from the document's search index because the "
            "original file is no longer held. It is faithful to the content and not "
            "byte-identical to the upload: heading lines and chunk boundaries may "
            "differ. Do not quote it as an exact transcription."
        )

    # `sections` before `mode`: an explicit list of what to read is a more
    # specific request than a mode, and an empty list is not a request at all.
    if sections:
        return _batched_sections_result(
            document_sections=document_sections,
            wanted=sections,
            scope=scope,
            document_id=document_id,
            project_id=project_id,
            filename=stored_filename,
        )

    if mode == "outline":
        scope["complete"] = False
        scope["searched_what"] = (
            f"The section structure of {stored_filename}, not its content. Ask again with "
            "'sections' naming every section that bears on the question, all in one "
            "call (for example sections=['s2','s5','s9']); each one comes back "
            f"complete. mode='full' returns all {total_chars} characters (about "
            f"{_estimated_tokens(total_chars)} tokens) when the document is small "
            "enough."
        )
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="succeeded",
            scope=scope,
            items=_text_items(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                document_id=document_id,
                project_id=project_id,
                filename=stored_filename,
                label="Outline",
                body=_outline_text(document_sections),
            ),
        )

    if mode == "section":
        matched_index = _match_section(document_sections, section or "")
        if matched_index is None:
            scope["complete"] = False
            scope["available_sections"] = _available_section_labels(document_sections)
            return EvidenceQueryResult(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                status="failed",
                error_message=(
                    f"No section named {section!r} in {stored_filename}. The sections that "
                    "do exist are listed in the scope of this result."
                ),
                scope=scope,
            )
        match = document_sections[matched_index]
        scope["complete"] = True
        scope["section"] = match.path or "(text before the first heading)"
        scope["section_id"] = f"s{matched_index + 1}"
        scope["returned_chars"] = len(match.text)
        scope["searched_what"] = (
            f"The whole of section {scope['section']!r} in {stored_filename}, verbatim and "
            "in document order. Nothing inside this section was left out. The rest "
            "of the document was not read. If other sections of this document also "
            "bear on the question, name them all in one 'sections' call rather than "
            "asking for them one by one."
        )
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="succeeded",
            scope=scope,
            items=_text_items(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                document_id=document_id,
                project_id=project_id,
                filename=stored_filename,
                label=scope["section"],
                body=match.text,
            ),
        )

    # mode == "full". The schema restricts the enum, so anything else is a
    # caller that bypassed validation; treating it as "full" would hand over the
    # largest possible payload for an argument nobody checked.
    if mode != "full":
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="failed",
            error_message="mode must be one of: outline, section, full.",
            scope=scope,
        )

    if total_chars > _FULL_READ_MAX_CHARS:
        # This text is the instruction the caller acts on at the exact moment it
        # decides what to do next, so it is written as one. Its predecessor said
        # to read the sections "one at a time", and a measured run did precisely
        # that: sixteen consecutive single-section calls on a 32-section
        # document. The outline is still returned -- choosing is the point -- but
        # the next step it names is one batched call.
        scope["complete"] = False
        scope["full_read_limit_chars"] = _FULL_READ_MAX_CHARS
        scope["searched_what"] = (
            f"{stored_filename} is {total_chars} characters (about "
            f"{_estimated_tokens(total_chars)} tokens), over the {_FULL_READ_MAX_CHARS}"
            "-character limit for a single whole-document read, so the outline is "
            "returned instead. Choose from the outline every section that bears on "
            "the question and ask for all of them in ONE further call, by passing "
            "their outline ids in the 'sections' array -- for example "
            "sections=['s6','s9','s12']. That single call returns each of them "
            f"complete and in document order, up to {_SECTIONS_READ_MAX_CHARS} "
            "characters; anything over that budget is named in the result so you "
            "can ask for the remainder. Do not issue a separate call per section: "
            "it reads the same text, and each extra call re-sends the whole "
            "conversation."
        )
        return EvidenceQueryResult(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            status="succeeded",
            scope=scope,
            items=_text_items(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                document_id=document_id,
                project_id=project_id,
                filename=stored_filename,
                label="Outline",
                body=_outline_text(document_sections),
            ),
        )

    scope["complete"] = True
    scope["returned_chars"] = total_chars
    scope["searched_what"] = (
        f"The entire text of {stored_filename}, verbatim and in document order. Nothing was "
        "omitted, so a count taken from this is a count of the document. Assemble the "
        "parts in order before counting anything."
    )
    return EvidenceQueryResult(
        capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
        status="succeeded",
        scope=scope,
        items=_text_items(
            capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
            document_id=document_id,
            project_id=project_id,
            filename=stored_filename,
            label=f"{stored_filename}, complete",
            body=text,
        ),
    )


async def query_metadata_evidence(
    *,
    db: AsyncSession,
    query_type: str,
    project_scope: list[str] | None,
    user_id: int,
    project_id: str | None = None,
    limit: int = 20,
) -> EvidenceQueryResult:
    """Reuse structured metadata queries selected by an explicit tool argument."""

    if query_type == "my_profile":
        context = await get_user_profile(db, user_id)
    elif query_type == "project_list":
        context = await get_project_list(db, project_scope)
    elif query_type == "project_detail" and project_id:
        context = await get_project_detail(db, project_id, project_scope)
    elif query_type == "document_stats":
        context = await get_document_stats(db, project_scope, project_id)
    elif query_type == "document_list":
        context = await get_document_list(db, project_scope, project_id, limit=limit)
    elif query_type == "system_overview":
        context = await get_system_overview(db, project_scope)
    else:
        return EvidenceQueryResult(
            capability_id=METADATA_QUERY_CAPABILITY.id,
            status="failed",
            error_message="The metadata query requires an authorized project selection.",
        )
    safe_text = _preview(redact_credentials(context.text), limit=8_000)
    return EvidenceQueryResult(
        capability_id=METADATA_QUERY_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"metadata:{context.query_type}:{project_id or 'authorized-scope'}",
                capability_id=METADATA_QUERY_CAPABILITY.id,
                source_type="structured_metadata",
                title=context.source_label,
                summary=safe_text,
                uri=None,
                observed_at=datetime.datetime.now(datetime.UTC),
                metadata={
                    "project_id": project_id,
                    "query_type": context.query_type,
                    "scope": "authorized_project_scope",
                },
            )
        ],
    )


def conversation_summary_facts(
    *,
    history: list[dict[str, Any]],
    conversation_id: int,
    max_messages: int = 20,
) -> EvidenceQueryResult:
    """Return bounded, redacted dialogue facts; synthesis remains in the agent."""

    bounded = history[-min(max_messages, 20) :]
    items = []
    for index, message in enumerate(bounded, start=1):
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        message_id = message.get("message_id") or message.get("id")
        identity = str(message_id) if message_id is not None else f"bounded-{index}"
        items.append(
            EvidenceItem(
                id=f"conversation:{conversation_id}:message:{identity}",
                capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
                source_type="conversation_message",
                title=f"Conversation {role} message",
                summary=_preview(redact_credentials(content), limit=1_000),
                uri=None,
                metadata={
                    "conversation_id": conversation_id,
                    "role": role,
                    "ordinal": index,
                    "message_id": message_id,
                },
            )
        )
    return EvidenceQueryResult(
        capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
        status="succeeded",
        items=items,
    )


def build_registered_read_adapters(
    *,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    user_groups: list[str],
    docs_provider: McpDocumentationProvider,
    conversation_history: list[dict[str, Any]],
    conversation_id: int,
    trusted_answer_sink: TrustedAnswerSink,
    answer_delta_sink: AnswerDeltaSink | None = None,
) -> dict[
    str,
    Callable[[dict[str, Any], CapabilityExecutionContext], Awaitable[EvidenceQueryResult]],
]:
    """Bind mature read services to one authenticated bounded-loop turn."""

    async def search_adapter(arguments, context):  # noqa: ANN001
        project_id = arguments.get("project_id")
        selected = await _resolve_document_project_scope(
            db=db,
            project_reference=str(project_id) if project_id is not None else None,
            authorized_scope=context.project_scope,
        )
        return await search_document_evidence(
            qdrant=qdrant,
            query=str(arguments["query"]),
            user_groups=user_groups,
            project_ids=selected,
            top_k=int(arguments.get("top_k", 5)),
            # Only so the result can report how many documents it covered. An
            # empty result without that number is what got reported to a client
            # as a fact about their estate.
            db=db,
        )

    async def answer_adapter(arguments, context):  # noqa: ANN001
        project_id = arguments.get("project_id")
        selected = await _resolve_document_project_scope(
            db=db,
            project_reference=str(project_id) if project_id is not None else None,
            authorized_scope=context.project_scope,
        )
        # This is addressing, not evidence: MCP resolves names using the request
        # token inside the already-authorized exact project.  A missing token,
        # multi-project scope, or provider failure returns no hints and normal
        # documentation retrieval continues unchanged.
        topology_hints = await resolve_topology_retrieval_hints(
            query=str(arguments["query"]),
            project_ids=selected,
            access_token=context.access_token,
        )
        if topology_hints is not None:
            logger.info(
                "Topology-assisted document addressing project=%s strength=%s "
                "entities=%d jobs=%d source_roots=%d",
                topology_hints.project_id,
                topology_hints.strength.value,
                len(topology_hints.matched_entity_ids),
                len(topology_hints.job_names),
                len(topology_hints.source_roots),
            )
        return await answer_document_question_evidence(
            qdrant=qdrant,
            db=db,
            query=str(arguments["query"]),
            user_groups=user_groups,
            user_id=context.user_id,
            project_ids=selected,
            top_k=int(arguments.get("top_k", 8) or 8),
            trusted_answer_sink=trusted_answer_sink,
            answer_delta_sink=answer_delta_sink,
            topology_hints=topology_hints,
        )

    async def read_adapter(arguments, context):  # noqa: ANN001
        del context
        return await search_within_document_evidence(
            qdrant=qdrant,
            query=str(arguments["query"]),
            user_groups=user_groups,
            project_id=str(arguments["project_id"]),
            document_id=int(arguments["document_id"]),
            max_chunks=int(arguments.get("max_chunks", 8)),
        )

    async def document_text_adapter(arguments, context):  # noqa: ANN001
        raw_sections = arguments.get("sections")
        # The executor validates against the capability schema before this runs,
        # but this adapter is also reachable from callers that assemble their own
        # arguments. Coercing whatever arrived into a list would read the wrong
        # part of the document silently; naming the field costs one call and
        # tells the caller what to fix.
        if raw_sections is not None and (
            not isinstance(raw_sections, list)
            or not all(isinstance(value, str) and value for value in raw_sections)
        ):
            return EvidenceQueryResult(
                capability_id=DOCUMENT_FULL_TEXT_CAPABILITY.id,
                status="failed",
                error_message=(
                    "'sections' must be an array of non-empty section names or "
                    "outline ids, for example ['s6', 's9', 's12']."
                ),
            )
        raw_document_id = arguments.get("document_id")
        raw_filename = arguments.get("filename")
        return await read_document_text_evidence(
            db=db,
            project_id=str(arguments["project_id"]),
            # Absent rather than defaulted: which of the two was supplied is the
            # request, and the reader refuses both and neither with a message
            # that says what to send instead.
            document_id=int(raw_document_id) if raw_document_id is not None else None,
            filename=str(raw_filename) if raw_filename is not None else None,
            # The authorized scope comes from the server-owned context, never
            # from the arguments: the caller names the project it wants, the
            # server decides which projects exist for it.
            project_scope=context.project_scope,
            mode=str(arguments.get("mode") or "outline"),
            section=(
                str(arguments["section"])
                if arguments.get("section") is not None
                else None
            ),
            sections=list(raw_sections) if raw_sections is not None else None,
        )

    async def list_adapter(arguments, context):  # noqa: ANN001
        return await list_generated_doc_evidence(
            provider=docs_provider,
            project_id=str(arguments["project_id"]),
            access_token=context.access_token,
        )

    async def generated_read_adapter(arguments, context):  # noqa: ANN001
        return await read_generated_doc_evidence(
            provider=docs_provider,
            project_id=str(arguments["project_id"]),
            filename=str(arguments["filename"]),
            access_token=context.access_token,
        )

    async def metadata_adapter(arguments, context):  # noqa: ANN001
        return await query_metadata_evidence(
            db=db,
            query_type=str(arguments["query_type"]),
            project_scope=context.project_scope,
            user_id=context.user_id,
            project_id=(
                str(arguments["project_id"])
                if arguments.get("project_id") is not None
                else None
            ),
            limit=int(arguments.get("limit", 20)),
        )

    async def runtime_adapter(arguments, context):  # noqa: ANN001
        return await query_structured_runtime_evidence(
            operation=str(arguments["operation"]),
            project_id=(
                str(arguments["project_id"])
                if arguments.get("project_id") is not None
                else None
            ),
            project_scope=context.project_scope,
            db=db,
            source_key=(
                str(arguments["source_key"])
                if arguments.get("source_key") is not None
                else None
            ),
            job_name=(
                str(arguments["job_name"])
                if arguments.get("job_name") is not None
                else None
            ),
            state=(
                str(arguments["state"])
                if arguments.get("state") is not None
                else None
            ),
            since=_parse_datetime(arguments.get("since")),
            until=_parse_datetime(arguments.get("until")),
            limit=int(arguments.get("limit", 20)),
        )

    async def logs_adapter(arguments, context):  # noqa: ANN001
        return await search_runtime_log_evidence(
            # Absent/None question is the recency read, not an error.
            question=str(arguments.get("question") or ""),
            project_scope=context.project_scope,
            db=db,
            qdrant=qdrant,
            project_id=(
                str(arguments["project_id"])
                if arguments.get("project_id") is not None
                else None
            ),
            project_ids=arguments.get("project_ids"),
            execution_id=(
                str(arguments["execution_id"])
                if arguments.get("execution_id") is not None
                else None
            ),
            since=_parse_datetime(arguments.get("since")),
            until=_parse_datetime(arguments.get("until")),
            limit=int(arguments.get("limit", 10)),
        )

    async def summary_adapter(arguments, context):  # noqa: ANN001
        if context.conversation_id != conversation_id:
            return EvidenceQueryResult(
                capability_id=CONVERSATION_SUMMARY_FACTS_CAPABILITY.id,
                status="failed",
                error_message="Conversation scope does not match the authorized turn.",
            )
        return conversation_summary_facts(
            history=conversation_history,
            conversation_id=conversation_id,
            max_messages=int(arguments.get("max_messages", 20)),
        )

    return {
        DOCUMENT_SEARCH_CAPABILITY.id: search_adapter,
        DOCUMENTATION_ANSWER_CAPABILITY.id: answer_adapter,
        DOCUMENT_READ_CAPABILITY.id: read_adapter,
        DOCUMENT_FULL_TEXT_CAPABILITY.id: document_text_adapter,
        DOCUMENT_LIST_CAPABILITY.id: list_adapter,
        GENERATED_DOCUMENT_READ_CAPABILITY.id: generated_read_adapter,
        METADATA_QUERY_CAPABILITY.id: metadata_adapter,
        STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id: runtime_adapter,
        LOG_SEARCH_EVIDENCE_CAPABILITY.id: logs_adapter,
        CONVERSATION_SUMMARY_FACTS_CAPABILITY.id: summary_adapter,
    }


async def list_generated_doc_evidence(
    *,
    provider: McpDocumentationProvider,
    project_id: str,
    access_token: str | None,
) -> EvidenceQueryResult:
    """List generated docs metadata through MCP."""
    try:
        docs = await provider.list_project_docs(
            project_id=project_id,
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        return EvidenceQueryResult(
            capability_id=DOCUMENT_LIST_CAPABILITY.id,
            status="failed",
            error_message=exc.safe_message,
        )

    return EvidenceQueryResult(
        capability_id=DOCUMENT_LIST_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"mcp-doc:{project_id}:{doc.name}",
                capability_id=DOCUMENT_LIST_CAPABILITY.id,
                source_type="generated_doc",
                title=doc.name,
                summary=f"{doc.type.upper()} documentation file, {doc.size_bytes} bytes.",
                uri=f"/projects/{project_id}/docs/{doc.name}",
                observed_at=doc.modified_at,
                metadata={
                    "project_id": project_id,
                    "type": doc.type,
                    "size_bytes": doc.size_bytes,
                    "has_pdf": doc.has_pdf,
                    "modified_at": doc.modified_at,
                },
            )
            for doc in docs
        ],
    )


async def read_generated_doc_evidence(
    *,
    provider: McpDocumentationProvider,
    project_id: str,
    filename: str,
    access_token: str | None,
) -> EvidenceQueryResult:
    """Read one exact authorized generated Markdown document through MCP."""
    try:
        content = await provider.read_project_doc(
            project_id=project_id,
            filename=filename,
            access_token=access_token,
        )
    except McpDocumentationProviderError as exc:
        return EvidenceQueryResult(
            capability_id=GENERATED_DOCUMENT_READ_CAPABILITY.id,
            status="failed",
            error_message=exc.safe_message,
        )

    uri = f"/projects/{project_id}/docs/{filename}"
    return EvidenceQueryResult(
        capability_id=GENERATED_DOCUMENT_READ_CAPABILITY.id,
        status="succeeded",
        items=[
            EvidenceItem(
                id=f"mcp-doc-content:{project_id}:{filename}",
                capability_id=GENERATED_DOCUMENT_READ_CAPABILITY.id,
                source_type="generated_doc_content",
                title=filename,
                summary=content[:4_000],
                uri=uri,
                metadata={"project_id": project_id, "filename": filename},
            )
        ],
        answer_draft=content[:8_000],
    )


async def query_structured_runtime_evidence(
    *,
    operation: str,
    project_scope: list[str] | None,
    db: AsyncSession,
    project_id: str | None = None,
    source_key: str | None = None,
    job_name: str | None = None,
    state: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    limit: int = 20,
) -> EvidenceQueryResult:
    """Execute one deterministic typed runtime read without a nested agent."""

    selected_scope = _intersect_scope(project_scope, project_id)
    provider = StoredRuntimeProvider()
    provider_context = RuntimeProviderContext(project_scope=selected_scope, db=db)
    try:
        if operation == "list_executions":
            values = await provider.list_executions(
                ListExecutionsInput(
                    job_name=job_name,
                    state=RuntimeExecutionState(state) if state else None,
                    since=since,
                    until=until,
                    limit=limit,
                ),
                provider_context,
            )
        elif operation == "detect_failures":
            values = await provider.detect_failures(
                DetectFailuresInput(since=since, until=until, limit=limit),
                provider_context,
            )
        elif operation == "list_runtime_health":
            values = await provider.list_runtime_health(
                ListRuntimeHealthInput(
                    source_key=source_key,
                    project_id=project_id,
                    limit=limit,
                ),
                provider_context,
            )
        elif operation == "list_runtime_sync_status":
            values = await provider.list_runtime_sync_status(
                ListRuntimeSyncStatusInput(
                    source_key=source_key,
                    project_id=project_id,
                    limit=limit,
                ),
                provider_context,
            )
        elif operation == "list_runtime_sources":
            values = await provider.list_runtime_sources(
                ListRuntimeInventoryInput(
                    source_key=source_key,
                    project_id=project_id,
                    limit=limit,
                ),
                provider_context,
            )
        else:
            return EvidenceQueryResult(
                capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
                status="failed",
                error_message="The requested runtime operation is not available.",
            )
    except (RuntimeProviderError, ValueError) as exc:
        return EvidenceQueryResult(
            capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
            status="failed",
            error_message=str(exc)[:500],
        )
    serialized = [
        value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        for value in values
        if hasattr(value, "model_dump") or isinstance(value, dict)
    ]
    return EvidenceQueryResult(
        capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
        status="succeeded",
        items=_generic_runtime_items(
            operation,
            serialized[:limit],
            project_id,
            capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
        ),
    )


async def search_runtime_log_evidence(
    *,
    question: str,
    project_scope: list[str] | None,
    db: AsyncSession,
    qdrant: AsyncQdrantClient | None = None,
    project_id: str | None = None,
    project_ids: list[str] | None = None,
    execution_id: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    limit: int = 10,
) -> EvidenceQueryResult:
    """Search held logs through the stored provider and normalize safe evidence.

    This function is intentionally only an adapter: harness callers must invoke it
    through ``HarnessCapabilityExecutor``. The stored provider owns repository
    scope and produces credential-redacted snippets; this layer prevents internal
    row identifiers or provider payloads from becoming evidence metadata.
    """
    selected_scope = _intersect_project_scopes(project_scope, project_ids)
    if project_id is not None:
        selected_scope = _intersect_scope(selected_scope, project_id)
    try:
        params = SearchLogsInput(
            question=question,
            project_id=project_id,
            execution_id=execution_id,
            since=since,
            until=until,
            limit=limit,
        )
        rows = await StoredRuntimeProvider().search_logs(
            params,
            RuntimeProviderContext(project_scope=selected_scope, db=db),
        )
    except (RuntimeProviderError, ValueError) as exc:
        return EvidenceQueryResult(
            capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
            status="failed",
            error_message=str(exc)[:500],
        )

    items = [
        _log_search_evidence_item(row, index=index)
        for index, row in enumerate(rows[:limit], start=1)
    ]
    if settings.runtime_semantic_enabled and qdrant is not None:
        from src.runtime.semantic.service import RuntimeSemanticService

        semantic_hits = await RuntimeSemanticService(qdrant).search(
            question,
            project_ids=selected_scope,
            top_k=limit,
        )
        items.extend(
            _semantic_runtime_evidence_item(hit, index=index)
            for index, hit in enumerate(semantic_hits, start=1)
        )
    items = _dedupe_runtime_search_items(items)[:limit]
    return EvidenceQueryResult(
        capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
        status="succeeded",
        items=items,
        answer_draft=_log_search_answer(question, items),
    )


def _semantic_runtime_evidence_item(hit: Any, *, index: int) -> EvidenceItem:
    execution_id = hit.execution_id
    record_ref = execution_id or hit.incident_id or hit.signature_hash or hit.record_id
    return EvidenceItem(
        id=f"runtime-semantic:{hit.source.value}:{hit.record_id}:{index}",
        capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
        source_type=f"runtime_{hit.source.value}",
        title=hit.source.value.replace("_", " ").title(),
        summary=_preview(redact_credentials(hit.text)),
        uri=(
            f"/admin/logs/executions/{execution_id}"
            if execution_id
            else "/admin/logs/incidents"
        ),
        score=hit.score,
        metadata={
            "execution_ref": execution_id,
            "record_ref": record_ref,
            "project_id": hit.project_id,
            "retrieval": "semantic",
        },
    )


def _dedupe_runtime_search_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str]] = set()
    result: list[EvidenceItem] = []
    for item in items:
        key = (item.source_type, str(item.metadata.get("record_ref") or item.id))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _log_search_evidence_item(row: dict[str, Any], *, index: int) -> EvidenceItem:
    execution_id = str(row.get("execution_id") or f"execution-{index}")
    timestamp = row.get("timestamp")
    severity = str(row.get("severity") or "info").lower()
    snippet = _preview(redact_credentials(str(row.get("snippet") or "")))
    job_name = str(row.get("job_name") or "runtime execution")
    return EvidenceItem(
        id=f"runtime-log:{execution_id}:{index}",
        capability_id=LOG_SEARCH_EVIDENCE_CAPABILITY.id,
        source_type="runtime_log_search",
        title=f"Log match in {job_name}",
        summary=snippet,
        uri=f"/admin/logs/executions/{execution_id}",
        observed_at=timestamp,
        metadata={
            "execution_ref": execution_id,
            "project_id": row.get("project_id"),
            "source_key": row.get("source_key"),
            "severity": severity,
            "occurrence_count": row.get("occurrence_count", 1),
        },
    )


def _log_search_answer(question: str, items: list[EvidenceItem]) -> str:
    """Return a bounded question-aware draft grounded in normalized log evidence."""
    if not items:
        return "No matching runtime log evidence was found for the requested scope."
    execution_refs = list(dict.fromkeys(
        str(item.metadata["execution_ref"])
        for item in items
        if item.metadata.get("execution_ref")
    ))
    first = items[0]
    scope = ", ".join(execution_refs[:3]) or "curated runtime insights"
    more = "" if len(execution_refs) <= 3 else f" and {len(execution_refs) - 3} more"
    return (
        f"For the log search question '{_preview(redact_credentials(question), limit=160)}', I found "
        f"{len(items)} matching snippet(s) in execution(s) {scope}{more}. "
        f"Most relevant {first.metadata.get('severity', 'log')} signal: {first.summary}"
    )[:4_000]


def _generic_runtime_items(
    tool_name: str,
    data: Any,
    project_id: str | None,
    *,
    capability_id: str = STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
) -> list[EvidenceItem]:
    """Normalize arbitrary safe runtime-tool output without knowing its intent."""
    if isinstance(data, list):
        values = [item for item in data[:10] if isinstance(item, dict)]
    elif isinstance(data, dict):
        nested = next(
            (
                value
                for value in data.values()
                if isinstance(value, list) and value and isinstance(value[0], dict)
            ),
            None,
        )
        values = nested[:10] if isinstance(nested, list) else [data]
    else:
        values = []
    result = []
    for index, value in enumerate(values, 1):
        fingerprint = hashlib.sha256(
            json.dumps(value, default=str, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        title = next(
            (
                str(value[key])
                for key in (
                    "engine_name",
                    "name",
                    "job_name",
                    "plan_name",
                    "source_key",
                    "execution_id",
                )
                if value.get(key)
            ),
            tool_name.replace("_", " ").title(),
        )
        observed_at = next(
            (
                value.get(key)
                for key in (
                    "last_seen_at",
                    "last_synced_at",
                    "failed_at",
                    "started_at",
                    "ended_at",
                    "updated_at",
                )
                if value.get(key)
            ),
            None,
        )
        record_ref = next(
            (
                str(value[key])
                for key in (
                    "execution_id",
                    "incident_id",
                    "source_key",
                    "job_id",
                    "engine_id",
                    "plan_id",
                    "id",
                )
                if value.get(key) is not None
            ),
            fingerprint,
        )
        result.append(
            EvidenceItem(
                id=(
                    f"runtime:{tool_name}:"
                    f"{value.get('id') or value.get('engine_id') or value.get('execution_id') or fingerprint}"
                ),
                capability_id=capability_id,
                source_type=f"runtime_{tool_name}",
                title=title[:500],
                summary=_runtime_record_summary(value),
                observed_at=observed_at,
                metadata={
                    "project_id": value.get("project_id") or project_id,
                    "tool_name": tool_name,
                    "record_ref": record_ref,
                    "execution_ref": value.get("execution_id"),
                    "source_key": value.get("source_key"),
                },
            )
        )
    return result


def _runtime_record_summary(value: dict[str, Any]) -> str:
    # project_id is included deliberately. Runtime reads are ordered by recency
    # across the whole authorized scope, so an unscoped question returns
    # whichever project failed most recently. Observed live: a question about one
    # estate was answered with a DIFFERENT project's failing jobs, named
    # individually, with nothing indicating they belonged elsewhere. The id was
    # already present in the evidence item's metadata but absent from the prose
    # the model actually reads, so nothing made the attribution unavoidable.
    safe_keys = (
        "project_id",
        "name",
        "engine_name",
        "job_name",
        "plan_name",
        "source_key",
        "status",
        "state",
        "environment_name",
        "workspace_name",
        "version",
        "availability",
        "last_seen_at",
        "last_synced_at",
        "started_at",
        "ended_at",
        "failed_at",
        "failure_rate",
        "current_failure_streak",
        "error_summary",
    )
    safe = {key: value[key] for key in safe_keys if value.get(key) is not None}
    return _preview(json.dumps(safe, default=str, sort_keys=True), limit=1000)


def _within_time_window(
    items: list[EvidenceItem],
    *,
    since: datetime.datetime | None,
    until: datetime.datetime | None,
) -> list[EvidenceItem]:
    if since is None and until is None:
        return items
    result: list[EvidenceItem] = []
    for item in items:
        observed = _parse_datetime(item.observed_at)
        if observed is None:
            continue
        if since is not None and observed < _as_utc(since):
            continue
        if until is not None and observed >= _as_utc(until):
            continue
        result.append(item)
    return result


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _runtime_health_summary(item: dict[str, Any]) -> str:
    parts = []
    if item.get("failure_rate") is not None:
        parts.append(f"failure rate {item['failure_rate']}")
    if item.get("current_failure_streak") is not None:
        parts.append(f"failure streak {item['current_failure_streak']}")
    if item.get("last_failure_at"):
        parts.append(f"last failure {item['last_failure_at']}")
    return ", ".join(parts) if parts else "Runtime health signal."


def _intersect_scope(scope: list[str] | None, project_id: str | None) -> list[str] | None:
    if project_id is None:
        return scope
    if scope is None:
        return [project_id]
    if project_id in scope:
        return [project_id]
    return []


async def _resolve_document_project_scope(
    *,
    db: AsyncSession,
    project_reference: str | None,
    authorized_scope: list[str] | None,
) -> list[str] | None:
    """Resolve one case-insensitive reference to the exact indexed project ID.

    A selected UI project already arrives as an exact authorized identifier and
    takes the zero-query path. Model-authored references are matched only
    against the caller's authorized IDs, or for unrestricted callers against
    distinct project IDs that actually exist in the document index. No match or
    a case-fold collision fails closed as an empty scope.
    """

    if project_reference is None:
        return authorized_scope
    reference = project_reference.strip()
    if not reference or authorized_scope == []:
        return []

    if authorized_scope is not None:
        if reference in authorized_scope:
            return [reference]
        matches = {
            candidate
            for candidate in authorized_scope
            if candidate.casefold() == reference.casefold()
        }
        return [matches.pop()] if len(matches) == 1 else []

    exact = await db.execute(
        select(Document.project_id)
        .where(
            Document.deleted_at.is_(None),
            Document.project_id == reference,
        )
        .distinct()
        .limit(2)
    )
    exact_matches = {value for value in exact.scalars().all() if value}
    if len(exact_matches) == 1:
        return [exact_matches.pop()]

    folded = await db.execute(
        select(Document.project_id)
        .where(
            Document.deleted_at.is_(None),
            Document.project_id.is_not(None),
            func.lower(Document.project_id) == reference.lower(),
        )
        .distinct()
        .limit(2)
    )
    folded_matches = {value for value in folded.scalars().all() if value}
    return [folded_matches.pop()] if len(folded_matches) == 1 else []


def _intersect_project_scopes(
    authorized_scope: list[str] | None, selected_project_ids: list[str] | None
) -> list[str] | None:
    """Apply an explicit runtime selection without widening the caller's RBAC scope."""

    if selected_project_ids is None:
        return authorized_scope
    selected = list(dict.fromkeys(selected_project_ids))
    if authorized_scope is None:
        return selected
    return [project_id for project_id in selected if project_id in authorized_scope]


def _preview(text: str, limit: int = 360) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "..."


def _parse_datetime(value: datetime.datetime | str | None) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
