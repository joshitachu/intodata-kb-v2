"""Template management REST endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from mcp_server.db.session import async_session_factory
from mcp_server.schemas import (
    AssignmentListResponse,
    AssignmentResponse,
    BulkSetAssignmentsRequest,
    CloneProfileRequest,
    CreateProfileRequest,
    CreateTemplateRequest,
    CreateVersionRequest,
    DiffResponse,
    ProfileDetailResponse,
    ProfileListResponse,
    ProfileResponse,
    SetAssignmentRequest,
    StatusResponse,
    TemplateListResponse,
    TemplateResponse,
    TemplateVersionListResponse,
    TemplateVersionResponse,
    UpdateProfileRequest,
    UpdateTemplateRequest,
)
from mcp_server.services.template_service import TemplateService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])
profile_router = APIRouter(prefix="/profiles", tags=["profiles"])


# ── Helpers ────────────────────────────────────────────────────────────

def _template_to_response(t, latest_version: int | None = None) -> TemplateResponse:
    lv = latest_version
    if lv is None and t.versions:
        lv = t.versions[0].version_number if t.versions else None
    return TemplateResponse(
        id=t.id,
        slug=t.slug,
        category=t.category,
        platform=t.platform,
        display_name=t.display_name,
        description=t.description,
        is_active=t.is_active,
        created_at=t.created_at,
        updated_at=t.updated_at,
        latest_version=lv,
    )


def _version_to_response(v, include_content: bool = False) -> TemplateVersionResponse:
    return TemplateVersionResponse(
        id=v.id,
        template_id=v.template_id,
        version_number=v.version_number,
        content=v.content if include_content else None,
        change_summary=v.change_summary,
        created_by=v.created_by,
        content_hash=v.content_hash,
        byte_size=v.byte_size,
        created_at=v.created_at,
    )


def _assignment_to_response(a) -> AssignmentResponse:
    return AssignmentResponse(
        id=a.id,
        profile_id=a.profile_id,
        template_slug=a.template.slug if a.template else "",
        template_display_name=a.template.display_name if a.template else "",
        version_id=a.version_id,
        version_number=a.version.version_number if a.version else 0,
        created_at=a.created_at,
    )


# ── Templates ──────────────────────────────────────────────────────────

@router.get("/", response_model=TemplateListResponse)
async def list_templates(
    category: str | None = Query(None),
    platform: str | None = Query(None),
):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        templates = await svc.list_templates(category=category, platform=platform)
        return TemplateListResponse(
            templates=[_template_to_response(t) for t in templates],
            total=len(templates),
        )


@router.get("/{slug}", response_model=TemplateResponse)
async def get_template(slug: str):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        t = await svc.get_template(slug)
        if not t:
            raise HTTPException(404, f"Template not found: {slug}")
        return _template_to_response(t)


@router.post("/", response_model=TemplateResponse, status_code=201)
async def create_template(req: CreateTemplateRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        existing = await svc.get_template(req.slug)
        if existing:
            raise HTTPException(409, f"Template already exists: {req.slug}")
        t = await svc.create_template(
            slug=req.slug,
            category=req.category,
            display_name=req.display_name,
            platform=req.platform,
            description=req.description,
        )
        await db.commit()
        return _template_to_response(t)


@router.patch("/{slug}", response_model=TemplateResponse)
async def update_template(slug: str, req: UpdateTemplateRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        t = await svc.update_template(
            slug,
            display_name=req.display_name,
            description=req.description,
            is_active=req.is_active,
        )
        if not t:
            raise HTTPException(404, f"Template not found: {slug}")
        await db.commit()
        return _template_to_response(t)


# ── Versions ───────────────────────────────────────────────────────────

@router.get("/{slug}/versions", response_model=TemplateVersionListResponse)
async def list_versions(slug: str):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        versions = await svc.list_versions(slug)
        return TemplateVersionListResponse(
            versions=[_version_to_response(v) for v in versions],
            total=len(versions),
        )


@router.get("/{slug}/versions/{version_number}", response_model=TemplateVersionResponse)
async def get_version(slug: str, version_number: int):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        v = await svc.get_version(slug, version_number)
        if not v:
            raise HTTPException(404, f"Version not found: {slug} v{version_number}")
        return _version_to_response(v, include_content=True)


@router.post("/{slug}/versions", response_model=TemplateVersionResponse, status_code=201)
async def create_version(slug: str, req: CreateVersionRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        v = await svc.create_version(
            slug,
            content=req.content,
            change_summary=req.change_summary,
            created_by=req.created_by,
        )
        if not v:
            raise HTTPException(404, f"Template not found: {slug}")
        await db.commit()
        return _version_to_response(v, include_content=True)


@router.get("/{slug}/diff", response_model=DiffResponse)
async def diff_versions(
    slug: str,
    from_v: int = Query(..., alias="from"),
    to_v: int = Query(..., alias="to"),
):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        diff = await svc.diff_versions(slug, from_v, to_v)
        if diff is None:
            raise HTTPException(404, "One or both versions not found")
        return DiffResponse(slug=slug, from_version=from_v, to_version=to_v, diff=diff)


@router.post("/{slug}/rollback/{version_number}", response_model=TemplateVersionResponse)
async def rollback_version(slug: str, version_number: int):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        v = await svc.rollback_version(slug, version_number)
        if not v:
            raise HTTPException(404, f"Version not found: {slug} v{version_number}")
        await db.commit()
        return _version_to_response(v, include_content=True)


# ── Profiles ───────────────────────────────────────────────────────────

@profile_router.get("/", response_model=ProfileListResponse)
async def list_profiles(
    platform: str | None = Query(None),
    scope: str | None = Query(None),
):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        profiles = await svc.list_profiles(platform=platform, scope=scope)
        return ProfileListResponse(
            profiles=[ProfileResponse(
                id=p.id, name=p.name, description=p.description,
                platform=p.platform, scope=p.scope, scope_ref=p.scope_ref,
                is_default=p.is_default, created_by=p.created_by,
                created_at=p.created_at, updated_at=p.updated_at,
            ) for p in profiles],
            total=len(profiles),
        )


@profile_router.get("/{profile_id}", response_model=ProfileDetailResponse)
async def get_profile(profile_id: int):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        p = await svc.get_profile(profile_id)
        if not p:
            raise HTTPException(404, f"Profile not found: {profile_id}")
        return ProfileDetailResponse(
            id=p.id, name=p.name, description=p.description,
            platform=p.platform, scope=p.scope, scope_ref=p.scope_ref,
            is_default=p.is_default, created_by=p.created_by,
            created_at=p.created_at, updated_at=p.updated_at,
            assignments=[_assignment_to_response(a) for a in p.assignments],
        )


@profile_router.post("/", response_model=ProfileResponse, status_code=201)
async def create_profile(req: CreateProfileRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        p = await svc.create_profile(
            name=req.name, platform=req.platform, description=req.description,
            scope=req.scope, scope_ref=req.scope_ref, created_by=req.created_by,
        )
        await db.commit()
        return ProfileResponse(
            id=p.id, name=p.name, description=p.description,
            platform=p.platform, scope=p.scope, scope_ref=p.scope_ref,
            is_default=p.is_default, created_by=p.created_by,
            created_at=p.created_at, updated_at=p.updated_at,
        )


@profile_router.patch("/{profile_id}", response_model=ProfileResponse)
async def update_profile(profile_id: int, req: UpdateProfileRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        p = await svc.update_profile(profile_id, name=req.name, description=req.description)
        if not p:
            raise HTTPException(404, f"Profile not found: {profile_id}")
        await db.commit()
        return ProfileResponse(
            id=p.id, name=p.name, description=p.description,
            platform=p.platform, scope=p.scope, scope_ref=p.scope_ref,
            is_default=p.is_default, created_by=p.created_by,
            created_at=p.created_at, updated_at=p.updated_at,
        )


@profile_router.post("/{profile_id}/clone", response_model=ProfileResponse, status_code=201)
async def clone_profile(profile_id: int, req: CloneProfileRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        p = await svc.clone_profile(profile_id, new_name=req.new_name, created_by=req.created_by)
        if not p:
            raise HTTPException(404, f"Profile not found: {profile_id}")
        await db.commit()
        return ProfileResponse(
            id=p.id, name=p.name, description=p.description,
            platform=p.platform, scope=p.scope, scope_ref=p.scope_ref,
            is_default=p.is_default, created_by=p.created_by,
            created_at=p.created_at, updated_at=p.updated_at,
        )


@profile_router.post("/{profile_id}/set-default", response_model=ProfileResponse)
async def set_default_profile(profile_id: int):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        p = await svc.set_default_profile(profile_id)
        if not p:
            raise HTTPException(404, f"Profile not found: {profile_id}")
        await db.commit()
        return ProfileResponse(
            id=p.id, name=p.name, description=p.description,
            platform=p.platform, scope=p.scope, scope_ref=p.scope_ref,
            is_default=p.is_default, created_by=p.created_by,
            created_at=p.created_at, updated_at=p.updated_at,
        )


# ── Assignments ────────────────────────────────────────────────────────

@profile_router.get("/{profile_id}/assignments", response_model=AssignmentListResponse)
async def list_assignments(profile_id: int):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        assignments = await svc.list_assignments(profile_id)
        return AssignmentListResponse(
            assignments=[_assignment_to_response(a) for a in assignments],
            total=len(assignments),
        )


@profile_router.put("/{profile_id}/assignments", response_model=AssignmentListResponse)
async def bulk_set_assignments(profile_id: int, req: BulkSetAssignmentsRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        results = await svc.bulk_set_assignments(
            profile_id,
            [{"slug": a.slug, "version_id": a.version_id} for a in req.assignments],
        )
        await db.commit()
        # Reload with relations
        assignments = await svc.list_assignments(profile_id)
        return AssignmentListResponse(
            assignments=[_assignment_to_response(a) for a in assignments],
            total=len(assignments),
        )


@profile_router.patch("/{profile_id}/assignments/{slug}", response_model=AssignmentResponse)
async def set_assignment(profile_id: int, slug: str, req: SetAssignmentRequest):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        a = await svc.set_assignment(profile_id, slug, req.version_id)
        if not a:
            raise HTTPException(404, f"Template not found: {slug}")
        await db.commit()
        # Reload with relations
        assignments = await svc.list_assignments(profile_id)
        for loaded in assignments:
            if loaded.template.slug == slug:
                return _assignment_to_response(loaded)
        raise HTTPException(500, "Assignment saved but could not reload")


@profile_router.delete("/{profile_id}/assignments/{slug}", response_model=StatusResponse)
async def delete_assignment(profile_id: int, slug: str):
    async with async_session_factory() as db:
        svc = TemplateService(db)
        ok = await svc.delete_assignment(profile_id, slug)
        if not ok:
            raise HTTPException(404, f"Assignment not found: {slug}")
        await db.commit()
        return StatusResponse(status="deleted", message=f"Removed {slug} from profile")
