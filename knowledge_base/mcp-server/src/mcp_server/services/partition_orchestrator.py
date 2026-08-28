"""Composite-upload orchestration: one scoped doc-generation run per sub-project.

A composite upload (several independent exports zipped into one scan) or an
oversized single-root upload cannot run the legacy pipeline whole -- the
per-run model-call budget starves and Phase 2 coverage collapses (verified on
the werkorder scan: 429 units, ~350 ``budget_exhaustion`` skips). The
deterministic partition planner (``providers/custom/partition_planner``) splits
such a scan into sub-projects that each fit the budget; this module is the
"hands" that runs the legacy pipeline once per partition:

* each partition run is a full, independent ``DocGenerationCoordinator``
  generation with its own staging dir, generation lock, call budget, atomic
  swap and DB run record, publishing to ``output/{project_id}/{subproject}/docs``;
* before any partition runs, the deterministic upload-level cross-project
  index (job-call edges across sub-projects, shared systems) is written to
  ``output/{project_id}/topology/`` -- partition runs consume it to NOTE their
  external references, and it is rendered/pushed as the upload-level
  connective-topology document;
* single-partition scans never reach this module -- the caller keeps the
  legacy single-run path unchanged.

Failure isolation: one failed sub-project never rolls back or blocks the
others; its staging is preserved for resume, and the orchestration reports a
project-level error listing the failed sub-projects while the succeeded ones
stay published.

One skip rule, two callers
--------------------------
Whether a sub-project is regenerated or reused is decided in exactly one place
(:func:`decide_partition_actions`). The run loop below consumes those decisions,
and so does the read-only dry run behind
``GET /mcp/projects/{project_id}/generation-plan``. A second copy of the rule
would let the dialog that tells an operator "5 of 6 sub-projects will be reused"
drift from what the run actually does, which is worse than showing nothing.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mcp_server.config import config
from mcp_server.db.session import async_session_factory
from mcp_server.providers.custom.partition_fingerprint import (
    compute_partition_fingerprints,
    unattributable_digest,
)
from mcp_server.providers.custom.partition_planner import (
    PartitionPlan,
    SHARED_PARTITION,
    build_cross_project_index,
    cross_project_index_path,
    partition_output_subdir,
    plan_partitions,
    render_cross_project_topology_md,
    upload_topology_dir,
)
from mcp_server.providers.custom.validators import build_facts_manifest
from mcp_server.services.atomic_swap import swap_docs
from mcp_server.services.project_registry import ProjectRegistry
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.rag_support_package import (
    build_entity_aliases,
    collect_internal_artifacts,
    replace_analysis_cache_from_rag_support,
)

if TYPE_CHECKING:
    from mcp_server.providers.custom.models import FactsManifest
    from mcp_server.services.claude_service import DocGenerationCoordinator

logger = logging.getLogger(__name__)

# Upload-level artifacts (deterministic, no LLM).
CROSS_PROJECT_TOPOLOGY_DOC = "CROSS_PROJECT_TOPOLOGY.md"
PARTITION_STATE_FILENAME = "partition_state.json"

#: Reserved top-level key in ``partition_state.json`` for run-level metadata
#: that belongs to no single partition (the shared-artifact digest, the name a
#: migrated flat source tree was given). Every other top-level key is a
#: partition name. A partition would have to be literally named ``__meta__`` to
#: collide -- ``_partition_entry`` filters the key out of partition lookups
#: and :func:`_set_state_meta` refuses to write when such a partition exists,
#: so the collision degrades to "no metadata" rather than to corruption.
STATE_META_KEY = "__meta__"

# Actions a sub-project can be assigned for the next run.
ACTION_GENERATE = "generate"
ACTION_REUSE = "reuse"

# Why a sub-project got its action. This set is the API contract for
# ``GET /mcp/projects/{project_id}/generation-plan`` -- see
# ``schemas.GenerationPlanPartition.reason``, which pins the same values.
REASON_NEW = "new"
REASON_CHANGED = "changed"
REASON_UNCHANGED = "unchanged"
REASON_NO_PUBLISHED_DOCS = "no_published_docs"
REASON_FINGERPRINT_UNAVAILABLE = "fingerprint_unavailable"
REASON_SHARED_ARTIFACT_CHANGED = "shared_artifact_changed"
REASON_FLAT_LAYOUT_ADOPTION = "flat_layout_adoption"

PARTITION_REASONS: frozenset[str] = frozenset({
    REASON_NEW,
    REASON_CHANGED,
    REASON_UNCHANGED,
    REASON_NO_PUBLISHED_DOCS,
    REASON_FINGERPRINT_UNAVAILABLE,
    REASON_SHARED_ARTIFACT_CHANGED,
    REASON_FLAT_LAYOUT_ADOPTION,
})

# Project-wide invalidation reasons.
GLOBAL_SHARED_ROUTINE_CHANGED = "shared_routine_changed"
_SHARED_ROUTINE_DETAIL = (
    "A shared Talend routine changed. Routines carry no source-root "
    "attribution, so every sub-project must be re-read."
)

# Notes rendered verbatim by both UI surfaces. Kept here, not in the route, so
# the dashboard and the RAG frontend cannot disagree about the caveats.
NOTE_CROSS_PROJECT_REFS = (
    "Cross-project references inside reused sub-projects are not rewritten. "
    "The landscape topology document is always rebuilt."
)
NOTE_FINGERPRINT_UNAVAILABLE = (
    "Some sub-projects have scan inputs that cannot be digested with "
    "confidence, so they are regenerated instead of reused."
)
NOTE_SIZE_SPLIT = (
    "Some sub-projects are split into several runs so each fits the per-run "
    "model-call budget. Each split is planned separately."
)


@dataclass
class PartitionRunOutcome:
    """Result of one sub-project's scoped generation run."""

    partition: str
    subdir: str
    run_id: int | None = None
    success: bool = False
    skipped: bool = False
    doc_count: int = 0
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GlobalInvalidation:
    """Something outside any one sub-project forces the whole project to rerun."""

    reason: str
    detail: str


@dataclass
class PartitionDecision:
    """What the next run will do with one sub-project, and why."""

    name: str
    subdir: str
    action: str
    reason: str
    fingerprint: str
    unit_count: int = 0
    doc_count: int = 0
    last_generated_at: str | None = None

    @property
    def reuse(self) -> bool:
        return self.action == ACTION_REUSE


@dataclass
class GenerationDecisions:
    """The full per-sub-project verdict for the next generation run.

    Carries the *inputs* of the verdict (``fingerprints``, ``state``) as well as
    the verdict itself, because the run path needs both: it writes those exact
    fingerprints back into ``partition_state.json`` when a sub-project
    publishes. Recomputing them there would open a window in which the digest
    that was compared and the digest that was recorded differ.
    """

    project_id: str
    scan_available: bool
    composite: bool
    decisions: list[PartitionDecision] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    global_invalidation: GlobalInvalidation | None = None
    notes: list[str] = field(default_factory=list)
    shared_artifact_digest: str = ""

    @property
    def partition_count(self) -> int:
        """Number of scoped runs the plan describes.

        A non-composite project has no partitions to list but still costs one
        run, which is what the ``composite: false`` branch of the API contract
        expresses as ``will_generate: 1``.
        """
        return len(self.decisions) if self.composite else (1 if self.scan_available else 0)

    @property
    def will_generate(self) -> int:
        if not self.composite:
            return 1
        return sum(1 for d in self.decisions if d.action == ACTION_GENERATE)

    @property
    def will_reuse(self) -> int:
        if not self.composite:
            return 0
        return sum(1 for d in self.decisions if d.action == ACTION_REUSE)

    def decision_for(self, partition_name: str) -> PartitionDecision | None:
        for decision in self.decisions:
            if decision.name == partition_name:
                return decision
        return None


def _load_partition_plan(
    project_id: str,
) -> "tuple[PartitionPlan, FactsManifest] | None":
    """Build the scan's facts manifest and partition plan, or ``None``.

    ``None`` means "no usable scan" -- missing, unreadable, or structurally
    invalid. The one loader for both the run path and the read-only plan
    endpoint, so a scan that the run path would refuse can never be described
    as plannable by the dialog. Never raises.
    """
    try:
        scan_output_dir = config.intodoc_output_dir / project_id
        facts = build_facts_manifest(project_id, scan_output_dir)
        return plan_partitions(facts), facts
    except Exception:
        logger.warning(
            "Partition planning failed for %s -- no usable scan",
            project_id,
            exc_info=True,
        )
        return None


def plan_composite_generation(
    project_id: str,
) -> "tuple[PartitionPlan, FactsManifest] | None":
    """Deterministic partition plan for the orchestrator.

    Returns ``None`` whenever the legacy single-run path must be used: planner
    disabled, scan missing/corrupt, or the scan resolves to a single partition
    (every existing single-root project). Never raises.
    """
    if not config.docgen_partition_orchestration:
        return None
    loaded = _load_partition_plan(project_id)
    if loaded is None:
        return None
    plan, facts = loaded
    if len(plan.partitions) <= 1:
        return None
    return plan, facts


def build_generation_plan(project_id: str, *, resume: bool = True) -> GenerationDecisions:
    """Read-only dry run: what the next generation of *project_id* would do.

    Answers the three states the UI has to distinguish, and never raises:

    * no usable scan -> ``scan_available=False`` (the operator must scan first);
    * usable scan, single sub-project (or partition orchestration disabled) ->
      ``composite=False``, no partition list, one run;
    * composite -> one :class:`PartitionDecision` per scoped run.

    The gating mirrors :func:`plan_composite_generation` exactly, so
    ``composite`` is a statement about which code path the next run will take,
    not merely about how many source roots the scan happens to have.
    """
    loaded = _load_partition_plan(project_id)
    if loaded is None:
        return GenerationDecisions(
            project_id=project_id, scan_available=False, composite=False
        )
    plan, facts = loaded
    composite = config.docgen_partition_orchestration and len(plan.partitions) > 1
    if not composite:
        return GenerationDecisions(
            project_id=project_id, scan_available=True, composite=False
        )
    return decide_partition_actions(project_id, plan, facts, resume=resume)


def decide_partition_actions(
    project_id: str,
    plan: PartitionPlan,
    facts: "FactsManifest",
    *,
    resume: bool = True,
) -> GenerationDecisions:
    """THE skip rule. One sub-project, one verdict, one place in the codebase.

    A sub-project is reused when its recorded per-partition digest still matches
    the current scan AND its published docs are still on disk
    (:func:`_partition_completed`), or when it is the unambiguous owner of a
    pre-existing flat docs tree that can be adopted
    (:func:`_flat_adoption_candidate`). Everything else regenerates, and the
    ``reason`` says which of the five failure modes applied -- an operator
    approving model spend deserves to know whether they are paying for a new
    sub-project, a changed one, a shared-routine edit, or a digest the server
    could not compute.

    ``resume=False`` (an explicitly forced full rebuild) suppresses every reuse
    but leaves the reason describing the *inputs*, so a forced run still reports
    honestly that a sub-project was ``unchanged``.

    Never raises: an unreadable digest degrades to "regenerate" and a corrupt
    state file reads as empty, both of which cost model calls rather than
    publishing stale documentation.
    """
    scan_output_dir = config.intodoc_output_dir / project_id
    output_root = config.project_root / "output" / project_id

    fingerprints = _partition_fingerprints(scan_output_dir, plan, facts)
    state = _read_partition_state(project_id)
    shared_digest = _shared_artifact_digest(scan_output_dir)
    global_invalidation = _detect_global_invalidation(state, shared_digest)
    adoption = _flat_adoption_candidate(project_id, plan, state)

    decisions: list[PartitionDecision] = []
    for part in plan.partitions:
        subdir = partition_output_subdir(part.name) or part.name
        part_output = output_root / subdir
        docs_dir = part_output / "docs"
        fingerprint = fingerprints.get(part.name, "")
        entry = _partition_entry(state, part.name)
        doc_count = _count_public_docs(docs_dir)
        generated_at = _entry_generated_at(entry) or _docs_generated_at(docs_dir)

        if _partition_completed(state, part.name, fingerprint, part_output):
            action, reason = ACTION_REUSE, REASON_UNCHANGED
        elif part.name == adoption and fingerprint:
            # The docs exist, just at the pre-composite location; the run path
            # relocates them instead of paying to regenerate them.
            action, reason = ACTION_REUSE, REASON_FLAT_LAYOUT_ADOPTION
            flat_docs = _flat_docs_dir(project_id)
            doc_count = _count_public_docs(flat_docs)
            generated_at = _docs_generated_at(flat_docs)
        elif not fingerprint:
            action, reason = ACTION_GENERATE, REASON_FINGERPRINT_UNAVAILABLE
        elif entry is None:
            action, reason = ACTION_GENERATE, REASON_NEW
        elif entry.get("partition_fingerprint") != fingerprint:
            action, reason = (
                ACTION_GENERATE,
                REASON_SHARED_ARTIFACT_CHANGED
                if global_invalidation is not None
                else REASON_CHANGED,
            )
        else:
            # Digest matches but nothing is published: the docs were deleted,
            # or a previous run recorded state without completing the swap.
            action, reason = ACTION_GENERATE, REASON_NO_PUBLISHED_DOCS

        if action == ACTION_REUSE and not resume:
            # Forced full rebuild. The reason still describes the inputs.
            action = ACTION_GENERATE

        decisions.append(
            PartitionDecision(
                name=part.name,
                subdir=subdir,
                action=action,
                reason=reason,
                fingerprint=fingerprint,
                unit_count=part.unit_count,
                doc_count=doc_count,
                last_generated_at=generated_at,
            )
        )

    return GenerationDecisions(
        project_id=project_id,
        scan_available=True,
        composite=True,
        decisions=decisions,
        fingerprints=fingerprints,
        state=state,
        global_invalidation=global_invalidation,
        notes=_plan_notes(plan, decisions),
        shared_artifact_digest=shared_digest,
    )


def _plan_notes(plan: PartitionPlan, decisions: list[PartitionDecision]) -> list[str]:
    """Caveats an operator must see before approving a partial regeneration."""
    notes = [NOTE_CROSS_PROJECT_REFS]
    if any(d.reason == REASON_FINGERPRINT_UNAVAILABLE for d in decisions):
        notes.append(NOTE_FINGERPRINT_UNAVAILABLE)
    if any(part.split_groups is not None for part in plan.partitions):
        notes.append(NOTE_SIZE_SPLIT)
    for decision in decisions:
        if decision.reason == REASON_FLAT_LAYOUT_ADOPTION:
            notes.append(
                "Existing project-level documentation will be adopted as "
                f"sub-project '{decision.name}' instead of being regenerated."
            )
    return notes


def write_cross_project_artifacts(
    project_id: str,
    facts: "FactsManifest",
    plan: PartitionPlan,
) -> dict[str, Any]:
    """Build and persist the upload-level cross-project index + connective map.

    Written BEFORE any partition run so each scoped run can stage its own
    ``cross_project_refs.md`` from it. Purely deterministic (scan facts only).
    """
    scan_output_dir = config.intodoc_output_dir / project_id
    scan_manifest = _load_json(scan_output_dir / "scan" / "manifest.json")
    landscape = _load_json(scan_output_dir / "scan" / "landscape.json")

    index = build_cross_project_index(
        facts, plan, scan_manifest=scan_manifest, landscape=landscape
    )

    topo_dir = upload_topology_dir(project_id)
    topo_dir.mkdir(parents=True, exist_ok=True)
    cross_project_index_path(project_id).write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (topo_dir / CROSS_PROJECT_TOPOLOGY_DOC).write_text(
        render_cross_project_topology_md(index), encoding="utf-8"
    )
    return index


async def run_partitioned_generation(
    project_id: str,
    run_id: int,
    log_mgr,
    coordinator: "DocGenerationCoordinator",
    *,
    plan: PartitionPlan,
    facts: "FactsManifest",
    resume: bool = True,
    generate_kwargs: dict[str, Any] | None = None,
) -> list[PartitionRunOutcome]:
    """Run one scoped legacy generation per partition and publish the results.

    ``run_id`` is the orchestration-level GenerateRun created by the route; it
    is completed at the end with aggregated totals. Each partition gets its own
    child GenerateRun (``triggered_by="composite"``, ``partition_name=<name>``)
    so per-sub-project cost/duration/verdict stay inspectable.
    """
    generate_kwargs = dict(generate_kwargs or {})
    output_root = config.project_root / "output" / project_id
    scan_output_dir = config.intodoc_output_dir / project_id

    await log_mgr.emit(
        project_id,
        "Composite upload detected -- running one scoped generation per "
        f"sub-project:\n{plan.table()}",
        level="info",
    )

    # Upload-level deterministic connective layer, consumed by every partition run.
    try:
        write_cross_project_artifacts(project_id, facts, plan)
        await log_mgr.emit(
            project_id,
            f"Cross-project index written to {cross_project_index_path(project_id)}",
            level="info",
        )
    except Exception as exc:  # noqa: BLE001 - additive layer must not block generation
        logger.warning("Cross-project index build failed for %s: %s", project_id, exc, exc_info=True)
        await log_mgr.emit(
            project_id,
            f"Cross-project index build failed (runs continue without it): {exc}",
            level="warning",
        )

    # Whole-scan digest: still the identity of the upload-level connective
    # layer, which by definition depends on every sub-project.
    fingerprint = _scan_fingerprint(scan_output_dir)
    # THE skip rule, shared verbatim with the read-only generation-plan
    # endpoint: per-sub-project digests decide whether an individual partition
    # may be reused, so a push to one repo moves only that repo's digest and
    # only that repo pays for regeneration.
    decisions = decide_partition_actions(project_id, plan, facts, resume=resume)
    # Establish the shared-artifact baseline before anything can be skipped;
    # a fully-skipped run publishes nothing and would never record it.
    record_shared_artifact_baseline(project_id, decisions.shared_artifact_digest)
    # A project that was generated flat keeps its documents when it becomes
    # composite. Must precede the loop: it is what makes the owning
    # sub-project's reuse decision physically true on disk.
    decisions = await _adopt_flat_layout(
        project_id,
        plan,
        facts,
        decisions=decisions,
        scan_fingerprint=fingerprint,
        log_mgr=log_mgr,
    )
    partition_fingerprints = decisions.fingerprints
    partition_state = decisions.state
    await log_mgr.emit(
        project_id,
        f"Incremental plan: {decisions.will_generate} sub-project(s) to "
        f"generate, {decisions.will_reuse} to reuse ("
        + ", ".join(f"{d.name}={d.action}:{d.reason}" for d in decisions.decisions)
        + ")"
        + (
            f"\n{decisions.global_invalidation.detail}"
            if decisions.global_invalidation is not None
            else ""
        ),
        level="info",
    )

    outcomes: list[PartitionRunOutcome] = []
    cancelled = False
    for part in plan.partitions:
        subdir = partition_output_subdir(part.name) or part.name
        outcome = PartitionRunOutcome(partition=part.name, subdir=subdir)
        outcomes.append(outcome)
        part_output = output_root / subdir
        part_fingerprint = partition_fingerprints.get(part.name, "")
        decision = decisions.decision_for(part.name)

        if decision is not None and decision.reuse:
            outcome.skipped = True
            outcome.success = True
            outcome.doc_count = _count_public_docs(part_output / "docs")
            explanation = (
                "adopted the project's existing documentation"
                if decision.reason == REASON_FLAT_LAYOUT_ADOPTION
                else "up to date for current scan"
            )
            await log_mgr.emit(
                project_id,
                f"[{part.name}] {explanation} -- skipping "
                f"({outcome.doc_count} docs published)",
                level="info",
            )
            continue

        child_run_id = await _create_child_run(project_id, part.name)
        outcome.run_id = child_run_id
        await log_mgr.emit(
            project_id,
            f"[{part.name}] starting scoped run "
            f"({part.unit_count} units, {part.group_count} groups, "
            f"budget {part.call_budget}/{plan.cap}) -> {part_output / 'docs'}",
            level="info",
        )

        try:
            result = await coordinator.generate_docs(
                project_id,
                resume=resume,
                run_id=child_run_id if child_run_id is not None else run_id,
                partition=part,
                **generate_kwargs,
            )
        except asyncio.CancelledError:
            outcome.error = "Generation cancelled by user"
            await _complete_child_run(child_run_id, status="failed", error=outcome.error)
            await log_mgr.emit(project_id, "Generation cancelled by user.", level="warning")
            cancelled = True
            break
        except Exception as exc:  # noqa: BLE001 - isolate partition failures
            logger.exception("Partition run crashed: %s/%s", project_id, part.name)
            outcome.error = str(exc)
            outcome.result = {"success": False, "error": str(exc)}
            await _complete_child_run(child_run_id, status="failed", error=str(exc))
            continue

        outcome.result = result
        staging_dir = part_output / "docs_staging"
        success = bool(result.get("success"))
        doc_count = _count_public_docs(staging_dir)

        if success and doc_count > 0:
            try:
                swap_docs(part_output)
                replace_analysis_cache_from_rag_support(part_output)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Swap failed for %s/%s", project_id, part.name)
                outcome.error = f"Publish swap failed: {exc}"
                await _complete_child_run(
                    child_run_id, status="failed", error=outcome.error, result=result
                )
                continue

            outcome.success = True
            outcome.doc_count = doc_count
            quality_score = _score_partition_docs(project_id, part_output / "docs")

            rag_errors = await _push_partition_docs(
                project_id, subdir, part_output / "docs", log_mgr
            )
            if rag_errors:
                await log_mgr.emit(
                    project_id,
                    f"[{part.name}] RAG push errors (docs are saved): {rag_errors}",
                    level="error",
                )

            await _complete_child_run(
                child_run_id,
                status="completed",
                doc_count=doc_count,
                result=result,
                quality_score=quality_score,
                error="; ".join(rag_errors) if rag_errors else None,
                run_verdict="published_with_rag_error" if rag_errors else "published",
            )
            partition_state[part.name] = {
                # The skip decision reads this one.
                "partition_fingerprint": part_fingerprint,
                # Whole-scan digest kept for diagnostics only: it answers "which
                # scan was this partition last published from", which the
                # per-partition digest deliberately cannot.
                "scan_fingerprint": fingerprint,
                "doc_count": doc_count,
                "run_id": child_run_id,
                # Reported as `last_generated_at` by the generation-plan
                # endpoint. Recorded at publish time rather than derived from
                # file mtimes, which any later touch would falsify.
                "generated_at": _utc_now_iso(),
            }
            # Refresh the shared-artifact baseline: these docs were produced
            # from THIS digest, so a later change to it is what invalidates them.
            _set_state_meta(
                partition_state,
                plan,
                shared_artifact_digest=decisions.shared_artifact_digest,
            )
            _write_partition_state(project_id, partition_state)
            await log_mgr.emit(
                project_id,
                f"[{part.name}] published {doc_count} docs to {part_output / 'docs'}",
                level="info",
            )
        elif success:
            # Success with no docs is a failure (mirrors the single-run path).
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            outcome.error = "Generation produced no documents"
            await _complete_child_run(
                child_run_id, status="failed", error=outcome.error, result=result
            )
            await log_mgr.emit(
                project_id,
                f"[{part.name}] produced no documents -- marked failed",
                level="error",
            )
        else:
            outcome.error = result.get("error") or "Doc generation failed"
            await _complete_child_run(
                child_run_id, status="failed", error=outcome.error, result=result
            )
            await log_mgr.emit(
                project_id,
                f"[{part.name}] failed: {outcome.error} "
                "(staging preserved -- generate again to resume this sub-project)",
                level="warning",
            )

    succeeded = [o for o in outcomes if o.success]
    failed = [o for o in outcomes if not o.success and not o.skipped]
    total_docs = sum(o.doc_count for o in succeeded)

    # Upload-level connective topology + internal support layers in RAG.
    if succeeded and not cancelled:
        _write_composite_org_topology(project_id, fingerprint)
        await _push_upload_level_artifacts(project_id, log_mgr)
        await sync_internal_layers_for_subprojects(
            project_id,
            output_root,
            [(o.subdir, o.run_id or run_id) for o in outcomes if o.success],
            log_mgr,
        )

    # Aggregate + finalize the orchestration run and the project status.
    totals = _aggregate_totals([o.result for o in outcomes if o.result])
    error_message = (
        "Sub-project runs failed: "
        + ", ".join(f"{o.partition} ({o.error})" for o in failed)
        if failed
        else None
    )
    if cancelled and error_message is None:
        error_message = "Generation cancelled by user"

    async with async_session_factory() as db:
        registry = ProjectRegistry(db)
        if succeeded and not failed and not cancelled:
            await registry.update_status(project_id, "ready")
        elif succeeded:
            await registry.update_status(project_id, "error", error_message=error_message)
        else:
            await registry.update_status(
                project_id, "error", error_message=error_message or "Doc generation failed"
            )
        if total_docs:
            await registry.update_generate_result(project_id, doc_count=total_docs)
        log_path = log_mgr.get_current_log_path(project_id)
        await registry.complete_generate_run(
            run_id,
            status="completed" if succeeded and not failed and not cancelled else "failed",
            doc_count=total_docs,
            cost_usd=totals.get("cost_usd"),
            duration_ms=totals.get("duration_ms"),
            input_tokens=totals.get("input_tokens"),
            output_tokens=totals.get("output_tokens"),
            model_name=totals.get("model_name"),
            write_model_name=totals.get("write_model_name"),
            error_message=error_message,
            log_file=log_path.name if log_path else None,
            run_verdict="published" if succeeded and not failed and not cancelled else "failed",
        )
        await db.commit()

    await log_mgr.emit(
        project_id,
        f"Composite generation finished: {len(succeeded)}/{len(outcomes)} sub-projects "
        f"published, {total_docs} docs total"
        + (f"; failed: {', '.join(o.partition for o in failed)}" if failed else ""),
        level="error" if failed else "info",
    )
    return outcomes


# ------------------------------------------------------------------------- #
# Helpers
# ------------------------------------------------------------------------- #


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _scan_fingerprint(scan_output_dir: Path) -> str:
    try:
        from mcp_server.services.business_topology.evidence import compute_scan_fingerprint

        return compute_scan_fingerprint(scan_output_dir)
    except Exception:  # noqa: BLE001 - fingerprint is only a resume optimization
        return ""


def _partition_fingerprints(
    scan_output_dir: Path, plan: PartitionPlan, facts: FactsManifest
) -> dict[str, str]:
    """Per-partition scan digests, or all-empty when they cannot be computed.

    All-empty degrades to "regenerate every partition" -- the pre-incremental
    behaviour -- rather than skipping on a digest we do not trust.
    """
    try:
        return compute_partition_fingerprints(scan_output_dir, plan, facts)
    except Exception as exc:  # noqa: BLE001 - never block generation on a skip optimization
        logger.warning(
            "Per-partition fingerprints unavailable (%s); every sub-project will regenerate",
            exc,
            exc_info=True,
        )
        return {}


def _partition_state_path(project_id: str) -> Path:
    return upload_topology_dir(project_id) / PARTITION_STATE_FILENAME


def _read_partition_state(project_id: str) -> dict[str, Any]:
    return _load_json(_partition_state_path(project_id))


def _write_partition_state(project_id: str, state: dict[str, Any]) -> None:
    path = _partition_state_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _partition_completed(
    state: dict[str, Any],
    partition_name: str,
    fingerprint: str,
    part_output: Path,
) -> bool:
    """A partition is up to date when its recorded PER-PARTITION fingerprint
    matches the current scan AND its published docs still exist on disk.

    *fingerprint* is this partition's own digest, not the whole-scan digest, so
    a sibling sub-project changing does not invalidate this one.

    An empty *fingerprint* never skips: the fingerprinter returns "" when it
    cannot determine a partition's inputs with confidence, and a regeneration is
    cheaper than a stale publish. State written before per-partition digests
    existed carries no ``partition_fingerprint`` key and so regenerates once.
    """
    if not fingerprint:
        return False
    entry = _partition_entry(state, partition_name)
    if entry is None or entry.get("partition_fingerprint") != fingerprint:
        return False
    return _count_public_docs(part_output / "docs") > 0


def _count_public_docs(docs_dir: Path) -> int:
    if not docs_dir.exists():
        return 0
    return len([p for p in docs_dir.glob("*.md") if not p.name.startswith("_")])


# ------------------------------------------------------------------------- #
# Durable state: partition entries, run-level metadata, timestamps
# ------------------------------------------------------------------------- #


def _partition_entry(state: dict[str, Any], partition_name: str) -> dict[str, Any] | None:
    """One partition's recorded state, or ``None`` when there is none.

    Also returns ``None`` for the reserved metadata key, so a caller can never
    mistake run-level metadata for a published partition.
    """
    if partition_name == STATE_META_KEY:
        return None
    entry = state.get(partition_name)
    return entry if isinstance(entry, dict) else None


def _state_meta(state: dict[str, Any]) -> dict[str, Any]:
    meta = state.get(STATE_META_KEY)
    return meta if isinstance(meta, dict) else {}


def _set_state_meta(state: dict[str, Any], plan: PartitionPlan | None = None, **values) -> None:
    """Merge run-level metadata into *state* in place (``None`` values ignored).

    Refuses to write when the plan contains a partition whose name is the
    reserved key: losing the metadata costs one explanatory banner, whereas
    overwriting a real partition entry would cost a needless regeneration and
    could mask a stale publish.
    """
    if plan is not None and STATE_META_KEY in plan.names():
        logger.warning(
            "Partition named %r collides with the reserved state key -- "
            "skipping run metadata",
            STATE_META_KEY,
        )
        return
    meta = dict(_state_meta(state))
    meta.update({k: v for k, v in values.items() if v is not None})
    state[STATE_META_KEY] = meta


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _entry_generated_at(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    value = entry.get("generated_at")
    return str(value) if value else None


def _docs_generated_at(docs_dir: Path) -> str | None:
    """When a docs tree was last written, from the newest public doc's mtime.

    The fallback for state entries written before ``generated_at`` existed, and
    the only available answer for adopted flat docs (no run of this orchestrator
    ever produced them). Returns ``None`` rather than guessing when the tree is
    absent or unreadable.
    """
    try:
        stamps = [
            p.stat().st_mtime
            for p in docs_dir.glob("*.md")
            if not p.name.startswith("_")
        ]
    except OSError:
        return None
    if not stamps:
        return None
    return datetime.datetime.fromtimestamp(
        max(stamps), datetime.UTC
    ).isoformat(timespec="seconds")


# ------------------------------------------------------------------------- #
# Shared (unattributable) artifacts
# ------------------------------------------------------------------------- #


def _shared_artifact_digest(scan_output_dir: Path) -> str:
    """Digest of the scan artifacts that belong to no single sub-project.

    Empty string when it cannot be computed, which suppresses the global
    invalidation banner without affecting any skip decision -- the digest is
    already mixed into every per-partition fingerprint, so correctness never
    depends on this value, only the explanation does.
    """
    try:
        return unattributable_digest(scan_output_dir)
    except Exception:  # noqa: BLE001 - explanation only, never blocks a run
        logger.warning(
            "Shared-artifact digest unavailable for %s", scan_output_dir, exc_info=True
        )
        return ""


def _detect_global_invalidation(
    state: dict[str, Any], shared_digest: str
) -> GlobalInvalidation | None:
    """Explain a project-wide regeneration, or ``None``.

    Detection needs a BASELINE: the shared digest recorded by the previous run.
    Without one (a project whose state predates this field) the honest answer is
    ``None`` -- the partitions are still invalidated correctly by their own
    fingerprints, we simply cannot claim to know that a routine is the cause.
    """
    recorded = str(_state_meta(state).get("shared_artifact_digest") or "")
    if not shared_digest or not recorded or recorded == shared_digest:
        return None
    return GlobalInvalidation(
        reason=GLOBAL_SHARED_ROUTINE_CHANGED, detail=_SHARED_ROUTINE_DETAIL
    )


def record_shared_artifact_baseline(project_id: str, shared_digest: str) -> None:
    """Persist the shared-artifact digest when no baseline is recorded yet.

    Called at the START of a composite run rather than after publishing,
    because a run in which every sub-project is skipped publishes nothing and
    would otherwise never establish the baseline -- leaving the NEXT shared
    routine change unexplainable. Recording it early is safe: the digest only
    ever explains an invalidation, and each partition's own fingerprint (which
    embeds the same digest) is what actually gates a skip.
    """
    if not shared_digest:
        return
    state = _read_partition_state(project_id)
    if str(_state_meta(state).get("shared_artifact_digest") or ""):
        return
    _set_state_meta(state, shared_artifact_digest=shared_digest)
    try:
        _write_partition_state(project_id, state)
    except OSError:  # metadata is advisory, never a gate
        logger.warning(
            "Could not record the shared-artifact baseline for %s", project_id,
            exc_info=True,
        )


# ------------------------------------------------------------------------- #
# Flat -> composite docs adoption
# ------------------------------------------------------------------------- #


def _flat_docs_dir(project_id: str) -> Path:
    """The pre-composite docs location (``output/{project_id}/docs``)."""
    return config.project_root / "output" / project_id / "docs"


def record_flat_source_migration(project_id: str, subproject_name: str) -> None:
    """Record which sub-project inherited a migrated flat source tree.

    Written by the additive-source endpoint at the moment it moves
    ``{repos_dir}/{project_id}/`` into ``{name}.__intodoc_repo__/``, which is the
    only moment the answer is known for certain. Afterwards the two trees are
    indistinguishable on disk -- both are marker directories -- and mtimes are
    not evidence (a move preserves them for the contents and resets them for the
    directory). Without this hint the flat docs cannot be attributed to a
    sub-project and the whole project regenerates, which is exactly the cost the
    feature exists to avoid.
    """
    if not subproject_name:
        return
    try:
        state = _read_partition_state(project_id)
        _set_state_meta(state, flat_source_subproject=subproject_name)
        _write_partition_state(project_id, state)
    except OSError:
        logger.warning(
            "Could not record the flat-source migration for %s/%s",
            project_id,
            subproject_name,
            exc_info=True,
        )


def _flat_adoption_candidate(
    project_id: str, plan: PartitionPlan, state: dict[str, Any]
) -> str | None:
    """The sub-project that owns a pre-existing flat docs tree, or ``None``.

    Preconditions, all required:

    * a flat ``output/{project_id}/docs`` with at least one published document
      exists (there is something to adopt);
    * the owning partition is IDENTIFIABLE. Two signals, in order:
      the migration hint recorded by the additive-source endpoint
      (:func:`record_flat_source_migration`), else the ``__shared__`` partition
      -- composite scans put every unmarked source root there, and an unmarked
      root is by definition the tree that predates the markers, which is what a
      Git project's additive-members path leaves behind;
    * that partition has no state entry and nothing published under its own
      subdirectory, so adoption cannot overwrite a real run's output.

    Anything ambiguous returns ``None`` and the project regenerates in full.
    Over-adopting would publish one sub-project's documentation as another's.

    A size-split parent is deliberately unadoptable: the hint names a zip-level
    sub-project, and one flat doc set cannot be attributed to one of several
    budget splits of it. That case costs a full regeneration once.
    """
    if _count_public_docs(_flat_docs_dir(project_id)) == 0:
        return None

    names = set(plan.names())
    hinted = str(_state_meta(state).get("flat_source_subproject") or "")
    if hinted and hinted in names:
        candidates = [hinted]
    elif hinted:
        # The hinted sub-project is not a partition of this scan: it was split
        # for budget, renamed, or the tree was removed. Do not guess.
        return None
    else:
        candidates = [SHARED_PARTITION] if SHARED_PARTITION in names else []

    output_root = config.project_root / "output" / project_id
    viable = [
        name
        for name in candidates
        if _partition_entry(state, name) is None
        and _count_public_docs(
            output_root / (partition_output_subdir(name) or name) / "docs"
        )
        == 0
    ]
    return viable[0] if len(viable) == 1 else None


async def _adopt_flat_layout(
    project_id: str,
    plan: PartitionPlan,
    facts: "FactsManifest",
    *,
    decisions: GenerationDecisions,
    scan_fingerprint: str = "",
    log_mgr=None,
) -> GenerationDecisions:
    """Migrate a flat-layout project's docs into its sub-project subdirectory.

    A project generated BEFORE it became composite published to
    ``output/{project_id}/docs``. Once a second sub-project is added, the same
    documentation belongs at ``output/{project_id}/{subproject}/docs``. Both
    halves of this migration are load-bearing and neither works alone:

    * without the MOVE, the skip predicate's published-docs precondition fails
      (``_partition_completed`` counts docs under the sub-project directory), so
      the sub-project regenerates even with a matching fingerprint;
    * without the STATE ENTRY, there is no recorded fingerprint to match, so it
      regenerates too.

    The recorded fingerprint is the freshly computed one. That is an explicit
    assertion that the existing documents describe this sub-project's current
    inputs -- true because the migration relocates bytes without changing them
    (the source tree moved into a marker directory; the jobs did not change).
    LIMITATION, stated plainly: a flat run records no fingerprint of its own, so
    if the operator ALSO edited code between the last flat generation and this
    run, adoption cannot detect it and the adopted docs are stale until the next
    change moves the digest. The alternative -- never adopting -- costs a full
    regeneration of every project that gains a second sub-project, which is the
    entire saving this feature exists to deliver.

    Called before the partition loop. On any failure the sub-project is demoted
    to ``generate`` and the run proceeds: a failed migration must cost model
    calls, never a lost docs tree.

    Which sub-project owns the flat tree, and whether adopting is safe, is NOT
    decided here: :func:`decide_partition_actions` already answered it (the
    decision carrying ``flat_layout_adoption``), so the dry-run endpoint and this
    executor cannot disagree. *facts* is accepted for signature symmetry with the
    other scan-truth helpers even though the decision it feeds is precomputed.
    """
    decision = next(
        (d for d in decisions.decisions if d.reason == REASON_FLAT_LAYOUT_ADOPTION),
        None,
    )
    if decision is None:
        return decisions

    flat_docs = _flat_docs_dir(project_id)
    target = config.project_root / "output" / project_id / decision.subdir / "docs"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same filesystem, so this is an atomic rename: either the tree is at
        # the old path or at the new one, never half-copied.
        flat_docs.replace(target)
    except OSError as exc:
        logger.warning(
            "Flat-layout docs adoption failed for %s/%s: %s",
            project_id,
            decision.name,
            exc,
            exc_info=True,
        )
        decision.action = ACTION_GENERATE
        decision.reason = REASON_NO_PUBLISHED_DOCS
        if log_mgr is not None:
            await log_mgr.emit(
                project_id,
                f"[{decision.name}] could not adopt the existing documentation "
                f"({exc}) -- regenerating it instead",
                level="warning",
            )
        return decisions

    doc_count = _count_public_docs(target)
    generated_at = _docs_generated_at(target) or _utc_now_iso()
    decisions.state[decision.name] = {
        "partition_fingerprint": decision.fingerprint,
        "scan_fingerprint": scan_fingerprint,
        "doc_count": doc_count,
        "run_id": None,
        # Provenance: these docs were not produced by a scoped run of this
        # orchestrator, so a later investigation can tell adoption from output.
        "adopted_from": "flat",
        "generated_at": generated_at,
    }
    _set_state_meta(
        decisions.state, plan, shared_artifact_digest=decisions.shared_artifact_digest
    )
    _write_partition_state(project_id, decisions.state)

    decision.doc_count = doc_count
    decision.last_generated_at = generated_at

    # ``docs/_rag_support/`` travelled with the move, so the sub-project can
    # have the flat analysis cache a publishing run would have left it. Without
    # this, the next incremental (webhook) regeneration of the adopted
    # sub-project silently falls back to a full regeneration.
    try:
        replace_analysis_cache_from_rag_support(target.parent)
    except Exception:  # noqa: BLE001 - cache is an optimization, never a gate
        logger.warning(
            "Analysis-cache restore after adoption failed for %s/%s",
            project_id,
            decision.name,
            exc_info=True,
        )

    if log_mgr is not None:
        await log_mgr.emit(
            project_id,
            f"[{decision.name}] adopted {doc_count} existing documents from "
            f"{flat_docs} -> {target} (no regeneration cost)",
            level="info",
        )
    await _reconcile_adopted_rag_docs(project_id, decision.subdir, target, log_mgr)
    return decisions


async def _reconcile_adopted_rag_docs(
    project_id: str,
    subdir: str,
    docs_dir: Path,
    log_mgr=None,
) -> None:
    """Re-key the adopted tree's RAG documents under ``{subproject}/``.

    The flat docs were pushed under bare filenames; composite runs push under
    ``{subproject}/{filename}``. Leaving both would give retrieval two copies of
    every document -- the exact duplication that makes an assistant contradict
    itself. Delete-then-repush is used rather than a rename because the RAG API
    exposes no rename, and the hash-skip baseline is keyed by the STORED
    filename, so the prefixed push is never skipped.

    Best effort: the docs are already correct on disk, and the next successful
    run re-pushes them. A RAG failure here must not abort the generation.
    """
    filenames = sorted(
        p.name for p in docs_dir.glob("*.md") if not p.name.startswith("_")
    )
    if not filenames:
        return
    bridge = RagBridge(log_manager=log_mgr)
    removed = 0
    for filename in filenames:
        try:
            await bridge.delete_document(project_id, filename)
            removed += 1
        except Exception as exc:  # noqa: BLE001 - stale copy is reported, not fatal
            logger.warning(
                "Could not delete the flat RAG copy of %s/%s: %s",
                project_id,
                filename,
                exc,
            )
    errors = await _push_partition_docs(project_id, subdir, docs_dir, log_mgr)
    if log_mgr is not None:
        await log_mgr.emit(
            project_id,
            f"[{subdir}] RAG reconciliation for adopted docs: {removed} flat "
            f"copies removed, {len(filenames)} repushed under '{subdir}/'"
            + (f", errors: {errors}" if errors else ""),
            level="error" if errors else "info",
        )


async def _create_child_run(project_id: str, partition_name: str) -> int | None:
    try:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            # The trigger source is "composite"; the partition is its own
            # field (encoding it into triggered_by overflowed the column).
            run = await registry.create_generate_run(
                project_id, triggered_by="composite", partition_name=partition_name
            )
            await db.commit()
            return run.id
    except Exception:  # noqa: BLE001 - run records are observability, not control flow
        logger.warning(
            "Could not create child GenerateRun for %s/%s",
            project_id,
            partition_name,
            exc_info=True,
        )
        return None


async def _complete_child_run(
    child_run_id: int | None,
    *,
    status: str,
    doc_count: int = 0,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    quality_score: float | None = None,
    run_verdict: str | None = None,
) -> None:
    if child_run_id is None:
        return
    result = result or {}
    try:
        async with async_session_factory() as db:
            registry = ProjectRegistry(db)
            await registry.complete_generate_run(
                child_run_id,
                status=status,
                doc_count=doc_count,
                cost_usd=result.get("cost_usd"),
                duration_ms=result.get("duration_ms"),
                duration_api_ms=result.get("duration_api_ms"),
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                num_turns=result.get("num_turns"),
                session_id=result.get("session_id"),
                model_name=result.get("model_name"),
                write_model_name=result.get("write_model_name"),
                quality_score=quality_score,
                error_message=error,
                run_verdict=run_verdict or ("failed" if status == "failed" else None),
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Could not complete child GenerateRun %s", child_run_id, exc_info=True)


def _score_partition_docs(project_id: str, docs_dir: Path) -> float | None:
    try:
        from mcp_server.services.doc_quality_scorer import score_project

        report = score_project(project_id, docs_dir=docs_dir)
        metrics = {"doc_quality": report.to_dict()}
        (docs_dir / "run_metrics.json").write_text(
            json.dumps(metrics, indent=2, default=str), encoding="utf-8"
        )
        return report.overall
    except Exception:  # noqa: BLE001 - scoring is advisory
        logger.warning("Doc quality scoring failed for %s (%s)", project_id, docs_dir, exc_info=True)
        return None


async def _push_partition_docs(
    project_id: str,
    subdir: str,
    docs_dir: Path,
    log_mgr,
) -> list[str]:
    """Push one sub-project's public docs to RAG under `{subproject}/` names.

    Prefixing keeps the six sub-projects' identically named enterprise docs
    (00_AGENT_BRIEF.md x N) distinct in the vector store while retaining the
    project-level tenancy (`project_id` is unchanged).
    """
    docs_payload: list[dict[str, str]] = []
    for path in sorted(docs_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read %s for RAG push: %s", path, exc)
            continue
        if content.strip():
            docs_payload.append({"filename": f"{subdir}/{path.name}", "content": content})
    if not docs_payload:
        return []
    bridge = RagBridge(log_manager=log_mgr)
    try:
        result = await bridge.push_docs(project_id=project_id, docs=docs_payload)
    except Exception as exc:  # noqa: BLE001
        return [f"RAG push failed for {subdir}: {exc}"]
    return list(result.get("errors") or [])


def _write_composite_org_topology(project_id: str, fingerprint: str) -> None:
    """Materialize the deterministic org-level business topology after a run.

    The landscape endpoint also builds this on request, but writing it here
    means the artifact exists under ``output/{pid}/topology/`` immediately after
    a composite run completes (inspectable, and served without a rebuild).
    """
    try:
        from mcp_server.services.business_topology import build_composite_org_topology

        topology = build_composite_org_topology(
            project_id, project_root=config.project_root
        )
        if topology is None:
            return
        topo_dir = upload_topology_dir(project_id)
        topo_dir.mkdir(parents=True, exist_ok=True)
        (topo_dir / "business_topology.json").write_text(
            json.dumps(topology.model_dump(mode="json"), indent=2), encoding="utf-8"
        )
        (topo_dir / "validation_report.json").write_text(
            json.dumps(
                {
                    "validated": True,
                    "scan_fingerprint": fingerprint,
                    "project_id": project_id,
                    "errors": [],
                    "warnings": topology.coverage.notes,
                    "generated_by": "composite_org_merge",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 - additive artifact must not block finalize
        logger.warning(
            "Composite org topology materialization failed for %s: %s",
            project_id,
            exc,
            exc_info=True,
        )


async def _push_upload_level_artifacts(project_id: str, log_mgr) -> None:
    """Push the deterministic cross-project topology doc to RAG (upload level)."""
    topo_md = upload_topology_dir(project_id) / CROSS_PROJECT_TOPOLOGY_DOC
    if not topo_md.exists():
        return
    try:
        bridge = RagBridge(log_manager=log_mgr)
        await bridge.push_docs(
            project_id=project_id,
            docs=[{
                "filename": CROSS_PROJECT_TOPOLOGY_DOC,
                "content": topo_md.read_text(encoding="utf-8"),
            }],
        )
    except Exception as exc:  # noqa: BLE001
        await log_mgr.emit(
            project_id,
            f"Cross-project topology RAG push failed (doc is saved on disk): {exc}",
            level="error",
        )


async def sync_internal_layers_for_subprojects(
    project_id: str,
    output_root: Path,
    trees: list[tuple[str, int | None]],
    log_mgr,
) -> dict:
    """Internal-layer sync across sub-project docs trees.

    ``trees`` is ``(output subdirectory, run id)`` per sub-project. Taking the
    subdirectory rather than a run outcome lets the operator re-push path
    (``POST /mcp/push-to-rag``) reuse this exact routine; that path has no run
    outcomes, only directories on disk, and rebuilding the prefix rule there
    would put two copies of it in the codebase.

    ``RagBridge.sync_internal_layers`` deletes the project's internal set
    wholesale before re-pushing one docs tree; calling it once per partition
    would wipe each previous partition's layers. This variant deletes once and
    pushes every sub-project's internal artifacts with a ``{subproject}/``
    filename prefix, then replaces the merged entity aliases.

    Returns ``{pushed, aliases, errors}`` -- the same shape as
    ``RagBridge.sync_internal_layers`` so callers can treat the two
    interchangeably.
    """
    result: dict = {"pushed": 0, "aliases": 0, "errors": []}
    bridge = RagBridge(log_manager=log_mgr)
    try:
        await bridge.delete_internal_documents(project_id)
    except Exception as exc:  # noqa: BLE001
        message = (
            f"Internal-layer delete failed -- skipping internal sync to avoid "
            f"mixing stale and new layers: {exc}"
        )
        result["errors"].append(message)
        await log_mgr.emit(project_id, message, level="error")
        return result

    errors: list[str] = result["errors"]
    merged_aliases: list[dict] = []
    for subdir, tree_run_id in trees:
        docs_dir = output_root / subdir / "docs"
        try:
            artifacts = collect_internal_artifacts(
                docs_dir, project_id=project_id, run_id=tree_run_id
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{subdir}: collect failed ({exc})")
            continue
        for artifact in artifacts:
            metadata = dict(artifact.get("metadata") or {})
            metadata["subproject"] = subdir
            try:
                await bridge.push_markdown(
                    project_id=project_id,
                    filename=f"{subdir}/{artifact['filename']}",
                    content=artifact["content"],
                    metadata=metadata,
                )
                result["pushed"] += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{subdir}/{artifact['filename']}: {exc}")
        try:
            merged_aliases.extend(build_entity_aliases(docs_dir))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{subdir}: alias build failed ({exc})")

    if merged_aliases:
        try:
            alias_result = await bridge.post_entity_aliases(project_id, merged_aliases)
            result["aliases"] = alias_result.get("inserted", len(merged_aliases))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"entity aliases: {exc}")

    await log_mgr.emit(
        project_id,
        f"Internal RAG sync (composite): {result['pushed']} docs, "
        f"{len(merged_aliases)} aliases, {len(errors)} errors",
        level="error" if errors else "info",
    )
    return result


def _aggregate_totals(results: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for key in ("cost_usd", "duration_ms", "input_tokens", "output_tokens"):
        values = [r.get(key) for r in results if isinstance(r.get(key), (int, float))]
        totals[key] = sum(values) if values else None
    for key in ("model_name", "write_model_name"):
        names = [str(r.get(key)) for r in results if r.get(key)]
        totals[key] = names[0] if names else None
    return totals
