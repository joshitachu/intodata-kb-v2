from __future__ import annotations

import datetime
import hashlib
import logging
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from src.db.models import Document, DocumentTextRow, IngestionJob
from src.db.models.document_text import (
    DOCUMENT_TEXT_MAX_CHARS,
    DOCUMENT_TEXT_SOURCE_PARSED,
)
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


def _revision_storage_filename(logical_filename: str, knowledge_revision_id: str | None) -> str:
    """Keep revisions separate in SQL while retaining logical RAG citations."""
    if not knowledge_revision_id:
        return logical_filename
    return f"__knowledge_revisions__/{knowledge_revision_id}/{logical_filename}"


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
            "artifact_id": request_metadata.get("artifact_id"),
            "content_sha256": request_metadata.get("content_sha256"),
            # A prepared candidate is stored under a new revision identifier.
            # Retrieval only makes it visible after the activation authority
            # supplies the matching active revision filter.
            "knowledge_revision_id": request_metadata.get("knowledge_revision_id"),
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


async def _persist_document_text(
    document_id: int,
    text: str,
    role: str,
    db: AsyncSession,
) -> bool:
    """Keep the parsed text of every document that produced usable content.

    Role is deliberately NOT consulted. It used to be: this called
    ``should_materialize``, which excludes the evidence roles, and the table
    ended up holding 3 rows against 551 documents -- effectively unpopulated,
    and empty in exactly the place it was needed. The questions that cannot be
    answered from similarity chunks ("how many fields does this mapping have",
    "list every step") are asked about the source corpus, which is the corpus
    that rule excluded.

    ``should_materialize`` is still the right rule for a different decision --
    which documents are MOUNTED into the agent's workspace as always-reachable
    files -- and still makes it, in
    :mod:`src.harness.guidance.material_repository`. What changed is that a
    stored row no longer implies a mounted file.

    The only gate left is size, and it bounds the table rather than the agent:
    see :data:`DOCUMENT_TEXT_MAX_CHARS`. A document over the ceiling stores
    nothing rather than a prefix, because a prefix is indistinguishable from the
    whole document to the caller that reads it.

    Best-effort on purpose. A stored copy is an enhancement -- it lets an agent
    read a document whole instead of retrieving fragments of it -- while the
    chunks that make the document answerable at all are already in Qdrant by the
    time the caller commits. Failing an upload because the readable copy could
    not be written would trade a working ingestion for a missing one.

    The write runs inside a SAVEPOINT so that "best-effort" is true rather than
    merely intended: a failed INSERT leaves the enclosing transaction unusable,
    so without the savepoint the caller's commit would fail anyway and the
    swallowed error would resurface as a failed upload one step later.

    Returns True when the text was stored.
    """

    char_count = len(text)
    if char_count == 0:
        return False
    if char_count > DOCUMENT_TEXT_MAX_CHARS:
        # Loud, because it is the one case where a document is searchable and
        # not readable, and an agent asking why will get "I could not read it
        # whole" with no way to find out that a ceiling is the reason.
        logger.warning(
            "Document %d is %d chars, above the %d-char store ceiling; its text is "
            "not kept and it can only be searched",
            document_id,
            char_count,
            DOCUMENT_TEXT_MAX_CHARS,
        )
        return False

    try:
        async with db.begin_nested():
            db.add(
                DocumentTextRow(
                    document_id=document_id,
                    text=text,
                    char_count=char_count,
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    source=DOCUMENT_TEXT_SOURCE_PARSED,
                )
            )
            await db.flush()
    except (SQLAlchemyError, UnicodeError):
        # UnicodeError covers the parser handing back lone surrogates, which
        # only fail once they are encoded for the digest.
        logger.error(
            "Failed to store readable text for document %d (role=%s, chars=%d); "
            "ingestion continues without it",
            document_id,
            role,
            char_count,
            exc_info=True,
        )
        return False

    return True


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
    logical_filename = _scoped_filename(filename, project_id)
    filename = _revision_storage_filename(
        logical_filename, (metadata or {}).get("knowledge_revision_id")
    )
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

        # 4b. Keep the text itself, so the document can be read in document order
        # and not only searched in relevance order. Placed after chunking so it
        # only happens for a document that produced usable content; the old
        # document's row went with it in step 0 (FK cascade), so a re-ingest
        # replaces the stored text instead of accumulating copies.
        await _persist_document_text(doc.id, text, normalized_role, db)

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
            payload_filename=logical_filename,
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
    logical_filename = _scoped_filename(filename, project_id)
    request_metadata = dict(metadata)
    filename = _revision_storage_filename(
        logical_filename, request_metadata.get("knowledge_revision_id")
    )
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

        # 3b. Same storage rule as the upload path. A generated document pushed
        # in through the MCP server is exactly as readable-whole as an uploaded
        # one, and the policy has no reason to know which door it came through.
        await _persist_document_text(doc.id, text, normalized_role, db)

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
            payload_filename=logical_filename,
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
