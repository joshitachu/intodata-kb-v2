"""Template resolver -- resolves template content at generation time.

Resolution cascade:
  1. Explicit profile_id (from API call)
  2. Project-scoped default profile
  3. Platform default profile
  4. Filesystem fallback (backward compat)
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mcp_server.db.template_models import (
    ProfileAssignment,
    Template,
    TemplateProfile,
    TemplateVersion,
)
from mcp_server.services.template_service import TemplateService

logger = logging.getLogger(__name__)


class TemplateResolver:
    """Resolves a complete template set for document generation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.svc = TemplateService(db)

    async def resolve_profile(
        self,
        *,
        profile_id: int | None = None,
        project_id: str | None = None,
        platform: str = "talend",
    ) -> TemplateProfile | None:
        """Find the active profile via resolution cascade."""
        # 1. Explicit profile
        if profile_id:
            return await self.svc.get_profile(profile_id)

        # 2. Project-scoped default
        if project_id:
            stmt = (
                select(TemplateProfile)
                .where(
                    TemplateProfile.scope == "project",
                    TemplateProfile.scope_ref == project_id,
                    TemplateProfile.is_default.is_(True),
                )
                .options(
                    selectinload(TemplateProfile.assignments)
                    .selectinload(ProfileAssignment.template),
                    selectinload(TemplateProfile.assignments)
                    .selectinload(ProfileAssignment.version),
                )
                .limit(1)
            )
            result = await self.db.execute(stmt)
            profile = result.scalar_one_or_none()
            if profile:
                return profile

        # 3. Platform default
        stmt = (
            select(TemplateProfile)
            .where(
                TemplateProfile.platform == platform,
                TemplateProfile.is_default.is_(True),
            )
            .options(
                selectinload(TemplateProfile.assignments)
                .selectinload(ProfileAssignment.template),
                selectinload(TemplateProfile.assignments)
                .selectinload(ProfileAssignment.version),
            )
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def resolve_all(
        self,
        *,
        profile_id: int | None = None,
        project_id: str | None = None,
        platform: str = "talend",
    ) -> dict[str, str]:
        """Resolve all template contents as {slug: content}.

        Returns a dict where keys are template slugs and values are the
        resolved content. Templates not covered by the profile fall back
        to their latest version in the DB.
        """
        profile = await self.resolve_profile(
            profile_id=profile_id,
            project_id=project_id,
            platform=platform,
        )

        resolved: dict[str, str] = {}
        snapshot: dict[str, int] = {}

        if profile and profile.assignments:
            # Build map from profile assignments
            for assignment in profile.assignments:
                if assignment.template and assignment.version:
                    slug = assignment.template.slug
                    resolved[slug] = assignment.version.content
                    snapshot[slug] = assignment.version.id

        # Fill in any templates not covered by the profile using latest versions
        all_templates = await self.svc.list_templates(platform=platform)
        for template in all_templates:
            if template.slug not in resolved:
                latest = await self.svc.get_latest_version(template.id)
                if latest:
                    resolved[template.slug] = latest.content
                    snapshot[template.slug] = latest.id

        self._snapshot = snapshot
        return resolved

    @property
    def snapshot(self) -> dict[str, int]:
        """Get the template snapshot (slug -> version_id) from last resolve_all() call."""
        return getattr(self, "_snapshot", {})

    async def resolve_by_category(
        self,
        category: str,
        *,
        profile_id: int | None = None,
        project_id: str | None = None,
        platform: str = "talend",
    ) -> dict[str, str]:
        """Resolve templates for a specific category only."""
        all_resolved = await self.resolve_all(
            profile_id=profile_id,
            project_id=project_id,
            platform=platform,
        )
        return {
            slug: content
            for slug, content in all_resolved.items()
            if slug.startswith(f"{category}/")
        }


class FilesystemFallback:
    """Reads templates from the filesystem (backward compatibility).

    Used when no DB templates exist or when DB is not available.
    """

    def __init__(self, platform: str = "talend") -> None:
        self.platform = platform
        # Resolve paths from source tree
        _source_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        self.platforms_dir = _source_root / "intodoc" / "platforms" / platform
        self.ref_dir = _source_root / "intodoc" / "reference-docs"
        self.templates_dir = Path(__file__).resolve().parent.parent / "templates"

    def get_analysis_guide(self) -> str | None:
        """Read ANALYSIS_GUIDE.md for the platform."""
        guide = self.platforms_dir / "prompts" / "ANALYSIS_GUIDE.md"
        if guide.exists():
            return guide.read_text(encoding="utf-8")
        return None

    def get_phase3_guide(self) -> str | None:
        """Read PHASE_3_GUIDE.md for the platform."""
        guide = self.platforms_dir / "prompts" / "PHASE_3_GUIDE.md"
        if guide.exists():
            return guide.read_text(encoding="utf-8")
        return None

    def get_reference_doc(self, doc_name: str) -> str | None:
        """Read a reference doc (e.g. '00_EXECUTIVE_SUMMARY')."""
        ref_file = self.ref_dir / f"{doc_name}.md"
        if ref_file.exists():
            return ref_file.read_text(encoding="utf-8")
        return None

    def get_scoring_template(self, name: str) -> str | None:
        """Read a scoring template (e.g. 'scoring-rubric')."""
        f = self.templates_dir / f"{name}.md"
        if f.exists():
            return f.read_text(encoding="utf-8")
        return None
