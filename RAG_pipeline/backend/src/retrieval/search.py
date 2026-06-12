from __future__ import annotations

import logging
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Filter,
    FieldCondition,
    FusionQuery,
    IsEmptyCondition,
    MatchAny,
    PayloadField,
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
    source_layers: list[str] | None = None,
) -> Filter | None:
    """Build Qdrant filter combining RBAC groups, project access, document_ids, and layer.

    Args:
        user_groups: Role-based groups for allowed_groups filtering.
        document_ids: Optional list of specific document IDs to restrict search to.
        project_ids: Optional list of project IDs the user has access to.
            None means no project restriction (admin/superadmin).
            Empty list means user has no project assignments → no results.
        source_layers: Optional corpus layer filter.
            None → default: canonical_docs + is-empty (legacy chunks included).
            Non-empty list → exact layer match, no legacy tolerance.
            Empty list → no layer condition (all layers, including legacy).
    """
    conditions = []
    # Admin users see everything — skip group filter
    if user_groups and "admin" not in user_groups:
        # Build the set of allowed group values: user's role groups + assigned project_ids
        allowed_values = list(user_groups)
        if project_ids is not None:
            allowed_values.extend(project_ids)
        if allowed_values:
            conditions.append(
                FieldCondition(
                    key="allowed_groups",
                    match=MatchAny(any=allowed_values),
                )
            )
        else:
            # No roles and no project assignments → block all results
            conditions.append(
                FieldCondition(
                    key="allowed_groups",
                    match=MatchAny(any=["__none__"]),
                )
            )
    elif project_ids is not None and not user_groups:
        # Non-admin user with no role groups but has project assignments
        allowed_values = list(project_ids)
        if allowed_values:
            conditions.append(
                FieldCondition(
                    key="allowed_groups",
                    match=MatchAny(any=allowed_values),
                )
            )
        else:
            # No project assignments → block all results
            conditions.append(
                FieldCondition(
                    key="allowed_groups",
                    match=MatchAny(any=["__none__"]),
                )
            )
    elif "admin" in user_groups and project_ids is not None and project_ids:
        # Admin selected a specific project → restrict to that project's docs.
        # Without project_ids (None), admin sees everything as before.
        conditions.append(
            FieldCondition(
                key="allowed_groups",
                match=MatchAny(any=project_ids),
            )
        )

    if document_ids:
        conditions.append(
            FieldCondition(
                key="document_id",
                match=MatchAny(any=document_ids),
            )
        )

    # Corpus layer partitioning.
    # source_layers=None  → default canonical filter with is-empty tolerance for
    #                       legacy chunks that predate the source_layer payload field.
    # source_layers=[...] → exact match, no legacy tolerance (used by rung 2).
    # source_layers=[]    → no layer condition (explicitly search all partitions).
    if source_layers is None:
        if settings.default_source_layers:
            conditions.append(
                Filter(
                    should=[
                        FieldCondition(
                            key="source_layer",
                            match=MatchAny(any=settings.default_source_layers),
                        ),
                        IsEmptyCondition(is_empty=PayloadField(key="source_layer")),
                    ]
                )
            )
    elif source_layers:
        conditions.append(
            FieldCondition(
                key="source_layer",
                match=MatchAny(any=source_layers),
            )
        )

    if conditions:
        return Filter(must=conditions)
    return None


_LAYER_DISPLAY_LABELS = {
    "technical_analysis": "Technical Analysis",
    "support_maps": "Support Map",
    "scorecard": "Quality Scorecard",
    "evidence_slices": "Evidence",
}


def friendly_display_name(filename: str, source_layer: str | None = None) -> str:
    """Human-readable citation name for a scoped RAG filename.

    Canonical docs keep the established "name (Project: id)" format. Internal
    docs (``{project}/_internal/{layer}/{encoded-name}.md``) never expose the
    raw ``_internal/`` path: the artifact name is percent-decoded and prefixed
    with the layer label instead.
    """
    project, _, remainder = filename.partition("/")
    if not remainder:
        return filename
    if remainder.startswith("_internal/"):
        parts = remainder.split("/")
        layer = source_layer or (parts[1] if len(parts) > 2 else "")
        stem = unquote(Path(parts[-1]).stem)
        label = _LAYER_DISPLAY_LABELS.get(layer, "Internal Analysis")
        return f"{label}: {stem} (Project: {project})"
    return f"{remainder} (Project: {project})"


def _source_from_payload(payload: dict, score: float) -> Source:
    """Build a Source from a Qdrant point payload.

    Uses .get() for all optional layer fields so legacy chunks (no source_layer)
    and persisted conversation sources (missing new fields) deserialize unchanged.
    """
    return Source(
        document_id=payload["document_id"],
        filename=payload["filename"],
        chunk_index=payload["chunk_index"],
        score=round(score, 4),
        text=payload["text"],
        source_layer=payload.get("source_layer"),
        section=payload.get("section"),
        unit=payload.get("unit"),
        group=payload.get("group"),
        display_name=friendly_display_name(payload["filename"], payload.get("source_layer")),
        artifact_path=payload.get("artifact_path"),
        visibility=payload.get("visibility"),
        run_id=payload.get("run_id"),
        project_id=payload.get("project_id"),
    )


async def search_similar(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
) -> list[Source]:
    """Embed query, search Qdrant with RBAC filtering, return sources above threshold.

    Uses dense vector search only (fallback when hybrid is disabled).
    """
    embed_start = time.perf_counter()
    query_embedding = await embed_text(query)
    embed_ms = int((time.perf_counter() - embed_start) * 1000)
    logger.info(f"Embedded query in {embed_ms}ms: {query[:80]!r}")

    search_filter = _build_search_filter(user_groups, document_ids, project_ids, source_layers)
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
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    prefetch_limit: int | None = None,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
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

    search_filter = _build_search_filter(user_groups, document_ids, project_ids, source_layers)
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
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    rerank_candidates: int | None = None,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
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
            source_layers=source_layers,
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
            source_layers=source_layers,
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

    # Preserve all payload fields (including layer metadata) via model_copy.
    result: list[Source] = []
    for orig_idx, rerank_score in reranked:
        src = candidates[orig_idx]
        result.append(src.model_copy(update={"score": round(rerank_score, 4)}))

    logger.info(f"Reranked: {len(candidates)} candidates → {len(result)} results")
    return await expand_neighbor_chunks(
        client=client,
        sources=result,
        user_groups=user_groups,
        project_ids=project_ids,
        source_layers=source_layers,
    )


def _build_neighbor_filter(
    user_groups: list[str],
    document_id: int,
    chunk_indexes: list[int],
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
) -> Filter:
    """Build a same-document/same-project filter for neighbor chunk fetches."""
    base = _build_search_filter(
        user_groups,
        document_ids=[document_id],
        project_ids=project_ids,
        source_layers=source_layers,
    )
    conditions = list(base.must) if base and base.must else []
    conditions.append(
        FieldCondition(
            key="chunk_index",
            match=MatchAny(any=chunk_indexes),
        )
    )
    return Filter(must=conditions)


async def expand_neighbor_chunks(
    client: AsyncQdrantClient,
    sources: list[Source],
    user_groups: list[str],
    project_ids: list[str] | None = None,
    window: int | None = None,
    source_layers: list[str] | None = None,
) -> list[Source]:
    """Add adjacent chunks around high-scoring retrieved chunks.

    The generated project docs often split one logical flow across neighboring
    chunks. Pulling immediate neighbors gives the LLM continuity while keeping
    retrieval cheap and scoped by the same RBAC/project filters as search.
    """
    if not settings.neighbor_chunk_expansion_enabled or not sources:
        return sources

    span = window if window is not None else settings.neighbor_chunk_window
    if span <= 0:
        return sources

    expanded: list[Source] = []
    seen: set[tuple[int, int]] = set()

    for src in sources:
        wanted = [
            idx
            for idx in range(src.chunk_index - span, src.chunk_index + span + 1)
            if idx >= 0
        ]
        missing = [
            idx for idx in wanted
            if (src.document_id, idx) not in seen
        ]
        fetched_by_index: dict[int, Source] = {}

        if missing:
            try:
                points, _ = await client.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=_build_neighbor_filter(
                        user_groups,
                        src.document_id,
                        missing,
                        project_ids=project_ids,
                        source_layers=source_layers,
                    ),
                    limit=len(missing),
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    payload = point.payload
                    chunk_index = payload["chunk_index"]
                    neighbor_score = src.score if chunk_index == src.chunk_index else src.score * 0.95
                    fetched_by_index[chunk_index] = _source_from_payload(payload, neighbor_score)
            except Exception as e:
                logger.warning("Neighbor chunk expansion failed for doc=%s chunk=%s: %s",
                               src.document_id, src.chunk_index, e)

        for idx in wanted:
            key = (src.document_id, idx)
            if key in seen:
                continue
            neighbor = fetched_by_index.get(idx)
            if neighbor is None and idx == src.chunk_index:
                neighbor = src
            if neighbor is None:
                continue
            expanded.append(neighbor)
            seen.add(key)

    logger.info("Neighbor expansion: %d retrieved chunks → %d context chunks",
                len(sources), len(expanded))
    return expanded


async def search_multi_query(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
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
                source_layers=source_layers,
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
                source_layers=source_layers,
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
        result: list[Source] = [
            unique_sources[orig_idx].model_copy(update={"score": round(rerank_score, 4)})
            for orig_idx, rerank_score in reranked
        ]
        return await expand_neighbor_chunks(
            client=client,
            sources=result,
            user_groups=user_groups,
            project_ids=project_ids,
            source_layers=source_layers,
        )

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
        sources.append(_source_from_payload(point.payload, point.score))

    if dropped_scores:
        logger.info(
            f"Threshold {threshold}: {len(sources)} passed, "
            f"{len(dropped_scores)} dropped (scores: {', '.join(str(s) for s in dropped_scores)})"
        )
    else:
        logger.info(f"Threshold {threshold}: all {len(sources)} results passed")

    return sources


async def search_layered(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    *,
    source_layers: list[str],
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    project_ids: list[str] | None = None,
) -> list[Source]:
    """Run the configured search strategy against explicit corpus layers.

    Used by retrieval-ladder rung 2: same RBAC/project filters as the default
    path, but with an exact ``source_layers`` filter (no legacy is-empty
    tolerance). ``source_layers`` is required — this helper must never fall
    back to the default partition.
    """
    if settings.reranker_enabled:
        return await search_and_rerank(
            client=client, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold, project_ids=project_ids,
            source_layers=source_layers,
        )
    if settings.hybrid_search_enabled:
        return await search_similar_hybrid(
            client=client, query=query, user_groups=user_groups,
            top_k=top_k, threshold=threshold, project_ids=project_ids,
            source_layers=source_layers,
        )
    return await search_similar(
        client=client, query=query, user_groups=user_groups,
        top_k=top_k, threshold=threshold, project_ids=project_ids,
        source_layers=source_layers,
    )


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
        best_chunk = max(chunks, key=lambda c: c.score)
        merged.append(
            DocumentSource(
                document_id=doc_id,
                filename=chunks[0].filename,
                score=best_chunk.score,
                text=combined_text,
                reference_number=0,  # assigned below
                chunk_indexes=[c.chunk_index for c in chunks],
                source_layer=best_chunk.source_layer,
                section=best_chunk.section,
                unit=best_chunk.unit,
                group=best_chunk.group,
                display_name=best_chunk.display_name,
                artifact_path=best_chunk.artifact_path,
                visibility=best_chunk.visibility,
                run_id=best_chunk.run_id,
                project_id=best_chunk.project_id,
            )
        )

    # Sort by score descending, limit to max_documents
    limit = max_documents if max_documents is not None else settings.max_result_documents
    merged.sort(key=lambda d: d.score, reverse=True)
    merged = merged[:limit]
    for i, doc in enumerate(merged):
        doc.reference_number = i + 1
    return merged
