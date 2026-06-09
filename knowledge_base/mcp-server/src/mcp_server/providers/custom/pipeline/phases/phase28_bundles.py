"""Phase 2.8: Doc-pack bundles — deterministic reassembly of analysis sections into doc bundles."""
from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl


class Phase28Bundles(Phase):
    def __init__(self, helpers: "HarnessHelpersImpl") -> None:
        self._helpers = helpers

    @property
    def name(self) -> str:
        return "phase28_bundles"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return not getattr(plan, "doc_bundles", True)

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        # _phase28_bundles is synchronous
        self._helpers.phase28_bundles(ctx, state.facts_manifest)
        self._helpers.write_documentation_obligations(ctx, state.facts_manifest)
        return PhaseResult(phase_name=self.name, success=True)

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        manifest = ctx.staging_dir / "_analysis" / "_doc_pack_manifest.json"
        if manifest.exists():
            await log_cb(ctx.project_id, "[custom] RESUME: Phase 2.8 bundles cached", "info")
            return PhaseResult(phase_name=self.name, success=True, was_cached=True)
        return await self.run(ctx, state, log_cb)

    def estimate(self, groups: list[str], docs: list[str], facts_unit_count: int) -> PhaseEstimate:
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=0,
            calls_max=0,
            notes="No LLM calls — deterministic reassembly",
        )
