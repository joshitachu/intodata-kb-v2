"""Custom async documentation-generation harness.

Python owns phase sequencing, coverage validation, artifact checks, and cleanup.
The model is only asked to write bounded markdown sections from supplied files.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from enum import Enum
from pathlib import Path

from mcp_server.config import config
from mcp_server.providers.base import AgentResult, DocGenContext, DocGenerationProvider, LogCallback
from mcp_server.services.pack_loader import InstructionPack
from mcp_server.providers.custom.investigator import (
    InvestigationError,
    InvestigatorLimits,
    SingleShotInvestigator,
    investigation_summary,
    write_investigation_artifacts,
)
from mcp_server.providers.custom.engineering_facts import EngineeringFactsService
from mcp_server.providers.custom.agent_runtime import (
    AgentTaskContract,
    AgentTaskRuntime,
    ScopedEvidenceTools,
    default_input_budget,
    default_tool_budget,
)
from mcp_server.providers.custom.models import (
    HarnessRunState,
    InvestigationReport,
    ModelOptions,
    ModelResult,
    ProviderCallLog,
)
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES, materialize_prompt_packs
from mcp_server.providers.custom.prompts import (
    DOC_ASSIGNMENTS,
    DOC_BUNDLE_MAP,
    REQUIRED_DOCS,
    doc_prompt,
    group_analysis_prompt,
    phase2_section_repair_prompt,
    scorecard_prompt,
)
from mcp_server.providers.custom.providers import ModelProvider, build_model_provider
from mcp_server.providers.custom.validators import (
    build_facts_manifest,
    group_analysis_is_complete,
    missing_required_analysis_sections,
    normalize_analysis_guide_headings,
    validate_analysis_guide_sections,
    validate_investigation_coverage,
    validate_analysis_depth,
    validate_analysis_coverage,
    validate_doc_depth,
    validate_docs,
    build_documentation_obligation_map,
    _scanned_config_files,
    _covered_source_files,
    _strip_config_contradiction_sentences,
)
from mcp_server.services.model_pricing import calculate_total_cost


LARGE_CONTEXT_ARTIFACTS = {
    "engineering_facts.json",
    "entity_index.json",
    "usage_index.json",
    "dependency_map.json",
    "architecture_map.json",
    "data_flow_map.json",
    "api_surface.json",
    "config_runtime_map.json",
    "known_risks.json",
    "test_coverage_map.json",
    "change_impact_matrix.json",
}

GROUP_SUMMARY_SECTION = "GROUP_SUMMARY"
ADDITIONAL_FINDINGS_SECTION = "UNCLASSIFIED_OR_EXTRA_FINDINGS"
PHASE2_SECTION_NAMES = tuple(ANALYSIS_SECTION_NAMES) + (GROUP_SUMMARY_SECTION, ADDITIONAL_FINDINGS_SECTION,)
BASELINE_BUNDLE_SECTIONS = {
    GROUP_SUMMARY_SECTION,
    "COMPONENTS",
    "EXECUTION_FLOW",
    "CROSS_GROUP_REFS",
    "RISK_FLAGS",
    ADDITIONAL_FINDINGS_SECTION,
}
DOC_SECTION_BUNDLE_MAP = {
    "00_AGENT_BRIEF": {"EXTERNAL_SYSTEMS", "ENVIRONMENT_CONFIG", "NAMING_PATTERNS", "RISK_FLAGS"},
    "01_ARCHITECTURE_MAP": {"EXECUTION_FLOW", "EXTERNAL_SYSTEMS", "SCHEMA_DICTIONARY", "CROSS_GROUP_REFS"},
    "02_COMPONENT_INVENTORY": {"COMPONENTS", "EXECUTION_FLOW", "EXTERNAL_SYSTEMS", "SCHEMA_DICTIONARY"},
    "03_DATA_FLOWS_AND_LINEAGE": {"DATA_FLOWS", "TRANSFORMATION_DETAIL", "SOURCE_SQL", "SCHEMA_DICTIONARY", "EXTERNAL_SYSTEMS"},
    "04_BUSINESS_RULES_AND_TRANSFORMATIONS": {"BUSINESS_RULES", "TRANSFORMATION_DETAIL", "SOURCE_SQL", "CUSTOM_CODE"},
    "05_RUNTIME_CONFIG_AND_EXTERNALS": {"EXTERNAL_SYSTEMS", "ENVIRONMENT_CONFIG", "SCHEDULING"},
    "06_OPERATIONS_TESTING_AND_DEBUGGING": {"SCHEDULING", "ERROR_HANDLING", "ENVIRONMENT_CONFIG", "EXTERNAL_SYSTEMS", "RISK_FLAGS"},
    "07_QUALITY_SECURITY_RISK_SCORECARD": {"ERROR_HANDLING", "EXTERNAL_SYSTEMS", "ENVIRONMENT_CONFIG", "SOURCE_SQL", "CUSTOM_CODE", "RISK_FLAGS"},
    "08_CHANGE_PLAYBOOK": {"BUSINESS_RULES", "TRANSFORMATION_DETAIL", "SCHEDULING", "ENVIRONMENT_CONFIG", "CROSS_GROUP_REFS", "RISK_FLAGS"},
}


class FailureKind(str, Enum):
    CONTEXT_TOO_LARGE = "context_too_large"
    EMPTY_OUTPUT = "empty_output"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    AUTH_ERROR = "auth_error"
    UNKNOWN = "unknown"
    BUDGET_EXCEEDED = "budget_exceeded"

    @property
    def is_retryable_with_fallback(self) -> bool:
        return self not in {FailureKind.CONTEXT_TOO_LARGE, FailureKind.BUDGET_EXCEEDED}


def estimate_input_tokens(prompt: str, context_files: list[Path] | None = None) -> int:
    """Cheap, stable estimate used for routing before model calls."""

    total_chars = len(prompt)
    for path in context_files or []:
        try:
            total_chars += path.stat().st_size
        except OSError:
            continue
        total_chars += len(path.name) + 32
    return max(1, total_chars // 4)


def context_bytes(context_files: list[Path] | None = None) -> int:
    total = 0
    for path in context_files or []:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def use_tools_for_core_generation(provider: ModelProvider) -> bool:
    """Use bounded native tool loops for providers that advertise tool support."""

    return bool(getattr(provider, "supports_tools", False))


def _with_tool_instructions(prompt: str, staging_dir: Path) -> str:
    """Prepend tool-use instructions to any existing prompt."""
    preamble = (
        "You have scoped tools available to read context and evidence:\n"
        "  list_artifacts(subdir=None) — list files in staging"
        " (pass '_preprocess', '_analysis', or '_assessment' to scope)\n"
        "  read_artifact(path) — read a file by path relative to staging root\n"
        "  search_evidence(query, scope=None, categories=None, limit=20) — search scoped evidence\n"
        "  get_unit_evidence(unit_name, categories=None, limit=20) — fetch scoped unit evidence\n"
        "  get_group_evidence(group_name, categories=None, limit=40) — fetch scoped group evidence\n"
        "  get_fact_table(table_name, filters=None, limit=50) — read structured fact tables\n"
        "\n"
        "Start by calling list_artifacts('_preprocess') to see what is available.\n"
        "Read the files you need to complete your task.\n"
        "Do NOT expect any files to be pre-loaded — fetch everything you need via tools.\n"
        "When you have finished reading and have produced your complete response, stop.\n\n"
    )
    return preamble + prompt


def _with_agent_contract(prompt: str, contract: AgentTaskContract | None) -> str:
    if contract is None:
        return prompt
    parts = [prompt]
    if contract.starter_context_files:
        parts.append(
            "Starter file hints. Read only what you need with tools:\n"
            + "\n".join(f"- {path}" for path in contract.starter_context_files)
        )
    if contract.allowed_units:
        parts.append("Allowed canonical units:\n" + "\n".join(f"- {unit}" for unit in contract.allowed_units))
    if contract.allowed_groups:
        parts.append("Allowed groups:\n" + "\n".join(f"- {group}" for group in contract.allowed_groups))
    if contract.allowed_categories:
        parts.append("Allowed evidence categories:\n" + "\n".join(f"- {cat}" for cat in contract.allowed_categories))
    if contract.validation_gaps:
        parts.append(
            "Previous attempt validation gaps to repair directly:\n"
            + "\n".join(f"- {gap}" for gap in contract.validation_gaps)
        )
    parts.append(f"Expected artifact: {contract.expected_artifact}. Attempt {contract.attempt}/{contract.max_attempts}.")
    return "\n\n".join(parts)


class CustomHarnessProvider(DocGenerationProvider):
    """Provider-agnostic Python harness with hard factual gates."""

    def __init__(
        self,
        model_provider: ModelProvider,
        write_provider: ModelProvider | None = None,
        investigation_provider: ModelProvider | None = None,
        fallback_provider: ModelProvider | None = None,
        pack: "InstructionPack | None" = None,
        plugin_pack_dir: "Path | None" = None,
    ) -> None:
        self.model_provider = model_provider
        self.write_provider = write_provider or model_provider
        self.investigation_provider = investigation_provider or model_provider
        self.fallback_provider = fallback_provider
        self.pack = pack
        self.plugin_pack_dir = plugin_pack_dir

        self._required_docs: list[str] = list(REQUIRED_DOCS)
        self._doc_assignments: dict[str, list[str]] = dict(DOC_ASSIGNMENTS)
        self._doc_bundle_map: dict[str, str] = dict(DOC_BUNDLE_MAP)
        self._doc_section_requirements_extra: dict[str, list[str]] = {}

        if plugin_pack_dir:
            from mcp_server.services.plugin_schema import load_plugin_manifest
            try:
                _manifest = load_plugin_manifest(plugin_pack_dir)
                if _manifest.docs:
                    self._required_docs = [d.name for d in _manifest.docs]
                    self._doc_assignments = {"custom": list(self._required_docs)}
                    self._doc_bundle_map = {d: "custom" for d in self._required_docs}
                    self._doc_section_requirements_extra = {
                        d.name: d.sections for d in _manifest.docs if d.sections
                    }
            except Exception:
                pass

    @staticmethod
    def _describe_provider(provider: ModelProvider) -> str:
        provider_name = getattr(provider, "provider_name", provider.__class__.__name__.lower())
        base_url = getattr(provider, "base_url", None)
        if base_url:
            return f"{provider_name}/{provider.model_name} @ {base_url}"
        return f"{provider_name}/{provider.model_name}"

    @staticmethod
    def _classify_failure(result: ModelResult) -> FailureKind | None:
        """Normalize provider-specific failures into harness retry decisions."""

        if result.success and result.text.strip():
            return None

        raw_error = result.raw_error or ""
        metadata_text = json.dumps(result.metadata, sort_keys=True, default=str) if result.metadata else ""
        evidence = f"{raw_error}\n{metadata_text}".lower()

        def has_status(*codes: int) -> bool:
            return any(re.search(rf"\b{code}\b", evidence) for code in codes)

        if has_status(401, 403) or re.search(
            r"\b(unauthorized|forbidden|invalid api key|auth(?:entication)? failed|permission denied|"
            r"insufficient permissions|payment required|billing)\b",
            evidence,
        ):
            return FailureKind.AUTH_ERROR

        if has_status(429) or re.search(
            r"\b(rate[_ -]?limit|too many requests|throttle|throttled|quota|capacity|"
            r"requests per minute|tokens per minute)\b",
            evidence,
        ):
            return FailureKind.RATE_LIMIT

        if has_status(413) or re.search(
            r"(context[_ -]?length[_ -]?exceeded|context length|maximum context|max context|"
            r"input (?:is )?too long|prompt too long|too many tokens|token limit|"
            r"maximum request length|request too large|payload too large|max prompt|"
            r"exceeds[^.\n]*(?:context|token|model))",
            evidence,
        ):
            return FailureKind.CONTEXT_TOO_LARGE

        if has_status(419):
            return FailureKind.UNKNOWN

        if has_status(408, 502, 503, 504) or re.search(
            r"\b(timeout|connection reset|connection refused|dns|socket|gateway timeout|"
            r"service unavailable|bad gateway|upstream error)\b",
            evidence,
        ):
            return FailureKind.NETWORK

        if result.success and (not result.text.strip() or result.output_tokens == 0):
            return FailureKind.EMPTY_OUTPUT

        return FailureKind.UNKNOWN

    @staticmethod
    def _failure_result(provider: ModelProvider, phase: str, target: str, message: str, kind: FailureKind) -> ModelResult:
        return ModelResult(
            success=False,
            model_name=getattr(provider, "model_name", ""),
            raw_error=message,
            metadata={"phase": phase, "target": target, "failure_kind": kind.value},
        )

    def _run_model_call_budget(self, state: HarnessRunState) -> int:
        group_count = max(1, len(state.facts_manifest.groups))
        required_doc_count = max(1, len(self._required_docs))
        allowed_slices = max(1, config.docgen_allowed_slices_per_doc)
        dynamic_budget = (
            config.docgen_call_budget_base
            + group_count * config.docgen_call_budget_per_group
            + required_doc_count * allowed_slices
        )
        return max(config.docgen_min_run_call_budget, dynamic_budget)

    def _doc_budget_key(self, target: str) -> str:
        for doc_name in self._required_docs:
            if target == doc_name or target.startswith(f"{doc_name}_"):
                return doc_name
        return target.split("_slice_", 1)[0].split("_retry_", 1)[0]

    async def _record_failure_policy(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        *,
        target: str,
        result: ModelResult,
        kind: FailureKind,
        log_cb: LogCallback,
    ) -> ModelResult:
        result.metadata["failure_kind"] = kind.value

        if kind in {FailureKind.EMPTY_OUTPUT, FailureKind.RATE_LIMIT, FailureKind.NETWORK} and config.docgen_adaptive_concurrency:
            state.adaptive_serial_generation = True

        if kind != FailureKind.EMPTY_OUTPUT:
            return result

        key = self._doc_budget_key(target)
        state.empty_output_counts[key] = state.empty_output_counts.get(key, 0) + 1
        state.total_empty_outputs += 1
        await log_cb(
            ctx.project_id,
            "[custom] empty output guard: "
            f"target={target} doc={key} doc_empty_outputs={state.empty_output_counts[key]}/"
            f"{config.docgen_empty_outputs_per_doc_limit} run_empty_outputs={state.total_empty_outputs}/"
            f"{config.docgen_empty_outputs_per_run_limit}",
            "warning",
        )

        if state.total_empty_outputs >= config.docgen_empty_outputs_per_run_limit:
            return result.model_copy(
                update={
                    "success": False,
                    "raw_error": (
                        "empty output run limit exceeded: "
                        f"{state.total_empty_outputs}/{config.docgen_empty_outputs_per_run_limit}; "
                        f"last target={target}"
                    ),
                }
            )

        if state.empty_output_counts[key] >= config.docgen_empty_outputs_per_doc_limit:
            return result.model_copy(
                update={
                    "success": False,
                    "raw_error": (
                        "empty output doc limit exceeded: "
                        f"{key} {state.empty_output_counts[key]}/"
                        f"{config.docgen_empty_outputs_per_doc_limit}"
                    ),
                }
            )

        return result

    async def run(self, ctx: DocGenContext, log_cb: LogCallback) -> AgentResult:
        start_ms = int(time.time() * 1000)
        scan_output_dir = config.intodoc_output_dir / ctx.project_id
        facts = build_facts_manifest(ctx.project_id, scan_output_dir)
        state = HarnessRunState(project_id=ctx.project_id, facts_manifest=facts)
        facts_path = ctx.staging_dir / "facts_manifest.json"
        facts.write(facts_path)
        _, engineering_artifacts = EngineeringFactsService().extract_and_write(
            project_id=ctx.project_id,
            scan_output_dir=scan_output_dir,
            facts_manifest=facts,
            staging_dir=ctx.staging_dir,
        )
        state.engineering_fact_artifacts = [
            path.name for path in engineering_artifacts if path.parent == ctx.staging_dir
        ]
        materialize_prompt_packs(ctx.staging_dir, ctx.analysis_guide_file)

        await log_cb(
            ctx.project_id,
            "[custom] resolved providers: "
            f"phase1={self._describe_provider(self.investigation_provider)} "
            f"phase2={self._describe_provider(self.model_provider)} "
            f"phase3={self._describe_provider(self.write_provider)}",
            "info",
        )
        await log_cb(
            ctx.project_id,
            f"[custom] facts manifest: {facts.unit_count} {facts.canonical_unit_label}, "
            f"{len(facts.groups)} groups",
            "info",
        )
        await log_cb(
            ctx.project_id,
            f"[custom] engineering facts artifacts: {len(state.engineering_fact_artifacts)} files",
            "info",
        )

        try:
            failed_groups = await self._phase1_investigation(ctx, state, log_cb)
            investigation_dir = ctx.staging_dir / "_investigation"
            investigation_dir.mkdir(parents=True, exist_ok=True)
            if failed_groups:
                ctx.preprocess_groups = [g for g in ctx.preprocess_groups if g not in failed_groups]
                await log_cb(
                    ctx.project_id,
                    f"[custom] WARNING: {len(failed_groups)} group(s) skipped due to investigation failure: "
                    f"{', '.join(sorted(failed_groups))}",
                    "warning",
                )
                if not ctx.preprocess_groups:
                    raise RuntimeError("Investigation failed for all groups — cannot generate any docs")
            investigation_coverage = validate_investigation_coverage(state.investigation_reports, facts)
            (investigation_dir / "_coverage_report.json").write_text(
                investigation_coverage.model_dump_json(indent=2), encoding="utf-8"
            )
            if not investigation_coverage.ok or investigation_coverage.coverage_count != facts.unit_count:
                await log_cb(
                    ctx.project_id,
                    "[custom] WARNING: investigation coverage incomplete — proceeding to Phase 2: "
                    + ("; ".join(investigation_coverage.errors) or "partial coverage"),
                    "warning",
                )
            else:
                await log_cb(ctx.project_id, "[custom] Investigation coverage gate passed", "info")

            await self._phase2_analysis(ctx, state, log_cb)
            coverage = validate_analysis_coverage(ctx.staging_dir / "_analysis", facts)
            (ctx.staging_dir / "_analysis" / "_coverage_report.json").write_text(
                coverage.model_dump_json(indent=2), encoding="utf-8"
            )
            if not coverage.ok or coverage.coverage_count != facts.unit_count:
                await self._phase2_targeted_repair(ctx, state, coverage, log_cb)
                coverage = validate_analysis_coverage(ctx.staging_dir / "_analysis", facts)
                (ctx.staging_dir / "_analysis" / "_coverage_report.json").write_text(
                    coverage.model_dump_json(indent=2), encoding="utf-8"
                )
            if not coverage.ok or coverage.coverage_count != facts.unit_count:
                raise RuntimeError("; ".join(coverage.errors) or "Phase 2 coverage mismatch")
            await log_cb(ctx.project_id, "[custom] Phase 2 coverage gate passed", "info")

            depth = validate_analysis_depth(ctx.staging_dir / "_analysis", ctx.staging_dir / "_preprocess", facts)
            (ctx.staging_dir / "_analysis" / "_depth_report.json").write_text(
                depth.model_dump_json(indent=2), encoding="utf-8"
            )
            if not depth.ok or depth.coverage_count != facts.unit_count:
                await self._phase2_targeted_repair(ctx, state, depth, log_cb)
                depth = validate_analysis_depth(ctx.staging_dir / "_analysis", ctx.staging_dir / "_preprocess", facts)
                (ctx.staging_dir / "_analysis" / "_depth_report.json").write_text(
                    depth.model_dump_json(indent=2), encoding="utf-8"
                )
            if not depth.ok or depth.coverage_count != facts.unit_count:
                raise RuntimeError("; ".join(depth.errors) or "Phase 2 depth mismatch")
            await log_cb(ctx.project_id, "[custom] Phase 2 depth gate passed", "info")
            guide_sections = validate_analysis_guide_sections(ctx.staging_dir / "_analysis", facts)
            (ctx.staging_dir / "_analysis" / "_guide_sections_report.json").write_text(
                guide_sections.model_dump_json(indent=2), encoding="utf-8"
            )
            if not guide_sections.ok:
                await self._phase2_targeted_repair(ctx, state, guide_sections, log_cb)
                guide_sections = validate_analysis_guide_sections(ctx.staging_dir / "_analysis", facts)
                (ctx.staging_dir / "_analysis" / "_guide_sections_report.json").write_text(
                    guide_sections.model_dump_json(indent=2), encoding="utf-8"
                )
            if not guide_sections.ok:
                raise RuntimeError("; ".join(guide_sections.errors) or "Phase 2 guide-section mismatch")
            await log_cb(ctx.project_id, "[custom] Phase 2 guide-section gate passed", "info")

            await self._phase26_scorecard(ctx, state, log_cb)
            self._phase28_bundles(ctx, facts)
            self._write_documentation_obligations(ctx, facts)
            await self._phase3_docs(ctx, state, log_cb)

            doc_report = validate_docs(ctx.staging_dir, facts)
            (ctx.staging_dir / "_assessment" / "doc_validation_report.json").write_text(
                doc_report.model_dump_json(indent=2), encoding="utf-8"
            )
            if not doc_report.ok or doc_report.coverage_count != facts.unit_count:
                await self._phase3_targeted_repair(ctx, state, doc_report, log_cb)
                doc_report = validate_docs(ctx.staging_dir, facts)
                (ctx.staging_dir / "_assessment" / "doc_validation_report.json").write_text(
                    doc_report.model_dump_json(indent=2), encoding="utf-8"
                )
            if not doc_report.ok or doc_report.coverage_count != facts.unit_count:
                raise RuntimeError("; ".join(doc_report.errors) or "Phase 3 coverage mismatch")
            await log_cb(ctx.project_id, "[custom] Phase 3 factual gates passed", "info")

            doc_depth = validate_doc_depth(ctx.staging_dir, facts)
            (ctx.staging_dir / "_assessment" / "doc_depth_report.json").write_text(
                doc_depth.model_dump_json(indent=2), encoding="utf-8"
            )
            if not doc_depth.ok or doc_depth.coverage_count != facts.unit_count:
                await self._phase3_targeted_repair(ctx, state, doc_depth, log_cb)
                doc_depth = validate_doc_depth(ctx.staging_dir, facts)
                (ctx.staging_dir / "_assessment" / "doc_depth_report.json").write_text(
                    doc_depth.model_dump_json(indent=2), encoding="utf-8"
                )
            if not doc_depth.ok or doc_depth.coverage_count != facts.unit_count:
                warnings = doc_depth.errors or ["Phase 3 depth warning"]
                if doc_depth.coverage_count != facts.unit_count:
                    warnings.append(
                        f"Phase 3 depth coverage warning: {doc_depth.coverage_count}/{facts.unit_count} units referenced"
                    )
                state.warnings.extend(warnings)
                await log_cb(
                    ctx.project_id,
                    "[custom] Phase 3 depth warnings preserved without failing generation: "
                    + "; ".join(warnings[:5]),
                    "warning",
                )
            else:
                await log_cb(ctx.project_id, "[custom] Phase 3 depth gate passed", "info")

            self._phase4_cleanup(ctx, state)
        except Exception as exc:
            state.errors.append(str(exc))
            await log_cb(ctx.project_id, f"[custom] generation failed: {exc}", "error")
            return AgentResult(
                success=False,
                error=str(exc),
                model_name=self.model_provider.model_name,
                write_model_name=self.write_provider.model_name,
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        return AgentResult(
            success=True,
            model_name=self.model_provider.model_name,
            write_model_name=(
                self.write_provider.model_name
                if self.write_provider.model_name != self.model_provider.model_name
                else None
            ),
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            cost_usd=calculate_total_cost(
                (c.result.model_name, c.result.input_tokens, c.result.output_tokens)
                for c in state.calls
            ),
            duration_ms=int(time.time() * 1000) - start_ms,
        )

    async def _phase1_investigation(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> set[str]:
        """Run investigation for all groups. Returns set of group names that failed."""
        preprocess_dir = ctx.staging_dir / "_preprocess"
        investigation_dir = ctx.staging_dir / "_investigation"
        investigation_dir.mkdir(parents=True, exist_ok=True)
        shared_context = self._phase2_shared_context(ctx, ctx.staging_dir / "facts_manifest.json")
        loop = SingleShotInvestigator(
            self.investigation_provider,
            ctx.staging_dir,
            state.facts_manifest,
        )
        failed_groups: set[str] = set()

        for group in ctx.preprocess_groups:
            group_units = self._group_units(state, group)
            chunks = self._phase2_group_chunks(preprocess_dir / f"{group}.md", group_units, investigation_dir)
            chunk_reports = []
            for idx, chunk_file in enumerate(chunks, start=1):
                chunk_name = group if len(chunks) == 1 else f"{group}_chunk_{idx}"
                cached = self._load_investigation_artifact(investigation_dir, chunk_name)
                if cached is not None and ctx.resume_state is not None:
                    chunk_reports.append(cached)
                    await log_cb(
                        ctx.project_id,
                        f"[custom] RESUME: skipping investigation for {chunk_name} "
                        f"({len(cached.evidence)} evidence items from prior run)",
                        "info",
                    )
                    continue
                try:
                    report, calls = await loop.run(
                        chunk_name,
                        group_file=chunk_file,
                        context_files=[*shared_context, chunk_file],
                        allowed_units=self._units_for_chunk(chunk_file, group_units),
                        provider_options=self._provider_options(self.investigation_provider),
                    )
                except InvestigationError as exc:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Investigation failed for {chunk_name} (partial, continuing): {exc}",
                        "warning",
                    )
                    continue
                state.calls.extend(calls)
                for call in calls:
                    await self._log_call_metrics(ctx, call.target, call.result, log_cb)
                chunk_reports.append(report)
                # Write per-chunk artifact immediately so RESUME can skip this chunk next run
                chunk_artifact = report.model_copy(update={"group": chunk_name})
                write_investigation_artifacts(chunk_artifact, investigation_dir)
                await log_cb(
                    ctx.project_id,
                    f"[custom] Investigation complete: {chunk_name} "
                    f"({len(report.evidence)} evidence items)",
                    "info",
                )

            if not chunk_reports:
                failed_groups.add(group)
                await log_cb(
                    ctx.project_id,
                    f"[custom] Investigation failed for all chunks of {group}, skipping group",
                    "error",
                )
                continue

            merged = self._merge_investigation_reports(group, chunk_reports)
            write_investigation_artifacts(merged, investigation_dir)
            state.investigation_reports.append(merged)

        (investigation_dir / "_summary.md").write_text(
            "# Investigation Summary\n\n"
            f"{investigation_summary(state.investigation_reports)}",
            encoding="utf-8",
        )
        return failed_groups

    async def _phase2_analysis(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        preprocess_dir = ctx.staging_dir / "_preprocess"
        analysis_dir = ctx.staging_dir / "_analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        shared_context = self._phase2_shared_context(ctx, ctx.staging_dir / "facts_manifest.json")
        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
        semaphore = asyncio.Semaphore(max(1, config.agent_phase2_concurrency))

        async def _process_group(group: str) -> None:
            async with semaphore:
                await self._phase2_group_agent(
                    ctx,
                    state,
                    runtime,
                    group,
                    shared_context,
                    analysis_dir,
                    preprocess_dir,
                    log_cb,
                )

        await asyncio.gather(*[_process_group(group) for group in ctx.preprocess_groups])

    async def _phase2_group_agent(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        runtime: AgentTaskRuntime,
        group: str,
        shared_context: list[Path],
        analysis_dir: Path,
        preprocess_dir: Path,
        log_cb: LogCallback,
    ) -> None:
            analysis_path = analysis_dir / f"{group}.md"
            if ctx.resume_state is not None and analysis_path.exists() and analysis_path.stat().st_size > 500:
                if group_analysis_is_complete(analysis_path, state.facts_manifest.platform):
                    await log_cb(ctx.project_id, f"[custom] RESUME: skipping Phase 2 for {group} (analysis exists)", "info")
                    return
                await log_cb(ctx.project_id, f"[custom] RESUME: cached Phase 2 for {group} is incomplete, regenerating", "info")

            group_units = self._group_units(state, group)
            group_file = preprocess_dir / f"{group}.md"
            file_bytes = group_file.stat().st_size if group_file.exists() else 0
            opts = ModelOptions(
                max_tokens=min(config.phase3_max_tokens, max(config.phase2_max_tokens, file_bytes // 4)),
                temperature=0.1,
                retries=1,
                timeout_s=300.0,
                provider_options=self._provider_options(self.model_provider),
            )
            chunks = self._phase2_group_chunks(group_file, group_units, analysis_dir)
            investigation_context = self._investigation_context(ctx, group)
            chunk_outputs: list[str] = []
            for idx, chunk_file in enumerate(chunks, start=1):
                prompt = group_analysis_prompt(state.facts_manifest, group, pack=self.pack, plugin_pack_dir=self.plugin_pack_dir, required_docs=self._required_docs)
                if len(chunks) > 1:
                    prompt += (
                        f"\n\nThis is chunk {idx} of {len(chunks)} for group {group}. "
                        "Analyze only the supplied chunk, but preserve exact unit names and platform-specific evidence."
                    )
                target = group if len(chunks) == 1 else f"{group}_chunk_{idx}"
                context_files = [*shared_context, *investigation_context, chunk_file]
                contract = AgentTaskContract(
                    phase="phase2_group",
                    target_id=target,
                    allowed_units=self._units_for_chunk(chunk_file, group_units),
                    allowed_groups=[group],
                    allowed_categories=self._default_evidence_categories(),
                    starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
                    tool_budget=default_tool_budget(),
                    input_budget=default_input_budget(),
                    expected_artifact="analysis markdown",
                    max_attempts=config.agent_max_task_attempts,
                )
                cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
                if cached is not None:
                    chunk_outputs.append(cached.strip())
                    await log_cb(ctx.project_id, f"[custom] RESUME: skipping Phase 2 task {target} (accepted)", "info")
                    continue

                async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                    return await self._complete_core_generation(
                        ctx,
                        state,
                        provider=self.model_provider,
                        phase="phase2",
                        target=task.target_id,
                        prompt=prompt,
                        context_files=context_files,
                        opts=opts,
                        log_cb=log_cb,
                        task_contract=task,
                        scoped_tools=tools,
                    )

                task_result = await runtime.run(
                    contract,
                    _dispatch,
                    lambda text, units=contract.allowed_units: self._validate_agent_markdown(text, units),
                )
                if not task_result.result.success:
                    raise RuntimeError(f"Phase 2 failed for {group}: {task_result.result.raw_error}")
                if not task_result.accepted:
                    await log_cb(ctx.project_id, f"[custom] Phase 2 task {target} has validation gaps; using repair ladder", "warning")
                chunk_outputs.append(task_result.result.text.strip())

            if len(chunk_outputs) > 1:
                if config.phase2_merge_mode.lower() == "llm":
                    model_analysis = await self._phase2_merge_agent(
                        ctx,
                        state,
                        runtime,
                        group,
                        group_units,
                        chunk_outputs,
                        shared_context,
                        investigation_context,
                        opts,
                        log_cb,
                    )
                else:
                    model_analysis = self._deterministic_phase2_merge(group, chunk_outputs)
                    await log_cb(
                        ctx.project_id,
                        f"[custom] Phase 2 deterministic merge complete: {group} ({len(chunk_outputs)} chunks)",
                        "info",
                    )
                model_analysis = await self._phase2_cross_chunk_synthesis(
                    ctx,
                    state,
                    group,
                    group_units,
                    chunk_outputs,
                    model_analysis,
                    shared_context,
                    investigation_context,
                    opts,
                    log_cb,
                )
            else:
                model_analysis = "\n\n---\n\n".join(chunk_outputs)
            model_analysis = await self._complete_phase2_sections(
                ctx,
                state,
                group,
                model_analysis,
                shared_context,
                investigation_context,
                group_file,
                opts,
                log_cb,
            )
            coverage_block = "\n".join(f"- COVERED: {name}" for name in group_units)
            content = (
                f"# Phase 2 Analysis: {group}\n\n"
                "## Coverage\n\n"
                f"{coverage_block}\n\n"
                "## Model Analysis\n\n"
                f"{model_analysis}\n"
            )
            (analysis_dir / f"{group}.md").write_text(content, encoding="utf-8")
            await log_cb(ctx.project_id, f"[custom] Phase 2 complete: {group}", "info")

    @classmethod
    def _deterministic_phase2_merge(cls, group: str, chunk_outputs: list[str]) -> str:
        parsed_chunks = [cls._parse_phase2_sections(text) for text in chunk_outputs]
        parts: list[str] = [
            f"Deterministic merge of {len(chunk_outputs)} Phase 2 chunk analyses for group `{group}`.",
            "Chunk text is preserved verbatim under provenance headings.",
        ]
        for section in PHASE2_SECTION_NAMES:
            section_parts: list[str] = []
            for idx, sections in enumerate(parsed_chunks, start=1):
                body = sections.get(section, "").strip()
                if not body:
                    continue
                section_parts.append(f"#### {group}_chunk_{idx}\n\n{body}")
            if section_parts:
                parts.append(f"### {section}\n\n" + "\n\n".join(section_parts))
        return "\n\n".join(parts).rstrip() + "\n"

    @staticmethod
    def _parse_phase2_sections(text: str) -> dict[str, str]:
        section_names = set(PHASE2_SECTION_NAMES)
        sections: dict[str, list[str]] = {name: [] for name in PHASE2_SECTION_NAMES}
        current = ADDITIONAL_FINDINGS_SECTION
        heading_re = re.compile(r"^#{1,6}\s+([A-Z][A-Z0-9_ ]+)\s*$")
        for line in text.splitlines():
            match = heading_re.match(line.strip())
            if match:
                candidate = re.sub(r"[^A-Z0-9_]+", "_", match.group(1).strip().upper()).strip("_")
                if candidate == "ADDITIONAL_FINDINGS":
                    candidate = ADDITIONAL_FINDINGS_SECTION
                if candidate in section_names:
                    current = candidate
                    continue
                current = ADDITIONAL_FINDINGS_SECTION
            sections.setdefault(current, []).append(line)
        return {
            name: "\n".join(lines).strip()
            for name, lines in sections.items()
            if "\n".join(lines).strip()
        }

    async def _phase2_merge_agent(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        runtime: AgentTaskRuntime,
        group: str,
        group_units: list[str],
        chunk_outputs: list[str],
        shared_context: list[Path],
        investigation_context: list[Path],
        opts: ModelOptions,
        log_cb: LogCallback,
    ) -> str:
        merge_dir = ctx.staging_dir / "_analysis" / "_merge_inputs"
        merge_dir.mkdir(parents=True, exist_ok=True)
        merge_input = merge_dir / f"{self._safe_name(group)}_chunks.md"
        merge_input.write_text(
            "\n\n--- CHUNK ANALYSIS ---\n\n".join(chunk_outputs),
            encoding="utf-8",
        )
        prompt = (
            f"Merge the supplied Phase 2 chunk analyses for group `{group}` into one cohesive "
            "group analysis body. Preserve all exact canonical unit names, expressions, risks, "
            "and scanner-limited caveats. Do not invent new conclusions. Write markdown only.\n\n"
            "Canonical units:\n"
            + "\n".join(f"- {name}" for name in group_units)
        )
        context_files = [*shared_context, *investigation_context, merge_input]
        contract = AgentTaskContract(
            phase="phase2_merge",
            target_id=group,
            allowed_units=group_units,
            allowed_groups=[group],
            allowed_categories=self._default_evidence_categories(),
            starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
            tool_budget=default_tool_budget(),
            input_budget=default_input_budget(),
            expected_artifact="merged analysis markdown",
            max_attempts=config.agent_max_task_attempts,
        )
        cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
        if cached is not None:
            await log_cb(ctx.project_id, f"[custom] RESUME: skipping Phase 2 merge for {group} (accepted)", "info")
            return cached.strip()

        async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
            return await self._complete_core_generation(
                ctx,
                state,
                provider=self.model_provider,
                phase="phase2.merge",
                target=task.target_id,
                prompt=prompt,
                context_files=context_files,
                opts=opts,
                log_cb=log_cb,
                task_contract=task,
                scoped_tools=tools,
            )

        task_result = await runtime.run(
            contract,
            _dispatch,
            lambda text: self._validate_agent_markdown(text, group_units),
        )
        if not task_result.result.success:
            raise RuntimeError(f"Phase 2 merge failed for {group}: {task_result.result.raw_error}")
        return task_result.result.text.strip()

    async def _phase2_cross_chunk_synthesis(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        group: str,
        group_units: list[str],
        chunk_outputs: list[str],
        merged_analysis: str,
        shared_context: list[Path],
        investigation_context: list[Path],
        opts: ModelOptions,
        log_cb: LogCallback,
    ) -> str:
        """Append a bounded synthesis section for relationships only visible across chunks."""

        synthesis_dir = ctx.staging_dir / "_analysis" / "_synthesis_inputs"
        synthesis_dir.mkdir(parents=True, exist_ok=True)
        synthesis_input = synthesis_dir / f"{self._safe_name(group)}_cross_chunk.md"
        synthesis_input.write_text(
            "# Merged Phase 2 Analysis\n\n"
            f"{merged_analysis.strip()}\n\n"
            + "\n\n--- CHUNK ANALYSIS ---\n\n".join(chunk_outputs),
            encoding="utf-8",
        )
        prompt = (
            f"Analyze the supplied merged and per-chunk Phase 2 evidence for group `{group}`. "
            "Write only a concise markdown section headed exactly `### CROSS_CHUNK_FINDINGS`. "
            "Cover shared systems, dependencies, repeated patterns, contradictions, risks, and "
            "end-to-end flows that require comparing multiple chunks. Preserve exact canonical "
            "unit names. Do not invent facts beyond the supplied evidence.\n\n"
            "Canonical units:\n"
            + "\n".join(f"- {name}" for name in group_units)
        )
        synthesis_opts = ModelOptions(
            max_tokens=min(config.phase3_max_tokens, max(8_000, config.phase2_max_tokens // 2)),
            temperature=0.1,
            retries=1,
            timeout_s=opts.timeout_s,
            provider_options=opts.provider_options,
        )
        context_files = [*shared_context, *investigation_context, synthesis_input]
        result = await self._complete_core_generation(
            ctx,
            state,
            provider=self.model_provider,
            phase="phase2.synthesis",
            target=f"{group}_cross_chunk",
            prompt=prompt,
            context_files=context_files,
            opts=synthesis_opts,
            log_cb=log_cb,
        )
        if not result.success:
            await log_cb(ctx.project_id, f"[custom] Phase 2 cross-chunk synthesis skipped for {group}: {result.raw_error}", "warning")
            return merged_analysis
        synthesis = result.text.strip()
        if "CROSS_CHUNK_FINDINGS" not in synthesis:
            synthesis = f"### CROSS_CHUNK_FINDINGS\n\n{synthesis}"
        await log_cb(ctx.project_id, f"[custom] Phase 2 cross-chunk synthesis complete: {group}", "info")
        return f"{merged_analysis.rstrip()}\n\n{synthesis}\n"

    async def _complete_phase2_sections(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        group: str,
        model_analysis: str,
        shared_context: list[Path],
        investigation_context: list[Path],
        group_file: Path,
        opts: ModelOptions,
        log_cb: LogCallback,
    ) -> str:
        """Normalize Talend guide headings and make one targeted repair call if needed."""

        platform = state.facts_manifest.platform
        normalized = normalize_analysis_guide_headings(model_analysis, platform)
        missing = missing_required_analysis_sections(normalized, platform)
        if not missing:
            return normalized

        await log_cb(
            ctx.project_id,
            f"[custom] Phase 2 for {group} missing guide sections after first pass: "
            f"{', '.join(missing)}; requesting targeted repair",
            "warning",
        )
        repair_prompt = phase2_section_repair_prompt(state.facts_manifest, group, missing)
        repair_opts = ModelOptions(
            max_tokens=min(config.phase3_max_tokens, max(config.phase2_max_tokens, len(missing) * 8_000)),
            temperature=0.1,
            retries=1,
            timeout_s=opts.timeout_s,
            provider_options=opts.provider_options,
        )
        context_files = [*shared_context, *investigation_context, group_file]
        result = await self._complete_core_generation(
            ctx,
            state,
            provider=self.model_provider,
            phase="phase2.repair",
            target=f"{group}_section_repair",
            prompt=repair_prompt,
            context_files=context_files,
            opts=repair_opts,
            log_cb=log_cb,
        )
        if not result.success:
            raise RuntimeError(f"Phase 2 section repair failed for {group}: {result.raw_error}")

        repaired = normalize_analysis_guide_headings(result.text.strip(), platform)
        combined = f"{normalized.rstrip()}\n\n---\n\n{repaired}\n"
        still_missing = missing_required_analysis_sections(combined, platform)
        if still_missing:
            raise RuntimeError(
                f"Phase 2 analysis for group {group} missing required sections after repair: "
                f"{', '.join(still_missing)}"
            )
        await log_cb(ctx.project_id, f"[custom] Phase 2 guide-section repair complete: {group}", "info")
        return combined

    async def _phase2_targeted_repair(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        report,
        log_cb: LogCallback,
    ) -> None:
        errors = report.errors[:10]
        missing_units = report.missing_units[:50]
        if not errors and not missing_units:
            return
        await log_cb(
            ctx.project_id,
            "[custom] Phase 2 targeted repair starting: "
            + ("; ".join(errors[:3]) or f"missing units: {', '.join(missing_units[:10])}"),
            "warning",
        )

        units_by_group: dict[str, list[str]] = {}
        if missing_units:
            for unit in state.facts_manifest.units:
                if unit.name in missing_units:
                    units_by_group.setdefault(unit.group, []).append(unit.name)
        else:
            for group in state.facts_manifest.groups:
                units_by_group[group] = self._group_units(state, group)

        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
        semaphore = asyncio.Semaphore(max(1, config.agent_repair_concurrency))

        async def _repair_group(group: str, units: list[str]) -> None:
            async with semaphore:
                target = ctx.staging_dir / "_analysis" / f"{group}.md"
                context_files = [
                    p for p in [
                        ctx.staging_dir / "facts_manifest.json",
                        target,
                        ctx.staging_dir / "_investigation" / f"{self._safe_name(group)}.md",
                        ctx.staging_dir / "_investigation" / "_summary.md",
                        ctx.staging_dir / "_preprocess" / f"{group}.md",
                        ctx.staging_dir / "engineering_facts.md",
                        ctx.staging_dir / "known_risks.md",
                        ctx.staging_dir / "dependency_map.md",
                    ]
                    if p.exists()
                ]
                prompt = (
                    f"Repair only the listed Phase 2 analysis gaps for group `{group}`.\n\n"
                    "Rules:\n"
                    "- Write markdown sections that can be appended under `## Model Analysis`.\n"
                    "- Preserve exact canonical unit names.\n"
                    "- Use only supplied scanner, investigation, and EngineeringFacts evidence.\n"
                    "- If evidence is unavailable, explicitly state scanner-limited evidence for the unit.\n\n"
                    "Validator errors:\n"
                    + ("\n".join(f"- {error}" for error in errors) or "- None")
                    + "\n\nUnits to cover:\n"
                    + "\n".join(f"- {unit}" for unit in units)
                )
                opts = ModelOptions(
                    max_tokens=min(config.phase3_max_tokens, 12_000),
                    temperature=0.1,
                    retries=1,
                    timeout_s=240.0,
                    provider_options=self._provider_options(self.model_provider),
                )
                contract = AgentTaskContract(
                    phase="repair",
                    target_id=f"{group}_phase2_repair",
                    allowed_units=units,
                    allowed_groups=[group],
                    allowed_categories=self._default_evidence_categories(),
                    starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
                    tool_budget=default_tool_budget(),
                    input_budget=default_input_budget(),
                    expected_artifact="repair markdown",
                    max_attempts=config.agent_max_repair_attempts,
                )

                async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                    return await self._complete_core_generation(
                        ctx,
                        state,
                        provider=self.model_provider,
                        phase="phase2.repair",
                        target=task.target_id,
                        prompt=prompt,
                        context_files=context_files,
                        opts=opts,
                        log_cb=log_cb,
                        task_contract=task,
                        scoped_tools=tools,
                    )

                task_result = await runtime.run(
                    contract,
                    _dispatch,
                    lambda text: self._validate_agent_markdown(text, units, min_bytes=80),
                )
                if not task_result.result.success:
                    await log_cb(ctx.project_id, f"[custom] Phase 2 targeted repair failed for {group}: {task_result.result.raw_error}", "warning")
                    return
                existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
                coverage = "\n".join(f"- COVERED: {unit}" for unit in units)
                target.write_text(
                    f"{existing.rstrip()}\n\n## Targeted Validator Repair\n\n{coverage}\n\n{task_result.result.text.strip()}\n",
                    encoding="utf-8",
                )
                await log_cb(ctx.project_id, f"[custom] Phase 2 targeted repair appended to {target.name}", "info")

        await asyncio.gather(*[_repair_group(group, units) for group, units in units_by_group.items()])

    async def _phase26_scorecard(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
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
            provider_options=self._provider_options(self.model_provider),
        )
        scorecard_prompt_text = scorecard_prompt(state.facts_manifest, pack=self.pack, plugin_pack_dir=self.plugin_pack_dir)
        context_files = []
        if scorecard_pack.exists():
            context_files.append(scorecard_pack)
        else:
            context_files.extend(self._existing(ctx.staging_dir / "_preprocess", [
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
            allowed_categories=self._default_evidence_categories(),
            starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
            tool_budget=default_tool_budget(),
            input_budget=default_input_budget(),
            expected_artifact="scorecard markdown",
            max_attempts=config.agent_max_task_attempts,
        )
        cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
        scorecard_task_output_path: str | None = None
        if cached is not None:
            result = ModelResult(success=True, text=cached, model_name=self.model_provider.model_name)
            await log_cb(ctx.project_id, "[custom] RESUME: skipping Phase 2.6 scorecard (accepted)", "info")
        else:
            async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                return await self._complete_core_generation(
                    ctx,
                    state,
                    provider=self.model_provider,
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
                lambda text: self._validate_agent_markdown(text, [], min_bytes=200),
            )
            result = task_result.result
            scorecard_task_output_path = task_result.output_path
        if not result.success:
            raise RuntimeError(f"Scorecard generation failed: {result.raw_error}")
        if int(result.metadata.get("slices") or 0) > 1:
            result = await self._reduce_split_scorecard(ctx, state, result.text, scorecard_pack, log_cb)
            if not result.success:
                raise RuntimeError(f"Scorecard reducer failed: {result.raw_error}")
            if scorecard_task_output_path:
                Path(scorecard_task_output_path).write_text(result.text.strip(), encoding="utf-8")
        content = (
            "# Assessment Scorecard\n\n"
            f"Canonical inventory: {state.facts_manifest.unit_count} "
            f"{state.facts_manifest.canonical_unit_label}.\n\n"
            f"{result.text.strip()}\n"
        )
        (assessment_dir / "scorecard.md").write_text(content, encoding="utf-8")
        await log_cb(ctx.project_id, "[custom] Phase 2.6 scorecard complete", "info")

    async def _reduce_split_scorecard(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        slice_text: str,
        scorecard_pack: Path,
        log_cb: LogCallback,
    ) -> ModelResult:
        reducer_dir = ctx.staging_dir / "_assessment" / "_scorecard_reduce"
        reducer_dir.mkdir(parents=True, exist_ok=True)
        slice_path = reducer_dir / "scorecard_slice_outputs.md"
        slice_path.write_text(slice_text.strip() + "\n", encoding="utf-8")
        prompt = (
            "Reduce the supplied Phase 2.6 scorecard slice outputs into one coherent global "
            "assessment scorecard. Preserve concrete evidence, exact unit names, anti-pattern IDs, "
            "red-flag IDs, and security checklist findings. Resolve duplicate or conflicting "
            "slice-level ratings into one global rating per dimension using the scoring rubric. "
            "Do not invent evidence. Write markdown only.\n\n"
            "Required final structure:\n"
            "1. Overall score with one-sentence justification\n"
            "2. Dimension table with architecture, data lineage, business logic, operations, "
            "security, performance, standards, migration\n"
            "3. Top risks ranked by severity\n"
            "4. Top strengths\n"
            "5. Anti-pattern findings\n"
            "6. Red flag findings\n"
            "7. Security checklist\n\n"
            f"Canonical inventory: {state.facts_manifest.unit_count} "
            f"{state.facts_manifest.canonical_unit_label}.\n"
        )
        context_files = [ctx.staging_dir / "facts_manifest.json", slice_path]
        if scorecard_pack.exists():
            context_files.append(scorecard_pack)
        else:
            context_files.extend(self._existing(ctx.staging_dir / "_preprocess", [
                "_scoring_rubric.md",
                "_anti_patterns.md",
                "_red_flags.md",
                "_security_checklist.md",
                "_pattern_library.md",
            ]))
        opts = ModelOptions(
            max_tokens=config.phase2_max_tokens,
            temperature=0.1,
            retries=1,
            timeout_s=300.0,
            provider_options=self._provider_options(self.model_provider),
        )
        await log_cb(ctx.project_id, "[custom] Phase 2.6 scorecard reducer starting", "info")
        result = await self._complete_core_generation(
            ctx,
            state,
            provider=self.model_provider,
            phase="phase2.6.reduce",
            target="scorecard",
            prompt=prompt,
            context_files=context_files,
            opts=opts,
            log_cb=log_cb,
        )
        if result.success:
            await log_cb(ctx.project_id, "[custom] Phase 2.6 scorecard reducer complete", "info")
        return result

    def _phase28_bundles(self, ctx: DocGenContext, facts) -> None:
        analysis_dir = ctx.staging_dir / "_analysis"
        investigation_text = "\n\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((ctx.staging_dir / "_investigation").glob("*.md"))
        )
        analysis_files = [
            p for p in sorted(analysis_dir.glob("*.md"))
            if not p.name.startswith("_") and "_chunk_" not in p.name
        ]
        for bundle_name, doc_names in self._doc_assignments.items():
            section_names = self._bundle_sections_for_docs(doc_names)
            analysis_text = self._targeted_analysis_bundle_text(analysis_files, section_names)
            (analysis_dir / f"_bundle_{bundle_name}.md").write_text(
                f"# Analysis Bundle: {bundle_name}\n\n"
                f"Validated canonical coverage: {facts.unit_count} units.\n\n"
                "Included Phase 2 sections: "
                f"{', '.join(sorted(section_names))}.\n\n"
                "## Investigation Evidence Summaries\n\n"
                f"{investigation_text}\n\n"
                "## Phase 2 Analysis\n\n"
                f"{analysis_text}",
                encoding="utf-8",
            )
        manifest: dict[str, object] = {"source_analyses": [p.name for p in analysis_files], "doc_packs": {}}
        for doc_name in self._required_docs:
            section_names = self._bundle_sections_for_docs([doc_name])
            pack_text, pack_manifest = self._doc_evidence_pack_text(
                ctx,
                doc_name,
                analysis_files,
                section_names,
                investigation_text,
                facts,
            )
            pack_path = analysis_dir / f"_doc_pack_{doc_name}.md"
            pack_path.write_text(pack_text, encoding="utf-8")
            manifest["doc_packs"][doc_name] = {"path": pack_path.name, **pack_manifest}
        (analysis_dir / "_doc_pack_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @staticmethod
    def _write_documentation_obligations(ctx: DocGenContext, facts) -> None:
        obligation_map = build_documentation_obligation_map(facts)
        path = ctx.staging_dir / "documentation_obligations.json"
        path.write_text(json.dumps(obligation_map, indent=2), encoding="utf-8")

    @staticmethod
    def _bundle_sections_for_docs(doc_names: list[str]) -> set[str]:
        sections = set(BASELINE_BUNDLE_SECTIONS)
        for doc_name in doc_names:
            sections.update(DOC_SECTION_BUNDLE_MAP.get(doc_name, set()))
        return sections

    def _doc_evidence_pack_text(
        self,
        ctx: DocGenContext,
        doc_name: str,
        analysis_files: list[Path],
        section_names: set[str],
        investigation_text: str,
        facts,
    ) -> tuple[str, dict[str, object]]:
        selected, manifest = self._targeted_analysis_bundle_parts(analysis_files, section_names)
        included_obligations, unmatched_obligations, obligation_text = self._doc_obligation_context(facts, doc_name)
        groups = ", ".join(f"{group}={count}" for group, count in sorted(facts.groups.items())) or "none"
        units = "\n".join(
            f"- {unit.name} | group={unit.group} | path={unit.path or 'scanner-limited'} | "
            f"type={unit.unit_type} | systems={', '.join(unit.key_systems) or 'none'}"
            for unit in facts.units
        )
        systems = "\n".join(f"- {system}" for system in facts.key_systems) or "- None identified in scanner facts"
        source_coverage = ctx.staging_dir / "_preprocess" / "_source_coverage.json"
        coverage_note = source_coverage.relative_to(ctx.staging_dir).as_posix() if source_coverage.exists() else "No direct source coverage file"
        pack = (
            f"# Document Evidence Pack: {doc_name}\n\n"
            "This is the primary Phase 3 evidence context for this document. "
            "Use tools only for drill-down or targeted repair, not for first-pass rediscovery.\n\n"
            "## Shared Project Scope\n\n"
            f"- Platform: {facts.platform}\n"
            f"- Canonical inventory: {facts.unit_count} {facts.canonical_unit_label}\n"
            f"- Groups: {groups}\n"
            f"- Source coverage: {coverage_note}\n\n"
            "## All Groups And Units\n\n"
            f"{units or '- No canonical units found'}\n\n"
            "## Cross-Group Reference Index\n\n"
            "Use the CROSS_GROUP_REFS excerpts below; open the full Phase 2 source files only for drill-down.\n\n"
            "## External Systems Summary\n\n"
            f"{systems}\n\n"
            "## Top Risks Summary\n\n"
            "Use the RISK_FLAGS excerpts below and `_assessment/scorecard.md` for ranked risk and quality scoring evidence.\n\n"
            "## Source Coverage And Scanner Limits\n\n"
            "Covered source files are evidence of record. Redacted values are intentionally hidden secrets, not missing files. "
            "Scanner-limited claims must name the limited artifact or section.\n\n"
            "## Documentation Obligations\n\n"
            f"{obligation_text}\n\n"
            "## Deep Evidence Pointers\n\n"
            + "\n".join(f"- `_analysis/{path.name}` full Phase 2 detail" for path in analysis_files)
            + "\n- `_assessment/scorecard.md` quantitative risk and quality scoring\n"
            "\n## Investigation Evidence Summaries\n\n"
            f"{investigation_text or 'No investigation summaries were written.'}\n\n"
            "## Doc-Specific Phase 2 Evidence\n\n"
            f"Included sections: {', '.join(sorted(section_names))}.\n\n"
            f"{selected}\n"
        )
        manifest["included_obligations"] = included_obligations
        manifest["unmatched_obligations"] = unmatched_obligations
        manifest["evidence_sources"] = [
            *[f"_analysis/{path.name}" for path in analysis_files],
            "_investigation/*.md",
            "_assessment/scorecard.md",
            "facts_manifest.json",
            "documentation_obligations.json",
        ]
        return pack, manifest

    @staticmethod
    def _doc_obligation_context(facts, doc_name: str) -> tuple[list[dict[str, object]], list[dict[str, object]], str]:
        obligation_map = build_documentation_obligation_map(facts)
        doc_targets = obligation_map.get("doc_targets", {}) if isinstance(obligation_map, dict) else {}
        included: list[dict[str, object]] = []
        unmatched: list[dict[str, object]] = []

        for category, terms in obligation_map.get("shared", {}).items():
            targets = doc_targets.get(category, REQUIRED_DOCS)
            item = {
                "scope": "shared",
                "category": category,
                "terms": terms,
                "doc_targets": targets,
            }
            if doc_name in targets:
                included.append(item)
            else:
                unmatched.append(item)

        for category, terms in obligation_map.get("platform_specific", {}).items():
            targets = doc_targets.get(f"platform_specific.{category}", REQUIRED_DOCS)
            item = {
                "scope": "platform_specific",
                "category": category,
                "terms": terms,
                "doc_targets": targets,
            }
            if doc_name in targets:
                included.append(item)
            else:
                unmatched.append(item)

        unit_targets = doc_targets.get("units", ["02_COMPONENT_INVENTORY", "00_AGENT_BRIEF", "08_CHANGE_PLAYBOOK"])
        unit_obligation = {
            "scope": "units",
            "category": "canonical_inventory",
            "terms": ["name", "group", "source artifact", "type", "key systems"],
            "doc_targets": unit_targets,
        }
        if doc_name in unit_targets:
            included.append(unit_obligation)
        else:
            unmatched.append(unit_obligation)

        lines = ["Included obligations for this document:"]
        lines.extend(
            f"- {item['scope']}.{item['category']}: {', '.join(str(term) for term in item['terms'])}"
            for item in included
        )
        if not included:
            lines.append("- None directly mapped; use shared project scope only.")
        lines.append("\nObligations mapped to other documents:")
        lines.extend(
            f"- {item['scope']}.{item['category']} -> {', '.join(str(doc) for doc in item['doc_targets'])}"
            for item in unmatched
        )
        return included, unmatched, "\n".join(lines)

    @classmethod
    def _targeted_analysis_bundle_text(cls, analysis_files: list[Path], section_names: set[str]) -> str:
        text, _ = cls._targeted_analysis_bundle_parts(analysis_files, section_names)
        return text

    @classmethod
    def _targeted_analysis_bundle_parts(cls, analysis_files: list[Path], section_names: set[str]) -> tuple[str, dict[str, object]]:
        parts: list[str] = []
        manifest: dict[str, object] = {"included": {}, "omitted": {}, "unclassified": {}}
        for path in analysis_files:
            text = path.read_text(encoding="utf-8", errors="replace")
            sections = cls._parse_phase2_sections(text)
            selected = [
                (section, sections[section])
                for section in PHASE2_SECTION_NAMES
                if section in section_names and sections.get(section)
            ]
            if not selected:
                selected = [(ADDITIONAL_FINDINGS_SECTION, text.strip())]
            selected_names = {name for name, _ in selected}
            manifest["included"][path.name] = [name for name, _ in selected]
            manifest["omitted"][path.name] = sorted(name for name in sections if name not in selected_names)
            if sections.get(ADDITIONAL_FINDINGS_SECTION):
                manifest["unclassified"][path.name] = True
            parts.append(f"### Source Analysis: {path.name}")
            for section, body in selected:
                parts.append(f"#### {section}\n\n{body.strip()}")
        return "\n\n".join(parts).rstrip(), manifest

    async def _phase3_docs(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        scorecard = ctx.staging_dir / "_assessment" / "scorecard.md"
        opts = ModelOptions(
            max_tokens=config.phase3_max_tokens,
            temperature=0.1,
            retries=1,
            timeout_s=300.0,
            provider_options=self._provider_options(self.write_provider),
        )

        _calls_lock = asyncio.Lock()
        runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)

        async def _write_one(doc_name: str) -> None:
            doc_prompt_text = doc_prompt(state.facts_manifest, doc_name, pack=self.pack, plugin_pack_dir=self.plugin_pack_dir, extra_section_requirements=self._doc_section_requirements_extra)
            context_files = self._phase3_context_files(ctx, doc_name)
            if scorecard.exists():
                context_files.append(scorecard)
            contract = AgentTaskContract(
                phase="phase3_doc",
                target_id=doc_name,
                allowed_units=[unit.name for unit in state.facts_manifest.units],
                allowed_groups=list(state.facts_manifest.groups),
                allowed_categories=self._default_evidence_categories(),
                starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
                tool_budget=default_tool_budget(),
                input_budget=default_input_budget(),
                expected_artifact="document markdown",
                max_attempts=config.agent_max_task_attempts,
            )
            cached = runtime.accepted_output(contract) if ctx.resume_state is not None else None
            if cached is not None:
                result = ModelResult(success=True, text=cached, model_name=self.write_provider.model_name)
                await log_cb(ctx.project_id, f"[custom] RESUME: skipping Phase 3 doc task {doc_name} (accepted)", "info")
            else:
                async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                    return await self._complete_core_generation(
                        ctx,
                        state,
                        provider=self.write_provider,
                        phase="phase3",
                        target=task.target_id,
                        prompt=doc_prompt_text,
                        context_files=context_files,
                        opts=opts,
                        log_cb=log_cb,
                        calls_lock=_calls_lock,
                        task_contract=task,
                        scoped_tools=tools,
                    )

                task_result = await runtime.run(
                    contract,
                    _dispatch,
                    lambda text: self._validate_agent_markdown(text, [], min_bytes=200),
                )
                result = task_result.result
            if not result.success:
                raise RuntimeError(f"Doc generation failed for {doc_name}: {result.raw_error}")
            content = self._final_doc_content(state, doc_name, result.text)
            (ctx.staging_dir / f"{doc_name}.md").write_text(content, encoding="utf-8")
            await log_cb(ctx.project_id, f"[custom] Phase 3 doc complete: {doc_name}", "info")

        _i = 0
        while _i < len(self._required_docs):
            batch_size = 1 if state.adaptive_serial_generation else max(1, config.agent_phase3_concurrency)
            await asyncio.gather(*[_write_one(d) for d in self._required_docs[_i : _i + batch_size]])
            _i += batch_size

    def _final_doc_content(self, state: HarnessRunState, doc_name: str, body: str) -> str:
        content = body.strip()
        canonical_title = doc_name.replace("_", " ").title()
        first_heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if not first_heading or canonical_title.lower() not in first_heading.group(1).lower():
            content = f"# {canonical_title}\n\n{content}"
        if doc_name == "02_COMPONENT_INVENTORY" and "## Canonical Unit Inventory" not in content:
            content = (
                f"{content.rstrip()}\n\n"
                "## Canonical Unit Inventory\n\n"
                f"Canonical inventory: {state.facts_manifest.unit_count} "
                f"{state.facts_manifest.canonical_unit_label}.\n\n"
                f"{self._unit_catalog_markdown(state)}"
            )
        return content.rstrip() + "\n"

    async def _phase35_critic_expander(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        log_cb: LogCallback,
    ) -> None:
        """Ask for bounded doc expansions and append only validated markdown."""

        assessment_dir = ctx.staging_dir / "_assessment"
        context_base = [
            ctx.staging_dir / "facts_manifest.json",
            assessment_dir / "scorecard.md",
            assessment_dir / "quick_scan.md",
            ctx.staging_dir / "_investigation" / "_summary.md",
        ]
        critic_pack = assessment_dir / "prompt_packs" / "_critic_prompt_pack.md"
        if critic_pack.exists():
            context_base.append(critic_pack)
        opts = ModelOptions(
            max_tokens=5000,
            temperature=0.0,
            retries=0,
            timeout_s=180.0,
            provider_options=self._provider_options(self.write_provider),
        )

        for family, doc_names in self._doc_assignments.items():
            critic_prompt_text = self._critic_prompt(state, family, doc_names)
            if getattr(self.write_provider, "supports_tools", False):
                result = await self.write_provider.complete_with_tools(
                    _with_tool_instructions(critic_prompt_text, ctx.staging_dir), ctx.staging_dir, opts
                )
            else:
                docs = [ctx.staging_dir / f"{doc}.md" for doc in doc_names if (ctx.staging_dir / f"{doc}.md").exists()]
                evidence = sorted((ctx.staging_dir / "_investigation").glob("*.md"))
                context_files = [p for p in [*context_base, *evidence, *docs] if p.exists()]
                result = await self.write_provider.complete(critic_prompt_text, context_files, opts)
            state.calls.append(ProviderCallLog(phase="phase3.5", target=family, result=result))
            await self._log_call_metrics(ctx, f"critic:{family}", result, log_cb)
            if not result.success:
                await log_cb(ctx.project_id, f"[custom] Critic skipped for {family}: {result.raw_error}", "warning")
                continue
            applied = self._apply_validated_expansions(ctx, state, result.text, set(doc_names))
            await log_cb(ctx.project_id, f"[custom] Critic expansions applied for {family}: {applied}", "info")

    async def _phase3_targeted_repair(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        report,
        log_cb: LogCallback,
    ) -> None:
        """Repair validator-reported gaps with a narrow evidence pack."""

        errors = report.errors[:10]
        missing_units = report.missing_units[:50]
        if not errors and not missing_units:
            return

        await log_cb(
            ctx.project_id,
            "[custom] Phase 3 targeted repair starting: "
            + ("; ".join(errors[:3]) or f"missing units: {', '.join(missing_units[:10])}"),
            "warning",
        )
        for target_doc, target_errors in self._phase3_repair_targets(ctx, errors):
            prompt = self._phase3_repair_prompt(state, target_doc.name, target_errors, missing_units)
            context_files = self._phase3_repair_context_files(ctx, state, target_doc, missing_units)
            opts = ModelOptions(
                max_tokens=min(config.phase3_max_tokens, 12_000),
                temperature=0.1,
                retries=1,
                timeout_s=240.0,
                provider_options=self._provider_options(self.write_provider),
            )
            runtime = AgentTaskRuntime(ctx.staging_dir, state.facts_manifest)
            contract = AgentTaskContract(
                phase="repair",
                target_id=f"{target_doc.stem}_doc_repair",
                allowed_units=missing_units or [unit.name for unit in state.facts_manifest.units],
                allowed_groups=list(state.facts_manifest.groups),
                allowed_categories=self._default_evidence_categories(),
                starter_context_files=[p.relative_to(ctx.staging_dir).as_posix() for p in context_files if p.exists()],
                tool_budget=default_tool_budget(),
                input_budget=default_input_budget(),
                expected_artifact="repair markdown",
                max_attempts=config.agent_max_repair_attempts,
            )

            async def _dispatch(task: AgentTaskContract, tools: ScopedEvidenceTools) -> ModelResult:
                return await self._complete_core_generation(
                    ctx,
                    state,
                    provider=self.write_provider,
                    phase="phase3.repair",
                    target=target_doc.stem,
                    prompt=prompt,
                    context_files=context_files,
                    opts=opts,
                    log_cb=log_cb,
                    task_contract=task,
                    scoped_tools=tools,
                )

            task_result = await runtime.run(
                contract,
                _dispatch,
                lambda text: self._validate_agent_markdown(text, missing_units, min_bytes=80),
            )
            result = task_result.result
            if not result.success:
                await log_cb(ctx.project_id, f"[custom] Phase 3 targeted repair failed for {target_doc.name}: {result.raw_error}", "warning")
                continue
            existing = target_doc.read_text(encoding="utf-8", errors="replace") if target_doc.exists() else ""
            config_files = _scanned_config_files(ctx.staging_dir) | _covered_source_files(ctx.staging_dir)
            assembled = f"{existing.rstrip()}\n\n## Targeted Validator Repair\n\n{result.text.strip()}\n"
            patched = _strip_config_contradiction_sentences(assembled, config_files)
            target_doc.write_text(patched, encoding="utf-8")
            phase3_checkpoint = ctx.staging_dir / "_agent_tasks" / "phase3_doc" / target_doc.stem / "output.md"
            if phase3_checkpoint.exists():
                phase3_checkpoint.write_text(patched, encoding="utf-8")
            await log_cb(ctx.project_id, f"[custom] Phase 3 targeted repair applied to {target_doc.name}", "info")

    def _phase3_repair_targets(self, ctx: DocGenContext, errors: list[str]) -> list[tuple[Path, list[str]]]:
        grouped: dict[str, list[str]] = {}
        for error in errors:
            matches = re.findall(r"\b([0-9][0-9A-Za-z_]*\.md)\b", error)
            matched_required = [
                name for name in matches
                if name.removesuffix(".md") in self._required_docs
            ]
            if matched_required:
                for name in matched_required:
                    grouped.setdefault(name, []).append(error)
            else:
                grouped.setdefault("02_COMPONENT_INVENTORY.md", []).append(error)

        if not grouped:
            grouped["02_COMPONENT_INVENTORY.md"] = []

        targets: list[tuple[Path, list[str]]] = []
        for name, doc_errors in grouped.items():
            target_doc = ctx.staging_dir / name
            if not target_doc.exists():
                existing_docs = [
                    ctx.staging_dir / f"{doc}.md"
                    for doc in self._required_docs
                    if (ctx.staging_dir / f"{doc}.md").exists()
                ]
                target_doc = existing_docs[0] if existing_docs else ctx.staging_dir / "02_COMPONENT_INVENTORY.md"
            targets.append((target_doc, doc_errors))
        return targets

    def _phase3_repair_context_files(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        target_doc: Path,
        missing_units: list[str],
    ) -> list[Path]:
        doc_name = target_doc.stem
        analysis_dir = ctx.staging_dir / "_analysis"
        affected_groups = {
            unit.group
            for unit in state.facts_manifest.units
            if not missing_units or unit.name in missing_units
        }
        section_names = set(BASELINE_BUNDLE_SECTIONS)
        section_names.update(DOC_SECTION_BUNDLE_MAP.get(doc_name, set()))
        analysis_files: list[Path] = []
        for group in sorted(affected_groups):
            direct = analysis_dir / f"{group}.md"
            safe = analysis_dir / f"{self._safe_name(group)}.md"
            if direct.exists():
                analysis_files.append(direct)
            elif safe.exists():
                analysis_files.append(safe)
        if not analysis_files:
            bundle = self._doc_bundle_map.get(doc_name, "core")
            fallback = analysis_dir / f"_bundle_{bundle}.md"
            analysis_files = [fallback] if fallback.exists() else []

        repair_dir = analysis_dir / "_repair_context"
        repair_dir.mkdir(parents=True, exist_ok=True)
        repair_bundle = repair_dir / f"{self._safe_name(doc_name)}_repair_bundle.md"
        if analysis_files:
            repair_bundle.write_text(
                "# Targeted Repair Evidence\n\n"
                f"Included Phase 2 sections: {', '.join(sorted(section_names))}.\n\n"
                + self._targeted_analysis_bundle_text(analysis_files, section_names)
                + "\n",
                encoding="utf-8",
            )

        files = [
            ctx.staging_dir / "facts_manifest.json",
            ctx.staging_dir / "documentation_obligations.json",
            target_doc,
            analysis_dir / f"_doc_pack_{doc_name}.md",
            analysis_dir / "_doc_pack_manifest.json",
            ctx.staging_dir / "_investigation" / "_summary.md",
            repair_bundle,
            ctx.staging_dir / "_assessment" / "scorecard.md",
            *analysis_files,
            *self._engineering_fact_context(ctx, doc_name),
        ]
        return [p for p in files if p.exists()]

    @staticmethod
    def _phase3_repair_prompt(
        state: HarnessRunState,
        doc_name: str,
        errors: list[str],
        missing_units: list[str],
    ) -> str:
        return f"""\
Repair only the listed documentation gaps in {doc_name}.

Rules:
- Use only the supplied facts manifest, documentation obligations, existing document, analysis bundle, scorecard, and scanner evidence.
- Preserve exact canonical unit names.
- Do not infer project-specific meaning beyond scanner evidence.
- Write markdown sections that can be appended directly to the document.
- Cover each missing obligation directly or explain scanner-limited evidence if the supplied context is insufficient.
- Do not add broad filler. If a validator asks for inspect/test/change guidance, include concrete inspect/test/change entries.

Canonical inventory: {state.facts_manifest.unit_count} {state.facts_manifest.canonical_unit_label}

Validator errors:
{chr(10).join(f"- {error}" for error in errors) or "- None"}

Missing units:
{chr(10).join(f"- {unit}" for unit in missing_units) or "- None"}
"""

    async def _complete_core_generation(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        *,
        provider: ModelProvider,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        opts: ModelOptions,
        log_cb: LogCallback,
        calls_lock: asyncio.Lock | None = None,
        task_contract: AgentTaskContract | None = None,
        scoped_tools: ScopedEvidenceTools | None = None,
    ):
        """Run a Phase 2/3 model call with explicit bounded files and slice caching."""

        context_files = self._bounded_context_files(context_files)
        if use_tools_for_core_generation(provider):
            prompt = _with_agent_contract(prompt, task_contract)
            context_files = []
        estimated = estimate_input_tokens(prompt, context_files)
        hard = config.core_context_hard_ceiling_tokens
        soft = config.core_context_soft_target_tokens
        if estimated <= hard:
            result = await self._call_model_with_logging(
                ctx,
                state,
                provider=provider,
                phase=phase,
                target=target,
                prompt=prompt,
                context_files=context_files,
                opts=opts,
                log_cb=log_cb,
                calls_lock=calls_lock,
                scoped_tools=scoped_tools,
            )
            if (
                not result.success
                and self._classify_failure(result) == FailureKind.CONTEXT_TOO_LARGE
                and config.agent_max_split_depth > 0
                and context_files
            ):
                await log_cb(
                    ctx.project_id,
                    f"[custom] context limit failure for {phase} {target}; retrying with split context",
                    "warning",
                )
            else:
                return result

        slices = self._split_context_pack(ctx, phase, target, prompt, context_files, hard, soft)
        await log_cb(
            ctx.project_id,
            f"[custom] split decision: phase={phase} target={target} "
            f"original_est_tokens={estimated} split_count={len(slices)} "
            f"slice_est_tokens={[estimate_input_tokens(prompt, s) for s in slices]}",
            "info",
        )
        cache_dir = ctx.staging_dir / "_model_slices" / phase
        cache_dir.mkdir(parents=True, exist_ok=True)
        slice_concurrency = 1 if state.adaptive_serial_generation else max(1, config.core_slice_concurrency)
        semaphore = asyncio.Semaphore(slice_concurrency)

        async def _run_slice(idx: int, slice_files: list[Path]) -> tuple[int, str | None, list[str]]:
            slice_id = f"{target}_slice_{idx}"
            cache_file = cache_dir / f"{self._safe_name(slice_id)}.md"
            if ctx.resume_state is not None and cache_file.exists() and cache_file.stat().st_size > 100:
                await log_cb(ctx.project_id, f"[custom] RESUME: skipping {phase} {slice_id} (slice cache exists)", "info")
                return idx, cache_file.read_text(encoding="utf-8", errors="replace"), []
            slice_prompt = (
                f"{prompt}\n\n"
                f"You are processing slice {idx} of {len(slices)} for target {target}. "
                "Use only this slice's evidence. Preserve exact names and mark any unresolved coverage as scanner-limited."
            )
            async with semaphore:
                result = await self._call_model_with_logging(
                    ctx,
                    state,
                    provider=provider,
                    phase=phase,
                    target=slice_id,
                    prompt=slice_prompt,
                    context_files=slice_files,
                    opts=opts,
                    log_cb=log_cb,
                    calls_lock=calls_lock,
                )
            if not result.success:
                failure_kind = self._classify_failure(result) or FailureKind.UNKNOWN
                if failure_kind != FailureKind.CONTEXT_TOO_LARGE:
                    return idx, None, [f"{slice_id}: {failure_kind.value}: {result.raw_error}"]
                smaller = (
                    self._split_context_pack(
                        ctx,
                        phase,
                        slice_id,
                        slice_prompt,
                        slice_files,
                        max(16_000, hard // 2),
                        max(8_000, soft // 2),
                    )
                    if config.agent_max_split_depth > 1
                    else [slice_files]
                )
                if len(smaller) > 1:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] retrying {phase} {slice_id} with {len(smaller)} smaller slices "
                        f"(failure_kind={failure_kind.value})",
                        "warning",
                    )
                    sub_outputs: list[str] = []
                    slice_failures: list[str] = []
                    for sub_idx, sub_files in enumerate(smaller, start=1):
                        async with semaphore:
                            retry_result = await self._call_model_with_logging(
                                ctx,
                                state,
                                provider=provider,
                                phase=phase,
                                target=f"{slice_id}_retry_{sub_idx}",
                                prompt=slice_prompt,
                                context_files=sub_files,
                                opts=opts.model_copy(update={"retries": 0}),
                                log_cb=log_cb,
                                calls_lock=calls_lock,
                                scoped_tools=scoped_tools,
                            )
                        if not retry_result.success:
                            retry_kind = self._classify_failure(retry_result) or FailureKind.UNKNOWN
                            slice_failures.append(
                                f"{slice_id}_retry_{sub_idx}: {retry_kind.value}: {retry_result.raw_error}"
                            )
                            continue
                        sub_outputs.append(retry_result.text.strip())
                    if not slice_failures and sub_outputs:
                        text = "\n\n---\n\n".join(sub_outputs)
                        cache_file.write_text(text, encoding="utf-8")
                        return idx, text, []
                    return idx, None, slice_failures
                return idx, None, [f"{slice_id}: {result.raw_error}"]
            cache_file.write_text(result.text.strip(), encoding="utf-8")
            return idx, result.text.strip(), []

        slice_results = await asyncio.gather(*[
            _run_slice(idx, slice_files)
            for idx, slice_files in enumerate(slices, start=1)
        ])
        outputs: list[str] = []
        failures: list[str] = []
        for _idx, text, slice_failures in sorted(slice_results, key=lambda item: item[0]):
            if slice_failures:
                failures.extend(slice_failures)
            if text:
                outputs.append(text)

        if failures:
            return ModelResult(
                success=False,
                text="\n\n---\n\n".join(outputs),
                model_name=getattr(provider, "model_name", ""),
                input_tokens=0,
                output_tokens=0,
                raw_error="; ".join(failures[:5]),
                metadata={"phase": phase, "target": target, "incomplete_slices": failures},
            )
        return ModelResult(
            success=True,
            text="\n\n---\n\n".join(outputs),
            model_name=getattr(provider, "model_name", ""),
            input_tokens=0,
            output_tokens=0,
            metadata={"phase": phase, "target": target, "slices": len(slices)},
        )

    async def _call_model_with_logging(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        *,
        provider: ModelProvider,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        opts: ModelOptions,
        log_cb: LogCallback,
        calls_lock: asyncio.Lock | None = None,
        scoped_tools: ScopedEvidenceTools | None = None,
    ):
        estimated = estimate_input_tokens(prompt, context_files)
        total_bytes = context_bytes(context_files)
        budget_key = self._doc_budget_key(target)
        if state.total_empty_outputs >= config.docgen_empty_outputs_per_run_limit:
            message = (
                "empty output run limit already exceeded before model call: "
                f"{state.total_empty_outputs}/{config.docgen_empty_outputs_per_run_limit}; target={target}"
            )
            await log_cb(ctx.project_id, f"[custom] {message}", "error")
            return self._failure_result(provider, phase, target, message, FailureKind.EMPTY_OUTPUT)
        if state.empty_output_counts.get(budget_key, 0) >= config.docgen_empty_outputs_per_doc_limit:
            message = (
                "empty output doc limit already exceeded before model call: "
                f"{budget_key} {state.empty_output_counts.get(budget_key, 0)}/"
                f"{config.docgen_empty_outputs_per_doc_limit}; target={target}"
            )
            await log_cb(ctx.project_id, f"[custom] {message}", "error")
            return self._failure_result(provider, phase, target, message, FailureKind.EMPTY_OUTPUT)

        budget = self._run_model_call_budget(state)
        current_calls = len(state.calls)
        if current_calls >= budget:
            message = (
                f"model call budget exceeded before {phase} {target}: "
                f"{current_calls}/{budget} calls; groups={len(state.facts_manifest.groups)} "
                f"docs={len(self._required_docs)}"
            )
            await log_cb(ctx.project_id, f"[custom] {message}", "error")
            return self._failure_result(provider, phase, target, message, FailureKind.BUDGET_EXCEEDED)

        await log_cb(
            ctx.project_id,
            f"[custom] model call start: phase={phase} target={target} split_id={target} "
            f"est_input_tokens={estimated} context_files={len(context_files)} context_bytes={total_bytes} "
            f"call_budget={current_calls + 1}/{budget}",
            "info",
        )
        self._write_model_input_snapshot(
            ctx,
            provider=provider,
            phase=phase,
            target=target,
            prompt=prompt,
            context_files=context_files,
            opts=opts,
            estimated_tokens=estimated,
            total_bytes=total_bytes,
            call_index=current_calls + 1,
        )
        if use_tools_for_core_generation(provider):
            result = await provider.complete_with_tools(
                _with_tool_instructions(prompt, ctx.staging_dir),
                ctx.staging_dir,
                opts,
                scoped_tools=scoped_tools,
            )
        else:
            result = await provider.complete(prompt, context_files, opts)
        failure_kind = self._classify_failure(result)
        if failure_kind is not None:
            raw_error = result.raw_error or (
                f"{failure_kind.value}: model returned no usable text"
                if failure_kind == FailureKind.EMPTY_OUTPUT
                else f"{failure_kind.value}: model call failed"
            )
            result = result.model_copy(
                update={
                    "success": False,
                    "raw_error": raw_error,
                    "metadata": {**result.metadata, "failure_kind": failure_kind.value},
                }
            )
            result = await self._record_failure_policy(
                ctx,
                state,
                target=target,
                result=result,
                kind=failure_kind,
                log_cb=log_cb,
            )
            if (
                not result.success
                and self.fallback_provider is not None
                and failure_kind.is_retryable_with_fallback
            ):
                await log_cb(
                    ctx.project_id,
                    f"[custom] primary failed ({failure_kind.value}); retrying with fallback "
                    f"{self._describe_provider(self.fallback_provider)}",
                    "warning",
                )
                if use_tools_for_core_generation(self.fallback_provider):
                    fallback_result = await self.fallback_provider.complete_with_tools(
                        _with_tool_instructions(prompt, ctx.staging_dir),
                        ctx.staging_dir,
                        opts,
                        scoped_tools=scoped_tools,
                    )
                else:
                    fallback_result = await self.fallback_provider.complete(prompt, context_files, opts)
                fallback_kind = self._classify_failure(fallback_result)
                if fallback_kind is None:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] fallback succeeded: {self._describe_provider(self.fallback_provider)} "
                        f"tokens={fallback_result.output_tokens}",
                        "info",
                    )
                    result = fallback_result
                else:
                    await log_cb(
                        ctx.project_id,
                        f"[custom] fallback also failed ({fallback_kind.value}): "
                        f"{self._describe_provider(self.fallback_provider)}",
                        "warning",
                    )
        call = ProviderCallLog(phase=phase, target=target, result=result)
        if calls_lock is None:
            state.calls.append(call)
        else:
            async with calls_lock:
                state.calls.append(call)
        await self._log_call_metrics(ctx, target, result, log_cb)
        return result

    def _write_model_input_snapshot(
        self,
        ctx: DocGenContext,
        *,
        provider: ModelProvider,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        opts: ModelOptions,
        estimated_tokens: int,
        total_bytes: int,
        call_index: int,
    ) -> None:
        """Persist the exact model-call inputs for offline review and no-AI tests."""

        snapshot_dir = (
            ctx.staging_dir
            / "_model_inputs"
            / self._safe_name(phase)
            / f"{call_index:03d}_{self._safe_name(target)}"
        )
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        context_entries: list[dict[str, object]] = []
        bundle_parts = [
            f"# Model Input Bundle: {phase} / {target}",
            "",
            "## Prompt",
            "",
            prompt.rstrip(),
            "",
            "## Context Files",
        ]
        for idx, path in enumerate(context_files, start=1):
            exists = path.exists()
            try:
                rel_path = path.relative_to(ctx.staging_dir).as_posix()
            except ValueError:
                rel_path = str(path)
            size = path.stat().st_size if exists else 0
            context_entries.append({
                "index": idx,
                "path": rel_path,
                "bytes": size,
                "exists": exists,
            })
            bundle_parts.extend(["", f"### {idx}. {rel_path}", ""])
            if exists:
                bundle_parts.append(path.read_text(encoding="utf-8", errors="replace").rstrip())
            else:
                bundle_parts.append("[missing context file]")

        (snapshot_dir / "prompt.md").write_text(prompt.rstrip() + "\n", encoding="utf-8")
        (snapshot_dir / "context_bundle.md").write_text("\n".join(bundle_parts).rstrip() + "\n", encoding="utf-8")
        manifest = {
            "phase": phase,
            "target": target,
            "provider": self._describe_provider(provider),
            "model": provider.model_name,
            "call_index": call_index,
            "estimated_input_tokens": estimated_tokens,
            "context_bytes": total_bytes,
            "context_file_count": len(context_files),
            "options": {
                "max_tokens": opts.max_tokens,
                "temperature": opts.temperature,
                "timeout_s": opts.timeout_s,
                "retries": opts.retries,
            },
            "context_files": context_entries,
        }
        (snapshot_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @staticmethod
    def _bounded_context_files(context_files: list[Path]) -> list[Path]:
        seen: set[Path] = set()
        bounded: list[Path] = []
        for path in context_files:
            if path.name in LARGE_CONTEXT_ARTIFACTS:
                continue
            if path in seen or not path.exists():
                continue
            seen.add(path)
            bounded.append(path)
        return bounded

    def _split_context_pack(
        self,
        ctx: DocGenContext,
        phase: str,
        target: str,
        prompt: str,
        context_files: list[Path],
        hard_tokens: int,
        soft_tokens: int,
    ) -> list[list[Path]]:
        if estimate_input_tokens(prompt, context_files) <= hard_tokens:
            return [context_files]
        if not context_files:
            return [context_files]

        existing = [path for path in context_files if path.exists()]
        if not existing:
            return [context_files]

        prompt_estimate = estimate_input_tokens(prompt, [])
        if prompt_estimate >= hard_tokens:
            return [[]]

        # Keep small/instructional context with every slice, but always reserve
        # room for evidence.  The previous splitter only split the single
        # largest file; if the fixed remainder still exceeded the hard ceiling,
        # Phase 3 produced one 300k+ token "slice".  This packer instead treats
        # the remaining files as evidence to page through.
        common_budget = min(
            max(16_000, int(soft_tokens * 0.75)),
            max(1, hard_tokens - 8_000),
        )
        common: list[Path] = []
        evidence: list[Path] = []
        for path in existing:
            candidate = [*common, path]
            if estimate_input_tokens(prompt, candidate) <= common_budget:
                common.append(path)
            else:
                evidence.append(path)

        if not evidence:
            return [common]

        common_estimate = estimate_input_tokens(prompt, common)
        if common_estimate >= hard_tokens:
            common = []
            common_estimate = estimate_input_tokens(prompt, common)

        target_tokens = min(soft_tokens, hard_tokens)
        available_tokens = target_tokens - common_estimate - 512
        if available_tokens < 2_000:
            available_tokens = hard_tokens - common_estimate - 512
        available_bytes = max(512, available_tokens * 4)

        slices: list[list[Path]] = []
        for path in evidence:
            for chunk in self._split_file(ctx, phase, target, path, available_bytes):
                pack = [*common, chunk]
                if estimate_input_tokens(prompt, pack) <= hard_tokens:
                    slices.append(pack)
                else:
                    # Last-resort fallback: drop replicated common context for
                    # this chunk instead of emitting an over-budget call.
                    for smaller in self._split_file(
                        ctx,
                        phase,
                        f"{target}_{path.stem}",
                        chunk,
                        max(512, (hard_tokens - prompt_estimate - 512) * 4),
                    ):
                        slices.append([smaller])

        return slices or [common]

    @staticmethod
    def _split_file(ctx: DocGenContext, phase: str, target: str, path: Path, max_bytes: int) -> list[Path]:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return [path]
        text = path.read_text(encoding="utf-8", errors="replace")
        split_dir = ctx.staging_dir / "_context_slices" / phase
        split_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[Path] = []
        start = 0
        idx = 1
        overlap = min(2_000, max_bytes // 10)
        while start < len(text):
            end = min(len(text), start + max_bytes)
            newline = text.rfind("\n## ", start, end)
            if newline > start + max_bytes // 3:
                end = newline
            body = text[start:end]
            chunk = split_dir / f"{CustomHarnessProvider._safe_name(target)}_{path.stem}_slice_{idx}.md"
            chunk.write_text(
                f"# Context slice {idx} from {path.name}\n\n{body}",
                encoding="utf-8",
            )
            chunks.append(chunk)
            if end >= len(text):
                break
            start = max(0, end - overlap)
            idx += 1
        return chunks

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)

    def _phase4_cleanup(self, ctx: DocGenContext, state: HarnessRunState) -> None:
        archived_engineering_facts = self._archive_engineering_artifacts(ctx, state)
        index = {
            "project_id": ctx.project_id,
            "platform": state.facts_manifest.platform,
            "groups": state.facts_manifest.groups,
            "unit_count": state.facts_manifest.unit_count,
            "canonical_unit_label": state.facts_manifest.canonical_unit_label,
            "docs": self._required_docs,
            "engineering_facts": archived_engineering_facts,
            "provider": "custom",
            "model": self.model_provider.model_name,
            "write_model": self.write_provider.model_name,
            "generated_at": time.time(),
        }
        if state.warnings:
            index["warnings"] = state.warnings
            index["warnings_count"] = len(state.warnings)
        (ctx.staging_dir / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        (ctx.staging_dir / "provider_calls.json").write_text(
            json.dumps([c.model_dump() for c in state.calls], indent=2),
            encoding="utf-8",
        )

        for directory in ("_preprocess", "_analysis"):
            target = ctx.staging_dir / directory
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

        logs_dir = Path(ctx.logs_abs)
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        (logs_dir / f"run_{ctx.project_id}_{ts}.log").write_text(
            f"project_id: {ctx.project_id}\n"
            f"provider: custom\n"
            f"model: {self.model_provider.model_name}\n"
            f"write_model: {self.write_provider.model_name}\n"
            f"unit_count: {state.facts_manifest.unit_count}\n"
            f"input_tokens: {state.input_tokens}\n"
            f"output_tokens: {state.output_tokens}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _archive_engineering_artifacts(ctx: DocGenContext, state: HarnessRunState) -> list[str]:
        facts_dir = ctx.staging_dir / "_facts"
        archived: list[str] = []
        for name in sorted(state.engineering_fact_artifacts):
            source = ctx.staging_dir / name
            if not source.exists() or not source.is_file():
                continue
            facts_dir.mkdir(parents=True, exist_ok=True)
            target = facts_dir / name
            if target.exists():
                target.unlink()
            shutil.move(str(source), str(target))
            archived.append(target.relative_to(ctx.staging_dir).as_posix())
        return archived

    @staticmethod
    def _critic_prompt(state: HarnessRunState, family: str, doc_names: list[str]) -> str:
        return f"""\
You are a documentation critic. Compare the supplied docs against the facts manifest,
scorecard, investigation evidence, and reference docs. Return JSON only.

Required JSON shape:
{{"expansions":[{{"doc_name":"02_COMPONENT_INVENTORY","heading":"Evidence gap","markdown":"...","units":["Exact Unit"]}}]}}

Rules:
- Only expand weak or missing factual sections.
- Use exact canonical unit names from the facts manifest.
- Do not introduce counts that conflict with {state.facts_manifest.unit_count} {state.facts_manifest.canonical_unit_label}.
- Markdown must cite concrete investigation snippets or scanner observations.
- If nothing needs expansion, return {{"expansions":[]}}.

Doc family: {family}
Allowed docs:
{chr(10).join(f"- {doc}" for doc in doc_names)}
"""

    def _apply_validated_expansions(
        self,
        ctx: DocGenContext,
        state: HarnessRunState,
        text: str,
        allowed_docs: set[str],
    ) -> int:
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```").strip()
            raw = raw.removesuffix("```").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        expansions = data.get("expansions")
        if not isinstance(expansions, list):
            return 0
        unit_names = state.facts_manifest.unit_names()
        applied = 0
        for item in expansions:
            if not isinstance(item, dict):
                continue
            doc_name = str(item.get("doc_name") or "")
            if doc_name not in allowed_docs:
                continue
            path = ctx.staging_dir / f"{doc_name}.md"
            if not path.exists():
                continue
            markdown = str(item.get("markdown") or "").strip()
            heading = str(item.get("heading") or "Critic Expansion").strip()
            units = item.get("units") or []
            if not markdown or len(markdown) < 80 or not isinstance(units, list):
                continue
            if any(str(unit) not in unit_names for unit in units):
                continue
            if units and not any(str(unit) in markdown for unit in units):
                continue
            if str(state.facts_manifest.unit_count) not in markdown and "canonical inventory" in markdown.lower():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            path.write_text(
                f"{content.rstrip()}\n\n## {heading}\n\n{markdown}\n",
                encoding="utf-8",
            )
            applied += 1
        return applied

    @staticmethod
    def _unit_catalog_markdown(state: HarnessRunState) -> str:
        lines = ["| Unit | Group | Type | Components |", "|------|-------|------|------------|"]
        for unit in state.facts_manifest.units:
            lines.append(
                f"| {unit.name} | {unit.group} | {unit.unit_type} | {unit.component_count} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _load_investigation_artifact(investigation_dir: Path, chunk_name: str) -> InvestigationReport | None:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", chunk_name)
        path = investigation_dir / f"{safe_name}.json"
        if not path.exists():
            return None
        try:
            return InvestigationReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _merge_investigation_reports(
        group: str,
        reports: list[InvestigationReport],
    ) -> InvestigationReport:
        evidence = []
        skipped = {}
        source_groups = []
        tool_rounds = 0
        invalid_retries = 0
        input_tokens = 0
        output_tokens = 0
        seen = set()
        for report in reports:
            source_groups.extend(report.source_groups or [report.group])
            for ev in report.evidence:
                key = (
                    ev.unit_name,
                    ev.category,
                    ev.source_file,
                    ev.line_range,
                    ev.chunk_ref,
                    ev.snippet,
                )
                if key not in seen:
                    seen.add(key)
                    evidence.append(ev)
            skipped.update(report.skipped_units)
            tool_rounds += report.tool_rounds
            invalid_retries += report.invalid_retries
            input_tokens += report.input_tokens
            output_tokens += report.output_tokens
        return InvestigationReport(
            group=group,
            source_groups=sorted(set(source_groups)),
            evidence=evidence,
            skipped_units=dict(sorted(skipped.items())),
            tool_rounds=tool_rounds,
            invalid_retries=invalid_retries,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _units_for_chunk(chunk_file: Path, group_units: list[str]) -> list[str]:
        try:
            text = chunk_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return group_units
        visible = [name for name in group_units if name in text]
        return visible or group_units

    @staticmethod
    def _investigation_context(ctx: DocGenContext, group: str) -> list[Path]:
        investigation_dir = ctx.staging_dir / "_investigation"
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in group)
        files = [investigation_dir / f"{safe}.md", investigation_dir / "_summary.md"]
        return [path for path in files if path.exists()]

    @staticmethod
    def _provider_options(provider: ModelProvider) -> dict:
        provider_name = getattr(provider, "provider_name", "")
        if provider_name == "ollama":
            return {"options": {"num_ctx": 32768}}
        return {}

    @staticmethod
    def _default_evidence_categories() -> list[str]:
        return [
            "inventory",
            "components",
            "data_flow",
            "transformation",
            "business_rules",
            "external_systems",
            "error_handling",
            "environment_config",
            "risk_flags",
            "dependencies",
            "security",
            "operations",
        ]

    @staticmethod
    def _validate_agent_markdown(text: str, required_units: list[str], min_bytes: int = 500) -> dict:
        body = text.strip()
        errors: list[str] = []
        if len(body.encode("utf-8")) < min_bytes:
            errors.append(f"artifact too shallow: {len(body.encode('utf-8'))} bytes < {min_bytes}")
        missing = [unit for unit in required_units if unit not in body]
        if missing:
            errors.append(f"artifact does not mention assigned units: {', '.join(missing[:20])}")
        if not re.search(r"^#{1,4}\s+\S+", body, re.MULTILINE):
            errors.append("artifact has no markdown headings")
        return {"ok": not errors, "errors": errors, "missing_units": missing}

    @staticmethod
    def _existing(directory: Path, names: list[str]) -> list[Path]:
        return [directory / name for name in names if (directory / name).exists()]

    def _phase2_shared_context(self, ctx: DocGenContext, facts_path: Path) -> list[Path]:
        preprocess_dir = ctx.staging_dir / "_preprocess"
        pack_dir = ctx.staging_dir / "_assessment" / "prompt_packs"
        files: list[Path] = []
        source_coverage = preprocess_dir / "_source_coverage.json"
        if facts_path.exists():
            files.append(facts_path)
        if not source_coverage.exists():
            files.extend(self._engineering_fact_context(ctx))
        quick_scan = ctx.staging_dir / "_assessment" / "quick_scan.md"
        if quick_scan.exists():
            files.append(quick_scan)
        if source_coverage.exists():
            files.append(source_coverage)
        analysis_pack = pack_dir / "_analysis_prompt_pack.md"
        if analysis_pack.exists():
            files.append(analysis_pack)
        elif ctx.analysis_guide_file == "_analysis_prompts*.md":
            files.extend(sorted(preprocess_dir.glob("_analysis_prompts*.md")))
        else:
            guide = preprocess_dir / ctx.analysis_guide_file
            if guide.exists():
                files.append(guide)
            else:
                files.extend(sorted(preprocess_dir.glob("_analysis_prompts*.md")))
        return files

    @staticmethod
    def _group_units(state: HarnessRunState, group: str) -> list[str]:
        units = [
            u.name for u in state.facts_manifest.units
            if u.group == group or group.startswith(f"{u.group}_part")
        ]
        if not units:
            units = [u.name for u in state.facts_manifest.units if u.group == group]
        return units

    @staticmethod
    def _phase2_group_chunks(group_file: Path, group_units: list[str], analysis_dir: Path) -> list[Path]:
        if not group_file.exists() or group_file.stat().st_size <= 70_000:
            return [group_file]

        text = group_file.read_text(encoding="utf-8", errors="replace")
        chunk_dir = analysis_dir / "_chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_size = 55_000
        overlap = 2_500
        chunks: list[Path] = []
        start = 0
        idx = 1
        while start < len(text):
            end = min(len(text), start + chunk_size)
            body = text[start:end]
            units_in_chunk = [name for name in group_units if name in body]
            header = (
                f"# Chunk {idx} from {group_file.name}\n\n"
                "Target canonical units visible in this chunk:\n"
                + "\n".join(f"- {name}" for name in units_in_chunk or group_units)
                + "\n\n---\n\n"
            )
            path = chunk_dir / f"{group_file.stem}_chunk_{idx}.md"
            path.write_text(header + body, encoding="utf-8")
            chunks.append(path)
            if end >= len(text):
                break
            start = max(0, end - overlap)
            idx += 1
        return chunks

    def _phase3_context_files(self, ctx: DocGenContext, doc_name: str) -> list[Path]:
        preprocess_dir = ctx.staging_dir / "_preprocess"
        analysis_dir = ctx.staging_dir / "_analysis"
        pack_dir = ctx.staging_dir / "_assessment" / "prompt_packs"
        bundle = self._doc_bundle_map.get(doc_name, "core")
        files: list[Path] = []
        obligations = ctx.staging_dir / "documentation_obligations.json"
        if obligations.exists():
            files.append(obligations)
        prompt_pack = pack_dir / f"_phase3_prompt_pack_{bundle}.md"
        if prompt_pack.exists():
            files.append(prompt_pack)
        else:
            phase3_guide = preprocess_dir / "_phase3_guide.md"
            if phase3_guide.exists():
                files.append(phase3_guide)
            doc_templates = preprocess_dir / "_doc_templates.md"
            if doc_templates.exists():
                files.append(doc_templates)
        doc_pack = analysis_dir / f"_doc_pack_{doc_name}.md"
        if doc_pack.exists():
            files.append(doc_pack)
        bundle_path = analysis_dir / f"_bundle_{bundle}.md"
        if bundle_path.exists() and not doc_pack.exists():
            files.append(bundle_path)
        manifest = analysis_dir / "_doc_pack_manifest.json"
        if manifest.exists():
            files.append(manifest)
        source_coverage = preprocess_dir / "_source_coverage.json"
        if not source_coverage.exists():
            files.extend(self._engineering_fact_context(ctx, doc_name))
        if prompt_pack.exists():
            return files
        if source_coverage.exists():
            files.append(source_coverage)
        if bundle == "core":
            files.extend(sorted(preprocess_dir.glob("_ref_core*.md")))
        elif bundle == "business_ops":
            files.extend(sorted(preprocess_dir.glob("_ref_business_ops*.md")))
        else:
            files.extend(sorted(preprocess_dir.glob("_ref_patterns*.md")))
            files.extend(sorted(preprocess_dir.glob("_ref_user*.md")))
        return files

    @staticmethod
    def _engineering_fact_context(ctx: DocGenContext, doc_name: str | None = None) -> list[Path]:
        if doc_name is None:
            # Phase 2 analysis: lightweight markdown only — large JSON indexes blow the context window
            names = ["engineering_facts.md", "dependency_map.md", "known_risks.md", "config_runtime_map.md"]
        else:
            names = ["engineering_facts.md", "entity_index.csv", "usage_index.csv"]
            if doc_name in {"01_ARCHITECTURE_MAP", "02_COMPONENT_INVENTORY"}:
                names.extend(["architecture_map.md", "dependency_map.md"])
            elif doc_name == "03_DATA_FLOWS_AND_LINEAGE":
                # CSV versions are more readable and cover the same data as the JSON base — drop JSON duplicates
                names = ["engineering_facts.md", "data_flow_map.md", "entity_index.csv", "usage_index.csv"]
            elif doc_name == "08_CHANGE_PLAYBOOK":
                names.extend(["change_impact_matrix.md"])
            elif doc_name == "05_RUNTIME_CONFIG_AND_EXTERNALS":
                names.extend(["api_surface.md", "config_runtime_map.md"])
            elif doc_name == "06_OPERATIONS_TESTING_AND_DEBUGGING":
                names.extend(["test_coverage_map.md", "known_risks.md"])
            else:
                names.extend(["dependency_map.md", "known_risks.md", "config_runtime_map.md"])
        return [ctx.staging_dir / name for name in names if (ctx.staging_dir / name).exists()]

    async def _log_call_metrics(
        self,
        ctx: DocGenContext,
        target: str,
        result,
        log_cb: LogCallback,
    ) -> None:
        meta = result.metadata or {}
        estimated = meta.get("estimated_input_tokens") or result.input_tokens
        configured = (meta.get("provider_options") or {}).get("options", {}).get("num_ctx") or meta.get("configured_context")
        context_bytes = meta.get("context_bytes", 0)
        finish_reason = meta.get("finish_reason")
        finish = f" finish={finish_reason}" if finish_reason else ""
        failure = f" failure_kind={meta.get('failure_kind')}" if meta.get("failure_kind") else ""
        await log_cb(
            ctx.project_id,
            f"[custom] call metrics: {target} model={result.model_name} "
            f"in~{estimated} out={result.output_tokens} context_bytes={context_bytes} "
            f"ctx={configured or 'default'} latency_ms={result.latency_ms}{finish}{failure}",
            "info",
        )


def build_custom_provider(
    model_str: str,
    write_model_str: str | None = None,
    investigation_model_str: str | None = None,
    fallback_model_str: str | None = None,
    instruction_pack_dir: str | None = None,
    plugin_pack_dir: Path | None = None,
) -> CustomHarnessProvider:
    def _build_secondary(secondary_str: str | None, *, skip_if_same: bool = True) -> ModelProvider | None:
        if not secondary_str or (skip_if_same and secondary_str == model_str):
            return None
        same_provider = secondary_str.partition("/")[0] == model_str.partition("/")[0]
        return build_model_provider(
            secondary_str,
            api_key=config.doc_generation_api_key if same_provider else None,
            base_url=config.doc_generation_base_url if same_provider else None,
            ollama_base_url=config.ollama_base_url,
        )

    from mcp_server.services.pack_loader import PackLoader
    resolved_pack_dir = instruction_pack_dir or config.instruction_pack_dir
    pack_dir = config.project_root / resolved_pack_dir
    pack = PackLoader(pack_dir).load() if pack_dir.exists() else None

    main = build_model_provider(
        model_str,
        api_key=config.doc_generation_api_key,
        base_url=config.doc_generation_base_url,
        ollama_base_url=config.ollama_base_url,
    )
    return CustomHarnessProvider(
        main,
        _build_secondary(write_model_str),
        _build_secondary(investigation_model_str),
        _build_secondary(fallback_model_str, skip_if_same=False),
        pack=pack,
        plugin_pack_dir=plugin_pack_dir,
    )
