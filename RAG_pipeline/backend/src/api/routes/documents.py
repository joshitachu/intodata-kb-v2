from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from sqlalchemy import func, select

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.api.routes.auth import limiter
from src.db.models import Document
from src.rbac.permissions import get_user_project_ids
from src.retrieval.vector_store import delete_document_points
from src.schemas import DocumentListResponse, DocumentResponse

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    db: DBSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
) -> DocumentListResponse:
    """List documents with RBAC filtering. Admins see all, others see only their projects."""
    project_ids = await get_user_project_ids(user, db)

    # Build base queries with optional project filter
    count_query = select(func.count(Document.id))
    list_query = select(Document).order_by(Document.created_at.desc())

    if project_ids is not None:
        # Non-admin: only documents from assigned projects
        count_query = count_query.where(Document.project_id.in_(project_ids))
        list_query = list_query.where(Document.project_id.in_(project_ids))

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    result = await db.execute(list_query.offset(skip).limit(limit))
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
                project_id=d.project_id,
                chunk_count=d.chunk_count,
                created_at=d.created_at,
            )
            for d in docs
        ],
        total=total,
    )


@router.delete("/documents/{document_id}", status_code=204)
@limiter.limit("10/minute")
async def delete_document(
    request: Request,
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
