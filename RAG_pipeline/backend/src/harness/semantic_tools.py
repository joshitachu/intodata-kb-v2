"""Disabled shadow-only semantic tools over the authoritative Nexus registry."""

from __future__ import annotations

import asyncio
import contextvars
import copy
import difflib
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from jsonschema import Draft202012Validator
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
)
from src.harness.conversation_state import (
    ConversationPreparedAction,
    PreparedActionValidationError,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.interaction_protocol import (
    TURN_ASSESSMENT_TOOL_NAME,
    TurnAssessment,
)
from src.harness.manifest import (
    ManifestScope,
    build_runtime_capability_manifest,
    runtime_visibility_reason,
)
from src.harness.models import CapabilityDefinition, HarnessProviderType, HarnessRiskTier
from src.harness.registry import CapabilityRegistry
from src.harness.approvals import ApprovalService
from src.harness.agent_trace import safe_argument_shape, safe_validation_errors
from src.harness.effect_operations import EffectOperationService
from src.harness.run_budgets import (
    AgentBudgetStop,
    BudgetStopReason,
    LayeredBudgetController,
)


SEMANTIC_CONTRACT_VERSION = "1.0.0"
SEMANTIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CALL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
MAX_TOOL_MESSAGE_BYTES = 16_384
MAX_TOOL_DATA_BYTES = 12_000
# Seven levels, not five: a topology navigation target is a contract-shaped
# payload six levels deep (data -> targets -> target -> level -> selection ->
# id), and a relationship selection nests its streamKeys one deeper again.
# Truncating those leaf ids yields a chip that navigates nowhere. Size is
# still bounded by MAX_TOOL_DATA_BYTES, string length, and container width, so
# the extra levels cannot be used to smuggle a large payload.
MAX_CONTAINER_DEPTH = 7
MAX_CONTAINER_ITEMS = 20
MAX_STRING_CHARS = 500
MAX_EVIDENCE_REFS = 10

_CURRENT_CALL_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "nexus_semantic_call_id", default=None
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b"),
)
_PRIVATE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "reasoning",
        "thinking",
        "chain_of_thought",
        "error_message",
        "traceback",
        "sql",
    }
)
_REFERENCE_KEYS = frozenset(
    {
        "id",
        "evidence_id",
        "source_type",
        "project_id",
        "document_id",
        "chunk_index",
        "runtime_record_id",
        "uri",
        "observed_at",
        "score",
        "provider_id",
        "external_run_id",
        "idempotency_key",
    }
)
_RECOVERY_KEYS = frozenset(
    {"validation_failed", "approval_required", "denied", "unavailable", "failed"}
)
MAX_PARALLEL_READ_CALLS = 3
MAX_PROPOSAL_CORRECTIONS = 1
_REQUIRED_PROJECTION_TOOL = "project_objective"
_MAX_CAPABILITY_TOOL_TIMEOUT_SECONDS = 120.0


def _normalized_message_fingerprint(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _human_message_text(message: HumanMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return " ".join(parts)


def _clarification_grounding_error(
    assessment: TurnAssessment,
    messages: list[Any],
) -> dict[str, Any] | None:
    clarification = assessment.clarification_to_user
    if clarification is None:
        return None
    fingerprint = _normalized_message_fingerprint(clarification)
    if not fingerprint:
        return {
            "path": ["clarification_to_user"],
            "code": "empty_clarification",
            "message": "The clarification must contain a direct question.",
        }
    if _clarification_echoes_user(clarification, messages):
        return {
            "path": ["clarification_to_user"],
            "code": "user_message_echo",
            "message": "The clarification must not repeat a user message.",
        }
    return None


def _clarification_echoes_user(value: str, messages: list[Any]) -> bool:
    fingerprint = _normalized_message_fingerprint(value)
    if not fingerprint:
        return False
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        user_fingerprint = _normalized_message_fingerprint(_human_message_text(message))
        if not user_fingerprint:
            continue
        similarity = difflib.SequenceMatcher(
            None,
            fingerprint,
            user_fingerprint,
            autojunk=False,
        ).ratio()
        shorter, longer = sorted(
            (fingerprint, user_fingerprint),
            key=len,
        )
        contained_echo = bool(
            shorter and shorter in longer and len(shorter) / max(len(longer), 1) >= 0.8
        )
        if fingerprint == user_fingerprint or similarity >= 0.9 or contained_echo:
            return True
    return False


class SemanticSnapshotError(ValueError):
    """The registry cannot be safely exposed as a semantic tool snapshot."""


class GovernanceClass(StrEnum):
    READ = "read"
    CONSEQUENTIAL = "consequential"


class GatewayMode(StrEnum):
    """Execution mode for the mandatory gateway.

    ``SHADOW`` (default) preserves the exact Slice 2 behavior: only certified
    ``shadow_safe`` reads execute; everything else is proposal-only.
    ``READ_ONLY_PRIMARY`` executes every ``READ``-governed capability for real
    under fresh read-only policy while consequential capabilities stay
    proposal-only.
    """

    SHADOW = "shadow"
    READ_ONLY_PRIMARY = "read_only_primary"
    PLAN_CONTROL_PRIMARY = "plan_control_primary"
    EFFECT_PRIMARY = "effect_primary"


class SemanticToolRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_name: str
    canonical_id: str
    description: str
    input_schema: dict[str, Any]
    output_type: str
    risk_tier: str
    governance_class: GovernanceClass
    requires_approval: bool
    shadow_safe: bool
    evidence_state_relevant: bool
    execution_state_relevant: bool
    preparable_across_turns: bool = False


class UnavailableCapabilityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_id: str
    semantic_name: str | None = None
    reason_code: str


class SemanticToolSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["1.0.0"] = SEMANTIC_CONTRACT_VERSION
    snapshot_hash: str
    pack_snapshot: dict[str, Any] = Field(default_factory=dict)
    pack_snapshot_hash: str
    project_binding_authority: Literal["request_capability_registry"] = (
        "request_capability_registry"
    )
    tools: tuple[SemanticToolRecord, ...]
    unavailable: tuple[UnavailableCapabilityRecord, ...] = ()
    semantic_to_canonical: dict[str, str]
    canonical_to_semantic: dict[str, str]
    handler_schema_output_parity: bool

    def tool_for_name(self, semantic_name: str) -> SemanticToolRecord | None:
        return next(
            (item for item in self.tools if item.semantic_name == semantic_name),
            None,
        )

    def unavailable_for_name(self, semantic_name: str) -> UnavailableCapabilityRecord | None:
        return next(
            (item for item in self.unavailable if item.semantic_name == semantic_name),
            None,
        )


class SemanticEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    kind: str
    call_id: str | None = None
    semantic_name: str | None = None
    canonical_id: str | None = None
    arguments_hash: str | None = None
    policy_class: str | None = None
    observation_status: str | None = None
    plan_required: bool | None = None


class SemanticEventLedger:
    """In-memory, sanitized event ledger for the disabled shadow seam."""

    def __init__(self) -> None:
        self.events: list[SemanticEvent] = []

    def append(self, kind: str, **values: Any) -> SemanticEvent:
        event = SemanticEvent(sequence=len(self.events) + 1, kind=kind, **values)
        self.events.append(event)
        return event


class ToolEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0.0"] = "1.0.0"
    status: Literal[
        "succeeded",
        "triggered",
        "proposal_only",
        "needs_input",
        "correction_required",
        "validation_failed",
        "approval_required",
        "denied",
        "unavailable",
        "failed",
        "call_set_rejected",
        "unknown_tool",
    ]
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    retryable: bool = False
    error: dict[str, str] | None = None
    workflow_ref: dict[str, Any] | None = None
    receipt_ref: dict[str, Any] | None = None
    evidence_state_relevant: bool = True
    execution_state_relevant: bool = False


def build_semantic_tool_snapshot(
    registry: CapabilityRegistry,
    *,
    scope: ManifestScope | None = None,
    expected_names: Mapping[str, str] | None = None,
) -> SemanticToolSnapshot:
    """Build a deterministic collision-checked semantic projection."""

    scope = scope or ManifestScope(include_bounded_loop_only=True)
    manifest = build_runtime_capability_manifest(registry, scope=scope)
    visible_ids = {tool.id for tool in manifest.tools}
    records: list[SemanticToolRecord] = []
    unavailable: list[UnavailableCapabilityRecord] = []
    semantic_to_canonical: dict[str, str] = {}
    canonical_to_semantic: dict[str, str] = {}

    for capability in registry.list(enabled_only=False):
        reason = runtime_visibility_reason(capability, scope)
        if capability.id not in visible_ids:
            unavailable.append(
                UnavailableCapabilityRecord(
                    canonical_id=capability.id,
                    semantic_name=capability.semantic_name,
                    reason_code=reason or "manifest_filtered",
                )
            )
            continue
        _validate_semantic_contract(capability, registry)
        semantic_name = str(capability.semantic_name)
        if expected_names is not None:
            expected = expected_names.get(capability.id)
            if expected is not None and expected != semantic_name:
                raise SemanticSnapshotError(f"semantic name drift for {capability.id}")
        other = semantic_to_canonical.get(semantic_name)
        if other is not None and other != capability.id:
            raise SemanticSnapshotError(f"semantic name collision: {semantic_name}")
        if capability.id in canonical_to_semantic:
            raise SemanticSnapshotError(f"duplicate canonical id: {capability.id}")
        semantic_to_canonical[semantic_name] = capability.id
        canonical_to_semantic[capability.id] = semantic_name
        governance_class = _governance_class(capability)
        records.append(
            SemanticToolRecord(
                semantic_name=semantic_name,
                canonical_id=capability.id,
                description=_semantic_description(capability),
                input_schema=copy.deepcopy(capability.input_schema),
                output_type=capability.output_type,
                risk_tier=capability.risk_tier.value,
                governance_class=governance_class,
                requires_approval=bool(
                    capability.metadata.get("requires_approval")
                    or capability.metadata.get("requires_explicit_approval")
                ),
                shadow_safe=capability.shadow_safe,
                evidence_state_relevant=(
                    governance_class == GovernanceClass.READ
                    and bool(capability.metadata.get("evidence_state_relevant", True))
                ),
                execution_state_relevant=(
                    governance_class == GovernanceClass.CONSEQUENTIAL
                ),
                preparable_across_turns=bool(
                    capability.metadata.get("preparable_across_turns", False)
                ),
            )
        )

    records.sort(key=lambda item: item.semantic_name)
    unavailable.sort(key=lambda item: item.canonical_id)
    pack_snapshot = copy.deepcopy(manifest.pack_snapshot)
    pack_snapshot_hash = _hash_json(pack_snapshot)
    hash_payload = {
        "contract_version": SEMANTIC_CONTRACT_VERSION,
        "pack_snapshot_hash": pack_snapshot_hash,
        "tools": [item.model_dump(mode="json") for item in records],
        "unavailable": [item.model_dump(mode="json") for item in unavailable],
        "semantic_to_canonical": dict(sorted(semantic_to_canonical.items())),
        "canonical_to_semantic": dict(sorted(canonical_to_semantic.items())),
        "project_binding_authority": "request_capability_registry",
    }
    return SemanticToolSnapshot(
        snapshot_hash=_hash_json(hash_payload),
        pack_snapshot=pack_snapshot,
        pack_snapshot_hash=pack_snapshot_hash,
        tools=tuple(records),
        unavailable=tuple(unavailable),
        semantic_to_canonical=dict(sorted(semantic_to_canonical.items())),
        canonical_to_semantic=dict(sorted(canonical_to_semantic.items())),
        handler_schema_output_parity=True,
    )


def _validate_semantic_contract(
    capability: CapabilityDefinition,
    registry: CapabilityRegistry,
) -> None:
    name = capability.semantic_name or ""
    if not SEMANTIC_NAME_RE.fullmatch(name):
        raise SemanticSnapshotError(f"invalid semantic name for {capability.id}")
    if not capability.purpose or not capability.purpose.strip():
        raise SemanticSnapshotError(f"missing purpose for {capability.id}")
    if not all(
        (
            capability.use_when,
            capability.do_not_use_when,
            capability.preconditions,
            capability.output_contracts,
            capability.failure_recovery,
        )
    ):
        raise SemanticSnapshotError(f"incomplete semantic contract for {capability.id}")
    if not set(capability.failure_recovery).issubset(_RECOVERY_KEYS):
        raise SemanticSnapshotError(f"unsupported recovery outcome for {capability.id}")
    try:
        Draft202012Validator.check_schema(capability.input_schema)
    except Exception as exc:
        raise SemanticSnapshotError(f"invalid schema for {capability.id}") from exc
    _require_closed_objects(capability.input_schema, capability_id=capability.id)
    try:
        handler = registry.handler_for(capability.id)
    except Exception as exc:
        raise SemanticSnapshotError(f"missing handler for {capability.id}") from exc
    if dict(handler.input_schema) != capability.input_schema:
        raise SemanticSnapshotError(f"handler schema mismatch for {capability.id}")
    if handler.output_type != capability.output_type:
        raise SemanticSnapshotError(f"handler output mismatch for {capability.id}")
    if tuple(handler.task_types) != tuple(capability.task_types):
        raise SemanticSnapshotError(f"handler task mismatch for {capability.id}")


def _require_closed_objects(schema: Any, *, capability_id: str) -> None:
    if isinstance(schema, list):
        for item in schema:
            _require_closed_objects(item, capability_id=capability_id)
        return
    if not isinstance(schema, dict):
        return
    schema_type = schema.get("type")
    types = set(schema_type if isinstance(schema_type, list) else [schema_type])
    if "object" in types and schema.get("additionalProperties") is not False:
        raise SemanticSnapshotError(f"open object schema for {capability_id}")
    if "array" in types and not isinstance(schema.get("items"), dict):
        raise SemanticSnapshotError(f"array items missing for {capability_id}")
    for key, value in schema.items():
        if key in {"properties", "$defs"} and isinstance(value, dict):
            for nested in value.values():
                _require_closed_objects(nested, capability_id=capability_id)
        elif key in {"items", "anyOf", "oneOf", "allOf", "not"}:
            _require_closed_objects(value, capability_id=capability_id)


def _semantic_description(capability: CapabilityDefinition) -> str:
    recovery = "; ".join(
        f"{key}: {value}" for key, value in sorted(capability.failure_recovery.items())
    )
    sections = (
        ("Purpose", capability.purpose or ""),
        ("Use", "; ".join(capability.use_when)),
        ("Do not use", "; ".join(capability.do_not_use_when)),
        ("Preconditions", "; ".join(capability.preconditions)),
        ("Result", f"{capability.output_type}; {'; '.join(capability.output_contracts)}"),
        (
            "Risk and approval",
            f"{capability.risk_tier.value}; "
            f"approval={'required' if capability.metadata.get('requires_approval') or capability.metadata.get('requires_explicit_approval') else 'not_declared'}",
        ),
        ("Safe recovery", recovery),
    )
    if any(not text.strip() for _, text in sections):
        raise SemanticSnapshotError(f"incomplete description for {capability.id}")
    return " ".join(f"{label}: {text.strip()}" for label, text in sections)


def _governance_class(capability: CapabilityDefinition) -> GovernanceClass:
    if capability.risk_tier == HarnessRiskTier.READ_ONLY:
        return GovernanceClass.READ
    return GovernanceClass.CONSEQUENTIAL


def _is_workflow_handoff(capability: CapabilityDefinition | None, *, is_plan_control: bool) -> bool:
    """Classify workflow providers while honoring an explicit capability override."""

    if capability is None:
        return False
    override = capability.metadata.get("workflow_handoff")
    if isinstance(override, bool):
        return override
    return capability.provider_type is HarnessProviderType.WORKFLOW and not is_plan_control


def _capability_tool_timeout_override(
    capability: CapabilityDefinition | None,
) -> float | None:
    """Return one safe per-capability timeout override, or preserve the default."""

    if capability is None:
        return None
    value = capability.metadata.get("tool_timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        timeout = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(timeout):
        return None
    if timeout <= 0 or timeout > _MAX_CAPABILITY_TOOL_TIMEOUT_SECONDS:
        return None
    return timeout


def _tool_timeout(
    controller: LayeredBudgetController,
    *,
    capability: CapabilityDefinition | None,
    risk_tier: HarnessRiskTier,
    estimated_cost_usd: float,
    workflow_handoff: bool,
) -> float:
    """Charge the tool call, then apply a bounded capability timeout override."""

    configured = controller.before_tool(
        risk_tier=risk_tier,
        estimated_cost_usd=estimated_cost_usd,
        workflow_handoff=workflow_handoff,
    )
    override = _capability_tool_timeout_override(capability)
    if override is None:
        return configured
    return min(override, controller.remaining_hard_seconds())


class SemanticToolGateway:
    """Mandatory shadow gateway; generated tools have no other execution path."""

    def __init__(
        self,
        *,
        snapshot: SemanticToolSnapshot,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
        ledger: SemanticEventLedger | None = None,
        mode: GatewayMode = GatewayMode.SHADOW,
        registry: CapabilityRegistry | None = None,
        approval_service: ApprovalService | None = None,
        effect_operation_service: EffectOperationService | None = None,
        agent_plan_service: Any = None,
        budget_controller: LayeredBudgetController | None = None,
    ) -> None:
        self.snapshot = snapshot
        self._executor = executor
        self._context = context
        self.ledger = ledger or SemanticEventLedger()
        self._read_lock = asyncio.Lock()
        self._mode = mode
        self._registry = registry
        self._approval_service = approval_service
        self._effect_operation_service = effect_operation_service
        self._agent_plan_service = agent_plan_service
        self._budget_controller = budget_controller

    async def execute(
        self,
        semantic_name: str,
        arguments: Mapping[str, Any],
    ) -> ToolEnvelope:
        call_id = _CURRENT_CALL_ID.get()
        if call_id is None or not CALL_ID_RE.fullmatch(call_id):
            return _error_envelope(
                "denied",
                "middleware_proposal_missing",
                "The tool proposal was not authorized by semantic middleware.",
            )
        record = self.snapshot.tool_for_name(semantic_name)
        if record is None:
            if self.snapshot.unavailable_for_name(semantic_name) is not None:
                return _error_envelope(
                    "unavailable",
                    "unavailable",
                    "The requested capability is unavailable.",
                )
            return _error_envelope("unknown_tool", "unknown_tool", "The requested tool is unknown.")
        arguments_hash = _hash_json(dict(arguments))
        is_read = record.governance_class == GovernanceClass.READ
        is_plan_control = record.canonical_id.startswith("nexus.agent_plan.")
        capability = self._registry.get(record.canonical_id) if self._registry is not None else None
        tool_timeout: float | None = None
        if self._budget_controller is not None:
            try:
                tool_timeout = _tool_timeout(
                    self._budget_controller,
                    capability=capability,
                    risk_tier=HarnessRiskTier(record.risk_tier),
                    estimated_cost_usd=float(
                        (capability.metadata if capability else {}).get("estimated_cost_usd", 0.0)
                    ),
                    workflow_handoff=_is_workflow_handoff(
                        capability,
                        is_plan_control=is_plan_control,
                    ),
                )
            except AgentBudgetStop as exc:
                self.ledger.append(
                    "gateway.budget_stopped",
                    call_id=call_id,
                    semantic_name=record.semantic_name,
                    canonical_id=record.canonical_id,
                    arguments_hash=arguments_hash,
                    policy_class=exc.reason.value,
                    observation_status="failed",
                )
                return _error_envelope(
                    "failed",
                    exc.reason.value,
                    "The run budget stopped this tool before execution.",
                )
        primary_read_mode = self._mode in {
            GatewayMode.READ_ONLY_PRIMARY,
            GatewayMode.PLAN_CONTROL_PRIMARY,
            GatewayMode.EFFECT_PRIMARY,
        }
        should_execute = (
            (is_read and (primary_read_mode or record.shadow_safe))
            or (self._mode == GatewayMode.PLAN_CONTROL_PRIMARY and is_plan_control)
            or self._mode == GatewayMode.EFFECT_PRIMARY
        )
        if not should_execute:
            self.ledger.append(
                "gateway.proposal_only",
                call_id=call_id,
                semantic_name=record.semantic_name,
                canonical_id=record.canonical_id,
                arguments_hash=arguments_hash,
                policy_class="proposal_only",
                observation_status="proposal_only",
            )
            return ToolEnvelope(
                status="proposal_only",
                summary="Shadow mode recorded this proposal without executing it.",
                data={"arguments_hash": arguments_hash},
                retryable=False,
                error={
                    "code": "proposal_only",
                    "recovery": "Continue only after an authorized non-shadow decision.",
                },
                evidence_state_relevant=record.evidence_state_relevant,
                execution_state_relevant=record.execution_state_relevant,
            )
        if self._mode == GatewayMode.READ_ONLY_PRIMARY:
            # Real read under fresh read-only policy: no writes, no approvals,
            # no external side effects; the real scope/permissions/turn claim
            # are preserved. The allowed set is exactly the read-only tools.
            read_only_ids = {
                item.canonical_id
                for item in self.snapshot.tools
                if item.governance_class == GovernanceClass.READ
            }
            safe_context = self._context.model_copy(
                update={
                    "approvals": set(),
                    "allow_writes_content": False,
                    "allow_external_side_effects": False,
                    "allowed_capability_ids": read_only_ids,
                    "denied_capability_ids": set(),
                }
            )
            started_policy_class = "read_only_primary_read"
        elif self._mode == GatewayMode.PLAN_CONTROL_PRIMARY:
            plan_and_read_ids = {
                item.canonical_id
                for item in self.snapshot.tools
                if item.governance_class == GovernanceClass.READ
                or item.canonical_id.startswith("nexus.agent_plan.")
            }
            safe_context = self._context.model_copy(
                update={
                    "approvals": set(),
                    "allow_writes_content": False,
                    "allow_external_side_effects": False,
                    "allowed_capability_ids": plan_and_read_ids,
                    "denied_capability_ids": set(),
                }
            )
            started_policy_class = (
                "plan_control_primary_read" if is_read else "plan_control_primary"
            )
        elif self._mode == GatewayMode.EFFECT_PRIMARY:
            safe_context = self._context.model_copy(
                update={
                    "approvals": set(),
                    "allow_writes_content": False,
                    "allow_external_side_effects": False,
                    "allowed_capability_ids": set(self.snapshot.canonical_to_semantic),
                    "denied_capability_ids": set(),
                }
            )
            started_policy_class = "effect_primary"
        else:
            safe_context = self._context.model_copy(
                update={
                    "approvals": set(),
                    "allow_writes_content": False,
                    "allow_external_side_effects": False,
                    "allowed_capability_ids": set(self.snapshot.canonical_to_semantic),
                    "denied_capability_ids": set(),
                    "current_message_id": None,
                    "conversation_state_version": None,
                    "conversation_turn_claim_id": None,
                    "conversation_turn_claim_token": None,
                }
            )
            started_policy_class = "shadow_safe_read"
        self.ledger.append(
            "gateway.executor_started",
            call_id=call_id,
            semantic_name=record.semantic_name,
            canonical_id=record.canonical_id,
            arguments_hash=arguments_hash,
            policy_class=started_policy_class,
        )
        if self._mode == GatewayMode.EFFECT_PRIMARY and not is_read and not is_plan_control:
            if self._registry is None or self._effect_operation_service is None:
                return _error_envelope(
                    "denied", "effect_authority_missing", "Effect execution is not configured."
                )
            capability = self._registry.get(record.canonical_id)
            if record.requires_approval:
                if self._approval_service is None:
                    return _error_envelope(
                        "denied", "approval_authority_missing", "Approval is not configured."
                    )
                proposal = await _bounded_await(
                    self._approval_service.propose(
                        capability=capability,
                        semantic_name=record.semantic_name,
                        arguments=dict(arguments),
                        context=safe_context,
                        call_id=call_id,
                    ),
                    tool_timeout,
                    self._budget_controller,
                )
                envelope = ToolEnvelope(
                    status="approval_required",
                    summary=f"Approval is required before {record.semantic_name.replace('_', ' ')}.",
                    data={
                        "proposal_id": proposal.proposal_id,
                        "proposal_version": proposal.version,
                        "expires_at": proposal.expires_at.isoformat(),
                    },
                    error={
                        "code": "approval_required",
                        "recovery": "Wait for the owner to approve or reject the exact proposal.",
                    },
                    evidence_state_relevant=record.evidence_state_relevant,
                    execution_state_relevant=record.execution_state_relevant,
                )
                if (
                    self._agent_plan_service is not None
                    and safe_context.conversation_id is not None
                    and safe_context.current_message_id is not None
                ):
                    await self._agent_plan_service.record_capability_outcome(
                        conversation_id=safe_context.conversation_id,
                        owner_user_id=safe_context.user_id,
                        message_id=safe_context.current_message_id,
                        capability_id=record.canonical_id,
                        operation_id=f"approval:{proposal.proposal_id}",
                        status="approval_required",
                    )
                self.ledger.append(
                    "gateway.executor_completed",
                    call_id=call_id,
                    semantic_name=record.semantic_name,
                    canonical_id=record.canonical_id,
                    arguments_hash=arguments_hash,
                    policy_class="approval_required",
                    observation_status=envelope.status,
                )
                return envelope
            if safe_context.conversation_turn_claim_id is None:
                return _error_envelope("denied", "turn_claim", "A live turn claim is required.")
            prepared = await self._effect_operation_service.prepare(
                turn_claim_id=safe_context.conversation_turn_claim_id,
                call_id=call_id,
                capability_id=record.canonical_id,
                arguments_hash=arguments_hash,
            )
            if prepared.replay is not None:
                observation = prepared.replay
            else:
                observation = await _bounded_await(
                    self._executor.execute(
                        CapabilityExecutionRequest(
                            capability_id=record.canonical_id,
                            arguments=dict(arguments),
                            idempotency_key=prepared.operation_id,
                        ),
                        safe_context,
                    ),
                    tool_timeout,
                    self._budget_controller,
                )
                await self._effect_operation_service.commit(prepared, observation)
            if (
                self._agent_plan_service is not None
                and safe_context.conversation_id is not None
                and safe_context.current_message_id is not None
            ):
                await self._agent_plan_service.record_capability_outcome(
                    conversation_id=safe_context.conversation_id,
                    owner_user_id=safe_context.user_id,
                    message_id=safe_context.current_message_id,
                    capability_id=record.canonical_id,
                    operation_id=prepared.operation_id,
                    status=observation.status.value,
                    receipt_ref=observation.receipt_ref,
                    workflow_ref=observation.workflow_ref,
                )
        else:
            async with self._read_lock:
                observation = await _bounded_await(
                    self._executor.execute(
                        CapabilityExecutionRequest(
                            capability_id=record.canonical_id,
                            arguments=dict(arguments),
                        ),
                        safe_context,
                    ),
                    tool_timeout,
                    self._budget_controller,
                )
        envelope = project_capability_observation(observation, capability=record)
        self.ledger.append(
            "gateway.executor_completed",
            call_id=call_id,
            semantic_name=record.semantic_name,
            canonical_id=record.canonical_id,
            arguments_hash=arguments_hash,
            policy_class=envelope.error.get("code") if envelope.error else "allowed",
            observation_status=envelope.status,
            plan_required=(
                envelope.data.get("plan_required")
                if record.canonical_id == "nexus.agent_plan.project_objective"
                and isinstance(envelope.data.get("plan_required"), bool)
                else None
            ),
        )
        return envelope


def build_semantic_structured_tools(
    snapshot: SemanticToolSnapshot,
    gateway: SemanticToolGateway,
) -> list[BaseTool]:
    tools: list[BaseTool] = []
    for record in snapshot.tools:
        tools.append(_structured_tool(record, gateway))
    return tools


async def _bounded_await(
    awaitable,  # noqa: ANN001
    timeout_seconds: float | None,
    controller: LayeredBudgetController | None,
):  # noqa: ANN202
    if timeout_seconds is None:
        return await awaitable
    try:
        async with asyncio.timeout(timeout_seconds):
            return await awaitable
    except TimeoutError as exc:
        if controller is not None:
            controller.timeout(BudgetStopReason.TOOL_TIMEOUT)
        raise AgentBudgetStop(BudgetStopReason.TOOL_TIMEOUT) from exc


def _structured_tool(
    record: SemanticToolRecord,
    gateway: SemanticToolGateway,
) -> BaseTool:
    async def submit_to_gateway(**arguments: Any) -> str:
        envelope = await gateway.execute(record.semantic_name, arguments)
        return serialize_tool_envelope(envelope)

    return StructuredTool.from_function(
        coroutine=submit_to_gateway,
        name=record.semantic_name,
        description=record.description,
        args_schema=copy.deepcopy(record.input_schema),
    )


def _safe_jsonschema_error(error: Any) -> dict[str, Any]:
    path = [str(part) for part in error.absolute_path]
    code = str(error.validator or "validation")
    message = "Required or supplied input is invalid."
    if error.validator == "required" and isinstance(error.instance, dict):
        required = error.validator_value
        missing = next(
            (field for field in required if isinstance(field, str) and field not in error.instance),
            None,
        )
        if missing is not None:
            path.append(missing)
            code = "missing"
            message = "Required field is missing."
    return {
        "path": path or ["$"],
        "code": code,
        "message": message,
    }


def _dedupe_validation_errors(
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], str, str]] = set()
    for error in errors:
        path = tuple(str(part) for part in error.get("path", ["$"]))
        code = str(error.get("code", "validation"))
        message = str(error.get("message", "Required or supplied input is invalid."))
        key = (path, code, message)
        if key in seen:
            continue
        seen.add(key)
        result.append({"path": list(path), "code": code, "message": message})
    return result


class SemanticToolMiddleware(AgentMiddleware):
    """Whole-response governance and mandatory correlation for semantic tools."""

    def __init__(
        self,
        snapshot: SemanticToolSnapshot,
        ledger: SemanticEventLedger,
        *,
        required_projection: bool = False,
        required_plan_action: str | None = None,
        project_before_plan_mutation: bool = False,
        require_interaction_assessment: bool = False,
        mode: GatewayMode = GatewayMode.SHADOW,
        budget_controller: LayeredBudgetController | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.ledger = ledger
        self._proposals: dict[str, str] = {}
        self._decisions: dict[str, str] = {}
        self._read_signatures: set[tuple[str, str]] = set()
        self._prevalidation_errors: dict[str, list[dict[str, Any]]] = {}
        self._required_projection = required_projection
        self._required_plan_action = required_plan_action
        self._project_before_plan_mutation = project_before_plan_mutation
        self._deferred_plan_action: str | None = None
        self._require_interaction_assessment = require_interaction_assessment
        self._turn_assessment: TurnAssessment | None = None
        self._clarification_only = False
        self._consequential_action_consumed = False
        self._proposal_corrections_used = 0
        self._assessment_corrections_used = 0
        self._proposal_correction_semantic_name: str | None = None
        self._proposal_correction_canonical_id: str | None = None
        self._prepared_action_candidate: ConversationPreparedAction | None = None
        self._assessed_execution_arguments: dict[str, dict[str, object]] = {}
        self._completed_consequential_semantic_name: str | None = None
        self._grounded_clarification: str | None = None
        self._interaction_grounding_failed = False
        self._mode = mode
        self._budget_controller = budget_controller
        if (
            self._required_projection
            and self.snapshot.tool_for_name(_REQUIRED_PROJECTION_TOOL) is None
        ):
            raise ValueError("required projection tool is absent from the snapshot")

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        answer_only_budget = (
            self._budget_controller is not None
            and self._budget_controller.stop_reason
            in {BudgetStopReason.TOOL_CALL_LIMIT, BudgetStopReason.SOFT_DEADLINE}
        )
        projection_pending = self._required_projection and not any(
            event.canonical_id == "nexus.agent_plan.project_objective"
            and event.kind
            in {
                "middleware.proposed",
                "middleware.intercepted",
                "gateway.executor_started",
                "gateway.executor_completed",
            }
            for event in self.ledger.events
        )
        plan_required = any(event.plan_required is True for event in self.ledger.events)
        plan_changed = any(
            event.kind == "gateway.executor_completed"
            and event.canonical_id
            in {
                "nexus.agent_plan.create_agent_plan",
                "nexus.agent_plan.revise_agent_plan",
            }
            and event.observation_status == "succeeded"
            for event in self.ledger.events
        )
        projection_completed = any(
            event.kind == "gateway.executor_completed"
            and event.canonical_id == "nexus.agent_plan.project_objective"
            and event.observation_status == "succeeded"
            for event in self.ledger.events
        )
        deferred_projection_pending = bool(
            self._project_before_plan_mutation
            and self._deferred_plan_action
            and not projection_completed
        )
        deferred_plan_change_pending = bool(
            self._project_before_plan_mutation
            and self._deferred_plan_action
            and projection_completed
            and plan_required
            and not plan_changed
        )
        if (
            self._project_before_plan_mutation
            and self._deferred_plan_action
            and projection_completed
            and not plan_required
        ):
            self._deferred_plan_action = None
        plan_change_pending = bool(
            plan_required and self._required_plan_action and not plan_changed
        )
        assessment_pending = bool(
            self._require_interaction_assessment and self._turn_assessment is None
        )
        consequential_permitted = bool(
            not self._require_interaction_assessment
            or (
                self._turn_assessment is not None
                and self._turn_assessment.permits_consequential_action
            )
        )
        if self._clarification_only:
            tool_choice = "none"
            self.ledger.append(
                "middleware.clarification_only",
                policy_class="needs_input",
                observation_status="needs_input",
            )
        elif answer_only_budget:
            tool_choice = "none"
            self.ledger.append(
                "middleware.final_synthesis_only",
                policy_class=str(self._budget_controller.stop_reason),
            )
        elif self._consequential_action_consumed:
            tool_choice = "none"
            self.ledger.append(
                "middleware.final_synthesis_only",
                semantic_name=self._completed_consequential_semantic_name,
                policy_class="consequential_action_consumed",
                observation_status=(
                    "succeeded"
                    if self._completed_consequential_semantic_name is not None
                    else "completed"
                ),
            )
        elif assessment_pending:
            tool_choice = TURN_ASSESSMENT_TOOL_NAME
            self.ledger.append(
                "middleware.tool_choice_required",
                semantic_name=TURN_ASSESSMENT_TOOL_NAME,
                policy_class="conversation_stage_assessment",
            )
        elif consequential_permitted and (projection_pending or deferred_projection_pending):
            tool_choice = _REQUIRED_PROJECTION_TOOL
            self.ledger.append(
                "middleware.tool_choice_required",
                semantic_name=_REQUIRED_PROJECTION_TOOL,
                canonical_id="nexus.agent_plan.project_objective",
                policy_class="mandatory_objective_projection",
            )
        elif consequential_permitted and (plan_change_pending or deferred_plan_change_pending):
            tool_choice = self._required_plan_action or self._deferred_plan_action
            self.ledger.append(
                "middleware.tool_choice_required",
                semantic_name=tool_choice,
                canonical_id=f"nexus.agent_plan.{tool_choice}",
                policy_class="mandatory_durable_plan_change",
            )
        elif (
            self._require_interaction_assessment
            and self._turn_assessment is not None
            and not consequential_permitted
        ):
            tool_choice = "none"
        elif not request.tools:
            tool_choice = "none"
        else:
            tool_choice = "auto"
        return await handler(request.override(tool_choice=tool_choice))

    async def aafter_model(self, state, runtime):  # noqa: ANN001, ANN201
        del runtime
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        calls = list(messages[-1].tool_calls)
        if not calls and self._proposal_corrections_used and self._turn_assessment is None:
            self._clarification_only = True
            self.ledger.append(
                "middleware.needs_input",
                semantic_name=self._proposal_correction_semantic_name,
                canonical_id=self._proposal_correction_canonical_id,
                policy_class="proposal_correction_skipped",
                observation_status="needs_input",
            )
            return None
        if not calls and self._assessment_corrections_used and self._turn_assessment is None:
            self._clarification_only = True
            self._interaction_grounding_failed = True
            self.ledger.append(
                "middleware.interaction_grounding_failed",
                semantic_name=TURN_ASSESSMENT_TOOL_NAME,
                policy_class="assessment_correction_skipped",
                observation_status="failed",
            )
            return None

        invalid_call_ids: set[str] = set()
        for call in calls:
            call_id = str(call.get("id") or "")
            name = str(call.get("name") or "")
            record = self.snapshot.tool_for_name(name)
            if record is None or not CALL_ID_RE.fullmatch(call_id):
                continue
            arguments = dict(call.get("args") or {})
            assessed_action = (
                self._turn_assessment.proposed_action
                if (
                    self._turn_assessment is not None
                    and self._turn_assessment.permits_consequential_action
                )
                else None
            )
            if (
                record.governance_class == GovernanceClass.CONSEQUENTIAL
                and assessed_action is not None
                and assessed_action.tool_name == name
            ):
                assessed_arguments = dict(assessed_action.arguments)
                if assessed_arguments != arguments:
                    self._assessed_execution_arguments[call_id] = assessed_arguments
                arguments = assessed_arguments
            errors = sorted(
                Draft202012Validator(record.input_schema).iter_errors(arguments),
                key=lambda item: tuple(str(part) for part in item.absolute_path),
            )
            if not errors:
                continue
            invalid_call_ids.add(call_id)
            self._prevalidation_errors[call_id] = [
                _safe_jsonschema_error(error) for error in errors[:8]
            ]
        consequential = [
            call
            for call in calls
            if (
                (record := self.snapshot.tool_for_name(str(call.get("name") or ""))) is not None
                and record.governance_class == GovernanceClass.CONSEQUENTIAL
            )
        ]
        reject_all = len(consequential) > 1
        read_calls = [
            call
            for call in calls
            if (
                (record := self.snapshot.tool_for_name(str(call.get("name") or ""))) is not None
                and record.governance_class == GovernanceClass.READ
            )
        ]
        reject_read_fanout = len(read_calls) > MAX_PARALLEL_READ_CALLS
        for call in calls:
            call_id = str(call.get("id") or "")
            name = str(call.get("name") or "")
            if not CALL_ID_RE.fullmatch(call_id):
                continue
            self._proposals[call_id] = name
            record = self.snapshot.tool_for_name(name)
            if name == TURN_ASSESSMENT_TOOL_NAME:
                decision = "interaction_assessment"
            elif self._clarification_only:
                decision = "clarification_only"
            elif call_id in invalid_call_ids:
                decision = "needs_input"
            elif invalid_call_ids:
                decision = "call_set_rejected"
            elif reject_all:
                decision = "call_set_rejected"
            elif (
                reject_read_fanout
                and record is not None
                and record.governance_class == GovernanceClass.READ
            ):
                decision = "read_fanout_rejected"
            elif record is None:
                decision = (
                    "unavailable"
                    if self.snapshot.unavailable_for_name(name) is not None
                    else "unknown_tool"
                )
            elif record.governance_class == GovernanceClass.CONSEQUENTIAL:
                if self._consequential_action_consumed:
                    decision = "conversation_action_consumed"
                elif self._require_interaction_assessment and not (
                    self._turn_assessment is not None
                    and self._turn_assessment.permits_consequential_action
                    and self._turn_assessment.proposed_action is not None
                    and self._turn_assessment.proposed_action.tool_name == name
                ):
                    decision = "conversation_action_not_ready"
                elif (
                    self._project_before_plan_mutation
                    and record.canonical_id
                    in {
                        "nexus.agent_plan.create_agent_plan",
                        "nexus.agent_plan.revise_agent_plan",
                    }
                    and not any(
                        event.kind == "gateway.executor_completed"
                        and event.canonical_id == "nexus.agent_plan.project_objective"
                        and event.observation_status == "succeeded"
                        for event in self.ledger.events
                    )
                ):
                    self._deferred_plan_action = name
                    decision = "projection_required"
                elif self._mode == GatewayMode.EFFECT_PRIMARY:
                    decision = "effect"
                elif (
                    self._mode == GatewayMode.PLAN_CONTROL_PRIMARY
                    and record.canonical_id.startswith("nexus.agent_plan.")
                ):
                    decision = "plan_control"
                else:
                    decision = "proposal_only"
            else:
                decision = "read"
            self._decisions[call_id] = decision
            self.ledger.append(
                "middleware.proposed",
                call_id=call_id,
                semantic_name=name,
                canonical_id=record.canonical_id if record else None,
                arguments_hash=_hash_json(dict(call.get("args") or {})),
                policy_class=decision,
            )
        return None

    async def awrap_tool_call(self, request, handler):  # noqa: ANN001, ANN201
        call_id = str(request.tool_call.get("id") or "")
        name = str(request.tool_call.get("name") or "")
        if call_id not in self._proposals:
            self._restore_proposal(request.state.get("messages", []), call_id, name)
        if call_id not in self._proposals:
            return _tool_message(
                call_id,
                _error_envelope(
                    "denied",
                    "middleware_proposal_missing",
                    "The tool proposal was not authorized by semantic middleware.",
                ),
            )
        decision = self._decisions.get(call_id, "middleware_proposal_missing")
        assessed_arguments = self._assessed_execution_arguments.get(call_id)
        if assessed_arguments is not None and decision in {
            "effect",
            "plan_control",
            "proposal_only",
        }:
            original_hash = _hash_json(dict(request.tool_call.get("args") or {}))
            execution_hash = _hash_json(assessed_arguments)
            tool_call = {**request.tool_call, "args": assessed_arguments}
            if hasattr(request, "override"):
                request = request.override(tool_call=tool_call)
            else:
                request = copy.copy(request)
                request.tool_call = tool_call
            self.ledger.append(
                "middleware.assessment_arguments_applied",
                call_id=call_id,
                semantic_name=name,
                canonical_id=self.snapshot.semantic_to_canonical.get(name),
                arguments_hash=execution_hash,
                policy_class=(
                    "prevalidated_assessment_arguments"
                    if execution_hash != original_hash
                    else "assessment_arguments_confirmed"
                ),
                observation_status="succeeded",
            )
        self.ledger.append(
            "middleware.intercepted",
            call_id=call_id,
            semantic_name=name,
            canonical_id=self.snapshot.semantic_to_canonical.get(name),
            arguments_hash=_hash_json(dict(request.tool_call.get("args") or {})),
            policy_class=decision,
        )
        if decision == "interaction_assessment":
            try:
                assessment = TurnAssessment.model_validate(
                    dict(request.tool_call.get("args") or {})
                )
            except ValidationError as exc:
                diagnostics = safe_validation_errors(exc)
                if self._assessment_corrections_used < MAX_PROPOSAL_CORRECTIONS:
                    self._assessment_corrections_used += 1
                    self.ledger.append(
                        "middleware.assessment_correction_required",
                        call_id=call_id,
                        semantic_name=TURN_ASSESSMENT_TOOL_NAME,
                        policy_class="assessment_validation",
                        observation_status="correction_required",
                    )
                    return _tool_message(
                        call_id,
                        _error_envelope(
                            "correction_required",
                            "invalid_turn_assessment",
                            "The conversation-stage assessment has an invalid field "
                            "combination. Return exactly one valid combination: act requires "
                            "action_ready=true, missing_information=[], one exact "
                            "proposed_action, and no clarification_to_user; prepare requires "
                            "action_ready=false, at least one missing_information item, one "
                            "direct clarification_to_user, and may optionally retain one "
                            "grounded partial proposed_action; "
                            "discuss or explain requires action_ready=false and no "
                            "proposed_action or clarification_to_user.",
                            data={"validation_errors": diagnostics},
                            retryable=True,
                        ),
                    )
                self._clarification_only = True
                self._interaction_grounding_failed = True
                self.ledger.append(
                    "middleware.interaction_grounding_failed",
                    call_id=call_id,
                    semantic_name=TURN_ASSESSMENT_TOOL_NAME,
                    policy_class="assessment_validation",
                    observation_status="failed",
                )
                return _tool_message(
                    call_id,
                    _error_envelope(
                        "failed",
                        "invalid_turn_assessment",
                        "The conversation stage could not be safely resolved after one "
                        "bounded correction.",
                        data={"validation_errors": diagnostics},
                    ),
                )
            proposed = assessment.proposed_action
            if proposed is not None:
                record = self.snapshot.tool_for_name(proposed.tool_name)
                errors = (
                    ["unknown"]
                    if record is None
                    else list(
                        Draft202012Validator(record.input_schema).iter_errors(proposed.arguments)
                    )
                )
                diagnostics = (
                    [_safe_jsonschema_error(error) for error in errors[:8]]
                    if record is not None
                    else [
                        {
                            "path": ["proposed_action", "tool_name"],
                            "code": "unknown_tool",
                            "message": "The proposed action is unavailable.",
                        }
                    ]
                )
                diagnostics = _dedupe_validation_errors(diagnostics)
                if record is not None and record.preparable_across_turns:
                    self._prepared_action_candidate = ConversationPreparedAction(
                        capability_id=record.canonical_id,
                        semantic_name=record.semantic_name,
                        objective=assessment.objective,
                        arguments=proposed.arguments,
                        missing_user_information=assessment.missing_information,
                        validation_errors=[
                            PreparedActionValidationError.model_validate(item)
                            for item in diagnostics
                        ],
                    )
                if errors and assessment.permits_consequential_action:
                    self._turn_assessment = None
                    if self._proposal_corrections_used < MAX_PROPOSAL_CORRECTIONS:
                        self._proposal_corrections_used += 1
                        self._proposal_correction_semantic_name = proposed.tool_name
                        self._proposal_correction_canonical_id = (
                            record.canonical_id if record is not None else None
                        )
                        self.ledger.append(
                            "middleware.proposal_correction_required",
                            call_id=call_id,
                            semantic_name=proposed.tool_name,
                            policy_class="assessment_preflight",
                            observation_status="correction_required",
                        )
                        return _tool_message(
                            call_id,
                            _error_envelope(
                                "correction_required",
                                "validation",
                                "The unexecuted proposal is structurally invalid. "
                                "Correct it once without asking for internal fields.",
                                data={"validation_errors": diagnostics},
                                retryable=True,
                            ),
                        )
                    self._clarification_only = True
                    self.ledger.append(
                        "middleware.needs_input",
                        call_id=call_id,
                        semantic_name=proposed.tool_name,
                        policy_class="assessment_validation",
                        observation_status="needs_input",
                    )
                    return _tool_message(
                        call_id,
                        _error_envelope(
                            "needs_input",
                            "validation",
                            "The proposed action needs missing or corrected information.",
                            data={"validation_errors": diagnostics},
                            retryable=True,
                        ),
                    )
            clarification_error = _clarification_grounding_error(
                assessment,
                request.state.get("messages", []),
            )
            if clarification_error is not None:
                if self._assessment_corrections_used < MAX_PROPOSAL_CORRECTIONS:
                    self._assessment_corrections_used += 1
                    self.ledger.append(
                        "middleware.assessment_correction_required",
                        call_id=call_id,
                        semantic_name=TURN_ASSESSMENT_TOOL_NAME,
                        policy_class="clarification_grounding",
                        observation_status="correction_required",
                    )
                    return _tool_message(
                        call_id,
                        _error_envelope(
                            "correction_required",
                            "invalid_clarification",
                            "The clarification is not a grounded question to the user. "
                            "Submit the assessment again with one concise question about "
                            "only the declared missing business information, without "
                            "copying any user message.",
                            data={"validation_errors": [clarification_error]},
                            retryable=True,
                        ),
                    )
                self._clarification_only = True
                self._interaction_grounding_failed = True
                self._turn_assessment = assessment
                self.ledger.append(
                    "middleware.interaction_grounding_failed",
                    call_id=call_id,
                    semantic_name=TURN_ASSESSMENT_TOOL_NAME,
                    policy_class="clarification_grounding",
                    observation_status="failed",
                )
                return await handler(request)
            self._turn_assessment = assessment
            self._grounded_clarification = assessment.clarification_to_user
            self.ledger.append(
                "middleware.interaction_assessed",
                call_id=call_id,
                semantic_name=TURN_ASSESSMENT_TOOL_NAME,
                policy_class=assessment.mode.value,
                observation_status="succeeded",
            )
            return await handler(request)
        if decision == "clarification_only":
            return _tool_message(
                call_id,
                _error_envelope(
                    "call_set_rejected",
                    "clarification_only",
                    "No tool may run after needs_input in this user turn.",
                ),
            )
        if decision == "needs_input":
            self._clarification_only = True
            diagnostics = self._prevalidation_errors.get(call_id, [])
            self.ledger.append(
                "middleware.needs_input",
                call_id=call_id,
                semantic_name=name,
                canonical_id=self.snapshot.semantic_to_canonical.get(name),
                arguments_hash=_hash_json(dict(request.tool_call.get("args") or {})),
                policy_class="validation",
                observation_status="needs_input",
            )
            return _tool_message(
                call_id,
                _error_envelope(
                    "needs_input",
                    "validation",
                    "The tool needs missing or corrected information before it can run.",
                    data={
                        "argument_shape": safe_argument_shape(
                            dict(request.tool_call.get("args") or {})
                        ),
                        "validation_errors": diagnostics,
                    },
                    retryable=True,
                ),
            )
        if decision == "conversation_action_not_ready":
            return _tool_message(
                call_id,
                _error_envelope(
                    "call_set_rejected",
                    "conversation_action_not_ready",
                    "The conversation is not ready for a consequential action.",
                ),
            )
        if decision == "conversation_action_consumed":
            return _tool_message(
                call_id,
                _error_envelope(
                    "call_set_rejected",
                    "conversation_action_consumed",
                    "The assessed consequential action was already used this turn.",
                ),
            )
        if decision == "call_set_rejected":
            return _tool_message(
                call_id,
                _error_envelope(
                    "call_set_rejected",
                    "call_set_rejected",
                    "Multiple consequential calls were rejected; replan one at a time.",
                ),
            )
        if decision == "read_fanout_rejected":
            return _tool_message(
                call_id,
                _error_envelope(
                    "call_set_rejected",
                    "read_fanout_rejected",
                    "Too many read calls were proposed at once. Use the evidence already "
                    "returned, or request at most three bounded reads before answering.",
                ),
            )
        if decision == "projection_required":
            return _tool_message(
                call_id,
                _error_envelope(
                    "denied",
                    "objective_projection_required",
                    "Project the objective topology before changing durable plan state.",
                ),
            )
        if decision == "unknown_tool":
            return _tool_message(
                call_id,
                _error_envelope("unknown_tool", "unknown_tool", "The requested tool is unknown."),
            )
        if decision == "unavailable":
            return _tool_message(
                call_id,
                _error_envelope(
                    "unavailable", "unavailable", "The requested capability is unavailable."
                ),
            )
        arguments_hash = _hash_json(dict(request.tool_call.get("args") or {}))
        if decision in {"effect", "plan_control", "proposal_only"}:
            self._consequential_action_consumed = True
        read_signature = (name, arguments_hash)
        if decision == "read" and read_signature in self._read_signatures:
            self.ledger.append(
                "middleware.repeated_read_rejected",
                call_id=call_id,
                semantic_name=name,
                canonical_id=self.snapshot.semantic_to_canonical.get(name),
                arguments_hash=arguments_hash,
                policy_class="repeated_call",
                observation_status="call_set_rejected",
            )
            return _tool_message(
                call_id,
                _error_envelope(
                    "call_set_rejected",
                    "repeated_call",
                    "This identical read already ran. Use the evidence already returned "
                    "instead of calling it again.",
                ),
            )
        if decision == "read":
            self._read_signatures.add(read_signature)
        token = _CURRENT_CALL_ID.set(call_id)
        try:
            try:
                result = await handler(request)
                try:
                    envelope = ToolEnvelope.model_validate_json(str(result.content))
                except (AttributeError, ValueError):
                    if decision == "read":
                        self._read_signatures.discard(read_signature)
                else:
                    if decision == "read" and envelope.status not in {
                        "succeeded",
                        "triggered",
                    }:
                        self._read_signatures.discard(read_signature)
                    if envelope.status == "needs_input":
                        self._clarification_only = True
                    if decision in {
                        "effect",
                        "plan_control",
                        "proposal_only",
                    } and envelope.status in {"succeeded", "triggered", "approval_required"}:
                        self._completed_consequential_semantic_name = name
                return result
            except ValidationError as exc:
                self._read_signatures.discard(read_signature)
                self._clarification_only = True
                diagnostics = safe_validation_errors(exc)
                self.ledger.append(
                    "middleware.needs_input",
                    call_id=call_id,
                    semantic_name=name,
                    canonical_id=self.snapshot.semantic_to_canonical.get(name),
                    arguments_hash=_hash_json(dict(request.tool_call.get("args") or {})),
                    policy_class="validation",
                    observation_status="needs_input",
                )
                return _tool_message(
                    call_id,
                    _error_envelope(
                        "needs_input",
                        "validation",
                        "The tool needs missing or corrected information before it can run.",
                        data={
                            "argument_shape": safe_argument_shape(
                                dict(request.tool_call.get("args") or {})
                            ),
                            "validation_errors": diagnostics,
                        },
                        retryable=True,
                    ),
                )
            except Exception:
                self._read_signatures.discard(read_signature)
                raise
        finally:
            _CURRENT_CALL_ID.reset(token)

    @property
    def prepared_action_candidate(self) -> ConversationPreparedAction | None:
        return self._prepared_action_candidate

    @property
    def completed_consequential_semantic_name(self) -> str | None:
        return self._completed_consequential_semantic_name

    @property
    def grounded_clarification(self) -> str | None:
        return self._grounded_clarification

    @property
    def interaction_grounding_failed(self) -> bool:
        return self._interaction_grounding_failed

    def _restore_proposal(self, messages: list[Any], call_id: str, name: str) -> None:
        if not CALL_ID_RE.fullmatch(call_id):
            return
        for message in reversed(messages):
            if not isinstance(message, AIMessage):
                continue
            call = next(
                (item for item in message.tool_calls if str(item.get("id") or "") == call_id),
                None,
            )
            if call is None:
                continue
            record = self.snapshot.tool_for_name(name)
            if record and record.governance_class == GovernanceClass.CONSEQUENTIAL:
                # A resumed consequential call must be observed and authorized again
                # by aafter_model; reconstructing it from message history would bypass
                # the current turn assessment and prevalidation boundaries.
                return
            self._proposals[call_id] = name
            self._decisions[call_id] = (
                (
                    "effect"
                    if self._mode == GatewayMode.EFFECT_PRIMARY
                    else "plan_control"
                    if self._mode == GatewayMode.PLAN_CONTROL_PRIMARY
                    and record.canonical_id.startswith("nexus.agent_plan.")
                    else "proposal_only"
                )
                if record and record.governance_class == GovernanceClass.CONSEQUENTIAL
                else "read"
                if record
                else "unavailable"
                if self.snapshot.unavailable_for_name(name)
                else "unknown_tool"
            )
            self.ledger.append(
                "middleware.proposal_restored",
                call_id=call_id,
                semantic_name=name,
                canonical_id=record.canonical_id if record else None,
                arguments_hash=_hash_json(dict(call.get("args") or {})),
                policy_class=self._decisions[call_id],
            )
            return


def project_capability_observation(
    observation: CapabilityObservation,
    *,
    capability: SemanticToolRecord,
) -> ToolEnvelope:
    status = _project_status(observation)
    recovery = _safe_recovery(status)
    error = (
        {"code": _safe_error_code(observation.error_code), "recovery": recovery}
        if status not in {"succeeded", "triggered"}
        else None
    )
    data = _bounded_data(observation.data)
    field_path = observation.safe_details.get("field_path")
    if status == "needs_input" and isinstance(field_path, str):
        data["validation_errors"] = [
            {
                "path": field_path.split(".") if field_path != "$" else ["$"],
                "code": "validation",
                "message": "Required or supplied input is invalid.",
            }
        ]
    return ToolEnvelope(
        status=status,
        summary=_safe_string(
            observation.summary
            or (
                "The capability completed."
                if status in {"succeeded", "triggered"}
                else "The capability did not complete."
            )
        ),
        data=data,
        evidence_refs=[
            reference
            for item in observation.evidence_refs[:MAX_EVIDENCE_REFS]
            if isinstance(item, dict) and (reference := _bounded_reference(item)) is not None
        ],
        retryable=observation.retryable,
        error=error,
        workflow_ref=_bounded_reference(observation.workflow_ref),
        receipt_ref=_bounded_reference(observation.receipt_ref),
        evidence_state_relevant=capability.evidence_state_relevant,
        execution_state_relevant=capability.execution_state_relevant,
    )


def serialize_tool_envelope(envelope: ToolEnvelope) -> str:
    payload = envelope.model_dump(mode="json", exclude_none=True)
    encoded = _canonical_json(payload)
    if len(encoded.encode("utf-8")) <= MAX_TOOL_MESSAGE_BYTES:
        return encoded
    payload["data"] = {"truncated": True, "reason": "model_context_limit"}
    payload["evidence_refs"] = []
    payload.pop("workflow_ref", None)
    payload.pop("receipt_ref", None)
    encoded = _canonical_json(payload)
    if len(encoded.encode("utf-8")) <= MAX_TOOL_MESSAGE_BYTES:
        return encoded
    minimal = ToolEnvelope(
        status=envelope.status,
        summary=_safe_string(envelope.summary, limit=160),
        data={"truncated": True, "reason": "model_context_limit"},
        retryable=envelope.retryable,
        error=envelope.error,
        evidence_state_relevant=envelope.evidence_state_relevant,
        execution_state_relevant=envelope.execution_state_relevant,
    )
    return _canonical_json(minimal.model_dump(mode="json", exclude_none=True))


def _project_status(observation: CapabilityObservation) -> str:
    code = _safe_error_code(observation.error_code)
    if observation.status.value == "needs_input" or code == "validation":
        return "needs_input"
    if code == "approval":
        return "approval_required"
    if code in {"unknown", "unavailable"}:
        return "unavailable"
    if observation.status.value in {"succeeded", "triggered"}:
        return observation.status.value
    return "denied" if observation.status.value == "denied" else "failed"


def _safe_error_code(value: str | None) -> str:
    allowed = {
        "unknown",
        "unavailable",
        "validation",
        "permission",
        "scope",
        "allowlist",
        "approval",
        "turn_claim",
        "tool_budget",
        "cost_budget",
        "handler_failure",
        "capability_handler_unavailable",
    }
    return value if value in allowed else "handler_failure"


def _safe_recovery(status: str) -> str:
    recovery = {
        "needs_input": (
            "Ask for the missing or corrected information; do not call another tool "
            "in this user turn."
        ),
        "correction_required": (
            "Correct the unexecuted proposal once using known facts and the smallest "
            "schema-valid argument object; do not ask for internal fields."
        ),
        "validation_failed": "Ask for corrected input before a later-turn retry.",
        "approval_required": "Wait for an authorized approval decision.",
        "unavailable": "Use an available capability or explain the limitation.",
        "denied": "Respect the authority boundary and offer a permitted alternative.",
        "failed": "Retry only when retryable; otherwise report the safe failure.",
    }
    return recovery.get(status, "Continue from the typed observation.")


def _bounded_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized = _sanitize_value(value, depth=0)
    if not isinstance(sanitized, dict):
        return {"truncated": True, "reason": "invalid_data_shape"}
    if len(_canonical_json(sanitized).encode("utf-8")) <= MAX_TOOL_DATA_BYTES:
        return sanitized

    # Evidence reads commonly return a list of useful, individually bounded
    # passages. Preserve as many complete leading passages as fit instead of
    # replacing the entire observation with a context-limit marker. Retrieval
    # order remains authoritative and the model can still see that the tail was
    # omitted.
    items = sanitized.get("items")
    if isinstance(items, list):
        bounded = {key: item for key, item in sanitized.items() if key != "items"}
        bounded_items: list[Any] = []
        bounded["items"] = bounded_items
        bounded["truncated"] = True
        bounded["reason"] = "model_context_limit"
        for item in items:
            bounded_items.append(item)
            if len(_canonical_json(bounded).encode("utf-8")) > MAX_TOOL_DATA_BYTES:
                bounded_items.pop()
                break
        if bounded_items:
            bounded["returned_item_count"] = len(bounded_items)
            bounded["total_item_count"] = len(items)
            return bounded
    return {"truncated": True, "reason": "model_context_limit"}


def _sanitize_value(value: Any, *, depth: int, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _PRIVATE_KEYS:
        return "[REDACTED]"
    if depth >= MAX_CONTAINER_DEPTH:
        return {"truncated": True, "reason": "depth_limit"}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        limit = 8_000 if key == "answer_draft" else 2_000 if key == "summary" else MAX_STRING_CHARS
        return _safe_string(value, limit=limit)
    if isinstance(value, dict):
        return {
            str(item_key)[:80]: _sanitize_value(item_value, depth=depth + 1, key=str(item_key))
            for item_key, item_value in list(value.items())[:MAX_CONTAINER_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_value(item, depth=depth + 1) for item in list(value)[:MAX_CONTAINER_ITEMS]
        ]
    return _safe_string(str(value))


def _bounded_reference(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        str(key): _sanitize_value(item, depth=1, key=str(key))
        for key, item in value.items()
        if str(key) in _REFERENCE_KEYS
    }
    return result or None


def _safe_string(value: Any, *, limit: int = MAX_STRING_CHARS) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", text
        )
    return text[:limit]


def _error_envelope(
    status: str,
    code: str,
    summary: str,
    *,
    data: dict[str, Any] | None = None,
    retryable: bool = False,
) -> ToolEnvelope:
    return ToolEnvelope(
        status=status,
        summary=summary,
        data=data or {},
        retryable=retryable,
        error={"code": code, "recovery": _safe_recovery(status)},
    )


def _tool_message(call_id: str, envelope: ToolEnvelope) -> ToolMessage:
    return ToolMessage(
        content=serialize_tool_envelope(envelope),
        tool_call_id=call_id or "missing-call-id",
        status="error" if envelope.status not in {"succeeded", "triggered"} else "success",
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "GatewayMode",
    "GovernanceClass",
    "SemanticEventLedger",
    "SemanticSnapshotError",
    "SemanticToolGateway",
    "SemanticToolMiddleware",
    "SemanticToolSnapshot",
    "ToolEnvelope",
    "build_semantic_structured_tools",
    "build_semantic_tool_snapshot",
    "project_capability_observation",
    "serialize_tool_envelope",
]
