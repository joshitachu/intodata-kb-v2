"""HarnessHelpers Protocol and implementation — breaks the circular import between
phase implementations and CustomHarnessProvider.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mcp_server.providers.custom.harness import ToolPolicy
    from mcp_server.providers.custom.models import CoverageReport, ExecutionPlan
    from mcp_server.providers.custom.providers import ModelProvider
    from mcp_server.services.pack_loader import InstructionPack
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import HarnessRunState


@runtime_checkable
class HarnessHelpers(Protocol):
    """Subset of CustomHarnessProvider that phase bodies depend on.

    Listing only the methods/properties that phase implementations call directly.
    The Protocol breaks the circular import: phase modules import HarnessHelpers,
    not CustomHarnessProvider.
    """

    # ── Provider references ──────────────────────────────────────────────────

    @property
    def model_provider(self) -> "ModelProvider":
        ...

    @property
    def write_provider(self) -> "ModelProvider":
        ...

    @property
    def investigation_provider(self) -> "ModelProvider":
        ...

    @property
    def fallback_provider(self) -> "ModelProvider | None":
        ...

    # ── Pack / plugin configuration ──────────────────────────────────────────

    @property
    def pack(self) -> "InstructionPack | None":
        ...

    @property
    def plugin_pack_dir(self) -> "Path | None":
        ...

    @property
    def required_docs(self) -> "list[str]":
        ...

    # ── Phase/tool policy ────────────────────────────────────────────────────

    def phase_policy(self, phase: str) -> str:
        """Return the execution policy string ('run', 'skip', 'bounded', …) for a phase."""
        ...

    def tool_policy(self, provider: "ModelProvider", phase: str) -> "ToolPolicy":
        """Return the ToolPolicy for a given provider/phase combination."""
        ...

    @property
    def plugin_mode(self) -> bool:
        """True when a plugin pack directory is configured."""
        ...

    @property
    def pack_contract_mode(self) -> bool:
        """True when both investigation and analysis are skipped (pack-contract path)."""
        ...

    # ── Context file helpers ─────────────────────────────────────────────────

    def phase2_shared_context(self, ctx: object, facts_path: "Path") -> "list[Path]":
        """Return the list of shared context files for Phase 2 analysis calls."""
        ...

    def phase3_context_files(self, ctx: object, doc_name: str) -> "list[Path]":
        """Return the list of context files for a Phase 3 document generation call."""
        ...


class HarnessHelpersImpl:  # noqa: PLR0904 — intentionally wide delegation surface
    """Thin delegation wrapper that satisfies HarnessHelpers by forwarding to a provider.

    Phase objects receive this at construction time; they never import
    CustomHarnessProvider directly, keeping the dependency graph acyclic.
    """

    def __init__(self, provider: object) -> None:
        self._p = provider

    # ── Provider references ──────────────────────────────────────────────────

    @property
    def model_provider(self) -> "ModelProvider":
        return self._p.model_provider  # type: ignore[attr-defined]

    @property
    def write_provider(self) -> "ModelProvider":
        return self._p.write_provider  # type: ignore[attr-defined]

    @property
    def investigation_provider(self) -> "ModelProvider":
        return self._p.investigation_provider  # type: ignore[attr-defined]

    @property
    def fallback_provider(self) -> "ModelProvider | None":
        return self._p.fallback_provider  # type: ignore[attr-defined]

    # ── Pack / plugin configuration ──────────────────────────────────────────

    @property
    def pack(self) -> "InstructionPack | None":
        return self._p.pack  # type: ignore[attr-defined]

    @property
    def plugin_pack_dir(self) -> "Path | None":
        return self._p.plugin_pack_dir  # type: ignore[attr-defined]

    @property
    def required_docs(self) -> "list[str]":
        return self._p._required_docs  # type: ignore[attr-defined]

    # ── Phase/tool policy ────────────────────────────────────────────────────

    def phase_policy(self, phase: str) -> str:
        return self._p._phase_policy(phase)  # type: ignore[attr-defined]

    def tool_policy(self, provider: "ModelProvider", phase: str) -> "ToolPolicy":
        return self._p._tool_policy(provider, phase)  # type: ignore[attr-defined]

    @property
    def plugin_mode(self) -> bool:
        return self._p._plugin_mode  # type: ignore[attr-defined]

    @property
    def pack_contract_mode(self) -> bool:
        return self._p._pack_contract_mode  # type: ignore[attr-defined]

    # ── Context file helpers ─────────────────────────────────────────────────

    def phase2_shared_context(self, ctx: object, facts_path: "Path") -> "list[Path]":
        return self._p._phase2_shared_context(ctx, facts_path)  # type: ignore[attr-defined]

    def phase3_context_files(self, ctx: object, doc_name: str) -> "list[Path]":
        return self._p._phase3_context_files(ctx, doc_name)  # type: ignore[attr-defined]

    # ── Phase 1 helpers ──────────────────────────────────────────────────────

    def group_units(self, state: "HarnessRunState", group: str) -> "list[str]":
        return self._p._group_units(state, group)  # type: ignore[attr-defined]

    def phase2_group_chunks(self, group_file: "Path", group_units: "list[str]", analysis_dir: "Path") -> "list[Path]":
        return self._p._phase2_group_chunks(group_file, group_units, analysis_dir)  # type: ignore[attr-defined]

    def units_for_chunk(self, chunk_file: "Path", group_units: "list[str]") -> "list[str]":
        return self._p._units_for_chunk(chunk_file, group_units)  # type: ignore[attr-defined]

    def load_investigation_artifact(self, investigation_dir: "Path", chunk_name: str) -> object:
        return self._p._load_investigation_artifact(investigation_dir, chunk_name)  # type: ignore[attr-defined]

    def chunk_has_pack_relevant_content(self, chunk_text: str) -> bool:
        return self._p._chunk_has_pack_relevant_content(chunk_text)  # type: ignore[attr-defined]

    def merge_investigation_reports(self, group: str, reports: list) -> object:
        return self._p._merge_investigation_reports(group, reports)  # type: ignore[attr-defined]

    def provider_options(self, provider: "ModelProvider") -> dict:
        return self._p._provider_options(provider)  # type: ignore[attr-defined]

    def log_call_metrics(self, ctx: "DocGenContext", target: str, result: object, log_cb: "LogCallback"):
        return self._p._log_call_metrics(ctx, target, result, log_cb)  # type: ignore[attr-defined]

    # ── Phase 2 helpers ──────────────────────────────────────────────────────

    def phase2_analysis(self, ctx: "DocGenContext", state: "HarnessRunState", log_cb: "LogCallback"):
        return self._p._phase2_analysis(ctx, state, log_cb)  # type: ignore[attr-defined]

    def phase2_analysis_direct(self, ctx: "DocGenContext", state: "HarnessRunState", log_cb: "LogCallback"):
        return self._p._phase2_analysis_direct(ctx, state, log_cb)  # type: ignore[attr-defined]

    def phase2_analysis_dag_tiered(self, ctx: "DocGenContext", state: "HarnessRunState", plan: "ExecutionPlan", log_cb: "LogCallback"):
        return self._p._phase2_analysis_dag_tiered(ctx, state, plan, log_cb)  # type: ignore[attr-defined]

    def phase2_pack_contract_records(self, ctx: "DocGenContext", state: "HarnessRunState") -> None:
        return self._p._phase2_pack_contract_records(ctx, state)  # type: ignore[attr-defined]

    def phase2_group_agent(self, ctx: "DocGenContext", state: "HarnessRunState", runtime: object, group: str, shared_context: "list[Path]", analysis_dir: "Path", preprocess_dir: "Path", log_cb: "LogCallback", phase_name: str = "phase2") -> object:
        return self._p._phase2_group_agent(ctx, state, runtime, group, shared_context, analysis_dir, preprocess_dir, log_cb, phase_name)  # type: ignore[attr-defined]

    def phase2_targeted_repair(self, ctx: "DocGenContext", state: "HarnessRunState", report: "CoverageReport", log_cb: "LogCallback"):
        return self._p._phase2_targeted_repair(ctx, state, report, log_cb)  # type: ignore[attr-defined]

    def write_phase2_coverage_record(self, ctx: "DocGenContext", group: str, units: "list[str]", analysis_path: "Path", *, status: str = "completed") -> None:
        return self._p._write_phase2_coverage_record(ctx, group, units, analysis_path, status=status)  # type: ignore[attr-defined]

    def record_dag_node_result(self, ctx: "DocGenContext", node_id: str, status: str, *, artifacts: "list[str] | None" = None, validation: str = "", error: "str | None" = None) -> None:
        return self._p._record_dag_node_result(ctx, node_id, status, artifacts=artifacts, validation=validation, error=error)  # type: ignore[attr-defined]

    # ── Phase 2.6 scorecard helpers ──────────────────────────────────────────

    def reduce_split_scorecard(self, ctx: "DocGenContext", state: "HarnessRunState", slice_text: str, scorecard_pack: "Path", log_cb: "LogCallback"):
        return self._p._reduce_split_scorecard(ctx, state, slice_text, scorecard_pack, log_cb)  # type: ignore[attr-defined]

    def validate_agent_markdown(self, text: str, required_units: "list[str]", min_bytes: int = 500) -> dict:
        return self._p._validate_agent_markdown(text, required_units, min_bytes)  # type: ignore[attr-defined]

    def default_evidence_categories(self) -> "list[str]":
        return self._p._default_evidence_categories()  # type: ignore[attr-defined]

    def existing(self, directory: "Path", names: "list[str]") -> "list[Path]":
        return self._p._existing(directory, names)  # type: ignore[attr-defined]

    # ── Phase 2.8 bundles helpers ────────────────────────────────────────────

    def phase28_bundles(self, ctx: "DocGenContext", facts: object) -> None:
        return self._p._phase28_bundles(ctx, facts)  # type: ignore[attr-defined]

    def write_documentation_obligations(self, ctx: "DocGenContext", facts: object) -> None:
        return self._p._write_documentation_obligations(ctx, facts)  # type: ignore[attr-defined]

    # ── Phase 3 helpers ──────────────────────────────────────────────────────

    def phase3_docs(self, ctx: "DocGenContext", state: "HarnessRunState", log_cb: "LogCallback"):
        return self._p._phase3_docs(ctx, state, log_cb)  # type: ignore[attr-defined]

    def phase3_targeted_repair(self, ctx: "DocGenContext", state: "HarnessRunState", report: "CoverageReport", log_cb: "LogCallback"):
        return self._p._phase3_targeted_repair(ctx, state, report, log_cb)  # type: ignore[attr-defined]

    def phase36_consistency(self, ctx: "DocGenContext", state: "HarnessRunState", log_cb: "LogCallback"):
        return self._p._phase36_consistency(ctx, state, log_cb)  # type: ignore[attr-defined]

    def reconcile_sql_usage_classification(self, ctx: "DocGenContext") -> int:
        return self._p._reconcile_sql_usage_classification(ctx)  # type: ignore[attr-defined]

    def validate_sql_usage_publication(self, ctx: "DocGenContext") -> "list[str]":
        return self._p._validate_sql_usage_publication(ctx)  # type: ignore[attr-defined]

    def write_phase_costs(self, staging_dir: "Path", state: "HarnessRunState", log_cb: "LogCallback") -> None:
        return self._p._write_phase_costs(staging_dir, state, log_cb)  # type: ignore[attr-defined]

    # ── Phase 4 helpers ──────────────────────────────────────────────────────

    def enterprise_gate_report(self, ctx: "DocGenContext", state: "HarnessRunState", provenance: object = None) -> dict:
        return self._p._enterprise_gate_report(ctx, state, provenance)  # type: ignore[attr-defined]

    def write_audit_bundle(self, ctx: "DocGenContext", state: "HarnessRunState", gate_report: dict):
        return self._p._write_audit_bundle(ctx, state, gate_report)  # type: ignore[attr-defined]

    def archive_engineering_artifacts(self, ctx: "DocGenContext", state: "HarnessRunState") -> "list[str]":
        return self._p._archive_engineering_artifacts(ctx, state)  # type: ignore[attr-defined]

    # ── Required docs ────────────────────────────────────────────────────────

    @property
    def required_docs_list(self) -> "list[str]":
        """The live list of required docs on the provider (mutable)."""
        return self._p._required_docs  # type: ignore[attr-defined]

    @required_docs_list.setter
    def required_docs_list(self, value: "list[str]") -> None:
        self._p._required_docs = value  # type: ignore[attr-defined]

    # ── Investigation policy ─────────────────────────────────────────────────

    @property
    def investigation_provider(self) -> "ModelProvider":
        return self._p.investigation_provider  # type: ignore[attr-defined]

    def provider_for_phase(self, phase: str) -> "ModelProvider":
        return self._p._provider_for_phase(phase)  # type: ignore[attr-defined]

    def concurrency_for_phase(self, phase: str, default: int) -> int:
        return self._p._concurrency_for_phase(phase, default)  # type: ignore[attr-defined]
