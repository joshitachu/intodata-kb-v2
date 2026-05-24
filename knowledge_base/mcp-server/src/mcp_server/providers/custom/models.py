"""Typed models for the custom documentation-generation harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class UnitFact(BaseModel):
    """One canonical scanner unit: Talend job, Workato recipe/function, etc."""

    name: str
    path: str = ""
    unit_type: str = "unit"
    group: str = "Global"
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


class ModelResult(BaseModel):
    success: bool
    text: str = ""
    model_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
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

    @property
    def input_tokens(self) -> int:
        return sum(c.result.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.result.output_tokens for c in self.calls)
