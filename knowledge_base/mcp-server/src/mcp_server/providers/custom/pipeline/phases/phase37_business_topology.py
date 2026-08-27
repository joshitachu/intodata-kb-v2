"""Additive, non-blocking business topology synthesis after documentation.

Two stages, in order of preference:

**Stage A + B (primary)** -- the model extracts the architecture from this run's
own finished Phase 3 documents in business language (:mod:`flow_extraction`),
and Python assembles the published overlay from it (:mod:`assembly`). This is a
generalisation of the pipeline that produced the hand-curated werkorder
topology.

**Single-shot synthesis (fallback)** -- the previous contract, where the model
emits the published JSON itself. Retained because a pack may override its
template and because a partition whose documents came out thin can still have a
scanner-groundable overlay.

**Deterministic overlay (floor)** -- evidence-only, no LLM. Published when both
model lanes fail, so `available` can still be true with real cited edges.

Verified motivation: on the last werkorder run all 8 partitions published the
deterministic floor (0 business objects, 0 delivery paths), and the one partition
whose model output was rich -- 14 systems / 8 streams / 8 delivery paths / 5
business objects -- was rejected for three bookkeeping defects and replaced with
a 4-system / 3-stream skeleton. Whatever validation rejects, this phase now
prunes per claim rather than discarding wholesale.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.services.business_topology import BusinessTopology
    from mcp_server.services.business_topology.evidence import (
        BusinessTopologyEvidenceBundle,
    )
    from mcp_server.services.business_topology.validation import (
        BusinessTopologyValidationReport,
    )


class Phase37BusinessTopology(Phase):
    """Synthesize a separate L1/L2 overlay without changing generated docs."""

    def __init__(self, helpers: "HarnessHelpersImpl") -> None:
        self._helpers = helpers

    @property
    def name(self) -> str:
        return "phase37_business_topology"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return False

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        from mcp_server.config import config
        from mcp_server.services.business_topology import (
            DOC_EVIDENCE_FILENAMES,
            build_business_topology_evidence,
            build_deterministic_topology,
            build_topology_context_files,
            register_doc_evidence,
            validate_business_topology,
        )

        output_dir = ctx.staging_dir / "_topology"
        output_dir.mkdir(parents=True, exist_ok=True)
        validation_path = output_dir / "validation_report.json"
        state_path = output_dir / "state.json"
        topology_path = output_dir / "business_topology.json"
        scan_output_dir = config.intodoc_output_dir / ctx.project_id
        bundle = None

        try:
            bundle = build_business_topology_evidence(
                project_id=ctx.project_id,
                scan_output_dir=scan_output_dir,
                staging_dir=ctx.staging_dir,
                # Composite sub-project runs scope the artifact/lineage ledger to
                # this partition's units; the starter pack's cross-project index
                # (staged by the harness) carries the connective edges instead.
                unit_allowlist=(
                    state.facts_manifest.unit_names() if ctx.partition is not None else None
                ),
            )
            # The generated documents become a typed evidence tier so a flow
            # taken from `03_DATA_FLOWS_AND_LINEAGE.md` can cite a resolvable id.
            # The scanner-only bundle is kept for the deterministic overlay's own
            # validation: its claims must not become citable via this tier.
            doc_bundle, doc_records = register_doc_evidence(bundle, ctx.staging_dir)
            if not doc_records:
                await log_cb(
                    ctx.project_id,
                    "[custom] Business topology: no generated documents found to extract "
                    "flows from; falling back to single-shot synthesis",
                    "warning",
                )

            # PUSH the high-signal evidence directly into the model context
            # instead of relying on budget-limited agentic tool pulls (the
            # verified ETTM_part1 starvation, where the model reported "without
            # the ability to read evidence files (tool budget exhausted)").
            # Tools are disabled for this phase in _tool_policy, so these files
            # reach the model via provider.complete(context_files=...).
            extraction_context = build_topology_context_files(
                doc_bundle,
                ctx.staging_dir,
                byte_cap=config.topology_context_evidence_bytes,
                include_generated_docs=bool(doc_records),
                doc_filenames=DOC_EVIDENCE_FILENAMES,
            )
            synthesis_context = build_topology_context_files(
                bundle,
                ctx.staging_dir,
                byte_cap=config.topology_context_evidence_bytes,
            )

            # Built once, used two ways: (a) the floor the model lanes must beat
            # -- a schema-valid but EMPTY topology must not be published when
            # scanner edges ground real streams; (b) the published fallback.
            deterministic_candidate = build_deterministic_topology(
                bundle, project_id=ctx.project_id
            )
            deterministic_report = validate_business_topology(
                deterministic_candidate, bundle=bundle, project_id=ctx.project_id
            )
            deterministic_streams = (
                len(deterministic_candidate.data_streams)
                if deterministic_report.validated
                else 0
            )

            lane = "deterministic"
            topology: BusinessTopology | None = None
            report: BusinessTopologyValidationReport | None = None
            active_bundle = bundle
            notes: list[str] = []

            if doc_records:
                topology, report, notes = await self._run_extraction_lane(
                    ctx,
                    state,
                    log_cb,
                    bundle=doc_bundle,
                    doc_records=doc_records,
                    context_files=extraction_context,
                )
                if topology is not None:
                    lane = "flow_extraction"
                    active_bundle = doc_bundle

            if topology is None or len(topology.data_streams) < max(
                1, deterministic_streams
            ):
                if topology is not None:
                    await log_cb(
                        ctx.project_id,
                        "[custom] Business topology: flow extraction produced "
                        f"{len(topology.data_streams)} stream(s) against a deterministic "
                        f"floor of {deterministic_streams}; trying single-shot synthesis",
                        "warning",
                    )
                fallback_topology, fallback_report = await self._run_synthesis_lane(
                    ctx,
                    state,
                    log_cb,
                    bundle=bundle,
                    context_files=synthesis_context,
                )
                if fallback_topology is not None and (
                    topology is None
                    or len(fallback_topology.data_streams) > len(topology.data_streams)
                ):
                    topology, report = fallback_topology, fallback_report
                    lane = "single_shot_synthesis"
                    active_bundle = bundle
                    notes = []

            if topology is None or not topology.data_streams:
                if deterministic_streams:
                    await log_cb(
                        ctx.project_id,
                        "[custom] Business topology: model lanes did not produce a "
                        "publishable overlay; using deterministic evidence-only fallback "
                        f"({len(deterministic_candidate.systems)} systems, "
                        f"{deterministic_streams} streams)",
                        "warning",
                    )
                    topology = deterministic_candidate
                    report = deterministic_report
                    lane = "deterministic"
                    active_bundle = bundle
                    notes = []
                else:
                    errors = list(report.errors) if report is not None else [
                        "no publishable business topology was produced"
                    ]
                    validation_path.write_text(
                        json.dumps({"validated": False, "errors": errors}, indent=2),
                        encoding="utf-8",
                    )
                    topology_path.unlink(missing_ok=True)
                    _write_state(
                        state_path,
                        available=False,
                        validated=False,
                        scan_fingerprint=bundle.scan_fingerprint,
                        errors=errors,
                    )
                    state.warnings.append(
                        "Business topology synthesis was not published; documentation is unaffected"
                    )
                    await log_cb(
                        ctx.project_id,
                        "[custom] Business topology validation failed (non-blocking): "
                        + "; ".join(errors[:3]),
                        "warning",
                    )
                    return PhaseResult(
                        phase_name=self.name,
                        success=True,
                        metadata={"available": False, "validated": False, "errors": errors[:5]},
                    )

            # Re-validate against the exact persisted payload, so
            # validation_report.json describes what is served.
            report = validate_business_topology(
                topology, bundle=active_bundle, project_id=ctx.project_id
            )
            if notes:
                report.warnings = [*[f"assembly: {note}" for note in notes[:10]], *report.warnings]
            validation_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            if not report.validated:
                topology_path.unlink(missing_ok=True)
                _write_state(
                    state_path,
                    available=False,
                    validated=False,
                    scan_fingerprint=bundle.scan_fingerprint,
                    errors=report.errors,
                    warnings=report.warnings,
                )
                state.warnings.append("Business topology failed final provenance validation")
                return PhaseResult(
                    phase_name=self.name,
                    success=True,
                    metadata={"available": False, "validated": False, "errors": report.errors[:5]},
                )
            topology_path.write_text(topology.model_dump_json(indent=2), encoding="utf-8")
            _write_state(
                state_path,
                available=True,
                validated=True,
                scan_fingerprint=bundle.scan_fingerprint,
                errors=[],
                warnings=report.warnings,
                lane=lane,
            )
            await log_cb(
                ctx.project_id,
                f"[custom] Business topology complete via {lane}: "
                f"{len(topology.systems)} systems, {len(topology.data_streams)} data streams, "
                f"{len(topology.business_objects)} business objects, "
                f"{len(topology.delivery_paths)} delivery paths",
                "info",
            )
            return PhaseResult(
                phase_name=self.name,
                success=True,
                metadata={
                    "available": True,
                    "validated": True,
                    "lane": lane,
                    "systems": len(topology.systems),
                    "data_streams": len(topology.data_streams),
                    "business_objects": len(topology.business_objects),
                    "delivery_paths": len(topology.delivery_paths),
                    "deterministic_fallback": lane == "deterministic",
                },
            )
        except Exception as exc:  # noqa: BLE001 - this phase is explicitly non-blocking
            fingerprint = bundle.scan_fingerprint if bundle is not None else ""
            report_payload = {
                "validated": False,
                "scan_fingerprint": fingerprint,
                "project_id": ctx.project_id,
                "errors": [str(exc)],
                "warnings": ["Documentation generation continued without a business topology overlay"],
                "evidence_count": len(bundle.evidence_ids) if bundle is not None else 0,
                "cited_evidence_count": 0,
                "artifact_count": len(bundle.artifact_ids) if bundle is not None else 0,
                "cited_artifact_count": 0,
            }
            validation_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")
            topology_path.unlink(missing_ok=True)
            _write_state(
                state_path,
                available=False,
                validated=False,
                scan_fingerprint=fingerprint,
                errors=[str(exc)],
            )
            state.warnings.append(f"Business topology synthesis failed non-fatally: {exc}")
            await log_cb(
                ctx.project_id,
                f"[custom] Business topology failed (non-blocking): {exc}",
                "warning",
            )
            return PhaseResult(
                phase_name=self.name,
                success=True,
                metadata={"available": False, "validated": False, "error": str(exc)},
            )

    async def _run_extraction_lane(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
        *,
        bundle: "BusinessTopologyEvidenceBundle",
        doc_records: dict[str, dict[str, Any]],
        context_files: list["Path"],
    ) -> tuple["BusinessTopology | None", "BusinessTopologyValidationReport | None", list[str]]:
        """Stage A + B: extract business-level flows, then assemble in Python."""

        from mcp_server.services.business_topology import (
            assemble_topology,
            parse_flow_extraction,
            prune_to_valid,
        )
        from mcp_server.services.business_topology.prompt import flow_extraction_prompt

        # `ctx.partition` is a Partition, not a name: a composite run scopes
        # this phase to one sub-project and the solution node is named after it.
        partition_name = ctx.partition.name if ctx.partition is not None else ""
        subproject = partition_name or ctx.project_id
        prompt = flow_extraction_prompt(
            subproject=subproject,
            plugin_pack_dir=self._helpers.plugin_pack_dir,
        )

        def _validate(text: str) -> dict[str, Any]:
            extraction, dropped = parse_flow_extraction(text, subproject=subproject)
            if extraction is None:
                return {"ok": False, "errors": dropped}
            if not extraction.flows:
                # An extraction with systems but no flows describes no
                # architecture; the retry is worth one call.
                return {
                    "ok": False,
                    "errors": [
                        "flow extraction contained no usable flows",
                        *dropped[:5],
                    ],
                }
            return {"ok": True, "errors": [], "warnings": dropped[:10]}

        result = await self._dispatch_task(
            ctx,
            state,
            log_cb,
            target_id=f"flow_extraction_{bundle.scan_fingerprint[:16]}",
            prompt=prompt,
            context_files=context_files,
            validate=_validate,
        )
        if result is None or not result.success:
            await log_cb(
                ctx.project_id,
                "[custom] Business topology: flow extraction did not produce a usable "
                "result; falling back",
                "warning",
            )
            return None, None, []

        extraction, dropped = parse_flow_extraction(result.text, subproject=subproject)
        if extraction is None or not extraction.flows:
            return None, None, []
        if dropped:
            await log_cb(
                ctx.project_id,
                f"[custom] Business topology: flow extraction dropped {len(dropped)} "
                f"unusable item(s) (e.g. {dropped[0]})",
                "warning",
            )

        topology, notes = assemble_topology(
            extraction,
            bundle=bundle,
            project_id=ctx.project_id,
            doc_records=doc_records,
            subproject=partition_name,
            model_name=result.model_name,
        )
        topology, report, pruned = prune_to_valid(
            topology, bundle=bundle, project_id=ctx.project_id
        )
        if pruned:
            await log_cb(
                ctx.project_id,
                f"[custom] Business topology: pruned {len(pruned)} claim(s) that failed "
                f"provenance validation (e.g. {pruned[0]}); publishing the remainder",
                "warning",
            )
        if not report.validated:
            await log_cb(
                ctx.project_id,
                "[custom] Business topology: assembled overlay did not validate ("
                + "; ".join(report.errors[:2])
                + ")",
                "warning",
            )
            return None, report, notes + [*dropped]
        return topology, report, notes + [*dropped]

    async def _run_synthesis_lane(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
        *,
        bundle: "BusinessTopologyEvidenceBundle",
        context_files: list["Path"],
    ) -> tuple["BusinessTopology | None", "BusinessTopologyValidationReport | None"]:
        """The previous single-shot contract, kept as the second model lane."""

        from mcp_server.services.business_topology import (
            parse_validate_repair_business_topology,
        )
        from mcp_server.services.business_topology.prompt import business_topology_prompt
        from mcp_server.services.business_topology.pruning import prune_to_valid

        prompt = business_topology_prompt(
            project_id=ctx.project_id,
            scan_fingerprint=bundle.scan_fingerprint,
            plugin_pack_dir=self._helpers.plugin_pack_dir,
        )

        def _validate(text: str) -> dict[str, Any]:
            parsed, validation, repair_notes = parse_validate_repair_business_topology(
                text, bundle=bundle, project_id=ctx.project_id
            )
            payload: dict[str, Any] = {
                "ok": validation.validated,
                **validation.model_dump(),
            }
            if parsed is not None and not parsed.data_streams:
                payload["ok"] = False
                payload["errors"] = [
                    *validation.errors,
                    "topology contains no data streams; emit the grounded streams",
                ]
            if repair_notes:
                payload["repair_notes"] = repair_notes
            return payload

        result = await self._dispatch_task(
            ctx,
            state,
            log_cb,
            target_id=f"business_topology_{bundle.scan_fingerprint[:16]}",
            prompt=prompt,
            context_files=context_files,
            validate=_validate,
        )
        if result is None or not result.success:
            return None, None

        topology, report, repair_notes = parse_validate_repair_business_topology(
            result.text, bundle=bundle, project_id=ctx.project_id
        )
        if topology is None:
            return None, report
        if not report.validated:
            # Partial acceptance: a claim-local defect costs that claim, not the
            # whole architecture (the verified ETTM_part2 loss).
            topology, report, pruned = prune_to_valid(
                topology, bundle=bundle, project_id=ctx.project_id
            )
            if pruned:
                await log_cb(
                    ctx.project_id,
                    f"[custom] Business topology: pruned {len(pruned)} invalid claim(s) "
                    f"from single-shot synthesis (e.g. {pruned[0]})",
                    "warning",
                )
        if repair_notes and report.validated:
            await log_cb(
                ctx.project_id,
                "[custom] Business topology: accepted after deterministic repair "
                f"({len(repair_notes)} corrections, e.g. {repair_notes[0]})",
                "warning",
            )
        return (topology if report.validated else None), report

    async def _dispatch_task(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
        *,
        target_id: str,
        prompt: str,
        context_files: list["Path"],
        validate,
    ):
        """Run one persisted agent task with retry-with-feedback.

        Shared by both model lanes so they cannot diverge on the pieces that
        were verified to matter: a fingerprint-scoped target id (a resume must
        not accept an overlay produced for an older scan), and validation-gap
        feedback delivered as a TAIL context file. Tools are disabled for this
        phase, so the harness never applies ``_with_agent_contract`` and the
        runtime's ``validation_gaps`` would otherwise be dropped -- the verified
        werkorder retries were byte-identical blind re-rolls that diverged
        (ESB_part1 went from 3 validation errors to 36).
        """

        from mcp_server.config import config
        from mcp_server.providers.custom.agent_runtime import (
            AgentTaskContract,
            AgentTaskRuntime,
            ScopedEvidenceTools,
            default_input_budget,
            default_tool_budget,
        )
        from mcp_server.providers.custom.models import ModelOptions, ModelResult
        from mcp_server.providers.custom.transient_artifacts import (
            transient_dir,
            transient_write,
        )

        provider = self._helpers.write_provider
        options = ModelOptions(
            max_tokens=config.phase3_max_tokens,
            temperature=0.0,
            retries=1,
            timeout_s=300.0,
            json_mode=True,
            provider_options=self._helpers.provider_options(
                provider, phase="business_topology"
            ),
        )
        contract = AgentTaskContract(
            phase="business_topology",
            target_id=target_id,
            allowed_units=[unit.name for unit in state.facts_manifest.units],
            allowed_groups=list(state.facts_manifest.groups),
            allowed_categories=[],
            starter_context_files=[
                path.relative_to(ctx.staging_dir).as_posix()
                for path in context_files
                if path.exists() and path.is_relative_to(ctx.staging_dir)
            ],
            tool_budget=default_tool_budget(),
            input_budget=default_input_budget(),
            expected_artifact="business topology JSON",
            max_attempts=config.agent_max_task_attempts,
        )
        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
        cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
        if cached is not None:
            await log_cb(
                ctx.project_id,
                f"[custom] RESUME: topology task {target_id} already accepted",
                "info",
            )
            return ModelResult(success=True, text=cached, model_name=provider.model_name)

        harness_provider = self._helpers._p  # type: ignore[attr-defined]

        async def _dispatch(
            task: AgentTaskContract,
            tools: ScopedEvidenceTools,
        ) -> ModelResult:
            effective_context = context_files
            if task.attempt > 1:
                gaps = "\n".join(f"- {gap}" for gap in task.validation_gaps) or (
                    "- Previous output failed validation."
                )
                repair_tail = transient_dir(
                    ctx.staging_dir, "claim_repair", "business_topology"
                ) / f"{task.target_id}__attempt_{task.attempt}.md"
                transient_write(
                    repair_tail,
                    "REPAIR INSTRUCTION: Return a complete corrected replacement "
                    f"JSON object. Fix only these gaps:\n{gaps}\n\n"
                    "Do not widen scope while repairing: drop any claim you cannot "
                    "ground in the attached evidence instead of inventing names, and "
                    "keep every already-valid claim unchanged.",
                )
                effective_context = [*context_files, repair_tail]
            return await harness_provider._complete_core_generation(
                ctx,
                state,
                provider=provider,
                phase="business_topology",
                target=task.target_id,
                prompt=prompt,
                context_files=effective_context,
                opts=options,
                log_cb=log_cb,
                task_contract=task,
                scoped_tools=tools,
            )

        task_result = await runtime.run(contract, _dispatch, validate)
        return task_result.result

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        return await self.run(ctx, state, log_cb)

    def estimate(self, groups: list[str], docs: list[str], facts_unit_count: int) -> PhaseEstimate:
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=1,
            calls_max=4,
            notes=(
                "Flow extraction plus assembly (1-2 calls); single-shot synthesis is "
                "attempted only when extraction underperforms the deterministic floor"
            ),
        )


def _write_state(
    path,
    *,
    available: bool,
    validated: bool,
    scan_fingerprint: str,
    errors: list[str],
    warnings: list[str] | None = None,
    lane: str = "",
) -> None:
    payload: dict[str, Any] = {
        "available": available,
        "validated": validated,
        "scan_fingerprint": scan_fingerprint,
        "errors": errors,
        "warnings": warnings or [],
    }
    if lane:
        payload["lane"] = lane
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
