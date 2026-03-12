from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.api.routes.auth import limiter
from src.config import settings
from src.ingestion.pipeline import ingest_document, ingest_text
from src.rbac.permissions import get_user_groups
from src.schemas import IngestResponse, MarkdownIngestRequest


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


@router.post("/ingest", response_model=IngestResponse)
@limiter.limit("10/minute")
async def ingest(
    request: Request,
    file: UploadFile,
    db: DBSession,
    qdrant: QdrantClient,
    user: CurrentUser,
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

    user_groups = get_user_groups(user)

    try:
        doc, job = await ingest_document(
            file_bytes=file_bytes,
            filename=file.filename,
            allowed_groups=user_groups,
            uploaded_by=user.id,
            db=db,
            qdrant=qdrant,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")

    return IngestResponse(
        document_id=doc.id,
        filename=doc.filename,
        status=job.status,
        message=f"Ingested {job.chunks_created} chunks",
    )


@router.post("/ingest/md", response_model=IngestResponse, dependencies=[Depends(verify_ingest_api_key)])
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
