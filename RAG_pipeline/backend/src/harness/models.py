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


class WorkflowRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    TRIGGERED = "triggered"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


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
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunCreate(BaseModel):
    conversation_id: int
    user_id: int
    project_id: str
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


class WorkflowRun(BaseModel):
    id: int
    conversation_id: int
    user_id: int
    project_id: str
    intent_mode: str
    capability_id: str
    provider_id: str
    status: WorkflowRunStatus
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
    started_at: datetime
    finished_at: datetime | None = None


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
        },
    },
    output_type="workflow_trigger_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["documentation"],
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
            "source_key": {"type": ["string", "null"]},
        },
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "operations"],
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
        "required": ["project_id", "target_doc", "evidence_refs"],
        "properties": {
            "project_id": {"type": "string"},
            "target_doc": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
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
    metadata={
        "uses_capabilities": [
            DOCUMENT_SEARCH_CAPABILITY.id,
            DOCUMENT_LIST_CAPABILITY.id,
            RUNTIME_EVIDENCE_CAPABILITY.id,
        ]
    },
)
