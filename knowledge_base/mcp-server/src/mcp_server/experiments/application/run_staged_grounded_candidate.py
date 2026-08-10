"""Nexus-owned staged Grounded execution with physically contained role packets."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from time import monotonic
from typing import Any

from mcp_server.config import config
from mcp_server.experiments.application.fact_ledger import (
    _logical_relative_path,
    canonical_unit_records,
    load_frozen_json_sources,
    validate_document_claims,
    validate_evidence_claims,
    validate_protected_document_facts,
)
from mcp_server.experiments.application.grounded_business_topology import (
    GroundedBusinessTopologyError,
    GroundedBusinessTopologyPreparation,
    prepare_grounded_business_topology,
    validate_grounded_business_topology,
)
from mcp_server.experiments.application.grounded_evidence_enrichment import (
    GroundedSupplementalEvidence,
    PROJECT_EVIDENCE_HANDOFF_NAME,
    PROJECT_EVIDENCE_INDEX_NAME,
    analysis_contract_prompt,
    evidence_relative_suffix,
    has_supplemental_project_evidence,
    investigation_contract_prompt,
    project_evidence_handoff,
    project_evidence_index,
    supplemental_project_evidence,
    validate_analysis_enrichment,
    validate_investigation_packets,
    validate_project_evidence_index,
    validate_writing_enrichment,
    writing_contract_prompt,
)
from mcp_server.experiments.application.analysis_batch_planner import (
    AnalysisBatchPlanner,
    BatchCoverageError,
    BatchPlan,
    UnitMultiplicity,
)
from mcp_server.experiments.application.grounded_pack import (
    GroundedExecutionPack,
    GroundedRole,
    batch_id_from_writer_role,
    expand_grounded_fanout,
    grounded_pack_payload,
)
from mcp_server.experiments.application.unit_tiering import (
    UnitTierDecision,
    classify_units,
    decisions_by_unit,
    production_unit_names,
    tiering_manifest,
)
from mcp_server.experiments.application.unit_evidence import (
    UnitEvidenceBundle,
    UnitEvidenceExtractor,
    safe_name as _unit_safe_name,
)
from mcp_server.experiments.application.mapping_association import (
    DocumentedMapping,
    MappingAssociationPlan,
    build_mapping_association_plan,
)
from mcp_server.experiments.application.grounded_preflight import (
    preflight_grounded_sources,
    reject_secret_like_output,
)
from mcp_server.experiments.application.grounded_runtime import (
    BOUNDED_BUDGET_MONITORING_CAPABILITY,
    GroundedRoleRuntimeContext,
    GroundedRuntimeTaskBuilder,
)
from mcp_server.experiments.application.run_legacy_replay_candidate import (
    _REPLAY_TASKS,
    _require_phase_one_skip,
)
from mcp_server.experiments.application.run_orchestrated_candidate import (
    _ORCHESTRATION_OUTPUTS,
    _read_orchestration_outputs,
    _reject_reproduced_secrets,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
    QuickCandidateResult,
)
from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    CancelRequest,
    RuntimeAvailability,
    RuntimeEventCategory,
    RuntimeTaskEvent,
    RuntimeTaskHandle,
    RuntimeTaskRequest,
    RuntimeTaskResult,
    RuntimeTaskStatus,
)
from mcp_server.experiments.domain.project_evidence import ProjectEvidenceSnapshot
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)
from mcp_server.services.log_manager import experiment_log_manager


@dataclass(frozen=True, slots=True)
class _GroundedRoleRuntimeBudget:
    max_input_tokens: int
    max_tool_calls: int
    max_billed_cost_usd: float | None = None


def _dedupe_preserve_order(names: "Iterable[str]") -> list[str]:
    """Return unique names in first-seen order.

    Canonical units can legitimately share a name (the same logging/monitoring
    route copied into several export roots). The evidence/doc/mapping side and
    the batch planner all key on unique name, so the coverage gates and tiering
    counts must use the same unique-name set -- not the physical-copy list.
    """

    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


class GroundedRuntimeRunner:
    """Execute pack roles through ``AgentRuntime``; Nexus owns all artifacts."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        task_builder: GroundedRuntimeTaskBuilder,
        output_root: Path,
        project_root: Path,
        workspace_root: Path,
        execution_pack: GroundedExecutionPack,
        runtime_workspace_root: Path = Path("/runtime/workspaces"),
        timeout_seconds: int = 3600,
        max_billed_cost_usd: float | None = None,
        transient_retry_limit: int | None = None,
    ) -> None:
        self._runtime = runtime
        self._task_builder = task_builder
        self._output_root = Path(output_root)
        self._project_root = Path(project_root)
        self._workspace_root = Path(workspace_root)
        self._pack = execution_pack
        self._runtime_workspace_root = Path(runtime_workspace_root)
        self._timeout_seconds = timeout_seconds
        self._max_billed_cost_usd = max_billed_cost_usd
        # Bounded retry budget for TRANSIENT runtime errors within a single
        # role attempt (see ``_is_transient_runtime_error``). Deterministic
        # failures are never retried here; they fall through to the pack repair
        # loop or fail loudly. ``None`` resolves to the operator config knob.
        self._transient_retry_limit = (
            transient_retry_limit
            if transient_retry_limit is not None
            else config.agentic_grounded_transient_retry_limit
        )

    async def run(
        self,
        *,
        project_id: str,
        experiment_id: str,
        source_items: tuple[tuple[str, Path], ...],
        provider_id: str,
        model_id: str,
        output_names: tuple[str, ...],
        legacy_baseline_available: bool = True,
        project_evidence_snapshot: ProjectEvidenceSnapshot | None = None,
        resume_from_experiment_id: str | None = None,
    ) -> QuickCandidateResult:
        await experiment_log_manager.emit(
            experiment_id,
            f"Grounded run starting (runtime={self._runtime.runtime_id}, "
            f"provider={provider_id}, model={model_id})",
        )
        expected_documents = tuple(
            path.removeprefix("candidate/docs/")
            for path in self._pack.owned_artifacts
            if path.startswith("candidate/docs/")
        )
        if output_names != expected_documents:
            raise QuickCandidateError("grounded output contract does not match pack ownership")
        ledger, source_preflight = await asyncio.to_thread(
            preflight_grounded_sources,
            source_items,
            max_source_items=self._pack.max_source_items,
            max_source_bytes=self._pack.max_source_bytes,
            project_id=project_id,
            approved_redaction_receipt_sha256=(
                _approved_scanner_receipt_sha256(project_evidence_snapshot)
                if project_evidence_snapshot is not None
                else None
            ),
        )
        health = await self._runtime.health()
        if health.availability != RuntimeAvailability.AVAILABLE:
            raise QuickCandidateError("grounded agent runtime is unavailable")
        capabilities = await self._runtime.capabilities()
        if (
            capabilities.runtime_id != health.runtime_id
            or capabilities.runtime_version != health.runtime_version
        ):
            raise QuickCandidateError("grounded runtime health and capabilities disagree")
        if BOUNDED_BUDGET_MONITORING_CAPABILITY not in capabilities.supported_capabilities:
            raise QuickCandidateError(
                "grounded runtime cannot enforce bounded budget monitoring"
            )
        route = next(
            (
                model
                for model in capabilities.models
                if model.provider_id == provider_id and model.model_id == model_id
            ),
            None,
        )
        if route is None or not route.available:
            raise QuickCandidateError("grounded provider/model route is unavailable")
        runtime_label = f"{health.runtime_id}/{health.runtime_version or 'unknown'}"

        # Deterministic per-unit verbatim evidence, built once from the frozen
        # scan. It sizes the analysis batch planner (bundle bytes) and is reused
        # to stage each evidence/analysis packet without re-extracting a unit.
        unit_evidence_plan = await asyncio.to_thread(
            _UnitEvidencePlan.from_frozen_sources,
            source_items,
            ledger,
        )
        # Production/test tiering drives Tier-A per-unit documentation depth and
        # the hard coverage gate. Err toward production; log the split, no
        # silent caps. ``MCP_GROUNDED_FORCE_ALL_UNITS_DEEP`` forces all-deep.
        force_all_deep = config.grounded_force_all_units_deep
        tier_decisions = classify_units(ledger, force_all_deep=force_all_deep)
        # Duplicate-named canonical copies classify identically (they share name
        # AND group), so collapse tiering to the same unique-name set the planner
        # and coverage gate use. ``production`` feeds ``assert_production_covered``
        # (a hard gate over the unique set) and the log counts below.
        production = _dedupe_preserve_order(production_unit_names(tier_decisions))
        unique_unit_names = _dedupe_preserve_order(d.unit for d in tier_decisions)

        batch_plan: BatchPlan | None = None
        has_writing_fanout = self._pack.writing_fanout_template is not None
        if self._pack.analysis_fanout_template is not None or has_writing_fanout:
            batch_plan = await asyncio.to_thread(
                self._plan_analysis_batches,
                ledger=ledger,
                unit_evidence_plan=unit_evidence_plan,
            )
            if has_writing_fanout:
                # Coverage gate: every production unit must land in a batch, so
                # every production unit gets a deep per-unit (Tier-A) document.
                try:
                    batch_plan.assert_production_covered(production)
                except BatchCoverageError as exc:
                    raise QuickCandidateError(
                        f"grounded Tier-A per-unit coverage gate failed: {exc}"
                    ) from exc
            self._pack = expand_grounded_fanout(self._pack, batch_plan.batches)
            writer_batches = sum(
                1 for role in self._pack.roles if role.unit_docs
            )
            await experiment_log_manager.emit(
                experiment_id,
                "Analysis fan-out: "
                f"{len(batch_plan.batches)} evidence batch(es) planned over "
                f"{sum(len(batch.unit_names) for batch in batch_plan.batches)} unit(s)",
            )
            # Surface the duplicate-copy dedup so the operator sees that N named
            # units are deployed as multiple physical copies (not a silent drop).
            duplicate_summary = batch_plan.duplicate_summary()
            if duplicate_summary is not None:
                await experiment_log_manager.emit(
                    experiment_id,
                    "Duplicate canonical copies: " + duplicate_summary,
                )
            if has_writing_fanout:
                await experiment_log_manager.emit(
                    experiment_id,
                    "Unit tiering: "
                    f"{len(production)} production (deep Tier-A doc), "
                    f"{len(unique_unit_names) - len(production)} test/scratch (light); "
                    f"force_all_units_deep={force_all_deep}. Per-unit doc writers: "
                    f"{writer_batches} (one per evidence batch).",
                )

        role_runtime_budgets = _allocate_role_runtime_budgets(
            self._pack,
            max_billed_cost_usd=self._max_billed_cost_usd,
        )
        docs_root = self._project_root / "output" / project_id / "docs"
        if self._pack.workflow_mode == "legacy_replay" and legacy_baseline_available:
            _require_phase_one_skip(docs_root)

        started = monotonic()
        workspace = await asyncio.to_thread(
            self._prepare_workspace,
            experiment_id=experiment_id,
            source_items=source_items,
            docs_root=docs_root,
            ledger=ledger,
            source_preflight=source_preflight,
            include_legacy_workflow_inputs=legacy_baseline_available,
            project_evidence_snapshot=project_evidence_snapshot,
            unit_evidence_plan=unit_evidence_plan,
            batch_plan=batch_plan,
            tier_decisions=tier_decisions,
        )
        # Deterministic per-role content hash. A resumed run reuses a prior
        # attempt's accepted role output ONLY when the role's content hash is
        # unchanged. Because analysis/writer roles are now generated per batch
        # (deterministic ``AnalysisBatchPlanner`` -> stable batch ids), keying
        # resume on a fixed role id/filename would silently reuse a stale batch
        # whose composition changed. The hash pins each dynamic role to the
        # sorted unit set + verbatim evidence + boundary manifest that actually
        # determine its output; static roles pin to the whole fact ledger.
        role_content_hashes = self._role_content_hashes(
            ledger=ledger,
            unit_evidence_plan=unit_evidence_plan,
            batch_plan=batch_plan,
            provider_id=provider_id,
            model_id=model_id,
        )
        accepted_role_hashes: dict[str, str] = {}

        accepted: dict[str, Path] = {}
        safety_warnings: list[dict[str, str]] = []
        handles: dict[str, RuntimeTaskHandle] = {}
        role_results: dict[str, list[RuntimeTaskResult]] = {}
        role_events: dict[str, _ToolCallTally] = {}
        role_outcomes: dict[str, str] = {}
        pending = {role.id: role for role in self._pack.roles}
        completed: set[str] = set()

        if resume_from_experiment_id is not None:
            resumed = await asyncio.to_thread(
                self._resume_accepted_roles,
                workspace=workspace,
                resume_from_experiment_id=resume_from_experiment_id,
                role_content_hashes=role_content_hashes,
            )
            for role_id, role_accepted in resumed:
                accepted.update(role_accepted)
                role_outcomes[role_id] = "resumed"
                role_results[role_id] = []
                role_events[role_id] = _ToolCallTally()
                completed.add(role_id)
                pending.pop(role_id, None)
                # Carry the reused role forward in this run's resume state so a
                # resume-of-a-resume can continue building on it.
                accepted_role_hashes[role_id] = role_content_hashes[role_id]
                self._write_resume_state(workspace, accepted_role_hashes)
                await experiment_log_manager.emit(
                    experiment_id,
                    f"Role {role_id} resumed from {resume_from_experiment_id} "
                    "(batch content unchanged, not re-run)",
                )

        while pending:
            ready = [role for role in pending.values() if set(role.depends_on).issubset(completed)]
            # Review is the final synthesis gate.  A topology role can be an
            # optional warning leaf so documentation remains useful when a
            # model/provider is unavailable, but when it does succeed the
            # reviewer must see its validated result rather than race it in
            # the same parallel batch.
            non_review_pending = [role for role in pending.values() if role.stage != "review"]
            if non_review_pending:
                ready = [role for role in ready if role.stage != "review"]
            if not ready:
                raise QuickCandidateError("grounded role graph made no progress")
            if any(role.stage == "review" for role in ready):
                safety_warnings.extend(
                    self._write_pre_review_validation(
                        workspace=workspace,
                        accepted=accepted,
                        output_names=output_names,
                        ledger=ledger,
                    )
                )
            for offset in range(0, len(ready), self._pack.max_parallelism):
                batch = ready[offset : offset + self._pack.max_parallelism]
                results = await asyncio.gather(
                    *(
                        self._run_role(
                            workspace=workspace,
                            experiment_id=experiment_id,
                            project_id=project_id,
                            role=role,
                            accepted=accepted,
                            source_items=source_items,
                            ledger=ledger,
                            provider_id=provider_id,
                            model_id=model_id,
                            runtime_budget=role_runtime_budgets[role.id],
                        )
                        for role in batch
                    ),
                    return_exceptions=True,
                )
                for scheduled_role, result in zip(batch, results, strict=True):
                    if isinstance(result, BaseException):
                        if scheduled_role.failure_policy != "warn":
                            # A required-artifact role failing zeroes the whole
                            # run, so it must fail loudly with a reason. Name the
                            # role and surface the concrete cause (built on
                            # ``_safe_role_failure_detail``) rather than letting
                            # a bare provider error propagate anonymously.
                            raise QuickCandidateError(
                                f"grounded required role {scheduled_role.id} failed "
                                f"({_safe_error_category(result)}): "
                                f"{_safe_role_failure_detail(result)}"
                            ) from result
                        role_warnings = self._write_optional_role_failure(
                            workspace=workspace,
                            project_id=project_id,
                            role=scheduled_role,
                            error=result,
                        )
                        role_results[scheduled_role.id] = []
                        role_events[scheduled_role.id] = _ToolCallTally()
                        role_outcomes[scheduled_role.id] = "warning"
                        safety_warnings.extend(role_warnings)
                        completed.add(scheduled_role.id)
                        pending.pop(scheduled_role.id)
                        continue
                    role, handle, results, events, produced, role_warnings = result
                    if handle is not None:
                        handles[role.id] = handle
                    role_results[role.id] = results
                    role_events[role.id] = events
                    accepted.update(produced)
                    role_outcomes[role.id] = (
                        "completed" if set(role.owns).issubset(produced) else "warning"
                    )
                    safety_warnings.extend(role_warnings)
                    completed.add(role.id)
                    pending.pop(role.id)
                    # Persist accepted role progress incrementally so a later
                    # resume (keyed on content hash) can continue from here
                    # rather than restart. Only roles that fully produced their
                    # owned artifacts are recorded as reusable.
                    if role.owns and set(role.owns).issubset(produced):
                        accepted_role_hashes[role.id] = role_content_hashes[role.id]
                        self._write_resume_state(workspace, accepted_role_hashes)
                await asyncio.to_thread(
                    self._refresh_deterministic_orchestration,
                    workspace,
                    ledger,
                    accepted,
                    output_names,
                )

        documents = {name: _read_text(accepted[f"candidate/docs/{name}"]) for name in output_names}
        # Tier-A per-unit deep documents are fanned out dynamically over the
        # evidence batches, so they are not in the static output contract. Collect
        # every accepted document beyond the named summary set (e.g. units/*.md)
        # so they are validated and persisted alongside the summaries.
        for artifact, path in accepted.items():
            if not artifact.startswith("candidate/docs/"):
                continue
            relative = artifact.removeprefix("candidate/docs/")
            if relative not in documents:
                documents[relative] = _read_text(path)
        intermediate_paths = tuple(
            path
            for path in self._pack.owned_artifacts
            if path.startswith("candidate/intermediates/")
        )
        intermediates = {
            path.removeprefix("candidate/intermediates/"): _read_text(accepted[path])
            for path in intermediate_paths
        }
        claim_report = validate_document_claims(documents, ledger)
        protected_report = validate_protected_document_facts(documents, ledger)
        if protected_report["status"] != "pass":
            safety_warnings.append(
                _protected_fact_warning(protected_report, category="protected_fact_mismatch")
            )
        claim_report["protected_fact_validation"] = protected_report
        (workspace / "candidate" / "orchestration").mkdir(parents=True, exist_ok=True)
        (workspace / "candidate" / "orchestration" / "deterministic_claim_report.json").write_bytes(
            canonical_json_bytes(claim_report)
        )
        # Claim mismatches are evidence-quality findings, not execution failures.
        # Keep the complete candidate bundle available for inspection while
        # surfacing every finding through the normal completed_warnings status.
        for issue in claim_report.get("issues", ()):
            if not isinstance(issue, dict):
                continue
            safety_warnings.append(
                {
                    "path": str(issue.get("document") or "candidate documentation"),
                    "category": f"deterministic_claim_{issue.get('code', 'mismatch')}",
                    "severity": "warning",
                }
            )

        orchestration = _read_orchestration_outputs(
            workspace,
            output_names,
            validate_evidence_locators=True,
        )
        topology_outputs = _read_topology_outputs(workspace)
        safety_warnings.extend(
            reject_secret_like_output(
                {
                    **{f"docs/{name}": text for name, text in documents.items()},
                    **{f"intermediates/{name}": text for name, text in intermediates.items()},
                    **{
                        f"orchestration/{name}": content.decode("utf-8", errors="ignore")
                        for name, content in orchestration.items()
                    },
                    **{f"topology/{name}": text for name, text in topology_outputs.items()},
                }
            )
        )
        _reject_reproduced_secrets(
            source_items,
            documents,
            intermediates,
            {
                **orchestration,
                **{f"topology/{name}": text.encode() for name, text in topology_outputs.items()},
            },
        )
        usage = _aggregate_runtime_usage(
            result for results in role_results.values() for result in results
        )
        tool_summary = _aggregate_runtime_tools(
            results=(result for results in role_results.values() for result in results),
            tallies=role_events.values(),
        )
        _enforce_runtime_budgets(
            self._pack,
            usage,
            tool_summary,
            max_billed_cost_usd=self._max_billed_cost_usd,
        )

        persisted = await asyncio.to_thread(
            self._persist,
            project_id=project_id,
            experiment_id=experiment_id,
            workspace=workspace,
            output_names=output_names,
            documents=documents,
            intermediates=intermediates,
            orchestration=orchestration,
            handles=handles,
            role_results=role_results,
            role_events=role_events,
            role_outcomes=role_outcomes,
            runtime_label=runtime_label,
            provider_id=provider_id,
            model_id=model_id,
            source_preflight=source_preflight,
            claim_report=claim_report,
            usage=usage,
            tool_summary=tool_summary,
            safety_warnings=tuple(safety_warnings),
            elapsed_ms=int((monotonic() - started) * 1000),
            project_evidence_snapshot=project_evidence_snapshot,
        )
        await experiment_log_manager.emit(experiment_id, "Grounded run finished successfully")
        await experiment_log_manager.emit_done(experiment_id)
        return persisted

    def _plan_analysis_batches(
        self,
        *,
        ledger: dict[str, Any],
        unit_evidence_plan: "_UnitEvidencePlan",
    ) -> BatchPlan:
        """Partition every canonical unit into evidence-sized analysis batches.

        Coverage gate: the plan must be an exact partition of the ledger's
        canonical units. A dropped or duplicated unit fails the run with a
        clear error instead of silently under-documenting the project.
        """

        _planner, plan = AnalysisBatchPlanner.from_fact_ledger(
            ledger,
            evidence_bytes=unit_evidence_plan.evidence_bytes(),
            max_batch_bytes=config.grounded_analysis_batch_bytes,
        )
        # Duplicate-named canonical copies (the same logging/monitoring route
        # deployed into several export roots) are collapsed first-wins by the
        # planner, matching the evidence/doc/mapping side which keys on unique
        # name. The coverage gate must therefore run against the SAME unique-name
        # set -- passing the raw physical list (which repeats names) would trip
        # assert_covers' "expected unit names contain duplicates" guard.
        canonical_units = _dedupe_preserve_order(
            str(item["name"])
            for item in ledger.get("canonical_units", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        try:
            plan.assert_covers(canonical_units)
        except BatchCoverageError as exc:
            raise QuickCandidateError(
                f"grounded analysis batch coverage gate failed: {exc}"
            ) from exc
        if not plan.batches:
            raise QuickCandidateError(
                "grounded analysis fan-out produced no batches for a scanned project"
            )
        return plan

    def _prepare_workspace(
        self,
        *,
        experiment_id: str,
        source_items: tuple[tuple[str, Path], ...],
        docs_root: Path,
        ledger: dict[str, Any],
        source_preflight: dict[str, Any],
        include_legacy_workflow_inputs: bool,
        project_evidence_snapshot: ProjectEvidenceSnapshot | None,
        unit_evidence_plan: "_UnitEvidencePlan | None" = None,
        batch_plan: BatchPlan | None = None,
        tier_decisions: tuple[UnitTierDecision, ...] = (),
    ) -> Path:
        if unit_evidence_plan is None:
            unit_evidence_plan = _UnitEvidencePlan.from_frozen_sources(source_items, ledger)
        tier_index = decisions_by_unit(tier_decisions)
        if (
            not experiment_id
            or "/" in experiment_id
            or "\\" in experiment_id
            or experiment_id in {".", ".."}
        ):
            raise QuickCandidateError("invalid grounded workspace identity")
        workspace = self._workspace_root / experiment_id
        workspace.mkdir(parents=True, exist_ok=False)
        (workspace / "candidate" / "docs").mkdir(parents=True)
        (workspace / "candidate" / "intermediates").mkdir(parents=True)
        (workspace / "candidate" / "orchestration").mkdir(parents=True)
        (workspace / "roles").mkdir()
        (workspace / "FACT_LEDGER.json").write_bytes(canonical_json_bytes(ledger))
        (workspace / "SOURCE_PREFLIGHT.json").write_bytes(canonical_json_bytes(source_preflight))
        (workspace / "GROUNDED_EXECUTION_PACK.json").write_bytes(
            canonical_json_bytes(grounded_pack_payload(self._pack))
        )
        if tier_decisions:
            (workspace / "candidate" / "orchestration" / UNIT_TIERING_NAME).write_bytes(
                canonical_json_bytes(
                    tiering_manifest(
                        tier_decisions,
                        force_all_deep=config.grounded_force_all_units_deep,
                    )
                )
            )

        evidence_roles = [role for role in self._pack.roles if role.stage == "evidence"]
        if source_items and not evidence_roles:
            raise QuickCandidateError("grounded pack has sources but no evidence role")
        supplemental = (
            supplemental_project_evidence(project_evidence_snapshot)
            if project_evidence_snapshot is not None
            else ()
        )
        enrichment_enabled = (
            project_evidence_snapshot is not None
            and has_supplemental_project_evidence(project_evidence_snapshot)
        )
        if enrichment_enabled:
            assert project_evidence_snapshot is not None
        if enrichment_enabled and not evidence_roles:
            raise QuickCandidateError("grounded pack has supplemental evidence but no evidence role")
        assignments: dict[str, list[tuple[str, Path]]] = {role.id: [] for role in self._pack.roles}
        for index, item in enumerate(source_items):
            assignments[evidence_roles[index % len(evidence_roles)].id].append(item)
        supplemental_assignments = {role.id: [] for role in evidence_roles}
        for index, item in enumerate(supplemental):
            supplemental_assignments[evidence_roles[index % len(evidence_roles)].id].append(item)
        source_manifest = _source_manifest(
            source_items=source_items,
            assignments=assignments,
            ledger=ledger,
        )
        (workspace / "SOURCE_MANIFEST.json").write_bytes(canonical_json_bytes(source_manifest))
        if enrichment_enabled:
            (workspace / PROJECT_EVIDENCE_HANDOFF_NAME).write_bytes(
                canonical_json_bytes(project_evidence_handoff(project_evidence_snapshot))
            )
        boundary_manifests: dict[str, dict[str, Any]] = {}
        multiplicity_index: dict[str, UnitMultiplicity] = {}
        if batch_plan is not None:
            boundary_manifests = {
                batch.batch_id: batch.boundary_manifest.to_dict()
                for batch in batch_plan.batches
            }
            # Per-unit duplicate-copy multiplicity, so the per-unit doc plan can
            # tell the writer a unit is deployed as N copies across roots (a
            # reader must not think there is only one MessageMonitoring when
            # there are several).
            multiplicity_index = batch_plan.duplicate_index()
        # Slice 5: associate prepared documented mappings/PDFs/notes to the units
        # that share concrete identifiers, so Tier-3 context reaches per-unit
        # docs; unmatched mappings still surface at the project tier below.
        mapping_plan = _build_mapping_association_plan(
            supplemental=supplemental,
            unit_evidence_plan=unit_evidence_plan,
        )
        if mapping_plan is not None:
            unit_evidence_plan.attach_mappings(
                mapping_plan, max_bytes=config.grounded_unit_mapping_bytes
            )
        project_mapping_doc = (
            mapping_plan.project_section(max_bytes=config.grounded_project_mapping_bytes)
            if mapping_plan is not None
            else None
        )
        for role in self._pack.roles:
            role_root = workspace / "roles" / role.id
            (role_root / "input").mkdir(parents=True)
            (role_root / "candidate").mkdir()
            _copy_readable_json(role_root / "input" / "FACT_LEDGER.json", workspace / "FACT_LEDGER.json")
            _copy_readable_json(
                role_root / "input" / "GROUNDED_EXECUTION_PACK.json",
                workspace / "GROUNDED_EXECUTION_PACK.json",
            )
            _copy_readable_json(
                role_root / "input" / "SOURCE_MANIFEST.json",
                workspace / "SOURCE_MANIFEST.json",
            )
            if enrichment_enabled:
                _copy_readable_json(
                    role_root / "input" / PROJECT_EVIDENCE_HANDOFF_NAME,
                    workspace / PROJECT_EVIDENCE_HANDOFF_NAME,
                )
            for index, (logical, source) in enumerate(assignments[role.id], start=1):
                relative = _safe_logical_path(logical, index)
                _copy(role_root / "input" / "sources" / relative, source)
            if role.stage == "evidence":
                unit_evidence_plan.stage(role_root, assignments[role.id])
            if role.units:
                # Expanded batch role (analysis OR Tier-A per-unit writer): stage
                # its batch's verbatim unit evidence (CODE = source of truth) plus
                # the cross-batch boundary manifest so the worker is not blind to
                # its dependencies. Writer roles carry a ``unitdoc_`` id prefix, so
                # resolve the owning batch id before looking up the manifest.
                unit_evidence_plan.stage_units(role_root, role.units)
                manifest = boundary_manifests.get(batch_id_from_writer_role(role.id))
                if manifest is not None:
                    (role_root / "input" / BOUNDARY_MANIFEST_NAME).write_text(
                        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
                        encoding="utf-8",
                    )
            if role.unit_docs:
                # Tier-A per-unit writer: the deterministic plan of which deep
                # document to write for each unit, with its production/test tier
                # and the reasons behind that tier.
                (role_root / "input" / UNIT_DOC_PLAN_NAME).write_text(
                    json.dumps(
                        _unit_doc_plan(
                            role, tier_index, mapping_plan, multiplicity_index
                        ),
                        indent=2,
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            if (
                project_mapping_doc is not None
                and role.stage == "writing"
                and not role.unit_docs
                and not role.fanout
            ):
                # Tier-B summary writers get the FULL documented-mapping set
                # (matched + project-only) so no mapping is dropped even when it
                # ties to no single unit. Per-unit writers already carry their
                # matched mappings inside each unit's evidence bundle.
                (role_root / "input" / DOCUMENTED_MAPPINGS_NAME).write_text(
                    project_mapping_doc, encoding="utf-8"
                )
            if enrichment_enabled and role.stage == "evidence":
                role_supplemental = tuple(supplemental_assignments[role.id])
                index_payload = project_evidence_index(
                    project_evidence_snapshot,
                    role_supplemental,
                )
                validate_project_evidence_index(index_payload)
                (role_root / "input" / PROJECT_EVIDENCE_INDEX_NAME).write_text(
                    json.dumps(index_payload, indent=2, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8",
                )
                for item in role_supplemental:
                    destination = role_root / "input" / item.relative_path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(item.content)
            if include_legacy_workflow_inputs:
                self._copy_workflow_inputs(role, role_root, docs_root)
            packet = {
                "schema_version": 1,
                "role_id": role.id,
                "stage": role.stage,
                "owns": list(role.owns),
                "depends_on": list(role.depends_on),
                "model_policy": role.model_policy,
                "failure_policy": role.failure_policy,
                "max_context_bytes": role.max_context_bytes,
                "input_roots": ["input"],
                "output_root": "candidate",
                "task_tool_allowed": False,
            }
            (role_root / "PACKET.json").write_bytes(canonical_json_bytes(packet))
            _assert_packet_budget(role_root, role.max_context_bytes)
            (role_root / "PACKET.json").chmod(0o444)
        return workspace

    def _copy_workflow_inputs(
        self,
        role: GroundedRole,
        role_root: Path,
        docs_root: Path,
    ) -> None:
        if self._pack.workflow_mode != "legacy_replay":
            return
        tasks = []
        if role.stage == "analysis":
            tasks = [task for task in _REPLAY_TASKS if task.phase in {"phase2", "phase2.6"}]
        elif role.stage == "writing":
            owned = {
                path.removeprefix("candidate/docs/")
                for path in role.owns
                if path.startswith("candidate/docs/")
            }
            tasks = [task for task in _REPLAY_TASKS if task.output_name in owned]
        for task in tasks:
            destination = role_root / "input" / "workflow" / task.phase / task.directory_name
            for name, source in (
                (
                    "prompt.md",
                    docs_root
                    / "_debug"
                    / "model_inputs"
                    / task.phase
                    / task.directory_name
                    / "prompt.md",
                ),
                (
                    "context_bundle.md",
                    docs_root
                    / "_debug"
                    / "model_inputs"
                    / task.phase
                    / task.directory_name
                    / "context_bundle.md",
                ),
            ):
                _copy(destination / name, source)

    def _resume_accepted_roles(
        self,
        *,
        workspace: Path,
        resume_from_experiment_id: str,
        role_content_hashes: dict[str, str],
    ) -> list[tuple[str, dict[str, Path]]]:
        """Reuse a prior attempt's accepted role outputs, keyed on content hash.

        A role is reused ONLY when three things hold:

        1. The prior run recorded it as accepted in ``RESUME_STATE.json`` with a
           content hash (a role that crashed or was rejected mid-attempt never
           reached that record, so it reruns fresh).
        2. That recorded hash equals the hash this run computes for the same
           role id. Because analysis/writer roles are generated per batch, the
           hash covers the batch's sorted unit set + verbatim evidence +
           boundary manifest (static roles cover the whole fact ledger). A
           re-batched or content-changed batch therefore has a different hash
           and is NOT reused -- it reruns.
        3. Its owned artifacts are still present and non-empty in the source
           workspace's canonical tree.

        A changed project (units added/removed/re-batched) thus never silently
        reuses stale batch output: only the batches whose content is byte-for
        -byte unchanged are reused; everything else reruns. A missing or
        unreadable resume state simply reuses nothing (full rerun), never a
        hard failure.
        """

        if (
            not resume_from_experiment_id
            or "/" in resume_from_experiment_id
            or "\\" in resume_from_experiment_id
            or resume_from_experiment_id in {".", ".."}
        ):
            raise QuickCandidateError("invalid grounded resume-from workspace identity")
        source_root = self._workspace_root / resume_from_experiment_id
        if not source_root.is_dir():
            raise QuickCandidateError(
                "grounded resume-from workspace does not exist: "
                f"{resume_from_experiment_id}"
            )
        stored_hashes = self._read_resume_state(source_root)
        resumed: list[tuple[str, dict[str, Path]]] = []
        for role in self._pack.roles:
            expected = role_content_hashes.get(role.id)
            # No recorded acceptance, or the batch composition/evidence changed:
            # rerun this role rather than reuse a stale output.
            if expected is None or stored_hashes.get(role.id) != expected:
                continue
            sources = {
                artifact: source_root.joinpath(*PurePosixPath(artifact).parts)
                for artifact in role.owns
            }
            if not role.owns or not all(
                path.is_file() and not path.is_symlink() and path.stat().st_size > 0
                for path in sources.values()
            ):
                continue
            role_accepted: dict[str, Path] = {}
            for artifact, source in sources.items():
                destination = workspace.joinpath(*PurePosixPath(artifact).parts)
                _copy(destination, source)
                destination.chmod(0o444)
                role_accepted[artifact] = destination
            resumed.append((role.id, role_accepted))
        return resumed

    # -- resume content hashing & state -----------------------------------

    def _role_content_hashes(
        self,
        *,
        ledger: dict[str, Any],
        unit_evidence_plan: "_UnitEvidencePlan",
        batch_plan: BatchPlan | None,
        provider_id: str,
        model_id: str,
    ) -> dict[str, str]:
        """Compute the deterministic content hash of every role in the pack."""

        ledger_digest = hashlib.sha256(canonical_json_bytes(ledger)).hexdigest()
        boundary_manifests: dict[str, dict[str, Any]] = {}
        if batch_plan is not None:
            boundary_manifests = {
                batch.batch_id: batch.boundary_manifest.to_dict()
                for batch in batch_plan.batches
            }
        return {
            role.id: self._role_content_hash(
                role,
                ledger_digest=ledger_digest,
                unit_evidence_plan=unit_evidence_plan,
                boundary_manifests=boundary_manifests,
                provider_id=provider_id,
                model_id=model_id,
            )
            for role in self._pack.roles
        }

    def _role_content_hash(
        self,
        role: GroundedRole,
        *,
        ledger_digest: str,
        unit_evidence_plan: "_UnitEvidencePlan",
        boundary_manifests: dict[str, dict[str, Any]],
        provider_id: str,
        model_id: str,
    ) -> str:
        """Hash the inputs that determine one role's output.

        For an expanded batch role (analysis batch or Tier-A per-unit writer)
        the determinant is batch-local -- its sorted unit set, each unit's
        verbatim evidence bundle, and its cross-batch boundary manifest -- so a
        re-batched or evidence-changed batch produces a different hash while an
        untouched batch's hash is stable even when OTHER batches changed. A
        static role (evidence/scorecard/summary/review/topology) reasons over
        the whole project, so it is pinned to the whole fact-ledger digest.
        """

        payload: dict[str, Any] = {
            "recipe_id": self._pack.recipe_id,
            "recipe_version": self._pack.recipe_version,
            "provider_id": provider_id,
            "model_id": model_id,
            "stage": role.stage,
            "owns": sorted(role.owns),
            "depends_on": sorted(role.depends_on),
            "max_context_bytes": role.max_context_bytes,
            "units": sorted(role.units),
            "unit_docs": sorted([unit, doc] for unit, doc in role.unit_docs),
        }
        if role.units:
            payload["evidence"] = {
                name: _sha256_text(bundle.text)
                for name in sorted(role.units)
                if (bundle := unit_evidence_plan.bundle_for(name)) is not None
            }
            manifest = boundary_manifests.get(batch_id_from_writer_role(role.id))
            if manifest is not None:
                payload["boundary_manifest"] = manifest
        else:
            payload["ledger_sha256"] = ledger_digest
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    def _read_resume_state(self, workspace: Path) -> dict[str, str]:
        path = workspace / RESUME_STATE_NAME
        if not path.is_file() or path.is_symlink():
            return {}
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        accepted = payload.get("accepted_role_hashes") if isinstance(payload, dict) else None
        if not isinstance(accepted, dict):
            return {}
        return {
            str(role_id): str(role_hash)
            for role_id, role_hash in accepted.items()
            if isinstance(role_id, str) and isinstance(role_hash, str)
        }

    def _write_resume_state(
        self, workspace: Path, accepted_role_hashes: dict[str, str]
    ) -> None:
        """Atomically persist which roles are accepted and their content hash."""

        payload = {
            "schema_version": 1,
            "accepted_role_hashes": dict(sorted(accepted_role_hashes.items())),
        }
        path = workspace / RESUME_STATE_NAME
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(canonical_json_bytes(payload))
        os.replace(temp, path)

    async def _run_role(
        self,
        *,
        workspace: Path,
        experiment_id: str,
        project_id: str,
        role: GroundedRole,
        accepted: dict[str, Path],
        source_items: tuple[tuple[str, Path], ...],
        ledger: dict[str, Any],
        provider_id: str,
        model_id: str,
        runtime_budget: _GroundedRoleRuntimeBudget,
    ) -> tuple[
        GroundedRole,
        RuntimeTaskHandle | None,
        list[RuntimeTaskResult],
        _ToolCallTally,
        dict[str, Path],
        tuple[dict[str, str], ...],
    ]:
        role_root = workspace / "roles" / role.id
        topology_preparation: GroundedBusinessTopologyPreparation | None = None
        if role.stage == "topology":
            try:
                topology_preparation = await asyncio.to_thread(
                    self._prepare_business_topology_role,
                    workspace=workspace,
                    role_root=role_root,
                    role=role,
                    project_id=project_id,
                    source_items=source_items,
                    accepted=accepted,
                )
            except Exception as exc:
                # Preparation never reaches the runtime, so there is no
                # attempt-loop log below to record why -- this role would
                # otherwise finish with a "warning" status, no session, and
                # no diagnosable reason anywhere in the receipt or logs.
                await experiment_log_manager.emit(
                    experiment_id,
                    f"Role {role.id} could not be prepared: {exc}",
                    level="error",
                )
                if role.failure_policy != "warn":
                    raise
                return (
                    role,
                    None,
                    [],
                    _ToolCallTally(),
                    {},
                    self._write_optional_role_failure(
                        workspace=workspace,
                        project_id=project_id,
                        role=role,
                        error=exc,
                    ),
                )
        dependency_root = role_root / "input" / "accepted"
        if role.stage != "topology":
            for dependency_id in role.depends_on:
                dependency = next(item for item in self._pack.roles if item.id == dependency_id)
                for artifact in dependency.owns:
                    source = accepted.get(artifact)
                    if source is None:
                        raise QuickCandidateError(f"grounded dependency was not accepted: {artifact}")
                    _copy(dependency_root / artifact, source)
        if role.stage == "review":
            for artifact, source in accepted.items():
                _copy(dependency_root / artifact, source)
        # Deterministic primary artifacts become visible only after Nexus creates
        # them, never while an upstream role is still unvalidated.
        if role.stage != "topology":
            orchestration_root = workspace / "candidate" / "orchestration"
            for name in (
                "execution_plan.json",
                "evidence_registry.json",
                "coverage_ledger.json",
                "deterministic_claim_report.json",
            ):
                source = orchestration_root / name
                if source.is_file():
                    _copy(role_root / "input" / "accepted" / "orchestration" / name, source)
        _assert_packet_budget(role_root, role.max_context_bytes)
        _lock_tree(role_root / "input")
        if role.stage == "topology":
            _lock_tree(role_root / "topology")
            (role_root / "ROLE_PROMPT.txt").chmod(0o444)

        runtime_directory = self._runtime_workspace_root / experiment_id / "roles" / role.id
        last_error: QuickCandidateError | None = None
        all_results: list[RuntimeTaskResult] = []
        tool_tally = _ToolCallTally()
        handle: RuntimeTaskHandle | None = None
        remaining_input_tokens = runtime_budget.max_input_tokens
        remaining_tool_calls = runtime_budget.max_tool_calls
        remaining_cost_usd = runtime_budget.max_billed_cost_usd
        for attempt in range(1, self._pack.repair_limit + 2):
            try:
                if attempt > 1:
                    _reset_role_owned_artifacts(role_root, role.owns)
                if remaining_input_tokens < 1 or remaining_tool_calls < 1:
                    raise QuickCandidateError(
                        "grounded role exhausted its reserved runtime budget"
                    )
                request = self._task_builder.build(
                    GroundedRoleRuntimeContext(
                        experiment_id=experiment_id,
                        role_id=role.id,
                        agent_id=role.agent,
                        prompt=_role_prompt(
                            role,
                            supplemental_evidence=_has_supplemental_context(role_root),
                            verbatim_unit_evidence=_has_verbatim_unit_evidence(role_root),
                            boundary_manifest=_has_boundary_manifest(role_root),
                            unit_doc_writer=_has_unit_doc_plan(role_root),
                            documented_mappings=_has_documented_mappings(role_root),
                            repair=attempt > 1,
                            previous_error=last_error,
                        ),
                        packet_root=role_root,
                        runtime_directory=runtime_directory,
                        owned_artifacts=role.owns,
                        provider_id=provider_id,
                        model_id=model_id,
                        attempt=attempt,
                        max_context_bytes=role.max_context_bytes,
                        max_input_tokens=remaining_input_tokens,
                        max_tool_calls=remaining_tool_calls,
                        timeout_seconds=self._timeout_seconds,
                        max_billed_cost_usd=remaining_cost_usd,
                    )
                )
                # Hard outer backstops: _wait() already tracks its own deadline,
                # but that only works if each inner runtime call returns in a
                # reasonable time. A single hung call (e.g. a stale connection
                # right after the runtime container restarts) can block inside
                # that await forever, past the intended deadline. These
                # wrappers guarantee the role fails within a bounded time
                # regardless of where the hang happens.
                await experiment_log_manager.emit(
                    experiment_id,
                    f"Role {role.id} starting (attempt {attempt})",
                )
                # Bounded retry on TRANSIENT runtime faults (e.g. the OpenCode
                # 5xx "runtime unavailable" class, or a runtime read timeout),
                # distinct from deterministic failures. A transient error here
                # is exactly what silently zeroed historical runs: a single
                # provider blip on a required role aborted the whole run. A
                # deterministic (non-retryable) error is re-raised immediately
                # and never retried; a validation failure surfaces as a
                # non-SUCCEEDED result and is handled by the repair loop below.
                transient_retries = 0
                while True:
                    try:
                        handle = await asyncio.wait_for(
                            self._runtime.start_task(request),
                            timeout=min(self._timeout_seconds, 300.0),
                        )
                        result, tally = await asyncio.wait_for(
                            self._wait(
                                handle,
                                request=request,
                                experiment_id=experiment_id,
                                role_id=role.id,
                            ),
                            timeout=self._timeout_seconds + 60.0,
                        )
                        break
                    except TimeoutError as exc:
                        await experiment_log_manager.emit(
                            experiment_id,
                            f"Role {role.id} made no progress before its hard timeout",
                            level="error",
                        )
                        raise QuickCandidateError(
                            "grounded role runtime made no progress before its hard timeout"
                        ) from exc
                    except Exception as exc:
                        if (
                            not _is_transient_runtime_error(exc)
                            or transient_retries >= self._transient_retry_limit
                        ):
                            raise
                        transient_retries += 1
                        delay = _transient_backoff_seconds(transient_retries)
                        await experiment_log_manager.emit(
                            experiment_id,
                            f"Role {role.id} hit a transient runtime error "
                            f"({_safe_role_failure_detail(exc)}); transient retry "
                            f"{transient_retries}/{self._transient_retry_limit} "
                            f"after {delay:.1f}s",
                            level="error",
                        )
                        await asyncio.sleep(delay)
                await experiment_log_manager.emit(
                    experiment_id,
                    f"Role {role.id} attempt {attempt} finished: {result.status.value}"
                    + (f" ({result.failure_category})" if result.failure_category else ""),
                    level="error" if result.status.value == "failed" else "info",
                )
            except Exception as exc:
                if role.failure_policy != "warn":
                    raise
                return (
                    role,
                    handle,
                    all_results,
                    tool_tally,
                    {},
                    self._write_optional_role_failure(
                        workspace=workspace,
                        project_id=project_id,
                        role=role,
                        error=exc,
                    ),
                )
            all_results.append(result)
            tool_tally.update(tally)
            remaining_input_tokens = _remaining_input_token_budget(
                remaining_input_tokens,
                result,
            )
            remaining_tool_calls = max(0, remaining_tool_calls - result.tool_calls)
            if remaining_cost_usd is not None:
                observed_cost = result.usage.normalized_cost_usd
                remaining_cost_usd = (
                    max(0.0, remaining_cost_usd - observed_cost)
                    if isinstance(observed_cost, (int, float))
                    else 0.0
                )
            if result.status != RuntimeTaskStatus.SUCCEEDED:
                error = QuickCandidateError(
                    "grounded role runtime did not succeed"
                    + (f": {result.failure_category}" if result.failure_category else "")
                )
                if result.status == RuntimeTaskStatus.CANCELLED:
                    error = QuickCandidateError("grounded role runtime was cancelled")
                last_error = error
                if attempt > self._pack.repair_limit:
                    if role.failure_policy == "warn":
                        return (
                            role,
                            handle,
                            all_results,
                            tool_tally,
                            {},
                            self._write_optional_role_failure(
                                workspace=workspace,
                                project_id=project_id,
                                role=role,
                                error=error,
                            ),
                        )
                    raise error
                continue
            try:
                produced, role_warnings = await asyncio.to_thread(
                    self._accept_role_outputs,
                    workspace,
                    role_root,
                    role,
                    source_items,
                    ledger,
                    project_id,
                    topology_preparation,
                )
                return role, handle, all_results, tool_tally, produced, role_warnings
            except QuickCandidateError as exc:
                last_error = exc
                if attempt > self._pack.repair_limit:
                    if role.failure_policy == "warn":
                        return (
                            role,
                            handle,
                            all_results,
                            tool_tally,
                            {},
                            self._write_optional_role_failure(
                                workspace=workspace,
                                project_id=project_id,
                                role=role,
                                error=exc,
                            ),
                        )
                    raise
        raise QuickCandidateError("grounded bounded role repair exhausted")

    def _prepare_business_topology_role(
        self,
        *,
        workspace: Path,
        role_root: Path,
        role: GroundedRole,
        project_id: str,
        source_items: tuple[tuple[str, Path], ...],
        accepted: dict[str, Path],
    ) -> GroundedBusinessTopologyPreparation:
        analysis_ids = {item.id for item in self._pack.roles if item.stage == "analysis"}
        if not analysis_ids or not analysis_ids.issubset(role.depends_on):
            raise GroundedBusinessTopologyError(
                "business topology role must depend on every Phase 2 analysis role"
            )
        expected_phase1 = {
            path
            for item in self._pack.roles
            if item.stage == "evidence"
            for path in item.owns
            if path.startswith("candidate/orchestration/evidence_packets/")
        }
        expected_phase2 = {
            path
            for item in self._pack.roles
            if item.stage == "analysis"
            for path in item.owns
            if path.startswith("candidate/intermediates/")
        }
        if not expected_phase1 or not expected_phase2:
            raise GroundedBusinessTopologyError(
                "business topology role requires declared Phase 1/2 analysis artifacts"
            )
        missing_phase1 = sorted(expected_phase1 - accepted.keys())
        missing_phase2 = sorted(expected_phase2 - accepted.keys())
        if missing_phase1 or missing_phase2:
            raise GroundedBusinessTopologyError(
                "business topology role started before all Phase 1/2 analyses were accepted"
            )
        phase1 = {
            path: source
            for path, source in accepted.items()
            if path.startswith("candidate/orchestration/evidence_packets/")
        }
        phase2 = {
            path: source
            for path, source in accepted.items()
            if path.startswith("candidate/intermediates/")
        }
        input_bytes = _tree_size(role_root / "input")
        remaining_bytes = role.max_context_bytes - input_bytes
        if remaining_bytes < 1:
            raise GroundedBusinessTopologyError(
                "business topology role has no remaining packet budget"
            )
        preparation = prepare_grounded_business_topology(
            project_id=project_id,
            source_items=source_items,
            accepted_phase1=phase1,
            accepted_phase2=phase2,
            work_root=workspace,
            packet_root=Path("_prepared_business_topology") / role.id,
            max_packet_bytes=remaining_bytes,
        )
        for source in sorted(
            preparation.packet_root.rglob("*"), key=lambda item: item.as_posix()
        ):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(preparation.packet_root)
            _copy(role_root / relative, source)
        return preparation

    def _write_optional_role_failure(
        self,
        *,
        workspace: Path,
        project_id: str,
        role: GroundedRole,
        error: BaseException,
    ) -> tuple[dict[str, str], ...]:
        category = _safe_error_category(error)
        detail = _safe_role_failure_detail(error)
        if role.stage == "topology":
            _write_topology_failure(
                workspace,
                project_id=project_id,
                category=category,
                detail=detail,
            )
        warning = {
            "path": role.owns[0] if role.owns else f"candidate/{role.id}",
            "category": f"optional_role_{category}",
            "severity": "warning",
        }
        if detail:
            warning["detail"] = detail
        return (warning,)

    def _accept_role_outputs(
        self,
        workspace: Path,
        role_root: Path,
        role: GroundedRole,
        source_items: tuple[tuple[str, Path], ...],
        ledger: dict[str, Any],
        project_id: str,
        topology_preparation: GroundedBusinessTopologyPreparation | None,
    ) -> tuple[dict[str, Path], tuple[dict[str, str], ...]]:
        texts: dict[str, str] = {}
        sources: dict[str, Path] = {}
        for artifact in role.owns:
            relative = PurePosixPath(artifact)
            source = role_root.joinpath(*relative.parts)
            if not source.is_file() or source.is_symlink():
                raise QuickCandidateError(f"grounded role {role.id} did not produce {artifact}")
            if source.stat().st_size <= 0:
                raise QuickCandidateError(f"grounded role {role.id} produced an empty artifact")
            if source.suffix in {".md", ".json"}:
                texts[artifact] = _read_text(source)
            if source.suffix == ".json" and role.stage != "topology":
                try:
                    json.loads(texts[artifact])
                except json.JSONDecodeError as exc:
                    raise QuickCandidateError(
                        f"grounded role {role.id} produced invalid JSON"
                    ) from exc
            sources[artifact] = source
        warnings: list[dict[str, str]] = list(reject_secret_like_output(texts))
        _reject_reproduced_secrets(
            source_items,
            {
                path.removeprefix("candidate/docs/"): text
                for path, text in texts.items()
                if path.startswith("candidate/docs/")
            },
            {
                path.removeprefix("candidate/intermediates/"): text
                for path, text in texts.items()
                if path.startswith("candidate/intermediates/")
            },
            {
                path.removeprefix("candidate/"): text.encode()
                for path, text in texts.items()
                if not path.startswith(("candidate/docs/", "candidate/intermediates/"))
            },
        )
        if role.stage == "evidence":
            evidence_payloads = {
                path: json.loads(text) for path, text in texts.items() if path.endswith(".json")
            }
            report = validate_evidence_claims(evidence_payloads, ledger)
            if report["status"] != "pass":
                warnings.append(_protected_fact_warning(report, category="protected_fact_mismatch"))
            index_path = role_root / "input" / PROJECT_EVIDENCE_INDEX_NAME
            if index_path.is_file():
                try:
                    validate_investigation_packets(
                        evidence_payloads,
                        index=json.loads(index_path.read_bytes()),
                        fact_ledger=ledger,
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    warnings.append(
                        {
                            "category": "investigation_contract_mismatch",
                            "detail": str(exc)[:4000],
                        }
                    )
        elif role.stage in {"analysis", "writing"}:
            report = validate_protected_document_facts(texts, ledger)
            if report["status"] != "pass":
                warnings.append(_protected_fact_warning(report, category="protected_fact_mismatch"))
            enrichment_active = _has_supplemental_context(role_root)
            if enrichment_active:
                supplemental_report = validate_document_claims(texts, ledger)
                if supplemental_report["status"] != "pass":
                    warnings.append(
                        _protected_fact_warning(supplemental_report, category="protected_fact_mismatch")
                    )
            try:
                if role.stage == "analysis":
                    validate_analysis_enrichment(
                        texts,
                        investigation_packets=_accepted_investigation_packets(role_root),
                    )
                else:
                    validate_writing_enrichment(
                        texts,
                        analyses=_accepted_analysis_artifacts(role_root),
                    )
            except ValueError as exc:
                warnings.append(
                    {
                        "category": f"{role.stage}_enrichment_contract_mismatch",
                        "detail": str(exc)[:4000],
                    }
                )
        elif role.stage == "topology":
            if topology_preparation is None or len(role.owns) != 1:
                raise QuickCandidateError("grounded business-topology preparation is unavailable")
            artifact = role.owns[0]
            result = validate_grounded_business_topology(
                texts[artifact],
                project_id=project_id,
                preparation=topology_preparation,
            )
            _write_topology_status(
                workspace,
                validation_payload=result.validation_payload,
                state_payload=result.state_payload,
            )
            if result.normalized_json is None:
                errors = json.dumps(
                    result.report.errors[:20],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                raise QuickCandidateError(
                    "grounded business-topology validation failed: " + errors[:8000]
                )
            source = sources[artifact]
            source.write_text(result.normalized_json, encoding="utf-8")
            texts[artifact] = result.normalized_json
        accepted: dict[str, Path] = {}
        for artifact, source in sources.items():
            destination = workspace.joinpath(*PurePosixPath(artifact).parts)
            _copy(destination, source)
            destination.chmod(0o444)
            accepted[artifact] = destination
        return accepted, tuple(warnings)

    def _write_pre_review_validation(
        self,
        *,
        workspace: Path,
        accepted: dict[str, Path],
        output_names: tuple[str, ...],
        ledger: dict[str, Any],
    ) -> tuple[dict[str, str], ...]:
        documents = {name: _read_text(accepted[f"candidate/docs/{name}"]) for name in output_names}
        protected_report = validate_protected_document_facts(documents, ledger)
        warnings: list[dict[str, str]] = []
        if protected_report["status"] != "pass":
            warnings.append(_protected_fact_warning(protected_report, category="protected_fact_mismatch"))
        advisory_report = validate_document_claims(documents, ledger)
        advisory_report["protected_fact_validation"] = protected_report
        root = workspace / "candidate" / "orchestration"
        root.mkdir(parents=True, exist_ok=True)
        (root / "deterministic_claim_report.json").write_bytes(
            canonical_json_bytes(advisory_report)
        )
        return tuple(warnings)

    async def _wait(
        self,
        handle: RuntimeTaskHandle,
        *,
        request: RuntimeTaskRequest,
        experiment_id: str | None = None,
        role_id: str | None = None,
    ) -> tuple[RuntimeTaskResult, _ToolCallTally]:
        tally = _ToolCallTally()
        observer = asyncio.create_task(
            self._observe_runtime_events(
                handle,
                request=request,
                tally=tally,
                experiment_id=experiment_id,
                role_id=role_id,
            ),
            name=f"grounded-runtime-events-{handle.session_id}",
        )
        deadline = min(
            monotonic() + self._timeout_seconds,
            monotonic()
            + max(0.0, (request.deadline_at - datetime.now(timezone.utc)).total_seconds()),
        )
        try:
            while monotonic() < deadline:
                # Cheap liveness check on every tick -- must never cost more
                # than O(1) regardless of how long the role has been running.
                # The full result() (e.g. OpenCode's whole-transcript fetch)
                # is only materialized once, below, right when this reports
                # terminal.
                status = await self._runtime.poll_status(handle)
                if _terminal(status):
                    result = await self._runtime.result(handle)
                    violation = await _finish_event_observer(observer)
                    result = _apply_result_budget_guard(request, result)
                    if violation is not None:
                        result = _budget_failure_result(result, violation)
                    return result, tally
                if observer.done():
                    violation = observer.result()
                    if violation is not None:
                        result = await self._cancel_and_reconcile(
                            handle,
                            request=request,
                            reason=f"grounded_budget_{violation}",
                        )
                        return _budget_failure_result(result, violation), tally
                await asyncio.sleep(min(0.05, max(0.0, deadline - monotonic())))

            # The task may have completed between the last poll and the deadline.
            result = await self._runtime.result(handle)
            if _terminal(result.status):
                violation = await _finish_event_observer(observer)
                result = _apply_result_budget_guard(request, result)
                if violation is not None:
                    result = _budget_failure_result(result, violation)
                return result, tally
            result = await self._cancel_and_reconcile(
                handle,
                request=request,
                reason="grounded_role_timeout",
            )
            violation = await _finish_event_observer(observer)
            if violation is not None:
                result = _budget_failure_result(result, violation)
            return result, tally
        finally:
            await _cancel_event_observer(observer)

    async def _observe_runtime_events(
        self,
        handle: RuntimeTaskHandle,
        *,
        request: RuntimeTaskRequest,
        tally: _ToolCallTally,
        experiment_id: str | None = None,
        role_id: str | None = None,
    ) -> str | None:
        tool_calls = 0
        async for event in self._runtime.observe(handle):
            tally.record(event)
            if experiment_id is not None:
                message = _format_runtime_event(role_id or "role", event)
                if message is not None:
                    await experiment_log_manager.emit(
                        experiment_id,
                        message,
                        level="error" if event.category == RuntimeEventCategory.ERROR else "info",
                    )
            if event.category == RuntimeEventCategory.TOOL:
                tool_calls += 1
                if (
                    request.budgets.max_tool_calls is not None
                    and tool_calls > request.budgets.max_tool_calls
                ):
                    return "tool_calls"
            violation = _event_usage_budget_violation(request, event)
            if violation is not None:
                return violation
        return None

    async def _cancel_and_reconcile(
        self,
        handle: RuntimeTaskHandle,
        *,
        request: RuntimeTaskRequest,
        reason: str,
    ) -> RuntimeTaskResult:
        # Reconcile once before aborting so a completion at the deadline keeps
        # its terminal state and final usage instead of being overwritten.
        result = await self._runtime.result(handle)
        if _terminal(result.status):
            return _apply_result_budget_guard(request, result)
        cancelled = await self._runtime.cancel(
            CancelRequest(
                experiment_id=handle.experiment_id,
                task_id=handle.task_id,
                session_id=handle.session_id,
                reason=reason,
            )
        )
        reconciliation_deadline = monotonic() + min(0.5, max(0.05, self._timeout_seconds))
        while monotonic() < reconciliation_deadline:
            result = await self._runtime.result(handle)
            if _terminal(result.status):
                return _apply_result_budget_guard(request, result)
            await asyncio.sleep(0.02)
        if not cancelled.accepted:
            raise QuickCandidateError("grounded runtime cancellation was rejected")
        raise QuickCandidateError(
            "grounded runtime cancellation did not produce a terminal result"
        )

    def _refresh_deterministic_orchestration(
        self,
        workspace: Path,
        ledger: dict[str, Any],
        accepted: dict[str, Path],
        output_names: tuple[str, ...],
    ) -> None:
        root = workspace / "candidate" / "orchestration"
        owners = {
            path.removeprefix("candidate/docs/"): role.id
            for role in self._pack.roles
            for path in role.owns
            if path.startswith("candidate/docs/")
        }
        units = [
            str(item["name"])
            for item in ledger.get("canonical_units", [])
            if isinstance(item, dict) and item.get("name")
        ] or [str(item) for item in ledger.get("sources", []) if isinstance(item, str)]
        (root / "execution_plan.json").write_bytes(
            canonical_json_bytes(
                {
                    "source_types": ["frozen_sources"],
                    "canonical_units": units or ["frozen-source-set"],
                    "partitions": [
                        {"role": role.id, "stage": role.stage} for role in self._pack.roles
                    ],
                    "agent_assignments": {role.id: role.agent for role in self._pack.roles},
                    "document_owners": owners,
                    "acceptance_checks": list(self._pack.validators),
                }
            )
        )
        claims: list[dict[str, Any]] = []
        enrichment_gaps: set[str] = set()
        has_enrichment_packets = False
        enrichment_packet_locators: list[str] = []
        for role in self._pack.roles:
            for artifact in role.owns:
                if (
                    role.stage != "evidence"
                    or artifact not in accepted
                    or not artifact.endswith(".json")
                ):
                    continue
                try:
                    payload = json.loads(accepted[artifact].read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                raw_claims = payload.get("claims") if isinstance(payload, dict) else None
                if isinstance(raw_claims, list):
                    packet_conflicts = (
                        payload.get("conflicts", []) if isinstance(payload, dict) else []
                    )
                    for item in raw_claims:
                        if not isinstance(item, dict):
                            continue
                        claims.append(
                            {
                                **item,
                                "_packet_locator": artifact,
                                "_packet_conflicts": packet_conflicts,
                            }
                        )
                coverage = payload.get("coverage") if isinstance(payload, dict) else None
                if isinstance(coverage, dict):
                    has_enrichment_packets = True
                    enrichment_packet_locators.append(artifact)
                    enrichment_gaps.update(
                        f"evidence:{value}"
                        for value in coverage.get("missing_evidence_ids", [])
                        if isinstance(value, str) and value
                    )
                    enrichment_gaps.update(
                        f"source:{value}"
                        for value in coverage.get("unavailable_source_ids", [])
                        if isinstance(value, str) and value
                    )
        if not claims:
            locator = (
                enrichment_packet_locators[0]
                if enrichment_packet_locators
                else "input/FACT_LEDGER.json"
                if (workspace / "FACT_LEDGER.json").is_file()
                else "input/GROUNDED_EXECUTION_PACK.json"
            )
            claims = [
                {
                    "claim_id": "deterministic-scope",
                    "statement": "The frozen source set defines the documented scope.",
                    "scope": units or ["frozen-source-set"],
                    "evidence_locators": [locator],
                    "confidence": "deterministic",
                    "contradictions": [],
                    "used_by_documents": list(output_names),
                }
            ]
        # Normalize agent claims to the strict public contract and durable
        # coordinator-relative ledger locator when necessary.
        normalized = []
        for index, claim in enumerate(claims, start=1):
            evidence_ids = [
                value for value in claim.get("evidence_ids", []) if isinstance(value, str)
            ]
            packet_locator = str(claim.get("_packet_locator") or "")
            locators = [packet_locator] if evidence_ids and packet_locator else ["FACT_LEDGER.json"]
            conflict_summaries = [
                str(value.get("summary"))
                for value in claim.get("_packet_conflicts", [])
                if isinstance(value, dict)
                and set(evidence_ids).intersection(
                    item
                    for item in value.get("evidence_ids", [])
                    if isinstance(item, str)
                )
                and value.get("summary")
            ]
            normalized.append(
                {
                    "claim_id": str(claim.get("claim_id") or f"claim-{index}"),
                    "statement": str(claim.get("statement") or "Evidence claim"),
                    "scope": (
                        [str(claim["claim_class"])]
                        if claim.get("claim_class")
                        else claim.get("scope")
                        if isinstance(claim.get("scope"), list)
                        else []
                    ),
                    "evidence_locators": locators,
                    "confidence": (
                        "supplemental"
                        if evidence_ids
                        else str(claim.get("confidence") or "deterministic")
                    ),
                    "contradictions": conflict_summaries
                    or claim.get("contradictions")
                    if isinstance(claim.get("contradictions"), list)
                    else conflict_summaries,
                    "used_by_documents": [
                        name
                        for name in claim.get("used_by_documents", output_names)
                        if name in output_names
                    ],
                }
            )
        (root / "evidence_registry.json").write_bytes(canonical_json_bytes({"claims": normalized}))
        (root / "coverage_ledger.json").write_bytes(
            canonical_json_bytes(
                {
                    "canonical_units": units or ["frozen-source-set"],
                    "concerns": sorted(
                        {role.stage for role in self._pack.roles}
                        | ({"supplemental_evidence"} if has_enrichment_packets else set())
                    ),
                    "document_coverage": {
                        name: units or ["frozen-source-set"] for name in output_names
                    },
                    "gaps": sorted(enrichment_gaps),
                }
            )
        )

    def _persist(
        self,
        *,
        project_id: str,
        experiment_id: str,
        workspace: Path,
        output_names: tuple[str, ...],
        documents: dict[str, str],
        intermediates: dict[str, str],
        orchestration: dict[str, bytes],
        handles: dict[str, RuntimeTaskHandle],
        role_results: dict[str, list[RuntimeTaskResult]],
        role_events: dict[str, _ToolCallTally],
        role_outcomes: dict[str, str],
        runtime_label: str,
        provider_id: str,
        model_id: str,
        source_preflight: dict[str, Any],
        claim_report: dict[str, Any],
        usage: dict[str, Any],
        tool_summary: dict[str, Any],
        safety_warnings: tuple[dict[str, str], ...],
        elapsed_ms: int,
        project_evidence_snapshot: ProjectEvidenceSnapshot | None,
    ) -> QuickCandidateResult:
        store = ArtifactStore(
            output_root=self._output_root,
            project_id=project_id,
            experiment_id=experiment_id,
        )
        artifacts: list[StoredArtifact] = []
        for name, text in documents.items():
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"docs/{name}",
                    relative_path=f"docs/{name}",
                    content=text.encode(),
                    media_type="text/markdown",
                    kind="document",
                )
            )
        for name, text in intermediates.items():
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"intermediates/{name}",
                    relative_path=f"intermediates/{name}",
                    content=text.encode(),
                    media_type="text/markdown",
                    kind="analysis",
                )
            )
        if (
            project_evidence_snapshot is not None
            and has_supplemental_project_evidence(project_evidence_snapshot)
        ):
            packet_root = workspace / "candidate" / "orchestration" / "evidence_packets"
            for path in sorted(packet_root.rglob("*.json"), key=lambda item: item.as_posix()):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(packet_root).as_posix()
                artifacts.append(
                    store.write_bytes(
                        branch="candidate",
                        logical_name=f"orchestration/evidence_packets/{relative}",
                        relative_path=f"orchestration/evidence_packets/{relative}",
                        content=path.read_bytes(),
                        media_type="application/json",
                        kind="supplemental-evidence-packet",
                    )
                )
        for name, kind in (
            ("business_topology.json", "business-topology"),
            ("validation_report.json", "business-topology-validation"),
            ("state.json", "business-topology-state"),
        ):
            source = workspace / "candidate" / "topology" / name
            if not source.is_file() or source.is_symlink():
                continue
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"topology/{name}",
                    relative_path=f"topology/{name}",
                    content=source.read_bytes(),
                    media_type="application/json",
                    kind=kind,
                )
            )
        for name, media_type, kind in _ORCHESTRATION_OUTPUTS:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"orchestration/{name}",
                    relative_path=f"orchestration/{name}",
                    content=orchestration[name],
                    media_type=media_type,
                    kind=kind,
                )
            )
        tiering_source = workspace / "candidate" / "orchestration" / UNIT_TIERING_NAME
        orchestration_extras = [
            (
                "fact_ledger.json",
                (workspace / "FACT_LEDGER.json").read_bytes(),
                "deterministic-fact-ledger",
            ),
            (
                "deterministic_claim_report.json",
                canonical_json_bytes(claim_report),
                "deterministic-claim-check",
            ),
        ]
        if tiering_source.is_file() and not tiering_source.is_symlink():
            orchestration_extras.append(
                (UNIT_TIERING_NAME, tiering_source.read_bytes(), "unit-tiering-manifest")
            )
        for name, content, kind in orchestration_extras:
            artifacts.append(
                store.write_bytes(
                    branch="candidate",
                    logical_name=f"orchestration/{name}",
                    relative_path=f"orchestration/{name}",
                    content=content,
                    media_type="application/json",
                    kind=kind,
                )
            )
        role_receipts = []
        for role in self._pack.roles:
            results = role_results.get(role.id, [])
            tally = role_events.get(role.id, _ToolCallTally())
            handle = handles.get(role.id)
            role_receipts.append(
                {
                    "role_id": role.id,
                    "stage": role.stage,
                    "failure_policy": role.failure_policy,
                    "status": role_outcomes.get(role.id, "warning"),
                    "session_id": handle.session_id if handle is not None else None,
                    "usage": _aggregate_runtime_usage(results),
                    "tool_summary": _aggregate_runtime_tools(results=results, tallies=(tally,)),
                    "accepted_artifacts": [
                        path
                        for path in role.owns
                        if (workspace / path).is_file()
                    ],
                }
            )
        receipt = {
            "schema_version": 2,
            "execution_mode": "grounded-nexus-staged-pack",
            "project_id": project_id,
            "experiment_id": experiment_id,
            "runtime": runtime_label,
            "provider_id": provider_id,
            "model_id": model_id,
            "publishes": False,
            "source_preflight": source_preflight,
            "execution_pack": grounded_pack_payload(self._pack),
            "role_sessions": role_receipts,
            "usage": usage,
            "tool_summary": tool_summary,
            "safety_warnings": list(safety_warnings),
            "deterministic_claim_check": claim_report,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if project_evidence_snapshot is not None:
            receipt["project_evidence_snapshot"] = {
                "fingerprint_sha256": project_evidence_snapshot.fingerprint_sha256,
                "snapshot_id": project_evidence_snapshot.id,
            }
        artifacts.append(
            store.write_bytes(
                branch="candidate",
                logical_name="receipts/grounded_execution.json",
                relative_path="receipts/grounded_execution.json",
                content=canonical_json_bytes(receipt),
                media_type="application/json",
                kind="receipt",
            )
        )
        finalized = store.finalize_branch(
            branch="candidate",
            manifest={
                "schema_version": 2,
                "experiment_id": experiment_id,
                "project_id": project_id,
                "runtime": runtime_label,
                "provider_id": provider_id,
                "model_id": model_id,
                "execution_mode": receipt["execution_mode"],
                "publishes": False,
                "role_session_count": len(handles),
                "usage": usage,
                "tool_summary": tool_summary,
                "safety_warnings": list(safety_warnings),
                "artifacts": [
                    {
                        "logical_name": item.logical_name,
                        "relative_path": item.relative_path,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "kind": item.kind,
                    }
                    for item in artifacts
                ],
            },
        )
        return QuickCandidateResult(
            artifacts=tuple(artifacts),
            manifest_sha256=finalized.manifest_sha256,
            session_id=next(iter(handles.values())).session_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            billed_cost_usd=usage["billed_cost_usd"],
            elapsed_ms=elapsed_ms,
            safety_warnings=safety_warnings,
        )


# Compatibility name retained for current callers while new orchestration is
# explicitly runtime-neutral.
GroundedOpenCodeRunner = GroundedRuntimeRunner


UNIT_EVIDENCE_DIRNAME = "unit_evidence"
UNIT_EVIDENCE_INDEX_NAME = "UNIT_EVIDENCE_INDEX.json"


@dataclass(slots=True)
class _UnitEvidencePlan:
    """Deterministic per-unit verbatim evidence, staged into evidence packets.

    Built once from the frozen scan JSON so every evidence role reasons over
    real code, tMap expressions, SQL, and context values -- not just the
    structural FACT_LEDGER counts. The former behaviour let an investigation
    role declare code/mappings "unknown" while the job that held them sat
    unread in its packet.

    Also the size source for the Phase 2 analysis batch planner: bundle byte
    sizes drive the deterministic bin-packing so analysis fans out with the
    project. Extracted bundles are cached so sizing and per-batch staging never
    re-run the extractor for the same unit.
    """

    extractor: UnitEvidenceExtractor
    units_by_relpath: dict[str, tuple[dict[str, Any], ...]]
    units_by_name: dict[str, dict[str, Any]]
    shared_contexts: tuple[dict[str, Any], ...]
    callers_by_unit: dict[str, tuple[str, ...]]
    callees_by_unit: dict[str, tuple[str, ...]]
    _bundle_cache: dict[str, UnitEvidenceBundle] = field(default_factory=dict)
    # Slice 5: prepared Tier-3 documented-mapping association, if any. When set,
    # each staged unit bundle gets an appended, byte-bounded "Documented
    # mappings" section. Verbatim code lives in ``bundle.text`` and is never
    # touched -- only the additive section is bounded by ``mapping_bytes``.
    mapping_plan: MappingAssociationPlan | None = None
    mapping_bytes: int = 0

    @classmethod
    def from_frozen_sources(
        cls,
        source_items: tuple[tuple[str, Path], ...],
        ledger: dict[str, Any],
    ) -> "_UnitEvidencePlan":
        json_sources = load_frozen_json_sources(source_items)
        units_by_relpath: dict[str, list[dict[str, Any]]] = {}
        units_by_name: dict[str, dict[str, Any]] = {}
        for relpath, unit in canonical_unit_records(json_sources):
            if isinstance(unit, dict) and str(unit.get("name") or "").strip():
                units_by_relpath.setdefault(relpath, []).append(unit)
                units_by_name.setdefault(str(unit["name"]), unit)
        shared_contexts: tuple[dict[str, Any], ...] = ()
        for relpath, payload in json_sources.items():
            if relpath.endswith("scan/contexts.json") and isinstance(payload, list):
                shared_contexts = tuple(item for item in payload if isinstance(item, dict))
                break
        callers_by_unit: dict[str, list[str]] = {}
        callees_by_unit: dict[str, list[str]] = {}
        for edge in ledger.get("call_graph", []) or []:
            if not isinstance(edge, dict):
                continue
            caller = str(edge.get("caller") or "")
            if not caller:
                continue
            callees = [str(c) for c in edge.get("callees", []) or [] if isinstance(c, str)]
            callees_by_unit.setdefault(caller, []).extend(callees)
            for callee in callees:
                callers_by_unit.setdefault(callee, []).append(caller)
        return cls(
            extractor=UnitEvidenceExtractor(max_bytes=config.grounded_unit_evidence_bytes),
            units_by_relpath={
                relpath: tuple(units) for relpath, units in units_by_relpath.items()
            },
            units_by_name=units_by_name,
            shared_contexts=shared_contexts,
            callers_by_unit={name: tuple(values) for name, values in callers_by_unit.items()},
            callees_by_unit={name: tuple(values) for name, values in callees_by_unit.items()},
        )

    def attach_mappings(self, plan: MappingAssociationPlan, *, max_bytes: int) -> None:
        """Attach a documented-mapping association plan for per-unit staging."""

        self.mapping_plan = plan
        self.mapping_bytes = max(0, max_bytes)

    def bundle_for(self, name: str) -> UnitEvidenceBundle | None:
        """Extract (and cache) one unit's verbatim evidence bundle by name."""

        if name in self._bundle_cache:
            return self._bundle_cache[name]
        unit = self.units_by_name.get(name)
        if unit is None:
            return None
        bundle = self.extractor.extract(
            unit,
            shared_contexts=self.shared_contexts,
            callers=self.callers_by_unit.get(name, ()),
            callees=self.callees_by_unit.get(name, ()),
        )
        self._bundle_cache[name] = bundle
        return bundle

    def evidence_bytes(self) -> dict[str, int]:
        """Return per-unit verbatim evidence byte sizes for batch planning."""

        return {
            name: len(bundle.text.encode("utf-8"))
            for name in self.units_by_name
            if (bundle := self.bundle_for(name)) is not None
        }

    def stage(self, role_root: Path, assigned_items: list[tuple[str, Path]]) -> None:
        """Write this role's assigned units (by source relpath) as evidence bundles."""

        bundles: list[UnitEvidenceBundle] = []
        seen_units: set[str] = set()
        for logical, _source in assigned_items:
            relpath = _logical_relative_path(logical)
            for unit in self.units_by_relpath.get(relpath, ()):
                name = str(unit.get("name") or "")
                if not name or name in seen_units:
                    continue
                seen_units.add(name)
                bundle = self.bundle_for(name)
                if bundle is not None:
                    bundles.append(bundle)
        self._write_bundles(role_root, bundles)

    def stage_units(self, role_root: Path, unit_names: tuple[str, ...]) -> None:
        """Write an analysis batch's assigned units as verbatim evidence bundles."""

        bundles: list[UnitEvidenceBundle] = []
        seen_units: set[str] = set()
        for name in unit_names:
            if not name or name in seen_units:
                continue
            seen_units.add(name)
            bundle = self.bundle_for(name)
            if bundle is not None:
                bundles.append(bundle)
        self._write_bundles(role_root, bundles)

    def _write_bundles(self, role_root: Path, bundles: list[UnitEvidenceBundle]) -> None:
        if not bundles:
            return
        evidence_dir = role_root / "input" / UNIT_EVIDENCE_DIRNAME
        evidence_dir.mkdir(parents=True, exist_ok=True)
        suffix = evidence_relative_suffix("text/markdown")
        index_units: list[dict[str, Any]] = []
        used_filenames: set[str] = set()
        for bundle in sorted(bundles, key=lambda item: item.unit_name):
            stem = _unit_safe_name(bundle.unit_name)
            filename = f"{stem}{suffix}"
            disambiguator = 1
            while filename in used_filenames:
                disambiguator += 1
                filename = f"{stem}_{disambiguator}{suffix}"
            used_filenames.add(filename)
            # Verbatim code lives in bundle.text and is never trimmed. The
            # documented-mapping (Tier-3) section, if any, is appended AFTER it
            # and byte-bounded independently -- mappings add context, never
            # displace evidence.
            text = bundle.text
            mapping_refs: list[str] = []
            if self.mapping_plan is not None:
                section = self.mapping_plan.unit_section(
                    bundle.unit_name, max_bytes=self.mapping_bytes
                )
                if section:
                    text = text.rstrip("\n") + "\n\n" + section + "\n"
                    mapping_refs = list(self.mapping_plan.unit_refs.get(bundle.unit_name, ()))
            # Bundles are line-based Markdown, so a line-based reader pages
            # through them cleanly -- no single-line JSON truncation risk.
            (evidence_dir / filename).write_text(text, encoding="utf-8")
            index_units.append(
                {
                    "unit": bundle.unit_name,
                    "relative_path": f"{UNIT_EVIDENCE_DIRNAME}/{filename}",
                    "categories": sorted(bundle.categories),
                    "component_ref_count": len(bundle.component_refs),
                    "truncated": bundle.truncated,
                    "mapping_refs": mapping_refs,
                }
            )
        index_payload = {
            "schema_version": 1,
            "description": (
                "Deterministic per-unit verbatim evidence (component inventory, all "
                "code bodies, tMap expressions, SQL, context variables) extracted from "
                "the frozen scan. This is the implemented-truth source for each unit; "
                "read the relevant bundle before making any claim about what it does."
            ),
            "authority": "input/FACT_LEDGER.json",
            "unit_count": len(index_units),
            "units": index_units,
        }
        (role_root / "input" / UNIT_EVIDENCE_INDEX_NAME).write_text(
            json.dumps(index_payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )


BOUNDARY_MANIFEST_NAME = "BOUNDARY_MANIFEST.json"
# Workspace-root sidecar recording which roles a run accepted and the content
# hash of each accepted role. A later run reads it (from the resume-from
# workspace) to decide, per role, whether reuse is safe. See
# ``GroundedRuntimeRunner._resume_accepted_roles``.
RESUME_STATE_NAME = "RESUME_STATE.json"
UNIT_DOC_PLAN_NAME = "UNIT_DOC_PLAN.json"
UNIT_TIERING_NAME = "unit_tiering.json"
DOCUMENTED_MAPPINGS_NAME = "DOCUMENTED_MAPPINGS.md"


def _build_mapping_association_plan(
    *,
    supplemental: tuple[GroundedSupplementalEvidence, ...],
    unit_evidence_plan: "_UnitEvidencePlan",
) -> MappingAssociationPlan | None:
    """Turn prepared supplemental evidence into a documented-mapping plan.

    All supplemental content is already redacted (it comes from the prepared
    supporting-evidence snapshot), so this only decodes text and associates by
    shared identifiers. Returns ``None`` when there is nothing to associate.
    """

    if not supplemental:
        return None
    mappings: list[DocumentedMapping] = []
    for item in supplemental:
        text = item.content.decode("utf-8", errors="ignore")
        if not text.strip():
            continue
        mappings.append(
            DocumentedMapping(
                evidence_id=item.evidence_id,
                logical_identity=item.logical_identity,
                claim_class=item.claim_class,
                text=text,
            )
        )
    if not mappings:
        return None
    return build_mapping_association_plan(mappings, unit_evidence_plan.units_by_name)


def _has_documented_mappings(role_root: Path) -> bool:
    return (role_root / "input" / DOCUMENTED_MAPPINGS_NAME).is_file()


def _has_verbatim_unit_evidence(role_root: Path) -> bool:
    return (role_root / "input" / UNIT_EVIDENCE_INDEX_NAME).is_file()


def _has_boundary_manifest(role_root: Path) -> bool:
    return (role_root / "input" / BOUNDARY_MANIFEST_NAME).is_file()


def _has_unit_doc_plan(role_root: Path) -> bool:
    return (role_root / "input" / UNIT_DOC_PLAN_NAME).is_file()


def _unit_doc_plan(
    role: GroundedRole,
    tier_index: Mapping[str, UnitTierDecision],
    mapping_plan: MappingAssociationPlan | None = None,
    multiplicity_index: Mapping[str, UnitMultiplicity] | None = None,
) -> dict[str, Any]:
    """Deterministic per-unit document plan for a Tier-A writer batch role.

    Lists, for every unit in the batch, the exact owned document path to write
    and the unit's production/test tier plus the reasons behind it. Production
    units get an exhaustive deep document grounded in their verbatim evidence
    bundle; classified test/scratch units get a lighter summary. When prepared
    documented mappings were associated with a unit, their evidence ids are
    listed as ``mapping_refs`` -- Tier-3 additive context, already appended to
    that unit's evidence bundle, that the writer must treat as documented intent
    (never an override of the code).

    When a unit is deployed as several identical physical copies across export
    roots (``multiplicity_index``), the plan records ``deployed_copies`` and the
    copy source paths so the writer states the unit is deployed N times rather
    than implying a single instance.
    """

    multiplicity_index = multiplicity_index or {}
    units_payload: list[dict[str, Any]] = []
    for unit, document in role.unit_docs:
        decision = tier_index.get(unit)
        tier = decision.tier if decision is not None else "production"
        reasons = list(decision.reasons) if decision is not None else [
            "no tiering decision available; defaulting to production"
        ]
        mapping_refs = (
            list(mapping_plan.unit_refs.get(unit, ())) if mapping_plan is not None else []
        )
        entry: dict[str, Any] = {
            "unit": unit,
            "document": document,
            "tier": tier,
            "reasons": reasons,
            "depth": "deep" if tier == "production" else "summary",
            "mapping_refs": mapping_refs,
        }
        multiplicity = multiplicity_index.get(unit)
        if multiplicity is not None:
            entry["deployed_copies"] = multiplicity.copy_count
            entry["copy_source_paths"] = list(multiplicity.copy_source_paths)
        units_payload.append(entry)
    return {
        "schema_version": 1,
        "description": (
            "One Markdown document per unit in this batch. Write EXACTLY the "
            "'document' path listed for each unit and nothing else. For a "
            "'production' (deep) unit, produce an exhaustive document grounded in "
            "that unit's verbatim evidence bundle under input/unit_evidence/: "
            "explain every component and its code/SQL/tMap logic, the data flow, "
            "connections, context variables, and callers/callees. Do not summarise "
            "away substance and do not impose a length ceiling -- explain "
            "everything the evidence shows. For a 'summary' (test/scratch) unit, a "
            "concise overview is sufficient. Never claim evidence is unavailable "
            "when a bundle for the unit exists. When a unit has 'mapping_refs', "
            "its evidence bundle ends with a Documented Mappings (Tier-3) section: "
            "use it as documented intent that ADDS context on top of the code and "
            "never overrides it; surface any documented-intent-vs-implemented-code "
            "mismatch. When a unit lists 'deployed_copies' > 1 it is deployed as "
            "that many identical physical copies across export roots (see "
            "'copy_source_paths'): state this explicitly so the reader knows the "
            "unit is deployed multiple times, not once."
        ),
        "batch_id": batch_id_from_writer_role(role.id),
        "unit_count": len(units_payload),
        "units": units_payload,
    }


def _read_topology_outputs(workspace: Path) -> dict[str, str]:
    root = workspace / "candidate" / "topology"
    outputs: dict[str, str] = {}
    for name in ("business_topology.json", "validation_report.json", "state.json"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            outputs[name] = _read_text(path)
    return outputs


def _write_topology_status(
    workspace: Path,
    *,
    validation_payload: dict[str, Any],
    state_payload: dict[str, Any],
) -> None:
    root = workspace / "candidate" / "topology"
    root.mkdir(parents=True, exist_ok=True)
    (root / "validation_report.json").write_bytes(canonical_json_bytes(validation_payload))
    (root / "state.json").write_bytes(canonical_json_bytes(state_payload))


def _write_topology_failure(
    workspace: Path,
    *,
    project_id: str,
    category: str,
    detail: str = "",
) -> None:
    root = workspace / "candidate" / "topology"
    root.mkdir(parents=True, exist_ok=True)
    (root / "business_topology.json").unlink(missing_ok=True)
    validation_path = root / "validation_report.json"
    state_path = root / "state.json"
    if validation_path.is_file() and state_path.is_file():
        return
    message = f"Optional grounded business topology was unavailable ({category})"
    # The bucketed category is a stable, greppable label; the concrete
    # exception text is the actual diagnosable reason (e.g. "packet exceeds
    # byte budget (X > Y)" or "missing required scanner artifacts: ...").
    # Both must be recorded -- a category alone gives an operator nothing to
    # act on when a genuine topology failure needs investigation.
    errors = [message]
    if detail and detail != category:
        errors.append(detail)
    validation = {
        "validated": False,
        "scan_fingerprint": "",
        "project_id": project_id,
        "errors": errors,
        "warnings": ["Candidate documentation is complete and unaffected"],
        "evidence_count": 0,
        "cited_evidence_count": 0,
        "artifact_count": 0,
        "cited_artifact_count": 0,
    }
    state = {
        "available": False,
        "validated": False,
        "scan_fingerprint": "",
        "errors": errors,
        "warnings": ["Candidate documentation is complete and unaffected"],
        "source": "grounded_candidate",
    }
    validation_path.write_bytes(canonical_json_bytes(validation))
    state_path.write_bytes(canonical_json_bytes(state))


def _accepted_investigation_packets(role_root: Path) -> dict[str, object]:
    root = role_root / "input" / "accepted" / "candidate" / "orchestration" / "evidence_packets"
    if not root.is_dir() or root.is_symlink():
        return {}
    packets: dict[str, object] = {}
    for path in sorted(root.rglob("*.json"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and "coverage" in payload:
            packets[path.relative_to(root).as_posix()] = payload
    return packets


def _accepted_analysis_artifacts(role_root: Path) -> dict[str, str]:
    root = role_root / "input" / "accepted" / "candidate" / "intermediates"
    if not root.is_dir() or root.is_symlink():
        return {}
    return {
        path.relative_to(root).as_posix(): _read_text(path)
        for path in sorted(root.rglob("*.md"), key=lambda item: item.as_posix())
        if path.is_file() and not path.is_symlink()
    }


def _has_supplemental_context(role_root: Path) -> bool:
    return (
        (role_root / "input" / PROJECT_EVIDENCE_HANDOFF_NAME).is_file()
        or (role_root / "input" / PROJECT_EVIDENCE_INDEX_NAME).is_file()
    )


def _approved_scanner_receipt_sha256(snapshot: ProjectEvidenceSnapshot) -> str | None:
    receipts = {
        source.redaction_receipt_sha256
        for source in snapshot.sources
        if source.source_kind == "intodoc_scan"
        and source.status.value == "readable"
        and source.redaction_status == "approved"
        and source.redaction_receipt_sha256 is not None
    }
    if not receipts:
        return None
    if len(receipts) != 1:
        raise QuickCandidateError("grounded scanner receipt identity is ambiguous")
    return next(iter(receipts))


_EVIDENCE_HIERARCHY_PROMPT = (
    "input/UNIT_EVIDENCE_INDEX.json lists per-unit VERBATIM evidence bundles under "
    "input/unit_evidence/ (component inventory, ALL code bodies, tMap expressions, "
    "SQL queries, and context variables extracted deterministically from the frozen "
    "scan). Read the relevant unit bundle before making any claim about what a unit "
    "does; never call code, mapping expressions, SQL, or context values 'unknown', "
    "'unavailable', or 'undecodable' when a bundle contains them. Evidence hierarchy: "
    "(1) CODE is the single source of truth -- ground every implemented fact in these "
    "verbatim bundles and FACT_LEDGER.json, never guess. (2) RUNTIME data would "
    "validate behaviour but is NOT AVAILABLE in this static scan -- treat execution "
    "frequency, traffic, latency, and failure rates as unknown; static evidence is "
    "not runtime proof. (3) Documented mappings and business notes, when present, ADD "
    "context on top of the code and are used only to surface documented-intent-vs-"
    "implemented-code mismatches -- they never override the code."
)


_BOUNDARY_MANIFEST_PROMPT = (
    "input/BOUNDARY_MANIFEST.json lists the units analysed by OTHER batches that "
    "this batch depends on: cross-batch caller/callee edges, connections shared "
    "with units outside this batch, and a one-line summary of each external "
    "neighbour. Those neighbours are present in the project even though their "
    "verbatim evidence is not staged here -- never claim a listed neighbour is "
    "absent. Document this batch's own units from their verbatim bundles; use the "
    "manifest only to describe dependencies that cross the batch boundary."
)


_UNIT_DOC_WRITER_PROMPT = (
    "input/UNIT_DOC_PLAN.json is your deterministic work list: it names EXACTLY "
    "which owned document to write for each unit in this batch, that unit's tier "
    "('production' -> deep, 'test' -> summary), and why. Write one document per "
    "listed unit at its exact 'document' path and nothing else. For every "
    "production unit, ground an exhaustive, code-level document in that unit's "
    "verbatim bundle under input/unit_evidence/: explain every component and its "
    "code/SQL/tMap logic, the data flow, connections, context variables, and "
    "callers/callees. Do not summarise away substance and impose no length "
    "ceiling. For a test/scratch unit, a concise summary is sufficient."
)


_DOCUMENTED_MAPPINGS_PROMPT = (
    "input/DOCUMENTED_MAPPINGS.md holds the project's prepared, redacted "
    "supporting material (documented field mappings, specs, notes). It is "
    "DOCUMENTED INTENT (Tier-3): additive project-wide context that never "
    "overrides the scanner facts or accepted analyses. Use it for narrative and "
    "to flag documented-intent-vs-implemented-code discrepancies under a "
    "Supplemental Evidence or Evidence Discrepancies section; never restate it "
    "as an implemented fact."
)


def _role_prompt(
    role: GroundedRole,
    *,
    supplemental_evidence: bool = False,
    verbatim_unit_evidence: bool = False,
    boundary_manifest: bool = False,
    unit_doc_writer: bool = False,
    documented_mappings: bool = False,
    repair: bool = False,
    previous_error: QuickCandidateError | None = None,
) -> str:
    if role.stage == "topology":
        prompt = (
            "Read ROLE_PROMPT.txt completely, then read topology/TOPOLOGY_TASK.md "
            "and only the contained topology/ and input/ evidence. You are an "
            "isolated Grounded business-topology role. Treat exact scanner facts "
            "and evidence identifiers as authoritative, and write only "
            "candidate/topology/business_topology.json. Task/subagent dispatch is "
            "forbidden. Never invent business ownership, intent, application "
            "boundaries, runtime behavior, or credential values."
        )
    else:
        prompt = (
            "Read PACKET.json, input/FACT_LEDGER.json, and "
        "input/SOURCE_MANIFEST.json completely. You are an isolated Grounded role. "
        "Read only files beneath input/ and use read/glob/grep within that contained "
        "packet. FACT_LEDGER.json and SOURCE_MANIFEST.json are deterministic and "
        "authoritative for project identity, source presence, unit kinds, counts, "
        "inventories, and call edges. Assigned files beneath input/sources are only "
        "this role's partition; absence from that partition never means absence from "
        "the project. Never recalculate or override a protected fact. "
        "Task/subagent dispatch is forbidden. Write exactly these owned paths "
        "relative to this role workspace and nothing else:\n"
        + "\n".join(f"- {path}" for path in role.owns)
        + "\nUnknown facts must remain unknown. Static evidence is not runtime proof. "
        "Never write credential values. Finish only after every owned file exists."
        )
    if verbatim_unit_evidence and role.stage in {"evidence", "analysis", "writing"}:
        prompt += "\n" + _EVIDENCE_HIERARCHY_PROMPT
    if boundary_manifest and role.stage in {"analysis", "writing"}:
        prompt += "\n" + _BOUNDARY_MANIFEST_PROMPT
    if unit_doc_writer and role.stage == "writing":
        prompt += "\n" + _UNIT_DOC_WRITER_PROMPT
    if documented_mappings and role.stage == "writing":
        prompt += "\n" + _DOCUMENTED_MAPPINGS_PROMPT
    if role.stage == "review":
        prompt += (
            "\nThe review report must contain these exact Markdown sections: "
            "Factual review; Coverage review; Cross-document consistency; "
            "Secret and evidence-path review; Resolved findings; Final verdict."
            " Read all accepted evidence, analyses, documents, and "
            "input/accepted/orchestration/deterministic_claim_report.json before "
            "issuing the verdict."
        )
    if supplemental_evidence:
        if role.stage == "evidence":
            prompt += "\n" + investigation_contract_prompt()
        elif role.stage == "analysis":
            prompt += "\n" + analysis_contract_prompt()
        elif role.stage == "writing":
            prompt += "\n" + writing_contract_prompt()
        elif role.stage == "review":
            prompt += (
                "\nVerify that supplemental claims remain additive, every evidence ID "
                "exists in accepted investigation/analysis artifacts, conflicts are visible, "
                "and no supplemental source rewrites FACT_LEDGER implemented truth."
            )
    if repair:
        prompt += (
            "\nThis is the one bounded repair attempt. Re-read all current owned "
            "outputs, replace unsafe/invalid/incomplete content, and satisfy the "
            "same exact ownership contract. Nexus rejected the previous attempt "
            f"with this category: {_safe_error_category(previous_error)}. "
            f"Protected-fact feedback: {_safe_protected_feedback(previous_error)}."
        )
        if role.stage == "topology":
            prompt += (
                " Business-topology validation feedback: "
                f"{_safe_topology_feedback(previous_error)}."
            )
        return prompt
    if role.stage == "review":
        prompt += (
            " Review every accepted document and, when present, the accepted "
            "validated business-topology artifact. Confirm its evidence and "
            "unknowns are consistent with the scanner-led analysis; do not "
            "invent, rewrite, or promote facts."
        )
    return prompt


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_transient_runtime_error(error: BaseException) -> bool:
    """Whether a runtime error is a TRANSIENT fault worth a bounded retry.

    Transient = the provider/runtime was momentarily unreachable or slow (e.g.
    the OpenCode 5xx "runtime unavailable" class, or a runtime read timeout).
    Runtime adapters mark these with ``retryable = True`` on the raised error
    (see ``OpenCodeUnavailable`` / ``OpenCodeTimeout``); anything without that
    marker -- authentication rejection, malformed response, an ambiguous
    submission that may already have reached the server, a deterministic
    validation error -- is treated as non-retryable and never retried here.
    """

    return getattr(error, "retryable", False) is True


def _transient_backoff_seconds(attempt: int) -> float:
    """Bounded exponential backoff (capped) between transient runtime retries."""

    return min(5.0, 0.5 * (2 ** max(0, attempt - 1)))


def _safe_error_category(error: BaseException | None) -> str:
    if error is None:
        return "unknown_validation_failure"
    text = str(error).casefold()
    if "business-topology validation failed" in text:
        return "business_topology_validation_failure"
    if isinstance(error, GroundedBusinessTopologyError):
        return "business_topology_packet_failure"
    if "protected-fact validation failed" in text:
        return "protected_fact_mismatch"
    if "enrichment" in text:
        return "supplemental_evidence_contract_failure"
    if "secret" in text:
        return "secret_safety_failure"
    if "invalid json" in text:
        return "invalid_json"
    if "empty" in text:
        return "empty_artifact"
    if "did not produce" in text:
        return "missing_artifact"
    return "artifact_validation_failure"


def _safe_role_failure_detail(error: BaseException | None) -> str:
    """Return the concrete exception text behind an optional-role failure.

    ``_safe_error_category`` collapses every cause into one of a handful of
    stable bucket names for grouping/telemetry; that bucket alone is not
    diagnosable (e.g. every ``GroundedBusinessTopologyError`` -- missing scan
    artifact, exhausted packet budget, byte-budget overflow -- previously
    reported identically as ``business_topology_packet_failure`` with no
    further information). This returns the real, bounded message so a
    genuine failure can always be investigated from the written receipt.
    """

    if error is None:
        return ""
    text = str(error).strip()
    return text[:4000]


def _safe_protected_feedback(error: QuickCandidateError | None) -> str:
    if error is None:
        return "none"
    text = str(error)
    marker = "grounded protected-fact validation failed: "
    if not text.startswith(marker):
        return "none"
    return text.removeprefix(marker)[:8000]


def _safe_topology_feedback(error: BaseException | None) -> str:
    if error is None:
        return "none"
    text = str(error)
    marker = "grounded business-topology validation failed: "
    if not text.startswith(marker):
        return "none"
    return text.removeprefix(marker)[:8000]


def _protected_fact_warning(report: dict[str, Any], *, category: str) -> dict[str, str]:
    """Format a protected-fact mismatch as an advisory warning, not a failure.

    The scanner that produces the protected fact ledger is itself imperfect
    (known gaps: unresolved joblets/routelets, partial tXMLMap parsing, TDM
    maps). Treating its output as an infallible publish-blocking oracle means
    a scanner gap can kill an otherwise-good run. This surfaces the same
    mismatch as a warning instead, so it is visible for review without
    stopping the pipeline.
    """

    safe_issues = [
        {
            key: item.get(key)
            for key in (
                "code",
                "artifact",
                "document",
                "path",
                "line",
                "subject",
                "claimed",
                "expected",
            )
            if item.get(key) is not None
        }
        for item in report.get("issues", [])[:50]
        if isinstance(item, dict)
    ]
    return {
        "category": category,
        "detail": json.dumps(safe_issues, ensure_ascii=False, separators=(",", ":"))[:4000],
    }


def _source_manifest(
    *,
    source_items: tuple[tuple[str, Path], ...],
    assignments: dict[str, list[tuple[str, Path]]],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    owners = {logical: role_id for role_id, items in assignments.items() for logical, _ in items}
    sources = []
    for logical, source in source_items:
        relative = _safe_logical_path(logical, len(sources) + 1).as_posix()
        sources.append(
            {
                "logical_identity": logical,
                "relative_path": relative,
                "size_bytes": source.stat().st_size,
                "present_in_frozen_snapshot": True,
                "assigned_evidence_role": owners.get(logical),
            }
        )
    return {
        "schema_version": 1,
        "complete_frozen_source_inventory": True,
        "instructions": {
            "presence_authority": (
                "Only this manifest and FACT_LEDGER.json determine source presence."
            ),
            "partition_boundary": (
                "A source assigned to another evidence role is present, not missing."
            ),
        },
        "project": ledger.get("project"),
        "canonical_units": [
            {
                key: unit.get(key)
                for key in (
                    "name",
                    "kind",
                    "group",
                    "project_source_path",
                    "source_present",
                    "source_presence_evidence",
                    "source",
                )
            }
            for unit in ledger.get("canonical_units", [])
            if isinstance(unit, dict)
        ],
        "source_count": len(sources),
        "sources": sources,
    }


def _safe_logical_path(logical: str, index: int) -> Path:
    # Staged sources are read by the OpenCode built-in ``read`` tool, which
    # refuses a ``.bin`` file as binary and returns no content. Frozen source
    # bytes are decodable text/JSON, so an unnamed fallback must still carry a
    # text suffix (matching run_quick_candidate's ``.json`` fallback) rather
    # than ``.bin`` -- otherwise a source with an unsafe logical name is
    # silently unreadable by every role.
    value = logical.split(":", 1)[-1].replace("\\", "/").strip()
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return Path(f"source-{index}.txt")
    return Path(*path.parts)


def _copy(destination: Path, source: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise QuickCandidateError("grounded packet input is not a regular file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _copy_readable_json(destination: Path, source: Path) -> None:
    """Copy a JSON evidence file reformatted onto multiple lines.

    The canonical form written at the workspace level (``canonical_json_bytes``)
    is compact -- one line, no whitespace -- because it must hash stably.
    Role-local copies exist only for a role to read, never for hashing, and a
    single-line file defeats line-based file-reading tools: verified in
    production, a role's own ``read`` tool truncated FACT_LEDGER.json and
    SOURCE_MANIFEST.json at 2000 characters because the entire file was one
    line, silently hiding everything past that point. Re-indenting preserves
    the exact same JSON content while letting a line-based reader page
    through all of it.
    """
    if not source.is_file() or source.is_symlink():
        raise QuickCandidateError("grounded packet input is not a regular file")
    payload = json.loads(source.read_bytes())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def _lock_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    for path in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        path.chmod(0o555)
    root.chmod(0o555)


def _reset_role_owned_artifacts(role_root: Path, owned_artifacts: tuple[str, ...]) -> None:
    """Clear a role's own owned-artifact files before a repair attempt re-writes them.

    A repair attempt re-prompts the same role against the same on-disk
    ``role_root``. The OpenAI Agents write tool creates owned artifacts
    exclusively -- write-once, see ``grounded.py::_invoke_bound_tool`` -- so
    without this, a repair's write collides with the file the rejected prior
    attempt already wrote, raising ``PermissionError`` instead of letting the
    role actually repair itself. Only ever removes the exact declared
    ``owned_artifacts`` paths, which the recipe loader guarantees are always
    under ``candidate/`` -- this can never reach ``role_root/input/accepted``.
    """
    root = role_root.resolve(strict=True)
    for artifact in owned_artifacts:
        path = root.joinpath(*PurePosixPath(artifact).parts)
        try:
            path.resolve(strict=False).relative_to(root)
        except ValueError:
            continue
        if path.is_file() or path.is_symlink():
            path.unlink()


def _assert_packet_budget(role_root: Path, max_bytes: int) -> None:
    total = _tree_size(role_root / "input") + _tree_size(role_root / "topology")
    prompt = role_root / "ROLE_PROMPT.txt"
    if prompt.is_file() and not prompt.is_symlink():
        total += prompt.stat().st_size
    if total > max_bytes:
        raise QuickCandidateError("grounded physical role packet budget exceeded")


def _tree_size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise QuickCandidateError("grounded artifact is unreadable") from exc
    if not text.strip():
        raise QuickCandidateError("grounded artifact is empty")
    return text


def _format_runtime_event(role_id: str, event: RuntimeTaskEvent) -> str | None:
    """Render one runtime event as a human-readable live-log line.

    ``event.payload`` is already redacted by ``RuntimeTaskEvent.__post_init__``,
    so nothing here needs its own secret filtering. Different runtimes shape
    their payload differently -- the OpenAI Agents adapter emits flat keys
    (``phase``, ``tool_name``, ``returned_bytes``); OpenCode's normalizer
    passes through its raw envelope largely as-is, nesting tool detail under a
    ``part`` object (``part.id``, ``part.returnedBytes``, ...) instead. Check
    both shapes rather than assuming one, and always fall back to *something*
    readable instead of silently dropping the line -- a formatter that only
    recognizes one runtime's vocabulary looks identical to "nothing is
    happening" for every other runtime, which is exactly the bug this
    replaces.
    """

    payload = event.payload
    if event.category == RuntimeEventCategory.TOOL:
        part = payload.get("part") if isinstance(payload.get("part"), Mapping) else {}
        phase = payload.get("phase")
        tool_name = (
            payload.get("tool_name")
            or part.get("tool")
            or part.get("name")
            or part.get("type")
        )
        returned_bytes = payload.get("returned_bytes") or part.get("returnedBytes")
        if phase == "called" or (tool_name and returned_bytes is None):
            return f"[{role_id}] tool call: {tool_name or 'unknown'}"
        if phase == "output" or returned_bytes is not None:
            return f"[{role_id}] tool result: {returned_bytes if returned_bytes is not None else '?'} bytes"
        return f"[{role_id}] tool event: {dict(payload)}"
    if event.category == RuntimeEventCategory.USAGE:
        parts = [
            f"{key}={value}"
            for key, value in payload.items()
            if key
            in {
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "billed_cost_usd",
                "tool_calls",
                "tool_bytes",
            }
            and value is not None
        ]
        return f"[{role_id}] usage: {', '.join(parts)}" if parts else f"[{role_id}] usage: {dict(payload)}"
    if event.category == RuntimeEventCategory.LIFECYCLE:
        phase = payload.get("phase")
        if phase:
            return f"[{role_id}] {phase}"
        status = payload.get("status") or payload.get("state")
        return f"[{role_id}] {status}" if status else None
    if event.category == RuntimeEventCategory.MESSAGE:
        text = payload.get("text") or payload.get("summary")
        return f"[{role_id}] {text}" if text else f"[{role_id}] message: {dict(payload)}"
    if event.category == RuntimeEventCategory.WARNING:
        return f"[{role_id}] warning: {dict(payload)}"
    if event.category == RuntimeEventCategory.ERROR:
        category = payload.get("failure_category") or payload.get("error_type")
        return f"[{role_id}] error: {category or dict(payload)}"
    return None


def _terminal(status: RuntimeTaskStatus) -> bool:
    return status in {
        RuntimeTaskStatus.SUCCEEDED,
        RuntimeTaskStatus.FAILED,
        RuntimeTaskStatus.CANCELLED,
    }


async def _finish_event_observer(observer: asyncio.Task[str | None]) -> str | None:
    if observer.done():
        return observer.result()
    try:
        return await asyncio.wait_for(asyncio.shield(observer), timeout=0.05)
    except TimeoutError:
        await _cancel_event_observer(observer)
        return None


async def _cancel_event_observer(observer: asyncio.Task[str | None]) -> None:
    if observer.done():
        with suppress(asyncio.CancelledError):
            observer.result()
        return
    observer.cancel()
    with suppress(asyncio.CancelledError):
        await observer


def _event_usage_budget_violation(
    request: RuntimeTaskRequest,
    event: RuntimeTaskEvent,
) -> str | None:
    if event.category != RuntimeEventCategory.USAGE:
        return None
    checks = (
        ("input_tokens", request.budgets.max_input_tokens),
        ("output_tokens", request.budgets.max_output_tokens),
    )
    for name, maximum in checks:
        value = event.payload.get(name)
        if isinstance(value, int) and maximum is not None and value > maximum:
            return name
    return None


def _allocate_role_runtime_budgets(
    pack: GroundedExecutionPack,
    *,
    max_billed_cost_usd: float | None = None,
) -> dict[str, _GroundedRoleRuntimeBudget]:
    """Reserve disjoint runtime ceilings whose aggregate cannot exceed the pack."""

    role_count = len(pack.roles)
    if pack.max_input_tokens < role_count or pack.max_tool_calls < role_count:
        raise QuickCandidateError(
            "grounded pack runtime budget cannot reserve every declared role"
        )
    input_base, input_remainder = divmod(pack.max_input_tokens, role_count)
    tool_base, tool_remainder = divmod(pack.max_tool_calls, role_count)
    return {
        role.id: _GroundedRoleRuntimeBudget(
            max_input_tokens=input_base + (1 if index < input_remainder else 0),
            max_tool_calls=tool_base + (1 if index < tool_remainder else 0),
            max_billed_cost_usd=(
                max_billed_cost_usd / role_count
                if max_billed_cost_usd is not None
                else None
            ),
        )
        for index, role in enumerate(pack.roles)
    }


def _remaining_input_token_budget(
    reserved: int,
    result: RuntimeTaskResult,
) -> int:
    used = result.usage.input_tokens
    # Missing cumulative usage cannot safely be reclaimed for a repair attempt.
    return max(0, reserved - used) if isinstance(used, int) else 0


def _apply_result_budget_guard(
    request: RuntimeTaskRequest,
    result: RuntimeTaskResult,
) -> RuntimeTaskResult:
    checks = (
        (result.usage.input_tokens, request.budgets.max_input_tokens, "input_tokens"),
        (result.usage.output_tokens, request.budgets.max_output_tokens, "output_tokens"),
        (result.tool_calls, request.budgets.max_tool_calls, "tool_calls"),
        (result.tool_bytes, request.budgets.max_tool_bytes, "tool_bytes"),
        (result.timings.elapsed_ms, request.budgets.max_elapsed_ms, "elapsed_ms"),
    )
    for actual, maximum, name in checks:
        if actual is not None and maximum is not None and actual > maximum:
            return _budget_failure_result(result, name)
    return result


def _budget_failure_result(
    result: RuntimeTaskResult,
    budget: str,
) -> RuntimeTaskResult:
    return replace(
        result,
        status=RuntimeTaskStatus.FAILED,
        failure_category="budget_exceeded",
        failure_detail={"budget": budget},
    )


def _aggregate_runtime_usage(results: Any) -> dict[str, Any]:
    """Aggregate normalized runtime telemetry without retaining provider envelopes."""

    collected = tuple(results)

    def total(field: str) -> int | None:
        values = [getattr(result.usage, field) for result in collected]
        known = [value for value in values if isinstance(value, int)]
        return sum(known) if known else None

    costs = [
        result.usage.billed_cost_usd
        if result.usage.billed_cost_usd is not None
        else result.usage.normalized_cost_usd
        for result in collected
    ]
    known_costs = [value for value in costs if isinstance(value, (int, float))]
    return {
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "cache_read_tokens": total("cache_read_tokens"),
        "billed_cost_usd": str(sum(known_costs)) if known_costs else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


@dataclass(slots=True)
class _ToolCallTally:
    """Running per-tool-name call counts, built incrementally as events stream by.

    A role's runtime events are recorded here one at a time and discarded --
    never accumulated into a list. Retaining every event for a role's whole
    lifetime (now potentially tens of thousands of tool calls, with per-role
    budgets no longer capped) just to count them once at the end was the
    actual driver of unbounded memory growth: nothing downstream ever reads
    more than the by-tool-name counts this tally produces directly.
    """

    by_tool: dict[str, int] = field(default_factory=dict)
    observed: int = 0

    def record(self, event: RuntimeTaskEvent) -> None:
        if event.category != RuntimeEventCategory.TOOL:
            return
        name = _runtime_tool_name(event)
        if name is None:
            return
        self.by_tool[name] = self.by_tool.get(name, 0) + 1
        self.observed += 1

    def update(self, other: "_ToolCallTally") -> None:
        for name, count in other.by_tool.items():
            self.by_tool[name] = self.by_tool.get(name, 0) + count
        self.observed += other.observed


def _aggregate_runtime_tools(
    *,
    results: Any,
    tallies: Any,
) -> dict[str, Any]:
    """Preserve total tool usage even when a runtime omits per-tool telemetry."""

    collected_results = tuple(results)
    by_tool: dict[str, int] = {}
    observed = 0
    for tally in tallies:
        for name, count in tally.by_tool.items():
            by_tool[name] = by_tool.get(name, 0) + count
        observed += tally.observed
    total = sum(result.tool_calls for result in collected_results)
    if total > observed:
        by_tool["runtime_reported_unattributed"] = (
            by_tool.get("runtime_reported_unattributed", 0) + total - observed
        )
    return {"total_calls": total, "by_tool": dict(sorted(by_tool.items()))}


def _runtime_tool_name(event: RuntimeTaskEvent) -> str | None:
    payload = event.payload
    for key in ("tool_name", "toolName", "tool"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            for key in ("tool", "tool_name", "name"):
                value = part.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _enforce_runtime_budgets(
    pack: GroundedExecutionPack,
    usage: dict[str, Any],
    tool_summary: dict[str, Any],
    *,
    max_billed_cost_usd: float | None = None,
) -> None:
    value = usage.get("input_tokens")
    if isinstance(value, int) and value > pack.max_input_tokens:
        raise QuickCandidateError("grounded input-token budget exceeded")
    calls = tool_summary.get("total_calls")
    if isinstance(calls, int) and calls > pack.max_tool_calls:
        raise QuickCandidateError("grounded tool-call budget exceeded")
    raw_cost = usage.get("billed_cost_usd")
    if max_billed_cost_usd is not None:
        if raw_cost is None:
            raise QuickCandidateError("grounded hard cost ceiling has no trusted estimate")
        if float(raw_cost) > max_billed_cost_usd:
            raise QuickCandidateError("grounded hard cost ceiling exceeded")
