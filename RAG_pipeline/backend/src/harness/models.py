"""Provider-agnostic Nexus harness domain contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Final

from pydantic import BaseModel, Field

from src.harness.evidence_contracts import EvidenceSource


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
    REVERSIBLE_WRITE = "reversible_write"
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
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)
    output_contracts: list[str] = Field(default_factory=list)
    semantic_name: str | None = None
    purpose: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    failure_recovery: dict[str, str] = Field(default_factory=dict)
    shadow_safe: bool = False
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
    children: list["WorkflowChildReceipt"] = Field(default_factory=list)


class WorkflowChildReceipt(BaseModel):
    id: int
    workflow_run_id: int
    provider_id: str
    child_kind: str
    external_run_id: str
    project_id: str | None = None
    status: str
    provider_status: str
    version: int
    provider_started_at: datetime | None = None
    provider_finished_at: datetime | None = None
    provider_timestamp: datetime | None = None
    last_observed_at: datetime
    last_observation_source: str
    error_message: str | None = None
    cancellation_state: str = "none"
    cancellation_requested_at: datetime | None = None
    cancellation_provider_status: str | None = None
    cancellation_recorded_at: datetime | None = None


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


#: How much of one evidence item's ``summary`` survives serialization.
#:
#: Enforced in :func:`src.harness.capability_handlers._serialized_evidence_item`,
#: which truncates silently and leaves no marker. Named here, beside the type it
#: bounds, because a producer that hands over larger pieces than this loses the
#: tail of every one of them without anything saying so -- and a whole-document
#: read that silently drops its tail is the exact failure such a read exists to
#: remove. Producers of long text split against this constant.
EVIDENCE_SUMMARY_MAX_CHARS: Final[int] = 4_000


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
    #: What the query actually covered, stated to the caller.
    #:
    #: An empty ``items`` list is ambiguous without this, and the ambiguity has
    #: already produced a wrong client-facing answer: the agent searched a prose
    #: corpus that provably does not contain a rule, found nothing, and published
    #: "no filter is recorded" -- absence-from-corpus reported as
    #: absence-in-reality. Nothing in the result told it that the search had been
    #: restricted to ``document_role="source"`` in one project, so it could not
    #: have scoped its own negative even if it had wanted to.
    #:
    #: Free-form rather than a model because each capability covers a different
    #: shape of thing (a role filter, a document, a time window). Keys are
    #: documented at each producer, and the handler passes them through
    #: unmodified.
    scope: dict[str, Any] | None = None


def _closed_object(
    properties: dict[str, Any], *, required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _pack_brief_schema() -> dict[str, Any]:
    """Model-facing contract for authoring a pack: say what you want to know.

    There is one pack format (``plugin.yaml``) and one author (the MCP pack
    author). The model therefore supplies a BRIEF, not a structured artifact:
    it never hand-writes manifest keys, section ids, or evidence objects. That
    is what let a second, weaker pack format grow here in the first place.
    """
    return _closed_object(
        {
            "name": {"type": "string", "minLength": 1, "maxLength": 160},
            "brief": {
                "type": "string",
                "minLength": 12,
                "maxLength": 2000,
                "description": (
                    "What the pack should look for and what documents it should "
                    "produce, in the user's own words."
                ),
            },
            "platform": {
                "type": ["string", "null"],
                "pattern": r"^[a-z][a-z0-9_-]{0,49}$",
            },
        },
        required=["name", "brief"],
    )


_PACK_BRIEF_SCHEMA = _pack_brief_schema()


def _pack_refine_schema() -> dict[str, Any]:
    """Refinement is an instruction against an exact draft revision."""
    return _closed_object(
        {
            "draft_id": {"type": "integer", "minimum": 1},
            "expected_revision": {"type": "integer", "minimum": 1},
            "name": {"type": ["string", "null"], "maxLength": 160},
            "instruction": {
                "type": "string",
                "minLength": 3,
                "maxLength": 2000,
                "description": (
                    "What to change about the pack, in the user's own words -- "
                    "for example 'drop the scorecard' or 'add a DDL document'."
                ),
            },
        },
        required=["draft_id", "expected_revision", "instruction"],
    )


_PACK_REFINE_SCHEMA = _pack_refine_schema()

_STANDARD_FAILURE_RECOVERY = {
    "validation_failed": "Correct only the named invalid fields and retry once.",
    "approval_required": "Explain the pending proposal and wait for user approval.",
    "denied": "Respect the authority boundary and offer a permitted alternative.",
    "unavailable": "Report that the capability is unavailable without inventing a result.",
    "failed": "Report the safe failure summary and retry only when marked retryable.",
}


PACK_CREATE_CAPABILITY = CapabilityDefinition(
    id="nexus.packs.create",
    label="Create a complete private Analysis Pack",
    semantic_name="create_pack",
    purpose=(
        "Create and save one complete, usable private Analysis Pack in a single action. "
        "The action uses the governed Workbench Pack builder to generate the complete artifact, "
        "then validates it before any Pack is persisted."
    ),
    preconditions=[
        "The user explicitly asked to create the Pack now.",
        "Its name, purpose, audience, focus components, and output documents are known.",
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.packs",
    action="create_pack",
    input_schema=_PACK_BRIEF_SCHEMA,
    output_type="private_pack_version",
    risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
    required_permissions=["manage_packs"],
    packs=["core"],
    task_types=[],
    output_contracts=["private_pack_version"],
    use_when=[
        "The user wants a new private Analysis Pack created now and all required business "
        "details are available from the conversation or authoritative prepared action.",
        "Put the user's wording in `brief`. Do not invent manifest keys, section ids, "
        "or document schemas -- the pack author writes the manifest.",
    ],
    do_not_use_when=[
        "Any required business detail is missing or ambiguous; prepare the action and ask "
        "one focused question instead, without creating any database record.",
        "The user only asks whether Pack creation is available or how it works.",
        "The user asks to inspect, edit, or save an existing Pack or draft.",
    ],
    metadata={
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "agent_control_plane": True,
        "preparable_across_turns": True,
        "workflow_handoff": False,
        "tool_timeout_seconds": 60,
    },
)


# Internal primitive retained for the Pack Workbench and existing draft lifecycle.
# It is deliberately absent from every model-facing semantic tool snapshot.
PACK_DRAFT_CREATE_CAPABILITY = CapabilityDefinition(
    id="nexus.packs.drafts.create",
    label="Create a private Analysis Pack draft",
    semantic_name="create_pack_draft",
    purpose="Create one named private Analysis Pack draft for the current conversation.",
    preconditions=["The user explicitly asked to begin a new Pack draft and supplied its name."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.packs",
    action="create_pack_draft",
    input_schema={
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 160},
        },
        "additionalProperties": False,
    },
    output_type="pack_draft",
    risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
    required_permissions=["manage_packs"],
    packs=["core"],
    task_types=["pack_draft"],
    output_contracts=["pack_draft"],
    use_when=[
        "The user wants to begin a new private Analysis Pack draft.",
        "Send exactly one argument named name. Never send purpose, audience, focus, "
        "outputs, or artifact_patch to this tool; configure those after creation.",
    ],
    do_not_use_when=[
        "The user only asks whether Pack creation is available or how it works, without asking to start.",
        "The user asks to list, compare, or inspect existing Packs.",
    ],
    metadata={
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "agent_control_plane": True,
        "runtime_hidden": True,
    },
)


PACK_DRAFT_READ_CAPABILITY = CapabilityDefinition(
    id="nexus.packs.drafts.read",
    label="Read a private Analysis Pack draft",
    semantic_name="read_pack_draft",
    purpose="Read one exact private Analysis Pack draft owned by the current user.",
    preconditions=["An authorized positive draft identifier is known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.packs",
    action="read_pack_draft",
    input_schema={
        "type": "object",
        "required": ["draft_id"],
        "properties": {"draft_id": {"type": "integer", "minimum": 1}},
        "additionalProperties": False,
    },
    output_type="pack_draft",
    risk_tier=HarnessRiskTier.READ_ONLY,
    required_permissions=["manage_packs"],
    packs=["core"],
    task_types=["pack_draft"],
    use_when=["The user returns to or asks about an exact active Pack draft."],
    do_not_use_when=["The user asks to list Packs or has not identified an exact draft."],
    output_contracts=["pack_draft"],
    metadata={"bounded_loop_only": True, "agent_control_plane": True},
)


PACK_DRAFT_REFINE_CAPABILITY = CapabilityDefinition(
    id="nexus.packs.drafts.refine",
    label="Refine a pack draft",
    semantic_name="refine_pack_draft",
    purpose=(
        "Refine one pack draft from a plain-language instruction. The pack author "
        "applies it to the draft's current manifest and returns a complete updated "
        "manifest, so earlier choices are preserved."
    ),
    preconditions=["The exact draft identifier and current expected revision are known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.packs",
    action="refine_pack_draft",
    input_schema=_PACK_REFINE_SCHEMA,
    output_type="pack_draft",
    risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
    required_permissions=["manage_packs"],
    packs=["core"],
    task_types=["pack_draft"],
    use_when=[
        "The user supplies details or corrections for an active Pack draft.",
        "Put the user's wording in `instruction`. Do not write manifest keys, "
        "section ids, or document schemas -- the pack author writes the manifest.",
    ],
    do_not_use_when=["The user asks only to inspect, validate, save, or abandon the draft."],
    output_contracts=["pack_draft"],
    # Model-facing: this replaced both the structured-patch primitive and the
    # friendly `configure` capability, and `configure` was the one the agent
    # loop used. Keeping `runtime_hidden` here would leave the loop with no way
    # to change a draft at all.
    metadata={
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "agent_control_plane": True,
    },
)


PACK_DRAFT_VALIDATE_CAPABILITY = CapabilityDefinition(
    id="nexus.packs.drafts.validate",
    label="Validate a private Analysis Pack draft",
    semantic_name="validate_pack_draft",
    purpose="Validate whether one exact private Analysis Pack draft can be saved.",
    preconditions=["The exact draft identifier and current expected revision are known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.packs",
    action="validate_pack_draft",
    input_schema={
        "type": "object",
        "required": ["draft_id", "expected_revision"],
        "properties": {
            "draft_id": {"type": "integer", "minimum": 1},
            "expected_revision": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    },
    output_type="pack_draft_validation",
    risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
    required_permissions=["manage_packs"],
    packs=["core"],
    task_types=["pack_draft"],
    use_when=["The user wants to check whether an exact Pack draft can be saved."],
    do_not_use_when=["The user asks to change, save, or abandon the draft."],
    output_contracts=["pack_draft_validation"],
    metadata={
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "agent_control_plane": True,
    },
)


PACK_DRAFT_SAVE_CAPABILITY = CapabilityDefinition(
    id="nexus.packs.drafts.save",
    label="Save an immutable private Analysis Pack version",
    semantic_name="save_pack",
    purpose="Save a validated private Analysis Pack draft as an immutable version.",
    preconditions=[
        "The exact draft identifier and current expected revision are known.",
        "The user explicitly requested save and approval is available.",
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.packs",
    action="save_pack_draft",
    input_schema={
        "type": "object",
        "required": ["draft_id", "expected_revision"],
        "properties": {
            "draft_id": {"type": "integer", "minimum": 1},
            "expected_revision": {"type": "integer", "minimum": 1},
            "pack_id": {"type": ["string", "null"], "maxLength": 80},
        },
        "additionalProperties": False,
    },
    output_type="private_pack_version",
    risk_tier=HarnessRiskTier.WRITES_CONTENT,
    required_permissions=["manage_packs"],
    packs=["core"],
    task_types=["pack_draft"],
    use_when=["The user explicitly asks to save the current valid Pack draft."],
    do_not_use_when=["The draft is incomplete, unvalidated, or the user asks only for advice."],
    output_contracts=["private_pack_version"],
    metadata={
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "requires_explicit_approval": True,
        "requires_approval": True,
        "agent_control_plane": True,
    },
)


PACK_DRAFT_ABANDON_CAPABILITY = CapabilityDefinition(
    id="nexus.packs.drafts.abandon",
    label="Abandon a private Analysis Pack draft",
    semantic_name="abandon_pack_draft",
    purpose="Abandon one exact private Analysis Pack draft after explicit approval.",
    preconditions=[
        "The exact draft identifier and current expected revision are known.",
        "The user explicitly requested abandonment and approval is available.",
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.packs",
    action="abandon_pack_draft",
    input_schema={
        "type": "object",
        "required": ["draft_id", "expected_revision"],
        "properties": {
            "draft_id": {"type": "integer", "minimum": 1},
            "expected_revision": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    },
    output_type="pack_draft",
    risk_tier=HarnessRiskTier.WRITES_CONTENT,
    required_permissions=["manage_packs"],
    packs=["core"],
    task_types=["pack_draft"],
    use_when=["The user explicitly asks to abandon an exact Pack draft."],
    do_not_use_when=["The user asks to pause, inspect, or save the draft."],
    output_contracts=["pack_draft"],
    metadata={
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "requires_explicit_approval": True,
        "requires_approval": True,
        "agent_control_plane": True,
    },
)


ONBOARDING_RESUME_CAPABILITY = CapabilityDefinition(
    id="nexus.workflow.resume_onboarding",
    label="Provide corrected input to an interrupted onboarding workflow",
    semantic_name="resume_onboarding",
    purpose="Resume one interrupted project-onboarding workflow with corrected bounded input.",
    preconditions=["The exact workflow run and expected version are known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.harness",
    action="resume_onboarding",
    input_schema={
        "type": "object",
        "required": ["workflow_run_id", "expected_version", "inputs"],
        "properties": {
            "workflow_run_id": {"type": "integer", "minimum": 1},
            "expected_version": {"type": "integer", "minimum": 1},
            "inputs": {
                "type": "object",
                "properties": {
                    "source_type": {"type": "string", "enum": ["git", "zip"]},
                    "repo_url": {"type": "string"},
                    "upload_receipt": {"type": "string"},
                    "platform": {
                        "type": "string",
                        "enum": list(SUPPORTED_PROJECT_PLATFORMS),
                    },
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    },
    output_type="onboarding_workflow_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["onboarding"],
    task_types=["project_onboarding"],
    use_when=["The user supplies or corrects requested input for an exact onboarding task."],
    do_not_use_when=["No interrupted onboarding workflow or requested correction is identified."],
    output_contracts=["onboarding_workflow_receipt"],
    metadata={
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "agent_control_plane": True,
    },
)


PROJECT_ONBOARDING_CAPABILITY = CapabilityDefinition(
    id="nexus.workflow.onboard_project",
    label="Create a project and onboard its source",
    semantic_name="onboard_project",
    purpose="Propose onboarding one project and its authorized Git or uploaded ZIP source.",
    preconditions=[
        "The user explicitly asked to onboard a project and supplied its project identity."
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.harness",
    action="onboard_project",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "repo_url": {"type": ["string", "null"], "maxLength": 2048},
            "source_type": {
                "type": ["string", "null"],
                "enum": ["git", "zip", None],
            },
            "platform": {
                "type": ["string", "null"],
                "enum": [*SUPPORTED_PROJECT_PLATFORMS, None],
                "description": "Scanner platform of the codebase, not the code host.",
            },
            "installation_id": {"type": ["integer", "null"]},
            "upload_receipt": {"type": ["string", "null"], "minLength": 32},
            "documentation_pack": {"type": ["string", "null"], "maxLength": 160},
            "plugin_pack_id": {"type": ["integer", "null"], "minimum": 1},
        },
        "additionalProperties": False,
    },
    output_type="onboarding_workflow_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["onboarding"],
    task_types=["project_onboarding"],
    use_when=["The user explicitly asks to onboard or connect one project source."],
    do_not_use_when=[
        "The user asks how onboarding works or asks only for status.",
        "The user asks to be briefed, oriented, or brought up to speed on an "
        "existing project. That is a documentation question, not a request to "
        "create or connect a project source; use answer_from_documentation.",
    ],
    output_contracts=["onboarding_workflow_receipt"],
    metadata={"requires_turn_claim": True},
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
    semantic_name="start_documentation_generation",
    purpose=(
        "Start the long-running workflow that generates or regenerates governed "
        "documentation for one project. This creates documentation; it does not "
        "answer a question about documents that already exist."
    ),
    preconditions=["An authorized project identifier is known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="generate_project_documentation",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "resume": {"type": "boolean"},
            "doc_generation_provider": {
                "type": ["string", "null"],
                "enum": ["custom", "opencode-cli", None],
            },
            "template_profile_id": {"type": ["string", "null"], "maxLength": 160},
            "investigation_model": {"type": ["string", "null"], "maxLength": 160},
            "model": {"type": ["string", "null"], "maxLength": 160},
            "write_model": {"type": ["string", "null"], "maxLength": 160},
            "plugin_pack_id": {"type": ["integer", "null"], "minimum": 1},
            "documentation_pack": {
                "type": ["string", "null"],
                "description": "Human-readable reusable documentation Pack name.",
            },
            "selected_pack_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "maxItems": 50,
            },
        },
        "additionalProperties": False,
    },
    output_type="workflow_trigger_receipt",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    required_permissions=["manage_projects"],
    packs=["documentation"],
    use_when=["The user asks to generate or regenerate documentation."],
    do_not_use_when=[
        "The user asks a question about existing documentation.",
        "The user asks whether or how documentation generation or Packs can be used.",
    ],
    task_types=["document_generation"],
    output_contracts=["workflow_receipt"],
    # Its handler routes through `ConversationTaskService._claim`, which raises
    # unless the context carries a turn claim -- exactly like the three
    # `nexus.workflow.*` capabilities that already declare this. Without the
    # flag the policy engine never issues a clean `turn_claim` denial, so the
    # RuntimeError surfaced as an undiagnosable `handler_failure`, and the labs
    # MCP bridge advertised a tool that could only ever fail.
    metadata={"requires_turn_claim": True},
)


DOCUMENTATION_PACK_LIST_CAPABILITY = CapabilityDefinition(
    id="nexus.docs.list_documentation_packs",
    label="List available documentation Packs",
    semantic_name="list_documentation_packs",
    purpose="List documentation Packs available to the current authorized user.",
    preconditions=["The request concerns Pack availability or ownership."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="nexus.documentation-packs",
    action="list_documentation_packs",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["all", "organization", "private"],
                "description": "Which user-facing documentation Packs to list.",
            }
        },
        "additionalProperties": False,
    },
    output_type="documentation_pack_catalog",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["documentation"],
    use_when=[
        "The user asks which documentation, Swap, Plugin, organization, or private Packs are available.",
        "The user asks whether they own a private Pack.",
    ],
    do_not_use_when=[
        "The user asks which tools or operational capabilities Nexus has.",
        "The user asks to list generated documents for a project.",
        "The user asks to explain a project from indexed or Pack-generated document content; search documentation instead because the Pack catalog contains templates and configuration, not project evidence.",
        "The user asks to generate, create, or publish project documentation.",
        "The user asks whether they can create a Pack or asks to begin a new private Pack draft.",
    ],
    task_types=["documentation_pack_management"],
    output_contracts=["documentation_pack_catalog"],
    metadata={},
)

DOCUMENTATION_GENERATION_STATUS_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.get_generation_status",
    label="Check documentation generation status",
    semantic_name="get_documentation_generation_status",
    purpose="Read documentation-generation status for one authorized project.",
    preconditions=["An authorized project identifier is known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="get_documentation_generation_status",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string", "minLength": 1, "maxLength": 255}},
        "additionalProperties": False,
    },
    output_type="documentation_generation_status",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "The user asks for documentation generation status for one explicitly selected project."
    ],
    do_not_use_when=[
        "The user asks which accessible project has any active onboarding, scan, or documentation workflow."
    ],
    task_types=["documentation_generation_status"],
    output_contracts=["documentation_generation_status"],
    metadata={
        "execution_surface": "existing_mcp_generation_run_ledger",
        "authoritative_for": "documentation_generation_status",
    },
)

WORKFLOW_CANCELLATION_CAPABILITY = CapabilityDefinition(
    id="nexus.workflows.cancel",
    label="Cancel an active workflow",
    semantic_name="cancel_workflow",
    purpose="Propose cancellation of one authorized active workflow.",
    preconditions=["The user explicitly asked to cancel an identifiable active workflow."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.WORKFLOW,
    provider_id="nexus.workflow-ledger",
    action="cancel_workflow",
    input_schema={
        "type": "object",
        "properties": {
            "workflow_reference": {
                "type": ["string", "null"],
                "description": "Authorized project or workflow reference; null means current/latest.",
            }
        },
        "additionalProperties": False,
    },
    output_type="workflow_cancellation_outcome",
    risk_tier=HarnessRiskTier.EXTERNAL_SIDE_EFFECT,
    packs=["core", "documentation", "onboarding"],
    use_when=[
        "The user explicitly asks to cancel or stop a current workflow.",
    ],
    do_not_use_when=[
        "The user only asks for workflow status or describes a historical cancellation.",
    ],
    task_types=["workflow_cancellation"],
    output_contracts=["workflow_cancellation_outcome"],
    metadata={
        "execution_surface": "owner_scoped_workflow_resolution_and_provider_cancellation",
    },
)

PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY = CapabilityDefinition(
    id="mcp.projects.list_active_workflows",
    label="List active project onboarding, scan, and documentation workflows",
    semantic_name="list_active_project_workflows",
    purpose="List active onboarding, scan, and documentation workflows in server-owned scope.",
    preconditions=["The request concerns current workflow status across accessible projects."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.SOURCE,
    provider_id="mcp.projects",
    action="list_active_project_workflows",
    input_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    output_type="active_project_workflows",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "onboarding", "documentation"],
    use_when=[
        "The user asks which accessible project is currently being onboarded, scanned, or having documentation generated.",
        "The user asks for active project workflow status across projects without naming one project.",
    ],
    do_not_use_when=[
        "The user asks for application runtime logs, job failures, or a time-bounded operational investigation.",
        "The user asks for documentation generation status for one explicitly selected project.",
    ],
    task_types=["project_workflow_status"],
    output_contracts=["active_project_workflows"],
    metadata={
        "execution_surface": "rbac_filtered_mcp_project_and_generation_ledgers",
        "scope_authority": "server",
    },
)

DOCUMENT_SEARCH_CAPABILITY = CapabilityDefinition(
    id="rag.docs.search_evidence",
    label="Search indexed documentation",
    semantic_name="search_documentation",
    purpose=(
        "Primary discovery tool for ordinary questions about indexed project "
        "documentation. Returns bounded relevance-ranked passages with document identity "
        "and scope; it can locate evidence but cannot prove a list or count is complete."
    ),
    preconditions=["A concrete documentation question is available."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="rag.vector_db",
    action="search_document_evidence",
    input_schema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 4000},
            "project_id": {"type": ["string", "null"], "maxLength": 255},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "Start here for a normal documentation question when the authoritative document "
        "or section is not already known.",
        "Use it to locate evidence about business processes, systems, data, architecture, "
        "operations, controls, integrations, implementation, rules, and mappings.",
        "Use it alongside runtime evidence when the question combines documented design "
        "with current operational state.",
    ],
    do_not_use_when=[
        "The user asks only for current runtime state or incident logs.",
        "The user asks for a complete list or exact count inside one identified document; "
        "use read_document because ranked search cannot guarantee completeness.",
        "The user explicitly excludes documentation for this turn. Honor that exclusion; "
        "if documentation is required for the requested explanation, disclose that the "
        "explanatory portion cannot be grounded.",
    ],
    task_types=["document_answer"],
    evidence_sources=["indexed_documentation"],
    output_contracts=["grounded_answer", "inventory", "migration_proposal"],
    metadata={"execution_surface": "existing_rag_retrieval"},
)

DOCUMENTATION_ANSWER_CAPABILITY = CapabilityDefinition(
    id="rag.docs.answer_question",
    label="Answer a question from indexed documentation",
    semantic_name="answer_from_documentation",
    purpose=(
        "Answer one self-contained explanatory question from indexed project "
        "documentation in a single deterministic step, using the existing grounded "
        "retrieval-and-generation pipeline."
    ),
    preconditions=["A concrete documentation question is available."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="rag.vector_db",
    action="answer_document_question",
    input_schema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 4000},
            "project_id": {"type": ["string", "null"], "maxLength": 255},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    },
    output_type="grounded_answer",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "This is the DEFAULT choice when the user asks what a project, system, job, "
        "component, or integration does; why it exists; how it is implemented in "
        "explanatory terms; or which business rules and mappings it applies.",
        "Use it for documentation-grounded explanations, deep dives, 'tell me more', "
        "and summaries of a single topic or component. Rewrite follow-ups into a "
        "standalone question, expanding pronouns like 'those' or 'it' from the "
        "conversation.",
        "For a mixed question that also needs topology or runtime evidence, call the "
        "needed topology and runtime tools first, then call this terminal documentation "
        "answer only after those observations are present in the current turn.",
    ],
    do_not_use_when=[
        "The request also needs topology or runtime evidence that has not been read yet; "
        "call those read tools first because this documentation answer is terminal.",
        "The user asks only for complete inventory, counts, connections, or dependency "
        "traversal; use topology rather than similarity search.",
        "The user explicitly excludes documentation for this turn. Honor the exclusion "
        "or disclose that the requested explanatory claim cannot be grounded.",
        "The user only wants raw passages, not a written answer.",
    ],
    task_types=["document_answer"],
    evidence_sources=["indexed_documentation"],
    output_contracts=["grounded_answer"],
    metadata={"execution_surface": "existing_rag_answer_pipeline"},
)

#: Similarity search restricted to one document -- NOT a read of that document.
#:
#: The semantic name says `search_within_document` because that is what the
#: handler does: it embeds ``query``, searches the chunks of one document, and
#: returns the top ``max_chunks`` by similarity, each carrying a score. It was
#: called `read_document` for a year, and the name is why an agent reported an
#: uploaded field mapping as 30 fields when the table has 31 rows -- it had read
#: eight similarity-ranked fragments of a table and had every reason, from the
#: tool's own name, to believe it had read the file.
#:
#: The capability ID is deliberately unchanged. It is persisted in conversation
#: history, referenced by the executor's project-canonicalization set and named
#: in pack fixtures; renaming it would invalidate all three to fix a problem that
#: only exists in what the MODEL sees. The MCP tool name is derived from
#: ``semantic_name`` (``src/labs/mcp_bridge.py::_tool_name_for``), so changing
#: that alone changes exactly the surface that was misleading.
DOCUMENT_READ_CAPABILITY = CapabilityDefinition(
    id="rag.docs.read_document",
    label="Search the passages of one indexed document",
    semantic_name="search_within_document",
    purpose=(
        "Similarity-search the chunks of ONE authorized indexed document and return the "
        "best-matching passages with their scores. This is a ranked search inside a "
        "document, not a read of it: passages that do not match the query are not "
        "returned, and nothing about the result says how much of the document was left "
        "out. Use read_document when completeness matters."
    ),
    preconditions=["An authorized project and positive document identifier are known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="rag.vector_db",
    action="read_document_passages",
    input_schema={
        "type": "object",
        "required": ["query", "project_id", "document_id"],
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 4000},
            "project_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "document_id": {"type": "integer", "minimum": 1},
            "max_chunks": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "A prior search or document list identified a large document and you need only "
        "the passages about one narrow topic in it.",
    ],
    do_not_use_when=[
        "The question asks for all, every, each, the complete list or the full set of "
        "anything -- fields, rows, columns, steps, rules. Ranked passages cannot support "
        "a completeness claim; use read_document.",
        "No authorized project and document identity is available; search or list documents first.",
        "The inventory already answers which documents, components, or job names exist; answer directly from the inventory instead of opening every document.",
    ],
    task_types=["document_answer"],
    evidence_sources=["indexed_documentation"],
    output_contracts=["grounded_answer"],
    metadata={
        "execution_surface": "existing_rag_retrieval",
        "evidence_output": True,
        "bounded_loop_only": True,
    },
)

#: The whole-document read the lane never had.
#:
#: Two paths existed before this and neither worked for an uploaded project.
#: ``mcp.docs.read_project_doc`` returns a complete file but only for documents
#: under ``output/{project_id}/docs/``, which an uploaded project does not have;
#: ``document_texts`` held the full text but ingestion only wrote a row for small
#: non-evidence documents, which measured 3 rows against 551 documents. Both are
#: addressed together: ingestion now stores every document's text, and this
#: capability is how an agent gets at it.
#:
#: The mode argument is the whole design. A 120 KB document is roughly 30k
#: tokens, so an unconditional dump would spend a quarter of the context window
#: on one call and do it silently. ``outline`` is therefore the default and is
#: cheap -- headings and their sizes, nothing else -- so the normal shape of use
#: is "see the structure, then ask for the part that matters". ``full`` stays
#: available because some questions genuinely need the whole thing, and it always
#: reports the size it is about to spend.
#:
#: ``sections`` -- the plural -- was added after the first measurement of that
#: shape in use. Asked to walk through one integration, an agent read the outline
#: and then made sixteen consecutive single-section calls; an earlier run of the
#: same question made thirty-four calls and read 31 of 32 sections. The bytes
#: were not the problem, the call count was: every call re-sends the
#: conversation, which is why the 34-call run charged 933,504 cached tokens
#: against 170,368 for the 19-call one. Choosing what to read is still the point,
#: so the outline stays first; asking for the chosen set one piece at a time
#: never was, so the plural argument exists and every description below names it
#: as the normal way to read more than one section.
DOCUMENT_FULL_TEXT_CAPABILITY = CapabilityDefinition(
    id="rag.docs.read_document_text",
    label="Read one document whole, or named sections of it",
    semantic_name="read_document",
    purpose=(
        "Read one indexed document in document order: its outline, any number of "
        "complete sections in a single call, or (only when small enough) the complete "
        "document. Identify the document by 'document_id', or by 'filename' when "
        "another document referred to a file by name and you never saw its id. Use the "
        "outline to choose, then name every section you need in one 'sections' call; "
        "this is the completeness tool for exact counts, complete mappings, "
        "procedures, and rules."
    ),
    preconditions=[
        "An authorized project is known, together with either the document's "
        "identifier or its filename."
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="rag.document_text",
    action="read_document_text",
    input_schema={
        "type": "object",
        # Only the project is structurally required. "exactly one of document_id or
        # filename" is enforced in the reader instead of as a schema `oneOf`,
        # because a schema violation is reported to the caller as an opaque
        # validation denial naming a field path. This tool exists because a model
        # could not recover from a dead end, so its argument errors have to say
        # what to do next, and only the reader can phrase that.
        "required": ["project_id"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "document_id": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "The document to read, by identifier. Supply exactly one of "
                    "'document_id' or 'filename'."
                ),
            },
            "filename": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": (
                    "The document to read, by name instead of by identifier. Use this "
                    "when another document referred to a file by name -- 'the fields "
                    "are listed in 13_VELDINDEX.md' -- and you never saw its id, "
                    "rather than guessing search queries for its content. Matching is "
                    "inside this project only and case-insensitive, against the last "
                    "part of the stored path with or without its extension: both "
                    "'13_VELDINDEX.md' and '13_VELDINDEX' find a document stored as "
                    "'PROJECT/13_VELDINDEX.md'. If the name fits more than one "
                    "document the call fails and lists every candidate with its id, so "
                    "ask again with 'document_id'. Supply exactly one of 'document_id' "
                    "or 'filename'."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["outline", "section", "full"],
                "default": "outline",
                "description": (
                    "Ignored when 'sections' is given. outline (default): headings and "
                    "their sizes, so you can choose what to read. section: the full text "
                    "of the single section named in 'section'. full: the entire document "
                    "only when it is below the safe transport limit; otherwise this "
                    "returns the outline."
                ),
            },
            "sections": {
                "type": "array",
                "minItems": 1,
                "maxItems": 24,
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "description": (
                    "The normal way to read more than one section, and it overrides "
                    "'mode'. Pass every section you need in one array -- outline ids "
                    "such as ['s6','s9','s12'], or headings, matched exactly as "
                    "'section' matches them. All of them come back complete and in "
                    "document order from this one call, up to 28000 characters; ids "
                    "that match nothing are reported without failing the call, and "
                    "anything over the budget is named so you can ask for the "
                    "remainder. Never call this tool repeatedly for one section at a "
                    "time: each call re-sends the whole conversation and reads the "
                    "same text."
                ),
            },
            "section": {
                "type": "string",
                "maxLength": 500,
                "description": (
                    "One single section, for mode='section'. Pass the outline id (for "
                    "example s3) or heading reported by the outline. Heading matching is "
                    "case-insensitive and accepts the trailing part of a heading path. "
                    "Use 'sections' whenever you need more than one."
                ),
            },
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "The question asks for all, every, each, the complete list or the full set of "
        "anything inside one document -- fields, rows, mappings, steps, conditions.",
        "A count must be right: reading in document order is the only way to know that "
        "nothing was left out.",
        "A search identified an authoritative document and you need one complete section.",
        "Several sections of one document bear on the question -- name them all in a "
        "single 'sections' call rather than one call per section.",
        "A document you have already read points at another file by name -- 'the "
        "per-field table is in 13_VELDINDEX.md' -- and you do not have its id: pass "
        "that name as 'filename' instead of searching for its contents.",
    ],
    do_not_use_when=[
        "You do not yet know which document; search or list documents first.",
        "The question is about one narrow topic inside a large document and a ranked "
        "passage is sufficient; search_within_document is cheaper.",
        "You have already outlined this document and are about to call again for the "
        "next section: the sections you still need belong in one 'sections' call, not "
        "in a call each.",
    ],
    task_types=["document_answer"],
    evidence_sources=["indexed_documentation"],
    output_contracts=["grounded_answer", "inventory"],
    metadata={
        "execution_surface": "stored_document_text",
        "evidence_output": True,
        "bounded_loop_only": True,
    },
)

DOCUMENT_LIST_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.list_project_docs",
    label="List generated project documentation",
    semantic_name="list_generated_documents",
    purpose=(
        "List generated Markdown artifacts for one authorized project. This is the output "
        "inventory of the documentation-generation workflow, not the project's indexed "
        "document catalogue used by search_documentation."
    ),
    preconditions=["An authorized project identifier is known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="list_project_docs",
    input_schema={
        "type": "object",
        "required": ["project_id"],
        "properties": {"project_id": {"type": "string", "minLength": 1, "maxLength": 255}},
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "The user asks which generated documentation artifacts exist for one project.",
        "A documentation-generation workflow needs its output inventory.",
    ],
    do_not_use_when=[
        "The conversational agent needs facts from document content; use indexed documentation search.",
        "The user asks for live health, failures, or execution status.",
    ],
    task_types=["document_answer"],
    evidence_sources=["generated_documentation"],
    output_contracts=["grounded_answer", "inventory"],
    metadata={"execution_surface": "existing_mcp_docs_api"},
)

GENERATED_DOCUMENT_READ_CAPABILITY = CapabilityDefinition(
    id="mcp.docs.read_project_doc",
    label="Read generated project documentation",
    semantic_name="read_generated_document",
    purpose=(
        "Read one generated Markdown artifact by exact filename. This reads workflow "
        "output directly; it does not search the indexed documentation corpus."
    ),
    preconditions=["An authorized project identifier and generated Markdown filename are known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="mcp.docs",
    action="read_project_doc",
    input_schema={
        "type": "object",
        "required": ["project_id", "filename"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "filename": {
                "type": "string",
                "minLength": 4,
                "maxLength": 255,
                "pattern": r"^[^/\\]+\.md$",
            },
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "The user asks to inspect one exact generated Markdown artifact, or a generation "
        "workflow needs to validate its output."
    ],
    do_not_use_when=[
        "The conversational agent needs facts from document content; use indexed documentation search.",
        "No exact generated Markdown filename is known; use list_generated_documents first.",
        "The user asks for live health, failures, or execution status.",
    ],
    task_types=["document_answer"],
    evidence_sources=["generated_documentation"],
    output_contracts=["grounded_answer"],
    metadata={"execution_surface": "existing_mcp_docs_content_api"},
)

METADATA_QUERY_CAPABILITY = CapabilityDefinition(
    id="nexus.metadata.query",
    label="Query authorized project and document metadata",
    semantic_name="query_project_metadata",
    purpose=(
        "Query bounded metadata for authorized projects, indexed documents, or the "
        "current profile. Its document list describes the searchable corpus, not "
        "generated Markdown workflow artifacts."
    ),
    preconditions=["A supported metadata query type is known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="nexus.metadata",
    action="query_metadata",
    input_schema={
        "type": "object",
        "required": ["query_type"],
        "properties": {
            "query_type": {
                "type": "string",
                "enum": [
                    "project_list",
                    "project_detail",
                    "document_stats",
                    "document_list",
                    "system_overview",
                    "my_profile",
                ],
                "description": (
                    "Metadata view to return. document_list and document_stats describe "
                    "indexed documents; use list_generated_documents for generation outputs."
                ),
            },
            "project_id": {"type": ["string", "null"], "maxLength": 255},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    },
    output_type="metadata_evidence",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "The user asks which authorized projects or documents exist, their counts, or structural project details."
    ],
    do_not_use_when=[
        "The request needs document content, live runtime evidence, or a state-changing action."
    ],
    task_types=["metadata_answer"],
    evidence_sources=["indexed_documentation"],
    output_contracts=["grounded_answer", "inventory"],
    metadata={
        "execution_surface": "existing_metadata_retriever",
        "evidence_output": True,
        "bounded_loop_only": True,
    },
)

CONVERSATION_SUMMARY_FACTS_CAPABILITY = CapabilityDefinition(
    id="nexus.conversations.read_summary_facts",
    label="Read bounded facts from this conversation",
    semantic_name="read_conversation_summary",
    purpose="Read bounded facts from the current authorized conversation only.",
    preconditions=["The server supplied the current conversation identity."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    shadow_safe=True,
    provider_type=HarnessProviderType.RETRIEVAL,
    provider_id="nexus.conversation_state",
    action="read_conversation_summary_facts",
    input_schema={
        "type": "object",
        "properties": {
            "max_messages": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
    output_type="conversation_facts",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=["The user asks to recap or summarize the current authorized conversation."],
    do_not_use_when=["The user asks to summarize a project document or another conversation."],
    task_types=["conversation_summary"],
    output_contracts=["grounded_answer"],
    metadata={
        "execution_surface": "bounded_authorized_conversation_history",
        "bounded_loop_only": True,
    },
)

STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY = CapabilityDefinition(
    id="runtime.stored.query_evidence",
    label="Read structured runtime evidence",
    semantic_name="read_runtime_evidence",
    purpose=(
        "Read bounded present-tense operational evidence about executions, failures, "
        "health, and synchronization from server-owned stored records."
    ),
    preconditions=["A supported runtime evidence operation is known."],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.RUNTIME,
    provider_id="runtime.stored",
    action="query_structured_runtime_evidence",
    input_schema={
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "list_executions",
                    "detect_failures",
                    "list_runtime_health",
                    "list_runtime_sync_status",
                    "list_runtime_sources",
                ],
            },
            "project_id": {"type": ["string", "null"], "maxLength": 255},
            "source_key": {"type": ["string", "null"], "maxLength": 255},
            "job_name": {"type": ["string", "null"], "maxLength": 500},
            "state": {
                "type": ["string", "null"],
                "enum": ["success", "failed", "running", "unknown", None],
            },
            "since": {"type": ["string", "null"], "format": "date-time"},
            "until": {"type": ["string", "null"], "format": "date-time"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "operations"],
    use_when=[
        "The user asks for present-tense or time-bounded runtime executions, failures, "
        "health, sync status, or runtime source inventory.",
        "The user asks for failure or execution facts while excluding raw log search; structured stored records remain available independently of logs.",
        "A mixed impact question asks both what structurally depends on an entity and "
        "whether it is healthy now; read runtime evidence as well as topology before "
        "the terminal answer step.",
    ],
    do_not_use_when=[
        "The user asks about documented design rather than observed runtime state.",
        "The user specifically requests raw or textual log evidence rather than structured execution records.",
        "The user explicitly excludes runtime evidence for this turn. Honor the "
        "exclusion or disclose that present-tense claims cannot be verified.",
    ],
    task_types=["runtime_answer", "operational_investigation"],
    evidence_sources=["runtime_evidence"],
    output_contracts=["runtime_answer", "runtime_ranking", "incident_report"],
    metadata={
        "execution_surface": "deterministic_stored_runtime_provider",
        "evidence_output": True,
        "bounded_loop_only": True,
    },
)

LOG_SEARCH_EVIDENCE_CAPABILITY = CapabilityDefinition(
    id="runtime.logs.search_evidence",
    label="Search runtime logs",
    semantic_name="search_runtime_logs",
    purpose=(
        "Search bounded redacted present-tense runtime log evidence for an operational "
        "question."
    ),
    preconditions=[
        "A keyword to match, or a time window for a recency-only read.",
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.LOG,
    provider_id="runtime.stored",
    action="search_logs",
    input_schema={
        "type": "object",
        # `question` is optional: empty selects newest-first instead of keyword
        # matching. Requiring it made "show me the latest logs" unexpressible,
        # so the model sent the user's whole sentence as a keyword and matched
        # nothing.
        "required": [],
        "properties": {
            "question": {"type": "string", "maxLength": 4000},
            "project_id": {"type": ["string", "null"], "maxLength": 255},
            "project_ids": {
                "type": ["array", "null"],
                "items": {"type": "string", "minLength": 1, "maxLength": 255},
                "maxItems": 50,
            },
            "execution_id": {"type": ["string", "null"], "maxLength": 255},
            "since": {"type": ["string", "null"], "format": "date-time"},
            "until": {"type": ["string", "null"], "format": "date-time"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "operations"],
    use_when=[
        "The user asks to search log content for a phrase, exception, symptom, or error.",
        "An operational investigation needs bounded log evidence for a known or suspected incident.",
    ],
    do_not_use_when=[
        "The user needs a complete raw log dump or unredacted diagnostic payload.",
        "The question is only about documentation or runtime inventory without log content.",
        "The user explicitly excludes logs for this turn. Honor the exclusion or "
        "disclose that log-specific claims cannot be verified.",
    ],
    task_types=["runtime_answer", "operational_investigation"],
    evidence_sources=[EvidenceSource.RUNTIME_EVIDENCE],
    output_contracts=["runtime_answer", "incident_report"],
    metadata={
        "execution_surface": "guarded_harness_executor",
        "evidence_output": True,
        "redaction_policy": {
            "credential_redaction": "required",
            "snippet_max_chars": 360,
            "max_items": 20,
            "raw_payload": "forbidden",
        },
    },
)

DOCUMENT_UPDATE_PROPOSAL_CAPABILITY = CapabilityDefinition(
    id="nexus.workflow.propose_documentation_update",
    label="Propose an evidence-backed documentation update",
    semantic_name="propose_documentation_update",
    purpose="Propose a bounded evidence-backed update to one named project document.",
    preconditions=[
        "An authorized project, named target document, and explicit evidence are known."
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.DOCUMENTATION,
    provider_id="nexus.workflow",
    action="propose_documentation_update",
    input_schema={
        "type": "object",
        "required": ["project_id", "target_doc", "evidence_refs", "evidence_text"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 255},
            "target_doc": {"type": "string", "minLength": 1, "maxLength": 500},
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 50,
            },
            "evidence_text": {"type": "string", "minLength": 1, "maxLength": 12000},
            "section": {"type": ["string", "null"], "maxLength": 500},
            "assertion_kind": {
                "type": "string",
                "enum": ["source_backed", "editorial"],
                "default": "editorial",
            },
        },
        "additionalProperties": False,
    },
    output_type="documentation_update_proposal",
    risk_tier=HarnessRiskTier.TRIGGERS_WORKFLOW,
    health=CapabilityHealth.AVAILABLE,
    packs=["documentation"],
    use_when=[
        "The user wants a named project document updated from explicit evidence.",
        "A documentation change must be proposed for user approval before publishing.",
    ],
    do_not_use_when=[
        "The user only wants to read, search, or generate documentation.",
        "The target document or supporting evidence is not known yet.",
    ],
    task_types=["document_update"],
    output_contracts=["documentation_update_proposal"],
    metadata={
        "execution_surface": "registered_conversation_task",
        "bounded_loop_only": True,
        "requires_turn_claim": True,
        "requires_explicit_user_evidence": True,
        "requires_named_target_doc": True,
        "activity_label": "Prepare documentation update approval",
        "agent_control_plane": True,
    },
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
            "proposal_id": {"type": ["string", "null"]},
            "patch_plan_hash": {"type": ["string", "null"]},
            "base_knowledge_revision_id": {"type": ["string", "null"]},
            "compact_diff": {"type": ["string", "null"]},
            "changed_scope": {"type": "array", "items": {"type": "string"}},
            "assertion_kind": {"type": "string"},
            "expires_at": {"type": ["string", "null"]},
        },
    },
    output_type="documentation_change_receipt",
    risk_tier=HarnessRiskTier.WRITES_CONTENT,
    health=CapabilityHealth.AVAILABLE,
    packs=["documentation"],
    task_types=["document_update"],
    metadata={
        "execution_surface": "mcp_evidence_doc_update",
        "runtime_hidden": True,
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
    task_types=["document_update"],
    metadata={
        "execution_surface": "mcp_evidence_doc_revert",
        "runtime_hidden": True,
    },
)


# ---------------------------------------------------------------------------
# Scanner source evidence
# ---------------------------------------------------------------------------
#
# Generation summarises component parameters away; the scan keeps them. Asked
# "what orders go through FMD?", an agent answered "I did not find any filter --
# confirm with the functional owner", while the scan held the predicate
# verbatim: a `cMessagingEndpoint` labelled `determineFMD` whose `URI` parameter
# is `"sql:SELECT COUNT(ABAN8) FROM " + context.jdeTableF0101 + " WHERE ... AND
# ABCLASS01 = 'FMD'?dataSource=..."`. None of `ABCLASS01`, `determineFMD`,
# `checkIfFMDNeeded` or `No FMD Customer` appears anywhere in the generated
# documentation. The two capabilities below exist so that a question the code
# answers unambiguously does not come back as a hedge.
#
# Both are deliberately structural rather than file-shaped. A filesystem mount
# with grep and read was considered and rejected: the scan is minified, one file
# is one line, so grep returns a 4 MB line as a single "match", and a whole job
# is up to 102 components. Neither is readable. See
# `src.harness.source_evidence` for the layout, the redaction position and the
# measured timings.

SOURCE_SEARCH_CAPABILITY = CapabilityDefinition(
    id="intodoc.source.search",
    label="Search the scanned source of one project",
    semantic_name="search_source",
    purpose=(
        "Find a term anywhere in a project's scanned integration source -- component "
        "parameters, SQL and expressions inside them, component names and labels, "
        "context variables, connection names -- and get back the structure around each "
        "hit: which job, which component, which parameter, and the parameter's value. "
        "This is the scan, not the documentation: it holds the literal predicates, "
        "table names, queues and endpoints that generated prose summarises away. Use it "
        "when a question is about what the code actually does and the documentation "
        "answered vaguely or not at all."
    ),
    preconditions=[
        "An authorized project identifier whose scan is mounted, and a literal term to "
        "look for."
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.SOURCE,
    provider_id="intodoc.scan",
    action="search_source",
    input_schema={
        "type": "object",
        "required": ["project_id", "query"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "query": {
                "type": "string",
                "minLength": 2,
                "maxLength": 200,
                "description": (
                    "One literal term, matched case-insensitively as a substring. Not a "
                    "regular expression and not a sentence: send the identifier you are "
                    "actually looking for -- a column such as 'ABCLASS01', a label such "
                    "as 'determineFMD', a queue, a table, a context variable. A natural "
                    "language question matches nothing here."
                ),
            },
            "job": {
                "type": "string",
                "maxLength": 500,
                "description": (
                    "Restrict the search to one job, by the 'job' name or the 'job_ref' "
                    "reported on an earlier hit. Omit it to search the whole project."
                ),
            },
            "component_type": {
                "type": "string",
                "maxLength": 120,
                "description": (
                    "Restrict the search to components of one type, matched case-"
                    "insensitively as a substring -- 'tMap', 'cMessagingEndpoint', "
                    "'tOracleInput'. Job-level hits are excluded when this is set."
                ),
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 25,
                "description": (
                    "How many hits to return. Every matching file is examined either "
                    "way, so the result always reports the true total and says how many "
                    "were withheld; raise this only when the total says it is worth it."
                ),
            },
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "The documentation does not answer a question about behaviour -- a filter, a "
        "condition, a routing rule, a field mapping -- and the answer would be in the "
        "code.",
        "You need the literal text of a query, expression or endpoint rather than a "
        "summary of it.",
        "A term appeared in one document (a table, a column, a queue, a job name) and "
        "you need every place in the source that touches it.",
        "You are about to report that something does not exist: the scan is where to "
        "check before saying so.",
    ],
    do_not_use_when=[
        "The question is answered by the generated documentation; read that first, it "
        "is written for a reader and this is not.",
        "You do not have a literal term yet -- search or read documentation to find the "
        "identifier, then come back with it.",
        "You already have a hit and want the rest of that component: use read_component "
        "instead of searching again.",
    ],
    task_types=["document_answer", "operational_investigation"],
    # `EvidenceSource` names four kinds and none of them is a code scan. Labelling
    # this `indexed_documentation` would let evidence ranking treat a raw component
    # parameter as a written document, which is the confusion this capability
    # exists to end; the field is rendered into the manifest and nothing else, so
    # leaving it empty costs nothing and claims nothing false.
    evidence_sources=[],
    output_contracts=["grounded_answer", "inventory"],
    metadata={
        "execution_surface": "intodoc_scan_filesystem",
        "evidence_output": True,
        "bounded_loop_only": True,
    },
)

SOURCE_COMPONENT_READ_CAPABILITY = CapabilityDefinition(
    id="intodoc.source.read_component",
    label="Read one scanned component whole",
    semantic_name="read_component",
    purpose=(
        "Return one component of one job complete: its type, its unique name, every "
        "parameter with its full value, the row schemas declared on it, and the "
        "connections into and out of it. One component, never a whole job -- a job "
        "reaches 102 components and reading one whole is what search_source truncates."
    ),
    preconditions=[
        "An authorized project, a job (by name or by the 'job_ref' from a search hit), "
        "and the component's unique name."
    ],
    failure_recovery=_STANDARD_FAILURE_RECOVERY,
    provider_type=HarnessProviderType.SOURCE,
    provider_id="intodoc.scan",
    action="read_component",
    input_schema={
        "type": "object",
        "required": ["project_id", "job", "component"],
        "properties": {
            "project_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "job": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
                "description": (
                    "The job holding the component. Pass the 'job_ref' reported by a "
                    "search_source hit and it resolves directly; a job name also works "
                    "and is matched case-insensitively. Job names repeat across "
                    "repositories, so an ambiguous name fails and lists every candidate "
                    "'job_ref' rather than guessing which one you meant."
                ),
            },
            "component": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": (
                    "The component's unique name within that job, such as "
                    "'cMessagingEndpoint_15' or 'tMap_3'. This is the 'component' field "
                    "of a search hit. A name that does not exist fails and lists the "
                    "names that do."
                ),
            },
        },
        "additionalProperties": False,
    },
    output_type="evidence_items",
    risk_tier=HarnessRiskTier.READ_ONLY,
    packs=["core", "documentation"],
    use_when=[
        "A search_source hit reported a truncated value and you need the whole of it.",
        "You need every parameter of one component, not only the one that matched.",
        "You need to know what a component is wired to -- which components feed it and "
        "which it feeds -- to follow a flow through a job.",
        "You need the row schema (column names and types) a component declares.",
    ],
    do_not_use_when=[
        "You do not know which component yet: search_source first.",
        "You want the whole job. There is no whole-job read on purpose; ask for the "
        "components you actually need.",
    ],
    task_types=["document_answer", "operational_investigation"],
    # Empty for the same reason as the search capability above.
    evidence_sources=[],
    output_contracts=["grounded_answer", "inventory"],
    metadata={
        "execution_surface": "intodoc_scan_filesystem",
        "evidence_output": True,
        "bounded_loop_only": True,
    },
)
