from __future__ import annotations

from fastapi import APIRouter, HTTPException

from sqlalchemy import func, select

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.db.models import Document
from src.retrieval.vector_store import delete_document_points
from src.schemas import DocumentListResponse, DocumentResponse

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    db: DBSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = 50,
) -> DocumentListResponse:
    """List all documents with pagination."""
    total_result = await db.execute(select(func.count(Document.id)))
    total = total_result.scalar_one()

    result = await db.execute(
        select(Document).order_by(Document.created_at.desc()).offset(skip).limit(limit)
    )
    docs = result.scalars().all()

    return DocumentListResponse(
        documents=[
            DocumentResponse(
                id=d.id,
                filename=d.filename,
                file_type=d.file_type,
                source=d.source,
                author=d.author,
                department=d.department,
                chunk_count=d.chunk_count,
                created_at=d.created_at,
            )
            for d in docs
        ],
        total=total,
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    db: DBSession,
    qdrant: QdrantClient,
    user: CurrentUser,
) -> None:
    """Delete a document and its Qdrant vectors."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove vectors from Qdrant
    await delete_document_points(qdrant, document_id)

    # Remove from database
    await db.delete(doc)
