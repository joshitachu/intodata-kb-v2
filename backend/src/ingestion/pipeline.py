from __future__ import annotations

import datetime
import logging
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Document, IngestionJob
from src.ingestion.chunker import chunk_text
from src.ingestion.metadata import extract_metadata
from src.ingestion.parser import parse_file
from src.retrieval.embedder import embed_texts
from src.retrieval.vector_store import ensure_collection, upsert_chunks

logger = logging.getLogger(__name__)


async def ingest_document(
    file_bytes: bytes,
    filename: str,
    allowed_groups: list[str],
    uploaded_by: int | None,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
) -> tuple[Document, IngestionJob]:
    """Full ingestion pipeline: parse → metadata → chunk → embed → store.

    Returns the created Document and IngestionJob records.
    """
    ext = Path(filename).suffix.lower().lstrip(".")

    # 1. Create document record
    meta = extract_metadata(file_bytes, filename)
    doc = Document(
        filename=filename,
        file_type=ext,
        author=meta.get("author"),
        department=meta.get("department"),
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    await db.flush()  # get doc.id

    # 2. Create ingestion job
    job = IngestionJob(
        document_id=doc.id,
        status="processing",
        started_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(job)
    await db.flush()

    try:
        # 3. Parse text
        text = parse_file(file_bytes, filename)
        if not text.strip():
            raise ValueError(f"No text extracted from {filename}")

        # 4. Chunk
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError(f"No chunks produced from {filename}")

        # 5. Embed
        embeddings = await embed_texts(chunks)

        # 6. Store in Qdrant
        await ensure_collection(qdrant)
        await upsert_chunks(
            client=qdrant,
            embeddings=embeddings,
            chunks=chunks,
            document_id=doc.id,
            filename=filename,
            allowed_groups=allowed_groups,
        )

        # 7. Update records
        doc.chunk_count = len(chunks)
        job.status = "completed"
        job.chunks_created = len(chunks)
        job.completed_at = datetime.datetime.now(datetime.timezone.utc)
        logger.info(f"Ingested {filename}: {len(chunks)} chunks")

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = datetime.datetime.now(datetime.timezone.utc)
        logger.error(f"Ingestion failed for {filename}: {e}")
        raise

    return doc, job
