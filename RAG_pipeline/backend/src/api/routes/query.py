from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.api.errors import api_error
from src.api.routes.auth import limiter
from src.config import settings
from src.db.models import QueryUsage
from src.generation.llm_client import generate_answer_stream, get_last_usage
from src.generation.prompt_builder import (
    build_messages,
    build_no_results_messages,
    should_use_code_assistant,
)
from src.retrieval.metadata_retriever import get_system_knowledge_summary
from src.generation.rag_chain import _execute_role_aware_search, run_rag_query
from src.rbac.permissions import get_user_groups, get_user_project_ids
from src.retrieval.embedder import embed_text, get_last_embed_tokens
from src.retrieval.search import merge_sources_by_document
from src.retrieval.knowledge_revisions import active_knowledge_revisions
from src.retrieval.vector_store import COLLECTION_NAME
from src.schemas import (
    DebugSearchRequest,
    DebugSearchResponse,
    DebugSearchResult,
    QueryRequest,
    QueryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse, summary="Run a RAG query")
@limiter.limit("30/minute")
async def query(
    request: Request,
    body: QueryRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> QueryResponse:
    """Run a RAG query: embed → search → generate answer with citations."""
    user_groups = get_user_groups(user)
    project_ids = await get_user_project_ids(user, db)
    if body.project_id:
        if project_ids is not None and body.project_id not in project_ids:
            raise api_error(403, "forbidden", "No access to this project")
        project_ids = [body.project_id]
    knowledge_revisions = await active_knowledge_revisions(db, project_ids)
    try:
        return await run_rag_query(
            request=body,
            user_groups=user_groups,
            user_id=user.id,
            qdrant=qdrant,
            db=db,
            project_ids=project_ids,
            knowledge_revisions=knowledge_revisions,
        )
    except RuntimeError as e:
        logger.error("RAG query failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"LLM generation failed: {e}",
        )


@router.post("/query/debug", response_model=DebugSearchResponse, summary="Debug search scores without threshold")
async def query_debug(
    body: DebugSearchRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> DebugSearchResponse:
    """Return raw Qdrant scores without threshold filtering for diagnostics."""
    user_groups = get_user_groups(user)
    project_ids = await get_user_project_ids(user, db)
    knowledge_revisions = await active_knowledge_revisions(db, project_ids)

    embed_start = time.perf_counter()
    query_embedding = await embed_text(body.query)
    embedding_ms = int((time.perf_counter() - embed_start) * 1000)

    from src.retrieval.search import _build_search_filter
    search_filter = _build_search_filter(
        user_groups, project_ids=project_ids, knowledge_revisions=knowledge_revisions
    )
    rbac_filter_applied = search_filter is not None

    results = await qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        using="dense",
        query_filter=search_filter,
        limit=body.top_k,
        with_payload=True,
    )

    debug_results = [
        DebugSearchResult(
            document_id=point.payload["document_id"],
            filename=point.payload["filename"],
            chunk_index=point.payload["chunk_index"],
            score=round(point.score, 4),
            text=point.payload["text"],
        )
        for point in results.points
    ]

    return DebugSearchResponse(
        query=body.query,
        rbac_groups=user_groups,
        rbac_filter_applied=rbac_filter_applied,
        top_k=body.top_k,
        embedding_ms=embedding_ms,
        total_results=len(debug_results),
        results=debug_results,
    )


@router.post("/query/stream", summary="Run a RAG query with streaming SSE response")
@limiter.limit("30/minute")
async def query_stream(
    request: Request,
    body: QueryRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> StreamingResponse:
    """Streaming RAG query: search → build prompt → stream LLM response via SSE.

    Returns Server-Sent Events with:
    - event: token   → individual LLM tokens
    - event: sources → JSON array of DocumentSource objects
    - event: done    → signals end of stream
    """
    user_groups = get_user_groups(user)
    project_ids = await get_user_project_ids(user, db)
    if body.project_id:
        if project_ids is not None and body.project_id not in project_ids:
            raise api_error(403, "forbidden", "No access to this project")
        project_ids = [body.project_id]
    knowledge_revisions = await active_knowledge_revisions(db, project_ids)
    start = time.perf_counter()

    # Hindsight recall used to run here, before any retrieval. It was removed
    # from the answer path: the three bank ensures plus the recall gather were
    # awaited before generation started, costing ~20-25s per turn against a 90s
    # turn-claim lease. Memory remains reachable via the /memory routes.
    answer_style = "code" if should_use_code_assistant(body.query, project_ids) else "assistant"

    # Fetch system knowledge for injection
    system_knowledge: str | None = None
    if settings.system_knowledge_injection:
        try:
            system_knowledge = await get_system_knowledge_summary(db, project_ids)
        except Exception as e:
            logger.warning("Stream: Failed to fetch system knowledge: %s", e)

    sources, auxiliary_sources = await _execute_role_aware_search(
        body.query, body, user_groups, qdrant, project_ids, knowledge_revisions,
    )

    if sources or auxiliary_sources:
        document_sources = merge_sources_by_document([*sources, *auxiliary_sources])
        ref_map = {doc.document_id: doc.reference_number for doc in document_sources}
        messages = build_messages(
            body.query,
            sources,
            reference_numbers=ref_map,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
            auxiliary_sources=auxiliary_sources,
        )
        sources_data = [source.model_dump() for source in document_sources]
    else:
        messages = build_no_results_messages(
            body.query,
            system_knowledge=system_knowledge,
            answer_style=answer_style,
        )
        sources_data = []
        document_sources = []

    # 3. Stream response
    async def event_generator():
        try:
            # Send sources first so frontend can display them
            yield f"event: sources\ndata: {json.dumps(sources_data)}\n\n"

            # Stream LLM tokens and accumulate answer for retain
            accumulated = ""
            async for token in generate_answer_stream(messages):
                accumulated += token
                yield f"event: token\ndata: {json.dumps(token)}\n\n"

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
            yield f"event: usage\ndata: {json.dumps(usage_data)}\n\n"

            # Persist usage to DB (fire-and-forget)
            if usage:
                try:
                    entry = QueryUsage(
                        user_id=user.id,
                        interface="rag_query",
                        query_text=body.query[:500],
                        model_name=usage.model,
                        provider=usage.provider,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        embedding_tokens=get_last_embed_tokens(),
                        cost_usd=usage.cost_usd,
                        latency_ms=latency_ms,
                        llm_ms=usage.latency_ms,
                    )
                    db.add(entry)
                    await db.commit()
                except Exception as db_err:
                    logger.warning("Failed to persist query usage: %s", db_err)

            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            logger.error("Streaming query failed: %s", e, exc_info=True)
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
