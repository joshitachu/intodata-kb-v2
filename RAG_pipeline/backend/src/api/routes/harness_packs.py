"""Reusable documentation Packs and project-scoped capability policy Packs."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.dependencies import CurrentUser, DBSession
from src.harness.packs.models import PackManifest, PackRecommendation, ProjectPackActivation
from src.harness.packs.project_service import (
    DEFAULT_WORKFLOW_KEY,
    PackServiceError,
    ProjectPackService,
)
from src.harness.registry import base_capability_registry
from src.harness.providers.mcp_docs import (
    McpDocumentationPack,
    McpDocumentationPackDocumentDraft,
    McpDocumentationProvider,
    McpDocumentationProviderError,
)
from src.rbac.permissions import MANAGE_PROJECTS, can_manage_project, get_user_project_ids, has_permission

router = APIRouter(prefix="/harness/packs", tags=["harness-packs"])


class PackRecommendationsResponse(BaseModel):
    recommendations: list[PackRecommendation]


class PackActivationRequest(BaseModel):
    active_pack_ids: list[str] = Field(default_factory=list, max_length=100)


class CustomPackRequest(BaseModel):
    manifest: dict[str, Any]


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


class DocumentationPackDocumentDraft(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    sections: list[str] = Field(default_factory=list, max_length=30)
    instructions: str | None = Field(default=None, max_length=8_000)


class CreateDocumentationPackRequest(BaseModel):
    name: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")
    description: str | None = Field(default=None, max_length=1_000)
    documents: list[DocumentationPackDocumentDraft] = Field(min_length=1, max_length=30)


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


def _documentation_pack_view(pack: McpDocumentationPack) -> DocumentationPackView:
    return DocumentationPackView(
        id=pack.id,
        name=pack.name,
        version=pack.version,
        description=pack.description,
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


@router.post(
    "/documentation-packs",
    response_model=DocumentationPackView,
    status_code=status.HTTP_201_CREATED,
)
async def create_documentation_pack(
    payload: CreateDocumentationPackRequest,
    request: Request,
    user: CurrentUser,
) -> DocumentationPackView:
    """Create and register a reusable IntoDoc documentation Pack."""
    if not has_permission(user, MANAGE_PROJECTS):
        raise HTTPException(status_code=403, detail="Documentation Pack management permission required")
    provider = McpDocumentationProvider()
    try:
        created = await provider.create_documentation_pack(
            name=payload.name,
            description=payload.description,
            documents=[
                McpDocumentationPackDocumentDraft(
                    name=document.name,
                    sections=document.sections,
                    instructions=document.instructions,
                )
                for document in payload.documents
            ],
            access_token=request.cookies.get("access_token"),
        )
    except McpDocumentationProviderError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from exc
    return _documentation_pack_view(created)


@router.get("/projects/{project_id}/recommendations", response_model=PackRecommendationsResponse)
async def recommend_packs(project_id: str, user: CurrentUser, db: DBSession) -> PackRecommendationsResponse:
    await _require_project_access(user, db, project_id)
    recommendations = await _service(db).recommendations(project_id)
    return PackRecommendationsResponse(recommendations=list(recommendations))


@router.get("/projects/{project_id}/activations/{workflow_key}", response_model=ProjectPackActivation | None)
async def get_pack_activation(
    project_id: str,
    workflow_key: str,
    user: CurrentUser,
    db: DBSession,
) -> ProjectPackActivation | None:
    await _require_project_access(user, db, project_id)
    return await _service(db).current_activation(project_id, workflow_key)


@router.put("/projects/{project_id}/activations/{workflow_key}", response_model=ProjectPackActivation)
async def set_pack_activation(
    project_id: str,
    workflow_key: str,
    payload: PackActivationRequest,
    user: CurrentUser,
    db: DBSession,
) -> ProjectPackActivation:
    await _require_project_management(user, db, project_id)
    try:
        return await _service(db).activate(
            project_id=project_id,
            workflow_key=workflow_key,
            active_pack_ids=payload.active_pack_ids,
            user_id=user.id,
        )
    except PackServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/custom",
    response_model=PackManifest,
    status_code=status.HTTP_201_CREATED,
)
async def create_custom_pack(
    project_id: str,
    payload: CustomPackRequest,
    user: CurrentUser,
    db: DBSession,
) -> PackManifest:
    await _require_project_management(user, db, project_id)
    try:
        return await _service(db).create_custom(
            project_id=project_id, payload=payload.manifest, user_id=user.id
        )
    except PackServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/custom/{pack_id}/versions",
    response_model=PackManifest,
    status_code=status.HTTP_201_CREATED,
)
async def save_custom_pack_version(
    project_id: str,
    pack_id: str,
    payload: CustomPackRequest,
    user: CurrentUser,
    db: DBSession,
) -> PackManifest:
    await _require_project_management(user, db, project_id)
    try:
        return await _service(db).save_custom_version(
            project_id=project_id,
            pack_id=pack_id,
            payload=payload.manifest,
            user_id=user.id,
        )
    except PackServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# Named export for planner/executor integration without importing this API module.
DEFAULT_PACK_WORKFLOW_KEY = DEFAULT_WORKFLOW_KEY
