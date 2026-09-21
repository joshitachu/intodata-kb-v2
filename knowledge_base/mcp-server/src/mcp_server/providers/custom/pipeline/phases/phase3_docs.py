"""Phase 3: Docs — generate the required markdown documentation files."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mcp_server.providers.custom.debug_artifacts import debug_dir, debug_write
from mcp_server.providers.custom.pipeline.phase import Phase, PhaseEstimate, PhaseResult

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl


class Phase3Docs(Phase):
    def __init__(
        self,
        helpers: "HarnessHelpersImpl",
        execution_plan: "ExecutionPlan | None" = None,
    ) -> None:
        self._helpers = helpers
        self._execution_plan = execution_plan

    @property
    def name(self) -> str:
        return "phase3_docs"

    def should_skip(self, plan: "ExecutionPlan") -> bool:
        return False

    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        execution_plan = self._execution_plan
        from mcp_server.providers.custom.validators import (
            validate_docs,
            validate_doc_depth,
            validate_source_provenance,
        )
        from mcp_server.providers.custom.models import CoverageReport

        # Deterministic evidence tier: fetch observed production behaviour once
        # and stage the rollup + cache + coverage record BEFORE any doc writer
        # asks for its context files. Shared with the OpenCode engine through
        # the facade so the two cannot diverge again. Never a gate -- absence is
        # a stated mode, not an exception.
        await self._helpers.ensure_runtime_evidence(ctx, state, log_cb)

        await self._helpers.phase3_docs(ctx, state, log_cb)

        if state.thin_bundle_groups:
            notice = _thin_bundle_notice(state.thin_bundle_groups)
            for doc_name in self._helpers.required_docs_list:
                doc_path = ctx.staging_dir / f"{doc_name}.md"
                if doc_path.exists():
                    original = doc_path.read_text(encoding="utf-8")
                    doc_path.write_text(notice + "\n\n" + original, encoding="utf-8")

        # Traceability check: flag docs that claim a unit's evidence is absent
        # when Phase 1 investigation actually found evidence for that unit.
        if state.investigation_reports:
            from mcp_server.providers.custom.evidence_traceability import EvidenceTraceabilityValidator
            _tracer = EvidenceTraceabilityValidator(
                investigation_reports=state.investigation_reports,
                facts=state.facts_manifest,
                investigation_dir=ctx.staging_dir / "_investigation",
            )
            for doc_name in self._helpers.required_docs_list:
                doc_path = ctx.staging_dir / f"{doc_name}.md"
                if doc_path.exists():
                    doc_text = doc_path.read_text(encoding="utf-8", errors="replace")
                    trace_report = _tracer.validate(doc_name, doc_text)
                    if trace_report.phantom_absences:
                        await log_cb(
                            ctx.project_id,
                            f"[custom] traceability: {len(trace_report.phantom_absences)} phantom absence(s) in {doc_name}: "
                            + ", ".join(c.unit_name for c in trace_report.phantom_absences[:5]),
                            "warning",
                        )
                        debug_write(
                            debug_dir(ctx.staging_dir, "_assessment", f"traceability_{doc_name}.json"),
                            json.dumps(
                                {
                                    "doc_name": doc_name,
                                    "phantom_absences": [
                                        {
                                            "unit_name": c.unit_name,
                                            "matched_phrase": c.matched_phrase,
                                            "evidence_path": c.evidence_path,
                                        }
                                        for c in trace_report.phantom_absences
                                    ],
                                },
                                indent=2,
                            ),
                        )

        reconciled = self._helpers.reconcile_sql_usage_classification(ctx)
        if reconciled:
            await log_cb(
                ctx.project_id,
                f"[custom] SQL usage reconciliation updated {reconciled} DDL-only table classification(s)",
                "info",
            )

        if execution_plan is not None:
            for doc in self._helpers.required_docs_list:
                self._helpers.record_dag_node_result(
                    ctx,
                    f"doc_write:{_safe_name(doc)}",
                    "passed" if (ctx.staging_dir / f"{doc}.md").exists() else "failed",
                    artifacts=[f"{doc}.md"],
                    validation="doc_markdown",
                )

        if execution_plan is not None and execution_plan.consistency:
            await self._helpers.phase36_consistency(ctx, state, log_cb)

        facts = state.facts_manifest
        if self._helpers.plugin_mode:
            await log_cb(ctx.project_id, "[custom] Phase 3 required docs source: plugin manifest", "info")

        doc_report = validate_docs(
            ctx.staging_dir,
            facts,
            required_docs=self._helpers.required_docs_list,
            enforce_default_contract=not self._helpers.plugin_mode,
        )
        (ctx.staging_dir / "_assessment" / "doc_validation_report.json").write_text(
            doc_report.model_dump_json(indent=2), encoding="utf-8"
        )
        if not doc_report.ok:
            summary = f"{len(doc_report.errors)} obligation gap(s): " + "; ".join(doc_report.errors[:3])
            await log_cb(ctx.project_id, f"[custom] Phase 3 doc validation: {summary}", "warning")
        self._helpers.reconcile_sql_usage_classification(ctx)
        sql_gate_errors = self._helpers.validate_sql_usage_publication(ctx)
        if sql_gate_errors:
            summary = f"{len(sql_gate_errors)} SQL usage gate finding(s): " + "; ".join(sql_gate_errors[:3])
            await log_cb(ctx.project_id, f"[custom] Phase 3 SQL usage gate: {summary}", "warning")
        elif doc_report.ok:
            await log_cb(ctx.project_id, "[custom] Phase 3 factual gates passed", "info")

        if execution_plan is not None:
            self._helpers.record_dag_node_result(
                ctx,
                "validate_docs",
                "passed" if doc_report.ok and not sql_gate_errors else "advisory",
                artifacts=["_assessment/doc_validation_report.json"],
                validation="validate_docs",
            )

        doc_depth = validate_doc_depth(
            ctx.staging_dir,
            facts,
            required_docs=self._helpers.required_docs_list,
            enforce_default_contract=not self._helpers.plugin_mode,
        )
        (ctx.staging_dir / "_assessment" / "doc_depth_report.json").write_text(
            doc_depth.model_dump_json(indent=2), encoding="utf-8"
        )
        if not doc_depth.ok:
            summary = f"{len(doc_depth.errors)} depth gap(s): " + "; ".join(doc_depth.errors[:3])
            await log_cb(ctx.project_id, f"[custom] Phase 3 doc depth: {summary}", "warning")
        else:
            await log_cb(ctx.project_id, "[custom] Phase 3 depth gate passed", "info")

        # ASCII-only publication gate. Generated Markdown is read on Windows
        # (cp1252) and must stay ASCII -- see knowledge_base/CLAUDE.md rule 4.
        # Run 258 shipped CJK inside client prose ("BTW number<CJK>") and 144
        # em-dashes in ASCII diagrams. Advisory like every other Phase 3 final
        # check: report file, line and codepoint; the operator decides.
        ascii_report = _scan_non_ascii(ctx.staging_dir)
        (ctx.staging_dir / "_assessment" / "non_ascii_report.json").write_text(
            json.dumps(ascii_report, indent=2, ensure_ascii=True), encoding="utf-8"
        )
        if not ascii_report["ok"]:
            summary = (
                f"{ascii_report['finding_count']} non-ASCII occurrence(s) in "
                f"{len(ascii_report['files'])} doc(s): "
                + "; ".join(_format_ascii_finding(f) for f in ascii_report["findings"][:3])
            )
            await log_cb(ctx.project_id, f"[custom] Phase 3 ASCII gate: {summary}", "warning")
        else:
            await log_cb(ctx.project_id, "[custom] Phase 3 ASCII gate passed", "info")

        if execution_plan is not None:
            self._helpers.record_dag_node_result(
                ctx,
                "validate_ascii",
                "passed" if ascii_report["ok"] else "advisory",
                artifacts=["_assessment/non_ascii_report.json"],
                validation="validate_ascii",
            )

        provenance: CoverageReport | None = None
        if ctx.enterprise_mode and self._helpers.pack_contract_mode:
            provenance = CoverageReport(
                ok=True,
                coverage_count=facts.unit_count,
                expected_count=facts.unit_count,
                warnings=["Default source provenance gate skipped for plugin-pack contract run."],
            )
            (ctx.staging_dir / "_assessment" / "source_provenance_report.json").write_text(
                provenance.model_dump_json(indent=2), encoding="utf-8"
            )
            await log_cb(
                ctx.project_id,
                "[custom] pack contract path: skipping default source provenance repair",
                "info",
            )
        elif ctx.enterprise_mode:
            provenance = validate_source_provenance(
                ctx.staging_dir,
                facts,
                required_docs=self._helpers.required_docs_list,
                enforce_default_contract=not self._helpers.plugin_mode,
            )
            (ctx.staging_dir / "_assessment" / "source_provenance_report.json").write_text(
                provenance.model_dump_json(indent=2), encoding="utf-8"
            )
            for w in provenance.warnings[:5]:
                await log_cb(ctx.project_id, f"[custom] Phase 3 provenance warning: {w}", "warning")
            if not provenance.ok or provenance.coverage_count != facts.unit_count:
                summary = f"{len(provenance.errors)} provenance gap(s): " + "; ".join(provenance.errors[:3])
                await log_cb(ctx.project_id, f"[custom] Phase 3 provenance: {summary}", "warning")
            else:
                await log_cb(ctx.project_id, "[custom] Phase 3 provenance gate passed", "info")

        gate_report = self._helpers.enterprise_gate_report(
            ctx, state,
            provenance=(provenance if ctx.enterprise_mode else None),
        )
        if ctx.enterprise_mode:
            await self._helpers.write_audit_bundle(ctx, state, gate_report)
            failed = [
                f"{name}: {entry.get('remediation') or '; '.join(entry.get('errors') or [])}"
                for name, entry in gate_report["gates"].items()
                if not entry.get("ok")
            ]
            if failed:
                summary = f"{len(failed)} enterprise gate finding(s): " + " | ".join(failed[:5])
                await log_cb(ctx.project_id, f"[custom] Phase 3 enterprise gates: {summary}", "warning")
                if execution_plan is not None:
                    self._helpers.record_dag_node_result(
                        ctx,
                        "enterprise_gates",
                        "advisory",
                        artifacts=["_assessment/enterprise_gates.json"],
                        validation="enterprise_gates",
                        error=" | ".join(failed[:5]),
                    )
            if execution_plan is not None and not failed:
                self._helpers.record_dag_node_result(
                    ctx,
                    "enterprise_gates",
                    "passed",
                    artifacts=["_assessment/enterprise_gates.json"],
                    validation="enterprise_gates",
                )
            if not failed:
                await log_cb(ctx.project_id, "[custom] Enterprise publish gates passed", "info")
        else:
            state.warnings.append("Draft mode: enterprise publish gates are advisory only")
            if execution_plan is not None:
                self._helpers.record_dag_node_result(
                    ctx,
                    "enterprise_gates",
                    "skipped",
                    artifacts=["_assessment/enterprise_gates.json"],
                    validation="draft_mode",
                )

        return PhaseResult(
            phase_name=self.name,
            success=True,
            metadata={"docs": len(self._helpers.required_docs_list)},
        )

    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        # Per-doc resume is handled internally via AgentTaskRuntime.accepted_output()
        return await self.run(ctx, state, log_cb)

    def estimate(self, groups: list[str], docs: list[str], facts_unit_count: int) -> PhaseEstimate:
        n = len(docs)
        return PhaseEstimate(
            phase_name=self.name,
            calls_min=n,
            calls_max=n * 3,
            notes="1 call per doc (min), up to 3 with retries and repairs (max)",
        )


def _thin_bundle_notice(thin_groups: list[str]) -> str:
    groups_str = ", ".join(sorted(thin_groups))
    return (
        "> **Evidence Notice:** Analysis evidence for the following scanner groups was "
        f"thin or absent: {groups_str}. "
        "Sections that depend on these groups may contain less detail than expected. "
        "Re-scan the project or inspect the scanner output to improve coverage."
    )


#: Cap the per-file finding list so one badly-encoded document cannot bloat the
#: report. The census counts every occurrence regardless.
_ASCII_FINDINGS_PER_FILE = 25


def _scan_non_ascii(staging_dir: "Path") -> dict:
    """Locate non-ASCII codepoints in the documents this run would publish.

    Only top-level ``*.md`` files are scanned: those are exactly what the atomic
    swap moves into ``docs/`` and what ``RagBridge`` pushes. Internal artifacts
    under ``_analysis/``, ``_assessment/`` and friends are never published and
    are legitimately free to carry whatever the model wrote.
    """
    findings: list[dict] = []
    census: dict[str, int] = {}
    files: list[str] = []
    for path in sorted(staging_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        file_hits = 0
        reported_for_file = 0
        for lineno, line in enumerate(text.splitlines(), start=1):
            offenders = [(col, ch) for col, ch in enumerate(line, start=1) if ord(ch) > 127]
            if not offenders:
                continue
            file_hits += len(offenders)
            for _col, ch in offenders:
                key = f"U+{ord(ch):04X}"
                census[key] = census.get(key, 0) + 1
            if reported_for_file < _ASCII_FINDINGS_PER_FILE:
                reported_for_file += 1
                col, _ch = offenders[0]
                findings.append(
                    {
                        "file": path.name,
                        "line": lineno,
                        "column": col,
                        "codepoints": sorted({f"U+{ord(ch):04X}" for _c, ch in offenders}),
                        "names": sorted({_codepoint_name(ch) for _c, ch in offenders}),
                        "excerpt": _ascii_excerpt(line),
                    }
                )
        if file_hits:
            files.append(path.name)
    return {
        "ok": not findings,
        "finding_count": sum(census.values()),
        "files": files,
        "codepoint_census": dict(sorted(census.items(), key=lambda kv: (-kv[1], kv[0]))),
        "findings": findings,
    }


def _codepoint_name(ch: str) -> str:
    import unicodedata
    return unicodedata.name(ch, f"UNNAMED U+{ord(ch):04X}")


def _ascii_excerpt(line: str, limit: int = 120) -> str:
    """ASCII-safe excerpt: non-ASCII characters are shown as escapes."""
    trimmed = line.strip()[:limit]
    return "".join(ch if ord(ch) <= 127 else f"\\u{ord(ch):04x}" for ch in trimmed)


def _format_ascii_finding(finding: dict) -> str:
    return (
        f"{finding['file']}:{finding['line']} "
        f"{','.join(finding['codepoints'])} ({finding['excerpt'][:60]})"
    )


def _safe_name(value: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
