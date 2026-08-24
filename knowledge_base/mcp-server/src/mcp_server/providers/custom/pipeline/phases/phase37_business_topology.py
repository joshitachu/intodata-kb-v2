"""Additive, non-blocking business topology synthesis after documentation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl


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
        from mcp_server.services.business_topology import (
            build_business_topology_evidence,
            build_deterministic_topology,
            build_topology_context_files,
            parse_validate_repair_business_topology,
            validate_business_topology,
        )
        from mcp_server.services.business_topology.prompt import business_topology_prompt

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
            # PUSH the high-signal scanner evidence directly into the model
            # context instead of relying on budget-limited agentic tool pulls
            # (the verified ETTM_part1 starvation). Tools are disabled for this
            # phase in _tool_policy, so these files reach the model via
            # provider.complete(context_files=...).
            topology_context_files = build_topology_context_files(
                bundle,
                ctx.staging_dir,
                byte_cap=config.topology_context_evidence_bytes,
            )
            prompt = business_topology_prompt(
                project_id=ctx.project_id,
                scan_fingerprint=bundle.scan_fingerprint,
                plugin_pack_dir=self._helpers.plugin_pack_dir,
            )
            # Built once, used two ways: (a) as the floor the LLM output must
            # beat -- a schema-valid but EMPTY topology (verified pre-fix
            # ETTM_part1 shape: the model refused to fabricate and emitted no
            # claims) must not be published when scanner edges ground real
            # streams; (b) as the published fallback when the LLM leg fails.
            deterministic_candidate = build_deterministic_topology(
                bundle, project_id=ctx.project_id
            )
            deterministic_report = validate_business_topology(
                deterministic_candidate, bundle=bundle, project_id=ctx.project_id
            )
            deterministic_has_streams = (
                deterministic_report.validated
                and bool(deterministic_candidate.data_streams)
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
                # Fingerprint-scoped target prevents a resume from accepting an
                # otherwise-valid overlay produced for an older scan.
                target_id=f"business_topology_{bundle.scan_fingerprint[:16]}",
                allowed_units=[unit.name for unit in state.facts_manifest.units],
                allowed_groups=list(state.facts_manifest.groups),
                allowed_categories=[],
                starter_context_files=[
                    path.relative_to(ctx.staging_dir).as_posix()
                    for path in topology_context_files
                    if path.exists()
                ],
                tool_budget=default_tool_budget(),
                input_budget=default_input_budget(),
                expected_artifact="business topology JSON",
                max_attempts=config.agent_max_task_attempts,
            )
            runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
            cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
            if cached is not None:
                result = ModelResult(success=True, text=cached, model_name=provider.model_name)
                await log_cb(
                    ctx.project_id,
                    "[custom] RESUME: business topology task already accepted",
                    "info",
                )
            else:
                harness_provider = self._helpers._p  # type: ignore[attr-defined]

                async def _dispatch(
                    task: AgentTaskContract,
                    tools: ScopedEvidenceTools,
                ) -> ModelResult:
                    # Tools are disabled for this phase, so the harness never
                    # applies _with_agent_contract to the prompt and the
                    # runtime's validation_gaps would otherwise be silently
                    # dropped: the verified werkorder retries were byte-
                    # identical blind re-rolls that diverged (ESB_part1 went
                    # from 3 validation errors to 36). Feed the gaps back as a
                    # transient TAIL context file (same cache-friendly pattern
                    # as the harness claim-repair dispatch) so attempt 2 fixes
                    # the reported errors instead of resampling from scratch.
                    effective_context = topology_context_files
                    if task.attempt > 1:
                        gaps = "\n".join(
                            f"- {gap}" for gap in task.validation_gaps
                        ) or "- Previous JSON failed validation."
                        repair_tail = transient_dir(
                            ctx.staging_dir, "claim_repair", "business_topology"
                        ) / f"{task.target_id}__attempt_{task.attempt}.md"
                        transient_write(
                            repair_tail,
                            "REPAIR INSTRUCTION: Return a complete corrected "
                            "replacement JSON object. Fix only these validation "
                            f"gaps:\n{gaps}\n\n"
                            "Do not widen scope while repairing: drop any claim "
                            "you cannot ground in the attached evidence instead "
                            "of inventing IDs, and keep every already-valid "
                            "claim unchanged.",
                        )
                        effective_context = [*topology_context_files, repair_tail]
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

                def _runtime_validation(text: str) -> dict[str, Any]:
                    # Repair-aware acceptance: a raw output that fails only on
                    # the verified mechanical grounding shapes (missing
                    # artifact_id citations, fabricated `artifact:` prefixes,
                    # ungrounded sibling-partition solutions) is accepted after
                    # deterministic repair instead of burning the single LLM
                    # retry. When repair cannot help, the RAW errors flow into
                    # validation_gaps for the retry.
                    parsed, validation, repair_notes = (
                        parse_validate_repair_business_topology(
                            text,
                            bundle=bundle,
                            project_id=ctx.project_id,
                        )
                    )
                    payload = {
                        "ok": validation.validated,
                        **validation.model_dump(),
                    }
                    if (
                        validation.validated
                        and parsed is not None
                        and not parsed.data_streams
                        and deterministic_has_streams
                    ):
                        # Schema-valid but empty while scanner edges ground
                        # real streams: give the retry a chance to emit them.
                        payload["ok"] = False
                        payload["errors"] = [
                            *validation.errors,
                            "topology contains no data streams although scanner "
                            "directional edges in the attached evidence ground at "
                            "least one stream; emit the grounded streams",
                        ]
                    if repair_notes:
                        payload["repair_notes"] = repair_notes
                    return payload

                task_result = await runtime.run(
                    contract,
                    _dispatch,
                    _runtime_validation,
                )
                result = task_result.result

            topology, report, repair_notes = parse_validate_repair_business_topology(
                result.text,
                bundle=bundle,
                project_id=ctx.project_id,
            )
            if topology is not None and repair_notes:
                await log_cb(
                    ctx.project_id,
                    "[custom] Business topology: accepted after deterministic repair "
                    f"({len(repair_notes)} corrections, e.g. {repair_notes[0]})",
                    "warning",
                )
            used_deterministic_fallback = False
            llm_failed = not result.success or topology is None or not report.validated
            llm_empty_but_valid = (
                not llm_failed
                and topology is not None
                and not topology.data_streams
                and deterministic_has_streams
            )
            if llm_failed or llm_empty_but_valid:
                # The agent runtime already retried the LLM up to max_attempts.
                # Rather than dropping topology entirely (or publishing a
                # schema-valid but empty overlay when scanner edges ground real
                # streams), use the evidence-only deterministic overlay so
                # `available` can still be true with real cited edges.
                fallback = deterministic_candidate
                fallback_report = deterministic_report
                if fallback_report.validated and fallback.data_streams:
                    if llm_empty_but_valid:
                        llm_errors = [
                            "LLM topology validated but contained no data streams"
                        ]
                    else:
                        llm_errors = list(report.errors)
                        if not result.success:
                            llm_errors.insert(0, result.raw_error or "model call failed")
                    await log_cb(
                        ctx.project_id,
                        "[custom] Business topology: LLM synthesis did not validate ("
                        + "; ".join(llm_errors[:2])
                        + "); using deterministic evidence-only fallback "
                        + f"({len(fallback.systems)} systems, {len(fallback.data_streams)} streams)",
                        "warning",
                    )
                    topology, report = fallback, fallback_report
                    repair_notes = []
                    used_deterministic_fallback = True
                elif llm_empty_but_valid:
                    # Unreachable in practice (deterministic_has_streams implies
                    # a validated stream-bearing fallback), but never turn a
                    # valid LLM overlay into `available=false` here.
                    pass
                else:
                    errors = list(report.errors)
                    if not result.success:
                        errors.insert(0, result.raw_error or "model call failed")
                    report.validated = False
                    report.errors = errors
                    validation_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
                    topology_path.unlink(missing_ok=True)
                    _write_state(
                        state_path,
                        available=False,
                        validated=False,
                        scan_fingerprint=bundle.scan_fingerprint,
                        errors=errors,
                    )
                    state.warnings.append("Business topology synthesis was not published; documentation is unaffected")
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

            if not used_deterministic_fallback:
                topology.metadata.model_name = result.model_name
            # Re-run validation against the exact persisted payload after adding
            # runtime provenance, so validation_report.json describes what is served.
            report = validate_business_topology(
                topology,
                bundle=bundle,
                project_id=ctx.project_id,
            )
            if repair_notes and report.validated:
                report.warnings = [
                    "accepted after deterministic repair "
                    f"({len(repair_notes)} corrections)",
                    *[f"repaired: {note}" for note in repair_notes[:20]],
                    *report.warnings,
                ]
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
            )
            await log_cb(
                ctx.project_id,
                f"[custom] Business topology complete: {len(topology.systems)} systems, "
                f"{len(topology.data_streams)} data streams"
                + (" (deterministic fallback)" if used_deterministic_fallback else ""),
                "info",
            )
            return PhaseResult(
                phase_name=self.name,
                success=True,
                metadata={
                    "available": True,
                    "validated": True,
                    "systems": len(topology.systems),
                    "data_streams": len(topology.data_streams),
                    "deterministic_fallback": used_deterministic_fallback,
                    "repaired": bool(repair_notes) and not used_deterministic_fallback,
                },
            )
        except Exception as exc:  # noqa: BLE001 - this phase is explicitly non-blocking
            fingerprint = bundle.scan_fingerprint if bundle is not None else ""
            report = {
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
            validation_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
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
            calls_max=2,
            notes="One additive JSON synthesis task; validation retries remain non-blocking",
        )


def _write_state(
    path,
    *,
    available: bool,
    validated: bool,
    scan_fingerprint: str,
    errors: list[str],
    warnings: list[str] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "available": available,
        "validated": validated,
        "scan_fingerprint": scan_fingerprint,
        "errors": errors,
        "warnings": warnings or [],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
