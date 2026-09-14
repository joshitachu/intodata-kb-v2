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
    protocol: str = "unknown"
    resource: str = ""
    component: str = ""
    artifact_ids: list[str] = Field(default_factory=list)
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


class ContractWidth(StrictModel):
    """How much of what a source offers actually reaches the destination.

    Three counts, not one: a source may expose 400 attributes, a mapping may
    read 12 of them, and the destination may persist 5. Collapsing that into a
    single "fields" number hides the funnel, which is usually the sharpest
    argument in a rationalisation discussion.
    """

    offered: int | None = Field(default=None, ge=0)
    mapped: int | None = Field(default=None, ge=0)
    written: int | None = Field(default=None, ge=0)
    note: str = ""


class StreamCondition(StrictModel):
    """A precondition or ordering constraint on an exchange.

    Deliberately NOT a value mapping. A value mapping answers "what does this
    field become"; a condition answers "does this exchange happen at all, and
    in what order". Mixing them is why a value table reads as a business rule.
    """

    kind: Literal[
        "trigger",
        "filter",
        "ordering",
        "schedule",
        "disabled",
        "guard",
        "unknown",
    ] = "unknown"
    statement: str
    evidence_note: str = ""


class DeploymentAgreement(StrictModel):
    """Whether the analysed definition is the one running in production.

    ``agreement`` is the verdict a reader needs; the two version strings are
    what it was derived from. ``unknown`` is a first-class answer: for many
    artifact kinds no runtime source reports a comparable version.
    """

    supplied_version: str = ""
    deployed_version: str = ""
    built_at: str = ""
    agreement: Literal[
        "equal", "supplied_behind", "supplied_ahead", "unknown"
    ] = "unknown"
    note: str = ""


class BusinessDataStream(EvidenceBackedClaim):
    source_system_id: str
    destination_system_id: str
    business_object_ids: list[str] = Field(default_factory=list)
    business_summary: str
    integration_solution_ids: list[str] = Field(default_factory=list)
    protocols: list[str] = Field(default_factory=list)
    requiredness: Literal[
        "required", "optional", "conditional", "not_established"
    ] = "not_established"
    delivery_path_id: str | None = None
    control_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    runtime_observation_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    # --- Additive structured facts -------------------------------------
    # Every field below is optional and defaults to "not established", so a
    # topology produced before they existed stays valid and renders exactly as
    # it did. They exist because these facts were previously concatenated into
    # `description`, where a reader cannot table them, filter them or compare
    # two streams on them.
    mechanism: str = ""
    generation: str = ""
    direction_pattern: Literal["push", "pull", "hybrid", "unknown"] = "unknown"
    lifecycle: Literal[
        "active",
        "duplicate",
        "stopped",
        "dormant",
        "reference_only",
        "unknown",
    ] = "unknown"
    contract_width: ContractWidth | None = None
    conditions: list[StreamCondition] = Field(default_factory=list)
    deployment: DeploymentAgreement | None = None
    key_form: str = ""
    verdict: str = ""
    document_refs: list[str] = Field(default_factory=list)


class TransformationMapping(StrictModel):
    source: str
    target: str
    mapping_type: Literal["direct", "value", "derived", "constant", "unknown"] = (
        "unknown"
    )
    # `source` stays the short identity used for graph joins; `expression` is
    # the verbatim rule when the two differ (`substring(DESCRIPTION, 1, 50)`).
    expression: str = ""
    category: str = ""
    reaches_target: Literal["yes", "no", "partial", "disabled", "unknown"] = "unknown"
    note: str = ""


class BusinessTransformation(EvidenceBackedClaim):
    stream_ids: list[str] = Field(default_factory=list)
    integration_solution_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    mappings: list[TransformationMapping] = Field(default_factory=list)
    # A field mapping, a value domain and a derived rule are three different
    # kinds of fact. Rendered as one flat list they are indistinguishable, so a
    # status value table ("GGK -> In Process") reads as a business decision.
    kind: Literal[
        "field_mapping",
        "value_domain",
        "derived_rule",
        "constant_set",
        "unknown",
    ] = "unknown"
    # For `value_domain`: the target field whose accepted values this table
    # defines, so a renderer can nest it under that field instead of beside it.
    target_field: str = ""


class BusinessRule(EvidenceBackedClaim):
    """A decision the integration layer takes on behalf of the business.

    Separate from :class:`BusinessTransformation` on purpose. A transformation
    says how a value is carried; a rule says which of several outcomes the
    business gets, and therefore needs a named owner to confirm or retire it.
    ``owner_question`` is the sentence to put in front of that owner.
    """

    kind: Literal[
        "value_mapping",
        "precedence",
        "derivation",
        "selection",
        "feedback",
        "ordering",
        "disabled",
        "threshold",
        "unknown",
    ] = "unknown"
    statement: str
    inputs: list[str] = Field(default_factory=list)
    output: str = ""
    stream_ids: list[str] = Field(default_factory=list)
    transformation_ids: list[str] = Field(default_factory=list)
    owner: str = ""
    owner_question: str = ""


class BusinessControl(EvidenceBackedClaim):
    stream_ids: list[str] = Field(default_factory=list)
    control_type: str = ""


class BusinessRisk(EvidenceBackedClaim):
    stream_ids: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"


class BusinessUnknown(EvidenceBackedClaim):
    stream_ids: list[str] = Field(default_factory=list)
    question: str


class RuntimeMetric(StrictModel):
    """One measured number, with the window and source it came from.

    A measurement without its window and denominator is not reportable: the
    same estate reads as 23% failing or 0.8% failing depending on which
    environment and period the number covers.
    """

    label: str
    value: str
    unit: str = ""
    window: str = ""
    source: str = ""
    note: str = ""


class RuntimeObservation(EvidenceBackedClaim):
    """Observed runtime behavior attached to an implementation-backed stream.

    Runtime evidence is deliberately annotative: it cannot declare source or
    destination systems and therefore cannot create a technical relationship.
    """

    stream_ids: list[str] = Field(default_factory=list)
    delivery_path_ids: list[str] = Field(default_factory=list)
    observed_behavior: str
    observation_window: str = ""
    kind: Literal[
        "volume",
        "reliability",
        "latency",
        "cadence",
        "throughput",
        "deployment",
        "coverage",
        "absence",
        "unknown",
    ] = "unknown"
    metrics: list[RuntimeMetric] = Field(default_factory=list)
    source: str = ""


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
    transformations: list[BusinessTransformation] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    controls: list[BusinessControl] = Field(default_factory=list)
    runtime_observations: list[RuntimeObservation] = Field(default_factory=list)
    conflicts: list[BusinessConflict] = Field(default_factory=list)
    risks: list[BusinessRisk] = Field(default_factory=list)
    unknowns: list[BusinessUnknown] = Field(default_factory=list)
    artifact_annotations: list[ArtifactAnnotation] = Field(default_factory=list)
    component_annotations: list[ComponentAnnotation] = Field(default_factory=list)
    lineage_annotations: list[LineageAnnotation] = Field(default_factory=list)
    coverage: BusinessTopologyCoverage
