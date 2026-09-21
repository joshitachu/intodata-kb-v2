from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import QueryLog
from src.document_roles import AUXILIARY_DOCUMENT_ROLES
from src.generation.llm_client import (
    generate_document_answer,
    generate_document_answer_stream,
    get_last_usage,
)
from src.generation.citation_audit import (
    append_citation_repair_notice,
    audit_citations,
    cited_documents,
    log_citation_audit,
)
from src.generation.usage import TRUNCATION_NOTICE, mark_if_truncated
from src.harness.trusted_answer import ANSWER_DELTA_CHARS
from src.retrieval.coverage import build_retrieval_coverage, entity_coverage
from src.retrieval.embedder import get_last_embed_tokens
from src.retrieval.entity_matcher import (
    EntityMatchResult,
    match_query_entities,
    matched_unit_names,
)
from src.generation.prompt_builder import (
    build_messages,
    build_no_results_messages,
    should_use_code_assistant,
)
from src.retrieval.metadata_retriever import get_system_knowledge_summary
from src.config import settings
from src.turn_progress import report_phase
from src.turn_timings import measure_model
from src.retrieval.search import (
    fetch_unit_chunks,
    merge_sources_by_document,
    search_and_rerank,
    search_layered,
    search_multi_query,
    search_similar,
    search_similar_hybrid,
)
from src.retrieval.topology_hints import TopologyRetrievalHints
from src.schemas import DocumentSource, QueryRequest, QueryResponse, Source

logger = logging.getLogger(__name__)

# Backwards-compatible monkeypatch seam for the established streaming tests.
# Production uses the approved-chain function itself; the fallback branch in
# ``_generate_streaming_answer`` only activates when a test/embedding caller
# deliberately replaces this historical module symbol.
generate_answer = generate_document_answer
generate_answer_stream = generate_document_answer_stream
_DEFAULT_DOCUMENT_STREAM = generate_document_answer_stream


def _answer_style(query: str, project_ids: list[str] | None) -> str:
    """Pick the system prompt variant for this question.

    Hindsight recall used to run here as well, injecting a memory preamble into
    the prompt. It was removed from this path: the recall was awaited before
    generation could start and cost ~20-25s per turn against a 90s turn-claim
    lease, so an optional enhancement was regularly what pushed a turn past the
    lease and got a completed answer discarded. Memory stays available through
    the /memory routes; it is simply no longer on the answer path.
    """
    return "code" if should_use_code_assistant(query, project_ids) else "assistant"


async def _execute_search(
    query: str,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    top_k: int,
    threshold: float,
    document_ids: list[int] | None,
    project_ids: list[str] | None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> list[Source]:
    """Run the operator-configured document search implementation."""
    if settings.query_expansion_enabled:
        return await search_multi_query(
            client=qdrant, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold,
            document_ids=document_ids, project_ids=project_ids,
            document_roles=document_roles,
            knowledge_revisions=knowledge_revisions,
        )
    if settings.reranker_enabled:
        return await search_and_rerank(
            client=qdrant, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold,
            document_ids=document_ids, project_ids=project_ids,
            document_roles=document_roles,
            knowledge_revisions=knowledge_revisions,
        )
    if settings.hybrid_search_enabled:
        return await search_similar_hybrid(
            client=qdrant, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold,
            document_ids=document_ids, project_ids=project_ids,
            document_roles=document_roles,
            knowledge_revisions=knowledge_revisions,
        )
    return await search_similar(
        client=qdrant, query=query, user_groups=user_groups,
        top_k=top_k, threshold=threshold,
        document_ids=document_ids, project_ids=project_ids,
        document_roles=document_roles,
        knowledge_revisions=knowledge_revisions,
    )


_NO_ENTITY_MATCHES = EntityMatchResult(matches=[], alias_rows_present=False)


async def _resolve_named_entities(
    db: AsyncSession,
    query: str,
    project_ids: list[str] | None,
) -> EntityMatchResult:
    """Detect which known project entities the question names.

    Fail-soft, and logged at ERROR when it fails. This is a retrieval-quality
    guarantee, not a formatting nicety: losing it means an entity the user named
    by hand is back in the ranking contest, which is precisely the failure the
    alias table exists to prevent. Degrading to semantic-only is still better than
    failing the turn, so the exception is swallowed -- but it must never be
    swallowed quietly.
    """

    if not settings.entity_exact_match_enabled:
        return _NO_ENTITY_MATCHES
    try:
        return await match_query_entities(db, query, project_ids)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Entity matching failed; exact-name retrieval is disabled for this "
            "turn: %s",
            exc,
            exc_info=True,
        )
        return _NO_ENTITY_MATCHES


async def _fetch_named_entity_chunks(
    qdrant: AsyncQdrantClient,
    user_groups: list[str],
    matched_units: list[str],
    project_ids: list[str] | None,
    knowledge_revisions: Mapping[str, str] | None,
) -> list[Source]:
    """Rung 1's exact-name safety net: the named units' chunks, by payload filter.

    Runs against the DEFAULT canonical partition (``source_layers=None``) and the
    source role, so it returns the published documents a user naming a job expects
    to be shown -- not the internal analysis partitions rung 2 searches.

    Swallows its own failure for the same reason ``search_layered`` does, at the
    same log level: the semantic result is already in hand and an answer without
    the guarantee beats no answer at all.
    """

    if not matched_units:
        return []
    try:
        return await fetch_unit_chunks(
            qdrant,
            user_groups,
            matched_units,
            source_layers=None,
            project_ids=project_ids,
            document_roles=["source"],
            limit=settings.entity_exact_match_limit,
            knowledge_revisions=knowledge_revisions,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Exact-name unit fetch failed for units=%s: %s",
            matched_units,
            exc,
            exc_info=True,
        )
        return []


async def _execute_role_aware_search(
    query: str,
    request: QueryRequest,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    project_ids: list[str] | None,
    knowledge_revisions: Mapping[str, str] | None = None,
    topology_hints: TopologyRetrievalHints | None = None,
    matched_units: list[str] | None = None,
) -> tuple[list[Source], list[Source]]:
    """Run source-first retrieval plus bounded auxiliary/template context.

    The source and auxiliary searches are independent, so they run concurrently
    rather than sequentially to halve the retrieval wall-clock.

    ``matched_units`` are units the question named by hand. Their chunks are
    fetched deterministically alongside the semantic searches and placed AHEAD of
    them: an entity the user asked for by name must not be able to lose a
    similarity contest to a chunk that merely reads like the question.
    """
    units = matched_units or []
    searches = [
        _execute_search(
            query,
            user_groups,
            qdrant,
            request.top_k,
            request.threshold,
            request.document_ids,
            project_ids,
            document_roles=["source"],
            knowledge_revisions=knowledge_revisions,
        ),
        _execute_search(
            query,
            user_groups,
            qdrant,
            max(1, min(4, request.top_k)),
            request.threshold,
            request.document_ids,
            project_ids,
            document_roles=list(AUXILIARY_DOCUMENT_ROLES),
            knowledge_revisions=knowledge_revisions,
        ),
    ]
    # The established query always runs unchanged.  A topology hint may only add
    # one small source-document search; it cannot alter project/document filters
    # or suppress a result the normal query found.
    if topology_hints is not None and topology_hints.retrieval_terms:
        searches.append(
            _execute_search(
                topology_hints.enriched_query(query),
                user_groups,
                qdrant,
                max(1, min(4, request.top_k)),
                request.threshold,
                request.document_ids,
                project_ids,
                document_roles=["source"],
                knowledge_revisions=knowledge_revisions,
            )
        )
    # Appended last so the two established searches keep indices 0 and 1 whether
    # or not a topology hint added one.
    searches.append(
        _fetch_named_entity_chunks(
            qdrant, user_groups, units, project_ids, knowledge_revisions
        )
    )
    results = await asyncio.gather(*searches)
    entity_sources = results[-1]
    source_sources, auxiliary_sources = results[:2]
    if len(results) == 4:
        source_sources = _merge_retrieval_sources(
            source_sources,
            results[2],
            max_items=request.top_k + min(4, request.top_k),
        )
    if entity_sources:
        # Guaranteed hits lead, exactly as they do inside `search_layered`. The
        # cap is widened by the number of guaranteed chunks rather than shared
        # with them, so the safety net ADDS evidence instead of evicting the
        # semantic results that were already good enough to rank.
        source_sources = _merge_retrieval_sources(
            entity_sources,
            source_sources,
            max_items=len(entity_sources) + request.top_k + min(4, request.top_k),
        )
        logger.info(
            "Exact-name retrieval: units=%s -> %d guaranteed chunk(s) ahead of "
            "%d ranked chunk(s)",
            units,
            len(entity_sources),
            len(source_sources) - len(entity_sources),
        )
    deep_sources = await _execute_deep_layer_search(
        query,
        request,
        user_groups,
        qdrant,
        project_ids,
        rung1_hits=len(source_sources),
        knowledge_revisions=knowledge_revisions,
        matched_units=units,
    )
    if deep_sources:
        # Merged as AUXILIARY, not source: an internal analysis interprets the
        # code, the canonical documents are the published account of it, and the
        # distinction is what `document_role` exists to preserve. Auxiliary
        # sources still reach `merge_sources_by_document`, so they receive
        # reference numbers and are citable -- they simply do not outrank a
        # canonical hit that answers the same question.
        auxiliary_sources = _merge_retrieval_sources(
            auxiliary_sources,
            deep_sources,
            max_items=len(auxiliary_sources) + settings.deep_layer_top_k,
        )
    return source_sources, auxiliary_sources


async def _execute_deep_layer_search(
    query: str,
    request: QueryRequest,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    project_ids: list[str] | None,
    *,
    rung1_hits: int,
    knowledge_revisions: Mapping[str, str] | None = None,
    matched_units: list[str] | None = None,
) -> list[Source]:
    """Retrieval-ladder rung 2: search the internal corpus partitions.

    Returns ``[]`` unless rung 1 came back thin, so the ordinary question pays
    nothing. When it did come back thin the alternative is not a worse answer,
    it is an answer with no sources: ``run_rag_query`` falls through to
    ``build_no_results_messages`` and the model is asked to respond with no
    retrieved context at all.

    A failure here is swallowed deliberately. This rung is supplementary by
    construction, and rung 1's result is already in hand -- degrading to the
    canonical-only answer is strictly better than failing the turn.

    ``matched_units`` finally populates ``search_layered``'s guaranteed-entity
    path. That parameter shipped with the function and had no caller, so the deep
    rung searched the internal partitions semantically only -- exactly the
    ranking contest the parameter was written to exempt named entities from.
    """

    if not settings.deep_layer_escalation_enabled:
        return []
    if not settings.deep_layer_source_layers:
        return []
    if rung1_hits >= settings.deep_layer_min_source_hits:
        return []
    try:
        sources = await search_layered(
            client=qdrant,
            query=query,
            user_groups=user_groups,
            source_layers=list(settings.deep_layer_source_layers),
            top_k=settings.deep_layer_top_k,
            threshold=request.threshold,
            project_ids=project_ids,
            matched_units=list(matched_units) if matched_units else None,
            knowledge_revisions=knowledge_revisions,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Deep-layer escalation failed, continuing without it: %s", exc)
        return []
    logger.info(
        "Deep-layer escalation: rung1=%d hits (< %d) -> %d internal chunk(s) from %s",
        rung1_hits,
        settings.deep_layer_min_source_hits,
        len(sources),
        ",".join(settings.deep_layer_source_layers),
    )
    return sources


def _merge_retrieval_sources(
    primary: list[Source],
    supplemental: list[Source],
    *,
    max_items: int,
) -> list[Source]:
    """Append unique hinted chunks after normal results, preserving recall."""

    merged: list[Source] = []
    seen: set[tuple[int, int]] = set()
    for source in (*primary, *supplemental):
        key = (source.document_id, source.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        merged.append(source)
        if len(merged) >= max_items:
            break
    return merged


async def _persist_query(
    db: AsyncSession,
    user_id: int,
    query_text: str,
    answer: str,
    document_sources: list[DocumentSource],
    latency_ms: int,
) -> None:
    """Persist QueryUsage + QueryLog.

    Non-blocking: each step catches its own exceptions so a logging failure
    never surfaces to the caller.
    """
    usage = get_last_usage()
    if usage:
        try:
            from src.db.models import QueryUsage
            db.add(QueryUsage(
                user_id=user_id,
                interface="rag_query",
                query_text=query_text[:500],
                model_name=usage.model,
                provider=usage.provider,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                embedding_tokens=get_last_embed_tokens(),
                cost_usd=usage.cost_usd,
                latency_ms=latency_ms,
                llm_ms=usage.latency_ms,
            ))
        except Exception as e:
            logger.warning("Failed to persist query usage: %s", e)

    try:
        db.add(QueryLog(
            user_id=user_id,
            query_text=query_text,
            response_text=answer,
            sources=[s.model_dump() for s in document_sources],
            latency_ms=latency_ms,
        ))
    except Exception as e:
        logger.warning("Failed to persist query log: %s", e)


async def _generate_streaming_answer(
    messages: list[dict],
    on_delta: Callable[[str], Awaitable[None]],
) -> str:
    """Stream the answer, publishing ~96-char batches, and return the full text.

    The returned string is the exact concatenation of what was published, so the
    live text and the persisted answer cannot diverge.

    Provider retry/failover is owned by ``generate_document_answer_stream``. It
    may advance only before the provider yielded any answer text, so a new model
    can never replace prose already visible to the user.
    """

    attempts = (0,) if generate_answer_stream is _DEFAULT_DOCUMENT_STREAM else (0, 1)
    for attempt in attempts:
        buffer: list[str] = []
        pending = 0
        produced: list[str] = []
        try:
            async for piece in generate_answer_stream(messages):
                if not piece:
                    continue
                produced.append(piece)
                buffer.append(piece)
                pending += len(piece)
                if pending >= ANSWER_DELTA_CHARS:
                    await on_delta("".join(buffer))
                    buffer.clear()
                    pending = 0
            if buffer:
                await on_delta("".join(buffer))
            # Truncation is only knowable once the stream has ended and the
            # provider's final usage record has arrived, so the notice is
            # published as a last delta rather than woven into the text. Doing it
            # here rather than only on the returned string means a reader
            # watching the answer arrive live sees it too, not just someone who
            # reloads the conversation later.
            marked = mark_if_truncated("".join(produced), get_last_usage())
            if len(marked) != sum(len(p) for p in produced):
                await on_delta(TRUNCATION_NOTICE)
            return marked
        except Exception:
            if produced or attempt == attempts[-1]:
                raise
    raise RuntimeError("Answer streaming exhausted its retry budget")


async def run_rag_query(
    request: QueryRequest,
    user_groups: list[str],
    user_id: int,
    qdrant: AsyncQdrantClient,
    db: AsyncSession,
    project_ids: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    topology_hints: TopologyRetrievalHints | None = None,
) -> QueryResponse:
    """Run the explicit RBAC-scoped document retrieval/generation API.

    ``on_delta`` is opt-in. When supplied, the answer is streamed and each batch
    is published as it is produced; the returned ``answer`` is still the complete
    text, byte-identical to what was published. Callers that omit it (the ``/query``
    endpoint, benchmarks) keep the existing non-streaming behaviour exactly.
    """
    start = time.perf_counter()

    answer_style = _answer_style(request.query, project_ids)

    document_sources: list[DocumentSource] = []

    system_knowledge: str | None = None
    if settings.system_knowledge_injection:
        try:
            system_knowledge = await get_system_knowledge_summary(db, project_ids)
        except Exception as e:
            logger.warning("Failed to fetch system knowledge: %s", e)

    # This function is the whole of a chat turn's work when it is reached through
    # the `answer_from_documentation` capability: the agent's outer model finishes
    # in a couple of seconds and then waits here. Reporting the two real phases is
    # what keeps the user's status line alive for the rest of the turn.
    await report_phase("searching_documents")
    # Resolved before retrieval because both halves of the partial-coverage fix
    # need it: the exact-name fetch needs the units, and the coverage report needs
    # to know which named entities never showed up in what was retrieved.
    entity_result = await _resolve_named_entities(db, request.query, project_ids)
    matched_units = matched_unit_names(entity_result.matches)
    sources, auxiliary_sources = await _execute_role_aware_search(
        request.query,
        request,
        user_groups,
        qdrant,
        project_ids,
        knowledge_revisions,
        topology_hints,
        matched_units=matched_units,
    )

    if sources or auxiliary_sources:
        logger.info("Document search returned %d source chunks", len(sources))
        document_sources = merge_sources_by_document([*sources, *auxiliary_sources])
        ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
        coverage = build_retrieval_coverage(
            sources=sources,
            auxiliary_sources=auxiliary_sources,
            referenced_documents=len(document_sources),
            entities=entity_coverage(
                entity_result.matches,
                [*sources, *auxiliary_sources],
                index_available=entity_result.alias_rows_present,
            ),
        )
        logger.info("Retrieval %s", coverage.log_line())
        messages = build_messages(
            request.query,
            sources,
            reference_numbers=ref_map,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
            auxiliary_sources=auxiliary_sources,
            coverage=coverage,
        )
    else:
        logger.info("No document results passed the configured threshold")
        messages = build_no_results_messages(
            request.query,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
        )
    await report_phase("generating_answer")
    # `measure_model` is what makes this inference visible. Reached through the
    # agent harness, this call is a *nested* model call: the harness middleware
    # only wraps the agent's own outer model, so without this the dominant share
    # of a turn's LLM time was recorded as opaque tool time and `llm_ms` reported
    # only the couple of seconds the outer model spent choosing the tool.
    with measure_model():
        if on_delta is None:
            # Historical module seam retained for tests/embedding callers. In
            # production this alias is exactly the approved provider-chain entry
            # point above, so compatibility does not bypass failover policy.
            answer = await generate_answer(messages)
        else:
            answer = await _generate_streaming_answer(messages, on_delta)

    # A model that stops because it exhausted its output budget ends its stream
    # cleanly and raises nothing, so without this check a fragment is persisted
    # and rendered as a finished answer. The notice is appended to the text
    # itself because that is what every surface shows and stores.
    answer = mark_if_truncated(answer, get_last_usage())

    # Bind the citations the model wrote to the references it was given. Nothing
    # read these markers back before, so a `[7]` in an answer built from four
    # references rendered as a citation and resolved to nothing.
    #
    # Repair is additive. The text is already on the user's screen by now, so the
    # notice is appended and published as one last delta -- the same contract the
    # truncation notice above follows, and for the same reason: the live text and
    # the persisted text must not diverge.
    audit = audit_citations(answer, document_sources)
    log_citation_audit(audit, offered=len(document_sources))
    if audit.has_dangling:
        repaired = append_citation_repair_notice(answer, audit)
        if repaired != answer:
            if on_delta is not None:
                # Exactly the text that was added, sliced from the two strings
                # rather than re-derived. `harness/evidence.py` compares the
                # streamed answer against the persisted one byte for byte and
                # logs an ERROR when they differ.
                await on_delta(repaired[len(answer):])
            answer = repaired

    latency_ms = int((time.perf_counter() - start) * 1000)

    logger.info(f"Completed in {latency_ms}ms | {len(document_sources)} document sources returned")

    # The full retrieved set is persisted: the query log is the audit record of
    # what retrieval offered, and narrowing it would destroy the only evidence
    # that a document was retrieved and then ignored.
    await _persist_query(
        db=db,
        user_id=user_id,
        query_text=request.query,
        answer=answer,
        document_sources=document_sources,
        latency_ms=latency_ms,
    )

    return QueryResponse(
        answer=answer,
        # The subset the answer actually used. Returning every merged document
        # made the sources panel assert provenance for documents no sentence in
        # the answer rests on, which is a citation contract in appearance only.
        # `cited_documents` returns the full list when the answer cited nothing,
        # so a greeting or an uncited answer still shows what was retrieved.
        sources=cited_documents(document_sources, audit),
        latency_ms=latency_ms,
    )
