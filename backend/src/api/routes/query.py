from __future__ import annotations

import time

from fastapi import APIRouter
from qdrant_client.models import Filter, FieldCondition, MatchAny

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.generation.rag_chain import run_rag_query
from src.rbac.permissions import get_user_groups
from src.retrieval.embedder import embed_text
from src.retrieval.vector_store import COLLECTION_NAME
from src.schemas import (
    DebugSearchRequest,
    DebugSearchResponse,
    DebugSearchResult,
    QueryRequest,
    QueryResponse,
)

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    user: CurrentUser,
    db: DBSession,
    qdrant: QdrantClient,
) -> QueryResponse:
    """Run a RAG query: embed → search → generate answer with citations."""
    user_groups = get_user_groups(user)
    return await run_rag_query(
        request=body,
        user_groups=user_groups,
        user_id=user.id,
        qdrant=qdrant,
        db=db,
    )


@router.post("/query/debug", response_model=DebugSearchResponse)
async def query_debug(
    body: DebugSearchRequest,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> DebugSearchResponse:
    """Return raw Qdrant scores without threshold filtering for diagnostics."""
    user_groups = get_user_groups(user)

    embed_start = time.perf_counter()
    query_embedding = await embed_text(body.query)
    embedding_ms = int((time.perf_counter() - embed_start) * 1000)

    rbac_filter_applied = bool(user_groups)
    search_filter = Filter(
        must=[
            FieldCondition(
                key="allowed_groups",
                match=MatchAny(any=user_groups),
            )
        ]
    ) if user_groups else None

    results = await qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
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
