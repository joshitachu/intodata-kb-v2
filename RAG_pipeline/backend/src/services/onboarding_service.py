"""Onboarding state management + background setup orchestration."""

from __future__ import annotations

import datetime
import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Notification, User, UserOnboarding

logger = logging.getLogger(__name__)


VALID_STEPS = {"workspace", "platforms", "comm_tools", "source", "tour"}
VALID_PLATFORMS = {"talend", "workato", "lobster", "generic"}
VALID_COMM_TOOLS = {"slack", "teams", "jira", "confluence"}
WORKSPACE_NAME_MAX_LEN = 200


class OnboardingService:
    """Manage per-user onboarding state with lazy row creation."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create(self, user: User, *, create: bool = True) -> UserOnboarding | None:
        """Return the user's onboarding row, creating a fresh one on first touch.

        When `create=False`, returns None if no row exists — used to distinguish
        "existing user (no row)" from "new user (row with wizard_completed=False)".
        """
        result = await self.db.execute(
            select(UserOnboarding).where(UserOnboarding.user_id == user.id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        if not create:
            return None

        row = UserOnboarding(user_id=user.id)
        self.db.add(row)
        await self.db.flush()
        return row

    async def save_step(self, user: User, step: str, data: dict[str, Any]) -> UserOnboarding:
        """Partial save for a wizard step."""
        if step not in VALID_STEPS:
            raise ValueError(f"Invalid step: {step}")

        row = await self.get_or_create(user)
        assert row is not None

        if step == "workspace":
            name = (data.get("workspace_name") or "").strip()
            if not name:
                raise ValueError("workspace_name is required")
            if len(name) > WORKSPACE_NAME_MAX_LEN:
                raise ValueError(
                    f"workspace_name must be <= {WORKSPACE_NAME_MAX_LEN} characters"
                )
            row.workspace_name = name
            row.wizard_current_step = max(row.wizard_current_step, 1)

        elif step == "platforms":
            platforms = data.get("platforms") or []
            for p in platforms:
                if p not in VALID_PLATFORMS:
                    raise ValueError(f"Unknown platform: {p}")
            row.selected_platforms = platforms
            row.wizard_current_step = max(row.wizard_current_step, 2)

        elif step == "comm_tools":
            tools = data.get("comm_tools") or []
            # We accept any comm_tool id from the config endpoint (no hard whitelist
            # here so the admin can add new tools via config). Known "coming soon"
            # tools are still recorded as user preference.
            row.selected_comm_tools = tools
            row.wizard_current_step = max(row.wizard_current_step, 3)

        elif step == "source":
            source_type = data.get("source_type")
            if source_type not in (None, "github", "zip"):
                raise ValueError(f"Invalid source_type: {source_type}")
            row.source_type = source_type
            row.source_url = data.get("source_url")
            row.source_filename = data.get("source_filename")
            row.wizard_current_step = max(row.wizard_current_step, 4)

        elif step == "tour":
            row.tour_current_step = int(data.get("tour_step", 0))

        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def complete_wizard(self, user: User, *, has_source: bool) -> UserOnboarding:
        row = await self.get_or_create(user)
        assert row is not None
        row.wizard_completed = True
        row.wizard_completed_at = datetime.datetime.now(datetime.timezone.utc)
        # 6-step wizard: 0-welcome, 1-workspace, 2-platforms, 3-tools, 4-source, 5-provisioning
        row.wizard_current_step = 5
        row.setup_status = "processing" if has_source else "skipped"
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def complete_tour(self, user: User) -> UserOnboarding:
        row = await self.get_or_create(user)
        assert row is not None
        row.tour_completed = True
        row.tour_completed_at = datetime.datetime.now(datetime.timezone.utc)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def reset(self, user: User) -> None:
        """Admin-only: wipe a user's onboarding state so the wizard shows again."""
        row = await self.get_or_create(user, create=False)
        if row is not None:
            await self.db.delete(row)
            await self.db.commit()

    async def mark_setup_failed(self, user_id: int, error: str) -> None:
        result = await self.db.execute(
            select(UserOnboarding).where(UserOnboarding.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.setup_status = "failed"
        row.setup_error = error[:4000]
        await self.db.commit()

    async def mark_setup_completed(self, user_id: int) -> None:
        result = await self.db.execute(
            select(UserOnboarding).where(UserOnboarding.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.setup_status = "completed"
        row.setup_error = None
        await self.db.commit()


async def run_background_setup(user_id: int, session_factory) -> None:
    """Background processor for onboarding source setup.

    ZIP sources: ProvisioningStep (frontend) already ran the MCP pipeline before
    the wizard completed, so we just mark setup complete.

    GitHub sources: download the repo archive, extract supported text files, and
    ingest them into RAG directly.
    """
    from src.services.notification_service import NotificationService

    try:
        async with session_factory() as db:
            result = await db.execute(
                select(UserOnboarding).where(UserOnboarding.user_id == user_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                logger.warning("run_background_setup: no onboarding row for user %s", user_id)
                return

            svc = OnboardingService(db)
            source_type = row.source_type

            if source_type == "github":
                project_id = _derive_project_id(row.workspace_name, user_id)
                await _ingest_github_source(
                    source_url=row.source_url or "",
                    project_id=project_id,
                    user_id=user_id,
                    db=db,
                )
            # ZIP: frontend ProvisioningStep → MCP → /ingest/md already handled it.
            # None/unknown: no source was provided, nothing to ingest.

            await svc.mark_setup_completed(user_id)
            notif = NotificationService(db)
            await notif.create(
                user_id=user_id,
                type="setup_complete",
                title="Workspace ready",
                message="Your workspace is set up and ready to query.",
            )
    except Exception as e:
        logger.exception("Background onboarding setup failed for user %s", user_id)
        async with session_factory() as db:
            svc = OnboardingService(db)
            await svc.mark_setup_failed(user_id, str(e))


def _derive_project_id(workspace_name: str | None, user_id: int | None) -> str:
    """Derive a stable, URL-safe project_id from the workspace name."""
    fallback = f"workspace_{user_id}" if user_id is not None else "workspace"
    base = (workspace_name or fallback).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return slug[:50] or fallback


_INGEST_EXTENSIONS = {
    ".md", ".txt", ".rst",
    ".py", ".js", ".ts",
    ".sql",
    ".yaml", ".yml",
    ".json", ".xml",
}
_SKIP_DIRS = {"node_modules", "__pycache__", "vendor", ".git", ".github", "dist", "build"}
_MAX_FILE_BYTES = 500_000  # 500 KB per file
_MAX_FILES = 200


async def _ingest_github_source(
    source_url: str,
    project_id: str,
    user_id: int,
    db: AsyncSession,
) -> None:
    """Download a public GitHub repo archive and ingest supported text files into RAG."""
    import httpx
    from qdrant_client import AsyncQdrantClient

    from src.config import settings
    from src.ingestion.pipeline import ingest_text

    url = source_url.rstrip("/").removesuffix(".git")
    if "github.com" not in url:
        raise ValueError(f"Only GitHub URLs are supported, got: {url!r}")

    archive_bytes: bytes | None = None
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for branch in ("main", "master"):
            archive_url = f"{url}/archive/refs/heads/{branch}.zip"
            response = await client.get(archive_url)
            if response.status_code == 200:
                archive_bytes = response.content
                logger.info("Downloaded GitHub archive (%d bytes) from %s", len(archive_bytes), archive_url)
                break
            logger.debug("Branch %s not found at %s (HTTP %d)", branch, archive_url, response.status_code)

    if archive_bytes is None:
        raise ValueError(f"Could not download archive from {url} (tried main and master branches)")

    qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        candidates = [
            name for name in zf.namelist()
            if not name.endswith("/")
            and Path(name).suffix.lower() in _INGEST_EXTENSIONS
            and not any(part in _SKIP_DIRS or part.startswith(".") for part in Path(name).parts[1:])
        ][:_MAX_FILES]

        logger.info("Ingesting %d files from GitHub repo for user %s", len(candidates), user_id)
        ingested = 0

        for zip_path in candidates:
            info = zf.getinfo(zip_path)
            if info.file_size > _MAX_FILE_BYTES:
                logger.debug("Skipping oversized file %s (%d bytes)", zip_path, info.file_size)
                continue
            try:
                text = zf.read(zip_path).decode("utf-8", errors="replace")
                if not text.strip():
                    continue
                # Strip the top-level archive directory prefix (e.g. "repo-main/")
                parts = Path(zip_path).parts
                filename = str(Path(*parts[1:])) if len(parts) > 1 else zip_path
                await ingest_text(
                    text=text,
                    filename=filename,
                    source=f"github:{source_url}",
                    project_id=project_id,
                    metadata={},
                    db=db,
                    qdrant=qdrant,
                )
                ingested += 1
            except Exception:
                logger.warning("Failed to ingest %s, skipping", zip_path, exc_info=True)

    logger.info(
        "GitHub ingest complete: %d/%d files ingested for user %s project %s",
        ingested, len(candidates), user_id, project_id,
    )
