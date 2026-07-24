"""Reusable documentation Packs and project-scoped capability policy Packs."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from src.api.dependencies import CurrentUser, DBSession
from src.db.models import Conversation
from src.db.models.harness_pack import HarnessPackActivationSnapshotRow, HarnessProjectPackActivationRow
from src.harness.packs.catalog import manifest_hash
from src.harness.packs.models import (
    OrganizationGovernancePolicy,
    PackManifest,
    PackPhase,
    PackRecommendation,
    PackScope,
    PackStackSelection,
    StylePresentation,
)
from src.harness.packs.project_service import (
    DEFAULT_WORKFLOW_KEY,
    GovernedProjectPackActivation,
    PackServiceError,
    ProjectPackService,
    StylePackRecord,
)
from src.harness.packs.draft_service import (
    DraftRecord,
    DraftServiceError,
    AttachmentRecord,
    PackDraftService,
    PrivateVersionRecord,
)
from src.harness.packs.builder_workflow import PackBuilderWorkflow
from src.harness.registry import base_capability_registry
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.models import DOCUMENTATION_GENERATION_CAPABILITY
from src.harness.packs.runtime import compile_governed_pack_registry
from src.harness.providers.mcp_docs import (
    McpDocumentationPack,
    McpDocumentationProvider,
    McpDocumentationProviderError,
)
from src.rbac.permissions import can_manage_project, get_user_project_ids

router = APIRouter(prefix="/harness/packs", tags=["harness-packs"])


class PackRecommendationsResponse(BaseModel):
    recommendations: list[PackRecommendation]


class PackActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    foundation_pack_ids: list[str] | None = Field(default=None, max_length=1)
    functional_pack_ids: list[str] | None = Field(default=None, max_length=100)
    style_pack_ids: list[str] | None = Field(default=None, max_length=100)
    # Compatibility input. The service classifies governed library/style ids,
    # but it cannot activate legacy custom Pack ids.
    active_pack_ids: list[str] | None = Field(default=None, max_length=100)


class StylePackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$")
    # The service validates this JSON into the strict frozen StylePresentation
    # contract. Keeping the transport field as JSON lets enum strings arrive
    # through FastAPI without weakening that domain validation.
    presentation: dict[str, Any]


class StyleQualityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_score: float = Field(ge=0, le=1)
    evaluation_score: float = Field(ge=0, le=1)
    evaluation_metadata: dict[str, Any] = Field(default_factory=dict)
    target_owner_user_id: int | None = Field(default=None, ge=1)


class GovernancePackView(BaseModel):
    id: str
    name: str
    version: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    pack_type: str
    phase: PackPhase
    authority: str
    trust: str
    mutability: str
    scope: str
    owner: str
    quality_score: float | None = None
    evaluation_score: float | None = None
    includes: tuple[str, ...]
    workflow_labels: tuple[str, ...]
    workflow_descriptions: tuple[str, ...]
    entity_labels: tuple[str, ...]
    default_domains: tuple[str, ...]
    documentation_provider_present: bool
    documentation_provider_ref: str | None = None
    blocked_override_rules: tuple[str, ...]


class StylePackResponse(BaseModel):
    pack: GovernancePackView
    presentation: StylePresentation
    project_id: str | None = None
    owner_user_id: int
    quality_score: float | None = None
    evaluation_score: float | None = None
    evaluation_metadata: dict[str, Any] | None = None
    evaluated_by_user_id: int | None = None
    review_status: str
    promotion_eligible: bool


class PackCatalogResponse(BaseModel):
    packs: list[GovernancePackView]


class GovernedActivationResponse(BaseModel):
    project_id: str
    workflow_key: str
    active_pack_ids: tuple[str, ...]
    selection: PackStackSelection
    snapshot: dict[str, Any]
    compiled_snapshot: dict[str, Any]
    presentation: StylePresentation | None = None
    packs: list[GovernancePackView]


class DocumentationPackView(BaseModel):
    id: int = Field(ge=1)
    name: str
    version: str
    description: str | None = None


class DocumentationPackSelectionResponse(BaseModel):
    project_id: str
    selected_plugin_pack_id: int | None = None
    packs: list[DocumentationPackView]


class DocumentationPackCatalogResponse(BaseModel):
    packs: list[DocumentationPackView]


class DocumentationPackSelectionRequest(BaseModel):
    plugin_pack_id: int | None = Field(default=None, ge=1)


class PersonalDocumentationPackView(BaseModel):
    id: int = Field(ge=1)
    name: str
    version: str
    description: str | None = None
    output_documents: list[str] = Field(default_factory=list)


class DocumentationPackWorkspaceResponse(BaseModel):
    project_id: str
    organization_packs: list[DocumentationPackView]
    personal_packs: list[PersonalDocumentationPackView]
    selected_organization_pack_id: int | None = None
    selected_private_version_ids: list[int] = Field(default_factory=list)


class DocumentationPackWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_pack_id: int | None = Field(default=None, ge=1)
    private_version_ids: list[int] = Field(default_factory=list, max_length=20)


class DocumentationPackGenerationRequest(DocumentationPackWorkspaceRequest):
    resume: bool = True
    investigation_model: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    write_model: str | None = Field(default=None, max_length=200)


class DocumentationPackGenerationResponse(BaseModel):
    status: str
    message: str


class PackDraftCreateRequest(BaseModel):
    conversation_id: int | None = Field(default=None, ge=1)
    name: str = Field(default="Untitled Analysis Pack", min_length=1, max_length=160)


class PackDraftMutationRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    patch: dict[str, Any]


class PackDraftRevisionRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class PackDraftSaveRequest(PackDraftRevisionRequest):
    pack_id: str | None = Field(default=None, max_length=80)


class PackVersionForkRequest(BaseModel):
    conversation_id: int | None = Field(default=None, ge=1)


class PackDraftAssistRequest(BaseModel):
    instruction: str


class PackAttachmentPurposeRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=500)


class PackProjectRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=255)


class PackPreviewRequest(PackProjectRequest):
    pass


class PackActivationV3Request(PackProjectRequest):
    workflow_key: str = Field(
        default="documentation.generate", pattern=r"^[a-z][a-z0-9_.-]{0,79}$"
    )


class PackDraftResponse(BaseModel):
    id: int
    conversation_id: int | None
    owner_user_id: int
    status: str
    revision: int
    name: str
    artifact: dict[str, Any]
    validation: dict[str, Any]
    attachments: list["PackAttachmentResponse"]


class PackDraftAssistResponse(BaseModel):
    draft: PackDraftResponse
    message: str


class PackAttachmentResponse(BaseModel):
    id: int
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str
    content_type: str
    size_bytes: int
    purpose: str


class PrivateAnalysisPackVersionResponse(BaseModel):
    id: int
    pack_id: str
    owner_user_id: int
    name: str
    version: str
    content_hash: str
    artifact: dict[str, Any]
    receipt: dict[str, Any]
    attachments: list[PackAttachmentResponse]


async def _require_project_access(user: CurrentUser, db: DBSession, project_id: str) -> None:
    scope = await get_user_project_ids(user, db)
    if scope is not None and project_id not in scope:
        raise HTTPException(status_code=404, detail="Project not found")


async def _require_project_management(user: CurrentUser, db: DBSession, project_id: str) -> None:
    await _require_project_access(user, db, project_id)
    if not await can_manage_project(user, db, project_id):
        raise HTTPException(status_code=403, detail="Project management permission required")


def _service(db: DBSession) -> ProjectPackService:
    return ProjectPackService(db, base_capability_registry())


def _draft_service(db: DBSession) -> PackDraftService:
    return PackDraftService(db, base_capability_registry())


def _draft_response(record: DraftRecord) -> PackDraftResponse:
    return PackDraftResponse(
        **{
            key: value
            for key, value in record.__dict__.items()
            if key not in {"attachments", "discovery_context"}
        },
        attachments=[_attachment_response(item) for item in record.attachments],
    )


def _attachment_response(record: AttachmentRecord) -> PackAttachmentResponse:
    return PackAttachmentResponse(
        id=record.id,
        sha256=record.sha256,
        filename=record.filename,
        content_type=record.content_type,
        size_bytes=record.size_bytes,
        purpose=record.purpose,
    )


def _private_version_response(record: PrivateVersionRecord) -> PrivateAnalysisPackVersionResponse:
    return PrivateAnalysisPackVersionResponse(
        id=record.id, pack_id=record.pack_id, owner_user_id=record.owner_user_id,
        name=record.name, version=record.version, content_hash=record.content_hash,
        artifact=record.artifact.model_dump(mode="json"), receipt=record.receipt,
        attachments=[_attachment_response(item) for item in record.attachments],
    )


def _governance_view(manifest: PackManifest) -> GovernancePackView:
    governance = manifest.governance
    if governance is None:
        raise HTTPException(status_code=409, detail="Legacy Pack has no governed API representation")
    quality = governance.quality
    return GovernancePackView(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        content_hash=manifest_hash(manifest),
        pack_type=manifest.pack_type.value,
        phase=governance.phase,
        authority=governance.authority.value,
        trust=governance.trust.value,
        mutability=governance.mutability.value,
        scope=governance.scope.value,
        owner=governance.owner.value,
        quality_score=quality.quality_score if quality else None,
        evaluation_score=quality.evaluation_score if quality else None,
        includes=manifest.includes,
        workflow_labels=tuple(workflow.label for workflow in manifest.workflows),
        workflow_descriptions=tuple(workflow.description for workflow in manifest.workflows),
        entity_labels=tuple(entity.label for entity in manifest.entities),
        default_domains=manifest.default_scopes.domains,
        documentation_provider_present=manifest.documentation_provider_ref is not None,
        documentation_provider_ref=manifest.documentation_provider_ref,
        blocked_override_rules=(
            "organization policy is mandatory",
            "foundation and functional Packs are immutable",
            "Style Packs cannot override semantic, evidence, policy, permission, or capability content",
        ),
    )


def _style_pack_response(record: StylePackRecord) -> StylePackResponse:
    assert record.manifest.style is not None
    return StylePackResponse(
        pack=_governance_view(record.manifest),
        presentation=record.manifest.style,
        project_id=record.project_id,
        owner_user_id=record.owner_user_id,
        quality_score=record.quality_score,
        evaluation_score=record.evaluation_score,
        evaluation_metadata=record.evaluation_metadata,
        evaluated_by_user_id=record.evaluated_by_user_id,
        review_status=record.review_status,
        promotion_eligible=record.promotion_eligible,
    )


def _activation_response(value: GovernedProjectPackActivation) -> GovernedActivationResponse:
    return GovernedActivationResponse(
        project_id=value.project_id,
        workflow_key=value.workflow_key,
        active_pack_ids=value.active_pack_ids,
        selection=value.selection,
        snapshot=value.snapshot.model_dump(mode="json"),
        compiled_snapshot=value.compiled_snapshot.model_dump(mode="json"),
        presentation=value.presentation,
        packs=[_governance_view(manifest) for manifest in value.manifests],
    )


async def _raise_pack_error(db: DBSession, exc: PackServiceError) -> None:
    await db.rollback()
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


async def _raise_draft_error(db: DBSession, exc: DraftServiceError) -> None:
    await db.rollback()
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _documentation_pack_view(pack: McpDocumentationPack) -> DocumentationPackView:
    return DocumentationPackView(
        id=pack.id,
        name=pack.name,
        version=pack.version,
        description=pack.description,
    )


def _personal_documentation_pack_view(version: PrivateVersionRecord) -> PersonalDocumentationPackView:
    return PersonalDocumentationPackView(
        id=version.id,
        name=version.name,
        version=version.version,
        description=version.artifact.purpose,
        output_documents=[document.title for document in version.artifact.output_documents],
    )


async def _latest_personal_pack_versions(db: DBSession, user_id: int) -> list[PrivateVersionRecord]:
    versions = await _draft_service(db).list_versions(user_id)
    latest: dict[str, PrivateVersionRecord] = {}
    for version in versions:
        latest.setdefault(version.pack_id, version)
    return list(latest.values())


async def _replace_private_documentation_packs(
    *,
    db: DBSession,
    user_id: int,
    project_id: str,
    version_ids: list[int],
    workflow_key: str = "documentation.generate",
) -> list[int]:
    if len(version_ids) != len(set(version_ids)):
        raise DraftServiceError("A personal Pack can only be selected once")
    draft_service = _draft_service(db)
    versions: list[PrivateVersionRecord] = []
    for version_id in version_ids:
        version = await draft_service.get_version(version_id, user_id)
        compatibility = await draft_service.compatibility(version_id, user_id, project_id)
        if not compatibility["compatible"]:
            raise DraftServiceError(
                f"{version.name} requires unavailable capabilities: "
                + ", ".join(compatibility["missing_required"])
            )
        versions.append(version)

    service = _service(db)
    current = (await db.execute(
        select(HarnessProjectPackActivationRow).where(
            HarnessProjectPackActivationRow.project_id == project_id,
            HarnessProjectPackActivationRow.workflow_key == workflow_key,
        )
    )).scalar_one_or_none()
    if current is None:
        await service.activate(
            project_id=project_id,
            workflow_key=workflow_key,
            user_id=user_id,
        )
        current = (await db.execute(
            select(HarnessProjectPackActivationRow).where(
                HarnessProjectPackActivationRow.project_id == project_id,
                HarnessProjectPackActivationRow.workflow_key == workflow_key,
            )
        )).scalar_one()
    base = await db.get(HarnessPackActivationSnapshotRow, current.snapshot_id)
    if base is None:
        raise PackServiceError("Documentation Pack baseline is unavailable")

    private_receipts = sorted(
        [
            {
                "version_id": version.id,
                "pack_id": version.pack_id,
                "version": version.version,
                "content_hash": version.content_hash,
                "audience_render": version.receipt.get("audience_render", {}),
            }
            for version in versions
        ],
        key=lambda item: str(item["pack_id"]),
    )
    governed_snapshot_hash = str(base.compiled_stack_hash or base.snapshot_hash)
    exact_hash = hashlib.sha256(json.dumps(
        {
            "governed_snapshot_hash": governed_snapshot_hash,
            "private_version_receipts": private_receipts,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()).hexdigest()
    snapshot = (await db.execute(
        select(HarnessPackActivationSnapshotRow).where(
            HarnessPackActivationSnapshotRow.snapshot_hash == exact_hash
        )
    )).scalar_one_or_none()
    if snapshot is None:
        snapshot = HarnessPackActivationSnapshotRow(
            project_id=base.project_id,
            workflow_key=workflow_key,
            active_pack_ids=base.active_pack_ids,
            resolved_snapshot=base.resolved_snapshot,
            resolved_manifests=base.resolved_manifests,
            snapshot_hash=exact_hash,
            stack_selection=base.stack_selection,
            compiled_stack_snapshot=base.compiled_stack_snapshot,
            compiled_stack_hash=base.compiled_stack_hash,
            compiled_presentation=base.compiled_presentation,
            private_version_receipts=private_receipts,
            created_by_user_id=user_id,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        db.add(snapshot)
        await db.flush()
    current.snapshot_id = snapshot.id
    current.updated_by_user_id = user_id
    current.updated_at = datetime.datetime.now(datetime.UTC)
    await db.commit()
    return [version.id for version in versions]


async def _current_private_version_ids(
    db: DBSession, project_id: str, workflow_key: str = "documentation.generate"
) -> list[int]:
    current = (await db.execute(
        select(HarnessProjectPackActivationRow).where(
            HarnessProjectPackActivationRow.project_id == project_id,
            HarnessProjectPackActivationRow.workflow_key == workflow_key,
        )
    )).scalar_one_or_none()
    if current is None:
        return []
    snapshot = await db.get(HarnessPackActivationSnapshotRow, current.snapshot_id)
    if snapshot is None or not isinstance(snapshot.private_version_receipts, list):
        return []
    return sorted(
        int(item["version_id"])
        for item in snapshot.private_version_receipts
        if isinstance(item, dict) and isinstance(item.get("version_id"), int)
    )


@router.get(
    "/documentation-packs",
    response_model=DocumentationPackCatalogResponse,
)
async def list_documentation_packs(
    request: Request,
    user: CurrentUser,
) -> DocumentationPackCatalogResponse:
    """Return reusable IntoDoc documentation Packs visible in this workspace."""
    del user
    provider = McpDocumentationProvider()
    try:
        packs = await provider.list_documentation_packs(
            access_token=request.cookies.get("access_token")
        )
    except McpDocumentationProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from exc
    return DocumentationPackCatalogResponse(
        packs=[_documentation_pack_view(pack) for pack in packs]
    )


@router.get(
    "/projects/{project_id}/documentation-pack",
    response_model=DocumentationPackSelectionResponse,
)
async def get_documentation_pack_selection(
    project_id: str,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> DocumentationPackSelectionResponse:
    """Return the selected MCP swap pack and safe installed-pack metadata."""
    await _require_project_access(user, db, project_id)
    provider = McpDocumentationProvider()
    try:
        packs, selected_plugin_pack_id = await asyncio.gather(
            provider.list_documentation_packs(
                access_token=request.cookies.get("access_token")
            ),
            provider.get_project_documentation_pack_id(
                project_id=project_id,
                access_token=request.cookies.get("access_token"),
            ),
        )
    except McpDocumentationProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from exc
    return DocumentationPackSelectionResponse(
        project_id=project_id,
        selected_plugin_pack_id=selected_plugin_pack_id,
        packs=[_documentation_pack_view(pack) for pack in packs],
    )


@router.put(
    "/projects/{project_id}/documentation-pack",
    response_model=DocumentationPackSelectionResponse,
)
async def set_documentation_pack_selection(
    project_id: str,
    payload: DocumentationPackSelectionRequest,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> DocumentationPackSelectionResponse:
    """Set the MCP plugin-pack default used by subsequent document generation."""
    await _require_project_management(user, db, project_id)
    provider = McpDocumentationProvider()
    try:
        selected_plugin_pack_id = await provider.set_project_documentation_pack(
            project_id=project_id,
            plugin_pack_id=payload.plugin_pack_id,
            access_token=request.cookies.get("access_token"),
        )
        packs = await provider.list_documentation_packs(
            access_token=request.cookies.get("access_token")
        )
    except McpDocumentationProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from exc
    return DocumentationPackSelectionResponse(
        project_id=project_id,
        selected_plugin_pack_id=selected_plugin_pack_id,
        packs=[_documentation_pack_view(pack) for pack in packs],
    )


@router.get(
    "/projects/{project_id}/workspace",
    response_model=DocumentationPackWorkspaceResponse,
)
async def get_documentation_pack_workspace(
    project_id: str,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> DocumentationPackWorkspaceResponse:
    """Return the complete user-facing Documentation Pack selection."""
    await _require_project_access(user, db, project_id)
    provider = McpDocumentationProvider()
    try:
        organization_packs, selected_organization_pack_id, personal_versions = await asyncio.gather(
            provider.list_documentation_packs(access_token=request.cookies.get("access_token")),
            provider.get_project_documentation_pack_id(
                project_id=project_id,
                access_token=request.cookies.get("access_token"),
            ),
            _latest_personal_pack_versions(db, user.id),
        )
        selected_private_version_ids = await _current_private_version_ids(db, project_id)
    except McpDocumentationProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from exc
    return DocumentationPackWorkspaceResponse(
        project_id=project_id,
        organization_packs=[_documentation_pack_view(pack) for pack in organization_packs],
        personal_packs=[_personal_documentation_pack_view(version) for version in personal_versions],
        selected_organization_pack_id=selected_organization_pack_id,
        selected_private_version_ids=selected_private_version_ids,
    )


@router.put(
    "/projects/{project_id}/workspace",
    response_model=DocumentationPackWorkspaceResponse,
)
async def set_documentation_pack_workspace(
    project_id: str,
    payload: DocumentationPackWorkspaceRequest,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> DocumentationPackWorkspaceResponse:
    """Select one organization baseline and additive personal Pack versions."""
    await _require_project_management(user, db, project_id)
    provider = McpDocumentationProvider()
    try:
        organization_packs = await provider.list_documentation_packs(
            access_token=request.cookies.get("access_token")
        )
        allowed_ids = {pack.id for pack in organization_packs}
        if payload.organization_pack_id is not None and payload.organization_pack_id not in allowed_ids:
            raise HTTPException(status_code=409, detail="Selected organization Pack is unavailable")
        selected_organization_pack_id = await provider.set_project_documentation_pack(
            project_id=project_id,
            plugin_pack_id=payload.organization_pack_id,
            access_token=request.cookies.get("access_token"),
        )
        selected_private_version_ids = await _replace_private_documentation_packs(
            db=db,
            user_id=user.id,
            project_id=project_id,
            version_ids=payload.private_version_ids,
        )
        personal_versions = await _latest_personal_pack_versions(db, user.id)
    except McpDocumentationProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from exc
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)
    return DocumentationPackWorkspaceResponse(
        project_id=project_id,
        organization_packs=[_documentation_pack_view(pack) for pack in organization_packs],
        personal_packs=[_personal_documentation_pack_view(version) for version in personal_versions],
        selected_organization_pack_id=selected_organization_pack_id,
        selected_private_version_ids=selected_private_version_ids,
    )


@router.post(
    "/projects/{project_id}/generate",
    response_model=DocumentationPackGenerationResponse,
)
async def generate_with_documentation_packs(
    project_id: str,
    payload: DocumentationPackGenerationRequest,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> DocumentationPackGenerationResponse:
    """Persist the visible selection and generate through the governed Nexus bridge."""
    await _require_project_management(user, db, project_id)
    provider = McpDocumentationProvider()
    try:
        organization_packs = await provider.list_documentation_packs(
            access_token=request.cookies.get("access_token")
        )
        if (
            payload.organization_pack_id is not None
            and payload.organization_pack_id not in {pack.id for pack in organization_packs}
        ):
            raise HTTPException(status_code=409, detail="Selected organization Pack is unavailable")
        await provider.set_project_documentation_pack(
            project_id=project_id,
            plugin_pack_id=payload.organization_pack_id,
            access_token=request.cookies.get("access_token"),
        )
        await _replace_private_documentation_packs(
            db=db,
            user_id=user.id,
            project_id=project_id,
            version_ids=payload.private_version_ids,
        )
        pack_service = _service(db)
        compiled = await pack_service.resolve_governed_for_execution(
            project_id, "documentation.generate"
        )
        if compiled is None:
            raise PackServiceError("Documentation Pack baseline is unavailable")
        private = await pack_service.resolve_private_analysis_for_execution(
            project_id, "documentation.generate"
        )
        registry = compile_governed_pack_registry(
            base_capability_registry(),
            compiled,
            tuple(item.provider_payload() for item in private),
        )
        observation = await HarnessCapabilityExecutor(
            registry, docs_provider=provider
        ).execute(
            CapabilityExecutionRequest(
                capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
                arguments={
                    "project_id": project_id,
                    "resume": payload.resume,
                    "investigation_model": payload.investigation_model,
                    "model": payload.model,
                    "write_model": payload.write_model,
                    "plugin_pack_id": payload.organization_pack_id,
                },
            ),
            CapabilityExecutionContext(
                user_id=user.id,
                project_scope=[project_id],
                access_token=request.cookies.get("access_token"),
            ),
        )
    except McpDocumentationProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from exc
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)
    if not observation.succeeded:
        raise HTTPException(
            status_code=502,
            detail=observation.error_message or "Documentation generation could not be started",
        )
    return DocumentationPackGenerationResponse(
        status="generating",
        message=observation.summary or f"Documentation generation started for {project_id}.",
    )


@router.post("/documentation-packs", status_code=status.HTTP_410_GONE)
async def create_documentation_pack(
    request: Request,
    user: CurrentUser,
) -> None:
    """Retired: certified library Packs are reviewed and file/Git-backed only."""
    del request, user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Pack creation is governed. Submit a file/Git change for PR certification; arbitrary instructions are not accepted.",
    )


@router.get("/projects/{project_id}/recommendations", response_model=PackRecommendationsResponse)
async def recommend_packs(project_id: str, user: CurrentUser, db: DBSession) -> PackRecommendationsResponse:
    await _require_project_access(user, db, project_id)
    recommendations = await _service(db).recommendations(project_id, user.id)
    return PackRecommendationsResponse(recommendations=list(recommendations))


@router.get("/policy", response_model=OrganizationGovernancePolicy)
async def get_organization_governance_policy(
    user: CurrentUser, db: DBSession
) -> OrganizationGovernancePolicy:
    """Read the mandatory organization policy. Mutation is intentionally admin-only/off-route."""
    del user
    try:
        return await _service(db).organization_policy()
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


# Task-oriented V3 private Analysis Pack Builder APIs.  These deliberately sit
# beside, rather than mutate, the immutable certified/style Pack library.
@router.post("/builder/drafts", response_model=PackDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_pack_draft(
    payload: PackDraftCreateRequest, user: CurrentUser, db: DBSession
) -> PackDraftResponse:
    if payload.conversation_id is not None:
        conversation = await db.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.user_id != user.id or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return _draft_response(await _draft_service(db).create_draft(
            conversation_id=payload.conversation_id, owner_user_id=user.id, name=payload.name
        ))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post(
    "/builder/drafts/{draft_id}/assist", response_model=PackDraftAssistResponse
)
async def assist_pack_draft(
    draft_id: int, payload: PackDraftAssistRequest, user: CurrentUser, db: DBSession
) -> PackDraftAssistResponse:
    """Assist one active workbench draft without creating chat records."""
    try:
        draft, message = await PackBuilderWorkflow().run_for_draft(
            service=_draft_service(db),
            draft_id=draft_id,
            owner_user_id=user.id,
            query=payload.instruction,
        )
        return PackDraftAssistResponse(draft=_draft_response(draft), message=message)
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.get("/builder/drafts/{draft_id}", response_model=PackDraftResponse)
async def get_pack_draft(draft_id: int, user: CurrentUser, db: DBSession) -> PackDraftResponse:
    try:
        return _draft_response(await _draft_service(db).get_draft(draft_id, user.id))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.patch("/builder/drafts/{draft_id}", response_model=PackDraftResponse)
async def mutate_pack_draft(
    draft_id: int, payload: PackDraftMutationRequest, user: CurrentUser, db: DBSession
) -> PackDraftResponse:
    try:
        return _draft_response(await _draft_service(db).mutate(
            draft_id=draft_id,
            owner_user_id=user.id,
            expected_revision=payload.expected_revision,
            patch=payload.patch,
            name=payload.name,
        ))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/drafts/{draft_id}/attachments", response_model=PackAttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_pack_draft_attachment(
    draft_id: int,
    user: CurrentUser,
    db: DBSession,
    file: UploadFile = File(...),
    purpose: str = Form(...),
) -> PackAttachmentResponse:
    try:
        content = await file.read(10 * 1024 * 1024 + 1)
        return _attachment_response(await _draft_service(db).upload_attachment(
            draft_id=draft_id,
            owner_user_id=user.id,
            filename=file.filename,
            content=content,
            purpose=purpose,
        ))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)
    finally:
        await file.close()


@router.patch("/builder/drafts/{draft_id}/attachments/{attachment_id}", response_model=PackAttachmentResponse)
async def update_pack_draft_attachment(
    draft_id: int, attachment_id: int, payload: PackAttachmentPurposeRequest, user: CurrentUser, db: DBSession
) -> PackAttachmentResponse:
    try:
        return _attachment_response(await _draft_service(db).update_attachment_purpose(
            draft_id=draft_id, attachment_id=attachment_id, owner_user_id=user.id, purpose=payload.purpose
        ))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.delete("/builder/drafts/{draft_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pack_draft_attachment(
    draft_id: int, attachment_id: int, user: CurrentUser, db: DBSession
) -> Response:
    try:
        await _draft_service(db).delete_attachment(
            draft_id=draft_id, attachment_id=attachment_id, owner_user_id=user.id
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.get("/builder/drafts/{draft_id}/attachments/{attachment_id}/content")
async def get_pack_draft_attachment_content(
    draft_id: int, attachment_id: int, user: CurrentUser, db: DBSession
) -> Response:
    try:
        attachment, content = await _draft_service(db).draft_attachment_content(
            draft_id=draft_id, attachment_id=attachment_id, owner_user_id=user.id
        )
        return Response(
            content=content,
            media_type=attachment.content_type,
            headers={
                "Content-Disposition": f'inline; filename="{attachment.filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/drafts/{draft_id}/validate", response_model=PackDraftResponse)
async def validate_pack_draft(draft_id: int, user: CurrentUser, db: DBSession) -> PackDraftResponse:
    try:
        return _draft_response(await _draft_service(db).validate(draft_id, user.id))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/drafts/{draft_id}/save", response_model=PrivateAnalysisPackVersionResponse, status_code=status.HTTP_201_CREATED)
async def save_pack_draft(
    draft_id: int, payload: PackDraftSaveRequest, user: CurrentUser, db: DBSession
) -> PrivateAnalysisPackVersionResponse:
    try:
        return _private_version_response(await _draft_service(db).save(
            draft_id=draft_id, owner_user_id=user.id, expected_revision=payload.expected_revision, pack_id=payload.pack_id
        ))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/drafts/{draft_id}/abandon", response_model=PackDraftResponse)
async def abandon_pack_draft(
    draft_id: int, payload: PackDraftRevisionRequest, user: CurrentUser, db: DBSession
) -> PackDraftResponse:
    try:
        return _draft_response(await _draft_service(db).abandon(
            draft_id=draft_id, owner_user_id=user.id, expected_revision=payload.expected_revision
        ))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.get("/builder/private", response_model=list[PrivateAnalysisPackVersionResponse])
async def list_private_analysis_pack_versions(user: CurrentUser, db: DBSession) -> list[PrivateAnalysisPackVersionResponse]:
    return [_private_version_response(item) for item in await _draft_service(db).list_versions(user.id)]


@router.get("/builder/private/{version_id}", response_model=PrivateAnalysisPackVersionResponse)
async def get_private_analysis_pack_version(version_id: int, user: CurrentUser, db: DBSession) -> PrivateAnalysisPackVersionResponse:
    try:
        return _private_version_response(await _draft_service(db).get_version(version_id, user.id))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/private/{version_id}/drafts", response_model=PackDraftResponse, status_code=status.HTTP_201_CREATED)
async def fork_private_analysis_pack_version(
    version_id: int, payload: PackVersionForkRequest, user: CurrentUser, db: DBSession
) -> PackDraftResponse:
    if payload.conversation_id is not None:
        conversation = await db.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.user_id != user.id or conversation.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return _draft_response(await _draft_service(db).fork_version(
            version_id=version_id, conversation_id=payload.conversation_id, owner_user_id=user.id
        ))
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.get("/builder/private/{version_id}/attachments/{attachment_id}/content")
async def get_private_analysis_pack_attachment_content(
    version_id: int, attachment_id: int, user: CurrentUser, db: DBSession
) -> Response:
    try:
        attachment, content = await _draft_service(db).version_attachment_content(
            version_id=version_id, attachment_id=attachment_id, owner_user_id=user.id
        )
        return Response(
            content=content,
            media_type=attachment.content_type,
            headers={
                "Content-Disposition": f'inline; filename="{attachment.filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/private/{version_id}/compatibility")
async def compatibility_private_analysis_pack(
    version_id: int, payload: PackProjectRequest, user: CurrentUser, db: DBSession
) -> dict[str, Any]:
    await _require_project_access(user, db, payload.project_id)
    try:
        return await _draft_service(db).compatibility(version_id, user.id, payload.project_id)
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/private/{version_id}/preview")
async def preview_private_analysis_pack(
    version_id: int, payload: PackPreviewRequest, user: CurrentUser, db: DBSession
) -> dict[str, Any]:
    await _require_project_access(user, db, payload.project_id)
    try:
        return await _draft_service(db).preview(
            version_id=version_id, owner_user_id=user.id, project_id=payload.project_id,
            # Preview has no provider adapters or write approvals. The guarded
            # executor therefore proves the allow-list without side effects.
            executor=HarnessCapabilityExecutor(base_capability_registry()),
        )
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/private/{version_id}/share")
async def share_private_analysis_pack(
    version_id: int, payload: PackProjectRequest, user: CurrentUser, db: DBSession
) -> dict[str, Any]:
    await _require_project_management(user, db, payload.project_id)
    try:
        return await _draft_service(db).share(version_id, user.id, payload.project_id)
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/private/{version_id}/promotion-proposals", status_code=status.HTTP_201_CREATED)
async def propose_private_analysis_pack_promotion(version_id: int, user: CurrentUser, db: DBSession) -> dict[str, Any]:
    try:
        return await _draft_service(db).propose_promotion(version_id, user.id)
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)


@router.post("/builder/private/{version_id}/activate")
async def activate_private_analysis_pack(
    version_id: int, payload: PackActivationV3Request, user: CurrentUser, db: DBSession
) -> dict[str, Any]:
    """Pin an exact private version beside a governed project stack snapshot."""
    await _require_project_management(user, db, payload.project_id)
    draft_service = _draft_service(db)
    try:
        version = await draft_service.get_version(version_id, user.id)
        compatibility = await draft_service.compatibility(version_id, user.id, payload.project_id)
        if not compatibility["compatible"]:
            raise DraftServiceError("Required capabilities are unavailable: " + ", ".join(compatibility["missing_required"]))
        current = (await db.execute(
            select(HarnessProjectPackActivationRow).where(
                HarnessProjectPackActivationRow.project_id == payload.project_id,
                HarnessProjectPackActivationRow.workflow_key == payload.workflow_key,
            )
        )).scalar_one_or_none()
        if current is None:
            # Establish the mandatory governed baseline once. Existing governed
            # selections are never recomputed or replaced by private activation.
            await _service(db).activate(
                project_id=payload.project_id,
                workflow_key=payload.workflow_key,
                user_id=user.id,
            )
            current = (await db.execute(
                select(HarnessProjectPackActivationRow).where(
                    HarnessProjectPackActivationRow.project_id == payload.project_id,
                    HarnessProjectPackActivationRow.workflow_key == payload.workflow_key,
                )
            )).scalar_one()
        base = await db.get(HarnessPackActivationSnapshotRow, current.snapshot_id)
        assert base is not None
        receipt = {
            "version_id": version.id, "pack_id": version.pack_id, "version": version.version,
            "content_hash": version.content_hash, "audience_render": version.receipt.get("audience_render", {}),
        }
        receipts_by_pack = {
            str(item.get("pack_id")): item
            for item in (base.private_version_receipts or [])
            if isinstance(item, dict) and item.get("pack_id")
        }
        receipts_by_pack[version.pack_id] = receipt
        private_receipts = sorted(receipts_by_pack.values(), key=lambda item: str(item["pack_id"]))
        if len(private_receipts) > 20:
            raise DraftServiceError("At most 20 private Analysis Packs may be active")
        governed_snapshot_hash = str(base.compiled_stack_hash or base.snapshot_hash)
        exact_hash = hashlib.sha256(json.dumps(
            {"governed_snapshot_hash": governed_snapshot_hash, "private_version_receipts": private_receipts},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()).hexdigest()
        snapshot = (await db.execute(
            select(HarnessPackActivationSnapshotRow).where(
                HarnessPackActivationSnapshotRow.snapshot_hash == exact_hash
            )
        )).scalar_one_or_none()
        if snapshot is None:
            snapshot = HarnessPackActivationSnapshotRow(
                project_id=base.project_id, workflow_key=base.workflow_key,
                active_pack_ids=base.active_pack_ids, resolved_snapshot=base.resolved_snapshot,
                resolved_manifests=base.resolved_manifests, snapshot_hash=exact_hash,
                stack_selection=base.stack_selection, compiled_stack_snapshot=base.compiled_stack_snapshot,
                compiled_stack_hash=base.compiled_stack_hash, compiled_presentation=base.compiled_presentation,
                private_version_receipts=private_receipts, created_by_user_id=user.id,
                created_at=datetime.datetime.now(datetime.UTC),
            )
            db.add(snapshot)
            await db.flush()
        current.snapshot_id = snapshot.id
        await db.commit()
        return {
            "project_id": payload.project_id,
            "workflow_key": payload.workflow_key,
            "activation_snapshot_hash": exact_hash,
            "private_version_receipts": private_receipts,
            "governed_snapshot_hash": governed_snapshot_hash,
        }
    except DraftServiceError as exc:
        await _raise_draft_error(db, exc)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.get("/library", response_model=PackCatalogResponse)
async def list_certified_library_packs(user: CurrentUser, db: DBSession) -> PackCatalogResponse:
    """List file/Git-backed immutable Phase 1 and Phase 2 Packs."""
    del user
    try:
        return PackCatalogResponse(packs=[_governance_view(item) for item in await _service(db).available()])
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.get("/library/{pack_id}", response_model=GovernancePackView)
async def get_certified_library_pack(
    pack_id: str, user: CurrentUser, db: DBSession
) -> GovernancePackView:
    del user
    try:
        manifest = next(
            (item for item in await _service(db).available() if item.id == pack_id), None
        )
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Certified Pack not found")
    return _governance_view(manifest)


@router.get("/styles/private", response_model=list[StylePackResponse])
async def list_private_style_packs(user: CurrentUser, db: DBSession) -> list[StylePackResponse]:
    return [
        _style_pack_response(item)
        for item in await _service(db).style_packs(scope=PackScope.USER, user_id=user.id)
    ]


@router.post("/styles/private", response_model=StylePackResponse, status_code=status.HTTP_201_CREATED)
async def create_private_style_pack(
    payload: StylePackRequest, user: CurrentUser, db: DBSession
) -> StylePackResponse:
    try:
        created = await _service(db).create_style(
            scope=PackScope.USER, payload=payload.model_dump(mode="json"), user_id=user.id
        )
        return _style_pack_response(created)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.post("/styles/private/{pack_id}/versions", response_model=StylePackResponse, status_code=status.HTTP_201_CREATED)
async def save_private_style_pack_version(
    pack_id: str, payload: StylePackRequest, user: CurrentUser, db: DBSession
) -> StylePackResponse:
    try:
        saved = await _service(db).save_style_version(
            scope=PackScope.USER, pack_id=pack_id, payload=payload.model_dump(mode="json"), user_id=user.id
        )
        return _style_pack_response(saved)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.put("/styles/private/{pack_id}/quality", response_model=StylePackResponse)
async def score_private_style_pack(
    pack_id: str, payload: StyleQualityRequest, user: CurrentUser, db: DBSession
) -> StylePackResponse:
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Trusted evaluator permission required")
    try:
        scored = await _service(db).record_style_quality(
            scope=PackScope.USER,
            pack_id=pack_id,
            evaluator_user_id=user.id,
            owner_user_id=payload.target_owner_user_id,
            quality_score=payload.quality_score,
            evaluation_score=payload.evaluation_score,
            evaluation_metadata=payload.evaluation_metadata,
        )
        return _style_pack_response(scored)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.get("/projects/{project_id}/styles", response_model=list[StylePackResponse])
async def list_project_style_packs(
    project_id: str, user: CurrentUser, db: DBSession
) -> list[StylePackResponse]:
    await _require_project_access(user, db, project_id)
    return [
        _style_pack_response(item)
        for item in await _service(db).style_packs(
            scope=PackScope.PROJECT, project_id=project_id, user_id=user.id
        )
    ]


@router.post("/projects/{project_id}/styles", response_model=StylePackResponse, status_code=status.HTTP_201_CREATED)
async def create_project_style_pack(
    project_id: str, payload: StylePackRequest, user: CurrentUser, db: DBSession
) -> StylePackResponse:
    await _require_project_management(user, db, project_id)
    try:
        created = await _service(db).create_style(
            scope=PackScope.PROJECT, project_id=project_id,
            payload=payload.model_dump(mode="json"), user_id=user.id,
        )
        return _style_pack_response(created)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.post("/projects/{project_id}/styles/{pack_id}/versions", response_model=StylePackResponse, status_code=status.HTTP_201_CREATED)
async def save_project_style_pack_version(
    project_id: str, pack_id: str, payload: StylePackRequest, user: CurrentUser, db: DBSession
) -> StylePackResponse:
    await _require_project_management(user, db, project_id)
    try:
        saved = await _service(db).save_style_version(
            scope=PackScope.PROJECT, project_id=project_id, pack_id=pack_id,
            payload=payload.model_dump(mode="json"), user_id=user.id,
        )
        return _style_pack_response(saved)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.put("/projects/{project_id}/styles/{pack_id}/quality", response_model=StylePackResponse)
async def score_project_style_pack(
    project_id: str, pack_id: str, payload: StyleQualityRequest, user: CurrentUser, db: DBSession
) -> StylePackResponse:
    await _require_project_management(user, db, project_id)
    if not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Trusted evaluator permission required")
    try:
        scored = await _service(db).record_style_quality(
            scope=PackScope.PROJECT, project_id=project_id, pack_id=pack_id,
            evaluator_user_id=user.id,
            quality_score=payload.quality_score,
            evaluation_score=payload.evaluation_score,
            evaluation_metadata=payload.evaluation_metadata,
        )
        return _style_pack_response(scored)
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.get("/projects/{project_id}/activations/{workflow_key}", response_model=GovernedActivationResponse | None)
async def get_pack_activation(
    project_id: str,
    workflow_key: str,
    user: CurrentUser,
    db: DBSession,
) -> GovernedActivationResponse | None:
    await _require_project_access(user, db, project_id)
    try:
        active = await _service(db).current_activation(project_id, workflow_key)
        return _activation_response(active) if active else None
    except PackServiceError as exc:
        await _raise_pack_error(db, exc)


@router.put("/projects/{project_id}/activations/{workflow_key}", response_model=GovernedActivationResponse)
async def set_pack_activation(
    project_id: str,
    workflow_key: str,
    payload: PackActivationRequest,
    user: CurrentUser,
    db: DBSession,
) -> GovernedActivationResponse:
    await _require_project_management(user, db, project_id)
    try:
        explicit_fields = {"foundation_pack_ids", "functional_pack_ids", "style_pack_ids"}
        selection = None
        if payload.model_fields_set & explicit_fields:
            selection = PackStackSelection(
                foundation_pack_ids=frozenset(payload.foundation_pack_ids or []),
                functional_pack_ids=frozenset(payload.functional_pack_ids or []),
                style_pack_ids=frozenset(payload.style_pack_ids or []),
            )
        active = await _service(db).activate(
            project_id=project_id,
            workflow_key=workflow_key,
            selection=selection,
            active_pack_ids=payload.active_pack_ids,
            user_id=user.id,
        )
        return _activation_response(active)
    except (PackServiceError, ValidationError) as exc:
        if isinstance(exc, PackServiceError):
            await _raise_pack_error(db, exc)
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/custom",
    response_model=PackManifest,
    status_code=status.HTTP_410_GONE,
)
async def create_custom_pack(
    project_id: str,
    user: CurrentUser,
    db: DBSession,
) -> PackManifest:
    await _require_project_management(user, db, project_id)
    del user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Legacy custom Pack creation was retired; use the Analysis Pack Builder",
    )


@router.post(
    "/projects/{project_id}/custom/{pack_id}/versions",
    response_model=PackManifest,
    status_code=status.HTTP_410_GONE,
)
async def save_custom_pack_version(
    project_id: str,
    pack_id: str,
    user: CurrentUser,
    db: DBSession,
) -> PackManifest:
    await _require_project_management(user, db, project_id)
    del pack_id, user
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Legacy custom Pack versioning was retired; use the Analysis Pack Builder",
    )


# Named export for planner/executor integration without importing this API module.
DEFAULT_PACK_WORKFLOW_KEY = DEFAULT_WORKFLOW_KEY
