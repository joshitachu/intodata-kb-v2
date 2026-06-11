"""Phase 4: Cleanup — post-generation artifact cleanup and staging finalisation."""
from __future__ import annotations

import json
import shutil
import time
from typing import TYPE_CHECKING

from mcp_server.providers.custom.debug_artifacts import debug_dir, debug_write
from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl


class Phase4Cleanup(Phase):
    def __init__(self, helpers: "HarnessHelpersImpl") -> None:
        self._helpers = helpers

    @property
    def name(self) -> str:
        return "phase4_cleanup"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return False

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        # The enterprise gate report is always written before this phase on the normal
        # generate() path; this guard is a safety net for direct phase invocations.
        if not (ctx.staging_dir / "_assessment" / "enterprise_gates.json").exists():
            gate_report = self._helpers.enterprise_gate_report(ctx, state)
            await self._helpers.write_audit_bundle(ctx, state, gate_report)
        archived_engineering_facts = self._helpers.archive_engineering_artifacts(ctx, state)
        index = {
            "project_id": ctx.project_id,
            "platform": state.facts_manifest.platform,
            "groups": state.facts_manifest.groups,
            "unit_count": state.facts_manifest.unit_count,
            "canonical_unit_label": state.facts_manifest.canonical_unit_label,
            "docs": self._helpers.required_docs_list,
            "engineering_facts": archived_engineering_facts,
            "provider": "custom",
            "model": self._helpers.model_provider.model_name,
            "write_model": self._helpers.write_provider.model_name,
            "generated_at": time.time(),
        }
        if state.warnings:
            index["warnings"] = state.warnings
            index["warnings_count"] = len(state.warnings)
        debug_write(
            debug_dir(ctx.staging_dir, "index.json"),
            json.dumps(index, indent=2),
        )
        if state.warning_collector.has_warnings():
            debug_write(
                debug_dir(ctx.staging_dir, "_assessment", "warnings.json"),
                json.dumps(
                    {
                        "schema_version": 1,
                        "warnings": [w.model_dump() for w in state.warning_collector.warnings],
                    },
                    indent=2,
                ),
            )
        calls_json = json.dumps([c.model_dump() for c in state.calls], indent=2)
        debug_write(debug_dir(ctx.staging_dir, "provider_calls.json"), calls_json)
        debug_write(debug_dir(ctx.staging_dir, "_audit", "provider_calls.json"), calls_json)

        for directory in ("_preprocess", "_analysis", "_transient"):
            target = ctx.staging_dir / directory
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

        from pathlib import Path as _Path
        logs_dir = _Path(ctx.logs_abs)
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        (logs_dir / f"run_{ctx.project_id}_{ts}.log").write_text(
            f"project_id: {ctx.project_id}\n"
            f"provider: custom\n"
            f"model: {self._helpers.model_provider.model_name}\n"
            f"write_model: {self._helpers.write_provider.model_name}\n"
            f"unit_count: {state.facts_manifest.unit_count}\n"
            f"input_tokens: {state.input_tokens}\n"
            f"output_tokens: {state.output_tokens}\n",
            encoding="utf-8",
        )
        return PhaseResult(phase_name=self.name, success=True)

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
            calls_min=0,
            calls_max=0,
            notes="No LLM calls",
        )
