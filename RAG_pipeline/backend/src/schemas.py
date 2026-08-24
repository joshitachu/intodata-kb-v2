from __future__ import annotations

import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.harness.workflow_projection import WorkflowCard

from src.document_roles import DEFAULT_DOCUMENT_ROLE, DocumentRole


# --- Query ---

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, description="Natural language question")
    top_k: int = Field(default=8, ge=1, le=50)
    threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    document_ids: list[int] | None = Field(
        default=None,
        description="Optional: restrict search to specific documents",
    )
    project_id: str | None = Field(
        default=None,
        description="Optional: restrict search to a specific project",
    )


class Source(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    score: float
    text: str
    document_role: DocumentRole = DEFAULT_DOCUMENT_ROLE
    source_layer: str | None = None
    section: str | None = None
    unit: str | None = None
    group: str | None = None
    display_name: str | None = None
    artifact_path: str | None = None
    visibility: str | None = None
    run_id: int | None = None
    project_id: str | None = None
    knowledge_revision_id: str | None = None


class DocumentSource(BaseModel):
    """Document-level source (chunks merged by document)."""
    document_id: int
    filename: str
    score: float
    text: str
    reference_number: int
    chunk_indexes: list[int] = Field(default_factory=list)
    document_role: DocumentRole = DEFAULT_DOCUMENT_ROLE
    source_layer: str | None = None
    section: str | None = None
    unit: str | None = None
    group: str | None = None
    display_name: str | None = None
    artifact_path: str | None = None
    visibility: str | None = None
    run_id: int | None = None
    project_id: str | None = None
    knowledge_revision_id: str | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[DocumentSource]
    latency_ms: int


class DebugSearchRequest(BaseModel):
    query: str = Field(..., min_length=3, description="Natural language question")
    top_k: int = Field(default=8, ge=1, le=50)


class DebugSearchResult(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    score: float
    text: str


class DebugSearchResponse(BaseModel):
    query: str
    rbac_groups: list[str]
    rbac_filter_applied: bool
    top_k: int
    embedding_ms: int
    total_results: int
    results: list[DebugSearchResult]


# --- Documents ---

class DocumentResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    document_role: DocumentRole = DEFAULT_DOCUMENT_ROLE
    source: str | None = None
    author: str | None = None
    department: str | None = None
    project_id: str | None = None
    chunk_count: int
    created_at: datetime.datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


# --- Ingest ---

class IngestResponse(BaseModel):
    document_id: int
    filename: str
    status: str  # pending | processing | completed | failed
    message: str


class MarkdownIngestRequest(BaseModel):
    """Request to ingest raw markdown text (no file upload needed)."""
    text: str = Field(..., min_length=1, description="Raw markdown content")
    filename: str = Field(..., min_length=1, description="e.g. job_001_payment_loader.md")
    source: str = Field(default="", description="e.g. intodoc:vaxxinova")
    project_id: str = Field(default="", description="Project identifier")
    document_role: DocumentRole = Field(
        default=DEFAULT_DOCUMENT_ROLE,
        description="How the assistant should use this document",
    )
    metadata: dict = Field(default_factory=dict)


class KnowledgeRevisionPrepareRequest(BaseModel):
    """Authenticated receipt for an inactive revision already staged in RAG."""

    project_id: str = Field(..., min_length=1, max_length=255)
    knowledge_revision_id: str = Field(..., min_length=1, max_length=255)
    base_knowledge_revision_id: str | None = Field(default=None, max_length=255)
    membership_hashes: dict[str, str] = Field(default_factory=dict)
    deleted_logical_ids: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)


class KnowledgeRevisionReceipt(BaseModel):
    project_id: str
    knowledge_revision_id: str
    state: str
    base_knowledge_revision_id: str | None = None
    previous_knowledge_revision_id: str | None = None


class KnowledgeRevisionRollbackRequest(BaseModel):
    expected_active_revision_id: str = Field(..., min_length=1, max_length=255)


class KnowledgeRevisionDetailResponse(KnowledgeRevisionReceipt):
    membership_hashes: dict[str, str] = Field(default_factory=dict)
    deleted_logical_ids: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)


class ActiveKnowledgeRevisionResponse(BaseModel):
    project_id: str
    knowledge_revision_id: str | None = None


class EntityAliasRecord(BaseModel):
    """One entity alias extracted from a project's generated fact indexes."""
    alias: str = Field(..., min_length=1, max_length=500)
    alias_normalized: str = Field(..., min_length=1, max_length=500)
    canonical_name: str = Field(..., min_length=1, max_length=500)
    entity_type: str = Field(..., min_length=1, max_length=100)
    unit_name: str | None = Field(default=None, max_length=500)
    source_path: str | None = Field(default=None, max_length=1000)
    source_artifact: str | None = Field(default=None, max_length=100)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EntityAliasReplaceRequest(BaseModel):
    """Replace all entity aliases for one project in a single transaction."""
    project_id: str = Field(..., min_length=1, max_length=255)
    aliases: list[EntityAliasRecord] = Field(default_factory=list)


class EntityAliasReplaceResponse(BaseModel):
    project_id: str
    deleted: int
    inserted: int


# --- Health ---

class ServiceStatus(BaseModel):
    status: str  # ok | error
    message: str | None = None


class HealthResponse(BaseModel):
    status: str  # healthy | degraded | unhealthy
    database: ServiceStatus
    qdrant: ServiceStatus
    ollama: ServiceStatus
    hindsight: ServiceStatus


# --- Page scope (the client's current viewing context) ---
#
# SECURITY: page_scope is client-supplied and therefore UNTRUSTED. It is a prompt
# hint only. It must never become a retrieval filter, an authorization input, or a
# widening of project access -- the routes' existing project checks remain the sole
# authority. A page_scope naming an inaccessible project must grant nothing.
#
# The level/selection field names are deliberately camelCase rather than the
# snake_case used elsewhere in this module: this union mirrors the frontend
# `TopologyLevel` in `frontend/hooks/useTopologyNavigation.ts` exactly, and the same
# shape travels back out as `targets[].level` from the topology tools straight into
# canvas navigation. Renaming here would produce chips that navigate nowhere.

# A single name/id token inside a topology level. Bounded so an oversized client
# payload cannot inflate the persisted row or the prompt hint.
TopologyToken = Annotated[str, Field(min_length=1, max_length=500)]


class TopologyApplicationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["application"]
    applicationId: TopologyToken


class TopologySystemSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["system"]
    systemId: TopologyToken


class TopologyEdgeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["edge"]
    edgeKey: TopologyToken


class TopologyRelationshipSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["relationship"]
    source: TopologyToken
    target: TopologyToken
    streamKeys: list[TopologyToken] = Field(default_factory=list, max_length=200)


IntegrationSelection = Annotated[
    TopologyApplicationSelection
    | TopologySystemSelection
    | TopologyEdgeSelection
    | TopologyRelationshipSelection,
    Field(discriminator="kind"),
]


class TopologyProjectLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["project"]


class TopologyIntegrationLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["integration"]
    selection: IntegrationSelection


class TopologyGroupLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["group"]
    groupName: TopologyToken
    sourceRoot: TopologyToken | None = None


class TopologyJobLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["job"]
    jobName: TopologyToken
    sourceRoot: TopologyToken | None = None


class TopologyComponentLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["component"]
    jobName: TopologyToken
    componentName: TopologyToken
    sourceRoot: TopologyToken | None = None


TopologyLevel = Annotated[
    TopologyProjectLevel
    | TopologyIntegrationLevel
    | TopologyGroupLevel
    | TopologyJobLevel
    | TopologyComponentLevel,
    Field(discriminator="kind"),
]


class TopologySelectedEntity(BaseModel):
    """The human-visible thing selected inside the current canvas level.

    This is deliberately not another navigation target. It is client-supplied
    conversational context for phrases such as "this column" and therefore has
    no authority over access, retrieval, or canvas movement.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "application",
        "system",
        "relationship",
        "edge",
        "group",
        "job",
        "component",
        "table",
        "column",
        "mapping",
    ]
    label: str = Field(..., min_length=1, max_length=200)
    entity_id: TopologyToken | None = None


class TopologyPageFilter(BaseModel):
    """One bounded, human-readable view filter supplied as prompt context."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    value: str = Field(..., min_length=1, max_length=200)


class TopologyPageScope(BaseModel):
    """The topology canvas level the user is looking at while asking a question."""

    model_config = ConfigDict(extra="forbid")

    surface: Literal["topology"]
    project_id: str = Field(..., min_length=1, max_length=255)
    level: TopologyLevel
    selected_entity: TopologySelectedEntity | None = None
    active_detail_tab: str | None = Field(default=None, min_length=1, max_length=100)
    filters: list[TopologyPageFilter] | None = Field(default=None, max_length=12)


# `surface` is the discriminator tag: other surfaces join this union later without
# changing the field name on the request.
PageScope = TopologyPageScope


# --- Conversations ---


class ConversationMessageRequest(BaseModel):
    """Request to send a message in a conversation."""

    query: str = Field(..., min_length=1, description="User's message/question")
    top_k: int = Field(default=8, ge=1, le=50)
    threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    project_id: str | None = Field(
        default=None,
        description="Optional: restrict search to a specific project",
    )
    images: list[str] | None = Field(
        default=None,
        max_length=4,
        description="Base64-encoded images for vision",
    )
    client_turn_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
        description="Stable idempotency key reused when the client retries this turn.",
    )
    page_scope: PageScope | None = Field(
        default=None,
        description=(
            "Untrusted hint describing what the client is currently viewing. Used only "
            "to disambiguate references like 'this' in the prompt; never an "
            "authorization input or a retrieval filter."
        ),
    )


class SlashCommandResponse(BaseModel):
    """Discoverable command metadata; planner templates remain server-only."""

    name: str
    description: str
    argument_hint: str | None = None


class SlashCommandListResponse(BaseModel):
    commands: list[SlashCommandResponse]


class MessageHarnessActivity(BaseModel):
    """Sanitized workflow activity safe to rehydrate in conversation history."""

    workflow_run_id: str | None = None
    event_type: str
    label: str
    status: str
    summary: str | None = None
    safe_details: dict = Field(default_factory=dict)
    timestamp: datetime.datetime | None = None
    workflow_status: str | None = None
    workflow_started_at: datetime.datetime | None = None
    workflow_finished_at: datetime.datetime | None = None


class MessageHarnessActionChoice(BaseModel):
    label: str
    value: str


class MessageHarnessPendingAction(BaseModel):
    conversation_id: int | None = None
    workflow_run_id: int | None = None
    interrupt_id: int | None = None
    proposal_id: str | None = None
    expected_version: int
    kind: str
    prompt: str
    choices: list[MessageHarnessActionChoice] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    project_id: str | None = None
    platform: str | None = None
    expires_at: datetime.datetime | None = None


class MessageResponse(BaseModel):
    """A single message in a conversation."""

    id: int
    role: str  # "user" | "assistant"
    content: str
    sources: list[DocumentSource] | None = None
    image_data: list[str] | None = None
    latency_ms: int | None = None
    status: str = "completed"
    error_message: str | None = None
    primary_source_layer: str | None = None
    fallback_used: bool = False
    doc_gap: bool = False
    workflow_run_id: int | None = None
    turn_plan: dict[str, Any] | None = None
    structured_answer: dict[str, Any] | None = None
    harness_activities: list[MessageHarnessActivity] = Field(default_factory=list)
    workflow_cards: list["WorkflowCard"] = Field(default_factory=list)
    pending_action: MessageHarnessPendingAction | None = None
    page_scope: PageScope | None = None
    created_at: datetime.datetime

    @field_validator("turn_plan", mode="before")
    @classmethod
    def normalize_turn_plan(cls, value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    @field_validator("structured_answer", mode="before")
    @classmethod
    def normalize_structured_answer(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        if value.get("response_type") == "agent_response_v1":
            from src.harness.response_synthesis import TypedAgentResponse

            try:
                TypedAgentResponse.model_validate(value)
            except ValueError:
                return None
            return value
        return value


class ConversationResponse(BaseModel):
    """Summary of a conversation (for list view)."""

    id: int
    title: str | None = None
    message_count: int
    origin_surface: Literal["chat", "topology"] = "chat"
    visible_in_chat: bool = True
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConversationDetailResponse(BaseModel):
    """Full conversation with all messages."""

    id: int
    title: str | None = None
    messages: list[MessageResponse]
    origin_surface: Literal["chat", "topology"] = "chat"
    visible_in_chat: bool = True
    agent_plan: "AgentPlanProjectionResponse | None" = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class AgentPlanStepProjectionResponse(BaseModel):
    key: str
    objective: str
    success_criteria: list[str]
    capability_id: str | None = None
    kind: str
    depends_on: list[str]
    requires_wait: bool
    restart_sensitive: bool
    status: str
    effect_operation_id: str | None = None
    capability_receipt_ref: dict | None = None
    workflow_run_id: int | None = None
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None


class AgentPlanProjectionResponse(BaseModel):
    plan_id: str
    goal: str
    success_criteria: list[str]
    constraints: list[str]
    scoped_entities: list[str]
    status: str
    version: int
    steps: list[AgentPlanStepProjectionResponse]
    last_change_reason: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    conversations: list[ConversationResponse]
    total: int


class ConversationCreateRequest(BaseModel):
    """Request to create a new conversation."""

    title: str | None = Field(default=None, max_length=200)
    surface: Literal["chat", "topology"] = "chat"


class ConversationUpdateRequest(BaseModel):
    """Request to update a conversation."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    visible_in_chat: bool | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
