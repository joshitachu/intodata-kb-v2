"""Periodic Git polling for projects without webhook setup."""

from __future__ import annotations

import asyncio
import logging

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.git_diff_patcher import run_webhook_diff_update
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry

logger = logging.getLogger(__name__)


async def _git_output(local_path: str, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=local_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return stdout.decode().strip()


async def _fetch_remote(local_path: str) -> None:
    await _git_output(local_path, "fetch", "--quiet")


async def _remote_head_sha(local_path: str) -> str:
    try:
        return await _git_output(local_path, "rev-parse", "@{u}")
    except RuntimeError:
        remote = await _git_output(local_path, "rev-parse", "--abbrev-ref", "origin/HEAD")
        return await _git_output(local_path, "rev-parse", remote)


async def poll_git_projects_once(log_mgr: LogManager) -> None:
    """Check all auto-reingest Git projects once."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        projects = await registry.list_auto_reingest_git_projects()

    for project in projects:
        if project.status in {"scanning", "generating"}:
            continue

        error: str | None = None
        remote_sha: str | None = None
        try:
            await _fetch_remote(project.local_path)
            remote_sha = await _remote_head_sha(project.local_path)
            if remote_sha == project.last_ingested_commit_sha:
                async with async_session_factory() as db:
                    registry = ProjectRegistry(db)
                    await registry.update_auto_reingest_check(project.id)
                    await db.commit()
                continue

            before_sha = project.last_ingested_commit_sha or ("0" * 40)
            success = await run_webhook_diff_update(
                project_id=project.id,
                before_sha=before_sha,
                after_sha=remote_sha,
                log_mgr=log_mgr,
                trigger="poller",
            )
            if not success:
                error = f"Diff-only auto update failed for remote commit {remote_sha}"
        except Exception as e:
            error = str(e)
            logger.warning("Git polling failed for %s: %s", project.id, e)

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_auto_reingest_check(project.id, error=error)
            await db.commit()


async def git_polling_loop(log_mgr: LogManager) -> None:
    interval = max(1, int(config.auto_reingest_poll_interval_seconds))
    while True:
        try:
            await poll_git_projects_once(log_mgr)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Git polling loop failed")
        await asyncio.sleep(interval)
