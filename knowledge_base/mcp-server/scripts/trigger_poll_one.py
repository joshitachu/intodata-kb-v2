"""Run one Git-poll reingestion cycle for a single project.

Manual test harness for the Git reingestion path. Does exactly what
``poll_git_projects_once`` does for one project, so the poll hour schedule
(``MCP_AUTO_REINGEST_POLL_HOURS``) does not have to be waited out.

    python scripts/trigger_poll_one.py POLY
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_server.db.session import async_session_factory
from mcp_server.services.git_credentials import provider_for_project
from mcp_server.services.git_poller import (
    _fetch_remote,
    _remote_head_sha,
    run_webhook_diff_update,
)
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry


class StderrLog(LogManager):
    async def emit(self, project_id, message, level="info"):  # type: ignore[override]
        print(f"[{level}] {message}", file=sys.stderr, flush=True)
        await super().emit(project_id, message, level)


async def main(project_id: str) -> int:
    async with async_session_factory() as db:
        project = await ProjectRegistry(db).get_project(project_id)
    if project is None:
        print(f"no such project: {project_id}", file=sys.stderr)
        return 2

    print(f"project      : {project.id} ({project.status})", flush=True)
    print(f"local_path   : {project.local_path}", flush=True)
    print(f"baseline sha : {project.last_ingested_commit_sha}", flush=True)

    await _fetch_remote(project.local_path, provider_for_project(project))
    remote_sha = await _remote_head_sha(project.local_path)
    print(f"remote sha   : {remote_sha}", flush=True)

    if remote_sha == project.last_ingested_commit_sha:
        print("up to date -- nothing to reingest", flush=True)
        return 0

    before_sha = project.last_ingested_commit_sha or ("0" * 40)
    success = await run_webhook_diff_update(
        project_id=project.id,
        before_sha=before_sha,
        after_sha=remote_sha,
        log_mgr=StderrLog(),
        trigger="poller",
    )
    print(f"result       : {'SUCCESS' if success else 'FAILED/HELD'}", flush=True)

    async with async_session_factory() as db:
        after = await ProjectRegistry(db).get_project(project_id)
    print(f"new baseline : {after.last_ingested_commit_sha}", flush=True)
    print(f"status       : {after.status}", flush=True)
    print(f"error        : {after.auto_reingest_error}", flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
