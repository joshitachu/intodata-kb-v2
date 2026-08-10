"""Phase 2.6: Scorecard — generate the quality/risk scorecard from analysis output."""
from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_server.providers.custom.debug_artifacts import debug_dir, debug_write
from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl


class Phase26Scorecard(Phase):
    def __init__(self, helpers: "HarnessHelpersImpl") -> None:
        self._helpers = helpers

    @property
    def name(self) -> str:
        return "phase26_scorecard"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return not getattr(plan, "scorecard", True)

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
        from mcp_server.providers.custom.prompts import scorecard_prompt
        from mcp_server.providers.custom.validators import validate_scorecard_contract

        assessment_dir = ctx.staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        analysis_files = [
            p for p in sorted((ctx.staging_dir / "_analysis").glob("*.md"))
            if not p.name.startswith("_") and "_chunk_" not in p.name
        ]
        investigation_files = sorted((ctx.staging_dir / "_investigation").glob("*.md"))
        pack_dir = ctx.staging_dir / "_assessment" / "prompt_packs"
        scorecard_pack = pack_dir / "_scorecard_prompt_pack.md"
        scorecard_opts = ModelOptions(
            max_tokens=config.phase2_max_tokens,
            temperature=0.1,
            retries=1,
            timeout_s=300.0,
            provider_options=self._helpers.provider_options(self._helpers.model_provider),
        )
        scorecard_prompt_text = scorecard_prompt(
            state.facts_manifest,
            pack=self._helpers.pack,
            plugin_pack_dir=self._helpers.plugin_pack_dir,
        )
        context_files = []
        if scorecard_pack.exists():
            context_files.append(scorecard_pack)
        else:
            context_files.extend(self._helpers.existing(ctx.staging_dir / "_preprocess", [
                "_scoring_rubric.md",
                "_anti_patterns.md",
                "_red_flags.md",
                "_security_checklist.md",
                "_pattern_library.md",
            ]))
        context_files.extend(investigation_files)
        context_files.extend(analysis_files)
        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
        contract = AgentTaskContract(
            phase="scorecard",
            target_id="scorecard",
            allowed_units=[unit.name for unit in state.facts_manifest.units],
            allowed_groups=list(state.facts_manifest.groups),
            allowed_categories=self._helpers.default_evidence_categories(),
            starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
            tool_budget=default_tool_budget(),
            input_budget=default_input_budget(),
            expected_artifact="scorecard markdown",
            max_attempts=config.agent_max_task_attempts,
        )
        cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
        scorecard_task_output_path: str | None = None
        if cached is not None:
            result = ModelResult(success=True, text=cached, model_name=self._helpers.model_provider.model_name)
            await log_cb(ctx.project_id, "[custom] RESUME: skipping Phase 2.6 scorecard (accepted)", "info")
        else:
            # Import _complete_core_generation via the provider to avoid circular deps
            provider = self._helpers._p  # type: ignore[attr-defined]

            async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                return await provider._complete_core_generation(
                    ctx,
                    state,
                    provider=self._helpers.model_provider,
                    phase="phase2.6",
                    target=task.target_id,
                    prompt=scorecard_prompt_text,
                    context_files=context_files,
                    opts=scorecard_opts,
                    log_cb=log_cb,
                    task_contract=task,
                    scoped_tools=tools,
                )

            task_result = await runtime.run(
                contract,
                _dispatch,
                lambda text: self._helpers.validate_agent_markdown(text, [], min_bytes=200),
            )
            result = task_result.result
            scorecard_task_output_path = task_result.output_path
        if not result.success:
            # Salvage-first (P0.2): the scorecard is an enrichment, not a
            # publish precondition. A failed scorecard (verified: ETTM died
            # here after Phase 2 consumed the mis-sized call budget) writes an
            # explicit unavailability scorecard and the run continues; docs
            # that reference scorecard dimensions mark them unavailable.
            reason = result.raw_error or "scorecard generation failed"
            await log_cb(
                ctx.project_id,
                f"[custom] Scorecard generation failed ({reason}); writing "
                "unavailability scorecard and continuing -- resume retries it",
                "warning",
            )
            state.warnings.append(f"Scorecard unavailable in this run: {reason[:200]}")
            (assessment_dir / "scorecard.md").write_text(
                "# Assessment Scorecard\n\n"
                f"Canonical inventory: {state.facts_manifest.unit_count} "
                f"{state.facts_manifest.canonical_unit_label}.\n\n"
                "> **SCORECARD UNAVAILABLE:** scorecard generation did not "
                "complete in this run. Documents must mark scorecard-derived "
                "dimensions as unavailable rather than inventing scores.\n\n"
                f"Failure: {reason}\n",
                encoding="utf-8",
            )
            return PhaseResult(
                phase_name=self.name,
                success=True,
                metadata={"scorecard": "unavailable"},
            )
        if int(result.metadata.get("slices") or 0) > 1:
            reduced = await self._helpers.reduce_split_scorecard(ctx, state, result.text, scorecard_pack, log_cb)
            if not reduced.success:
                await log_cb(
                    ctx.project_id,
                    f"[custom] Scorecard reducer failed ({reduced.raw_error}); "
                    "keeping the unreduced sliced scorecard",
                    "warning",
                )
                state.warnings.append(
                    f"Scorecard reducer failed: {str(reduced.raw_error)[:200]}"
                )
            else:
                result = reduced
                if scorecard_task_output_path:
                    from pathlib import Path as _Path
                    _Path(scorecard_task_output_path).write_text(result.text.strip(), encoding="utf-8")

        if not self._helpers.plugin_mode:
            sc_check = validate_scorecard_contract(result.text)
            if not sc_check["ok"]:
                await log_cb(
                    ctx.project_id,
                    "[custom] Phase 2.6 scorecard contract gaps: " + "; ".join(sc_check["errors"][:5]),
                    "warning",
                )
                debug_write(
                    debug_dir(ctx.staging_dir, "_assessment", "_scorecard_contract_errors.json"),
                    __import__("json").dumps(sc_check, indent=2),
                )
                state.warnings.extend(sc_check["errors"])

        content = (
            "# Assessment Scorecard\n\n"
            f"Canonical inventory: {state.facts_manifest.unit_count} "
            f"{state.facts_manifest.canonical_unit_label}.\n\n"
            f"{result.text.strip()}\n"
        )
        (assessment_dir / "scorecard.md").write_text(content, encoding="utf-8")
        await log_cb(ctx.project_id, "[custom] Phase 2.6 scorecard complete", "info")
        return PhaseResult(phase_name=self.name, success=True)

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        scorecard = ctx.staging_dir / "_assessment" / "scorecard.md"
        if scorecard.exists() and scorecard.stat().st_size > 0:
            # An unavailability scorecard (salvage path) is a resume marker,
            # not a cached result -- retry it.
            text = scorecard.read_text(encoding="utf-8", errors="replace")
            if "SCORECARD UNAVAILABLE" not in text:
                await log_cb(ctx.project_id, "[custom] RESUME: Phase 2.6 scorecard cached", "info")
                return PhaseResult(phase_name=self.name, success=True, was_cached=True)
        return await self.run(ctx, state, log_cb)

    def estimate(self, groups: list[str], docs: list[str], facts_unit_count: int) -> PhaseEstimate:
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=1,
            calls_max=2,
            notes="1 scorecard call, up to 2 with retry",
        )
