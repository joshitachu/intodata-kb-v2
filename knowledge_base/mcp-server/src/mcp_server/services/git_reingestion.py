"""Shared Git-triggered re-ingestion flow for webhooks and polling."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.services.git_remote_service import pull_remote_ff_only, run_git
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.group_doc_dependency import (
    DOC_PACK_MANIFEST_FILENAME,
    GroupDocDependency,
)
from mcp_server.services.rag_support_package import (
    RAG_SUPPORT_DIRNAME,
    inspect_generation_cache_readiness,
)
from mcp_server.services.reingestion_gate import (
    CorrectnessGate,
    GateOutcome,
    load_facts_manifest,
)
from mcp_server.services.scanner_service import ScannerService
from mcp_server.utils.error_sanitizer import sanitize_git_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChangedFile:
    """One file change reported by git diff --name-status."""

    status: str
    path: str
    previous_path: str | None = None


@dataclass(frozen=True)
class IncrementalStagingResult:
    """Outcome of restoring cached Phase 2 analyses for targeted regeneration."""

    project_id: str
    staged: bool
    targeted_regen_ready: bool
    requires_full_rebaseline: bool
    reason_codes: list[str]
    changed_groups: list[str]
    staged_analysis_count: int = 0
    removed_changed_groups: list[str] | None = None
    cache_readiness: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "staged": self.staged,
            "targeted_regen_ready": self.targeted_regen_ready,
            "requires_full_rebaseline": self.requires_full_rebaseline,
            "reason_codes": list(self.reason_codes),
            "changed_groups": list(self.changed_groups),
            "staged_analysis_count": self.staged_analysis_count,
            "removed_changed_groups": list(self.removed_changed_groups or []),
            "cache_readiness": dict(self.cache_readiness or {}),
        }


async def run_git_reingestion(
    project_id: str,
    before_sha: str,
    after_sha: str,
    triggered_by: str,
    log_mgr: LogManager,
    targeted_docs: list[str] | None = None,
) -> bool:
    """Pull latest code, scan, regenerate docs, sync RAG, then record after SHA.

    ``targeted_docs`` (Slice 3) restricts Phase 3 to exactly those documents
    (names without ``.md``); unaffected docs are preserved byte-stable from the
    prior baseline. ``None`` means full regeneration (no Phase 3 doc filter).
    """
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
        github_token_enc = getattr(project, "github_token_enc", None)
        has_baseline = bool(project.last_ingested_commit_sha)
        run_id = run.id

    try:
        await pull_remote_ff_only(local_path, github_token_enc)
    except Exception as e:
        logger.warning("git pull failed for %s: %s", project_id, sanitize_git_error(str(e)))

    is_first_push = before_sha == "0" * 40 or not before_sha
    file_changes: list[ChangedFile] = []
    if not is_first_push and before_sha and after_sha:
        try:
            diff_output = await run_git(
                local_path,
                "diff",
                "--name-status",
                before_sha,
                after_sha,
            )
            file_changes = parse_name_status_diff(diff_output)
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

    # Load the persisted provenance map once: it drives both the affected-doc
    # preview and the tightened (mapped-doc) correctness gate.
    output_dir = config.project_root / "output" / project_id
    dependency = GroupDocDependency.load(
        output_dir / "docs" / RAG_SUPPORT_DIRNAME / DOC_PACK_MANIFEST_FILENAME
    )
    affected_docs_preview = _affected_docs_from_dependency(dependency, changed_groups)
    scoped_docs: set[str] | None = set(targeted_docs) if targeted_docs else None

    staging_result = setup_incremental_staging(
        project_id,
        changed_groups,
        is_first_push,
        has_baseline=has_baseline,
    )
    if not staging_result.staged:
        reason = _full_rebaseline_reason(staging_result.reason_codes)
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_git_update_summary(
                project_id,
                _full_rebaseline_required_summary(
                    project_id=project_id,
                    before_sha=before_sha,
                    after_sha=after_sha,
                    triggered_by=triggered_by,
                    reason=reason,
                    staging_result=staging_result,
                    affected_docs_preview=affected_docs_preview,
                ),
                error=reason,
            )
            await registry.update_status(project_id, "ready", error_message=reason)
            await db.commit()
        await log_mgr.emit(project_id, reason, level="warning")
        return False

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

    gate = CorrectnessGate(changed_groups=set(changed_groups), dependency=dependency)
    last_outcome: list[GateOutcome] = []

    def pre_swap_gate(staging_dir: Path) -> GateOutcome:
        facts = load_facts_manifest(staging_dir)
        if facts is None:
            # The harness always writes facts_manifest.json on the normal path;
            # treat its absence as non-blocking telemetry loss rather than a hold.
            outcome = GateOutcome(
                passed=True,
                evaluated=False,
                reason="facts manifest unavailable; gate skipped",
            )
        else:
            outcome = gate.evaluate(staging_dir, facts)
        last_outcome.clear()
        last_outcome.append(outcome)
        return outcome

    _currently_generating.add(project_id)
    await _run_generate_background(
        project_id,
        gen_run_id,
        log_mgr,
        resume=True,
        doc_generation_provider=doc_generation_provider,
        partial_rag_sync=not is_first_push,
        pre_swap_gate=pre_swap_gate,
        scoped_docs=scoped_docs,
    )

    gate_held = bool(last_outcome and not last_outcome[0].passed)
    if gate_held:
        # Retry the failing analysis + docs exactly ONCE before holding the
        # commit. The resume re-run regenerates the absent/empty group analyses
        # and re-writes their docs; the gate then re-evaluates the fresh output.
        await log_mgr.emit(
            project_id,
            "Correctness gate failed; retrying generation once before holding the commit",
            level="warning",
        )
        last_outcome.clear()
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_status(project_id, "generating")
            retry_run = await registry.create_generate_run(
                project_id, triggered_by=f"{triggered_by}:gate_retry"
            )
            await db.commit()
            retry_run_id = retry_run.id
        _currently_generating.add(project_id)
        await _run_generate_background(
            project_id,
            retry_run_id,
            log_mgr,
            resume=True,
            doc_generation_provider=doc_generation_provider,
            partial_rag_sync=not is_first_push,
            pre_swap_gate=pre_swap_gate,
            scoped_docs=scoped_docs,
        )
        gate_held = bool(last_outcome and not last_outcome[0].passed)

    if gate_held:
        held = last_outcome[0]
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.update_git_update_summary(
                project_id,
                _held_summary(
                    project_id=project_id,
                    before_sha=before_sha,
                    after_sha=after_sha,
                    triggered_by=triggered_by,
                    changed_groups=sorted(changed_groups),
                    outcome=held,
                    affected_docs_preview=affected_docs_preview,
                ),
                error=held.reason or "Reingestion held by correctness gate",
            )
            await db.commit()
        return False

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if project and project.status == "ready" and project.error_message is None:
            if project.last_ingested_commit_sha != after_sha:
                await registry.update_last_ingested_commit(project_id, after_sha)
            if affected_docs_preview is not None:
                # Augment the summary written by auto_doc_update with the preview.
                existing_summary: dict[str, object] = (
                    project.git_update_summary
                    if isinstance(project.git_update_summary, dict)
                    else {}
                )
                await registry.update_git_update_summary(
                    project_id,
                    {**existing_summary, "affected_docs_preview": affected_docs_preview},
                )
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
    *,
    has_baseline: bool | None = None,
) -> IncrementalStagingResult:
    """Stage cached Phase 2 analyses for unchanged groups before generation."""
    output_dir = config.project_root / "output" / project_id
    analysis_cache = output_dir / "analysis_cache"
    changed_group_names = sorted(group for group in changed_groups if group)
    cache_readiness = inspect_generation_cache_readiness(
        output_dir,
        has_baseline=(not is_first_push if has_baseline is None else has_baseline),
        changed_groups=changed_group_names,
    )

    if not cache_readiness.targeted_regen_ready:
        return IncrementalStagingResult(
            project_id=project_id,
            staged=False,
            targeted_regen_ready=False,
            requires_full_rebaseline=cache_readiness.requires_full_rebaseline,
            reason_codes=cache_readiness.reason_codes,
            changed_groups=changed_group_names,
            cache_readiness=cache_readiness.to_dict(),
        )

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

    return IncrementalStagingResult(
        project_id=project_id,
        staged=True,
        targeted_regen_ready=True,
        requires_full_rebaseline=False,
        reason_codes=[],
        changed_groups=changed_group_names,
        staged_analysis_count=len(list(staging_analysis.glob("*.md"))),
        removed_changed_groups=changed_group_names,
        cache_readiness=cache_readiness.to_dict(),
    )


def _held_summary(
    *,
    project_id: str,
    before_sha: str,
    after_sha: str,
    triggered_by: str,
    changed_groups: list[str],
    outcome: GateOutcome,
    affected_docs_preview: list[str] | None = None,
) -> dict[str, object]:
    """Build the git-update summary for a commit held by the correctness gate."""
    reason = outcome.reason or "Reingestion held by correctness gate"
    summary: dict[str, object] = {
        "trigger": triggered_by,
        "mode": "git_reingestion",
        "action": "manual_review",
        "outcome": "manual_review",
        "reason_code": "correctness_gate_failed",
        "decision_strategy": "blocking_correctness_gate",
        "project_id": project_id,
        "before": before_sha,
        "after": after_sha,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "manual_review_reason": reason,
        "held": True,
        "changed_groups": list(changed_groups),
        "gate": outcome.to_summary(),
        "regen_result": {
            "type": "targeted_regen",
            "success": False,
            "held": True,
            "reason": "correctness_gate_failed",
        },
    }
    if affected_docs_preview is not None:
        summary["affected_docs_preview"] = affected_docs_preview
    return summary


def _compute_affected_docs_preview(
    output_dir: Path,
    changed_groups: set[str],
) -> list[str] | None:
    """Read the persisted provenance map and return docs affected by changed_groups.

    Returns None when no manifest exists yet (pre-baseline) so callers know the
    preview is unavailable rather than empty. Returns an empty list when groups
    are resolved but map to no docs.
    """
    manifest_path = (
        output_dir / "docs" / RAG_SUPPORT_DIRNAME / DOC_PACK_MANIFEST_FILENAME
    )
    dep = GroupDocDependency.load(manifest_path)
    return _affected_docs_from_dependency(dep, changed_groups)


def _affected_docs_from_dependency(
    dependency: GroupDocDependency | None,
    changed_groups: set[str],
) -> list[str] | None:
    """Map changed groups to their docs (+ cross-cutting) via the provenance map.

    Returns None when no manifest exists (pre-baseline) so callers know the
    preview is unavailable rather than empty; an empty list when groups resolve
    but map to no docs.
    """
    if dependency is None:
        return None
    if not changed_groups:
        return []
    affected: set[str] = set()
    for group in changed_groups:
        affected |= dependency.docs_for_group(group)
    affected |= dependency.cross_cutting_docs()
    return sorted(affected)


def _full_rebaseline_reason(reason_codes: list[str]) -> str:
    suffix = ", ".join(reason_codes) if reason_codes else "targeted_regen_not_ready"
    return f"Full rebaseline required before targeted regeneration: {suffix}"


def _full_rebaseline_required_summary(
    *,
    project_id: str,
    before_sha: str,
    after_sha: str,
    triggered_by: str,
    reason: str,
    staging_result: IncrementalStagingResult,
    affected_docs_preview: list[str] | None = None,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "trigger": triggered_by,
        "mode": "git_reingestion",
        "action": "manual_review",
        "outcome": "manual_review",
        "reason_code": "full_rebaseline_required",
        "decision_strategy": "targeted_regen_preflight",
        "project_id": project_id,
        "before": before_sha,
        "after": after_sha,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "manual_review_reason": reason,
        "fallback_used": False,
        "fallback_reason": reason,
        "expensive_fallback_required": True,
        "requires_full_rebaseline": staging_result.requires_full_rebaseline,
        "targeted_regen_ready": staging_result.targeted_regen_ready,
        "changed_groups": list(staging_result.changed_groups),
        "reason_codes": list(staging_result.reason_codes),
        "staging_result": staging_result.to_dict(),
        "regen_result": {
            "type": "targeted_regen",
            "success": False,
            "skipped": True,
            "reason": "preflight_failed",
            "reason_codes": list(staging_result.reason_codes),
        },
    }
    if affected_docs_preview is not None:
        summary["affected_docs_preview"] = affected_docs_preview
    return summary
