from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.ingestion.pipeline import ingest_document
from src.rbac.permissions import get_user_groups
from src.schemas import IngestResponse

router = APIRouter(tags=["ingest"])

ALLOWED_EXTENSIONS = {"pdf", "docx", "pptx", "xlsx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
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
