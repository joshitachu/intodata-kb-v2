from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request

from src.api.errors import api_error

from sqlalchemy import func, select

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.api.routes.auth import limiter
from src.db.models import Document
from src.rbac.permissions import get_user_project_ids
from src.retrieval.vector_store import delete_document_points
from src.schemas import DocumentListResponse, DocumentResponse
from src.services.audit_service import AuditService

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentListResponse, summary="List documents with RBAC filtering")
async def list_documents(
    db: DBSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
) -> DocumentListResponse:
    """List documents with RBAC filtering. Admins see all, others see only their projects."""
    project_ids = await get_user_project_ids(user, db)

    # Build base queries with optional project filter
    count_query = select(func.count(Document.id)).where(Document.deleted_at.is_(None))
    list_query = select(Document).where(Document.deleted_at.is_(None)).order_by(Document.created_at.desc())

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


@router.delete("/documents/{document_id}", status_code=204, summary="Delete a document and its vectors")
@limiter.limit("10/minute")
async def delete_document(
    request: Request,
    document_id: int,
    db: DBSession,
    qdrant: QdrantClient,
    user: CurrentUser,
) -> None:
    """Delete a document and its Qdrant vectors."""
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise api_error(404, "not_found", "Document not found")

    user_project_ids = await get_user_project_ids(user, db)
    if user_project_ids is not None and doc.project_id not in user_project_ids:
        raise api_error(403, "forbidden", "No access to this project")

    # Remove vectors from Qdrant (hard delete — content must not remain searchable)
    await delete_document_points(qdrant, document_id)

    # Soft-delete the DB row — row is preserved for audit/recovery
    doc.deleted_at = datetime.now(timezone.utc)
    await AuditService(db).log(
        user_id=user.id,
        username=user.username,
        action="document_deleted",
        resource_type="document",
        resource_id=str(document_id),
        details={"filename": doc.filename, "project_id": doc.project_id},
        ip_address=request.client.host if request.client else None,
    )
