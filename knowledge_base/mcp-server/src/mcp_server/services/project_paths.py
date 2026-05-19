"""Helpers for repairing persisted project repository paths."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from mcp_server.config import config
from mcp_server.db.models import Project

logger = logging.getLogger(__name__)


def expected_repo_path(project_id: str) -> Path:
    """Return the container-local repository path for a managed project."""
    return config.repos_dir / project_id


def should_repair_repo_path(project: Project) -> bool:
    """Return whether a stored project path should be repaired to /app/data/repos."""
    if project.source_type not in {"zip", "git"}:
        return False

    stored_path = Path(project.local_path)
    if stored_path.exists():
        return False

    return stored_path != expected_repo_path(project.id)


async def repair_project_repo_path(project: Project) -> bool:
    """Repair one stale managed-project path in-place.

    Managed zip/git projects should be addressed through config.repos_dir inside
    the running container. This fixes stale absolute host paths left behind by
    deploy-root renames while preserving manually registered local projects.
    """
    if not should_repair_repo_path(project):
        return False

    old_path = project.local_path
    new_path = str(expected_repo_path(project.id))
    project.local_path = new_path
    logger.warning(
        "Repaired stale repo path for project %s: %s -> %s",
        project.id,
        old_path,
        new_path,
    )
    return True


async def repair_managed_project_repo_paths(
    session_factory: async_sessionmaker,
) -> int:
    """Repair stale managed-project repository paths at startup."""
    async with session_factory() as db:
        result = await db.execute(select(Project))
        repaired = 0
        for project in result.scalars():
            if await repair_project_repo_path(project):
                repaired += 1
        if repaired:
            await db.commit()
        return repaired
