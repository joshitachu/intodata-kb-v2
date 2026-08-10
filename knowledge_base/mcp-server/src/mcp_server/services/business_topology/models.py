"""Platform-neutral contract for the business-facing topology overlay."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ClaimStatus = Literal[
    "observed",
    "inferred",
    "proposed",
    "confirmed",
    "not_established",
    "rejected",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BusinessTopologyMetadata(StrictModel):
    project_id: str
    scan_fingerprint: str
    generated_by: str = "documentation_topology_agent"
    model_name: str = ""


class EvidenceBackedClaim(StrictModel):
    id: str
    name: str
    description: str = ""
    status: ClaimStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str]


class BusinessSystem(EvidenceBackedClaim):
    kind: Literal[
        "software_system",
        "data_system",
        "external_service",
        "organization",
        "person_role",
        "unresolved",
    ] = "software_system"
    aliases: list[str] = Field(default_factory=list)
    purpose: str = ""
    owner: str = ""


class IntegrationSolution(EvidenceBackedClaim):
    platform: str = ""
    role: str = ""
    artifact_ids: list[str] = Field(default_factory=list)


class BusinessObject(EvidenceBackedClaim):
    aliases: list[str] = Field(default_factory=list)


class DeliverySegment(EvidenceBackedClaim):
    sequence: int = Field(ge=1)
    source_ref: str
    destination_ref: str
    mechanism: str = "unknown"
    mode: Literal[
        "direct",
        "buffered",
        "batch",
        "scheduled",
        "event_driven",
        "file_transfer",
        "database",
        "unknown",
    ] = "unknown"


class DeliveryPath(EvidenceBackedClaim):
    stream_id: str
    segments: list[DeliverySegment] = Field(default_factory=list)


class BusinessDataStream(EvidenceBackedClaim):
    source_system_id: str
    destination_system_id: str
    business_object_ids: list[str] = Field(default_factory=list)
    business_summary: str
    integration_solution_ids: list[str] = Field(default_factory=list)
    delivery_path_id: str | None = None
    control_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    runtime_observation_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class BusinessControl(EvidenceBackedClaim):
    stream_ids: list[str] = Field(default_factory=list)
    control_type: str = ""


class BusinessRisk(EvidenceBackedClaim):
    stream_ids: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"


class BusinessUnknown(EvidenceBackedClaim):
    stream_ids: list[str] = Field(default_factory=list)
    question: str


class RuntimeObservation(EvidenceBackedClaim):
    """Observed runtime behavior attached to an implementation-backed stream.

    Runtime evidence is deliberately annotative: it cannot declare source or
    destination systems and therefore cannot create a technical relationship.
    """

    stream_ids: list[str] = Field(default_factory=list)
    delivery_path_ids: list[str] = Field(default_factory=list)
    observed_behavior: str
    observation_window: str = ""


class BusinessConflict(EvidenceBackedClaim):
    """A disagreement between implementation and supporting or runtime evidence."""

    conflict_type: Literal[
        "supporting_vs_implementation",
        "runtime_vs_implementation",
        "supporting_sources_disagree",
        "unresolved",
    ]
    subject_refs: list[str] = Field(default_factory=list)
    implementation_evidence_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    resolution: str = ""


class ArtifactAnnotation(EvidenceBackedClaim):
    artifact_id: str
    business_role: str = ""


class ComponentAnnotation(EvidenceBackedClaim):
    component_id: str
    human_explanation: str


class LineageAnnotation(EvidenceBackedClaim):
    lineage_id: str
    business_meaning: str
    business_object_id: str | None = None


class BusinessTopologyCoverage(StrictModel):
    total_artifacts: int = Field(ge=0)
    cited_artifacts: int = Field(ge=0)
    total_evidence: int = Field(ge=0)
    cited_evidence: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    technical_relationships: int = Field(default=0, ge=0)
    grounded_technical_relationships: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)


class BusinessTopology(StrictModel):
    schema_version: Literal["business-topology-v1"] = "business-topology-v1"
    scan_fingerprint: str
    metadata: BusinessTopologyMetadata
    systems: list[BusinessSystem] = Field(default_factory=list)
    integration_solutions: list[IntegrationSolution] = Field(default_factory=list)
    business_objects: list[BusinessObject] = Field(default_factory=list)
    data_streams: list[BusinessDataStream] = Field(default_factory=list)
    delivery_paths: list[DeliveryPath] = Field(default_factory=list)
    controls: list[BusinessControl] = Field(default_factory=list)
    runtime_observations: list[RuntimeObservation] = Field(default_factory=list)
    conflicts: list[BusinessConflict] = Field(default_factory=list)
    risks: list[BusinessRisk] = Field(default_factory=list)
    unknowns: list[BusinessUnknown] = Field(default_factory=list)
    artifact_annotations: list[ArtifactAnnotation] = Field(default_factory=list)
    component_annotations: list[ComponentAnnotation] = Field(default_factory=list)
    lineage_annotations: list[LineageAnnotation] = Field(default_factory=list)
    coverage: BusinessTopologyCoverage
