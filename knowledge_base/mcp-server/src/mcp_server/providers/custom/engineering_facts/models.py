"""Shared, platform-neutral EngineeringFacts domain model."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    """Concrete source reference for an extracted fact."""

    path: str
    unit_name: str | None = None
    line: int | None = None
    symbol: str | None = None
    source_key: str | None = None
    excerpt: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class BaseFact(BaseModel):
    """Base fact shape shared by all EngineeringFacts artifacts."""

    id: str
    kind: str
    name: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EntityFact(BaseFact):
    entity_type: str = "entity"
    path: str | None = None
    unit_name: str | None = None


class UsageFact(BaseFact):
    usage_type: Literal[
        "defined",
        "read",
        "written",
        "called",
        "queried",
        "imported",
        "emitted",
        "configured",
    ] = "defined"
    entity_id: str | None = None
    location: str | None = None


class DependencyFact(BaseFact):
    source: str
    target: str
    dependency_type: str = "depends_on"


class ImpactFact(BaseFact):
    changed_entity: str
    impacted_entity: str
    check: str
    impact_type: str = "change"


class ArchitectureFact(BaseFact):
    layer: str | None = None
    component: str | None = None
    boundary: str | None = None


class DataFlowFact(BaseFact):
    source: str
    target: str
    transformation: str | None = None
    schema_hint: str | None = None
    persistence: str | None = None


class ApiSurfaceFact(BaseFact):
    route: str | None = None
    method: str | None = None
    handler: str | None = None
    request_shape: str | None = None
    response_shape: str | None = None
    auth_hint: str | None = None


class ConfigRuntimeFact(BaseFact):
    config_type: str = "config"
    key: str | None = None
    value_hint: str | None = None


class TestCoverageFact(BaseFact):
    test_file: str | None = None
    covered_entity: str | None = None
    gap: str | None = None
    suggested_scenario: str | None = None


class KnownRiskFact(BaseFact):
    severity: str = "LOW"
    affected_entity: str | None = None
    risk_type: str = "risk"
    remediation: str | None = None


class EngineeringFacts(BaseModel):
    project_id: str
    platform: str
    entities: list[EntityFact] = Field(default_factory=list)
    usage: list[UsageFact] = Field(default_factory=list)
    dependencies: list[DependencyFact] = Field(default_factory=list)
    impacts: list[ImpactFact] = Field(default_factory=list)
    architecture: list[ArchitectureFact] = Field(default_factory=list)
    data_flows: list[DataFlowFact] = Field(default_factory=list)
    api_surface: list[ApiSurfaceFact] = Field(default_factory=list)
    config_runtime: list[ConfigRuntimeFact] = Field(default_factory=list)
    test_coverage: list[TestCoverageFact] = Field(default_factory=list)
    known_risks: list[KnownRiskFact] = Field(default_factory=list)

    def write(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
