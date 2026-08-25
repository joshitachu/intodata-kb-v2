from __future__ import annotations

import logging
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    FieldCondition,
    Filter,
    MatchAny,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from src.config import settings
from src.retrieval.embedder import get_embedding_dim
from src.retrieval.sparse_embedder import SparseVector as AppSparseVector

logger = logging.getLogger(__name__)

COLLECTION_NAME = settings.qdrant_collection


async def ensure_collection(client: AsyncQdrantClient) -> None:
    """Create the documents collection with named vectors if it doesn't exist.

    If the collection exists but has a different dense vector dimension
    (e.g. provider changed from Ollama 768-dim to Voyage AI 1024-dim),
    delete and recreate it.
    """
    dim = get_embedding_dim()
    collections = await client.get_collections()
    names = [c.name for c in collections.collections]

    if COLLECTION_NAME in names:
        info = await client.get_collection(COLLECTION_NAME)
        existing_dim = info.config.params.vectors["dense"].size
        if existing_dim != dim:
            logger.warning(
                f"Qdrant collection dimension mismatch: existing={existing_dim}, expected={dim}. "
                f"Deleting and recreating collection."
            )
            await client.delete_collection(COLLECTION_NAME)

    # (Re-)create if missing or just deleted
    collections = await client.get_collections()
    names = [c.name for c in collections.collections]
    if COLLECTION_NAME not in names:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                "dense": VectorParams(size=dim, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(),
            },
        )
        logger.info(f"Created Qdrant collection: {COLLECTION_NAME} (dim={dim})")

    # Ensure payload indexes exist (idempotent — safe to call on every startup)
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="allowed_groups",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="document_id",
        field_schema=PayloadSchemaType.INTEGER,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="source_layer",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="document_role",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="knowledge_revision_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="artifact_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="unit",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="section",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    # project_id and chunk_index are FILTERED on but were never indexed here, and
    # the production collection runs with strict_mode
    # ``unindexed_filtering_retrieve`` disabled -- so Qdrant REFUSES a retrieve
    # filtered on them rather than scanning.
    #
    # The casualty was neighbour chunk expansion: it scrolls with a chunk_index
    # filter (``search._build_neighbor_filter``), received HTTP 400 "Index
    # required but not found for chunk_index", and the caller swallows that
    # exception -- so ``neighbor_chunk_expansion_enabled = True`` was a no-op in
    # production and every answer shipped without its surrounding context. It
    # stayed invisible because strict mode is production-only: with it off,
    # development scans instead of refusing and the feature appears to work.
    #
    # chunk_index MUST be INTEGER (the filter is MatchAny over ints). A keyword
    # index does not satisfy the requirement and the 400 would persist.
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="project_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="chunk_index",
        field_schema=PayloadSchemaType.INTEGER,
    )


async def delete_collection(client: AsyncQdrantClient) -> None:
    """Delete the documents collection. Used when vector dimensions change."""
    collections = await client.get_collections()
    names = [c.name for c in collections.collections]
    if COLLECTION_NAME in names:
        await client.delete_collection(COLLECTION_NAME)
        logger.info(f"Deleted Qdrant collection: {COLLECTION_NAME}")


async def upsert_chunks(
    client: AsyncQdrantClient,
    embeddings: list[list[float]],
    chunks: list[str],
    document_id: int,
    filename: str,
    allowed_groups: list[str],
    sparse_embeddings: list[AppSparseVector] | None = None,
    chunk_metadatas: list[dict] | None = None,
    payload_filename: str | None = None,
) -> list[str]:
    """Store chunk embeddings in Qdrant with metadata payload.

    Supports both dense-only and hybrid (dense + sparse) modes.
    ``chunk_metadatas``, when provided, must be the same length as ``chunks``.
    Each dict is merged into the point payload after the base fields, so callers
    cannot accidentally override ``document_id``, ``filename``, ``chunk_index``,
    ``text``, or ``allowed_groups`` via metadata.
    Returns point IDs.
    """
    if chunk_metadatas is not None and len(chunk_metadatas) != len(chunks):
        raise ValueError(
            f"chunk_metadatas length {len(chunk_metadatas)} != chunks length {len(chunks)}"
        )

    points = []
    point_ids = []
    for i, (embedding, text) in enumerate(zip(embeddings, chunks)):
        point_id = str(uuid.uuid4())
        point_ids.append(point_id)

        vectors: dict = {"dense": embedding}
        if sparse_embeddings and i < len(sparse_embeddings):
            sv = sparse_embeddings[i]
            vectors["sparse"] = SparseVector(
                indices=sv.indices,
                values=sv.values,
            )

        base_payload: dict = {
            "document_id": document_id,
            "filename": payload_filename or filename,
            "chunk_index": i,
            "text": text,
            "allowed_groups": allowed_groups,
        }
        if chunk_metadatas is not None:
            # Caller metadata merged first; base fields overwrite to protect RBAC keys.
            payload = {**chunk_metadatas[i], **base_payload}
        else:
            payload = base_payload

        points.append(
            PointStruct(
                id=point_id,
                vector=vectors,
                payload=payload,
            )
        )

    await client.upsert(collection_name=COLLECTION_NAME, points=points)
    logger.info(
        f"Upserted {len(points)} chunks for document {document_id}"
        f" (sparse: {'yes' if sparse_embeddings else 'no'})"
    )
    return point_ids


async def clone_revision_artifact_points(
    client: AsyncQdrantClient,
    *,
    project_id: str,
    source_revision_id: str,
    target_revision_id: str,
    artifact_id: str,
) -> int:
    """Copy an unchanged artifact's vectors into a new revision without embedding.

    Deterministic UUID5 IDs make retries idempotent.  The candidate revision is
    still invisible until the SQL active-revision authority switches to it.
    """
    points, _ = await client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[
            FieldCondition(key="project_id", match=MatchAny(any=[project_id])),
            FieldCondition(
                key="knowledge_revision_id", match=MatchAny(any=[source_revision_id])
            ),
            FieldCondition(key="artifact_id", match=MatchAny(any=[artifact_id])),
        ]),
        limit=10_000,
        with_payload=True,
        with_vectors=True,
    )
    clones = []
    for point in points:
        payload = dict(point.payload)
        payload["knowledge_revision_id"] = target_revision_id
        clones.append(
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"rag-revision:{target_revision_id}:{point.id}")),
                vector=point.vector,
                payload=payload,
            )
        )
    if clones:
        await client.upsert(collection_name=COLLECTION_NAME, points=clones)
    return len(clones)


async def delete_document_points(
    client: AsyncQdrantClient, document_id: int
) -> None:
    """Delete all points for a given document."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        ),
    )
    logger.info(f"Deleted all Qdrant points for document {document_id}")
