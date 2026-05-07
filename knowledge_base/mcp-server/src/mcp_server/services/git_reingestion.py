"""Shared Git-triggered re-ingestion flow for webhooks and polling."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.scanner_service import ScannerService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChangedFile:
    """One file change reported by git diff --name-status."""

    status: str
    path: str
    previous_path: str | None = None


async def run_git_reingestion(
    project_id: str,
    before_sha: str,
    after_sha: str,
    triggered_by: str,
    log_mgr: LogManager,
) -> bool:
    """Pull latest code, scan, regenerate docs, sync RAG, then record after SHA."""
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            logger.warning("Git re-ingestion ignored; project not found: %s", project_id)
            return False

        await registry.update_status(project_id, "scanning")
        run = await registry.create_scan_run(project_id, triggered_by=triggered_by)
        await db.commit()

        local_path = project.local_path
        platform = project.platform
        doc_generation_provider = project.doc_generation_provider
        run_id = run.id

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "pull",
            cwd=local_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.wait()
    except Exception as e:
        logger.warning("git pull failed for %s: %s", project_id, e)

    is_first_push = before_sha == "0" * 40 or not before_sha
    file_changes: list[ChangedFile] = []
    if not is_first_push and before_sha and after_sha:
        try:
            diff_proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--name-status", before_sha, after_sha,
                cwd=local_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await diff_proc.communicate()
            file_changes = parse_name_status_diff(stdout.decode())
            logger.info(
                "Git re-ingestion diff for %s: %d changed files",
                project_id,
                len(file_changes),
            )
        except Exception as e:
            logger.warning("git diff failed for %s: %s", project_id, e)

    scanner = ScannerService(log_manager=log_mgr)
    log_mgr.reset_log_file(project_id)
    result = await scanner.run_scan(
        project_id=project_id,
        repo_path=local_path,
        platform=platform,
    )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if result["success"]:
            await registry.update_scan_result(
                project_id,
                scan_result_path=result["scan_result_path"],
                output_dir=result["output_dir"],
                job_count=result["job_count"],
            )
            await registry.complete_scan_run(
                run_id, status="completed", job_count=result["job_count"]
            )
        else:
            await registry.update_status(
                project_id, "error", error_message=result.get("error")
            )
            await registry.complete_scan_run(run_id, status="failed")
        await db.commit()

    if not result["success"]:
        return False

    changed_groups = resolve_changed_groups(project_id, file_changes, is_first_push)
    setup_incremental_staging(project_id, changed_groups, is_first_push)

    from mcp_server.api.routes.projects import (
        _currently_generating,
        _run_generate_background,
    )

    if project_id in _currently_generating:
        logger.info(
            "Generation already running for %s; skipping %s-triggered regen",
            project_id,
            triggered_by,
        )
        return False

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        await registry.update_status(project_id, "generating")
        gen_run = await registry.create_generate_run(project_id, triggered_by=triggered_by)
        await db.commit()
        gen_run_id = gen_run.id

    _currently_generating.add(project_id)
    await _run_generate_background(
        project_id,
        gen_run_id,
        log_mgr,
        resume=True,
        doc_generation_provider=doc_generation_provider,
        partial_rag_sync=not is_first_push,
    )

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if project and project.status == "ready" and project.error_message is None:
            await registry.update_last_ingested_commit(project_id, after_sha)
            await db.commit()
            return True
        await db.commit()
    return False


def parse_name_status_diff(diff_output: str) -> list[ChangedFile]:
    """Parse `git diff --name-status` output."""
    changes: list[ChangedFile] = []
    for line in diff_output.splitlines():
        if not line.strip():
            continue
        parts = line.rstrip("\n").split("\t")
        status = parts[0]
        code = status[:1]
        if code in {"R", "C"} and len(parts) >= 3:
            changes.append(ChangedFile(status=code, path=parts[2], previous_path=parts[1]))
        elif len(parts) >= 2:
            changes.append(ChangedFile(status=code, path=parts[1]))
    return changes


def resolve_changed_groups(
    project_id: str,
    file_changes: list[ChangedFile],
    is_first_push: bool,
) -> set[str]:
    """Map changed file paths to group names using the scan cache directory structure."""
    scan_jobs_dir = config.intodoc_output_dir / project_id / "scan" / "jobs"
    if not scan_jobs_dir.exists():
        return set()

    scan_groups = {d.name for d in scan_jobs_dir.iterdir() if d.is_dir()}
    if not scan_groups:
        return set()

    if is_first_push or not file_changes:
        return scan_groups

    changed_groups: set[str] = set()
    for change in file_changes:
        paths = [change.path]
        if change.previous_path:
            paths.append(change.previous_path)
        for filepath in paths:
            parts = Path(filepath).parts
            for group in scan_groups:
                if group in parts:
                    changed_groups.add(group)
                    break

    return changed_groups if changed_groups else scan_groups


def setup_incremental_staging(
    project_id: str,
    changed_groups: set[str],
    is_first_push: bool,
) -> None:
    """Stage cached Phase 2 analyses for unchanged groups before generation."""
    output_dir = config.project_root / "output" / project_id
    analysis_cache = output_dir / "analysis_cache"

    if is_first_push or not analysis_cache.exists() or not changed_groups:
        return

    staging_dir = output_dir / "docs_staging"
    staging_analysis = staging_dir / "_analysis"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_analysis.mkdir(parents=True, exist_ok=True)

    for f in analysis_cache.glob("*.md"):
        shutil.copy2(f, staging_analysis / f.name)

    for group in changed_groups:
        for variant in [group, f"{group}_part1", f"{group}_part2"]:
            p = staging_analysis / f"{variant}.md"
            if p.exists():
                p.unlink()

    for f in staging_analysis.glob("_bundle_*.md"):
        f.unlink()
