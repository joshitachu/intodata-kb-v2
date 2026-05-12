from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy import delete, select

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.api.routes.auth import limiter
from src.config import settings
from src.db.models.document import Document
from src.db.models.user import ProjectAssignment
from src.ingestion.pipeline import ingest_document, ingest_text
from src.rbac.permissions import get_user_groups, get_user_project_ids
from src.retrieval.vector_store import delete_document_points
from src.schemas import IngestResponse, MarkdownIngestRequest

logger = logging.getLogger(__name__)


async def verify_ingest_api_key(
    authorization: str | None = Header(default=None),
) -> None:
    """Verify Bearer token matches MCP_INGEST_API_KEY.

    If no key is configured, the endpoint is open (backwards-compatible).
    """
    if not settings.mcp_ingest_api_key:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.mcp_ingest_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

router = APIRouter(tags=["ingest"])

ALLOWED_EXTENSIONS = {"pdf", "docx", "pptx", "xlsx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/ingest", response_model=IngestResponse, summary="Ingest a document into Qdrant")
@limiter.limit("10/minute")
async def ingest(
    request: Request,
    file: UploadFile,
    db: DBSession,
    qdrant: QdrantClient,
    user: CurrentUser,
    project_id: str = Form(...),
) -> IngestResponse:
    """Upload a document and trigger the ingestion pipeline."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: .{ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds 50 MB limit")

    # Validate project access
    user_project_ids = await get_user_project_ids(user, db)
    if user_project_ids is not None and project_id not in user_project_ids:
        raise HTTPException(status_code=403, detail="No access to this project")

    try:
        doc, job = await ingest_document(
            file_bytes=file_bytes,
            filename=file.filename,
            allowed_groups=[project_id],
            uploaded_by=user.id,
            db=db,
            qdrant=qdrant,
            project_id=project_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")

    return IngestResponse(
        document_id=doc.id,
        filename=doc.filename,
        status=job.status,
        message=f"Ingested {job.chunks_created} chunks",
    )


@router.delete(
    "/ingest/by-project/{project_id}",
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Delete all documents for a project",
)
async def delete_project_documents(
    project_id: str,
    db: DBSession,
    qdrant: QdrantClient,
) -> dict:
    """Delete all documents (and their Qdrant vectors) for a project.

    Used by the MCP server when deleting a project to clean up RAG data.
    """
    # Find all documents belonging to this project
    result = await db.execute(
        select(Document).where(Document.project_id == project_id)
    )
    docs = list(result.scalars().all())

    # Delete Qdrant vectors for each document
    for doc in docs:
        try:
            await delete_document_points(qdrant, doc.id)
        except Exception as e:
            logger.warning("Failed to delete Qdrant points for document %d: %s", doc.id, e)

    # Delete document rows (cascades to ingestion_jobs)
    if docs:
        doc_ids = [d.id for d in docs]
        await db.execute(delete(Document).where(Document.id.in_(doc_ids)))

    # Delete project_assignments for this project
    await db.execute(
        delete(ProjectAssignment).where(ProjectAssignment.project_id == project_id)
    )

    await db.commit()

    logger.info("Deleted %d documents for project %s", len(docs), project_id)
    return {"deleted_documents": len(docs), "project_id": project_id}


@router.delete(
    "/ingest/by-project/{project_id}/document/{filename}",
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Delete a single document from a project",
)
async def delete_project_document(
    project_id: str,
    filename: str,
    db: DBSession,
    qdrant: QdrantClient,
) -> dict:
    """Delete one generated document and its vectors for a project."""
    scoped_filename = f"{project_id}/{filename}"
    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.filename == scoped_filename,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        return {"deleted": False, "project_id": project_id, "filename": filename}

    try:
        await delete_document_points(qdrant, doc.id)
    except Exception as e:
        logger.warning("Failed to delete Qdrant points for document %d: %s", doc.id, e)

    await db.delete(doc)
    await db.commit()

    logger.info("Deleted document %s for project %s", scoped_filename, project_id)
    return {"deleted": True, "project_id": project_id, "filename": filename}


@router.post("/ingest/md", response_model=IngestResponse, dependencies=[Depends(verify_ingest_api_key)], summary="Ingest pre-rendered markdown content")
async def ingest_markdown(
    request: MarkdownIngestRequest,
    db: DBSession,
    qdrant: QdrantClient,
) -> IngestResponse:
    """Ingest raw markdown text directly (no file upload, requires API key).

    Used by the MCP server to push generated documentation into the RAG pipeline.
    Idempotent: re-ingesting the same filename replaces the previous version.
    """
    try:
        doc, job = await ingest_text(
            text=request.text,
            filename=request.filename,
            source=request.source,
            project_id=request.project_id,
            metadata=request.metadata,
            db=db,
            qdrant=qdrant,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Markdown ingestion failed: {e}")

    return IngestResponse(
        document_id=doc.id,
        filename=doc.filename,
        status=job.status,
        message=f"Ingested {job.chunks_created} chunks",
    )
