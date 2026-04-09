"""Template management service -- CRUD for templates, versions, and profiles."""

from __future__ import annotations

import difflib
import logging

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mcp_server.db.template_models import (
    TIER_BY_CATEGORY,
    ProfileAssignment,
    Template,
    TemplateProfile,
    TemplateVersion,
)

logger = logging.getLogger(__name__)


class TemplateService:
    """CRUD operations for template management."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Templates ──────────────────────────────────────────────────────

    async def list_templates(
        self,
        *,
        category: str | None = None,
        platform: str | None = None,
        tier: str | None = None,
        active_only: bool = True,
    ) -> list[Template]:
        stmt = select(Template).options(selectinload(Template.versions))
        if category:
            stmt = stmt.where(Template.category == category)
        if platform:
            stmt = stmt.where(
                (Template.platform == platform) | (Template.platform.is_(None))
            )
        if tier:
            stmt = stmt.where(Template.tier == tier)
        if active_only:
            stmt = stmt.where(Template.is_active.is_(True))
        stmt = stmt.order_by(Template.category, Template.slug)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_template(self, slug: str) -> Template | None:
        stmt = (
            select(Template)
            .where(Template.slug == slug)
            .options(selectinload(Template.versions))
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_template_by_id(self, template_id: int) -> Template | None:
        result = await self.db.execute(
            select(Template).where(Template.id == template_id)
        )
        return result.scalar_one_or_none()

    async def create_template(
        self,
        *,
        slug: str,
        category: str,
        display_name: str,
        platform: str | None = None,
        description: str | None = None,
        tier: str | None = None,
    ) -> Template:
        resolved_tier = tier or TIER_BY_CATEGORY.get(category, "configurable")
        template = Template(
            slug=slug,
            category=category,
            platform=platform,
            display_name=display_name,
            description=description,
            tier=resolved_tier,
        )
        self.db.add(template)
        await self.db.flush()
        return template

    async def update_template(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> Template | None:
        template = await self.get_template(slug)
        if not template:
            return None
        if display_name is not None:
            template.display_name = display_name
        if description is not None:
            template.description = description
        if is_active is not None:
            template.is_active = is_active
        await self.db.flush()
        return template

    # ── Versions ───────────────────────────────────────────────────────

    async def list_versions(self, slug: str) -> list[TemplateVersion]:
        template = await self.get_template(slug)
        if not template:
            return []
        stmt = (
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template.id)
            .order_by(TemplateVersion.version_number.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_version(self, slug: str, version_number: int) -> TemplateVersion | None:
        template = await self.get_template(slug)
        if not template:
            return None
        stmt = select(TemplateVersion).where(
            TemplateVersion.template_id == template.id,
            TemplateVersion.version_number == version_number,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_version(self, template_id: int) -> TemplateVersion | None:
        stmt = (
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id)
            .order_by(TemplateVersion.version_number.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_version(
        self,
        slug: str,
        *,
        content: str,
        change_summary: str,
        created_by: str | None = None,
    ) -> TemplateVersion | None:
        template = await self.get_template(slug)
        if not template:
            return None

        # Policy templates can only be updated by privileged actors
        _PRIVILEGED_ACTORS = {"system", "seed_script", "migration"}
        if template.tier == "policy" and (created_by or "") not in _PRIVILEGED_ACTORS:
            raise PermissionError(
                f"Template '{slug}' is a policy template and cannot be edited via the dashboard. "
                "Policy templates are managed via source control and the seed script."
            )

        # Get next version number
        stmt = select(func.max(TemplateVersion.version_number)).where(
            TemplateVersion.template_id == template.id
        )
        result = await self.db.execute(stmt)
        max_version = result.scalar() or 0

        version = TemplateVersion(
            template_id=template.id,
            version_number=max_version + 1,
            content=content,
            change_summary=change_summary,
            created_by=created_by,
            content_hash=TemplateVersion.compute_hash(content),
            byte_size=len(content.encode("utf-8")),
        )
        self.db.add(version)
        await self.db.flush()
        return version

    async def diff_versions(
        self, slug: str, from_version: int, to_version: int,
    ) -> str | None:
        v_from = await self.get_version(slug, from_version)
        v_to = await self.get_version(slug, to_version)
        if not v_from or not v_to:
            return None
        diff = difflib.unified_diff(
            v_from.content.splitlines(keepends=True),
            v_to.content.splitlines(keepends=True),
            fromfile=f"v{from_version}",
            tofile=f"v{to_version}",
        )
        return "".join(diff)

    async def rollback_version(
        self, slug: str, target_version: int, *, created_by: str | None = None,
    ) -> TemplateVersion | None:
        old = await self.get_version(slug, target_version)
        if not old:
            return None
        return await self.create_version(
            slug,
            content=old.content,
            change_summary=f"Rollback to v{target_version}",
            created_by=created_by,
        )

    # ── Profiles ───────────────────────────────────────────────────────

    async def list_profiles(
        self,
        *,
        platform: str | None = None,
        scope: str | None = None,
    ) -> list[TemplateProfile]:
        stmt = select(TemplateProfile)
        if platform:
            stmt = stmt.where(TemplateProfile.platform == platform)
        if scope:
            stmt = stmt.where(TemplateProfile.scope == scope)
        stmt = stmt.order_by(TemplateProfile.name)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_profile(self, profile_id: int) -> TemplateProfile | None:
        stmt = (
            select(TemplateProfile)
            .where(TemplateProfile.id == profile_id)
            .options(
                selectinload(TemplateProfile.assignments)
                .selectinload(ProfileAssignment.template),
                selectinload(TemplateProfile.assignments)
                .selectinload(ProfileAssignment.version),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_profile(
        self,
        *,
        name: str,
        platform: str | None = None,
        description: str | None = None,
        scope: str = "global",
        scope_ref: str | None = None,
        created_by: str | None = None,
    ) -> TemplateProfile:
        profile = TemplateProfile(
            name=name,
            platform=platform,
            description=description,
            scope=scope,
            scope_ref=scope_ref,
            created_by=created_by,
        )
        self.db.add(profile)
        await self.db.flush()
        return profile

    async def update_profile(
        self,
        profile_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> TemplateProfile | None:
        profile = await self.get_profile(profile_id)
        if not profile:
            return None
        if name is not None:
            profile.name = name
        if description is not None:
            profile.description = description
        await self.db.flush()
        return profile

    async def clone_profile(
        self,
        profile_id: int,
        *,
        new_name: str,
        created_by: str | None = None,
    ) -> TemplateProfile | None:
        source = await self.get_profile(profile_id)
        if not source:
            return None

        new_profile = TemplateProfile(
            name=new_name,
            platform=source.platform,
            description=f"Cloned from '{source.name}'",
            scope=source.scope,
            scope_ref=source.scope_ref,
            created_by=created_by,
        )
        self.db.add(new_profile)
        await self.db.flush()

        for assignment in source.assignments:
            new_assignment = ProfileAssignment(
                profile_id=new_profile.id,
                template_id=assignment.template_id,
                version_id=assignment.version_id,
            )
            self.db.add(new_assignment)
        await self.db.flush()
        return new_profile

    async def set_default_profile(self, profile_id: int) -> TemplateProfile | None:
        profile = await self.get_profile(profile_id)
        if not profile:
            return None

        # Unset other defaults for same platform
        await self.db.execute(
            update(TemplateProfile)
            .where(
                TemplateProfile.platform == profile.platform,
                TemplateProfile.id != profile_id,
            )
            .values(is_default=False)
        )
        profile.is_default = True
        await self.db.flush()
        return profile

    # ── Assignments ────────────────────────────────────────────────────

    async def list_assignments(self, profile_id: int) -> list[ProfileAssignment]:
        stmt = (
            select(ProfileAssignment)
            .where(ProfileAssignment.profile_id == profile_id)
            .options(
                selectinload(ProfileAssignment.template),
                selectinload(ProfileAssignment.version),
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def set_assignment(
        self,
        profile_id: int,
        template_slug: str,
        version_id: int,
    ) -> ProfileAssignment | None:
        template = await self.get_template(template_slug)
        if not template:
            return None

        # Upsert
        stmt = select(ProfileAssignment).where(
            ProfileAssignment.profile_id == profile_id,
            ProfileAssignment.template_id == template.id,
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.version_id = version_id
            await self.db.flush()
            return existing

        assignment = ProfileAssignment(
            profile_id=profile_id,
            template_id=template.id,
            version_id=version_id,
        )
        self.db.add(assignment)
        await self.db.flush()
        return assignment

    async def bulk_set_assignments(
        self,
        profile_id: int,
        assignments: list[dict],
    ) -> list[ProfileAssignment]:
        """Set multiple assignments at once. Each dict has 'slug' and 'version_id'."""
        results = []
        for a in assignments:
            result = await self.set_assignment(
                profile_id, a["slug"], a["version_id"],
            )
            if result:
                results.append(result)
        return results

    async def delete_assignment(
        self, profile_id: int, template_slug: str,
    ) -> bool:
        template = await self.get_template(template_slug)
        if not template:
            return False

        stmt = select(ProfileAssignment).where(
            ProfileAssignment.profile_id == profile_id,
            ProfileAssignment.template_id == template.id,
        )
        result = await self.db.execute(stmt)
        assignment = result.scalar_one_or_none()
        if assignment:
            await self.db.delete(assignment)
            await self.db.flush()
            return True
        return False
