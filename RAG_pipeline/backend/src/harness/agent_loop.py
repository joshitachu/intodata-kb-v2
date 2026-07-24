"""One bounded, provider-neutral agent loop for registered Nexus capabilities.

The model chooses dialogue or a capability.  Nexus owns capability availability,
policy, execution, budgets, and the authoritative state supplied to the model.
This module deliberately has no dependency on the legacy chat planner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.generation.llm_client import generate_answer_with_runtime_tools
from src.generation.usage import UsageStats
from src.harness import UnknownCapabilityError
from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
    CapabilityObservationStatus,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.interaction_protocol import (
    INTERACTION_PROTOCOL_PROMPT,
    TURN_ASSESSMENT_TOOL_NAME,
    TurnAssessment,
    turn_assessment_tool_definition,
)
from src.harness.manifest import RuntimeCapabilityManifest, RuntimeCapabilityTool
from src.harness.registry import CapabilityRegistry, CapabilityRegistryError
from src.harness.response_synthesis import (
    ResponseFact,
    ResponseSynthesisPlan,
    TypedAgentResponse,
    build_response_fact_catalog,
    render_typed_response,
)
from src.runtime.models import RuntimeToolDefinition


_MAX_TEXT = 8_000
_MAX_HISTORY = 20
_AGENT_RESPONSE_TOOL_NAME = "nexus_agent_respond"
_PROVIDER_DECISION_ATTEMPTS = 2
logger = logging.getLogger(__name__)
_SAFE_WORKFLOW_REF_KEYS = frozenset(
    {"workflow_run_id", "external_run_id", "project_id", "status", "provider_id"}
)
_SAFE_RECEIPT_REF_KEYS = frozenset({"idempotency_key", "receipt_id", "version"})


class AgentBoundaryAction(StrEnum):
    ASSESS = "assess"
    ANSWER = "answer"
    CLARIFY = "clarify"
    CALL_CAPABILITY = "call_capability"


class AgentStopReason(StrEnum):
    ANSWERED = "answered"
    CLARIFICATION_REQUIRED = "clarification_required"
    PLANNER_UNAVAILABLE = "planner_unavailable"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    UNKNOWN_CAPABILITY = "unknown_capability"
    CAPABILITY_DENIED = "capability_denied"
    MAX_ITERATIONS = "max_iterations"
    MAX_TOOL_CALLS = "max_tool_calls"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    COST_BUDGET_EXCEEDED = "cost_budget_exceeded"
    REPEATED_CALL = "repeated_call"
    REPEATED_RESPONSE = "repeated_response"
    STATE_CONFLICT = "state_conflict"
    MODEL_TIMEOUT = "model_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    SOFT_DEADLINE_EXCEEDED = "soft_deadline_exceeded"
    HARD_DEADLINE_EXCEEDED = "hard_deadline_exceeded"
    MODEL_CALL_LIMIT = "model_call_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"


class AgentDecision(BaseModel):
    """The complete semantic surface available to the loop model."""

    model_config = ConfigDict(extra="forbid")

    action: AgentBoundaryAction
    content: str | None = Field(default=None, max_length=_MAX_TEXT)
    capability_id: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    complete_objective: bool = False
    response_plan: ResponseSynthesisPlan | None = None
    assessment: TurnAssessment | None = None

    @model_validator(mode="after")
    def validate_action_shape(self) -> "AgentDecision":
        if self.action is AgentBoundaryAction.ASSESS:
            if self.assessment is None:
                raise ValueError("assess requires a turn assessment")
            if self.content is not None or self.capability_id is not None or self.arguments:
                raise ValueError("assess cannot include dialogue or a capability call")
            if self.response_plan is not None or self.complete_objective:
                raise ValueError("assess cannot complete a conversation objective")
        elif self.action is AgentBoundaryAction.CALL_CAPABILITY:
            if not self.capability_id:
                raise ValueError("call_capability requires capability_id")
            if self.content is not None:
                raise ValueError("call_capability cannot include dialogue content")
            if self.response_plan is not None:
                raise ValueError("call_capability cannot include a response plan")
            if self.complete_objective:
                raise ValueError("call_capability cannot complete a conversation objective")
            if self.assessment is not None:
                raise ValueError("call_capability cannot include a turn assessment")
        else:
            if (not self.content or not self.content.strip()) and self.response_plan is None:
                raise ValueError(f"{self.action.value} requires content or a response plan")
            if self.capability_id is not None or self.arguments:
                raise ValueError(f"{self.action.value} cannot include a capability call")
            if self.action is AgentBoundaryAction.CLARIFY and self.complete_objective:
                raise ValueError("clarify cannot complete a conversation objective")
            if self.assessment is not None:
                raise ValueError(f"{self.action.value} cannot include a turn assessment")
        return self


class AgentUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    model_cost_usd: float = Field(default=0.0, ge=0)
    capability_cost_usd: float = Field(default=0.0, ge=0)

    @property
    def total_cost_usd(self) -> float:
        return self.model_cost_usd + self.capability_cost_usd


class AgentModelResult(BaseModel):
    decision: AgentDecision
    usage: AgentUsage = Field(default_factory=AgentUsage)


class AgentModelRequest(BaseModel):
    user_message: str = Field(max_length=_MAX_TEXT)
    images: list[str] | None = Field(default=None, max_length=4, exclude=True)
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    conversation_state: dict[str, Any] = Field(default_factory=dict)
    workflow_state: list[dict[str, Any]] = Field(default_factory=list)
    state_version: str
    manifest: RuntimeCapabilityManifest
    observations: list[CapabilityObservation] = Field(default_factory=list)
    response_facts: list[ResponseFact] = Field(default_factory=list)
    iteration: int = Field(ge=1)
    remaining_tool_calls: int = Field(ge=0)
    remaining_cost_usd: float = Field(ge=0)
    interaction_assessment: TurnAssessment | None = None
    assessment_required: bool = False
    clarification_only: bool = False


class AgentDecisionModel(Protocol):
    async def decide(self, request: AgentModelRequest) -> AgentModelResult: ...


class AgentLoopLimits(BaseModel):
    max_iterations: int = Field(default=6, ge=1, le=20)
    max_tool_calls: int = Field(default=4, ge=0, le=24)
    deadline_seconds: float = Field(default=30.0, gt=0, le=300.0)
    max_cost_usd: float = Field(default=0.25, ge=0, le=100.0)


class AgentTraceEvent(BaseModel):
    sequence: int = Field(ge=1)
    iteration: int = Field(ge=0)
    action: AgentBoundaryAction | None = None
    capability_id: str | None = None
    observation_status: CapabilityObservationStatus | None = None
    workflow_ref: dict[str, Any] | None = None
    receipt_ref: dict[str, Any] | None = None
    stop_reason: AgentStopReason | None = None
    elapsed_ms: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)


class AgentLoopResult(BaseModel):
    action: AgentBoundaryAction | None = None
    output: str
    stop_reason: AgentStopReason
    observations: list[CapabilityObservation] = Field(default_factory=list)
    trace: list[AgentTraceEvent] = Field(default_factory=list)
    usage: AgentUsage = Field(default_factory=AgentUsage)
    state_version: str
    response_fingerprint: str | None = None
    complete_objective: bool = False
    typed_response: TypedAgentResponse | None = None


class AgentLoopInput(BaseModel):
    user_message: str = Field(max_length=_MAX_TEXT)
    images: list[str] | None = Field(default=None, max_length=4, exclude=True)
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    conversation_state: dict[str, Any] = Field(default_factory=dict)
    workflow_state: list[dict[str, Any]] = Field(default_factory=list)
    state_version: str | None = None
    prior_response_fingerprints: set[str] = Field(default_factory=set)


class AgentModelError(RuntimeError):
    def __init__(self, reason: AgentStopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class ProviderAgentDecisionModel:
    """Selected-provider adapter with one native structured-call boundary.

    Capability calls and terminal dialogue use the provider's tool protocol.
    Requiring models to alternate between native tool calls and hand-written
    JSON proved unreliable across providers and created avoidable parse errors.
    """

    supports_interaction_assessment = True

    async def decide(self, request: AgentModelRequest) -> AgentModelResult:
        names = {_provider_tool_name(tool.id): tool.id for tool in request.manifest.tools}
        response_only = bool(
            request.clarification_only
            or (
                request.interaction_assessment is not None
                and not request.interaction_assessment.permits_consequential_action
            )
        )
        tools = (
            [turn_assessment_tool_definition()]
            if request.assessment_required
            else [
                RuntimeToolDefinition(
                    name=name,
                    description=_capability_tool_description(tool),
                    input_schema=tool.input_schema,
                )
                for name, capability_id in names.items()
                for tool in request.manifest.tools
                if tool.id == capability_id
                and not response_only
            ]
        )
        if not request.assessment_required:
            tools.append(_agent_response_tool(request))
        messages = _provider_messages(request, names)
        usage = AgentUsage()
        for attempt in range(1, _PROVIDER_DECISION_ATTEMPTS + 1):
            response = await generate_answer_with_runtime_tools(
                messages,
                tools,
                images=request.images,
            )
            if response.no_tool_support:
                raise AgentModelError(AgentStopReason.PLANNER_UNAVAILABLE)
            usage = _add_usage(usage, _agent_usage(response.usage))
            try:
                decision = _provider_decision(response.tool_calls, request, names)
                return AgentModelResult(decision=decision, usage=usage)
            except AgentModelError as exc:
                logger.warning(
                    "Agent provider decision rejected attempt=%d reason=%s call_count=%d tools=%s",
                    attempt,
                    exc.reason.value,
                    len(response.tool_calls),
                    sorted({call.tool_name for call in response.tool_calls}),
                )
                if attempt >= _PROVIDER_DECISION_ATTEMPTS:
                    raise
                messages = [
                    *messages,
                    {
                        "role": "system",
                        "content": (
                            "The previous provider response was rejected and was not applied. "
                            "Return exactly one valid native tool call using the supplied schema."
                        ),
                    },
                ]
        raise AgentModelError(AgentStopReason.INVALID_MODEL_OUTPUT)


class BoundedAgentLoop:
    """Execute one turn with hard bounds and the Slice 2 governed executor."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        executor: HarnessCapabilityExecutor,
        model: AgentDecisionModel,
        limits: AgentLoopLimits | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._model = model
        self._limits = limits or AgentLoopLimits()
        self._clock = clock

    async def run(
        self,
        turn: AgentLoopInput,
        *,
        context: CapabilityExecutionContext,
    ) -> AgentLoopResult:
        started = self._clock()
        deadline = started + self._limits.deadline_seconds
        state_version = turn.state_version or authoritative_state_version(
            turn.conversation_state, turn.workflow_state
        )
        history = _safe_history(turn.conversation_history)
        observations: list[CapabilityObservation] = []
        trace: list[AgentTraceEvent] = []
        seen_validated_calls: set[str] = set()
        usage = AgentUsage()
        tool_calls = 0
        manifest = registered_capability_manifest(
            self._registry,
            permissions=context.permissions,
            project_ids=context.project_scope,
        )
        manifest = _apply_turn_capability_policy(manifest, context)
        requires_interaction_assessment = bool(
            getattr(self._model, "supports_interaction_assessment", False)
            and any(tool.risk_tier != "read_only" for tool in manifest.tools)
        )
        interaction_assessment: TurnAssessment | None = None
        clarification_only = False
        facts = build_response_fact_catalog(
            observations=observations,
            conversation_state=turn.conversation_state,
            workflow_state=turn.workflow_state,
        )

        for iteration in range(1, self._limits.max_iterations + 1):
            remaining = deadline - self._clock()
            if remaining <= 0:
                return _stopped_result(
                    reason=AgentStopReason.DEADLINE_EXCEEDED,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                )
            try:
                assessment_required = bool(
                    requires_interaction_assessment and interaction_assessment is None
                )
                # Keep the complete manifest in model context so discuss/explain and
                # needs-input responses retain capability/schema context. The provider
                # adapter independently removes executable tools in response-only mode.
                request_manifest = manifest
                model_result = await asyncio.wait_for(
                    self._model.decide(
                        AgentModelRequest(
                            user_message=turn.user_message,
                            images=turn.images,
                            conversation_history=history,
                            conversation_state=turn.conversation_state,
                            workflow_state=turn.workflow_state,
                            state_version=state_version,
                            manifest=request_manifest,
                            observations=observations,
                            response_facts=[] if clarification_only else facts,
                            iteration=iteration,
                            remaining_tool_calls=max(0, self._limits.max_tool_calls - tool_calls),
                            remaining_cost_usd=max(
                                0.0, self._limits.max_cost_usd - usage.total_cost_usd
                            ),
                            interaction_assessment=interaction_assessment,
                            assessment_required=assessment_required,
                            clarification_only=clarification_only,
                        )
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                return _stopped_result(
                    reason=AgentStopReason.DEADLINE_EXCEEDED,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                )
            except AgentModelError as exc:
                return _stopped_result(
                    reason=exc.reason,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                )
            except Exception:
                return _stopped_result(
                    reason=AgentStopReason.PLANNER_UNAVAILABLE,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                )

            usage = _add_usage(usage, model_result.usage)
            decision = model_result.decision
            if usage.total_cost_usd > self._limits.max_cost_usd:
                return _stopped_result(
                    reason=AgentStopReason.COST_BUDGET_EXCEEDED,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                )

            if decision.action is AgentBoundaryAction.ASSESS:
                if not assessment_required or decision.assessment is None:
                    return _stopped_result(
                        reason=AgentStopReason.INVALID_MODEL_OUTPUT,
                        output="",
                        state_version=state_version,
                        fact_catalog=facts,
                        observations=observations,
                        trace=trace,
                        usage=usage,
                        started=started,
                        clock=self._clock,
                        iteration=iteration,
                        tool_calls=tool_calls,
                        action=decision.action,
                    )
                interaction_assessment = decision.assessment
                trace.append(
                    _trace_event(
                        trace,
                        started=started,
                        clock=self._clock,
                        iteration=iteration,
                        action=decision.action,
                        tool_calls=tool_calls,
                        usage=usage,
                    )
                )
                continue

            if assessment_required or (
                clarification_only and decision.action is AgentBoundaryAction.CALL_CAPABILITY
            ):
                return _stopped_result(
                    reason=AgentStopReason.INVALID_MODEL_OUTPUT,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                )

            if (
                decision.action is AgentBoundaryAction.CALL_CAPABILITY
                and requires_interaction_assessment
                and interaction_assessment is not None
            ):
                selected_tool = next(
                    (
                        tool
                        for tool in manifest.tools
                        if tool.id == decision.capability_id
                    ),
                    None,
                )
                if not interaction_assessment.permits_consequential_action or (
                    selected_tool is not None
                    and selected_tool.risk_tier != "read_only"
                    and not interaction_assessment.matches_action(
                        _provider_tool_name(decision.capability_id or ""),
                        decision.arguments,
                    )
                ):
                    return _stopped_result(
                        reason=AgentStopReason.INVALID_MODEL_OUTPUT,
                        output="",
                        state_version=state_version,
                        fact_catalog=facts,
                        observations=observations,
                        trace=trace,
                        usage=usage,
                        started=started,
                        clock=self._clock,
                        iteration=iteration,
                        tool_calls=tool_calls,
                        action=decision.action,
                        capability_id=decision.capability_id,
                    )

            if decision.action in {
                AgentBoundaryAction.ANSWER,
                AgentBoundaryAction.CLARIFY,
            }:
                typed_response = render_typed_response(
                    action=decision.action.value,
                    content=decision.content,
                    plan=decision.response_plan,
                    facts=[] if clarification_only else facts,
                    state_version=state_version,
                    stop_reason=(
                        AgentStopReason.ANSWERED.value
                        if decision.action is AgentBoundaryAction.ANSWER
                        else AgentStopReason.CLARIFICATION_REQUIRED.value
                    ),
                    observations=observations,
                )
                fingerprint = (
                    typed_response.factual_fingerprint
                    if facts and not clarification_only
                    else response_fingerprint(
                        decision.action,
                        typed_response.content,
                        state_version=state_version,
                        observations=observations,
                    )
                )
                if fingerprint in turn.prior_response_fingerprints:
                    return _stopped_result(
                        reason=AgentStopReason.REPEATED_RESPONSE,
                        output=typed_response.content,
                        state_version=state_version,
                        fact_catalog=facts,
                        observations=observations,
                        trace=trace,
                        usage=usage,
                        started=started,
                        clock=self._clock,
                        iteration=iteration,
                        tool_calls=tool_calls,
                        action=decision.action,
                        response_fingerprint_value=fingerprint,
                        no_progress=True,
                    )
                reason = (
                    AgentStopReason.ANSWERED
                    if decision.action is AgentBoundaryAction.ANSWER
                    else AgentStopReason.CLARIFICATION_REQUIRED
                )
                trace.append(
                    _trace_event(
                        trace,
                        started=started,
                        clock=self._clock,
                        iteration=iteration,
                        action=decision.action,
                        stop_reason=reason,
                        tool_calls=tool_calls,
                        usage=usage,
                    )
                )
                return AgentLoopResult(
                    action=decision.action,
                    output=typed_response.content,
                    stop_reason=reason,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    state_version=state_version,
                    response_fingerprint=fingerprint,
                    complete_objective=decision.complete_objective,
                    typed_response=typed_response,
                )

            if tool_calls >= self._limits.max_tool_calls:
                return _stopped_result(
                    reason=AgentStopReason.MAX_TOOL_CALLS,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                )
            assert decision.capability_id is not None
            if not _is_registered(self._registry, decision.capability_id):
                return _stopped_result(
                    reason=AgentStopReason.UNKNOWN_CAPABILITY,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                )

            call_key = canonical_capability_call(decision.capability_id, decision.arguments)
            if call_key in seen_validated_calls:
                return _stopped_result(
                    reason=AgentStopReason.REPEATED_CALL,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                )

            capability = self._registry.get(decision.capability_id)
            declared_cost = _declared_capability_cost(capability.metadata)
            if usage.total_cost_usd + declared_cost > self._limits.max_cost_usd:
                return _stopped_result(
                    reason=AgentStopReason.COST_BUDGET_EXCEEDED,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                )
            remaining = deadline - self._clock()
            if remaining <= 0:
                return _stopped_result(
                    reason=AgentStopReason.DEADLINE_EXCEEDED,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                )

            manifest_capability_ids = {tool.id for tool in request_manifest.tools}
            invocation_context = context.model_copy(
                update={
                    "allowed_capability_ids": manifest_capability_ids,
                    "remaining_tool_calls": self._limits.max_tool_calls - tool_calls,
                    "remaining_cost_usd": max(
                        0.0, self._limits.max_cost_usd - usage.total_cost_usd
                    ),
                }
            )
            try:
                observation = await asyncio.wait_for(
                    self._executor.execute(
                        CapabilityExecutionRequest(
                            capability_id=decision.capability_id,
                            arguments=decision.arguments,
                            idempotency_key=_idempotency_key(state_version, iteration, call_key),
                        ),
                        invocation_context,
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                return _stopped_result(
                    reason=AgentStopReason.DEADLINE_EXCEEDED,
                    output="",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                )
            tool_calls += 1
            usage = usage.model_copy(
                update={"capability_cost_usd": usage.capability_cost_usd + declared_cost}
            )
            observations.append(observation)
            facts = build_response_fact_catalog(
                observations=observations,
                conversation_state=turn.conversation_state,
                workflow_state=turn.workflow_state,
            )
            trace.append(
                _trace_event(
                    trace,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    action=decision.action,
                    capability_id=decision.capability_id,
                    observation=observation,
                    tool_calls=tool_calls,
                    usage=usage,
                )
            )
            if observation.status is not CapabilityObservationStatus.DENIED:
                seen_validated_calls.add(call_key)
            if observation.status is CapabilityObservationStatus.NEEDS_INPUT:
                clarification_only = True
                continue
            if observation.status is CapabilityObservationStatus.DENIED:
                return _stopped_result(
                    reason=AgentStopReason.CAPABILITY_DENIED,
                    output=observation.summary or "The capability call was denied by policy.",
                    state_version=state_version,
                    fact_catalog=facts,
                    observations=observations,
                    trace=trace,
                    usage=usage,
                    started=started,
                    clock=self._clock,
                    iteration=iteration,
                    tool_calls=tool_calls,
                    action=decision.action,
                    capability_id=decision.capability_id,
                    observation=observation,
                )

        return _stopped_result(
            reason=AgentStopReason.MAX_ITERATIONS,
            output="",
            state_version=state_version,
            fact_catalog=facts,
            observations=observations,
            trace=trace,
            usage=usage,
            started=started,
            clock=self._clock,
            iteration=self._limits.max_iterations,
            tool_calls=tool_calls,
        )


def registered_capability_manifest(
    registry: CapabilityRegistry,
    *,
    permissions: set[str] | None = None,
    project_ids: list[str] | None = None,
) -> RuntimeCapabilityManifest:
    """Expose only capabilities backed by Slice 2 registered handlers."""

    from src.harness.manifest import ManifestScope, build_runtime_capability_manifest

    complete = build_runtime_capability_manifest(
        registry,
        scope=ManifestScope(
            permissions=permissions,
            project_ids=project_ids,
            include_bounded_loop_only=True,
        ),
    )
    tools = [tool for tool in complete.tools if _is_registered(registry, tool.id)]
    return complete.model_copy(
        update={
            "tools": tools,
            "total": len(tools),
            "filtered": complete.total + complete.filtered - len(tools),
        }
    )


def authoritative_state_version(
    conversation_state: Mapping[str, Any], workflow_state: Sequence[Mapping[str, Any]]
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {"conversation_state": conversation_state, "workflow_state": workflow_state}
        ).encode("utf-8")
    ).hexdigest()


def canonical_capability_call(capability_id: str, arguments: Mapping[str, Any]) -> str:
    return f"{capability_id}:{_canonical_json(arguments)}"


def response_fingerprint(
    action: AgentBoundaryAction,
    content: str,
    *,
    state_version: str,
    observations: Sequence[CapabilityObservation],
) -> str:
    payload = {
        "action": action.value,
        "content": " ".join(content.split()),
        "state_version": state_version,
        "observations": [item.model_dump(mode="json") for item in observations],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def non_stream_transport(result: AgentLoopResult) -> dict[str, Any]:
    """Thin non-stream projection of the authoritative loop result."""

    return result.model_dump(mode="json")


async def stream_transport(
    result: AgentLoopResult,
) -> AsyncIterator[tuple[str, str]]:
    """Thin ordered stream projection; it never reruns or redispatches the loop."""

    observation_index = 0
    for event in result.trace:
        yield "agent_trace", event.model_dump_json()
        if (
            event.action is AgentBoundaryAction.CALL_CAPABILITY
            and event.observation_status is not None
            and observation_index < len(result.observations)
        ):
            yield (
                "agent_observation",
                result.observations[observation_index].model_dump_json(),
            )
            observation_index += 1
    while observation_index < len(result.observations):
        yield (
            "agent_observation",
            result.observations[observation_index].model_dump_json(),
        )
        observation_index += 1
    if result.typed_response is not None:
        yield "agent_response", result.typed_response.model_dump_json()
    yield (
        "agent_final",
        json.dumps(
            {
                "action": result.action.value if result.action else None,
                "stop_reason": result.stop_reason.value,
                "state_version": result.state_version,
                "response_fingerprint": result.response_fingerprint,
                "complete_objective": result.complete_objective,
                "usage": result.usage.model_dump(mode="json"),
            },
            separators=(",", ":"),
        ),
    )


def _provider_decision(
    tool_calls: Sequence[Any],
    request: AgentModelRequest,
    provider_names: Mapping[str, str],
) -> AgentDecision:
    unique_calls: dict[tuple[str, str], Any] = {}
    for call in tool_calls:
        key = (call.tool_name, _canonical_json(call.arguments))
        unique_calls.setdefault(key, call)
    if len(unique_calls) != 1:
        raise AgentModelError(AgentStopReason.INVALID_MODEL_OUTPUT)
    call = next(iter(unique_calls.values()))
    if call.tool_name == TURN_ASSESSMENT_TOOL_NAME:
        try:
            assessment = TurnAssessment.model_validate(call.arguments)
        except ValueError as exc:
            raise AgentModelError(AgentStopReason.INVALID_MODEL_OUTPUT) from exc
        return AgentDecision(
            action=AgentBoundaryAction.ASSESS,
            assessment=assessment,
        )
    if call.tool_name == _AGENT_RESPONSE_TOOL_NAME:
        return _response_tool_decision(call.arguments, request)
    capability_id = provider_names.get(call.tool_name)
    if capability_id is None:
        raise AgentModelError(AgentStopReason.UNKNOWN_CAPABILITY)
    return AgentDecision(
        action=AgentBoundaryAction.CALL_CAPABILITY,
        capability_id=capability_id,
        arguments=call.arguments,
    )


def _provider_messages(
    request: AgentModelRequest, provider_names: Mapping[str, str]
) -> list[dict[str, str]]:
    context = {
        "conversation_state": request.conversation_state,
        "workflow_state": request.workflow_state,
        "state_version": request.state_version,
        "capabilities": [
            {
                "id": tool.id,
                "tool_name": next(
                    name
                    for name, capability_id in provider_names.items()
                    if capability_id == tool.id
                ),
                "label": tool.label,
                "action": tool.action,
                "input_schema": tool.input_schema,
                "use_when": tool.use_when,
                "do_not_use_when": tool.do_not_use_when,
            }
            for tool in request.manifest.tools
        ],
        "observations": [item.model_dump(mode="json") for item in request.observations],
        "response_facts": [item.model_dump(mode="json") for item in request.response_facts],
        "remaining_tool_calls": request.remaining_tool_calls,
        "remaining_cost_usd": request.remaining_cost_usd,
        "interaction_assessment": (
            request.interaction_assessment.model_dump(mode="json")
            if request.interaction_assessment is not None
            else None
        ),
        "clarification_only": request.clarification_only,
    }
    system = (
        f"{INTERACTION_PROTOCOL_PROMPT} "
        "You are the Nexus agent. Make exactly one native tool call on every turn. "
        "When the turn-assessment tool is the only supplied tool, call it before "
        "answering or taking action. "
        "Call a capability tool to act, or call nexus_agent_respond to answer or clarify. "
        "Cancellation and status checks are capabilities, not control actions. "
        "Reason from the current message, conversation, authoritative workflow state, "
        "and typed observations. Never invent state, tools, permissions, or results. "
        "The supplied capability tools are the complete, authoritative description of "
        "what is available for this turn. Never claim an available capability is missing "
        "or direct the user to another interface when a supplied tool matches. A question "
        "about whether an available action is possible may be answered from the manifest; "
        "a request to perform it must call the matching capability. Follow each tool's "
        "Use when and Do not use when guidance. When response_facts exist, select their "
        "fact_ids with nexus_agent_respond; do not write replacement factual prose. "
        "Include every mandatory fact. There is no later synthesis call. "
        "Set complete_objective true only when this answer "
        "directly resolves the authoritative taskless requested input; unrelated "
        "answers must leave it false. "
        f"Runtime-safe context: {_canonical_json(context)}"
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(_safe_history(request.conversation_history))
    messages.append({"role": "user", "content": request.user_message[:_MAX_TEXT]})
    return messages


def _capability_tool_description(tool: RuntimeCapabilityTool) -> str:
    use_when = "; ".join(tool.use_when) or "the user's objective matches this capability"
    do_not_use = "; ".join(tool.do_not_use_when) or "the objective does not match"
    approval = " Approval is required before execution." if tool.requires_approval else ""
    return (
        f"{tool.label}. Action: {tool.action}. Use when: {use_when}. "
        f"Do not use when: {do_not_use}. Returns: {tool.output_type}. "
        f"Risk tier: {tool.risk_tier}.{approval}"
    )


def _agent_response_tool(request: AgentModelRequest) -> RuntimeToolDefinition:
    if request.clarification_only:
        return RuntimeToolDefinition(
            name=_AGENT_RESPONSE_TOOL_NAME,
            description=(
                "Ask one concise, natural clarification grounded in the needs_input "
                "observation. Do not retry, apologize, or narrate tool mechanics."
            ),
            input_schema={
                "type": "object",
                "required": ["action", "content"],
                "properties": {
                    "action": {"type": "string", "enum": ["clarify"]},
                    "content": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT},
                    "complete_objective": {"type": "boolean", "enum": [False]},
                },
                "additionalProperties": False,
            },
        )
    if request.response_facts:
        fact_ids = [fact.fact_id for fact in request.response_facts]
        mandatory = [fact.fact_id for fact in request.response_facts if fact.mandatory]
        return RuntimeToolDefinition(
            name=_AGENT_RESPONSE_TOOL_NAME,
            description=(
                "Finish the turn by answering or asking a clarification using only the "
                "authoritative response facts. Select relevant fact_ids and include every "
                f"mandatory ID: {', '.join(mandatory) or 'none'}."
            ),
            input_schema={
                "type": "object",
                "required": ["action", "fact_ids"],
                "properties": {
                    "action": {"type": "string", "enum": ["answer", "clarify"]},
                    "fact_ids": {
                        "type": "array",
                        "items": {"type": "string", "enum": fact_ids},
                        "uniqueItems": True,
                        "maxItems": len(fact_ids),
                    },
                    "complete_objective": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
        )
    return RuntimeToolDefinition(
        name=_AGENT_RESPONSE_TOOL_NAME,
        description=(
            "Finish the turn with a direct answer or a necessary clarification. Use the "
            "capability manifest as authoritative; never claim a supplied capability is "
            "unavailable. Do not use this instead of a matching capability when the user "
            "asked you to perform the action."
        ),
        input_schema={
            "type": "object",
            "required": ["action", "content"],
            "properties": {
                "action": {"type": "string", "enum": ["answer", "clarify"]},
                "content": {"type": "string", "minLength": 1, "maxLength": _MAX_TEXT},
                "complete_objective": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    )


def _response_tool_decision(
    arguments: Mapping[str, Any], request: AgentModelRequest
) -> AgentDecision:
    try:
        action = AgentBoundaryAction(arguments.get("action"))
        if action not in {AgentBoundaryAction.ANSWER, AgentBoundaryAction.CLARIFY}:
            raise ValueError("response action must answer or clarify")
        complete_objective = arguments.get("complete_objective", False)
        if not isinstance(complete_objective, bool):
            raise ValueError("complete_objective must be a boolean")
        if request.response_facts and not request.clarification_only:
            raw_ids = arguments.get("fact_ids")
            if not isinstance(raw_ids, list) or not all(isinstance(item, str) for item in raw_ids):
                raise ValueError("fact_ids must be a string list")
            known = {fact.fact_id for fact in request.response_facts}
            mandatory = {fact.fact_id for fact in request.response_facts if fact.mandatory}
            selected = set(raw_ids)
            if not selected.issubset(known) or not mandatory.issubset(selected):
                raise ValueError("response fact selection is invalid")
            return AgentDecision(
                action=action,
                complete_objective=complete_objective,
                response_plan=ResponseSynthesisPlan(fact_ids=raw_ids),
            )
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("response content is required")
        return AgentDecision(
            action=action,
            content=content,
            complete_objective=complete_objective,
        )
    except (TypeError, ValueError) as exc:
        raise AgentModelError(AgentStopReason.INVALID_MODEL_OUTPUT) from exc


def _safe_history(history: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in list(history)[-_MAX_HISTORY:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            result.append({"role": role, "content": content[:_MAX_TEXT]})
    return result


def _trace_event(
    trace: Sequence[AgentTraceEvent],
    *,
    started: float,
    clock: Callable[[], float],
    iteration: int,
    tool_calls: int,
    usage: AgentUsage,
    action: AgentBoundaryAction | None = None,
    capability_id: str | None = None,
    observation: CapabilityObservation | None = None,
    stop_reason: AgentStopReason | None = None,
) -> AgentTraceEvent:
    return AgentTraceEvent(
        sequence=len(trace) + 1,
        iteration=iteration,
        action=action,
        capability_id=capability_id,
        observation_status=observation.status if observation else None,
        workflow_ref=(
            _safe_ref(observation.workflow_ref, _SAFE_WORKFLOW_REF_KEYS) if observation else None
        ),
        receipt_ref=(
            _safe_ref(observation.receipt_ref, _SAFE_RECEIPT_REF_KEYS) if observation else None
        ),
        stop_reason=stop_reason,
        elapsed_ms=max(0, int((clock() - started) * 1000)),
        tool_calls_used=tool_calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=round(usage.total_cost_usd, 6),
    )


def _stopped_result(
    *,
    reason: AgentStopReason,
    output: str,
    state_version: str,
    fact_catalog: list[ResponseFact],
    observations: list[CapabilityObservation],
    trace: list[AgentTraceEvent],
    usage: AgentUsage,
    started: float,
    clock: Callable[[], float],
    iteration: int,
    tool_calls: int,
    action: AgentBoundaryAction | None = None,
    capability_id: str | None = None,
    observation: CapabilityObservation | None = None,
    response_fingerprint_value: str | None = None,
    no_progress: bool = False,
) -> AgentLoopResult:
    trace.append(
        _trace_event(
            trace,
            started=started,
            clock=clock,
            iteration=iteration,
            action=action,
            capability_id=capability_id,
            observation=observation,
            stop_reason=reason,
            tool_calls=tool_calls,
            usage=usage,
        )
    )
    typed_response = render_typed_response(
        action=action.value if action is not None else None,
        content=output,
        plan=None,
        facts=fact_catalog,
        state_version=state_version,
        stop_reason=reason.value,
        observations=observations,
        no_progress=no_progress,
    )
    return AgentLoopResult(
        action=action,
        output=typed_response.content,
        stop_reason=reason,
        observations=observations,
        trace=trace,
        usage=usage,
        state_version=state_version,
        response_fingerprint=(
            response_fingerprint_value
            or (typed_response.factual_fingerprint if fact_catalog else None)
        ),
        typed_response=typed_response,
    )


def _safe_ref(
    value: Mapping[str, Any] | None, allowed_keys: frozenset[str]
) -> dict[str, Any] | None:
    if not value:
        return None
    safe = {
        key: item if isinstance(item, (int, float, bool)) else str(item)[:500]
        for key, item in value.items()
        if key in allowed_keys and item is not None
    }
    return safe or None


def _is_registered(registry: CapabilityRegistry, capability_id: str) -> bool:
    try:
        registry.handler_for(capability_id)
        return True
    except (UnknownCapabilityError, CapabilityRegistryError):
        return False


def _apply_turn_capability_policy(
    manifest: RuntimeCapabilityManifest,
    context: CapabilityExecutionContext,
) -> RuntimeCapabilityManifest:
    allowed = context.allowed_capability_ids
    denied = context.denied_capability_ids
    tools = [
        tool
        for tool in manifest.tools
        if (allowed is None or tool.id in allowed) and tool.id not in denied
    ]
    return manifest.model_copy(
        update={
            "tools": tools,
            "total": len(tools),
            "filtered": manifest.filtered + len(manifest.tools) - len(tools),
        }
    )


def _declared_capability_cost(metadata: Mapping[str, Any]) -> float:
    value = metadata.get("estimated_cost_usd", 0.0)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return 0.0


def _idempotency_key(state_version: str, iteration: int, call_key: str) -> str:
    digest = hashlib.sha256(f"{state_version}:{iteration}:{call_key}".encode("utf-8")).hexdigest()
    return f"agent-{digest[:40]}"


def _provider_tool_name(capability_id: str) -> str:
    digest = hashlib.sha256(capability_id.encode("utf-8")).hexdigest()[:24]
    return f"nexus_tool_{digest}"


def _agent_usage(usage: UsageStats | None) -> AgentUsage:
    if usage is None:
        return AgentUsage()
    cost = usage.cost_usd or usage.calculate_cost()
    return AgentUsage(
        input_tokens=max(0, usage.input_tokens),
        output_tokens=max(0, usage.output_tokens),
        model_cost_usd=max(0.0, cost),
    )


def _add_usage(current: AgentUsage, added: AgentUsage) -> AgentUsage:
    return AgentUsage(
        input_tokens=current.input_tokens + added.input_tokens,
        output_tokens=current.output_tokens + added.output_tokens,
        model_cost_usd=current.model_cost_usd + added.model_cost_usd,
        capability_cost_usd=current.capability_cost_usd + added.capability_cost_usd,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "AgentBoundaryAction",
    "AgentDecision",
    "AgentDecisionModel",
    "AgentLoopInput",
    "AgentLoopLimits",
    "AgentLoopResult",
    "AgentModelError",
    "AgentModelRequest",
    "AgentModelResult",
    "AgentStopReason",
    "AgentTraceEvent",
    "AgentUsage",
    "BoundedAgentLoop",
    "ProviderAgentDecisionModel",
    "authoritative_state_version",
    "canonical_capability_call",
    "non_stream_transport",
    "registered_capability_manifest",
    "response_fingerprint",
    "stream_transport",
]
