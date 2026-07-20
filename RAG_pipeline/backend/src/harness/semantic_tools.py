"""Disabled shadow-only semantic tools over the authoritative Nexus registry."""

from __future__ import annotations

import asyncio
import contextvars
import copy
import hashlib
import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from jsonschema import Draft202012Validator
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from src.harness.capability_runtime import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityObservation,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.manifest import (
    ManifestScope,
    build_runtime_capability_manifest,
    runtime_visibility_reason,
)
from src.harness.models import CapabilityDefinition, HarnessRiskTier
from src.harness.registry import CapabilityRegistry


SEMANTIC_CONTRACT_VERSION = "1.0.0"
SEMANTIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CALL_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
MAX_TOOL_MESSAGE_BYTES = 8_192
MAX_CONTAINER_DEPTH = 5
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


class SemanticSnapshotError(ValueError):
    """The registry cannot be safely exposed as a semantic tool snapshot."""


class GovernanceClass(StrEnum):
    READ = "read"
    CONSEQUENTIAL = "consequential"


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

    def unavailable_for_name(
        self, semantic_name: str
    ) -> UnavailableCapabilityRecord | None:
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
                raise SemanticSnapshotError(
                    f"semantic name drift for {capability.id}"
                )
        other = semantic_to_canonical.get(semantic_name)
        if other is not None and other != capability.id:
            raise SemanticSnapshotError(f"semantic name collision: {semantic_name}")
        if capability.id in canonical_to_semantic:
            raise SemanticSnapshotError(f"duplicate canonical id: {capability.id}")
        semantic_to_canonical[semantic_name] = capability.id
        canonical_to_semantic[capability.id] = semantic_name
        records.append(
            SemanticToolRecord(
                semantic_name=semantic_name,
                canonical_id=capability.id,
                description=_semantic_description(capability),
                input_schema=copy.deepcopy(capability.input_schema),
                output_type=capability.output_type,
                risk_tier=capability.risk_tier.value,
                governance_class=_governance_class(capability),
                requires_approval=bool(
                    capability.metadata.get("requires_approval")
                    or capability.metadata.get("requires_explicit_approval")
                ),
                shadow_safe=capability.shadow_safe,
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


class SemanticToolGateway:
    """Mandatory shadow gateway; generated tools have no other execution path."""

    def __init__(
        self,
        *,
        snapshot: SemanticToolSnapshot,
        executor: HarnessCapabilityExecutor,
        context: CapabilityExecutionContext,
        ledger: SemanticEventLedger | None = None,
    ) -> None:
        self.snapshot = snapshot
        self._executor = executor
        self._context = context
        self.ledger = ledger or SemanticEventLedger()
        self._read_lock = asyncio.Lock()

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
            return _error_envelope(
                "unknown_tool", "unknown_tool", "The requested tool is unknown."
            )
        arguments_hash = _hash_json(dict(arguments))
        if record.governance_class == GovernanceClass.CONSEQUENTIAL or not record.shadow_safe:
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
            )
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
        self.ledger.append(
            "gateway.executor_started",
            call_id=call_id,
            semantic_name=record.semantic_name,
            canonical_id=record.canonical_id,
            arguments_hash=arguments_hash,
            policy_class="shadow_safe_read",
        )
        async with self._read_lock:
            observation = await self._executor.execute(
                CapabilityExecutionRequest(
                    capability_id=record.canonical_id,
                    arguments=dict(arguments),
                ),
                safe_context,
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


class SemanticToolMiddleware(AgentMiddleware):
    """Whole-response governance and mandatory correlation for semantic tools."""

    def __init__(
        self,
        snapshot: SemanticToolSnapshot,
        ledger: SemanticEventLedger,
    ) -> None:
        self.snapshot = snapshot
        self.ledger = ledger
        self._proposals: dict[str, str] = {}
        self._decisions: dict[str, str] = {}

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        return await handler(request.override(tool_choice="auto" if request.tools else "none"))

    async def aafter_model(self, state, runtime):  # noqa: ANN001, ANN201
        del runtime
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        calls = list(messages[-1].tool_calls)
        consequential = [
            call
            for call in calls
            if (
                (record := self.snapshot.tool_for_name(str(call.get("name") or "")))
                is not None
                and record.governance_class == GovernanceClass.CONSEQUENTIAL
            )
        ]
        reject_all = len(consequential) > 1
        for call in calls:
            call_id = str(call.get("id") or "")
            name = str(call.get("name") or "")
            if not CALL_ID_RE.fullmatch(call_id):
                continue
            self._proposals[call_id] = name
            record = self.snapshot.tool_for_name(name)
            if reject_all:
                decision = "call_set_rejected"
            elif record is None:
                decision = (
                    "unavailable"
                    if self.snapshot.unavailable_for_name(name) is not None
                    else "unknown_tool"
                )
            elif record.governance_class == GovernanceClass.CONSEQUENTIAL:
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
        self.ledger.append(
            "middleware.intercepted",
            call_id=call_id,
            semantic_name=name,
            canonical_id=self.snapshot.semantic_to_canonical.get(name),
            arguments_hash=_hash_json(dict(request.tool_call.get("args") or {})),
            policy_class=decision,
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
        if decision == "unknown_tool":
            return _tool_message(
                call_id,
                _error_envelope(
                    "unknown_tool", "unknown_tool", "The requested tool is unknown."
                ),
            )
        if decision == "unavailable":
            return _tool_message(
                call_id,
                _error_envelope(
                    "unavailable", "unavailable", "The requested capability is unavailable."
                ),
            )
        token = _CURRENT_CALL_ID.set(call_id)
        try:
            return await handler(request)
        finally:
            _CURRENT_CALL_ID.reset(token)

    def _restore_proposal(self, messages: list[Any], call_id: str, name: str) -> None:
        if not CALL_ID_RE.fullmatch(call_id):
            return
        for message in reversed(messages):
            if not isinstance(message, AIMessage):
                continue
            call = next(
                (
                    item
                    for item in message.tool_calls
                    if str(item.get("id") or "") == call_id
                ),
                None,
            )
            if call is None:
                continue
            self._proposals[call_id] = name
            record = self.snapshot.tool_for_name(name)
            self._decisions[call_id] = (
                "proposal_only"
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
        data=_bounded_data(observation.data),
        evidence_refs=[
            reference
            for item in observation.evidence_refs[:MAX_EVIDENCE_REFS]
            if isinstance(item, dict)
            and (reference := _bounded_reference(item)) is not None
        ],
        retryable=observation.retryable,
        error=error,
        workflow_ref=_bounded_reference(observation.workflow_ref),
        receipt_ref=_bounded_reference(observation.receipt_ref),
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
    )
    return _canonical_json(minimal.model_dump(mode="json", exclude_none=True))


def _project_status(observation: CapabilityObservation) -> str:
    code = _safe_error_code(observation.error_code)
    if code == "validation":
        return "validation_failed"
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
    }
    return value if value in allowed else "handler_failure"


def _safe_recovery(status: str) -> str:
    recovery = {
        "validation_failed": "Correct the invalid field path and retry once.",
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
    if len(_canonical_json(sanitized).encode("utf-8")) > MAX_TOOL_MESSAGE_BYTES // 2:
        return {"truncated": True, "reason": "model_context_limit"}
    return sanitized


def _sanitize_value(value: Any, *, depth: int, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _PRIVATE_KEYS:
        return "[REDACTED]"
    if depth >= MAX_CONTAINER_DEPTH:
        return {"truncated": True, "reason": "depth_limit"}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, dict):
        return {
            str(item_key)[:80]: _sanitize_value(
                item_value, depth=depth + 1, key=str(item_key)
            )
            for item_key, item_value in list(value.items())[:MAX_CONTAINER_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_value(item, depth=depth + 1)
            for item in list(value)[:MAX_CONTAINER_ITEMS]
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
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex else "[REDACTED]", text)
    return text[:limit]


def _error_envelope(status: str, code: str, summary: str) -> ToolEnvelope:
    return ToolEnvelope(
        status=status,
        summary=summary,
        retryable=False,
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
