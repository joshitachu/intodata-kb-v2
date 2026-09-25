"""Provider-neutral contracts for static/runtime correlation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_MAX_JOBS = 5_000
_MAX_COMPONENTS_PER_JOB = 2_000
_MAX_EDGES = 30_000
_MAX_LINEAGE = 100_000


class StaticTopologyComponent(BaseModel):
    unique_name: str = Field(min_length=1, max_length=255)
    component_type: str = Field(default="", max_length=120)


class StaticTopologyJob(BaseModel):
    static_job_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    source_root: str = Field(default="", max_length=1_000)
    components: list[StaticTopologyComponent] = Field(default_factory=list, max_length=_MAX_COMPONENTS_PER_JOB)


class StaticTopologyDependency(BaseModel):
    """A directed scanner fact.

    ``control_flow`` direction is caller -> called child. ``data_lineage`` is
    upstream producer -> downstream consumer. Consumers must preserve that
    semantic distinction when calculating impact.
    """

    source_job_id: str = Field(min_length=1, max_length=255)
    target_job_id: str = Field(min_length=1, max_length=255)
    dependency_type: Literal["control_flow", "data_lineage"]
    evidence_id: str = Field(default="", max_length=512)


class StaticLineageFact(BaseModel):
    job_id: str = Field(min_length=1, max_length=255)
    source_job_id: str = Field(default="", max_length=255)
    source_component: str = Field(default="", max_length=255)
    target_component: str = Field(default="", max_length=255)
    source_table: str = Field(default="", max_length=255)
    source_column: str = Field(default="", max_length=255)
    target_table: str = Field(default="", max_length=255)
    target_column: str = Field(default="", max_length=255)
    confidence: str = Field(default="opaque", max_length=32)
    # MCP may publish only scan-redaction-approved expression strings.  Both
    # count and size are bounded here so a mapping-level lookup cannot become a
    # source-bundle transport channel.
    transformation_types: list[str] = Field(default_factory=list, max_length=10)
    expressions: list[str] = Field(default_factory=list, max_length=10)
    evidence_id: str = Field(default="", max_length=512)

    @field_validator("transformation_types")
    @classmethod
    def _bounded_transform_types(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 50 for value in values):
            raise ValueError("transformation types must be non-empty and at most 50 characters")
        return values

    @field_validator("expressions")
    @classmethod
    def _bounded_expressions(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1_000 for value in values):
            raise ValueError("lineage expressions must be at most 1000 characters")
        return values


class RuntimeStaticTopologySnapshotIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=255)
    scan_revision: str = Field(min_length=8, max_length=128)
    # Monotonic scanner-owned generation (currently MCP ScanRun.id).  This is
    # intentionally separate from the content revision: uploads can arrive out
    # of order, while the scanner's source sequence remains authoritative.
    source_sequence: int = Field(ge=1)
    platform: str = Field(min_length=1, max_length=80)
    jobs: list[StaticTopologyJob] = Field(default_factory=list, max_length=_MAX_JOBS)
    dependencies: list[StaticTopologyDependency] = Field(default_factory=list, max_length=_MAX_EDGES)
    lineage: list[StaticLineageFact] = Field(default_factory=list, max_length=_MAX_LINEAGE)

    @field_validator("scan_revision")
    @classmethod
    def _safe_revision(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("scan_revision must be a non-empty token")
        return value

    @model_validator(mode="after")
    def _validate_references(self) -> "RuntimeStaticTopologySnapshotIn":
        job_ids = {job.static_job_id for job in self.jobs}
        if len(job_ids) != len(self.jobs):
            raise ValueError("jobs must have unique static_job_id values")
        for edge in self.dependencies:
            if edge.source_job_id not in job_ids or edge.target_job_id not in job_ids:
                raise ValueError("dependency references an unknown static job")
        for entry in self.lineage:
            if entry.job_id not in job_ids:
                raise ValueError("lineage references an unknown static job")
            if entry.source_job_id and entry.source_job_id not in job_ids:
                raise ValueError("lineage source_job_id references an unknown static job")
        return self


class RuntimeStaticTopologySnapshotReceipt(BaseModel):
    project_id: str
    scan_revision: str
    source_sequence: int
    received_at: datetime
    jobs: int
    dependencies: int
    lineage: int


#: Provenance value for a mapping a human created through the write route.
RESOLVED_BY_MANUAL = "manual"


def static_ref_resolved_by(source_field: str) -> str:
    """Provenance value for a mapping the declared static reference resolved.

    The field name is carried in the value so a stored row says WHICH provider
    field produced it. When a provider changes the field it declares, the rows
    the previous one wrote are still identifiable rather than indistinguishable
    from the new ones.
    """
    return f"static_ref:{source_field}"


class RuntimeJobScanMapCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=255)
    source_key: str = Field(min_length=1, max_length=100)
    runtime_job_id: str = Field(min_length=1, max_length=255)
    static_job_id: str = Field(min_length=1, max_length=255)
    scan_revision: str | None = Field(default=None, min_length=8, max_length=128)
    # Fixed, not merely defaulted. This model is the body of the human write
    # route, and a mapping created there is manual BY DEFINITION; accepting a
    # `static_ref:...` value would let a request claim the resolver's
    # provenance for a row the resolver never produced, which is the one thing
    # the column exists to distinguish.
    resolved_by: Literal["manual"] = RESOLVED_BY_MANUAL


class RuntimeJobScanMap(BaseModel):
    id: int
    project_id: str
    source_key: str
    runtime_job_id: str
    static_job_id: str
    scan_revision: str | None = None
    #: `manual`, or `static_ref:<provider field>`. See `RuntimeJobScanMapRow`.
    resolved_by: str = RESOLVED_BY_MANUAL
    created_at: datetime
    created_by: str | None = None


class RuntimeFieldViolation(BaseModel):
    field_name: str
    kind: str
    expected_type: str | None = None
    evidence_log_event_id: int


class RuntimeCorrelationImpact(BaseModel):
    static_job_id: str
    job_name: str
    source_root: str = ""
    distance: int = Field(ge=1)
    dependency_type: Literal["control_flow", "data_lineage"]


class RuntimeStaticLineageEvidence(BaseModel):
    job_id: str
    source_component: str = ""
    target_component: str = ""
    source_table: str = ""
    source_column: str = ""
    target_table: str = ""
    target_column: str = ""
    confidence: str
    transformation_types: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    evidence_id: str = ""


class RuntimeExecutionCorrelation(BaseModel):
    execution_id: str
    project_id: str
    status: Literal["matched", "partial", "unmatched"]
    scan_revision: str | None = None
    runtime_job_id: str | None = None
    runtime_job_name: str
    failed_component: str | None = None
    component_match: Literal["exact_normalized", "unmatched", "not_reported"]
    field_violation: RuntimeFieldViolation | None = None
    static_job_id: str | None = None
    static_job_name: str | None = None
    static_source_root: str | None = None
    lineage_evidence: list[RuntimeStaticLineageEvidence] = Field(default_factory=list)
    control_flow_impacts: list[RuntimeCorrelationImpact] = Field(default_factory=list)
    data_lineage_impacts: list[RuntimeCorrelationImpact] = Field(default_factory=list)
    runtime_evidence_log_event_ids: list[int] = Field(default_factory=list)
    note: str = (
        "Correlation links explicit runtime and static evidence only; it does not establish an upstream API specification change."
    )
