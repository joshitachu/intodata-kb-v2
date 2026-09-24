"""Phase 3.7: the custom harness's adapter onto the shared topology builder.

The pipeline itself -- flow extraction (Stage A + B), single-shot synthesis,
deterministic floor, pruning, deterministic enrichment, validation -- lives in
:mod:`mcp_server.services.business_topology.builder` so every engine runs the
same one. This phase only supplies what is harness-specific:

* the inputs, from the run context (partition scope, plugin pack, the staged
  runtime-evidence cache that ``Phase3Docs`` produced and ``Phase4Cleanup``
  destroys, so it is still on disk here);
* the :class:`~mcp_server.services.business_topology.builder.TopologyLLM`
  strategy, which is the harness's persisted agent task with retry-with-
  feedback, budget reservation under ``phase="business_topology"`` and resume;
* writing ``docs_staging/_topology/`` and folding the result into the run state.

Additive and non-blocking: a topology failure never fails the documentation run.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.services.business_topology.builder import TopologyCompletion


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
        from mcp_server.services.business_topology.builder import (
            TopologyBuildInputs,
            build_business_topology,
            load_staged_runtime_payload,
        )

        # Everything up to the build is inside the guard: a topology failure,
        # including a malformed facts manifest while building the inputs, must
        # never fail the documentation run.
        try:
            # `ctx.partition` is a Partition, not a name: a composite run scopes
            # this phase to one sub-project and the solution node is named after it.
            partition = ctx.partition
            inputs = TopologyBuildInputs(
                project_id=ctx.project_id,
                staging_dir=ctx.staging_dir,
                scan_output_dir=config.intodoc_output_dir / ctx.project_id,
                subproject=partition.name if partition is not None else "",
                unit_allowlist=(
                    frozenset(state.facts_manifest.unit_names()) if partition is not None else None
                ),
                plugin_pack_dir=getattr(self._helpers, "plugin_pack_dir", None),
                runtime_payload=load_staged_runtime_payload(ctx.staging_dir),
                log_prefix="[custom]",
            )

            async def _log(message: str, level: str) -> None:
                await log_cb(ctx.project_id, message, level)

            result = await build_business_topology(
                inputs, _HarnessTopologyLLM(self, ctx, state, log_cb), log=_log
            )
        except Exception as exc:  # noqa: BLE001 - topology is advisory
            logger.warning(
                "Business topology phase failed for %s: %s", ctx.project_id, exc, exc_info=True
            )
            state.warnings.append(f"Business topology failed (non-blocking): {exc}")
            # A topology left in staging by an earlier attempt must not publish
            # as if this run had produced it.
            stale = ctx.staging_dir / "_topology" / "business_topology.json"
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
            return PhaseResult(
                phase_name=self.name,
                success=True,
                metadata={"available": False, "validated": False, "error": str(exc)},
            )
        try:
            result.write(ctx.staging_dir / "_topology")
        except OSError as exc:
            state.warnings.append(f"Business topology could not be written: {exc}")
            return PhaseResult(
                phase_name=self.name,
                success=True,
                metadata={"available": False, "validated": False, "error": str(exc)},
            )
        state.warnings.extend(result.run_warnings)
        return PhaseResult(phase_name=self.name, success=True, metadata=result.metadata)

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


class _HarnessTopologyLLM:
    """The harness strategy: one persisted agent task per topology lane."""

    def __init__(
        self,
        phase: Phase37BusinessTopology,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> None:
        self._phase = phase
        self._ctx = ctx
        self._state = state
        self._log_cb = log_cb

    async def complete(
        self,
        prompt: str,
        *,
        context_files: list["Path"],
        purpose: str,
        target_id: str,
        validate,
    ) -> "TopologyCompletion | None":
        from mcp_server.services.business_topology.builder import TopologyCompletion

        result = await self._phase._dispatch_task(
            self._ctx,
            self._state,
            self._log_cb,
            target_id=target_id,
            prompt=prompt,
            context_files=context_files,
            validate=validate,
        )
        # A rejected-but-successful task still hands its text on: the builder
        # re-parses and prunes, exactly as this phase always did.
        if result is None or not result.success:
            return None
        return TopologyCompletion(text=result.text, model_name=result.model_name or "")
