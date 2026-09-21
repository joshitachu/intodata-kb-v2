"""Contract for the MCP landscape and graph-manifest artifacts plus the tool inputs.

The artifact half mirrors `frontend/types/landscape.ts`, `frontend/types/jobGraph.ts`
and `frontend/types/componentGraph.ts`, so the assistant and the canvas read the
same documents. Unlike the browser contract these models are deliberately NOT
strict: MCP owns the artifacts and adds fields to them, and a field this package
never reads must not turn every topology answer into an error. Unknown keys are
ignored and missing keys fall back to the same defaults the zod contracts declare.

Some fields are omitted on purpose rather than ignored. `LandscapeChannel` keeps
`channel_type` but not the channel name, `LandscapeInterface` keeps
`endpoint_type` but not the endpoint, `GraphComponentFact` keeps the fact's type
but not its channel/endpoint/resource descriptor, and `ProjectGraph` does not
parse the manifest's `connections` block at all (it carries `host` and
`database`). Queue names, file paths, URLs and hosts are the part of a scan most
likely to embed an address or a credential, and these tools only report type
breakdowns and wiring. Not parsing them is a stronger guarantee than remembering
not to project them.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

#: Same shape MCP itself accepts. Applied on tool input so a traversal attempt
#: never reaches the URL builder.
PROJECT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$"

#: Job and component names are path segments in the graph-manifest URLs. MCP
#: rejects separators and parent references itself (`_load_job_scan`); the same
#: rule is enforced here so a traversal attempt never reaches the URL builder.
#:
#: Deliberately no stricter than MCP's own check. Real scanner names contain
#: dots, spaces and `::` (`Assetcheck.__intodoc_zip__`, `ROOT::LogConnection`),
#: so an allow-list pattern would make legitimate artifacts unreachable and
#: produce "not found" for something the canvas is currently rendering.
_UNSAFE_NAME_CHARS = re.compile(r"[/\\\x00-\x1f\x7f]")

ARTIFACT_NAME_MAX_CHARS = 200


def validated_artifact_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("Name must not be empty.")
    if ".." in name or _UNSAFE_NAME_CHARS.search(name) is not None:
        raise ValueError("Name must not contain path separators or control characters.")
    return name


#: A job or component name safe to place in a URL path segment.
ArtifactName = Annotated[
    str,
    Field(min_length=1, max_length=ARTIFACT_NAME_MAX_CHARS),
    AfterValidator(validated_artifact_name),
]

#: A source root is a display/disambiguation token, never a URL segment, so it is
#: only length-bounded. It is long: real composite roots are full export paths.
SourceRootToken = Annotated[str, Field(min_length=1, max_length=500)]

#: Control characters only. A value that becomes a URL *query* parameter rather
#: than a path segment cannot traverse, so the path-segment rule above does not
#: apply to it — and must not, because real functional-group names ARE export
#: paths containing `/` (`VolkerVessels_Workorder/Talend exports/ETTM.__intodoc_
#: zip__/ERP_TO_THE_MAX/maximoFourps`). Applying `validated_artifact_name` to a
#: group name would make every real group unreachable.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

#: Query values are long for the same reason source roots are.
QUERY_VALUE_MAX_CHARS = 500


def validated_query_value(value: str) -> str:
    """A job or group name destined for a URL query parameter, not a path segment.

    Control characters are still rejected: no scanner name contains one, and one
    reaching a request line would corrupt the request rather than be encoded.
    `..` is rejected too — it cannot traverse from a query value, but no real
    design or group name contains it either, so keeping the guard costs nothing
    and removes the need to reason about the upstream handler's own path joins.
    """
    name = value.strip()
    if not name:
        raise ValueError("Name must not be empty.")
    if ".." in name or _CONTROL_CHARS.search(name) is not None:
        raise ValueError("Name must not contain parent references or control characters.")
    return name


#: A job or group name safe to place in a URL query parameter.
QueryValue = Annotated[
    str,
    Field(min_length=1, max_length=QUERY_VALUE_MAX_CHARS),
    AfterValidator(validated_query_value),
]


# --- Scanner facts -----------------------------------------------------------


class LandscapeSystemEvidence(BaseModel):
    signal_type: str = ""
    label: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class LandscapeSystem(BaseModel):
    id: str
    name: str = ""
    kind: str = "unknown"
    evidence: list[LandscapeSystemEvidence] = Field(default_factory=list)
    source_roots: list[str] = Field(default_factory=list)


class LandscapeApplication(BaseModel):
    id: str
    source_root: str = ""
    source_roots: list[str] = Field(default_factory=list)
    project_id: str = ""
    label: str = ""
    description: str = ""
    role: str = "execution"
    artifact_count: int = 0
    kinds_breakdown: dict[str, int] = Field(default_factory=dict)


class LandscapeProvenance(BaseModel):
    """One scanner fact behind an edge: which artifact and component proved it."""

    artifact_id: str = ""
    qualified_artifact_id: str = ""
    source_root: str = ""
    component_name: str = ""
    fact_type: str = ""


class LandscapeEdge(BaseModel):
    source: str
    target: str
    source_kind: str = ""
    target_kind: str = ""
    dependency_type: str = ""
    mechanism: str = "unknown"
    direction: str = "unknown"
    reliability: str = "unknown"
    confidence: str = "unknown"
    evidence_count: int = 0
    provenance: list[LandscapeProvenance] = Field(default_factory=list)


class LandscapeChannel(BaseModel):
    channel_type: str = ""
    direction: str = ""
    source_root: str = ""
    fact_type: str = ""


class LandscapeInterface(BaseModel):
    endpoint_type: str = ""
    direction: str = ""
    source_root: str = ""
    fact_type: str = ""


class LandscapeDiagnostic(BaseModel):
    kind: str = ""
    description: str = ""
    evidence: list[str] = Field(default_factory=list)
    severity: str = "info"


# --- Business overlay --------------------------------------------------------
#
# An additive, agent-produced interpretation. Every claim carries a status and a
# confidence, which is the artifact's own distinction between what the scanner
# observed and what the overlay inferred. Tool output keeps that distinction.


class BusinessSystem(BaseModel):
    id: str
    name: str = ""
    aliases: list[str] = Field(default_factory=list)
    kind: str = "software_system"
    description: str = ""
    purpose: str = ""
    owner: str = ""
    status: str = ""
    confidence: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class BusinessIntegrationSolution(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    platform: str = ""
    role: str = "execution"
    status: str = ""
    confidence: float | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class BusinessObject(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    status: str = ""
    confidence: float | None = None
    aliases: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class BusinessContractWidth(BaseModel):
    offered: int | None = None
    mapped: int | None = None
    written: int | None = None
    note: str = ""


class BusinessStreamCondition(BaseModel):
    kind: str = "unknown"
    statement: str = ""
    evidence_note: str = ""


class BusinessDeploymentAgreement(BaseModel):
    supplied_version: str = ""
    deployed_version: str = ""
    built_at: str = ""
    agreement: str = "unknown"
    note: str = ""


class BusinessDataStream(BaseModel):
    id: str
    source_system_id: str = ""
    destination_system_id: str = ""
    name: str = ""
    description: str = ""
    business_object_ids: list[str] = Field(default_factory=list)
    business_summary: str = ""
    integration_solution_ids: list[str] = Field(default_factory=list)
    protocols: list[str] = Field(default_factory=list)
    requiredness: str = "not_established"
    status: str = ""
    confidence: float | None = None
    control_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    # Additive structured facts. This mirror is lenient, so an absent field is
    # not an error -- but a field the publisher fills and this model omits is
    # invisible to every answer, which reads as "not established" and is
    # therefore worse than a rejection.
    mechanism: str = ""
    generation: str = ""
    direction_pattern: str = "unknown"
    lifecycle: str = "unknown"
    contract_width: BusinessContractWidth | None = None
    conditions: list[BusinessStreamCondition] = Field(default_factory=list)
    deployment: BusinessDeploymentAgreement | None = None
    key_form: str = ""
    verdict: str = ""
    document_refs: list[str] = Field(default_factory=list)


class BusinessClaim(BaseModel):
    """Shared shape of the overlay's secondary claims (controls, risks, ...)."""

    id: str
    name: str = ""
    description: str = ""
    status: str = ""
    confidence: float | None = None
    stream_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class BusinessTransformationMapping(BaseModel):
    source: str = ""
    target: str = ""
    mapping_type: str = "unknown"
    expression: str = ""
    category: str = ""
    reaches_target: str = "unknown"
    note: str = ""


class BusinessTransformation(BusinessClaim):
    """How a value is carried across one exchange.

    Distinct from :class:`BusinessRule`, which says which of several outcomes
    the business gets. Typing this as a bare claim dropped `mappings` entirely,
    so every answer about field mappings had to fall back to scanner lineage
    and could not see an analyst-curated table at all.
    """

    kind: str = "unknown"
    target_field: str = ""
    integration_solution_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    mappings: list[BusinessTransformationMapping] = Field(default_factory=list)


class BusinessRule(BusinessClaim):
    """A decision the integration layer takes on behalf of the business."""

    kind: str = "unknown"
    statement: str = ""
    inputs: list[str] = Field(default_factory=list)
    output: str = ""
    transformation_ids: list[str] = Field(default_factory=list)
    owner: str = ""
    owner_question: str = ""


class BusinessRisk(BusinessClaim):
    severity: str = "unknown"


class BusinessUnknown(BusinessClaim):
    question: str = ""


class BusinessConflict(BusinessClaim):
    conflict_type: str = "unresolved"
    subject_refs: list[str] = Field(default_factory=list)
    resolution: str = ""


class BusinessCoverage(BaseModel):
    total_artifacts: int = 0
    cited_artifacts: int = 0
    total_evidence: int = 0
    cited_evidence: int = 0
    coverage_ratio: float = 0.0
    technical_relationships: int | None = None
    grounded_technical_relationships: int | None = None
    notes: list[str] = Field(default_factory=list)


class BusinessTopology(BaseModel):
    schema_version: str = ""
    scan_fingerprint: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    systems: list[BusinessSystem] = Field(default_factory=list)
    integration_solutions: list[BusinessIntegrationSolution] = Field(default_factory=list)
    business_objects: list[BusinessObject] = Field(default_factory=list)
    data_streams: list[BusinessDataStream] = Field(default_factory=list)
    transformations: list[BusinessTransformation] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    controls: list[BusinessClaim] = Field(default_factory=list)
    risks: list[BusinessRisk] = Field(default_factory=list)
    unknowns: list[BusinessUnknown] = Field(default_factory=list)
    conflicts: list[BusinessConflict] = Field(default_factory=list)
    coverage: BusinessCoverage = Field(default_factory=BusinessCoverage)


class TopologyState(BaseModel):
    available: bool = False
    validated: bool = False
    stale: bool = False
    source: str = "none"
    message: str = ""


# --- Identity bridge from overlay claims back to scanner entities ------------


class TopologyEvidenceArtifact(BaseModel):
    id: str
    name: str = ""
    job_id: str = ""
    source_root: str = ""
    artifact_kind: str = ""
    artifact_role: str = ""


class TopologyEvidenceComponent(BaseModel):
    id: str
    artifact_id: str = ""
    name: str = ""
    component_type: str = ""


class TopologyEvidenceLineage(BaseModel):
    id: str
    job_name: str = ""
    source_table: str = ""
    source_column: str = ""
    target_table: str = ""
    target_column: str = ""


class TopologyEvidenceRelation(BaseModel):
    id: str
    source: str = ""
    target: str = ""
    relation_type: str = ""
    component_name: str = ""
    resolution: str = "unknown"


class TopologyEvidence(BaseModel):
    artifacts: list[TopologyEvidenceArtifact] = Field(default_factory=list)
    components: list[TopologyEvidenceComponent] = Field(default_factory=list)
    lineage: list[TopologyEvidenceLineage] = Field(default_factory=list)
    relations: list[TopologyEvidenceRelation] = Field(default_factory=list)


class ProjectLandscape(BaseModel):
    systems: list[LandscapeSystem] = Field(default_factory=list)
    applications: list[LandscapeApplication] = Field(default_factory=list)
    edges: list[LandscapeEdge] = Field(default_factory=list)
    channels: list[LandscapeChannel] = Field(default_factory=list)
    interfaces: list[LandscapeInterface] = Field(default_factory=list)
    diagnostics: list[LandscapeDiagnostic] = Field(default_factory=list)
    business_topology: BusinessTopology | None = None
    topology_state: TopologyState | None = None
    topology_evidence: TopologyEvidence | None = None
    unmapped: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


# --- Graph manifests (L3/L4 design facts) ------------------------------------
#
# The landscape artifact is a whole-project rollup and has no job or component
# nodes: job names appear in it only as evidence pointers inside
# `edges[].provenance[].artifact_id`. Jobs and components live in the three
# graph-manifest endpoints, which are what the canvas drills into.


class GraphSourceRoot(BaseModel):
    """One partition of a composite upload."""

    id: str = ""
    path: str = ""
    platform: str = ""
    role: str = ""
    label: str = ""
    technical_label: str = ""


class GraphArtifactRelation(BaseModel):
    """One design-level relation, e.g. a tRunJob call from one job to another."""

    source: str = ""
    target: str = ""
    relation_type: str = ""
    resolution: str = "unknown"
    provenance: str = ""


class GraphComponentFact(BaseModel):
    """A manifest fact that names the component that produced it.

    Shared shape for `artifact_channels`, `artifact_endpoints` and
    `artifact_resources`; whichever `*_type` field the source list carries is
    populated and the rest stay empty. This is the only component-level index
    available without a per-job fetch, which is what makes component names
    searchable at project level.
    """

    artifact: str = ""
    component_name: str = ""
    direction: str = ""
    provenance: str = ""
    channel_type: str = ""
    endpoint_type: str = ""
    resource_type: str = ""

    @property
    def fact_type(self) -> str:
        return self.channel_type or self.endpoint_type or self.resource_type or "unknown"


class GraphManifest(BaseModel):
    job_groups: dict[str, str] = Field(default_factory=dict)
    job_source_roots: dict[str, str] = Field(default_factory=dict)
    job_calls: dict[str, list[str]] = Field(default_factory=dict)
    job_called_by: dict[str, list[str]] = Field(default_factory=dict)
    job_external_systems: dict[str, list[str]] = Field(default_factory=dict)
    artifact_kinds: dict[str, str] = Field(default_factory=dict)
    artifact_relations: list[GraphArtifactRelation] = Field(default_factory=list)
    artifact_channels: list[GraphComponentFact] = Field(default_factory=list)
    artifact_endpoints: list[GraphComponentFact] = Field(default_factory=list)
    artifact_resources: list[GraphComponentFact] = Field(default_factory=list)
    source_roots: list[GraphSourceRoot] = Field(default_factory=list)


class GraphJobMetadata(BaseModel):
    purpose: str = ""
    summary: str = ""
    direction: str = ""
    component_count: int = 0
    component_types: list[str] = Field(default_factory=list)
    external_systems: list[str] = Field(default_factory=list)
    artifact_kind: str = "job"
    artifact_role: str = "standalone"
    subgroup: str = ""
    has_docs: bool = False


class ProjectGraph(BaseModel):
    """`GET /projects/{id}/graph` — the L1/L2 design manifest the canvas renders."""

    project_id: str = ""
    manifest: GraphManifest = Field(default_factory=GraphManifest)
    job_metadata: dict[str, GraphJobMetadata] = Field(default_factory=dict)
    artifact_counts: dict[str, int] = Field(default_factory=dict)


#: `param_summary` values are scalar display values; MCP already allow-lists the
#: parameter names and redacts credential values before serving them.
ParamValue = str | int | float | bool | None


class JobGraphParamTable(BaseModel):
    """One row-structured component parameter, as MCP publishes it.

    The counterpart of `param_summary` for params whose value is a TABLE: their
    scalar `value` is empty and the configuration is in `rows`, so a
    `dict[str, ParamValue]` cannot express them. Upstream applies the same
    credential filter to these rows as to the scalar params, and additionally
    withholds any row whose KEY OR VALUE looks credential-bearing, because a
    row is typically a name/value pair and only the value half is the secret.

    The three counts are against the scan's own row count rather than against
    `rows`, so a bounded or filtered table is visibly incomplete: `rows_omitted`
    is what the upstream cap elided, `rows_redacted` what it withheld.
    """

    name: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, ParamValue]] = Field(default_factory=list)
    row_count: int = 0
    rows_omitted: int = 0
    rows_redacted: int = 0


class JobGraphComponent(BaseModel):
    unique_name: str
    display_label: str = ""
    component_type: str = ""
    type_explanation: str = ""
    role: str = ""
    has_subgraph: bool = False
    subjob_target: str | None = None
    connection_ref: str = ""
    param_summary: dict[str, ParamValue] = Field(default_factory=dict)
    # Defaulted, not required: an MCP that predates the field simply publishes no
    # tables, and a component with none is the normal case (4 of 5 components in
    # the real corpus carry no row-structured param at all).
    param_tables: list[JobGraphParamTable] = Field(default_factory=list)
    has_code: bool = False
    description: str = ""

    @property
    def has_settings(self) -> bool:
        """Whether the scan captured any configuration for this component.

        Both halves, always: a cSetHeader has an empty `param_summary` and its
        whole configuration in `param_tables`, so testing `param_summary` alone
        reports the components that hold the answer as having no settings.
        """
        return bool(self.param_summary or self.param_tables)


class JobGraphConnection(BaseModel):
    name: str = ""
    source: str = ""
    target: str = ""
    connector_name: str = ""
    line_style: int = 0
    is_control_flow: bool = False
    explanation: str = ""


class JobGraphTables(BaseModel):
    input_tables: list[str] = Field(default_factory=list)
    output_tables: list[str] = Field(default_factory=list)


class JobGraph(BaseModel):
    """`GET /projects/{id}/jobs/{job}/graph` — one job's component graph (L3)."""

    project_id: str = ""
    job_name: str = ""
    group: str = ""
    nodes: list[JobGraphComponent] = Field(default_factory=list)
    edges: list[JobGraphConnection] = Field(default_factory=list)
    tmap_tables: dict[str, JobGraphTables] = Field(default_factory=dict)


class ComponentGraphTable(BaseModel):
    table_name: str = ""
    columns: list[str] = Field(default_factory=list)
    source_component: str | None = None


class ComponentGraphExpression(BaseModel):
    target_table: str = ""
    target_column: str = ""
    expression: str = ""
    resolved_refs: list[str] = Field(default_factory=list)
    # "opaque" until the upstream expression tokenizer lands, at which point it
    # widens to certain|likely|dynamic|opaque. Kept a bare string so a future
    # value cannot invalidate a whole answer.
    confidence: str = "opaque"


class ComponentGraph(BaseModel):
    """`GET .../components/{name}/graph` — one component's mapping graph (L4)."""

    project_id: str = ""
    job_name: str = ""
    component_unique_name: str = ""
    input_tables: list[ComponentGraphTable] = Field(default_factory=list)
    var_table: ComponentGraphTable = Field(default_factory=ComponentGraphTable)
    output_tables: list[ComponentGraphTable] = Field(default_factory=list)
    edges: list[ComponentGraphExpression] = Field(default_factory=list)


# --- Field lineage (`scan/lineage.json`, the deepest artifact) ----------------
#
# `GET /projects/{id}/lineage/job` is the only artifact that carries field-level
# movement: source column -> target column plus the expression that transformed
# it. It is a whole different order of magnitude from the graph manifests — the
# real werkorder project publishes 2 222 entries across 429 designs — so it is
# the one family that is paged upstream rather than fetched whole.
#
# Upstream flattens each raw lineage entry's `transformations` list into the
# three scalar fields below, so this contract is the flat one.

#: Contract bound on the upstream page size; the endpoint rejects anything above.
LINEAGE_PAGE_LIMIT_MAX = 200

#: Contract bound on how many job names one request may filter by.
LINEAGE_MAX_JOBS = 20


class FieldLineageEdge(BaseModel):
    """One field mapping: a column arriving in a column, and how.

    `resolved` false means the scan could not establish the source column: the
    expression is decided at runtime (a context variable, a routine call, a
    tXMLMap output field with no expression). Such a row is a real mapping with
    an unknown source, NOT the absence of a transformation, and the tool layer is
    required to count it rather than drop it.
    """

    job_name: str = ""
    source_component: str = ""
    source_table: str = ""
    source_column: str = ""
    target_component: str = ""
    target_table: str = ""
    target_column: str = ""
    transformation_type: str = ""
    expression: str = ""
    human_explanation: str = ""
    #: certain|likely|dynamic|inferred today. Deliberately a bare string, for the
    #: same reason `ComponentGraphExpression.confidence` is: a value the
    #: expression tokenizer adds later must not invalidate a whole answer.
    confidence: str = "opaque"
    resolved: bool = False
    dynamic_refs: list[str] = Field(default_factory=list)


class FieldLineageTotals(BaseModel):
    """Counts over the WHOLE matched set, not over the returned page.

    `confidence` is a plain dict rather than a model with the five documented
    buckets on purpose. A fixed model would silently drop a bucket the scanner
    adds later, and under-reporting the runtime-determined share is the one
    failure this artifact exists to prevent.
    """

    entries: int = 0
    resolved: int = 0
    unresolved: int = 0
    confidence: dict[str, int] = Field(default_factory=dict)


class JobFieldLineage(BaseModel):
    """`GET /projects/{id}/lineage/job` — field mappings for jobs or a group."""

    project_id: str = ""
    jobs: list[str] = Field(default_factory=list)
    totals: FieldLineageTotals = Field(default_factory=FieldLineageTotals)
    target_tables: list[str] = Field(default_factory=list)
    edges: list[FieldLineageEdge] = Field(default_factory=list)
    offset: int = 0
    limit: int = 0
    returned: int = 0
    total_edges: int = 0
    has_more: bool = False


# --- Tool inputs ------------------------------------------------------------


class TopologyDirection(str, Enum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    BOTH = "both"


class NavigationIntent(str, Enum):
    """Whether the targets a call produces should move the user's canvas.

    Set by the MODEL per call, from the user's wording: ``navigate`` only for an
    explicit navigational request ("take me to...", "go back to the view
    between..."), ``reference`` for everything else.
    """

    NAVIGATE = "navigate"
    REFERENCE = "reference"


class VisualRole(str, Enum):
    """How a grounded target participates in the answer's visual explanation.

    Roles are assigned by deterministic tool code after resolving artifact
    identities. They are not accepted as tool input, so the model cannot promote
    an arbitrary identifier into the canvas's primary focus.
    """

    PRIMARY = "primary"
    SUPPORTING = "supporting"
    PATH = "path"


class TopologyProjectInput(BaseModel):
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    navigation_intent: NavigationIntent = NavigationIntent.REFERENCE

    @field_validator("navigation_intent", mode="before")
    @classmethod
    def _only_exact_navigate(cls, value: object) -> object:
        """Anything but the exact string ``"navigate"`` degrades to ``reference``.

        A rejected value would cost the model one of its few loop iterations, and
        a coerced-to-navigate value would move the user's canvas unbidden. Both
        are worse than quietly not navigating, so an unrecognized value (wrong
        case, wrong word, null) resolves to the passive intent instead.
        """
        if value is NavigationIntent.NAVIGATE or value == NavigationIntent.NAVIGATE.value:
            return NavigationIntent.NAVIGATE
        return NavigationIntent.REFERENCE


class GetTopologyOverviewInput(TopologyProjectInput):
    # The overview clips its application list and its diagnostic list on every
    # real project (38 diagnostics against a cap of 8 in the werkorder upload),
    # and before this there was no way past the first page: the model could see
    # that something was elided but not read it. One offset pages both lists.
    offset: int = Field(default=0, ge=0)


class FindTopologyNodeInput(TopologyProjectInput):
    query: str = Field(min_length=1, max_length=200)
    # Scopes component search to one job's full component graph. Without it,
    # components are searched in the project manifest's component-level facts,
    # which covers only components that touch a channel, endpoint or resource.
    job: ArtifactName | None = None


class GetTopologyNodeInput(TopologyProjectInput):
    name_or_id: str = Field(min_length=1, max_length=200)


class ListTopologyDependenciesInput(TopologyProjectInput):
    name_or_id: str = Field(min_length=1, max_length=200)
    direction: TopologyDirection = TopologyDirection.BOTH
    # Two hops is the whole budget: the agent loop allows four iterations total,
    # shared with the runtime tools, so a deeper walk would return a subgraph the
    # model has no turns left to interpret.
    hops: int = Field(default=1, ge=1, le=2)


class GetJobDetailInput(TopologyProjectInput):
    # A NAME, resolved against the manifest here. Not URL-validated on input:
    # the resolved manifest name is what reaches the path, and a fuzzy query
    # ("Quintiq to Maximo") must be allowed to fail resolution rather than
    # validation.
    name_or_id: str = Field(min_length=1, max_length=ARTIFACT_NAME_MAX_CHARS)
    source_root: SourceRootToken | None = None
    # A real design has 251 components and 201 connections against listing caps
    # of 12, so a single page dead-ends the drill-down it exists to enable. One
    # offset pages the component list and the connection list together: the agent
    # loop has four iterations for the whole turn, and two independent cursors
    # would cost an iteration to advance each.
    offset: int = Field(default=0, ge=0)
    # Batch read of the shown components' configured settings.
    #
    # WHY HERE AND NOT AS `components: list[str]` ON get_component_detail: the
    # measured failure is a BLIND sweep. An agent asked which criterion routes a
    # shipment to one flow or the other, opened 15 components of one design one
    # at a time, and never found it -- because it had no way to know which
    # component held the setting until it had already opened it. A component
    # list would only help an agent that already knows the names to ask for,
    # which is precisely what the failing agent did not know; it would also add
    # per-name resolution, partial-failure and ambiguity semantics, and
    # get_component_detail's other half (the per-component mapping graph) is a
    # separate upstream read per component and cannot be batched with it.
    # A flag here reuses the existing page, the existing offset contract and the
    # single job-graph read that already happened, and it answers the question
    # the sweeping agent was actually asking: "show me this design's components
    # AND their settings".
    include_parameters: bool = False


class GetComponentDetailInput(TopologyProjectInput):
    job: str = Field(min_length=1, max_length=ARTIFACT_NAME_MAX_CHARS)
    component: str = Field(min_length=1, max_length=ARTIFACT_NAME_MAX_CHARS)
    source_root: SourceRootToken | None = None


class GetFieldLineageInput(TopologyProjectInput):
    """Field-level lineage for one design or one whole functional group.

    `job` and `group` are both optional individually but at least one is
    required: the artifact has 2 222 entries for a real project, and an
    unscoped read would be a document, not an answer. Both may be given, in
    which case upstream intersects them.

    There is deliberately no `limit`: the page size is this package's cap, not
    the model's choice. A model asking for 200 field mappings would produce a
    result the harness has to throw away.
    """

    job: QueryValue | None = None
    group: QueryValue | None = None
    offset: int = Field(default=0, ge=0)
    source_root: SourceRootToken | None = None

    @model_validator(mode="after")
    def _require_a_scope(self) -> GetFieldLineageInput:
        if not self.job and not self.group:
            raise ValueError(
                "Provide job=<design name> and/or group=<functional group name>. "
                "Field lineage is never read for a whole project at once."
            )
        return self
