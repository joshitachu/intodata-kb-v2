"""Periodic Git polling for projects without webhook setup."""

from __future__ import annotations

import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.auto_doc_update import AutoDocUpdateService
from mcp_server.services.git_remote_service import fetch_remote, run_git
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.utils.error_sanitizer import sanitize_git_error

logger = logging.getLogger(__name__)

DEFAULT_POLL_HOURS = (0, 12)


async def run_webhook_diff_update(
    *,
    project_id: str,
    before_sha: str,
    after_sha: str,
    log_mgr: LogManager,
    trigger: str = "poller",
) -> bool:
    """Compatibility wrapper around the shared auto-doc update service."""
    return await AutoDocUpdateService(log_mgr=log_mgr).run(
        project_id=project_id,
        before_sha=before_sha,
        after_sha=after_sha,
        trigger=trigger,
    )


async def _git_output(local_path: str, *args: str) -> str:
    return await run_git(local_path, *args, timeout=30.0, strip=True)


async def _fetch_remote(local_path: str, token_enc: str | None = None) -> None:
    await fetch_remote(local_path, token_enc, remote="origin", git_func=_git_output)


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
            await _fetch_remote(project.local_path, token_enc=getattr(project, "github_token_enc", None))
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
            error = sanitize_git_error(str(e))
            logger.warning("Git polling failed for %s: %s", project.id, e)

        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_auto_reingest_check(project.id, error=error)
            await db.commit()


def _parse_poll_hours(raw_hours: str) -> tuple[int, ...]:
    hours: set[int] = set()
    for raw_hour in raw_hours.split(","):
        raw_hour = raw_hour.strip()
        if not raw_hour:
            continue
        try:
            hour = int(raw_hour)
        except ValueError:
            logger.warning("Ignoring invalid Git poll hour %r", raw_hour)
            continue
        if 0 <= hour <= 23:
            hours.add(hour)
        else:
            logger.warning("Ignoring out-of-range Git poll hour %r", raw_hour)
    return tuple(sorted(hours)) or DEFAULT_POLL_HOURS


def _poll_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown Git poll timezone %r; falling back to UTC", timezone_name)
        return ZoneInfo("UTC")


def next_git_poll_at(
    now: datetime.datetime | None = None,
    *,
    timezone_name: str | None = None,
    raw_hours: str | None = None,
) -> datetime.datetime:
    """Return the next configured Git poll boundary as a timezone-aware datetime."""
    tz = _poll_timezone(timezone_name or config.auto_reingest_poll_timezone)
    hours = _parse_poll_hours(raw_hours or config.auto_reingest_poll_hours)
    current = now.astimezone(tz) if now else datetime.datetime.now(tz)
    today = current.date()

    candidates = [
        datetime.datetime.combine(today, datetime.time(hour=hour), tzinfo=tz)
        for hour in hours
    ]
    for candidate in candidates:
        if candidate > current:
            return candidate

    return datetime.datetime.combine(
        today + datetime.timedelta(days=1),
        datetime.time(hour=hours[0]),
        tzinfo=tz,
    )


async def poll_plugin_packs_once() -> None:
    """Pull any plugin packs installed from a GitHub URL whose remote HEAD changed."""
    from sqlalchemy import select
    from mcp_server.db.models import PluginPack

    async with async_session_factory() as db:
        packs = (
            await db.execute(
                select(PluginPack).where(
                    PluginPack.repo_url.isnot(None),
                    PluginPack.auto_update.is_(True),
                )
            )
        ).scalars().all()

    for pack in packs:
        try:
            await _git_output(pack.path, "fetch", "--quiet")
            remote_sha = await _remote_head_sha(pack.path)
            if remote_sha == pack.last_commit_sha:
                continue

            logger.info("Plugin pack %r has new commits — pulling (%s)", pack.name, remote_sha[:8])
            await _git_output(pack.path, "pull", "--ff-only")

            async with async_session_factory() as db:
                p = await db.get(PluginPack, pack.id)
                if p:
                    p.last_commit_sha = remote_sha
                    # Reload version from manifest in case it changed
                    from pathlib import Path
                    from mcp_server.services.plugin_schema import load_plugin_manifest
                    try:
                        manifest = load_plugin_manifest(Path(p.path))
                        p.version = manifest.version
                        p.description = manifest.description or p.description
                    except Exception:
                        pass
                    await db.commit()

        except Exception:
            logger.exception("Failed to poll plugin pack %r at %s", pack.name, pack.path)


async def git_polling_loop(log_mgr: LogManager) -> None:
    while True:
        next_run = next_git_poll_at()
        delay_seconds = max(
            0.0,
            (next_run - datetime.datetime.now(next_run.tzinfo)).total_seconds(),
        )
        logger.info("Next Git polling run scheduled for %s", next_run.isoformat())
        await asyncio.sleep(delay_seconds)
        try:
            await poll_git_projects_once(log_mgr)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Git polling loop failed")
        try:
            await poll_plugin_packs_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Plugin pack polling loop failed")
