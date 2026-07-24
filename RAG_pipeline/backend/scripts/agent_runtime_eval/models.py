"""Strict provider-neutral schemas shared by every runtime evaluation target."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "1.0.0"
REPORT_VERSION = "1.0.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureTaxonomy(StrEnum):
    MODEL_OBJECTIVE = "MODEL_OBJECTIVE"
    MODEL_TOOL = "MODEL_TOOL"
    MODEL_ARGS = "MODEL_ARGS"
    TOOL_CONTRACT = "TOOL_CONTRACT"
    MANIFEST = "MANIFEST"
    POLICY = "POLICY"
    EXECUTION = "EXECUTION"
    OBSERVATION = "OBSERVATION"
    EVIDENCE = "EVIDENCE"
    GROUNDING = "GROUNDING"
    RESPONSE = "RESPONSE"
    PLAN_STATE = "PLAN_STATE"
    WORKFLOW_STATE = "WORKFLOW_STATE"
    RUNTIME_PROTOCOL = "RUNTIME_PROTOCOL"
    TRANSPORT = "TRANSPORT"
    EVALUATOR = "EVALUATOR"


class CaptureDimension(StrEnum):
    TERMINAL_STATUS = "terminal_status"
    BOUNDARY = "boundary"
    TOOLS = "tools"
    POLICY = "policy"
    ARGUMENTS = "arguments"
    OBSERVATIONS = "observations"
    TOOL_OBSERVATION_CORRELATION = "tool_observation_correlation"
    STATE = "state"
    EFFECTS = "effects"
    GROUNDING = "grounding"
    RESPONSE = "response"
    BUDGET = "budget"
    TURN_AUTHORITY = "turn_authority"
    EVENTS = "events"
    REPLAY = "replay"
    RECOVERY = "recovery"
    TRANSPORT_PARITY = "transport_parity"
    RUNTIME_FAILURE = "runtime_failure"


FAILURE_PRECEDENCE: tuple[FailureTaxonomy, ...] = (
    FailureTaxonomy.POLICY,
    FailureTaxonomy.MODEL_OBJECTIVE,
    FailureTaxonomy.MODEL_TOOL,
    FailureTaxonomy.MODEL_ARGS,
    FailureTaxonomy.TOOL_CONTRACT,
    FailureTaxonomy.MANIFEST,
    FailureTaxonomy.EXECUTION,
    FailureTaxonomy.OBSERVATION,
    FailureTaxonomy.PLAN_STATE,
    FailureTaxonomy.WORKFLOW_STATE,
    FailureTaxonomy.EVIDENCE,
    FailureTaxonomy.GROUNDING,
    FailureTaxonomy.RESPONSE,
    FailureTaxonomy.RUNTIME_PROTOCOL,
    FailureTaxonomy.TRANSPORT,
    FailureTaxonomy.EVALUATOR,
)


class AuthorizedContext(StrictModel):
    user: str
    project_scope: list[str]
    permissions: list[str]
    manifest_hash: str


class ArgumentConstraint(StrictModel):
    tool_family: str
    path: str
    operator: Literal["eq", "in", "present", "absent"]
    value: Any | None = None


class StateExpectation(StrictModel):
    task: str = "none"
    plan: str = "none"
    workflow: str = "none"
    approval: str = "none"


class EffectExpectation(StrictModel):
    kind: str
    minimum: int = 0
    operation_id_required: bool = False
    receipt_required: bool = False
    maximum: int = 1

    @model_validator(mode="after")
    def validate_counts(self) -> "EffectExpectation":
        if self.minimum < 0 or self.maximum < self.minimum:
            raise ValueError("effect minimum/maximum are inconsistent")
        return self


class ToolPolicyExpectation(StrictModel):
    call_index: int
    family: str
    allowed_outcomes: list[Literal["allowed", "denied", "approval_required", "not_checked"]]


class MechanicsExpectation(StrictModel):
    turn_authority: Literal["none", "deduplicated", "concurrent"] = "none"
    ordered_events: bool = False
    replay_identity: bool = False
    recovery_correlation: bool = False
    recovery_operation_id: str | None = None
    recovery_receipt_id: str | None = None
    tool_observation_correlation: bool = False
    paired_transport_parity: bool = False


class SemanticAssertion(StrictModel):
    kind: Literal[
        "contains",
        "contains_any",
        "excludes",
        "nonempty",
        "asks_clarification",
        "list_items_at_least",
        "word_count_at_most",
    ]
    value: str | int | list[str] | None = None


class GroundingExpectation(StrictModel):
    mode: Literal["none", "required", "no_evidence", "partial", "conflict"]
    minimum_evidence_refs: int = 0
    unsupported_claims_allowed: bool = False


class ExpectedTrajectory(StrictModel):
    allowed_terminal_statuses: list[Literal["completed", "failed"]] = Field(
        default_factory=lambda: ["completed"]
    )
    boundary_decisions: list[str]
    allowed_tool_sequences: list[list[str]]
    tool_policies: list[ToolPolicyExpectation]
    argument_constraints: list[ArgumentConstraint]
    observations: list[str]
    state: StateExpectation
    allowed_effects: list[EffectExpectation]
    forbidden_effects: list[str]
    response_semantics: list[SemanticAssertion]
    grounding: GroundingExpectation
    budget_class: str
    mechanics: MechanicsExpectation = Field(default_factory=MechanicsExpectation)

    @model_validator(mode="after")
    def validate_effect_contract(self) -> "ExpectedTrajectory":
        allowed = {effect.kind for effect in self.allowed_effects}
        overlap = allowed.intersection(self.forbidden_effects)
        if overlap:
            raise ValueError(f"effect kinds cannot be both allowed and forbidden: {overlap}")
        if not self.boundary_decisions:
            raise ValueError("at least one boundary decision is required")
        if not self.response_semantics:
            raise ValueError("visible response semantics are required")
        if not self.allowed_terminal_statuses:
            raise ValueError("at least one terminal status is required")
        primary = self.allowed_tool_sequences[0] if self.allowed_tool_sequences else []
        if len(self.tool_policies) != len(primary):
            raise ValueError("primary tool sequence needs one policy expectation per call")
        for index, policy in enumerate(self.tool_policies):
            if policy.call_index != index or policy.family != primary[index]:
                raise ValueError("tool policy expectation does not align with primary sequence")
        mechanics = self.mechanics
        if mechanics.recovery_correlation and not (
            mechanics.recovery_operation_id and mechanics.recovery_receipt_id
        ):
            raise ValueError("recovery correlation requires expected operation and receipt IDs")
        return self


class Turn(StrictModel):
    turn_id: str
    user_message: str
    transports: list[Literal["stream", "nonstream"]]
    expected: ExpectedTrajectory


class Scenario(StrictModel):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    scenario_id: str
    family: str
    split: Literal["development", "holdout"]
    variant: Literal[
        "formal",
        "informal",
        "typo",
        "pronoun",
        "negation",
        "keyword_collision",
        "neighbor",
        "ambiguous",
    ]
    provenance_incidents: list[int] = Field(default_factory=list)
    conversation_seed: list[dict[str, str]]
    authorized_context: AuthorizedContext
    turns: list[Turn]

    @model_validator(mode="after")
    def validate_scenario(self) -> "Scenario":
        if not self.turns:
            raise ValueError("scenario must contain at least one turn")
        if len({turn.turn_id for turn in self.turns}) != len(self.turns):
            raise ValueError("turn IDs must be unique within a scenario")
        return self


class ToolCallTrace(StrictModel):
    call_id: str | None = None
    family: str
    arguments: dict[str, Any]
    policy: Literal["allowed", "denied", "approval_required", "not_checked"]


class EffectTrace(StrictModel):
    kind: str
    operation_id: str | None = None
    receipt_id: str | None = None
    authorized: bool = True


class ObservationTrace(StrictModel):
    status: str
    call_id: str | None = None


class EventTrace(StrictModel):
    event_id: str
    sequence: int
    kind: str
    replay_of_event_id: str | None = None
    operation_id: str | None = None


class RecoveryTrace(StrictModel):
    original_operation_id: str
    recovered_receipt_id: str
    observation_reconstructed: bool


class BudgetTrace(StrictModel):
    budget_class: str
    hard_limit_exceeded: bool = False
    soft_limit_exceeded: bool = False


class RuntimeAttribution(StrictModel):
    application_revision: str
    runtime_version: str
    model_profile: str
    prompt_version: str
    manifest_hash: str
    transport: Literal["stream", "nonstream"]
    captured_at: str


class RunTrace(StrictModel):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    scenario_id: str
    turn_id: str
    status: Literal["completed", "failed", "not_evaluated"]
    captured_dimensions: list[CaptureDimension]
    attribution: RuntimeAttribution
    client_turn_id: str | None = None
    turn_authority_id: str | None = None
    authority_acquisitions: int = 0
    turn_version_before: int | None = None
    turn_version_after: int | None = None
    conflict_detected: bool = False
    boundary_decisions: list[str]
    tool_calls: list[ToolCallTrace]
    observations: list[str]
    observation_traces: list[ObservationTrace] = Field(default_factory=list)
    state: StateExpectation
    effects: list[EffectTrace]
    response: str
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    persisted_message_id: str | None = None
    evidence_refs: list[str]
    events: list[EventTrace] = Field(default_factory=list)
    recovery: RecoveryTrace | None = None
    unsupported_claim_count: int = 0
    raw_internal_output: bool = False
    repeated_response: bool = False
    budget: BudgetTrace
    runtime_failure_hint: FailureTaxonomy | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_capture_dimensions(self) -> "RunTrace":
        if len(self.captured_dimensions) != len(set(self.captured_dimensions)):
            raise ValueError("captured dimensions must be unique")
        if self.status == "not_evaluated" and self.captured_dimensions:
            raise ValueError("not-evaluated traces cannot claim captured behavioral dimensions")
        return self


class GradeFailure(StrictModel):
    taxonomy: FailureTaxonomy
    precedence: int
    message: str


class ScenarioGrade(StrictModel):
    scenario_id: str
    turn_id: str
    evaluated: bool
    passed: bool
    evaluator_status: Literal[
        "product_pass", "product_failure", "evaluator_gap", "evaluator_invalid"
    ]
    uncaptured_dimensions: list[CaptureDimension] = Field(default_factory=list)
    failures: list[GradeFailure]


class EvaluationSummary(StrictModel):
    total: int
    evaluated: int
    passed: int
    product_failed: int
    not_evaluated: int
    evaluator_invalid: int
    by_taxonomy: dict[str, int]
    by_family: dict[str, dict[str, int]]


class EvaluationReport(StrictModel):
    report_version: Literal["1.0.0"] = REPORT_VERSION
    corpus_version: str
    target: str
    generated_at: str
    attributions: list[RuntimeAttribution]
    summary: EvaluationSummary
    grades: list[ScenarioGrade]
