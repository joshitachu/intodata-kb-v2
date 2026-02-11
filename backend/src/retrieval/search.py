from __future__ import annotations

import logging
import time

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny

from src.retrieval.embedder import embed_text
from src.retrieval.vector_store import COLLECTION_NAME
from src.schemas import DocumentSource, Source

logger = logging.getLogger(__name__)


async def search_similar(
    client: AsyncQdrantClient,
    query: str,
    user_groups: list[str],
    top_k: int = 3,
    threshold: float = 0.4,
) -> list[Source]:
    """Embed query, search Qdrant with RBAC filtering, return sources above threshold."""
    embed_start = time.perf_counter()
    query_embedding = await embed_text(query)
    embed_ms = int((time.perf_counter() - embed_start) * 1000)
    logger.info(f"Embedded query in {embed_ms}ms: {query[:80]!r}")

    # RBAC: only return chunks where user has access
    if user_groups:
        search_filter = Filter(
            must=[
                FieldCondition(
                    key="allowed_groups",
                    match=MatchAny(any=user_groups),
                )
            ]
        )
        logger.info(f"RBAC filter: groups={user_groups} | top_k={top_k}")
    else:
        search_filter = None
        logger.info(f"RBAC filter: none (no groups) | top_k={top_k}")

    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        query_filter=search_filter,
        limit=top_k,
        with_payload=True,
    )

    raw_count = len(results.points)
    logger.info(f"Qdrant returned {raw_count} raw results:")
    for i, point in enumerate(results.points, 1):
        fname = point.payload.get("filename", "?")
        cidx = point.payload.get("chunk_index", "?")
        text_preview = point.payload.get("text", "")[:80].replace("\n", " ")
        logger.info(f"  #{i} score={point.score:.4f} {fname} chunk {cidx}: {text_preview!r}")

    sources = []
    dropped_scores: list[float] = []
    for point in results.points:
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
    max_documents: int = 1,
) -> list[DocumentSource]:
    """Merge chunk-level sources into document-level sources.

    Groups chunks by document_id, concatenates texts (sorted by chunk_index),
    keeps the highest score per document, sorts by score descending, and
    assigns sequential reference numbers starting at 1.

    Args:
        sources: Chunk-level search results.
        max_documents: Maximum number of documents to return. Defaults to 1.
    """
    from collections import defaultdict

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
    merged.sort(key=lambda d: d.score, reverse=True)
    merged = merged[:max_documents]
    for i, doc in enumerate(merged):
        doc.reference_number = i + 1
    return merged
