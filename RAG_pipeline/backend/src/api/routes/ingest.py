from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy import delete, select

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.api.routes.auth import limiter
from src.config import settings
from src.db.models.document import Document
from src.db.models.entity_alias import EntityAlias
from src.db.models.user import ProjectAssignment
from src.document_roles import DEFAULT_DOCUMENT_ROLE, DocumentRole, normalize_document_role
from src.ingestion.pipeline import ingest_document, ingest_text
from src.retrieval.knowledge_revisions import (
    KnowledgeRevisionConflictError,
    KnowledgeRevisionNotFoundError,
    activate_knowledge_revision,
    prepare_knowledge_revision,
    rollback_knowledge_revision,
)
from src.rbac.permissions import get_user_project_ids
from src.retrieval.vector_store import delete_document_points
from src.retrieval.vector_store import clone_revision_artifact_points
from src.schemas import (
    EntityAliasReplaceRequest,
    EntityAliasReplaceResponse,
    ActiveKnowledgeRevisionResponse,
    IngestResponse,
    KnowledgeRevisionPrepareRequest,
    KnowledgeRevisionRollbackRequest,
    KnowledgeRevisionDetailResponse,
    KnowledgeRevisionReceipt,
    MarkdownIngestRequest,
)
from src.runtime.topology.models import (
    RuntimeStaticTopologySnapshotIn,
    RuntimeStaticTopologySnapshotReceipt,
)
from src.runtime.topology.repository import (
    RuntimeTopologyRepository,
    StaticTopologySnapshotConflictError,
)

# Internal support-layer docs are scoped as "{project_id}/_internal/...". The
# prefix separates them from canonical docs ("{project_id}/NN_*.md") so the
# internal lifecycle (full delete-and-repush) can never touch user-facing docs.
INTERNAL_DOC_PREFIX = "_internal/"

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


def _allows_keyless_machine_topology_ingest() -> bool:
    """Permit an intentionally keyless local/test/dev setup only.

    Production startup already rejects an empty ``MCP_INGEST_API_KEY`` in
    ``Settings.validate_production_secrets``.  This narrower request-time gate
    also fails closed for staging or any other non-local deployment, where
    startup does not apply the production-only validation.
    """
    return settings.app_env.strip().casefold() in {
        "development",
        "dev",
        "local",
        "test",
        "testing",
    }


async def verify_machine_topology_ingest_api_key(
    authorization: str | None = Header(default=None),
) -> None:
    """Authenticate topology machine ingest or disable it without a key.

    Unlike legacy document-ingest endpoints, static topology becomes runtime
    correlation evidence and must not be anonymously writable outside an
    explicit local/test/dev environment.
    """
    if settings.mcp_ingest_api_key:
        await verify_ingest_api_key(authorization)
        return
    if _allows_keyless_machine_topology_ingest():
        return
    raise HTTPException(
        status_code=503,
        detail="Machine topology ingest is disabled until MCP_INGEST_API_KEY is configured",
    )

router = APIRouter(tags=["ingest"])

ALLOWED_EXTENSIONS = {"pdf", "docx", "pptx", "xlsx", "md"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post(
    "/ingest/revisions/prepare",
    response_model=KnowledgeRevisionReceipt,
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Record an inactive RAG knowledge revision",
)
async def prepare_rag_knowledge_revision(
    request: KnowledgeRevisionPrepareRequest,
    db: DBSession,
    qdrant: QdrantClient,
) -> KnowledgeRevisionReceipt:
    """Persist a revision receipt; it is not retrievable until activation."""
    try:
        row = await prepare_knowledge_revision(
            db,
            project_id=request.project_id,
            knowledge_revision_id=request.knowledge_revision_id,
            base_knowledge_revision_id=request.base_knowledge_revision_id,
            membership_hashes=request.membership_hashes,
            deleted_logical_ids=request.deleted_logical_ids,
            aliases=request.aliases,
        )
        # The membership receipt contains inherited artifacts as well as changed
        # ones.  Copy inherited vectors under the new revision tag so R2 query
        # filtering returns a complete coherent snapshot without re-embedding.
        if request.base_knowledge_revision_id:
            from src.db.models import RagKnowledgeRevision

            base = (
                await db.execute(
                    select(RagKnowledgeRevision).where(
                        RagKnowledgeRevision.project_id == request.project_id,
                        RagKnowledgeRevision.knowledge_revision_id
                        == request.base_knowledge_revision_id,
                    )
                )
            ).scalar_one_or_none()
            if base is None:
                raise KnowledgeRevisionConflictError("base knowledge revision is not retained")
            deleted = set(request.deleted_logical_ids)
            for artifact_id, content_hash in request.membership_hashes.items():
                if (
                    artifact_id not in deleted
                    and base.membership_hashes.get(artifact_id) == content_hash
                ):
                    await clone_revision_artifact_points(
                        qdrant,
                        project_id=request.project_id,
                        source_revision_id=request.base_knowledge_revision_id,
                        target_revision_id=request.knowledge_revision_id,
                        artifact_id=artifact_id,
                    )
        await db.commit()
    except KnowledgeRevisionConflictError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return KnowledgeRevisionReceipt(
        project_id=row.project_id,
        knowledge_revision_id=row.knowledge_revision_id,
        state=row.state,
        base_knowledge_revision_id=row.base_knowledge_revision_id,
    )


@router.post(
    "/ingest/revisions/{project_id}/{knowledge_revision_id}/activate",
    response_model=KnowledgeRevisionReceipt,
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Atomically activate a prepared RAG knowledge revision",
)
async def activate_rag_knowledge_revision(
    project_id: str,
    knowledge_revision_id: str,
    db: DBSession,
) -> KnowledgeRevisionReceipt:
    """CAS-switch the revision normal retrieval is allowed to see."""
    try:
        row, previous = await activate_knowledge_revision(
            db, project_id=project_id, knowledge_revision_id=knowledge_revision_id
        )
        await db.commit()
    except KnowledgeRevisionNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeRevisionConflictError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return KnowledgeRevisionReceipt(
        project_id=row.project_id,
        knowledge_revision_id=row.knowledge_revision_id,
        state=row.state,
        base_knowledge_revision_id=row.base_knowledge_revision_id,
        previous_knowledge_revision_id=previous,
    )


@router.post(
    "/ingest/revisions/{project_id}/{knowledge_revision_id}/rollback",
    response_model=KnowledgeRevisionReceipt,
    dependencies=[Depends(verify_ingest_api_key)],
    summary="CAS-restore a retained RAG knowledge revision",
)
async def rollback_rag_knowledge_revision(
    project_id: str,
    knowledge_revision_id: str,
    request: KnowledgeRevisionRollbackRequest,
    db: DBSession,
) -> KnowledgeRevisionReceipt:
    """Restore a known-complete membership without embedding it again."""

    try:
        row, previous = await rollback_knowledge_revision(
            db,
            project_id=project_id,
            knowledge_revision_id=knowledge_revision_id,
            expected_active_revision_id=request.expected_active_revision_id,
        )
        await db.commit()
    except KnowledgeRevisionNotFoundError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeRevisionConflictError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return KnowledgeRevisionReceipt(
        project_id=row.project_id,
        knowledge_revision_id=row.knowledge_revision_id,
        state=row.state,
        base_knowledge_revision_id=row.base_knowledge_revision_id,
        previous_knowledge_revision_id=previous,
    )


@router.get(
    "/ingest/revisions/{project_id}/active",
    response_model=ActiveKnowledgeRevisionResponse,
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Read the active RAG knowledge revision for a project",
)
async def active_rag_knowledge_revision(
    project_id: str,
    db: DBSession,
) -> ActiveKnowledgeRevisionResponse:
    from src.db.models import ActiveRagKnowledgeRevision

    active = await db.get(ActiveRagKnowledgeRevision, project_id)
    return ActiveKnowledgeRevisionResponse(
        project_id=project_id,
        knowledge_revision_id=active.knowledge_revision_id if active else None,
    )


@router.get(
    "/ingest/revisions/{project_id}/{knowledge_revision_id}",
    response_model=KnowledgeRevisionDetailResponse,
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Read a prepared RAG knowledge revision receipt",
)
async def get_rag_knowledge_revision(
    project_id: str,
    knowledge_revision_id: str,
    db: DBSession,
) -> KnowledgeRevisionDetailResponse:
    from src.db.models import RagKnowledgeRevision

    row = (
        await db.execute(
            select(RagKnowledgeRevision).where(
                RagKnowledgeRevision.project_id == project_id,
                RagKnowledgeRevision.knowledge_revision_id == knowledge_revision_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="knowledge revision is not prepared")
    return KnowledgeRevisionDetailResponse(
        project_id=row.project_id,
        knowledge_revision_id=row.knowledge_revision_id,
        state=row.state,
        base_knowledge_revision_id=row.base_knowledge_revision_id,
        membership_hashes=row.membership_hashes,
        deleted_logical_ids=row.deleted_logical_ids,
        aliases=row.aliases,
    )


@router.post(
    "/ingest/runtime-static-topology",
    response_model=RuntimeStaticTopologySnapshotReceipt,
    dependencies=[Depends(verify_machine_topology_ingest_api_key)],
    summary="Ingest a bounded scanner-derived runtime topology snapshot",
)
async def ingest_runtime_static_topology(
    snapshot: RuntimeStaticTopologySnapshotIn,
    db: DBSession,
) -> RuntimeStaticTopologySnapshotReceipt:
    """Store scanner-redacted topology facts outside the document/vector path."""
    try:
        receipt = await RuntimeTopologyRepository(db).upsert_snapshot(snapshot)
    except StaticTopologySnapshotConflictError as exc:
        raise HTTPException(status_code=409, detail="Conflicting scan revision") from exc
    await db.commit()
    return receipt


@router.post("/ingest", response_model=IngestResponse, summary="Ingest a document into Qdrant")
@limiter.limit("10/minute")
async def ingest(
    request: Request,
    file: UploadFile,
    db: DBSession,
    qdrant: QdrantClient,
    user: CurrentUser,
    project_id: str = Form(...),
    document_role: DocumentRole = Form(DEFAULT_DOCUMENT_ROLE),
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
            document_role=document_role,
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

    # Delete project_assignments and entity aliases for this project
    await db.execute(
        delete(ProjectAssignment).where(ProjectAssignment.project_id == project_id)
    )
    await db.execute(
        delete(EntityAlias).where(EntityAlias.project_id == project_id)
    )

    await db.commit()

    logger.info("Deleted %d documents for project %s", len(docs), project_id)
    return {"deleted_documents": len(docs), "project_id": project_id}


@router.delete(
    "/ingest/by-project/{project_id}/internal",
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Delete all internal support-layer documents for a project",
)
async def delete_project_internal_documents(
    project_id: str,
    db: DBSession,
    qdrant: QdrantClient,
) -> dict:
    """Delete internal-layer docs (and their vectors) plus entity aliases.

    Used by the MCP server before re-pushing internal support layers: removed
    groups are invisible to the canonical-doc diff, so the internal set is
    always replaced wholesale. Canonical documents and project assignments are
    never touched.
    """
    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            # autoescape: "_" in project ids and in "_internal" are literals,
            # not LIKE wildcards — this filter is destructive and must be exact.
            Document.filename.startswith(
                f"{project_id}/{INTERNAL_DOC_PREFIX}", autoescape=True
            ),
        )
    )
    docs = list(result.scalars().all())

    for doc in docs:
        try:
            await delete_document_points(qdrant, doc.id)
        except Exception as e:
            logger.warning("Failed to delete Qdrant points for document %d: %s", doc.id, e)

    if docs:
        doc_ids = [d.id for d in docs]
        await db.execute(delete(Document).where(Document.id.in_(doc_ids)))

    alias_result = await db.execute(
        delete(EntityAlias).where(EntityAlias.project_id == project_id)
    )

    await db.commit()

    logger.info(
        "Deleted %d internal documents and %d entity aliases for project %s",
        len(docs), alias_result.rowcount, project_id,
    )
    return {
        "deleted_documents": len(docs),
        "deleted_aliases": alias_result.rowcount,
        "project_id": project_id,
    }


@router.post(
    "/ingest/entity-aliases",
    response_model=EntityAliasReplaceResponse,
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Replace all entity aliases for a project",
)
async def replace_entity_aliases(
    request: EntityAliasReplaceRequest,
    db: DBSession,
) -> EntityAliasReplaceResponse:
    """Replace the entity alias set for one project in a single transaction.

    Aliases come from the MCP server's `_facts/entity_index.csv` and
    `_facts/usage_index.csv`; they feed query-time entity lookup, never the
    vector corpus. Other projects' aliases are unaffected.
    """
    deleted = await db.execute(
        delete(EntityAlias).where(EntityAlias.project_id == request.project_id)
    )
    db.add_all(
        EntityAlias(
            project_id=request.project_id,
            alias=record.alias,
            alias_normalized=record.alias_normalized,
            canonical_name=record.canonical_name,
            entity_type=record.entity_type,
            unit_name=record.unit_name,
            source_path=record.source_path,
            source_artifact=record.source_artifact,
            confidence=record.confidence,
        )
        for record in request.aliases
    )
    await db.commit()

    logger.info(
        "Replaced entity aliases for project %s: %d deleted, %d inserted",
        request.project_id, deleted.rowcount, len(request.aliases),
    )
    return EntityAliasReplaceResponse(
        project_id=request.project_id,
        deleted=deleted.rowcount,
        inserted=len(request.aliases),
    )


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


@router.post(
    "/ingest/md",
    response_model=IngestResponse,
    dependencies=[Depends(verify_ingest_api_key)],
    summary="Ingest pre-rendered markdown content",
)
async def ingest_markdown(
    request: MarkdownIngestRequest,
    db: DBSession,
    qdrant: QdrantClient,
) -> IngestResponse:
    """Ingest raw markdown text directly (no file upload, requires API key).

    Used by the MCP server to push generated documentation into the RAG pipeline.
    Idempotent: re-ingesting the same filename replaces the previous version.
    """
    metadata = dict(request.metadata)
    raw_role = metadata.get("document_role") or request.document_role
    try:
        document_role = normalize_document_role(raw_role)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    metadata["document_role"] = document_role

    try:
        doc, job = await ingest_text(
            text=request.text,
            filename=request.filename,
            source=request.source,
            project_id=request.project_id,
            metadata=metadata,
            document_role=document_role,
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
