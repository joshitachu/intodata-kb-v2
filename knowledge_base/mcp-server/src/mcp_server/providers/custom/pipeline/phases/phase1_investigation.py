"""Phase 1: Investigation — extract structured evidence from scanner output."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult
from mcp_server.providers.custom.pipeline.warning_collector import WarningCode

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl

logger = logging.getLogger(__name__)


class Phase1Investigation(Phase):
    def __init__(self, helpers: "HarnessHelpersImpl") -> None:
        self._helpers = helpers

    @property
    def name(self) -> str:
        return "phase1_investigation"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return getattr(plan, "investigation_mode", "full") == "skip"

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        from mcp_server.config import config
        from mcp_server.providers.custom.investigator import (
            ChunkManifestInvestigator,
            InvestigationError,
            InvestigationReport,
            SingleShotInvestigator,
            investigation_summary,
            write_investigation_artifacts,
        )
        from mcp_server.providers.custom.evidence_bundles import CATEGORY_PATTERNS
        from mcp_server.providers.custom.models import ModelResult
        from mcp_server.providers.custom.harness import (
            FailureKind,
            _extract_chunk_identifiers,
        )
        from mcp_server.services.generation_run_state import record_manifest_chunks
        from mcp_server.providers.custom.validators import validate_investigation_coverage

        preprocess_dir = ctx.staging_dir / "_preprocess"
        investigation_dir = ctx.staging_dir / "_investigation"
        investigation_dir.mkdir(parents=True, exist_ok=True)
        shared_context = self._helpers.phase2_shared_context(ctx, ctx.staging_dir / "facts_manifest.json")
        inv_preamble: str | None = None
        if self._helpers.plugin_pack_dir:
            inv_override = self._helpers.plugin_pack_dir / "prompts" / "investigation.md"
            if inv_override.exists():
                inv_preamble = inv_override.read_text(encoding="utf-8").rstrip()
                await log_cb(
                    ctx.project_id,
                    "[custom] investigation: using pack prompt override from prompts/investigation.md",
                    "info",
                )
        investigation_provider = self._helpers.provider_for_phase("investigation")
        loop = SingleShotInvestigator(
            investigation_provider,
            ctx.staging_dir,
            state.facts_manifest,
            preamble=inv_preamble,
            pack_dir=self._helpers.plugin_pack_dir,
        )
        failed_groups: set[str] = set()
        sem = asyncio.Semaphore(self._helpers.concurrency_for_phase("investigation", config.agent_phase1_concurrency))

        async def _investigate_group(group: str) -> tuple[bool, list[InvestigationReport]]:
            """Process all evidence tasks for one group. Returns (missing_task_error, chunk_reports)."""
            group_units = self._helpers.group_units(state, group)
            tasks = self._helpers.plan_evidence_tasks(
                group, preprocess_dir / f"{group}.md", group_units, investigation_dir
            )
            chunks = [t.path for t in tasks]
            record_manifest_chunks(ctx.staging_dir, phase="investigation", group=group, chunks=chunks)
            chunk_reports: list[InvestigationReport] = []
            missing_chunk = False
            if not tasks:
                await log_cb(
                    ctx.project_id,
                    f"[custom] Investigation source group file missing for {group}",
                    "error",
                )
                return True, chunk_reports

            # Unit assignment comes from the task planner (job-boundary authoritative),
            # not from substring matching against the chunk text.
            all_assigned: set[str] = set()
            chunk_unit_map: dict[str, list[str]] = {}
            for task in tasks:
                if task.path.exists():
                    chunk_unit_map[task.path.stem] = sorted(task.all_units)
                    all_assigned.update(task.all_units)
            covered_units: set[str] = set()

            if config.agent_phase1_agentic:
                cached_agentic = self._helpers.load_investigation_artifact(investigation_dir, group)
                if cached_agentic is not None and ctx.resume_state is not None:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] RESUME: skipping agentic investigation for {group} "
                        f"({len(cached_agentic.evidence)} evidence items from prior run)",
                        "info",
                    )
                    return missing_chunk, [cached_agentic]

                async with sem:
                    manifest_entries: list[dict] = []
                    for cf in chunks:
                        if not cf.exists():
                            continue
                        try:
                            chunk_text = cf.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            chunk_text = ""
                        categories = [
                            cat for cat, pat in CATEGORY_PATTERNS.items()
                            if cat != "source_summary" and pat.search(chunk_text)
                        ]
                        identifiers = _extract_chunk_identifiers(chunk_text)
                        manifest_entries.append({
                            "id": cf.stem,
                            "size_bytes": cf.stat().st_size,
                            "unit_names": chunk_unit_map.get(cf.stem, []),
                            "categories": categories,
                            "identifiers": identifiers,
                        })

                    manifest_investigator = ChunkManifestInvestigator(
                        investigation_provider,
                        ctx.staging_dir,
                        state.facts_manifest,
                        preamble=inv_preamble,
                        pack_dir=self._helpers.plugin_pack_dir,
                    )
                    try:
                        report, calls = await manifest_investigator.run(
                            group,
                            chunks=chunks,
                            group_units=group_units,
                            manifest=manifest_entries,
                            group_file=preprocess_dir / f"{group}.md",
                            provider_options=self._helpers.provider_options(investigation_provider),
                        )
                    except InvestigationError as exc:
                        await log_cb(
                            ctx.project_id,
                            f"[custom] Agentic investigation failed for {group}: {exc}",
                            "warning",
                        )
                        return missing_chunk, chunk_reports
                    state.calls.extend(calls)
                    for call in calls:
                        await self._helpers.log_call_metrics(ctx, call.target, call.result, log_cb)
                    chunk_reports.append(report)
                    write_investigation_artifacts(report.model_copy(update={"group": group}), investigation_dir)
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Agentic investigation complete: {group}"
                        f" ({len(report.evidence)} evidence items, {len(calls)} call(s))",
                        "info",
                    )
                return missing_chunk, chunk_reports

            async with sem:
                for idx, task in enumerate(tasks, start=1):
                    chunk_file = task.path
                    if all_assigned and covered_units >= all_assigned:
                        skipped_count = len(tasks) - idx + 1
                        await log_cb(
                            ctx.project_id,
                            f"[custom] Investigation early stop for {group}: all {len(all_assigned)} units"
                            f" covered, skipping {skipped_count} remaining task(s)",
                            "info",
                        )
                        break

                    if not chunk_file.exists():
                        missing_chunk = True
                        await log_cb(
                            ctx.project_id,
                            f"[custom] Investigation task evidence missing for {group}: {chunk_file.relative_to(ctx.staging_dir).as_posix()}",
                            "error",
                        )
                        break
                    chunk_name = task.task_id
                    cached = self._helpers.load_investigation_artifact(investigation_dir, chunk_name)
                    if cached is not None and ctx.resume_state is not None:
                        chunk_reports.append(cached)
                        covered_units.update(ev.unit_name for ev in cached.evidence)
                        covered_units.update(cached.skipped_units.keys())
                        await log_cb(
                            ctx.project_id,
                            f"[custom] RESUME: skipping investigation for {chunk_name} "
                            f"({len(cached.evidence)} evidence items from prior run)",
                            "info",
                        )
                        continue
                    chunk_units = task.all_units
                    try:
                        chunk_text = chunk_file.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        chunk_text = ""
                    if not self._helpers.chunk_has_pack_relevant_content(chunk_text):
                        synthetic = InvestigationReport(
                            group=chunk_name,
                            source_groups=[chunk_name],
                            evidence=[],
                            skipped_units={
                                u: "no pack-relevant content in chunk (pre-screen)"
                                for u in chunk_units
                            },
                            tool_rounds=0,
                            invalid_retries=0,
                            input_tokens=0,
                            output_tokens=0,
                        )
                        chunk_reports.append(synthetic)
                        covered_units.update(synthetic.skipped_units.keys())
                        write_investigation_artifacts(
                            synthetic.model_copy(update={"group": chunk_name}),
                            investigation_dir,
                        )
                        await log_cb(
                            ctx.project_id,
                            f"[custom] Investigation pre-screened (no pack-relevant content): {chunk_name} "
                            f"({len(chunk_units)} units auto-skipped)",
                            "info",
                        )
                        continue
                    try:
                        report, calls = await loop.run(
                            chunk_name,
                            group_file=chunk_file,
                            context_files=[*shared_context, chunk_file],
                            allowed_units=chunk_units,
                            provider_options=self._helpers.provider_options(investigation_provider),
                        )
                    except InvestigationError as exc:
                        failure_kind = _classify_failure_from_error(str(exc))
                        if failure_kind == FailureKind.AUTH_ERROR:
                            raise RuntimeError(
                                f"Phase 1 investigation auth failed for {chunk_name}: {exc}"
                            ) from exc
                        await log_cb(
                            ctx.project_id,
                            f"[custom] Investigation failed for {chunk_name} (partial, continuing): {exc}",
                            "warning",
                        )
                        continue
                    state.calls.extend(calls)
                    for call in calls:
                        await self._helpers.log_call_metrics(ctx, call.target, call.result, log_cb)
                    chunk_reports.append(report)
                    covered_units.update(ev.unit_name for ev in report.evidence)
                    covered_units.update(report.skipped_units.keys())
                    write_investigation_artifacts(
                        report.model_copy(update={"group": chunk_name}),
                        investigation_dir,
                    )
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Investigation complete: {chunk_name} "
                        f"({len(report.evidence)} evidence items)",
                        "info",
                    )

            return missing_chunk, chunk_reports

        # Scoped reingestion: only investigate groups that Phase 2 will actually
        # regenerate. Unchanged groups had their analysis restored from cache by
        # incremental staging (present under _analysis/), so re-investigating them
        # is wasted LLM work — Phase 2 reuses the cached analysis and Phase 3 reads
        # it directly. The investigation-coverage check downstream is non-blocking.
        def _should_investigate(group: str) -> bool:
            if ctx.scoped_docs is None:
                return True
            analysis_path = ctx.staging_dir / "_analysis" / f"{group}.md"
            return not (analysis_path.exists() and analysis_path.stat().st_size > 500)

        groups_to_investigate = [g for g in ctx.preprocess_groups if _should_investigate(g)]
        for skipped in (g for g in ctx.preprocess_groups if g not in groups_to_investigate):
            await log_cb(
                ctx.project_id,
                f"[custom] REUSE: skipping Phase 1 investigation for {skipped} (unchanged group; cached analysis)",
                "info",
            )

        group_results = await asyncio.gather(
            *[_investigate_group(g) for g in groups_to_investigate]
        )

        for group, (missing_chunk, chunk_reports) in zip(groups_to_investigate, group_results):
            if missing_chunk:
                failed_groups.add(group)

            if not chunk_reports:
                failed_groups.add(group)
                await log_cb(
                    ctx.project_id,
                    f"[custom] Investigation failed for all chunks of {group}, skipping group",
                    "error",
                )
                continue

            merged = self._helpers.merge_investigation_reports(group, chunk_reports)
            write_investigation_artifacts(merged, investigation_dir)
            state.investigation_reports.append(merged)

        for group in sorted(failed_groups):
            await state.warning_collector.emit(
                "phase1",
                WarningCode.PHASE1_GROUP_FAILURE,
                f"Investigation failed for group {group}",
                {"group": group},
            )

        if groups_to_investigate and len(failed_groups) >= len(groups_to_investigate):
            return PhaseResult(
                phase_name=self.name,
                success=False,
                error="Investigation failed for all groups — cannot generate any docs",
                metadata={"failed_groups": sorted(failed_groups)},
            )

        (investigation_dir / "_summary.md").write_text(
            "# Investigation Summary\n\n"
            f"{investigation_summary(state.investigation_reports)}",
            encoding="utf-8",
        )
        return PhaseResult(
            phase_name=self.name,
            success=True,
            metadata={"failed_groups": sorted(failed_groups)},
        )

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        from mcp_server.providers.custom.investigator import InvestigationReport, write_investigation_artifacts

        investigation_dir = ctx.staging_dir / "_investigation"
        if not investigation_dir.exists():
            return await self.run(ctx, state, log_cb)

        # Check if all groups have complete investigation artifacts
        all_complete = True
        for group in ctx.preprocess_groups:
            import re
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", group)
            md_path = investigation_dir / f"{safe}.md"
            json_path = investigation_dir / f"{safe}.json"
            if not md_path.exists() or not json_path.exists() or md_path.stat().st_size == 0:
                all_complete = False
                break
            try:
                report = InvestigationReport.model_validate_json(json_path.read_text(encoding="utf-8"))
                state.investigation_reports.append(report)
            except Exception:
                all_complete = False
                state.investigation_reports.clear()
                break

        if all_complete and state.investigation_reports:
            await log_cb(ctx.project_id, "[custom] RESUME: Phase 1 investigation fully cached", "info")
            return PhaseResult(phase_name=self.name, success=True, was_cached=True, metadata={"failed_groups": []})

        state.investigation_reports.clear()
        return await self.run(ctx, state, log_cb)

    def estimate(self, groups: list[str], docs: list[str], facts_unit_count: int) -> PhaseEstimate:
        n = len(groups)
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=n,
            calls_max=n * 3,
            notes="1 call per group (min), up to 3 with retries (max)",
        )


def _classify_failure_from_error(error_str: str) -> object:
    """Minimal helper to classify an InvestigationError without a full ModelResult."""
    from mcp_server.providers.custom.models import ModelResult
    from mcp_server.providers.custom.harness import CustomHarnessProvider
    mr = ModelResult(success=False, model_name="", raw_error=error_str)
    return CustomHarnessProvider._classify_failure(mr)
