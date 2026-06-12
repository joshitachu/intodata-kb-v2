from __future__ import annotations

import logging
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
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
        field_name="unit",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="section",
        field_schema=PayloadSchemaType.KEYWORD,
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
            "filename": filename,
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
