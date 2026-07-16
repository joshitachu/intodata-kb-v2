"""Bounded Qdrant service for optional runtime-insight semantic recall."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    Filter,
    FieldCondition,
    MatchAny,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from src.config import settings
from src.retrieval.embedder import embed_text, embed_texts, get_embedding_dim
from src.runtime.semantic.models import RuntimeSemanticResult, RuntimeSemanticSource
from src.runtime.semantic.projection import RuntimeSemanticDocument

logger = logging.getLogger(__name__)

RUNTIME_SEMANTIC_COLLECTION = f"{settings.qdrant_collection}_runtime_semantic"
MAX_RUNTIME_SEMANTIC_RESULTS = 20
DEFAULT_RUNTIME_SEMANTIC_THRESHOLD = 0.3


class RuntimeSemanticService:
    """An optional, failure-isolated semantic index over curated runtime insight rows."""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def upsert(self, documents: Sequence[RuntimeSemanticDocument]) -> bool:
        """Embed and upsert curated documents, returning false on optional-service failure."""
        if not documents:
            return True
        try:
            await self._ensure_collection()
            embeddings = await embed_texts([document.text for document in documents])
            if len(embeddings) != len(documents):
                raise RuntimeError("embedding provider returned an unexpected vector count")
            await self._client.upsert(
                collection_name=RUNTIME_SEMANTIC_COLLECTION,
                points=[
                    PointStruct(id=document.point_id, vector=embedding, payload=document.payload)
                    for document, embedding in zip(documents, embeddings)
                ],
            )
            return True
        except Exception:
            logger.warning("Runtime semantic indexing unavailable; continuing without it", exc_info=True)
            return False

    async def delete(self, point_ids: Iterable[str]) -> bool:
        """Idempotently remove known semantic point IDs without affecting runtime writes."""
        ids = list(point_ids)
        if not ids:
            return True
        try:
            await self._client.delete(
                collection_name=RUNTIME_SEMANTIC_COLLECTION,
                points_selector=PointIdsList(points=ids),
            )
            return True
        except Exception:
            logger.warning("Runtime semantic deletion unavailable; continuing without it", exc_info=True)
            return False

    async def search(
        self,
        query: str,
        *,
        project_ids: Sequence[str] | None,
        top_k: int = 8,
        threshold: float = DEFAULT_RUNTIME_SEMANTIC_THRESHOLD,
    ) -> list[RuntimeSemanticResult]:
        """Search bounded curated insight text with a server-side project filter.

        ``project_ids`` must come from authenticated RBAC scope. An empty scope
        is denied before embedding, and callers cannot supply an arbitrary filter.
        """
        if not query.strip() or project_ids == () or project_ids == []:
            return []
        limit = max(1, min(top_k, MAX_RUNTIME_SEMANTIC_RESULTS))
        try:
            embedding = await embed_text(query)
            results = await self._client.query_points(
                collection_name=RUNTIME_SEMANTIC_COLLECTION,
                query=embedding,
                query_filter=_project_filter(project_ids),
                limit=limit,
                with_payload=True,
            )
        except Exception:
            logger.warning("Runtime semantic search unavailable; using primary search only", exc_info=True)
            return []

        hits: list[RuntimeSemanticResult] = []
        for point in results.points:
            payload = point.payload or {}
            score = float(point.score)
            if score < threshold or not _safe_payload(payload):
                continue
            hits.append(
                RuntimeSemanticResult(
                    project_id=str(payload["project_id"]),
                    source=RuntimeSemanticSource(str(payload["source"])),
                    record_id=str(payload["record_id"]),
                    text=str(payload["text"]),
                    score=round(score, 4),
                    execution_id=_optional_string(payload.get("execution_id")),
                    incident_id=_optional_string(payload.get("incident_id")),
                    signature_hash=_optional_string(payload.get("signature_hash")),
                )
            )
        return hits

    async def _ensure_collection(self) -> None:
        collections = await self._client.get_collections()
        names = {collection.name for collection in collections.collections}
        if RUNTIME_SEMANTIC_COLLECTION not in names:
            await self._client.create_collection(
                collection_name=RUNTIME_SEMANTIC_COLLECTION,
                vectors_config=VectorParams(size=get_embedding_dim(), distance=Distance.COSINE),
            )
        await self._client.create_payload_index(
            collection_name=RUNTIME_SEMANTIC_COLLECTION,
            field_name="project_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        await self._client.create_payload_index(
            collection_name=RUNTIME_SEMANTIC_COLLECTION,
            field_name="source",
            field_schema=PayloadSchemaType.KEYWORD,
        )


def merge_runtime_search_results(
    fts_results: Sequence[RuntimeSemanticResult], semantic_results: Sequence[RuntimeSemanticResult]
) -> list[RuntimeSemanticResult]:
    """Merge FTS and semantic hits without dropping either retrieval provenance.

    The source identity is stable across paths. On overlap the higher score wins,
    while its provenance is the deterministic union of both paths.
    """
    merged: dict[tuple[str, RuntimeSemanticSource, str], RuntimeSemanticResult] = {}
    for result in [*fts_results, *semantic_results]:
        key = (result.project_id, result.source, result.record_id)
        prior = merged.get(key)
        if prior is None:
            merged[key] = result
            continue
        provenance = tuple(dict.fromkeys((*prior.provenance, *result.provenance)))
        preferred = result if result.score > prior.score else prior
        merged[key] = preferred.model_copy(update={"provenance": provenance})
    return sorted(merged.values(), key=lambda result: result.score, reverse=True)


def _project_filter(project_ids: Sequence[str] | None) -> Filter | None:
    if project_ids is None:
        return None
    return Filter(
        must=[FieldCondition(key="project_id", match=MatchAny(any=list(project_ids)))]
    )


def _safe_payload(payload: dict) -> bool:
    return all(payload.get(key) is not None for key in ("project_id", "source", "record_id", "text"))


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
