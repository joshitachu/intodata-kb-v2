from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Mapping
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
from src.document_roles import DEFAULT_DOCUMENT_ROLE
from src.retrieval.embedder import embed_text
from src.retrieval.query_expander import expand_query
from src.retrieval.reranker import record_rerank_failure, rerank
from src.retrieval.sparse_embedder import embed_text_sparse
from src.retrieval.vector_store import COLLECTION_NAME
from src.schemas import DocumentSource, Source
from src.turn_timings import measured_retrieval, record_embedding_seconds

logger = logging.getLogger(__name__)


def _build_search_filter(
    user_groups: list[str],
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> Filter | None:
    """Build Qdrant filter combining RBAC groups, project access, ids, layer, and role.

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
        document_roles: Optional document role filter.
            None or [] → no role condition.
            Including "source" → exact source match plus is-empty for legacy chunks.
            Other roles → exact role match.
        knowledge_revisions: Active revision by project.  Each pair is matched
            together, so a query cannot combine project A's revision with
            project B's revision or read a prepared candidate.
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

    if document_roles:
        role_condition = FieldCondition(
            key="document_role",
            match=MatchAny(any=document_roles),
        )
        if DEFAULT_DOCUMENT_ROLE in document_roles:
            conditions.append(
                Filter(
                    should=[
                        role_condition,
                        IsEmptyCondition(is_empty=PayloadField(key="document_role")),
                    ]
                )
            )
        else:
            conditions.append(role_condition)

    # ``knowledge_revisions`` is intentionally distinct from ``None``.  Normal
    # request paths always pass a (possibly empty) durable active map.  This
    # includes legacy chunks that have no revision tag but excludes every
    # prepared, non-active tagged chunk.  Direct/legacy callers can pass None
    # during migration to preserve the old unfiltered behaviour.
    if knowledge_revisions is not None:
        revision_pairs = [
            Filter(
                must=[
                    FieldCondition(key="project_id", match=MatchAny(any=[project_id])),
                    FieldCondition(
                        key="knowledge_revision_id", match=MatchAny(any=[revision_id])
                    ),
                ]
            )
            for project_id, revision_id in sorted(knowledge_revisions.items())
            if project_id and revision_id
        ]
        legacy_conditions = [IsEmptyCondition(is_empty=PayloadField(key="knowledge_revision_id"))]
        active_projects = [project_id for project_id, revision_id in knowledge_revisions.items() if project_id and revision_id]
        if active_projects:
            legacy_scope = Filter(
                must=legacy_conditions,
                must_not=[FieldCondition(key="project_id", match=MatchAny(any=active_projects))],
            )
        else:
            legacy_scope = Filter(must=legacy_conditions)
        conditions.append(Filter(should=[*revision_pairs, legacy_scope]))

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
        document_role=payload.get("document_role") or DEFAULT_DOCUMENT_ROLE,
        source_layer=payload.get("source_layer"),
        section=payload.get("section"),
        unit=payload.get("unit"),
        group=payload.get("group"),
        display_name=friendly_display_name(payload["filename"], payload.get("source_layer")),
        artifact_path=payload.get("artifact_path"),
        visibility=payload.get("visibility"),
        run_id=payload.get("run_id"),
        project_id=payload.get("project_id"),
        knowledge_revision_id=payload.get("knowledge_revision_id"),
    )


@measured_retrieval
async def search_similar(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> list[Source]:
    """Embed query, search Qdrant with RBAC filtering, return sources above threshold.

    Uses dense vector search only (fallback when hybrid is disabled).
    """
    embed_start = time.perf_counter()
    query_embedding = await embed_text(query)
    embed_seconds = time.perf_counter() - embed_start
    record_embedding_seconds(embed_seconds)
    logger.info(f"Embedded query in {int(embed_seconds * 1000)}ms: {query[:80]!r}")

    search_filter = _build_search_filter(
        user_groups, document_ids, project_ids, source_layers, document_roles, knowledge_revisions,
    )
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


@measured_retrieval
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
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> list[Source]:
    """Hybrid search: dense + sparse with Reciprocal Rank Fusion (RRF).

    Uses Qdrant's Prefetch API to retrieve candidates from both indexes
    and fuse them server-side with RRF.
    """
    pf_limit = prefetch_limit if prefetch_limit is not None else settings.prefetch_limit

    embed_start = time.perf_counter()
    query_embedding = await embed_text(query)
    sparse_vector = embed_text_sparse(query)
    embed_seconds = time.perf_counter() - embed_start
    record_embedding_seconds(embed_seconds)
    logger.info(
        f"Embedded query (dense + sparse) in {int(embed_seconds * 1000)}ms: {query[:80]!r}"
    )

    search_filter = _build_search_filter(
        user_groups, document_ids, project_ids, source_layers, document_roles, knowledge_revisions,
    )
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


@measured_retrieval
async def search_and_rerank(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    rerank_candidates: int | None = None,
    prefetch_limit: int | None = None,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
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
            prefetch_limit=prefetch_limit,
            document_ids=document_ids,
            project_ids=project_ids,
            source_layers=source_layers,
            document_roles=document_roles,
            knowledge_revisions=knowledge_revisions,
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
            document_roles=document_roles,
            knowledge_revisions=knowledge_revisions,
        )

    if not candidates:
        return []

    if not settings.reranker_enabled:
        return candidates[:top_k]

    # Rerank, falling back to fusion order on API failure. The fallback is kept
    # deliberately -- degrading beats erroring out mid-turn -- but it is recorded
    # and logged at ERROR by `record_rerank_failure`, because an un-reranked
    # result set is indistinguishable from a reranked one at this boundary.
    texts = [s.text for s in candidates]
    try:
        reranked = await rerank(query, texts, top_k=top_k)
    except Exception as e:
        record_rerank_failure(e)
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
        document_roles=document_roles,
        knowledge_revisions=knowledge_revisions,
    )


def _build_neighbor_filter(
    user_groups: list[str],
    document_id: int,
    chunk_indexes: list[int],
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> Filter:
    """Build a same-document/same-project filter for neighbor chunk fetches."""
    base = _build_search_filter(
        user_groups,
        document_ids=[document_id],
        project_ids=project_ids,
        source_layers=source_layers,
        document_roles=document_roles,
        knowledge_revisions=knowledge_revisions,
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
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
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
                        document_roles=document_roles,
                        knowledge_revisions=knowledge_revisions,
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
                # exc_info: this is a swallowed infrastructure error that silently
                # narrows the context handed to the model, so the reason has to be
                # fully recoverable from the log rather than summarised to one line.
                logger.warning("Neighbor chunk expansion failed for doc=%s chunk=%s: %s",
                               src.document_id, src.chunk_index, e, exc_info=True)

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


@measured_retrieval
async def search_multi_query(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    document_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
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
                document_roles=document_roles,
                knowledge_revisions=knowledge_revisions,
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
                document_roles=document_roles,
                knowledge_revisions=knowledge_revisions,
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
            # Same contract as `search_and_rerank`: keep the fallback, refuse to
            # let it be silent.
            record_rerank_failure(e)
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
            document_roles=document_roles,
            knowledge_revisions=knowledge_revisions,
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


def _build_unit_filter(
    user_groups: list[str],
    units: list[str],
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> Filter:
    """RBAC/project/layer filter plus exact `unit` payload match.

    ``source_layers=None`` reaches ``_build_search_filter`` as the default
    canonical partition with legacy tolerance, which is what rung 1 wants; rung 2
    passes its explicit internal layers. The exact-name guarantee is orthogonal to
    which partition it applies to, so this helper takes no position on it.
    """
    base = _build_search_filter(
        user_groups, document_ids=None, project_ids=project_ids,
        source_layers=source_layers, document_roles=document_roles,
        knowledge_revisions=knowledge_revisions,
    )
    conditions = list(base.must) if base and base.must else []
    conditions.append(FieldCondition(key="unit", match=MatchAny(any=units)))
    return Filter(must=conditions)


async def fetch_unit_chunks(
    client: AsyncQdrantClient,
    user_groups: list[str],
    units: list[str],
    *,
    source_layers: list[str] | None,
    project_ids: list[str] | None = None,
    document_roles: list[str] | None = None,
    limit: int | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> list[Source]:
    """Deterministically fetch chunks whose `unit` payload matches a named unit.

    The exact-match counterpart to semantic search: when the query names a
    known job/component/unit, its chunks are retrieved by payload filter,
    not embedding similarity — an alias-matched unit can never lose the
    ranking contest. Scores are pinned to 1.0 so merged ranking keeps these
    guaranteed hits on top.

    ``source_layers`` is required but nullable, and the difference is the whole
    point of the parameter. ``None`` is the default canonical partition (rung 1,
    where a user's question about a named job should find the published document
    about it); an explicit list is a deep internal partition (rung 2). It has no
    default value because silently picking one for a caller is how this function
    would start guaranteeing hits in the wrong corpus.
    """
    if not units:
        return []
    points, _ = await client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=_build_unit_filter(
            user_groups, units, project_ids=project_ids, source_layers=source_layers,
            document_roles=document_roles,
            knowledge_revisions=knowledge_revisions,
        ),
        limit=limit if limit is not None else settings.rung2_entity_match_limit,
        with_payload=True,
        with_vectors=False,
    )
    sources = [_source_from_payload(p.payload, 1.0) for p in points]
    sources.sort(key=lambda s: (s.document_id, s.chunk_index))
    logger.info("Unit payload fetch: units=%s → %d chunks", units, len(sources))
    return sources


def _build_section_filter(
    user_groups: list[str],
    document_id: int,
    section: str,
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> Filter:
    """Same-document same-section filter under the standard RBAC constraints."""
    base = _build_search_filter(
        user_groups, document_ids=[document_id], project_ids=project_ids,
        source_layers=source_layers, document_roles=document_roles,
        knowledge_revisions=knowledge_revisions,
    )
    conditions = list(base.must) if base and base.must else []
    conditions.append(FieldCondition(key="section", match=MatchAny(any=[section])))
    return Filter(must=conditions)


async def expand_section_chunks(
    client: AsyncQdrantClient,
    sources: list[Source],
    user_groups: list[str],
    project_ids: list[str] | None = None,
    source_layers: list[str] | None = None,
    document_roles: list[str] | None = None,
    limit: int | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> list[Source]:
    """Replace each section-bearing hit with its complete owning section.

    A retrieved chunk is often a fragment of one logical technical passage;
    fetching the full section (same document_id + section payload) gives the
    LLM the whole passage in chunk_index order. Sources without section
    metadata are kept as-is (legacy chunks keep neighbor-expansion behavior
    from the upstream search). Total fetched chunks are capped.
    """
    cap = limit if limit is not None else settings.rung2_section_expansion_limit
    expanded: list[Source] = []
    seen: set[tuple[int, int]] = set()
    expanded_sections: set[tuple[int, str]] = set()
    fetched = 0

    for src in sources:
        key = (src.document_id, src.chunk_index)
        if not src.section:
            if key not in seen:
                expanded.append(src)
                seen.add(key)
            continue

        section_key = (src.document_id, src.section)
        if section_key in expanded_sections:
            if key not in seen:
                expanded.append(src)
                seen.add(key)
            continue

        section_chunks: list[Source] = []
        if fetched < cap:
            try:
                points, _ = await client.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=_build_section_filter(
                        user_groups, src.document_id, src.section,
                        project_ids=project_ids, source_layers=source_layers,
                        document_roles=document_roles,
                        knowledge_revisions=knowledge_revisions,
                    ),
                    limit=cap - fetched,
                    with_payload=True,
                    with_vectors=False,
                )
                section_chunks = sorted(
                    (_source_from_payload(p.payload, src.score) for p in points),
                    key=lambda s: s.chunk_index,
                )
                fetched += len(section_chunks)
                expanded_sections.add(section_key)
            except Exception as e:
                logger.warning(
                    "Section expansion failed for doc=%s section=%r: %s",
                    src.document_id, src.section, e, exc_info=True,
                )

        if not section_chunks:
            section_chunks = [src]
        for chunk in section_chunks:
            chunk_key = (chunk.document_id, chunk.chunk_index)
            if chunk_key not in seen:
                expanded.append(chunk)
                seen.add(chunk_key)

    logger.info("Section expansion: %d hits → %d context chunks", len(sources), len(expanded))
    return expanded


@measured_retrieval
async def search_layered(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    *,
    source_layers: list[str],
    top_k: int = settings.default_top_k,
    threshold: float = settings.default_threshold,
    project_ids: list[str] | None = None,
    matched_units: list[str] | None = None,
    document_roles: list[str] | None = None,
    knowledge_revisions: Mapping[str, str] | None = None,
) -> list[Source]:
    """Deep-rung search against explicit corpus layers.

    Used by retrieval-ladder rung 2: same RBAC/project filters as the default
    path, but with an exact ``source_layers`` filter (no legacy is-empty
    tolerance) and wider recall — forced query expansion, a larger candidate
    pool (`rung2_prefetch_limit`/`rung2_rerank_top_n`), guaranteed unit-payload
    hits for alias-matched entities, and whole-section expansion. Rung 1 and
    `/query` keep the default settings. ``source_layers`` is required — this
    helper must never fall back to the default partition.
    """
    queries = [query]
    if settings.rung2_query_expansion_enabled:
        queries = await expand_query(query, force=True)

    semantic: list[Source] = []
    seen: set[tuple[int, int]] = set()
    for q in queries:
        if settings.reranker_enabled:
            results = await search_and_rerank(
                client=client, query=q, user_groups=user_groups,
                top_k=top_k, threshold=threshold, project_ids=project_ids,
                source_layers=source_layers,
                document_roles=document_roles,
                knowledge_revisions=knowledge_revisions,
                rerank_candidates=settings.rung2_rerank_top_n,
                prefetch_limit=settings.rung2_prefetch_limit,
            )
        elif settings.hybrid_search_enabled:
            results = await search_similar_hybrid(
                client=client, query=q, user_groups=user_groups,
                top_k=top_k, threshold=threshold, project_ids=project_ids,
                source_layers=source_layers,
                document_roles=document_roles,
                prefetch_limit=settings.rung2_prefetch_limit,
                knowledge_revisions=knowledge_revisions,
            )
        else:
            results = await search_similar(
                client=client, query=q, user_groups=user_groups,
                top_k=top_k, threshold=threshold, project_ids=project_ids,
                source_layers=source_layers,
                document_roles=document_roles,
                knowledge_revisions=knowledge_revisions,
            )
        for s in results:
            key = (s.document_id, s.chunk_index)
            if key not in seen:
                seen.add(key)
                semantic.append(s)

    unit_hits: list[Source] = []
    if matched_units:
        try:
            for s in await fetch_unit_chunks(
                client, user_groups, matched_units,
                source_layers=source_layers, project_ids=project_ids,
                document_roles=document_roles,
                knowledge_revisions=knowledge_revisions,
            ):
                key = (s.document_id, s.chunk_index)
                if key not in seen:
                    seen.add(key)
                    unit_hits.append(s)
        except Exception as e:
            # The guaranteed-entity path quietly becoming best-effort is a
            # retrieval-quality event, not a formatting problem: an alias-matched
            # unit that should have been unmissable is back in the ranking contest.
            logger.error(
                "Unit payload fetch failed for units=%s: %s", matched_units, e, exc_info=True
            )

    # Guaranteed entity hits lead; semantic candidates follow in rank order.
    merged = unit_hits + semantic
    if not merged:
        return []
    return await expand_section_chunks(
        client, merged, user_groups,
        project_ids=project_ids, source_layers=source_layers,
        document_roles=document_roles,
        knowledge_revisions=knowledge_revisions,
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
                document_role=best_chunk.document_role,
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
