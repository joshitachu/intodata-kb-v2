"""Provider-neutral, model-guided planning for harness chat turns.

The model is a proposal mechanism only. Every returned decision and capability
call is validated again at this boundary before another harness component may
act on it.

Observability contract: no planning failure is ever swallowed silently. Every
degradation is classified into a closed ``fallback_reason`` enum, logged, and
surfaced on the returned :class:`PlannerTurnResult` so chat routing can record
why a turn was routed the way it was.
"""

from __future__ import annotations

import inspect
import json
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.harness.evidence_policy import EvidenceSource

from src.generation.llm_client import generate_answer_with_runtime_tools
from src.generation.providers.types import ToolAwareLLMResponse
from src.harness.manifest import RuntimeCapabilityManifest, RuntimeCapabilityTool
from src.runtime.models import RuntimeToolDefinition


logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_CALLS = 3
DEFAULT_HISTORY_LIMIT = 8
_MAX_TEXT_LENGTH = 8_000
_MAX_ANSWER_LENGTH = 4_000
_MAX_LOGGED_ERROR_CHARS = 200
_PROSE_ANSWER_CONFIDENCE = 0.5

# Closed fallback-reason vocabulary. Values outside this set (besides the
# dynamic ``provider_error:<Type>`` form) must not be emitted.
FALLBACK_NO_TOOL_SUPPORT = "no_tool_support"
FALLBACK_INVALID_JSON = "invalid_json"
FALLBACK_SCHEMA_VALIDATION = "schema_validation"
FALLBACK_UNKNOWN_CAPABILITY = "unknown_capability"
FALLBACK_BUDGET_EXCEEDED = "budget_exceeded"
FALLBACK_EMPTY_ANSWER = "empty_answer"
FALLBACK_PROVIDER_ERROR_PREFIX = "provider_error:"

NOTE_PROSE_ANSWER = "prose_answer"

_RETRYABLE_REASONS = frozenset(
    {
        FALLBACK_SCHEMA_VALIDATION,
        FALLBACK_UNKNOWN_CAPABILITY,
        FALLBACK_BUDGET_EXCEEDED,
        FALLBACK_EMPTY_ANSWER,
    }
)


class PlannerAction(str, Enum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    EXECUTE = "execute"
    CONTINUE = "continue"
    CANCEL = "cancel"


class TurnTaskType(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    DOCUMENT_ANSWER = "document_answer"
    RUNTIME_ANSWER = "runtime_answer"
    OPERATIONAL_INVESTIGATION = "operational_investigation"
    DOCUMENT_GENERATION = "document_generation"
    DOCUMENTATION_PACK_MANAGEMENT = "documentation_pack_management"
    DOCUMENT_UPDATE = "document_update"
    PROJECT_ONBOARDING = "project_onboarding"
    CONNECTOR_INVENTORY = "connector_inventory"
    CLARIFICATION = "clarification"


class TurnExecutionMode(str, Enum):
    DIRECT = "direct"
    EVIDENCE = "evidence"
    WORKFLOW = "workflow"
    CLARIFICATION = "clarification"


class RuntimeScopeKind(str, Enum):
    """The only supported selection shapes for runtime evidence."""

    GLOBAL = "global"
    SINGLE_PROJECT = "single_project"
    MULTI_PROJECT = "multi_project"


class RuntimeScope(BaseModel):
    """Typed runtime selection persisted with a runtime turn plan.

    ``project_ids`` are authenticated Nexus project identifiers only. Runtime
    engines, jobs, and sources remain request entities, never project scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RuntimeScopeKind
    project_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=50)

    @model_validator(mode="after")
    def validate_selection(self) -> RuntimeScope:
        if len(set(self.project_ids)) != len(self.project_ids):
            raise ValueError("runtime scope project_ids must be unique")
        if any(not project_id.strip() for project_id in self.project_ids):
            raise ValueError("runtime scope project_ids must be non-empty")
        expected_count = {
            RuntimeScopeKind.GLOBAL: 0,
            RuntimeScopeKind.SINGLE_PROJECT: 1,
        }.get(self.kind)
        if expected_count is not None and len(self.project_ids) != expected_count:
            raise ValueError(f"{self.kind.value} runtime scope has an invalid project selection")
        if self.kind is RuntimeScopeKind.MULTI_PROJECT and len(self.project_ids) < 2:
            raise ValueError("multi_project runtime scope requires at least two projects")
        return self

    @property
    def project_id(self) -> str | None:
        return self.project_ids[0] if self.kind is RuntimeScopeKind.SINGLE_PROJECT else None


class EvidencePolicy(BaseModel):
    """Closed evidence boundary for a chat turn."""

    model_config = ConfigDict(extra="forbid")

    allowed: list[EvidenceSource] = Field(default_factory=list)
    denied: list[EvidenceSource] = Field(default_factory=list)
    preferred: list[EvidenceSource] = Field(default_factory=list)
    required: list[EvidenceSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sets(self) -> EvidencePolicy:
        allowed = set(self.allowed)
        denied = set(self.denied)
        if allowed & denied:
            raise ValueError("evidence sources cannot be both allowed and denied")
        for item in (*self.preferred, *self.required):
            if item not in allowed:
                raise ValueError("preferred and required evidence must be allowed")
        return self


class TurnBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tool_calls: int = Field(default=3, ge=0, le=20)
    max_evidence_items: int = Field(default=30, ge=0, le=200)
    max_duration_ms: int = Field(default=30_000, ge=100, le=300_000)


class TurnPlan(BaseModel):
    """Validated, persisted routing authority for one conversation turn."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    objective: str = Field(min_length=1, max_length=2_000)
    task_type: TurnTaskType
    resolved_scope: dict[str, Any] = Field(default_factory=dict)
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    execution_mode: TurnExecutionMode
    output_contract: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)
    missing_slots: list[str] = Field(default_factory=list)
    continuation: dict[str, Any] = Field(default_factory=dict)
    budget: TurnBudget = Field(default_factory=TurnBudget)
    capability_ids: list[str] = Field(default_factory=list)
    decision_source: str = Field(default="model", max_length=80)

    def allows(self, evidence_source: str) -> bool:
        return (
            evidence_source in self.evidence_policy.allowed
            and evidence_source not in self.evidence_policy.denied
        )


class ContinuationSlot(BaseModel):
    """A constrained field that may be filled by a later user turn."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    value: Any | None = None
    required: bool = True
    options: list[dict[str, str]] = Field(default_factory=list)


class ContinuationEnvelope(BaseModel):
    """Versioned, resumable authority for a multi-turn user request."""

    model_config = ConfigDict(extra="forbid")

    continuation_id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)
    status: str = Field(pattern="^(active|completed|superseded)$")
    plan: TurnPlan
    slots: list[ContinuationSlot] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)


class PlannedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelPlannerDecision(BaseModel):
    """Validated planner output safe to pass to orchestration code."""

    model_config = ConfigDict(extra="forbid")

    action: PlannerAction
    tool_calls: list[PlannedToolCall] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    answer: str | None = Field(default=None, max_length=_MAX_ANSWER_LENGTH)
    clarification: str | None = Field(default=None, max_length=2_000)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().casefold()
            labels = {"low": 0.25, "medium": 0.55, "high": 0.85}
            if normalized in labels:
                return labels[normalized]
            try:
                return float(normalized)
            except ValueError:
                return value
        return value

    @model_validator(mode="after")
    def validate_action_payload(self) -> ModelPlannerDecision:
        if self.action == PlannerAction.EXECUTE and not self.tool_calls:
            raise ValueError("execute decisions require at least one tool call")
        if self.action != PlannerAction.EXECUTE and self.tool_calls:
            raise ValueError("only execute decisions may contain tool calls")
        if self.action == PlannerAction.CLARIFY and not self.clarification:
            raise ValueError("clarify decisions require user-facing clarification")
        return self


class PlannerTurnResult(BaseModel):
    """A validated decision plus the reason it was reached.

    ``decision_source`` is ``model`` for every decision derived from model
    output (including prose degradations, flagged via ``note``), and
    ``fallback`` when the model could not produce anything usable and the
    caller must route deterministically. ``fallback_reason`` is only set for
    ``fallback`` results.
    """

    decision: ModelPlannerDecision
    decision_source: str = "model"
    fallback_reason: str | None = None
    note: str | None = None
    retried: bool = False
    latency_ms: int = 0


class _PlanValidationError(ValueError):
    """Model output was structured but failed Nexus validation."""

    def __init__(self, message: str, *, reason: str = FALLBACK_SCHEMA_VALIDATION) -> None:
        super().__init__(message)
        self.reason = reason


class _ProseTextError(Exception):
    """Model ignored the JSON protocol and answered in plain language."""

    def __init__(self, text: str) -> None:
        super().__init__("planner returned prose instead of a JSON decision")
        self.text = text


PlannerCallable = Callable[
    [list[dict[str, str]], list[RuntimeToolDefinition]],
    ToolAwareLLMResponse | Awaitable[ToolAwareLLMResponse],
]


async def plan_harness_turn(
    *,
    user_message: str,
    manifest: RuntimeCapabilityManifest,
    project_options: Sequence[Mapping[str, Any] | BaseModel] = (),
    pending_workflow_summary: str | None = None,
    conversation_history: Sequence[Mapping[str, Any]] = (),
    planner: PlannerCallable | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> ModelPlannerDecision:
    """Compatibility wrapper over :func:`plan_harness_turn_detailed`."""

    result = await plan_harness_turn_detailed(
        user_message=user_message,
        manifest=manifest,
        project_options=project_options,
        pending_workflow_summary=pending_workflow_summary,
        conversation_history=conversation_history,
        planner=planner,
        max_tool_calls=max_tool_calls,
        history_limit=history_limit,
    )
    return result.decision


async def plan_harness_turn_detailed(
    *,
    user_message: str,
    manifest: RuntimeCapabilityManifest,
    project_options: Sequence[Mapping[str, Any] | BaseModel] = (),
    pending_workflow_summary: str | None = None,
    conversation_history: Sequence[Mapping[str, Any]] = (),
    planner: PlannerCallable | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> PlannerTurnResult:
    """Ask the selected model for a plan and validate its untrusted response.

    Invalid, unsupported, or unavailable model output degrades in this order:

    1. Plain prose (non-JSON text, no tool calls) becomes an ``answer``
       decision - prose cannot execute anything, so it is safe to show.
    2. Structured-but-invalid output gets exactly one corrective retry.
    3. Everything else falls back to a no-tool decision with a classified
       ``fallback_reason``. This function never executes a capability.
    """

    if max_tool_calls < 1:
        raise ValueError("max_tool_calls must be at least 1")
    started = time.perf_counter()
    tools_by_id = {tool.id: tool for tool in manifest.tools if tool.available}
    provider_names = {
        _provider_tool_name(capability_id): capability_id
        for capability_id in tools_by_id
    }
    runtime_tools = [
        _runtime_tool(tool, provider_name=_provider_tool_name(tool.id))
        for tool in tools_by_id.values()
    ]
    messages = _planner_messages(
        user_message=user_message,
        tools=tuple(tools_by_id.values()),
        application_context=manifest.application_context,
        project_options=project_options,
        pending_workflow_summary=pending_workflow_summary,
        conversation_history=conversation_history,
        history_limit=history_limit,
        max_tool_calls=max_tool_calls,
    )

    call = planner or generate_answer_with_runtime_tools
    retried = False
    while True:
        try:
            response = call(messages, runtime_tools)
            if inspect.isawaitable(response):
                response = await response
        except Exception as exc:
            # Provider failures are never retried here: llm_client owns
            # provider-chain retries, and a second identical call would only
            # double the outage latency.
            return _fallback_result(
                reason=f"{FALLBACK_PROVIDER_ERROR_PREFIX}{exc.__class__.__name__}",
                error=exc,
                retried=retried,
                started=started,
            )

        if not isinstance(response, ToolAwareLLMResponse):
            return _fallback_result(
                reason=f"{FALLBACK_PROVIDER_ERROR_PREFIX}InvalidResponseType",
                error=TypeError(f"planner returned {type(response).__name__}"),
                retried=retried,
                started=started,
            )
        if response.no_tool_support:
            return _fallback_result(
                reason=FALLBACK_NO_TOOL_SUPPORT,
                error=None,
                retried=retried,
                started=started,
            )

        try:
            decision = _response_decision(
                response,
                provider_names=provider_names,
                max_tool_calls=max_tool_calls,
            )
            _validate_calls(decision, tools_by_id, max_tool_calls=max_tool_calls)
            if decision.action == PlannerAction.ANSWER and not (decision.answer or "").strip():
                raise _PlanValidationError(
                    "answer decisions require answer text",
                    reason=FALLBACK_EMPTY_ANSWER,
                )
            return PlannerTurnResult(
                decision=decision,
                decision_source="model",
                retried=retried,
                latency_ms=_elapsed_ms(started),
            )
        except _ProseTextError as prose:
            # The model ignored the envelope but said something harmless.
            # Prose cannot execute capabilities, so surface it as the answer
            # rather than whiplashing the user into a different routing path.
            logger.info(
                "Planner returned prose instead of JSON; degrading to answer decision "
                "(retried=%s, chars=%d)",
                retried,
                len(prose.text),
            )
            return PlannerTurnResult(
                decision=ModelPlannerDecision(
                    action=PlannerAction.ANSWER,
                    confidence=_PROSE_ANSWER_CONFIDENCE,
                    answer=prose.text[:_MAX_ANSWER_LENGTH],
                ),
                decision_source="model",
                note=NOTE_PROSE_ANSWER,
                retried=retried,
                latency_ms=_elapsed_ms(started),
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            reason = _classify_validation_failure(exc)
            if not retried and reason in _RETRYABLE_REASONS:
                retried = True
                messages = _corrective_messages(messages, response, exc)
                logger.warning(
                    "Planner output failed validation (%s: %s); retrying once with "
                    "corrective instruction",
                    reason,
                    _bounded_error_text(exc),
                )
                continue
            return _fallback_result(reason=reason, error=exc, retried=retried, started=started)


def _classify_validation_failure(exc: Exception) -> str:
    if isinstance(exc, _PlanValidationError):
        return exc.reason
    if isinstance(exc, json.JSONDecodeError):
        return FALLBACK_INVALID_JSON
    return FALLBACK_SCHEMA_VALIDATION


def _fallback_result(
    *,
    reason: str,
    error: Exception | None,
    retried: bool,
    started: float,
) -> PlannerTurnResult:
    logger.warning(
        "Planner fallback: reason=%s retried=%s error=%s",
        reason,
        retried,
        _bounded_error_text(error) if error is not None else "-",
    )
    return PlannerTurnResult(
        decision=_safe_fallback(),
        decision_source="fallback",
        fallback_reason=reason,
        retried=retried,
        latency_ms=_elapsed_ms(started),
    )


def _corrective_messages(
    messages: list[dict[str, str]],
    response: ToolAwareLLMResponse,
    error: Exception,
) -> list[dict[str, str]]:
    if response.tool_calls:
        previous = json.dumps(
            [
                {"tool_name": call.tool_name, "arguments": call.arguments}
                for call in response.tool_calls
            ],
            separators=(",", ":"),
        )
    else:
        previous = response.text or ""
    return [
        *messages,
        {"role": "assistant", "content": _bounded_text(previous) or "(empty response)"},
        {
            "role": "user",
            "content": (
                "Your previous response was invalid: "
                f"{_bounded_error_text(error)}. "
                "Respond again following the protocol exactly: either call one "
                "supplied tool, or return only the JSON object "
                '{"action", "tool_calls", "confidence", "answer", "clarification"} '
                "using only supplied capability tool names and allowed values."
            ),
        },
    ]


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _bounded_error_text(error: Exception) -> str:
    return str(error)[:_MAX_LOGGED_ERROR_CHARS]


def _response_decision(
    response: ToolAwareLLMResponse,
    *,
    provider_names: Mapping[str, str],
    max_tool_calls: int,
) -> ModelPlannerDecision:
    if response.tool_calls:
        if len(response.tool_calls) > max_tool_calls:
            raise _PlanValidationError(
                "planner exceeded the tool-call budget",
                reason=FALLBACK_BUDGET_EXCEEDED,
            )
        return ModelPlannerDecision(
            action=PlannerAction.EXECUTE,
            tool_calls=[
                PlannedToolCall(
                    capability_id=provider_names.get(call.tool_name, call.tool_name),
                    arguments=call.arguments,
                )
                for call in response.tool_calls
            ],
            confidence=1.0,
        )
    if not response.text:
        raise _PlanValidationError(
            "planner returned neither a decision nor tool calls",
            reason=FALLBACK_EMPTY_ANSWER,
        )
    text = response.text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) < 3:
            raise _PlanValidationError("planner decision code fence is empty")
        text = "\n".join(lines[1:-1]).strip()
        if not text:
            raise _PlanValidationError("planner decision code fence is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise _ProseTextError(text) from None
    if not isinstance(payload, dict):
        # Valid JSON that is not a decision object (a bare string, list, or
        # number) is conversational output, not a malformed plan.
        raise _ProseTextError(text)
    if payload.get("action") == PlannerAction.EXECUTE.value:
        raw_calls = payload.get("tool_calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise _PlanValidationError("execute decisions require tool calls")
        normalized_calls: list[dict[str, Any]] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict):
                raise _PlanValidationError("planner text tool call has an invalid shape")
            function = raw_call.get("function")
            if isinstance(function, dict):
                if not set(raw_call).issubset({"function", "type", "id"}) or not set(
                    function
                ).issubset({"name", "arguments"}):
                    raise _PlanValidationError("planner text tool call has an invalid shape")
                raw_call = {**raw_call, **function}
            elif not set(raw_call).issubset(
                {
                    "tool_name",
                    "capability_id",
                    "name",
                    "parameters",
                    "arguments",
                    "args",
                    "input",
                }
            ):
                raise _PlanValidationError("planner text tool call has an invalid shape")
            tool_name = (
                raw_call.get("tool_name")
                or raw_call.get("capability_id")
                or raw_call.get("name")
            )
            parameters = raw_call.get(
                "parameters",
                raw_call.get("arguments", raw_call.get("args", raw_call.get("input", {}))),
            )
            if isinstance(parameters, str):
                try:
                    parameters = json.loads(parameters)
                except json.JSONDecodeError:
                    raise _PlanValidationError(
                        "planner text tool call arguments are not valid JSON"
                    ) from None
            if not isinstance(tool_name, str) or not isinstance(parameters, dict):
                raise _PlanValidationError("planner text tool call has invalid values")
            normalized_calls.append(
                {
                    "capability_id": provider_names.get(tool_name, tool_name),
                    "arguments": parameters,
                }
            )
        payload = {**payload, "tool_calls": normalized_calls}
    return ModelPlannerDecision.model_validate(payload)


def _validate_calls(
    decision: ModelPlannerDecision,
    tools_by_id: Mapping[str, RuntimeCapabilityTool],
    *,
    max_tool_calls: int,
) -> None:
    if len(decision.tool_calls) > max_tool_calls:
        raise _PlanValidationError(
            "planner exceeded the tool-call budget",
            reason=FALLBACK_BUDGET_EXCEEDED,
        )
    for call in decision.tool_calls:
        tool = tools_by_id.get(call.capability_id)
        if tool is None:
            raise _PlanValidationError(
                "planner selected an unknown or unavailable capability",
                reason=FALLBACK_UNKNOWN_CAPABILITY,
            )
        _validate_schema(call.arguments, tool.input_schema, path="arguments")


def _validate_schema(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    """Validate the useful JSON-schema subset emitted by capability manifests."""

    expected = schema.get("type")
    allowed_types = expected if isinstance(expected, list) else [expected] if expected else []
    if allowed_types and not any(_matches_json_type(value, item) for item in allowed_types):
        raise _PlanValidationError(f"{path} has an invalid type")
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        raise _PlanValidationError(f"{path} is not an allowed value")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or any(key not in value for key in required):
            raise _PlanValidationError(f"{path} is missing required fields")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise _PlanValidationError(f"{path} has an invalid schema")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise _PlanValidationError(f"{path} contains unknown fields")
        for key, item in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _validate_schema(item, child_schema, path=f"{path}.{key}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], path=f"{path}[{index}]")
    elif isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            raise _PlanValidationError(f"{path} is too short")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            raise _PlanValidationError(f"{path} is too long")


def _matches_json_type(value: Any, expected: Any) -> bool:
    return {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, False)


def _runtime_tool(
    tool: RuntimeCapabilityTool, *, provider_name: str
) -> RuntimeToolDefinition:
    return RuntimeToolDefinition(
        name=provider_name,
        description=(
            f"Capability {tool.id}. {tool.label}: {tool.action}. "
            f"Use when: {'; '.join(tool.use_when) or 'applicable to the request'}. "
            f"Do not use when: {'; '.join(tool.do_not_use_when) or 'not applicable'}."
        ),
        input_schema=tool.input_schema,
    )


def _provider_tool_name(capability_id: str) -> str:
    """Return a provider-safe opaque function name for an internal capability."""

    digest = hashlib.sha256(capability_id.encode("utf-8")).hexdigest()[:24]
    return f"nexus_tool_{digest}"


def _planner_messages(
    *,
    user_message: str,
    tools: Sequence[RuntimeCapabilityTool],
    application_context: Mapping[str, Any],
    project_options: Sequence[Mapping[str, Any] | BaseModel],
    pending_workflow_summary: str | None,
    conversation_history: Sequence[Mapping[str, Any]],
    history_limit: int,
    max_tool_calls: int,
) -> list[dict[str, str]]:
    safe_projects = []
    for option in project_options:
        if isinstance(option, BaseModel):
            raw = option.model_dump()
        elif isinstance(option, Mapping):
            raw = dict(option)
        else:
            raw = vars(option)
        safe_projects.append(
            {key: _bounded_text(raw[key]) for key in ("id", "display_name", "name") if key in raw}
        )
    context = {
        "capabilities": [
            {
                "id": tool.id,
                "tool_name": _provider_tool_name(tool.id),
                "label": tool.label,
                "action": tool.action,
                "use_when": tool.use_when,
                "do_not_use_when": tool.do_not_use_when,
                "task_types": tool.task_types,
                "evidence_sources": tool.evidence_sources,
                "output_contracts": tool.output_contracts,
            }
            for tool in tools
        ],
        "projects": safe_projects,
        "pending_workflow": _bounded_text(pending_workflow_summary),
        "max_tool_calls": max_tool_calls,
        "application": dict(application_context),
    }
    system = (
        "You are the provider-neutral Nexus harness planner. Choose exactly one action: "
        "answer, clarify, execute, continue, or cancel. Use only supplied capability IDs "
        "and use only supplied project IDs when populating project_id or project_ids arguments. "
        "Use project_ids only for an explicit multi-project selection. Names in "
        "the request may instead identify runtime engines, jobs, sources, plans, or other "
        "domain entities; preserve those names in the capability question and never reject "
        "them merely because they are not project IDs. For an explicit runtime-data request, "
        "execute the runtime evidence capability and omit project_id when the user has not "
        "selected one; the harness owns any required scope clarification. Do not answer questions "
        "about currently available, organization, or private Documentation "
        "Packs from memory; execute the supplied Documentation Pack catalog capability. "
        "Internal application modules and capabilities are not Documentation Packs. Never present "
        "them as Pack choices. Never invent tools "
        "or include provider identifiers, credentials, "
        "or secrets. For execute, call the supplied tool_name corresponding to the desired "
        "capability. Otherwise return only a "
        "JSON object matching {action, tool_calls: [], confidence, answer, clarification}. "
        f"Runtime-safe context: {json.dumps(context, separators=(',', ':'))}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for item in list(conversation_history)[-max(0, history_limit):]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": _bounded_text(content) or ""})
    messages.append({"role": "user", "content": _bounded_text(user_message) or ""})
    return messages


def _bounded_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:_MAX_TEXT_LENGTH]


def _safe_fallback() -> ModelPlannerDecision:
    return ModelPlannerDecision(
        action=PlannerAction.ANSWER,
        confidence=0.0,
        answer=None,
        clarification=None,
    )


__all__ = [
    "EvidencePolicy",
    "ModelPlannerDecision",
    "PlannedToolCall",
    "PlannerAction",
    "PlannerCallable",
    "PlannerTurnResult",
    "RuntimeScope",
    "RuntimeScopeKind",
    "TurnBudget",
    "TurnExecutionMode",
    "TurnPlan",
    "TurnTaskType",
    "plan_harness_turn",
    "plan_harness_turn_detailed",
]
