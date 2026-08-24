from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import QueryLog
from src.document_roles import AUXILIARY_DOCUMENT_ROLES
from src.memory.context import build_memory_context
from src.generation.llm_client import (
    generate_document_answer,
    generate_document_answer_stream,
    get_last_usage,
)
from src.harness.trusted_answer import ANSWER_DELTA_CHARS
from src.retrieval.embedder import get_last_embed_tokens
from src.generation.prompt_builder import (
    build_messages,
    build_no_results_messages,
    should_use_code_assistant,
)
from src.retrieval.metadata_retriever import get_system_knowledge_summary
from src.config import settings
from src.turn_progress import report_phase
from src.turn_timings import measure_model
from src.memory.hindsight_client import (
    ensure_corrections_bank,
    ensure_quality_bank,
    ensure_user_bank,
    recall_corrections,
    recall_quality_insights,
    recall_user_memory,
    retain_interaction,
)
from src.retrieval.search import (
    merge_sources_by_document,
    search_and_rerank,
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


async def _recall_memories(
    uid: str,
    query: str,
    project_ids: list[str] | None,
) -> tuple[str | None, str]:
    """Run parallel Hindsight recall. Returns (memory_context, answer_style).

    Memory failure is non-fatal — returns (None, answer_style) on any exception.
    """
    try:
        await ensure_user_bank(uid)
        await ensure_quality_bank()
        await ensure_corrections_bank()
        user_memories, quality_insights, corrections = await asyncio.gather(
            recall_user_memory(uid, query),
            recall_quality_insights(query),
            recall_corrections(query),
        )
        memory_context = build_memory_context(user_memories, quality_insights, corrections)
        if memory_context:
            logger.info(
                "Injecting %d user memories + %d quality insights + %d corrections",
                len(user_memories), len(quality_insights), len(corrections),
            )
    except Exception as e:
        logger.warning("Memory recall failed (non-fatal): %s", e)
        memory_context = None
    answer_style = "code" if should_use_code_assistant(query, project_ids) else "assistant"
    return memory_context, answer_style


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


async def _execute_role_aware_search(
    query: str,
    request: QueryRequest,
    user_groups: list[str],
    qdrant: AsyncQdrantClient,
    project_ids: list[str] | None,
    knowledge_revisions: Mapping[str, str] | None = None,
    topology_hints: TopologyRetrievalHints | None = None,
) -> tuple[list[Source], list[Source]]:
    """Run source-first retrieval plus bounded auxiliary/template context.

    The source and auxiliary searches are independent, so they run concurrently
    rather than sequentially to halve the retrieval wall-clock.
    """
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
    results = await asyncio.gather(*searches)
    source_sources, auxiliary_sources = results[:2]
    if len(results) == 3:
        source_sources = _merge_retrieval_sources(
            source_sources,
            results[2],
            max_items=request.top_k + min(4, request.top_k),
        )
    return source_sources, auxiliary_sources


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
    uid: str,
    query_text: str,
    answer: str,
    document_sources: list[DocumentSource],
    latency_ms: int,
    action: str,
    query_type: str,
    project_ids: list[str] | None,
) -> None:
    """Persist QueryUsage + QueryLog and fire-and-forget Hindsight retention.

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

    source_names = [s.filename for s in document_sources] if document_sources else []
    asyncio.create_task(retain_interaction(
        user_id=uid,
        query=query_text,
        answer=answer,
        sources_used=source_names,
        search_result_count=len(document_sources),
        latency_ms=latency_ms,
        action=action,
        query_type=query_type,
        project_ids=project_ids,
    ))

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
            return "".join(produced)
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
    uid = str(user_id)

    memory_context, answer_style = await _recall_memories(uid, request.query, project_ids)

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
    sources, auxiliary_sources = await _execute_role_aware_search(
        request.query,
        request,
        user_groups,
        qdrant,
        project_ids,
        knowledge_revisions,
        topology_hints,
    )

    if sources or auxiliary_sources:
        logger.info("Document search returned %d source chunks", len(sources))
        document_sources = merge_sources_by_document([*sources, *auxiliary_sources])
        ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
        messages = build_messages(
            request.query,
            sources,
            reference_numbers=ref_map,
            memory_context=memory_context,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
            auxiliary_sources=auxiliary_sources,
        )
    else:
        logger.info("No document results passed the configured threshold")
        messages = build_no_results_messages(
            request.query,
            memory_context=memory_context,
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

    latency_ms = int((time.perf_counter() - start) * 1000)

    logger.info(f"Completed in {latency_ms}ms | {len(document_sources)} document sources returned")

    await _persist_query(
        db=db,
        user_id=user_id,
        uid=uid,
        query_text=request.query,
        answer=answer,
        document_sources=document_sources,
        latency_ms=latency_ms,
        action="document_search",
        query_type="document_answer",
        project_ids=project_ids,
    )

    return QueryResponse(
        answer=answer,
        sources=document_sources,
        latency_ms=latency_ms,
    )
