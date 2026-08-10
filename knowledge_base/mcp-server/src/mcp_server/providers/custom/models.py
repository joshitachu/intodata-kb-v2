"""Typed models for the custom documentation-generation harness."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.providers.custom.pipeline.warning_collector import WarningCollector


class UnitFact(BaseModel):
    """One canonical scanner unit: Talend job, Workato recipe/function, etc."""

    name: str
    display_name: str = ""
    path: str = ""
    unit_type: str = "unit"
    group: str = "Global"
    logical_group: str = ""
    source_root: str = ""
    component_count: int = 0
    component_types: dict[str, int] = Field(default_factory=dict)
    key_systems: list[str] = Field(default_factory=list)


class FactsManifest(BaseModel):
    """Source-of-truth inventory for one generation run."""

    project_id: str
    platform: str
    canonical_unit_label: str
    unit_count: int
    groups: dict[str, int] = Field(default_factory=dict)
    units: list[UnitFact] = Field(default_factory=list)
    component_counts: dict[str, int] = Field(default_factory=dict)
    key_systems: list[str] = Field(default_factory=list)
    scan_warnings: list[dict[str, Any]] = Field(default_factory=list)
    raw_source_audit: dict[str, Any] = Field(default_factory=dict)

    def unit_names(self) -> set[str]:
        return {u.name for u in self.units}

    def write(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


class CoverageReport(BaseModel):
    """Coverage validation outcome against the facts manifest."""

    ok: bool
    covered_units: list[str] = Field(default_factory=list)
    covered_unit_ids: list[str] = Field(default_factory=list)
    skipped_units: dict[str, str] = Field(default_factory=dict)
    skipped_unit_ids: dict[str, str] = Field(default_factory=dict)
    missing_units: list[str] = Field(default_factory=list)
    missing_unit_ids: list[str] = Field(default_factory=list)
    coverage_count: int = 0
    expected_count: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ModelOptions(BaseModel):
    max_tokens: int = 4096
    temperature: float = 0.1
    timeout_s: float = 180.0
    retries: int = 3
    provider_options: dict[str, Any] = Field(default_factory=dict)
    prefill: str | None = None
    json_mode: bool = False  # request native JSON enforcement from providers that support it
    tool_input_token_cap: int | None = None
    tool_phase: str | None = None
    tool_target: str | None = None
    system_prompt: str | None = None  # static shared context; each provider delivers this natively


class ModelResult(BaseModel):
    success: bool
    text: str = ""
    model_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    latency_ms: int = 0
    retries: int = 0
    raw_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderCallLog(BaseModel):
    phase: str
    target: str
    result: ModelResult


class InvestigationEvidence(BaseModel):
    """One validated investigator finding tied to a canonical unit and source."""

    unit_name: str
    category: str
    source_file: str
    line_range: str | None = None
    chunk_ref: str | None = None
    snippet: str | None = None
    observation: str


class InvestigationReport(BaseModel):
    """Validated evidence collected before Phase 2 analysis."""

    group: str
    source_groups: list[str] = Field(default_factory=list)
    evidence: list[InvestigationEvidence] = Field(default_factory=list)
    skipped_units: dict[str, str] = Field(default_factory=dict)
    tool_rounds: int = 0
    invalid_retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class HarnessRunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    project_id: str
    facts_manifest: FactsManifest
    calls: list[ProviderCallLog] = Field(default_factory=list)
    investigation_reports: list[InvestigationReport] = Field(default_factory=list)
    engineering_fact_artifacts: list[str] = Field(default_factory=list)
    empty_output_counts: dict[str, int] = Field(default_factory=dict)
    total_empty_outputs: int = 0
    adaptive_serial_generation: bool = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warning_collector: WarningCollector = Field(default_factory=WarningCollector)
    thin_bundle_groups: list[str] = Field(default_factory=list)
    # Salvage-first bookkeeping: groups whose Phase 2 degraded to a partial
    # stub, and docs that shipped as stubs / truncated after a Phase 3 failure.
    partial_groups: list[str] = Field(default_factory=list)
    failed_docs: list[dict] = Field(default_factory=list)
    # Call budget computed from the actual bin-packed work plan at run start
    # (P0.1/R2.1). ``planned_call_budget`` is the plan SUM -- a reservation
    # guide for reporting, never the enforcement ceiling (the verified
    # Assetcheck failure: the run blocked Phase 3 at plan=179 while cap=250
    # had ~71 calls unused). The enforced ceiling is ``docgen_max_run_calls``;
    # ``planned_phase_floors`` reserves guaranteed call counts for late phases
    # (Phase 3 docs, business topology) so earlier-phase overruns cannot
    # starve them. ``floor_class_calls`` counts reserved calls per budget
    # class ("phase3" / "business_topology" / "shared") to track floor use.
    planned_call_budget: int | None = Field(default=None, exclude=True)
    planned_phase1_tasks: int | None = Field(default=None, exclude=True)
    planned_phase2_tasks: int | None = Field(default=None, exclude=True)
    planned_phase_floors: dict[str, int] = Field(default_factory=dict, exclude=True)
    floor_class_calls: dict[str, int] = Field(default_factory=dict, exclude=True)
    # Provider calls run concurrently. These excluded runtime-only fields make
    # call/token budget reservations atomic without changing persisted run data.
    budget_lock: asyncio.Lock = Field(default_factory=asyncio.Lock, exclude=True)
    pending_model_calls: int = Field(default=0, exclude=True)
    pending_input_tokens: int = Field(default=0, exclude=True)
    pending_output_tokens: int = Field(default=0, exclude=True)
    budget_consumed_calls: int = Field(default=0, exclude=True)
    budget_consumed_input_tokens: int = Field(default=0, exclude=True)
    budget_consumed_output_tokens: int = Field(default=0, exclude=True)

    @property
    def input_tokens(self) -> int:
        return sum(c.result.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.result.output_tokens for c in self.calls)


DAGNodeType = Literal[
    "planner",
    "facts",
    "investigation",
    "phase2_skeleton",
    "phase2_deep_dive",
    "phase2_merge",
    "scorecard",
    "doc_bundle",
    "doc_write",
    "critic",
    "consistency",
    "repair",
    "validation_gate",
]


class GroupExecutionPlan(BaseModel):
    group: str
    depth: Literal["skip", "shallow", "deep"] = "shallow"
    tool_budget: Literal["none", "standard", "expanded"] = "none"
    reasons: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    confidence_target: float = 0.75


class ExecutionNode(BaseModel):
    id: str
    type: DAGNodeType
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    validators: list[str] = Field(default_factory=list)
    tool_policy: Literal["none", "standard", "expanded"] = "none"
    cache_key: str = ""
    target: str | None = None


class NodeResult(BaseModel):
    node_id: str
    status: Literal["pending", "running", "passed", "failed", "skipped", "cached"] = "pending"
    artifacts: list[str] = Field(default_factory=list)
    validation: str = ""
    cache_hit: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class ExecutionGraph(BaseModel):
    graph_version: str = "dag-v1"
    nodes: list[ExecutionNode] = Field(default_factory=list)
    global_constraints: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    mode: Literal["deterministic", "llm"] = "deterministic"
    planner_enabled: bool = False
    dag_enabled: bool = False
    investigation_mode: Literal["skip", "manifest_agentic", "full"] = "full"
    analysis_mode: Literal["skip", "direct", "skeleton_plus_deep_dive", "full"] = "full"
    required_docs: list[str] = Field(default_factory=list)
    scorecard: bool = True
    doc_bundles: bool = True
    critic: bool = False
    consistency: bool = True
    groups: dict[str, GroupExecutionPlan] = Field(default_factory=dict)
    graph: ExecutionGraph = Field(default_factory=ExecutionGraph)
    reasons: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
