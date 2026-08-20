"""EvidenceQualityGate — checks preprocess bundle sizes before Phase 3."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult
from mcp_server.providers.custom.pipeline.warning_collector import WarningCode

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState


class EvidenceQualityGate(Phase):
    THIN_THRESHOLD_BYTES: int = 500

    @property
    def name(self) -> str:
        return "evidence_quality_gate"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return False

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        preprocess_dir = ctx.staging_dir / "_preprocess"
        thin: list[str] = []

        for group in ctx.preprocess_groups:
            bundle = preprocess_dir / f"{group}.md"
            size = bundle.stat().st_size if bundle.exists() else 0
            if size < self.THIN_THRESHOLD_BYTES:
                thin.append(group)
                await state.warning_collector.emit(
                    "evidence_quality_gate",
                    WarningCode.THIN_BUNDLE,
                    f"Thin bundle for group '{group}': {size} bytes (threshold {self.THIN_THRESHOLD_BYTES})",
                    context={"group": group, "size_bytes": size, "threshold": self.THIN_THRESHOLD_BYTES},
                )

        # Merge, never overwrite: Phase 2 salvage containment (P0.2) marks
        # failed groups PARTIAL by adding them to thin_bundle_groups before
        # this gate runs; clobbering them would drop the evidence notice for
        # exactly the groups whose analysis is unreliable.
        merged = list(dict.fromkeys([*state.thin_bundle_groups, *thin]))
        state.thin_bundle_groups = merged

        assessment_dir = ctx.staging_dir / "_assessment"
        assessment_dir.mkdir(parents=True, exist_ok=True)
        (assessment_dir / "thin_bundle_report.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "thin_groups": merged,
                    "threshold_bytes": self.THIN_THRESHOLD_BYTES,
                    "partial_groups": list(state.partial_groups),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        return PhaseResult(
            phase_name=self.name,
            success=True,
            metadata={"thin_groups": thin},
        )

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        report = ctx.staging_dir / "_assessment" / "thin_bundle_report.json"
        if report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            state.thin_bundle_groups = data.get("thin_groups", [])
            return PhaseResult(phase_name=self.name, success=True, was_cached=True)
        return await self.run(ctx, state, log_cb)

    def estimate(
        self,
        groups: list[str],
        docs: list[str],
        facts_unit_count: int,
    ) -> PhaseEstimate:
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=0,
            calls_max=0,
            notes="No LLM calls — pure file size check",
        )
