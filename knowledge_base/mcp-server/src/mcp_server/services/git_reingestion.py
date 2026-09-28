"""Shared Git-triggered re-ingestion flow for webhooks and polling."""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.licensing.core import LicenceRefused
from mcp_server.licensing.usage import GATE_RETRY_TRIGGER_SUFFIX, RunAdmission, log_refusal
from mcp_server.services.doc_version_history import record_push_outcome
from mcp_server.services.git_credentials import provider_for_project
from mcp_server.services.git_diff_patcher import _is_ignored_path
from mcp_server.services.git_remote_service import pull_remote_ff_only, run_git
from mcp_server.services.log_manager import LogManager
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.group_doc_dependency import (
    DOC_PACK_MANIFEST_FILENAME,
    GroupDocDependency,
)
from mcp_server.services.impact_router import ImpactRouter
from mcp_server.services.rag_support_package import (
    RAG_SUPPORT_DIRNAME,
    analysis_stem_matches_group,
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
    full_rederive: bool = False,
    outcome_sink: dict | None = None,
) -> bool:
    """Pull latest code, scan, regenerate docs, sync RAG, then record after SHA.

    ``targeted_docs`` (Slice 3) restricts Phase 3 to exactly those documents
    (names without ``.md``); unaffected docs are preserved byte-stable from the
    prior baseline. A ``None``/empty collection is never interpreted as an
    automatic full regeneration: whole-project generation is reserved for
    ``full_rederive=True`` or an initial ingestion.

    ``full_rederive`` (Slice 1) forces a full Phase 3 regeneration regardless of
    the impact map -- the explicit, deliberate path for a first push or an
    operator-triggered rebuild. When ``config.impact_routing_enabled`` is set and
    ``full_rederive`` is False, the Phase 3 document set is derived from the
    fingerprint dirty-set via ``ImpactRouter`` (not from the passed-in
    ``targeted_docs``, which came from the legacy path/threshold router).

    ``outcome_sink`` (optional): when provided, this function records whether it
    actually regenerated (``{"skipped": False}``) or skipped without generating
    (``{"skipped": True}`` -- fingerprint unchanged or infra-only push). Callers
    use this to avoid reporting a ``targeted_regen`` that never ran. Default
    ``None`` preserves the bool-only contract for existing callers/tests.
    """
    def _mark(skipped: bool, *, held_reason_code: str | None = None) -> None:
        if outcome_sink is not None:
            outcome_sink["skipped"] = skipped
            if held_reason_code is not None:
                outcome_sink["held"] = True
                outcome_sink["held_reason_code"] = held_reason_code
    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        project = await registry.get_project(project_id)
        if not project:
            logger.warning("Git re-ingestion ignored; project not found: %s", project_id)
            return False

        try:
            run = await registry.create_scan_run(project_id, triggered_by=triggered_by)
        except LicenceRefused as exc:
            # Nothing was written; the commit marker stays put, so the next
            # push/poll retries once the licence is renewed. One line per
            # attempt -- the poller only fires at its configured hours.
            log_refusal("git re-ingestion scan", exc, project_id=project_id, trigger=triggered_by)
            _mark(skipped=False, held_reason_code=exc.refusal.code.value)
            return False
        await registry.update_status(project_id, "scanning")
        await db.commit()

        local_path = project.local_path
        platform = project.platform
        doc_generation_provider = project.doc_generation_provider
        credential = provider_for_project(project)
        has_baseline = bool(project.last_ingested_commit_sha)
        run_id = run.id

    try:
        await pull_remote_ff_only(local_path, credential)
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
    # Write scan output to the PER-PROJECT dir (intodoc_output_dir/{project_id}),
    # the same location the fingerprint diff and generation read from. Without an
    # explicit output_dir, run_scan defaults to the root intodoc_output_dir, so the
    # fresh scan lands in the wrong place and the fingerprint compares against a
    # stale per-project snapshot -> misses all changes. Mirrors _run_scan_background.
    result = await scanner.run_scan(
        project_id=project_id,
        repo_path=local_path,
        platform=platform,
        output_dir=str(config.intodoc_output_dir / project_id),
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

    # --- Scan fingerprint dirty-set detection (Slice 1) ---
    # Compute current hashes AFTER confirming the scan succeeded (critique Lens 2).
    from mcp_server.services.scan_fingerprint_service import ScanFingerprintService

    scan_jobs_dir = config.intodoc_output_dir / project_id / "scan" / "jobs"
    try:
        current_hashes = ScanFingerprintService.compute_project_hashes(scan_jobs_dir)
        async with async_session_factory() as db:
            fp_service = ScanFingerprintService(db)
            dirty_groups, fp_is_first_push = await fp_service.diff_against_baseline(
                project_id, current_hashes
            )
    except Exception as fp_exc:
        # Fingerprint service failure MUST fail loud — do NOT fall through to the
        # legacy resolver, as that would silently produce an unreliable dirty set.
        # (critique Lens 3)
        logger.error(
            "Scan fingerprint service failed for %s: %s — aborting reingestion",
            project_id,
            fp_exc,
        )
        await log_mgr.emit(
            project_id,
            f"Fingerprint service error: {fp_exc}",
            level="error",
        )
        return False

    if not dirty_groups and not fp_is_first_push:
        # Empty dirty set with an established baseline: scan output unchanged.
        # Advance commit marker and write hash baseline in one atomic transaction.
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            fp_svc = ScanFingerprintService(db)
            now = datetime.now(timezone.utc)
            await fp_svc.persist_baseline(db, project_id, current_hashes, now)
            await registry.update_last_ingested_commit(project_id, after_sha)
            await registry.update_git_update_summary(
                project_id,
                _skip_summary(project_id, before_sha, after_sha, triggered_by),
            )
            await registry.update_status(project_id, "ready")
            await db.commit()
        await log_mgr.emit(
            project_id,
            "Fingerprint diff: no scan groups changed; skipping regeneration",
            level="info",
        )
        _mark(skipped=True)
        return True

    changed_groups = dirty_groups

    # Filter out infrastructure / CI / build files before group resolution.
    # Mirrors the QuickChangeScanner.scan() path used by AutoDocAgentRuntime.
    doc_relevant_changes = [c for c in file_changes if not _is_ignored_path(c.path)]
    if not is_first_push and file_changes and not doc_relevant_changes:
        # All changed files are infra/ignore-listed; advance SHA and stop.
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            project = await registry.get_project(project_id)
            if project and project.last_ingested_commit_sha != after_sha:
                await registry.update_last_ingested_commit(project_id, after_sha)
            await db.commit()
        await log_mgr.emit(
            project_id,
            "Git re-ingestion skipped: all changed files are ignored (infra/docs/CI)",
            level="info",
        )
        _mark(skipped=True)
        return True

    # NOTE(S1): resolve_changed_groups superseded by ScanFingerprintService;
    # callee preserved for Slice 3 audit before removal.
    resolve_changed_groups(project_id, doc_relevant_changes, is_first_push)

    # Load the persisted provenance map once: it drives both the affected-doc
    # preview and the tightened (mapped-doc) correctness gate.
    output_dir = config.project_root / "output" / project_id
    dependency = GroupDocDependency.load(
        output_dir / "docs" / RAG_SUPPORT_DIRNAME / DOC_PACK_MANIFEST_FILENAME
    )
    # affected_docs_preview is computed over the FULL dirty set for reporting.
    affected_docs_preview = _affected_docs_from_dependency(dependency, changed_groups)

    # --- Slice 1: fingerprint dirty-set drives the Phase 3 document set ---
    # Under impact routing, the Phase 3 scope comes from the fingerprint dirty-set
    # via the provenance map (NOT the legacy path/threshold router's targeted_docs).
    # Only a first ingestion or an explicit operator rebuild may use an
    # unfiltered Phase 3 generation. Every ordinary source update needs a
    # non-empty bounded scope; missing provenance or an empty map is held.
    if config.impact_routing_enabled and not full_rederive and not is_first_push:
        impact_plan = ImpactRouter.plan(dirty_groups, dependency)
        scoped_docs: set[str] | None = set(impact_plan.targeted_docs)
    elif full_rederive:
        scoped_docs = None
    elif is_first_push:
        scoped_docs = None
    else:
        scoped_docs = set(targeted_docs or [])

    if not full_rederive and not is_first_push and not scoped_docs:
        reason = "Automatic reingestion held: no bounded documentation target scope"
        logger.warning("%s for %s", reason, project_id)
        await log_mgr.emit(project_id, reason, level="warning")
        _mark(skipped=False, held_reason_code="empty_target_scope")
        return False

    # --- Slice 3: classify dirty groups and partition into scoped/patch lanes ---
    group_delta_classifications = classify_dirty_groups(
        project_id, dirty_groups, output_dir
    )
    threshold = config.reingestion_patch_field_edit_threshold
    patch_groups: set[str] = {
        g for g, cls in group_delta_classifications.items()
        if cls.kind == "FIELD_EDIT" and cls.changed_fraction < threshold
    }
    scoped_groups: set[str] = dirty_groups - patch_groups
    logger.info(
        "Slice 3 delta classification for %s: %d scoped, %d patch | %s",
        project_id,
        len(scoped_groups),
        len(patch_groups),
        {g: f"{cls.kind}({cls.changed_fraction:.3f})" for g, cls in group_delta_classifications.items()},  # type: ignore[attr-defined]
    )
    # --- end Slice 3 ---

    staging_result = setup_incremental_staging(
        project_id,
        scoped_groups,  # Slice 3: only scoped groups need analysis-cache eviction
        is_first_push,
        has_baseline=has_baseline,
    )
    # Slice 3: if scoped_groups is empty (all patch-eligible) and staging fails,
    # fall back conservatively to treating all dirty groups as scoped_regen.
    if not staging_result.staged and not scoped_groups:
        logger.warning(
            "Slice 3: staging preflight failed with empty scoped set for %s "
            "(all groups were patch-eligible); falling back to full dirty set "
            "as scoped_regen. Reason: %s",
            project_id,
            staging_result.reason_codes,
        )
        scoped_groups = dirty_groups
        patch_groups = set()
        staging_result = setup_incremental_staging(
            project_id, scoped_groups, is_first_push, has_baseline=has_baseline
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

    from mcp_server.services.generation_profile import load_published_profile
    generation_profile = load_published_profile(
        config.project_root / "output" / project_id / "docs"
    )
    if generation_profile is not None:
        doc_generation_provider = generation_profile["engine"]

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        try:
            gen_run = await registry.create_generate_run(
                project_id, triggered_by=triggered_by,
                generation_profile=generation_profile,
            )
        except LicenceRefused as exc:
            # The scan above already completed and left the project "ready";
            # the commit marker is not advanced, so the update is retried later.
            log_refusal("git re-ingestion generation", exc, project_id=project_id, trigger=triggered_by)
            await log_mgr.emit(
                project_id,
                f"Automatic regeneration not started: {exc.refusal.message}",
                level="warning",
            )
            _mark(skipped=False, held_reason_code=exc.refusal.code.value)
            return False
        await registry.update_status(project_id, "generating")
        await db.commit()
        gen_run_id = gen_run.id

    # Slice 3 Lens-4 fix: gate receives SCOPED subset only. Patch groups preserve
    # their analysis from cache and must NOT be checked for fresh analysis presence
    # (that would fire REASON_CACHE_SUPPORT_MISMATCH spuriously).
    gate = CorrectnessGate(changed_groups=set(scoped_groups), dependency=dependency)
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
        generation_profile=generation_profile,
    )

    gate_held = bool(last_outcome and not last_outcome[0].passed)
    retry_run_id: int | None = None
    if gate_held:
        # Retry the failing analysis + docs exactly ONCE before holding the
        # commit. The resume re-run regenerates the absent/empty group analyses
        # and re-writes their docs; the gate then re-evaluates the fresh output.
        await log_mgr.emit(
            project_id,
            "Correctness gate failed; retrying generation once before holding the commit",
            level="warning",
        )
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            try:
                # The retry is the same update as the run it retries: a
                # continuation (licence-checked, not counted against quota).
                retry_run = await registry.create_generate_run(
                    project_id, triggered_by=f"{triggered_by}{GATE_RETRY_TRIGGER_SUFFIX}",
                    generation_profile=generation_profile,
                    admission=RunAdmission.CONTINUATION,
                )
            except LicenceRefused as exc:
                # Fall through to the hold below with the first outcome intact.
                log_refusal("git re-ingestion gate retry", exc, project_id=project_id, trigger=triggered_by)
            else:
                await registry.update_status(project_id, "generating")
                await db.commit()
                retry_run_id = retry_run.id
    if gate_held and retry_run_id is not None:
        last_outcome.clear()
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
            generation_profile=generation_profile,
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
            # Report the docs actually regenerated (fingerprint-derived scope) so the
            # caller records truthful touched_docs, not the router's path-based
            # estimate. None scoped_docs == full regen (all published docs).
            if outcome_sink is not None:
                if scoped_docs:
                    outcome_sink["regenerated_docs"] = sorted(scoped_docs)
                else:
                    docs_dir_now = output_dir / "docs"
                    outcome_sink["regenerated_docs"] = (
                        sorted(p.stem for p in docs_dir_now.glob("*.md"))
                        if docs_dir_now.is_dir() else []
                    )
            if project.last_ingested_commit_sha != after_sha:
                await registry.update_last_ingested_commit(project_id, after_sha)
            if affected_docs_preview is not None:
                # Augment the summary written by auto_doc_update with the preview
                # and Slice 3 delta classification telemetry.
                existing_summary: dict[str, object] = (
                    project.git_update_summary
                    if isinstance(project.git_update_summary, dict)
                    else {}
                )
                delta_cls_summary: dict[str, object] = {
                    g: {
                        "kind": cls.kind,  # type: ignore[attr-defined]
                        "changed_fraction": cls.changed_fraction,  # type: ignore[attr-defined]
                    }
                    for g, cls in group_delta_classifications.items()
                }
                await registry.update_git_update_summary(
                    project_id,
                    {
                        **existing_summary,
                        "affected_docs_preview": affected_docs_preview,
                        "delta_classifications": delta_cls_summary,
                    },
                )
            # Establish the reingestion baseline (fingerprint hashes + prev_scan)
            # via the shared helper so both generation paths behave identically.
            await prime_reingestion_baseline(
                db, project_id, output_dir, current_hashes=current_hashes
            )
            if full_rederive:
                # Slice 5 (US-7/US-8): this is the ONE call site (besides the
                # `full_rederive` default itself) that can reach this branch --
                # see critique.md Attack 1. Record the manual-rebuild history
                # entry here (this path bypasses AutoDocUpdateService, so
                # record_push_outcome is not otherwise reached for it) and
                # persist the summary so the reingestion-status surface can
                # report the reset immediately.
                docs_dir = output_dir / "docs"
                touched_docs = (
                    sorted(p.name for p in docs_dir.glob("*.md"))
                    if docs_dir.is_dir()
                    else []
                )
                manual_summary = _manual_full_rebuild_summary(
                    project_id=project_id,
                    before_sha=before_sha,
                    after_sha=after_sha,
                    triggered_by=triggered_by,
                    touched_docs=touched_docs,
                )
                record_push_outcome(project_id, manual_summary)
                await registry.update_git_update_summary(project_id, manual_summary)
            await db.commit()
            _mark(skipped=False)
            return True
        await db.commit()
    return False


def _read_group_jobs_from_scan(intodoc_output_dir: Path, project_id: str, group: str) -> list[dict]:
    """Read current scan job list for a group directly from the segmented scan output.

    Returns an empty list when the group directory does not exist or contains no
    JSON files. Never raises (file errors are surfaced to the caller).
    """
    jobs_dir = intodoc_output_dir / project_id / "scan" / "jobs" / group
    if not jobs_dir.is_dir():
        return []
    jobs: list[dict] = []
    for f in sorted(jobs_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                jobs.append(data)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "_read_group_jobs_from_scan: cannot read %s: %s", f, exc
            )
    return jobs


def classify_dirty_groups(
    project_id: str,
    dirty_set: set[str],
    output_dir: Path,
) -> "dict[str, object]":
    """Classify each dirty group by its delta kind.

    For each group in ``dirty_set``:
    - If ``output_dir/prev_scan/{group}.json`` exists: load it and the current
      scan JSON, call ``classify_group_delta``.
    - If absent: no baseline available -> return NEW_UNIT (routes to scoped_regen).

    Returns a dict mapping group name -> DeltaClassification.
    All errors per group are caught and logged; the group is conservatively
    classified as NEW_UNIT on read failure.
    """
    from mcp_server.services.group_delta_classifier import (
        DeltaClassification,
        classify_group_delta,
    )

    result: dict[str, DeltaClassification] = {}
    prev_scan_dir = output_dir / "prev_scan"

    for group in dirty_set:
        prev_path = prev_scan_dir / f"{group}.json"

        if prev_path.is_file():
            try:
                prev_raw = json.loads(prev_path.read_text(encoding="utf-8"))
                prev_jobs: list[dict] = prev_raw if isinstance(prev_raw, list) else []
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "classify_dirty_groups: cannot read prev scan for group %s: %s",
                    group,
                    exc,
                )
                prev_jobs = []

            try:
                curr_jobs = _read_group_jobs_from_scan(
                    config.intodoc_output_dir, project_id, group
                )
            except OSError as exc:
                logger.warning(
                    "classify_dirty_groups: cannot read curr scan for group %s: %s",
                    group,
                    exc,
                )
                curr_jobs = []

            if not prev_jobs:
                result[group] = DeltaClassification(
                    kind="NEW_UNIT",
                    changed_fraction=0.0,
                    detail="no prior baseline (empty or unreadable prev scan)",
                )
            else:
                result[group] = classify_group_delta(prev_jobs, curr_jobs)
        else:
            result[group] = DeltaClassification(
                kind="NEW_UNIT",
                changed_fraction=0.0,
                detail="no prior baseline",
            )

    return result


async def prime_reingestion_baseline(
    db,
    project_id: str,
    output_dir: Path,
    *,
    current_hashes: "dict[str, str] | None" = None,
) -> None:
    """Establish the reingestion baseline after a successful generation + swap.

    Writes the per-group scan fingerprint baseline (drives dirty-set detection)
    and persists the current scan JSON as the prev-baseline (drives delta
    classification). BOTH generation entry points must call this -- webhook
    reingestion (``run_git_reingestion``) and the manual generate flow
    (``_run_generate_background``) -- so a project's first push after either can
    be diffed instead of being forced into a full regen.

    The caller owns the transaction (this does not commit). When ``current_hashes``
    is not supplied it is computed from the segmented scan output. prev_scan
    persistence is best-effort and never raises.
    """
    from mcp_server.services.scan_fingerprint_service import ScanFingerprintService

    if current_hashes is None:
        scan_jobs_dir = config.intodoc_output_dir / project_id / "scan" / "jobs"
        current_hashes = ScanFingerprintService.compute_project_hashes(scan_jobs_dir)
    fp_svc = ScanFingerprintService(db)
    await fp_svc.persist_baseline(
        db, project_id, current_hashes, datetime.now(timezone.utc)
    )
    persist_group_prev_scans(project_id, output_dir)


def persist_group_prev_scans(project_id: str, output_dir: Path) -> None:
    """Write the current scan output as the prev-baseline per group (Slice 3).

    Location: ``output_dir/prev_scan/{group}.json`` (same project tree as
    ``analysis_cache/``, ``docs/``, and ``docs_staging/``).

    Write is atomic: temp file -> os.replace. Failures are logged per-group
    and never propagate. A partial write (crash between tmp-write and replace)
    leaves a ``.json.tmp`` file which is ignored on the next read.

    Called from ``run_git_reingestion`` at the Slice 1 reserved seam, inside
    the ``project.status == "ready"`` guard, immediately after the fingerprint
    baseline is persisted and before ``db.commit()``.
    """
    scan_jobs_dir = config.intodoc_output_dir / project_id / "scan" / "jobs"
    if not scan_jobs_dir.is_dir():
        logger.warning(
            "persist_group_prev_scans: scan jobs dir not found for %s: %s",
            project_id,
            scan_jobs_dir,
        )
        return

    prev_scan_dir = output_dir / "prev_scan"
    try:
        prev_scan_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "persist_group_prev_scans: cannot create prev_scan dir for %s: %s",
            project_id,
            exc,
        )
        return

    for group_dir in sorted(scan_jobs_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        group = group_dir.name
        try:
            jobs = _read_group_jobs_from_scan(
                config.intodoc_output_dir, project_id, group
            )
            dest = prev_scan_dir / f"{group}.json"
            tmp = prev_scan_dir / f"{group}.json.tmp"
            tmp.write_text(
                json.dumps(jobs, ensure_ascii=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(tmp, dest)
        except OSError as exc:
            logger.warning(
                "persist_group_prev_scans: failed to write prev scan for group %s: %s",
                group,
                exc,
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "persist_group_prev_scans: serialization error for group %s: %s",
                group,
                exc,
            )


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

    return changed_groups


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

    # Evict every staged slice belonging to a changed group: {group}.md plus
    # any {group}_part* slice (large-group _part1/_part2 splits AND
    # case-collision _part_ci{k} slices). Matching actual directory-entry
    # stems case-sensitively also prevents unlinking a case-colliding sibling
    # group's file on case-insensitive filesystems.
    for staged in list(staging_analysis.glob("*.md")):
        if any(
            analysis_stem_matches_group(staged.stem, group)
            for group in changed_groups
        ):
            staged.unlink()

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


def _manual_full_rebuild_summary(
    *,
    project_id: str,
    before_sha: str,
    after_sha: str,
    triggered_by: str,
    touched_docs: list[str],
) -> dict[str, object]:
    """Build the git-update summary for a successful manual full rebuild.

    ``reason_code`` is assigned UNCONDITIONALLY here (never derived from
    ``triggered_by``, which is caller-supplied free text) -- this is the
    single, structural signal ``services/drift_summary.compute_drift_summary``
    uses to reset the drift counters (Slice 5, US-7/US-8). Keying the reset
    on the code path (this function, only reachable when ``full_rederive``
    is True) rather than on a string convention closes the "smuggled reset"
    risk analyzed in .plans/slice-5-drift-manual-rebuild/critique.md
    (Attack 3).
    """
    return {
        "trigger": triggered_by,
        "mode": "git_reingestion",
        "action": "full_regen",
        "outcome": "full_regen",
        "reason_code": config.manual_full_rebuild_reason_code,
        "decision_strategy": "manual_full_rebuild",
        "project_id": project_id,
        "before": before_sha,
        "after": after_sha,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "reason": "Operator-triggered manual full rebuild",
        "touched_docs": list(touched_docs),
    }


def _skip_summary(
    project_id: str,
    before_sha: str,
    after_sha: str,
    triggered_by: str,
) -> dict[str, object]:
    """Build the git-update summary for a fingerprint-identical (skipped) push."""
    return {
        "trigger": triggered_by,
        "mode": "git_reingestion",
        "action": "skip",
        "outcome": "skip",
        "reason_code": "fingerprint_no_change",
        "decision_strategy": "scan_fingerprint",
        "project_id": project_id,
        "before": before_sha,
        "after": after_sha,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "reason": "Scan output unchanged; no regeneration needed",
    }


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

    Delegates to the pure ``ImpactRouter`` so the (docs_for_group union
    cross_cutting) resolution lives in exactly one place (Slice 1, REC-1).

    Returns None when no manifest exists (pre-baseline) so callers know the
    preview is unavailable rather than empty; an empty list when groups resolve
    but map to no docs.
    """
    plan = ImpactRouter.plan(set(changed_groups), dependency)
    if plan.manifest_missing:
        return None
    return plan.targeted_docs


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
