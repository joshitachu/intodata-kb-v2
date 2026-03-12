from __future__ import annotations

import logging
import time
from collections import defaultdict

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Filter,
    FieldCondition,
    FusionQuery,
    MatchAny,
    Prefetch,
    SparseVector as QdrantSparseVector,
)

from src.config import settings
from src.retrieval.embedder import embed_text
from src.retrieval.query_expander import expand_query
from src.retrieval.reranker import rerank
from src.retrieval.sparse_embedder import embed_text_sparse
from src.retrieval.vector_store import COLLECTION_NAME
from src.schemas import DocumentSource, Source

logger = logging.getLogger(__name__)


def _build_search_filter(
    user_groups: list[str],
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
) -> Filter | None:
    """Build Qdrant filter combining RBAC groups, project access, and optional document_ids.

    Args:
        user_groups: Role-based groups for allowed_groups filtering.
        document_ids: Optional list of specific document IDs to restrict search to.
        project_ids: Optional list of project IDs the user has access to.
            None means no project restriction (admin/superadmin).
            Empty list means user has no project assignments → no results.
    """
    conditions = []
    # Admin users see everything — skip group filter
    if user_groups and "admin" not in user_groups:
        # Build the set of allowed group values: user's role groups + assigned project_ids
        allowed_values = list(user_groups)
        if project_ids is not None:
            allowed_values.extend(project_ids)
        # Always include "public" so public docs are visible
        if "public" not in allowed_values:
            allowed_values.append("public")
        conditions.append(
            FieldCondition(
                key="allowed_groups",
                match=MatchAny(any=allowed_values),
            )
        )
    elif project_ids is not None and not user_groups:
        # Non-admin user with no role groups but has project assignments
        allowed_values = list(project_ids)
        if "public" not in allowed_values:
            allowed_values.append("public")
        conditions.append(
            FieldCondition(
                key="allowed_groups",
                match=MatchAny(any=allowed_values),
            )
        )

    if document_ids:
        conditions.append(
            FieldCondition(
                key="document_id",
                match=MatchAny(any=document_ids),
            )
        )
    if conditions:
        return Filter(must=conditions)
    return None


async def search_similar(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = 5,
    threshold: float = 0.3,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
) -> list[Source]:
    """Embed query, search Qdrant with RBAC filtering, return sources above threshold.

    Uses dense vector search only (fallback when hybrid is disabled).
    """
    embed_start = time.perf_counter()
    query_embedding = await embed_text(query)
    embed_ms = int((time.perf_counter() - embed_start) * 1000)
    logger.info(f"Embedded query in {embed_ms}ms: {query[:80]!r}")

    search_filter = _build_search_filter(user_groups, document_ids, project_ids)
    logger.info(f"Search filter: groups={user_groups}, doc_ids={document_ids}, project_ids={project_ids} | top_k={top_k}")

    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        using="dense",
        query_filter=search_filter,
        limit=top_k,
        with_payload=True,
    )

    return _process_results(results.points, threshold)


async def search_similar_hybrid(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = 5,
    threshold: float = 0.25,
    prefetch_limit: int | None = None,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
) -> list[Source]:
    """Hybrid search: dense + sparse with Reciprocal Rank Fusion (RRF).

    Uses Qdrant's Prefetch API to retrieve candidates from both indexes
    and fuse them server-side with RRF.
    """
    pf_limit = prefetch_limit if prefetch_limit is not None else settings.prefetch_limit

    embed_start = time.perf_counter()
    query_embedding = await embed_text(query)
    sparse_vector = embed_text_sparse(query)
    embed_ms = int((time.perf_counter() - embed_start) * 1000)
    logger.info(f"Embedded query (dense + sparse) in {embed_ms}ms: {query[:80]!r}")

    search_filter = _build_search_filter(user_groups, document_ids, project_ids)
    logger.info(f"Search filter: groups={user_groups}, doc_ids={document_ids}, project_ids={project_ids} | top_k={top_k}")

    # Prefetch from both indexes, fuse with RRF
    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            Prefetch(
                query=query_embedding,
                using="dense",
                filter=search_filter,
                limit=pf_limit,
            ),
            Prefetch(
                query=QdrantSparseVector(
                    indices=sparse_vector.indices,
                    values=sparse_vector.values,
                ),
                using="sparse",
                filter=search_filter,
                limit=pf_limit,
            ),
        ],
        query=FusionQuery(fusion="rrf"),
        limit=top_k,
        with_payload=True,
    )

    logger.info(f"Hybrid search (RRF) returned {len(results.points)} results")
    return _process_results(results.points, threshold)


async def search_and_rerank(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = 5,
    threshold: float = 0.25,
    rerank_candidates: int | None = None,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
) -> list[Source]:
    """Search with hybrid retrieval then rerank with cross-encoder.

    1. Retrieve candidates via hybrid search (more than needed)
    2. Rerank candidates with cross-encoder
    3. Return top_k by reranker score

    Args:
        rerank_candidates: How many candidates to retrieve before reranking.
            Defaults to settings.rerank_top_n.
    """
    candidates_n = rerank_candidates or settings.rerank_top_n

    # Retrieve more candidates than needed for reranking.
    # Use threshold=0.0 — the reranker is the quality gate, not the RRF threshold.
    if settings.hybrid_search_enabled:
        candidates = await search_similar_hybrid(
            client=client,
            query=query,
            user_groups=user_groups,
            top_k=candidates_n,
            threshold=0.0,
            document_ids=document_ids,
            project_ids=project_ids,
        )
    else:
        candidates = await search_similar(
            client=client,
            query=query,
            user_groups=user_groups,
            top_k=candidates_n,
            threshold=0.0,
            document_ids=document_ids,
            project_ids=project_ids,
        )

    if not candidates:
        return []

    if not settings.reranker_enabled:
        return candidates[:top_k]

    # Rerank (with graceful fallback to original scores on API failure)
    texts = [s.text for s in candidates]
    try:
        reranked = await rerank(query, texts, top_k=top_k)
    except Exception as e:
        logger.warning(f"Reranker failed ({type(e).__name__}: {e}), falling back to original scores")
        return candidates[:top_k]

    # Build reranked source list with updated scores
    result: list[Source] = []
    for orig_idx, rerank_score in reranked:
        src = candidates[orig_idx]
        result.append(
            Source(
                document_id=src.document_id,
                filename=src.filename,
                chunk_index=src.chunk_index,
                score=round(rerank_score, 4),
                text=src.text,
            )
        )

    logger.info(f"Reranked: {len(candidates)} candidates → {len(result)} results")
    return result


async def search_multi_query(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = 5,
    threshold: float = 0.25,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
) -> list[Source]:
    """Multi-query search with query expansion + deduplication + reranking.

    1. Expand query into 2-3 variants
    2. Search with each variant (hybrid or dense)
    3. Deduplicate by (document_id, chunk_index)
    4. Rerank combined set using original query
    5. Return top_k
    """
    queries = await expand_query(query)
    logger.info(f"Multi-query search with {len(queries)} queries")

    # Search with each query variant
    all_sources: list[Source] = []
    for q in queries:
        if settings.hybrid_search_enabled:
            sources = await search_similar_hybrid(
                client=client,
                query=q,
                user_groups=user_groups,
                top_k=settings.rerank_top_n if settings.reranker_enabled else top_k,
                threshold=threshold,
                document_ids=document_ids,
                project_ids=project_ids,
            )
        else:
            sources = await search_similar(
                client=client,
                query=q,
                user_groups=user_groups,
                top_k=settings.rerank_top_n if settings.reranker_enabled else top_k,
                threshold=threshold,
                document_ids=document_ids,
                project_ids=project_ids,
            )
        all_sources.extend(sources)

    # Deduplicate by (document_id, chunk_index)
    seen: set[tuple[int, int]] = set()
    unique_sources: list[Source] = []
    for src in all_sources:
        key = (src.document_id, src.chunk_index)
        if key not in seen:
            seen.add(key)
            unique_sources.append(src)

    logger.info(f"Multi-query: {len(all_sources)} total → {len(unique_sources)} unique sources")

    if not unique_sources:
        return []

    # Rerank using the ORIGINAL query (not the expanded ones)
    if settings.reranker_enabled and len(unique_sources) > top_k:
        texts = [s.text for s in unique_sources]
        try:
            reranked = await rerank(query, texts, top_k=top_k)
        except Exception as e:
            logger.warning(
                f"Reranker failed ({type(e).__name__}: {e}), falling back to original scores"
            )
            unique_sources.sort(key=lambda s: s.score, reverse=True)
            return unique_sources[:top_k]
        result: list[Source] = []
        for orig_idx, rerank_score in reranked:
            src = unique_sources[orig_idx]
            result.append(
                Source(
                    document_id=src.document_id,
                    filename=src.filename,
                    chunk_index=src.chunk_index,
                    score=round(rerank_score, 4),
                    text=src.text,
                )
            )
        return result

    # No reranker — sort by score and return top_k
    unique_sources.sort(key=lambda s: s.score, reverse=True)
    return unique_sources[:top_k]


def _process_results(
    points: list,
    threshold: float,
) -> list[Source]:
    """Convert Qdrant points to Source objects, filtering by threshold."""
    raw_count = len(points)
    logger.info(f"Qdrant returned {raw_count} raw results:")
    for i, point in enumerate(points, 1):
        fname = point.payload.get("filename", "?")
        cidx = point.payload.get("chunk_index", "?")
        text_preview = point.payload.get("text", "")[:80].replace("\n", " ")
        logger.info(f"  #{i} score={point.score:.4f} {fname} chunk {cidx}: {text_preview!r}")

    sources = []
    dropped_scores: list[float] = []
    for point in points:
        if point.score < threshold:
            dropped_scores.append(round(point.score, 4))
            continue
        payload = point.payload
        sources.append(
            Source(
                document_id=payload["document_id"],
                filename=payload["filename"],
                chunk_index=payload["chunk_index"],
                score=round(point.score, 4),
                text=payload["text"],
            )
        )

    if dropped_scores:
        logger.info(
            f"Threshold {threshold}: {len(sources)} passed, "
            f"{len(dropped_scores)} dropped (scores: {', '.join(str(s) for s in dropped_scores)})"
        )
    else:
        logger.info(f"Threshold {threshold}: all {len(sources)} results passed")

    return sources


def merge_sources_by_document(
    sources: list[Source],
    max_documents: int | None = None,
) -> list[DocumentSource]:
    """Merge chunk-level sources into document-level sources.

    Groups chunks by document_id, concatenates texts (sorted by chunk_index),
    keeps the highest score per document, sorts by score descending, and
    assigns sequential reference numbers starting at 1.

    Args:
        sources: Chunk-level search results.
        max_documents: Maximum number of documents to return. Defaults to 1.
    """
    groups: dict[int, list[Source]] = defaultdict(list)
    for src in sources:
        groups[src.document_id].append(src)

    merged: list[DocumentSource] = []
    for doc_id, chunks in groups.items():
        chunks.sort(key=lambda c: c.chunk_index)
        combined_text = "\n\n".join(c.text for c in chunks)
        best_score = max(c.score for c in chunks)
        merged.append(
            DocumentSource(
                document_id=doc_id,
                filename=chunks[0].filename,
                score=best_score,
                text=combined_text,
                reference_number=0,  # assigned below
            )
        )

    # Sort by score descending, limit to max_documents
    limit = max_documents if max_documents is not None else settings.max_result_documents
    merged.sort(key=lambda d: d.score, reverse=True)
    merged = merged[:limit]
    for i, doc in enumerate(merged):
        doc.reference_number = i + 1
    return merged
