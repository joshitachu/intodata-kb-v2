from __future__ import annotations

import datetime
import logging
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from src.db.models import Document, IngestionJob
from src.ingestion.chunker import chunk_text
from src.ingestion.markdown_chunker import MarkdownChunk, chunk_markdown
from src.ingestion.metadata import extract_metadata
from src.ingestion.parser import parse_file
from src.ingestion.semantic_chunker import chunk_text_semantic
from src.config import settings
from src.retrieval.embedder import embed_texts
from src.retrieval.sparse_embedder import embed_texts_sparse
from src.retrieval.vector_store import delete_document_points, ensure_collection, upsert_chunks
from src.document_roles import DEFAULT_DOCUMENT_ROLE, normalize_document_role

logger = logging.getLogger(__name__)


def _scoped_filename(filename: str, project_id: str | None) -> str:
    """Prefix filename with project_id so dedup is project-scoped."""
    if project_id:
        return f"{project_id}/{filename}"
    return filename


def _build_chunk_metadatas(
    chunks: list[str] | list[MarkdownChunk],
    request_metadata: dict,
    *,
    default_source_layer: str,
    default_artifact_type: str,
) -> list[dict]:
    """Build per-chunk metadata dicts to store in Qdrant payloads.

    Merges document-level fields from request_metadata with per-chunk
    structural fields (section, unit) from MarkdownChunk objects.
    Applies defaults when the caller sends no layer/visibility metadata.
    """
    source_layer = request_metadata.get("source_layer") or default_source_layer
    visibility = request_metadata.get("visibility") or "public"
    artifact_type = request_metadata.get("artifact_type") or default_artifact_type
    document_role = normalize_document_role(request_metadata.get("document_role"))

    doc_level: dict = {
        k: v
        for k, v in {
            "source_layer": source_layer,
            "visibility": visibility,
            "artifact_type": artifact_type,
            "document_role": document_role,
            "project_id": request_metadata.get("project_id"),
            "doc_name": request_metadata.get("doc_name"),
            "group": request_metadata.get("group"),
            "artifact_path": request_metadata.get("artifact_path"),
            "run_id": request_metadata.get("run_id"),
        }.items()
        if v is not None
    }

    metadatas: list[dict] = []
    for chunk in chunks:
        if isinstance(chunk, MarkdownChunk):
            m = {**doc_level, "section": chunk.section, "unit": chunk.unit}
        else:
            m = dict(doc_level)
        metadatas.append(m)
    return metadatas


async def _replace_existing_document(
    filename: str,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
) -> None:
    """Check if a document with the same filename exists, and if so, delete it.

    Removes both the Qdrant vectors and the database record for the old document.
    """
    result = await db.execute(
        select(Document).where(Document.filename == filename)
    )
    existing = result.scalars().first()
    if existing is None:
        return

    old_id = existing.id
    old_chunks = existing.chunk_count
    logger.info(
        f"Replacing existing document '{filename}' "
        f"(id={old_id}, {old_chunks} chunks) with new version"
    )

    # Delete old vectors from Qdrant
    await delete_document_points(qdrant, old_id)

    # Delete old DB record (cascades to IngestionJob via relationship)
    await db.delete(existing)
    await db.flush()


async def ingest_document(
    file_bytes: bytes,
    filename: str,
    allowed_groups: list[str],
    uploaded_by: int | None,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    project_id: str | None = None,
    metadata: dict | None = None,
    document_role: str | None = DEFAULT_DOCUMENT_ROLE,
) -> tuple[Document, IngestionJob]:
    """Full ingestion pipeline: parse → metadata → chunk → embed → store.

    If a document with the same filename already exists, it is replaced
    (old vectors deleted from Qdrant, old DB record removed).

    Returns the created Document and IngestionJob records.
    """
    filename = _scoped_filename(filename, project_id)
    ext = Path(filename).suffix.lower().lstrip(".")
    request_metadata = dict(metadata or {})
    if document_role is not None:
        request_metadata["document_role"] = document_role
    normalized_role = normalize_document_role(request_metadata.get("document_role"))

    # 0. Replace existing document if same filename exists
    await _replace_existing_document(filename, db, qdrant)

    # 1. Create document record
    meta = extract_metadata(file_bytes, filename)
    doc = Document(
        filename=filename,
        file_type=ext,
        author=meta.get("author"),
        department=meta.get("department"),
        document_role=normalized_role,
        uploaded_by=uploaded_by,
        project_id=project_id,
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

        # 4. Chunk — markdown files use structure-aware heading-path chunker.
        doc_name = request_metadata.get("doc_name") or Path(filename).stem
        if ext == "md":
            md_chunks = chunk_markdown(text, doc_name=doc_name)
            if not md_chunks:
                raise ValueError(f"No chunks produced from {filename}")
            chunk_texts = [c.text for c in md_chunks]
            chunk_metadatas = _build_chunk_metadatas(
                md_chunks,
                request_metadata,
                default_source_layer="canonical_docs",
                default_artifact_type="uploaded_file",
            )
        else:
            chunk_texts = chunk_text_semantic(text, file_type=ext)
            if not chunk_texts:
                raise ValueError(f"No chunks produced from {filename}")
            chunk_metadatas = _build_chunk_metadatas(
                chunk_texts,
                request_metadata,
                default_source_layer="canonical_docs",
                default_artifact_type="uploaded_file",
            )

        # 5. Embed (dense)
        embeddings = await embed_texts(chunk_texts)

        # 5b. Sparse embeddings (for hybrid search)
        sparse_embeddings = None
        if settings.hybrid_search_enabled:
            sparse_embeddings = embed_texts_sparse(chunk_texts)
            logger.info(f"Generated {len(sparse_embeddings)} sparse embeddings")

        # 6. Store in Qdrant
        await ensure_collection(qdrant)
        await upsert_chunks(
            client=qdrant,
            embeddings=embeddings,
            chunks=chunk_texts,
            document_id=doc.id,
            filename=filename,
            allowed_groups=allowed_groups,
            sparse_embeddings=sparse_embeddings,
            chunk_metadatas=chunk_metadatas,
        )

        # 7. Update records
        doc.chunk_count = len(chunk_texts)
        job.status = "completed"
        job.chunks_created = len(chunk_texts)
        job.completed_at = datetime.datetime.now(datetime.timezone.utc)
        logger.info(f"Ingested {filename}: {len(chunk_texts)} chunks")

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = datetime.datetime.now(datetime.timezone.utc)
        logger.error(f"Ingestion failed for {filename}: {e}")
        raise

    return doc, job


async def ingest_text(
    text: str,
    filename: str,
    source: str,
    project_id: str,
    metadata: dict,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    document_role: str | None = None,
) -> tuple[Document, IngestionJob]:
    """Ingest raw text (markdown) — no file parsing needed.

    Used by the MCP server's /ingest/md endpoint to push generated docs into RAG.
    Idempotent: replaces existing document with same filename.
    """
    filename = _scoped_filename(filename, project_id)
    request_metadata = dict(metadata)
    if document_role is not None:
        request_metadata["document_role"] = document_role
    normalized_role = normalize_document_role(request_metadata.get("document_role"))

    # 0. Replace existing document if same filename exists
    await _replace_existing_document(filename, db, qdrant)

    # 1. Create document record
    doc = Document(
        filename=filename,
        file_type="md",
        source=source or None,
        author=request_metadata.get("author"),
        department=request_metadata.get("department"),
        document_role=normalized_role,
        project_id=project_id or None,
    )
    db.add(doc)
    await db.flush()

    # 2. Create ingestion job
    job = IngestionJob(
        document_id=doc.id,
        status="processing",
        started_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(job)
    await db.flush()

    try:
        # 3. Chunk — always md; use structure-aware heading-path chunker.
        doc_name = request_metadata.get("doc_name") or Path(filename).stem
        md_chunks = chunk_markdown(text, doc_name=doc_name)
        if not md_chunks:
            raise ValueError(f"No chunks produced from {filename}")
        chunk_texts = [c.text for c in md_chunks]
        chunk_metadatas = _build_chunk_metadatas(
            md_chunks,
            request_metadata,
            default_source_layer="canonical_docs",
            default_artifact_type="final_doc",
        )

        # 4. Embed (dense)
        embeddings = await embed_texts(chunk_texts)

        # 4b. Sparse embeddings (hybrid search)
        sparse_embeddings = None
        if settings.hybrid_search_enabled:
            sparse_embeddings = embed_texts_sparse(chunk_texts)

        # 5. Store in Qdrant
        await ensure_collection(qdrant)
        # Tag with project_id only — no "public" catch-all
        allowed_groups = [project_id] if project_id else []
        await upsert_chunks(
            client=qdrant,
            embeddings=embeddings,
            chunks=chunk_texts,
            document_id=doc.id,
            filename=filename,
            allowed_groups=allowed_groups,
            sparse_embeddings=sparse_embeddings,
            chunk_metadatas=chunk_metadatas,
        )

        # 6. Update records
        doc.chunk_count = len(chunk_texts)
        job.status = "completed"
        job.chunks_created = len(chunk_texts)
        job.completed_at = datetime.datetime.now(datetime.timezone.utc)
        logger.info(f"Ingested text {filename}: {len(chunk_texts)} chunks")

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = datetime.datetime.now(datetime.timezone.utc)
        logger.error(f"Text ingestion failed for {filename}: {e}")
        raise

    return doc, job
