"""Provider-agnostic Nexus harness domain contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HarnessProviderType(str, Enum):
    MODEL = "model_provider"
    RETRIEVAL = "retrieval_provider"
    SOURCE = "source_provider"
    RUNTIME = "runtime_provider"
    LOG = "log_provider"
    DOCUMENTATION = "documentation_provider"
    COMMUNICATION = "communication_provider"
    PUBLISHING = "publishing_provider"
    WORKFLOW = "workflow_provider"
    AUTH = "auth_provider"


class HarnessRiskTier(str, Enum):
    READ_ONLY = "read_only"
    TRIGGERS_WORKFLOW = "triggers_workflow"
    WRITES_CONTENT = "writes_content"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class CapabilityHealth(str, Enum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


# Single source of truth for scanner platforms a project can be onboarded as.
# Capability input schemas and the MCP projects provider must both use this.
SUPPORTED_PROJECT_PLATFORMS: tuple[str, ...] = ("generic", "lobster", "talend", "workato")


class WorkflowRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    TRIGGERED = "triggered"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    INDETERMINATE = "indeterminate"


class WorkflowInterruptType(str, Enum):
    CLARIFICATION = "clarification"
    APPROVAL = "approval"


class WorkflowInterruptStatus(str, Enum):
    PENDING = "pending"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ActivityEventStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class CapabilityDefinition(BaseModel):
    id: str
    label: str
    provider_type: HarnessProviderType
    provider_id: str
    action: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_type: str
    risk_tier: HarnessRiskTier
    enabled: bool = True
    health: CapabilityHealth = CapabilityHealth.AVAILABLE
    required_permissions: list[str] = Field(default_factory=list)
    packs: list[str] = Field(default_factory=list)
    use_when: list[str] = Field(default_factory=list)
    do_not_use_when: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    output_contracts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunCreate(BaseModel):
    conversation_id: int
    user_id: int
    project_id: str | None = None
    intent_mode: str
    capability_id: str
    provider_id: str
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING
    input_summary: str | None = None
    output_summary: str | None = None
    artifact_links: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    runtime_provider_id: str | None = None
    runtime_thread_id: str | None = None
    runtime_run_id: str | None = None
    current_node: str | None = None
    resume_token: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_receipts: list[dict[str, Any]] = Field(default_factory=list)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowRunUpdate(BaseModel):
    project_id: str | None = None
    status: WorkflowRunStatus | None = None
    output_summary: str | None = None
    artifact_links: list[dict[str, Any]] | None = None
    error_message: str | None = None
    runtime_provider_id: str | None = None
    runtime_thread_id: str | None = None
    runtime_run_id: str | None = None
    current_node: str | None = None
    resume_token: str | None = None
    budget: dict[str, Any] | None = None
    evidence_refs: list[dict[str, Any]] | None = None
    artifact_receipts: list[dict[str, Any]] | None = None
    runtime_metadata: dict[str, Any] | None = None
    finished_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by_user_id: int | None = None
    cancellation_reason: str | None = Field(default=None, max_length=500)


class WorkflowRun(BaseModel):
    id: int
    conversation_id: int
    user_id: int
    project_id: str | None
    intent_mode: str
    capability_id: str
    provider_id: str
    status: WorkflowRunStatus
    version: int = 1
    # The originating chat message may contain evidence or credentials. Keep it
    # available to the runtime, but never serialize it through workflow APIs.
    input_summary: str | None = Field(default=None, exclude=True)
    output_summary: str | None = None
    artifact_links: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    runtime_provider_id: str | None = None
    runtime_thread_id: str | None = None
    runtime_run_id: str | None = None
    current_node: str | None = None
    resume_token: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_receipts: list[dict[str, Any]] = Field(default_factory=list)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by_user_id: int | None = None
    cancellation_reason: str | None = None


class WorkflowInterruptCreate(BaseModel):
    workflow_run_id: int
    owner_user_id: int
    interrupt_type: WorkflowInterruptType
    prompt: str = Field(min_length=1, max_length=2000)
    options: list[dict[str, Any]] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    scope_snapshot: dict[str, Any] = Field(default_factory=dict)
    argument_hash: str = Field(min_length=16, max_length=128)
    expires_at: datetime


class WorkflowInterrupt(BaseModel):
    id: int
    workflow_run_id: int
    owner_user_id: int = Field(exclude=True)
    interrupt_type: WorkflowInterruptType
    status: WorkflowInterruptStatus
    prompt: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict, exclude=True)
    scope_snapshot: dict[str, Any] = Field(default_factory=dict, exclude=True)
    argument_hash: str = Field(exclude=True)
    response_summary: str | None = Field(default=None, exclude=True)
    expires_at: datetime
    consumed_at: datetime | None = None


class WorkflowResumeRequest(BaseModel):
    interrupt_id: int
    expected_version: int = Field(ge=1)
    response: str = Field(min_length=1, max_length=4096)


class WorkflowCancelRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)


class WorkflowActionResponse(BaseModel):
    run: WorkflowRun
    message: str


class ActivityEventCreate(BaseModel):
    workflow_run_id: int
    event_type: str
    label: str
    status: ActivityEventStatus
    provider_id: str | None = None
    capability_id: str | None = None
    summary: str | None = None
    safe_details: dict[str, Any] = Field(default_factory=dict)
    runtime_node: str | None = None
    tool_call_id: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_receipts: list[dict[str, Any]] = Field(default_factory=list)
    occurred_at: datetime | None = None


class ActivityEvent(BaseModel):
    id: int
    workflow_run_id: int
    sequence: int
    event_type: str
    label: str
    status: ActivityEventStatus
    provider_id: str | None = None
    capability_id: str | None = None
    summary: str | None = None
    safe_details: dict[str, Any] = Field(default_factory=dict)
    runtime_node: str | None = None
    tool_call_id: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_receipts: list[dict[str, Any]] = Field(default_factory=list)
    occurred_at: datetime


class EvidenceItem(BaseModel):
    """Normalized, sanitized evidence emitted by harness capabilities."""

    id: str
    capability_id: str
    source_type: str
    title: str
    summary: str
    uri: str | None = None
    score: float | None = None
    observed_at: datetime | str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceQueryResult(BaseModel):
    capability_id: str
    status: str
    items: list[EvidenceItem] = Field(default_factory=list)
    error_message: str | None = None
    answer_draft: str | None = Field(default=None, max_length=12_000)


PROJECT_ONBOARDING_CAPABILITY = CapabilityDefinition(
    id="nexus.workflow.onboard_project",
    label="Create a project and onboard its source",
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.harness",
    action="onboard_project",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "repo_url": {"type": ["string", "null"]},
            "platform": {
                "type": ["string", "null"],
                "enum": list(SUPPORTED_PROJECT_PLATFORMS),
                "description": "Scanner platform of the codebase, not the code host.",
            },
            "installation_id": {"type": ["integer", "null"]},
        },
    },
    output_type="onboarding_workflow_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["onboarding"],
)

SOURCE_OPTIONS_CAPABILITY = CapabilityDefinition(
    id="mcp.projects.list_source_options",
    label="List project source options",
    provider_type=HarnessProviderType.SOURCE,
    provider_id="mcp.projects",
    action="list_source_options",
    input_schema={"type": "object", "properties": {}},
    output_type="source_options",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["onboarding"],
    metadata={"runtime_hidden": True},
)

ATTACH_GIT_SOURCE_CAPABILITY = CapabilityDefinition(
    id="mcp.projects.attach_git_source",
    label="Attach a Git repository to a project",
    provider_type=HarnessProviderType.SOURCE,
    provider_id="mcp.projects",
    action="attach_git_repository",
    input_schema={
        "type": "object",
        "required": ["project_id", "repo_url", "platform"],
        "properties": {
            "project_id": {"type": "string"},
            "repo_url": {"type": "string"},
            "platform": {
                "type": "string",
                "enum": list(SUPPORTED_PROJECT_PLATFORMS),
                "description": "Scanner platform of the codebase, not the code host.",
            },
            "installation_id": {"type": ["integer", "null"]},
            "display_name": {"type": ["string", "null"]},
        },
    },
    output_type="source_attachment_receipt",
    risk_tier=HarnessRiskTier.EXTERNAL_SIDE_EFFECT,
    required_permissions=["manage_projects"],
    packs=["onboarding"],
    metadata={"runtime_hidden": True},
)

ATTACH_ZIP_SOURCE_CAPABILITY = CapabilityDefinition(
    id="mcp.projects.attach_uploaded_zip_source",
    label="Attach an uploaded ZIP source to a project",
    provider_type=HarnessProviderType.SOURCE,
    provider_id="mcp.projects",
    action="verify_uploaded_zip_source",
    input_schema={
        "type": "object",
        "required": ["upload_receipt"],
        "properties": {"upload_receipt": {"type": "string"}},
        "additionalProperties": False,
    },
    output_type="source_attachment_receipt",
    risk_tier=HarnessRiskTier.EXTERNAL_SIDE_EFFECT,
    required_permissions=["manage_projects"],
    packs=["onboarding"],
    metadata={"runtime_hidden": True},
)

TRIGGER_SCAN_CAPABILITY = CapabilityDefinition(
    id="mcp.projects.trigger_scan",
    label="Scan a project source",
    provider_type=HarnessProviderType.SOURCE,
    provider_id="mcp.projects",
    action="trigger_scan",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
    },
    output_type="scan_workflow_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["onboarding"],
    metadata={"runtime_hidden": True},
)

PROJECT_STATUS_CAPABILITY = CapabilityDefinition(
    id="mcp.projects.read_status",
    label="Read project onboarding status",
    provider_type=HarnessProviderType.SOURCE,
    provider_id="mcp.projects",
    action="get_status",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
    },
    output_type="project_status",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["onboarding"],
    metadata={"runtime_hidden": True},
)

PUBLISH_INDEX_CAPABILITY = CapabilityDefinition(
    id="mcp.projects.publish_index",
    label="Publish project documentation to search",
    provider_type=HarnessProviderType.PUBLISHING,
    provider_id="mcp.projects",
    action="push_to_rag",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
    },
    output_type="index_workflow_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["onboarding", "documentation"],
    metadata={"runtime_hidden": True},
)


DOCUMENTATION_GENERATION_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.generate_project_documentation",
    label="Generate project documentation",
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="generate_project_documentation",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string"},
            "resume": {"type": "boolean"},
            "template_profile_id": {"type": ["string", "null"]},
            "model": {"type": ["string", "null"]},
            "write_model": {"type": ["string", "null"]},
            "plugin_pack_id": {"type": ["integer", "null"], "minimum": 1},
            "selected_pack_ids": {"type": "array"},
        },
    },
    output_type="workflow_trigger_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["documentation"],
    use_when=["The user asks to generate or regenerate documentation."],
    do_not_use_when=["The user asks a question about existing documentation."],
    task_types=["document_generation"],
    output_contracts=["workflow_receipt"],
)

DOCUMENTATION_GENERATION_STATUS_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.get_generation_status",
    label="Check documentation generation status",
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="get_documentation_generation_status",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
    },
    output_type="documentation_generation_status",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    metadata={
        "execution_surface": "existing_mcp_generation_run_ledger",
        "authoritative_for": "documentation_generation_status",
    },
)

DOCUMENT_SEARCH_CAPABILITY = CapabilityDefinition(
    id="rag.docs.search_evidence",
    label="Search indexed documentation",
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="rag.vector_db",
    action="search_document_evidence",
    input_schema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "project_id": {"type": ["string", "null"]},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=["The user asks to explain, inventory, compare, or plan from project documentation."],
    do_not_use_when=["The user asks only for current runtime state or incident logs."],
    task_types=["document_answer"],
    evidence_sources=["indexed_documentation"],
    output_contracts=["grounded_answer", "inventory", "migration_proposal"],
    metadata={"execution_surface": "existing_rag_retrieval"},
)

DOCUMENT_LIST_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.list_project_docs",
    label="List generated project documentation",
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="list_project_docs",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string"}},
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=["The user asks which generated documents or documented components exist."],
    do_not_use_when=["The user asks for live health, failures, or execution status."],
    task_types=["document_answer"],
    evidence_sources=["generated_documentation"],
    output_contracts=["grounded_answer", "inventory"],
    metadata={"execution_surface": "existing_mcp_docs_api"},
)

RUNTIME_EVIDENCE_CAPABILITY = CapabilityDefinition(
    id="runtime.tools.query_evidence",
    label="Query runtime evidence",
    provider_type=HarnessProviderType.RUNTIME,
    provider_id="runtime.tool_loop",
    action="query_runtime_evidence",
    input_schema={
        "type": "object",
        "required": ["question"],
        "properties": {
            "question": {"type": "string"},
            "project_id": {"type": ["string", "null"]},
            "project_ids": {"type": ["array", "null"], "items": {"type": "string"}},
            "source_key": {"type": ["string", "null"]},
            "since": {"type": ["string", "null"], "format": "date-time"},
            "until": {"type": ["string", "null"], "format": "date-time"},
        },
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "operations"],
    use_when=["The user explicitly asks about current or time-bounded runtime state, executions, or logs."],
    do_not_use_when=["The user asks what jobs or components do according to documentation."],
    task_types=["runtime_answer", "operational_investigation"],
    evidence_sources=["runtime_evidence"],
    output_contracts=["runtime_answer", "runtime_ranking", "incident_report"],
    metadata={"execution_surface": "existing_runtime_tool_loop"},
)

DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.update_from_evidence",
    label="Update documentation from provided evidence",
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="update_documentation_from_evidence",
    input_schema={
        "type": "object",
        "required": ["project_id", "target_doc", "evidence_refs", "evidence_text"],
        "properties": {
            "project_id": {"type": "string"},
            "target_doc": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "evidence_text": {"type": "string"},
            "workflow_run_id": {"type": ["string", "null"]},
            "section": {"type": ["string", "null"]},
            "expected_base_sha": {"type": ["string", "null"]},
        },
    },
    output_type="documentation_change_receipt",
    risk_tier=HarnessRiskTier.WRITES_CONTENT,
    health=CapabilityHealth.AVAILABLE,
    packs=["documentation"],
    metadata={
        "execution_surface": "mcp_evidence_doc_update",
        "requires_explicit_user_evidence": True,
        "requires_named_target_doc": True,
        "note": "Append-only update to a named markdown doc from explicit user-provided evidence.",
    },
)

DOCUMENT_REVERT_EVIDENCE_UPDATE_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.revert_evidence_update",
    label="Revert a documentation evidence update",
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="revert_documentation_evidence_update",
    input_schema={
        "type": "object",
        "required": ["project_id", "target_doc", "receipt_path", "expected_base_sha"],
        "properties": {
            "project_id": {"type": "string"},
            "target_doc": {"type": "string"},
            "receipt_path": {"type": "string"},
            "expected_base_sha": {"type": "string"},
            "workflow_run_id": {"type": ["string", "null"]},
        },
    },
    output_type="documentation_revert_receipt",
    risk_tier=HarnessRiskTier.WRITES_CONTENT,
    packs=["documentation"],
    metadata={"execution_surface": "mcp_evidence_doc_revert"},
)

EXTERNAL_CONNECTOR_READ_CAPABILITY = CapabilityDefinition(
    id="external.connector.read_evidence",
    label="Read evidence from an external connector",
    provider_type=HarnessProviderType.SOURCE,
    provider_id="external.connector_registry",
    action="read_external_evidence",
    input_schema={
        "type": "object",
        "required": ["connector_id", "query"],
        "properties": {
            "connector_id": {"type": "string"},
            "query": {"type": "string"},
            "project_id": {"type": ["string", "null"]},
        },
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    health=CapabilityHealth.AVAILABLE,
    packs=["connectors"],
    metadata={
        "execution_surface": "existing_runtime_source_registry",
        "auth_required": True,
    },
)

OPERATIONAL_INVESTIGATION_CAPABILITY = CapabilityDefinition(
    id="nexus.workflow.operational_investigation",
    label="Investigate operational issue",
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.harness",
    action="operational_investigation",
    input_schema={
        "type": "object",
        "required": ["question", "project_id"],
        "properties": {
            "question": {"type": "string"},
            "project_id": {"type": "string"},
        },
    },
    output_type="investigation_answer",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "operations", "documentation"],
    use_when=["The user describes a failure or symptom and asks for cause or diagnosis."],
    do_not_use_when=["The user asks for descriptive documentation, inventory, or migration planning."],
    task_types=["operational_investigation"],
    evidence_sources=["indexed_documentation", "generated_documentation", "runtime_evidence", "connector_evidence"],
    output_contracts=["incident_report"],
    metadata={
        "uses_capabilities": [
            DOCUMENT_SEARCH_CAPABILITY.id,
            DOCUMENT_LIST_CAPABILITY.id,
            RUNTIME_EVIDENCE_CAPABILITY.id,
        ]
    },
)
