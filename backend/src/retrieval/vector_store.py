from __future__ import annotations

import logging
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from src.retrieval.embedder import EMBEDDING_DIM

logger = logging.getLogger(__name__)

COLLECTION_NAME = "documents"


async def ensure_collection(client: AsyncQdrantClient) -> None:
    """Create the documents collection if it doesn't exist."""
    collections = await client.get_collections()
    names = [c.name for c in collections.collections]
    if COLLECTION_NAME not in names:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        logger.info(f"Created Qdrant collection: {COLLECTION_NAME}")

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


async def upsert_chunks(
    client: AsyncQdrantClient,
    embeddings: list[list[float]],
    chunks: list[str],
    document_id: int,
    filename: str,
    allowed_groups: list[str],
) -> list[str]:
    """Store chunk embeddings in Qdrant with metadata payload. Returns point IDs."""
    points = []
    point_ids = []
    for i, (embedding, text) in enumerate(zip(embeddings, chunks)):
        point_id = str(uuid.uuid4())
        point_ids.append(point_id)
        points.append(
            PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "document_id": document_id,
                    "filename": filename,
                    "chunk_index": i,
                    "text": text,
                    "allowed_groups": allowed_groups,
                },
            )
        )

    await client.upsert(collection_name=COLLECTION_NAME, points=points)
    logger.info(f"Upserted {len(points)} chunks for document {document_id}")
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
