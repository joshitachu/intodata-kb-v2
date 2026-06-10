"""Phase 2: Analysis — produce structured group-level analysis from investigation output."""
from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult
from mcp_server.providers.custom.pipeline.warning_collector import WarningCode

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl


class Phase2Analysis(Phase):
    def __init__(
        self,
        helpers: "HarnessHelpersImpl",
        execution_plan: "ExecutionPlan | None" = None,
        analysis_policy: "str | None" = None,
    ) -> None:
        self._helpers = helpers
        self._execution_plan = execution_plan
        self._analysis_policy = analysis_policy

    @property
    def name(self) -> str:
        return "phase2_analysis"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return getattr(plan, "analysis_mode", "full") == "skip"

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        execution_plan = self._execution_plan
        analysis_policy = self._analysis_policy
        from mcp_server.config import config
        from mcp_server.providers.custom.repair_planner import (
            deterministic_coverage_marker_patch,
            deterministic_phase2_section_patch,
            mentioned_units_by_group,
            phase2_report_plan,
        )
        from mcp_server.providers.custom.validators import (
            validate_analysis_coverage,
            validate_analysis_depth,
            validate_analysis_guide_sections,
        )

        # Determine policy — callers can override; fall back to plan or helpers
        if analysis_policy is None:
            if execution_plan is not None:
                analysis_policy = {
                    "skip": "skip",
                    "direct": "bounded",
                    "skeleton_plus_deep_dive": "dag_tiered",
                    "full": "run",
                }[execution_plan.analysis_mode]
            else:
                analysis_policy = self._helpers.phase_policy("analysis")

        facts = state.facts_manifest
        use_direct_path = facts.unit_count <= config.small_project_threshold

        if analysis_policy == "skip":
            await log_cb(
                ctx.project_id,
                "[custom] pack contract path: using declared evidence bundles and structured Phase 2 coverage",
                "info",
            )
            self._helpers.phase2_pack_contract_records(ctx, state)
        elif analysis_policy == "bounded" or (analysis_policy == "run" and use_direct_path):
            label = (
                "bounded (pack policy)"
                if analysis_policy == "bounded"
                else f"direct path: {facts.unit_count} units <= {config.small_project_threshold} threshold"
            )
            await log_cb(ctx.project_id, f"[custom] Phase 2 {label}; no tool loops", "info")
            await self._helpers.phase2_analysis_direct(ctx, state, log_cb)
        elif analysis_policy == "dag_tiered" and execution_plan is not None:
            await log_cb(
                ctx.project_id,
                "[custom] DAG Phase 2 tiered analysis: skeleton plus selected deep dives",
                "info",
            )
            await self._helpers.phase2_analysis_dag_tiered(ctx, state, execution_plan, log_cb)
        else:
            await self._helpers.phase2_analysis(ctx, state, log_cb)

        if self._helpers.plugin_mode:
            await log_cb(ctx.project_id, "[custom] Phase 2 coverage source: structured sidecar", "info")

        coverage = validate_analysis_coverage(
            ctx.staging_dir / "_analysis",
            facts,
            use_structured=self._helpers.plugin_mode,
        )
        (ctx.staging_dir / "_analysis" / "_coverage_report.json").write_text(
            coverage.model_dump_json(indent=2), encoding="utf-8"
        )
        if not coverage.ok or coverage.coverage_count != facts.unit_count:
            coverage_plan = phase2_report_plan(report=coverage, report_kind="coverage")
            await self._write_repair_plan(ctx, coverage_plan, "phase2_coverage")
            patched = self._patch_coverage_markers(
                ctx,
                facts,
                coverage.missing_units,
                mentioned_units_by_group,
                deterministic_coverage_marker_patch,
            )
            if patched:
                await log_cb(
                    ctx.project_id,
                    f"[custom] Phase 2 coverage marker repair applied deterministically: {patched} group(s)",
                    "warning",
                )
            coverage = validate_analysis_coverage(
                ctx.staging_dir / "_analysis",
                facts,
                use_structured=self._helpers.plugin_mode,
            )
            (ctx.staging_dir / "_analysis" / "_coverage_report.json").write_text(
                coverage.model_dump_json(indent=2), encoding="utf-8"
            )
        if not coverage.ok or coverage.coverage_count != facts.unit_count:
            if coverage.coverage_count == 0:
                raise RuntimeError(
                    "; ".join(coverage.errors) or "Phase 2 coverage: zero units covered"
                )
            await state.warning_collector.emit(
                "phase2",
                WarningCode.PHASE2_COVERAGE_PARTIAL,
                "; ".join(coverage.errors) or "Phase 2 coverage partial after repair",
                {"covered": coverage.coverage_count, "total": facts.unit_count},
            )
        else:
            await log_cb(ctx.project_id, "[custom] Phase 2 coverage gate passed", "info")

        depth = validate_analysis_depth(
            ctx.staging_dir / "_analysis",
            ctx.staging_dir / "_preprocess",
            facts,
            use_structured=self._helpers.plugin_mode,
        )
        (ctx.staging_dir / "_analysis" / "_depth_report.json").write_text(
            depth.model_dump_json(indent=2), encoding="utf-8"
        )
        if not depth.ok or depth.coverage_count != facts.unit_count:
            depth_plan = phase2_report_plan(report=depth, report_kind="depth")
            await self._write_repair_plan(ctx, depth_plan, "phase2_depth")
            await log_cb(
                ctx.project_id,
                "[custom] Phase 2 depth issue recorded without LLM repair; byte count alone is advisory",
                "warning",
            )
            (ctx.staging_dir / "_analysis" / "_depth_report.json").write_text(
                depth.model_dump_json(indent=2), encoding="utf-8"
            )
        if not depth.ok or depth.coverage_count != facts.unit_count:
            await state.warning_collector.emit(
                "phase2",
                WarningCode.PHASE2_DEPTH_PARTIAL,
                "; ".join(depth.errors) or "Phase 2 depth partial after repair",
                {"covered": depth.coverage_count, "total": facts.unit_count},
            )
        else:
            await log_cb(ctx.project_id, "[custom] Phase 2 depth gate passed", "info")

        guide_sections = validate_analysis_guide_sections(
            ctx.staging_dir / "_analysis",
            facts,
            require_group_summary=not self._helpers.plugin_mode,
        )
        (ctx.staging_dir / "_analysis" / "_guide_sections_report.json").write_text(
            guide_sections.model_dump_json(indent=2), encoding="utf-8"
        )
        if not guide_sections.ok:
            guide_plan = phase2_report_plan(report=guide_sections, report_kind="guide_sections")
            await self._write_repair_plan(ctx, guide_plan, "phase2_guide_sections")
            patched = self._patch_guide_sections(ctx, facts, deterministic_phase2_section_patch)
            await log_cb(
                ctx.project_id,
                f"[custom] Phase 2 guide-section repair applied deterministically: {patched} group(s)",
                "warning",
            )
            guide_sections = validate_analysis_guide_sections(
                ctx.staging_dir / "_analysis",
                facts,
                require_group_summary=not self._helpers.plugin_mode,
            )
            (ctx.staging_dir / "_analysis" / "_guide_sections_report.json").write_text(
                guide_sections.model_dump_json(indent=2), encoding="utf-8"
            )
        if not guide_sections.ok:
            raise RuntimeError("; ".join(guide_sections.errors) or "Phase 2 guide-section mismatch")
        await log_cb(ctx.project_id, "[custom] Phase 2 guide-section gate passed", "info")

        return PhaseResult(
            phase_name=self.name,
            success=True,
            metadata={"analysis_policy": analysis_policy},
        )

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        coverage_report = ctx.staging_dir / "_analysis" / "_coverage_report.json"
        if coverage_report.exists():
            try:
                import json
                payload = json.loads(coverage_report.read_text(encoding="utf-8"))
                if payload.get("ok"):
                    await log_cb(ctx.project_id, "[custom] RESUME: Phase 2 analysis fully cached", "info")
                    return PhaseResult(phase_name=self.name, success=True, was_cached=True)
            except Exception:
                pass
        return await self.run(ctx, state, log_cb)

    def estimate(self, groups: list[str], docs: list[str], facts_unit_count: int) -> PhaseEstimate:
        n = len(groups)
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=n,
            calls_max=n * 4,
            notes="1 call per group (min), up to 4 with retries and repairs (max)",
        )

    async def _write_repair_plan(self, ctx: "DocGenContext", plan, name: str) -> None:
        repair_dir = ctx.staging_dir / "_assessment" / "repair"
        repair_dir.mkdir(parents=True, exist_ok=True)
        (repair_dir / f"{name}_repair_plan.json").write_text(
            plan.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _patch_coverage_markers(
        self,
        ctx: "DocGenContext",
        facts,
        missing_units: list[str],
        finder,
        patcher,
    ) -> int:
        analysis_dir = ctx.staging_dir / "_analysis"
        mentioned = finder(facts, analysis_dir, missing_units)
        patched = 0
        for group, units in mentioned.items():
            path = self._analysis_path(analysis_dir, group)
            if path is None:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            updated = patcher(text, units)
            if updated != text:
                path.write_text(updated, encoding="utf-8")
                self._helpers.write_phase2_coverage_record(ctx, group, units, path)
                patched += 1
        return patched

    def _patch_guide_sections(self, ctx: "DocGenContext", facts, patcher) -> int:
        analysis_dir = ctx.staging_dir / "_analysis"
        patched = 0
        for group in facts.groups:
            path = self._analysis_path(analysis_dir, group)
            if path is None:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            updated, missing = patcher(text, platform=facts.platform, group=group)
            if missing and updated != text:
                path.write_text(updated, encoding="utf-8")
                patched += 1
        return patched

    def _analysis_path(self, analysis_dir, group: str):
        direct = analysis_dir / f"{group}.md"
        if direct.exists():
            return direct
        safe = analysis_dir / f"{self._safe_name(group)}.md"
        return safe if safe.exists() else None

    @staticmethod
    def _safe_name(value: str) -> str:
        import re

        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "group"
