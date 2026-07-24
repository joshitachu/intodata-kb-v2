"""Safe, operator-facing Agent Run trace projections.

Trace data intentionally stores structure and deterministic reason codes, never
prompt bodies, argument values, tool results, provider payloads, or reasoning.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import HarnessAgentPublicEventRow, HarnessAgentRunRow, HarnessAgentStepRow

_PRIVATE_NAMES = frozenset({
    "authorization", "password", "secret", "token", "access_token",
    "refresh_token", "api_key", "prompt", "messages", "reasoning",
    "thinking", "chain_of_thought", "content", "query", "text",
})


def safe_argument_shape(value: Any, *, depth: int = 0) -> Any:
    """Describe JSON structure without retaining any values."""
    if depth >= 4:
        return "nested"
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: (
                "redacted" if str(key).casefold() in _PRIVATE_NAMES
                else safe_argument_shape(item, depth=depth + 1)
            )
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        samples = [safe_argument_shape(item, depth=depth + 1) for item in list(value)[:3]]
        return {"type": "array", "length": len(value), "item_shapes": samples}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string" if isinstance(value, str) else type(value).__name__[:40]


def safe_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Return stable schema diagnostics with inputs and values removed."""
    diagnostics: list[dict[str, Any]] = []
    for item in exc.errors(include_input=False, include_url=False)[:20]:
        location = [str(part)[:80] for part in item.get("loc", ())[:8]]
        diagnostic: dict[str, Any] = {
            "path": location,
            "code": str(item.get("type") or "validation_error")[:80],
            "message": _validation_message(str(item.get("type") or "")),
        }
        context = item.get("ctx")
        if isinstance(context, Mapping):
            allowed = {
                str(key)[:80]: str(value)[:120]
                for key, value in context.items()
                if str(key) in {"expected", "class_name", "field_type"}
            }
            if allowed:
                diagnostic["context"] = allowed
        diagnostics.append(diagnostic)
    return diagnostics


def _validation_message(code: str) -> str:
    if code == "missing":
        return "Required field is missing."
    if code == "extra_forbidden":
        return "Unexpected field is not allowed."
    if code.startswith("string_"):
        return "Value does not satisfy the string schema."
    if code.startswith(("int_", "float_", "decimal_")):
        return "Value does not satisfy the numeric schema."
    if code.startswith("literal_"):
        return "Value is outside the allowed choices."
    return "Value does not satisfy the declared schema."


class AgentTraceStep(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    sequence: int
    kind: str
    capability_id: str | None = None
    semantic_name: str | None = None
    call_ref: str | None = None
    parent_call_ref: str | None = None
    attempt: int | None = None
    phase: str | None = None
    reason_code: str | None = None
    task_type: str | None = None
    trigger: str | None = None
    recovery_from: str | None = None
    arguments_hash: str | None = None
    argument_shape: dict[str, Any] = Field(default_factory=dict)
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    policy_status: str | None = None
    observation_status: str | None = None
    stop_reason: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    workflow_ref: dict[str, Any] | None = None
    receipt_ref: dict[str, Any] | None = None
    started_at: datetime.datetime
    finished_at: datetime.datetime | None = None


class AgentTraceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    conversation_id: int
    owner_user_id: int
    runtime_cohort: str
    transport: str
    status: str
    stop_reason: str | None
    objective_summary: str | None
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    started_at: datetime.datetime
    finished_at: datetime.datetime | None


class AgentTraceDetail(AgentTraceSummary):
    manifest_snapshot_hash: str | None
    budget: dict[str, Any]
    usage: dict[str, Any]
    timing: dict[str, Any]
    steps: list[AgentTraceStep]


class AgentTraceList(BaseModel):
    traces: list[AgentTraceSummary]
    total: int


class AgentTraceService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_traces(
        self, *, status: str | None = None, cohort: str | None = None,
        tool: str | None = None, conversation_id: int | None = None,
        owner_user_id: int | None = None,
        started_after: datetime.datetime | None = None,
        started_before: datetime.datetime | None = None,
        offset: int = 0, limit: int = 50,
    ) -> AgentTraceList:
        filters = []
        if status:
            filters.append(HarnessAgentRunRow.status == status)
        if cohort:
            filters.append(HarnessAgentRunRow.runtime_cohort == cohort)
        if conversation_id is not None:
            filters.append(HarnessAgentRunRow.conversation_id == conversation_id)
        if owner_user_id is not None:
            filters.append(HarnessAgentRunRow.owner_user_id == owner_user_id)
        if started_after:
            filters.append(HarnessAgentRunRow.started_at >= started_after)
        if started_before:
            filters.append(HarnessAgentRunRow.started_at <= started_before)
        if tool:
            filters.append(HarnessAgentRunRow.steps.any(
                (HarnessAgentStepRow.semantic_name == tool)
                | (HarnessAgentStepRow.capability_id == tool)
            ))
        rows = (await self._db.execute(
            select(HarnessAgentRunRow).where(*filters)
            .order_by(HarnessAgentRunRow.started_at.desc())
            .offset(max(0, offset)).limit(max(1, min(limit, 100)))
        )).scalars().all()
        total = (await self._db.execute(
            select(func.count(HarnessAgentRunRow.id)).where(*filters)
        )).scalar_one()
        return AgentTraceList(traces=[_summary(row) for row in rows], total=total)

    async def get_trace(self, run_id: str) -> AgentTraceDetail | None:
        row = (await self._db.execute(
            select(HarnessAgentRunRow).where(HarnessAgentRunRow.run_id == run_id)
        )).scalar_one_or_none()
        if row is None:
            return None
        steps = (await self._db.execute(
            select(HarnessAgentStepRow)
            .where(HarnessAgentStepRow.agent_run_id == row.id)
            .order_by(HarnessAgentStepRow.sequence)
        )).scalars().all()
        events = (await self._db.execute(
            select(HarnessAgentPublicEventRow)
            .where(HarnessAgentPublicEventRow.agent_run_id == row.id)
        )).scalars().all()
        event_payloads = {
            event.sequence: {
                "event_type": event.event_type,
                **dict(event.public_payload or {}),
            }
            for event in events
        }
        capability_contracts = _capability_contracts()
        return AgentTraceDetail(
            **_summary(row).model_dump(),
            manifest_snapshot_hash=row.manifest_snapshot_hash,
            budget=dict(row.budget or {}), usage=dict(row.usage or {}),
            timing=dict(row.timing or {}),
            steps=[
                _project_trace_step(
                    step,
                    event_payload=event_payloads.get(step.sequence, {}),
                    capability_contracts=capability_contracts,
                    runtime_cohort=row.runtime_cohort,
                    transport=row.transport,
                )
                for step in steps
            ],
        )


def _project_trace_step(
    row: HarnessAgentStepRow,
    *,
    event_payload: Mapping[str, Any],
    capability_contracts: Mapping[str, dict[str, Any]],
    runtime_cohort: str,
    transport: str,
) -> AgentTraceStep:
    step = AgentTraceStep.model_validate(row)
    details = dict(step.details)
    for key in ("event_type", "tool_label", "summary", "workflow_run_id"):
        value = event_payload.get(key)
        if value not in (None, "", [], {}):
            details.setdefault(key, value)
    if step.capability_id and step.capability_id in capability_contracts:
        details.setdefault("capability", capability_contracts[step.capability_id])
    status = step.observation_status
    if step.kind == "routing":
        status = "selected"
        details.setdefault("routing", _routing_contract(runtime_cohort, transport))
    elif step.kind == "runtime" and status == "running":
        status = "completed"
    return step.model_copy(update={"details": details, "observation_status": status})


def _capability_contracts() -> dict[str, dict[str, Any]]:
    # Local import avoids coupling the trace persistence model to registry startup.
    from src.harness.registry import base_capability_registry

    return {
        definition.id: {
            "label": definition.label,
            "purpose": definition.purpose,
            "provider_id": definition.provider_id,
            "provider_type": definition.provider_type.value,
            "risk_tier": definition.risk_tier.value,
            "action": definition.action,
            "task_types": list(definition.task_types),
            "required_permissions": list(definition.required_permissions),
            "preconditions": list(definition.preconditions),
            "selection_guidance": list(definition.use_when[:3]),
            "input_schema": _safe_schema_projection(definition.input_schema),
        }
        for definition in base_capability_registry().list()
    }


def _safe_schema_projection(schema: Mapping[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    required = {
        str(item) for item in schema.get("required", []) if isinstance(item, str)
    }
    fields: list[dict[str, Any]] = []
    if isinstance(properties, Mapping):
        for name, definition in list(properties.items())[:50]:
            field = definition if isinstance(definition, Mapping) else {}
            projected: dict[str, Any] = {
                "name": str(name)[:80],
                "type": field.get("type", "unspecified"),
                "required": str(name) in required,
            }
            for constraint in (
                "minLength", "maxLength", "minimum", "maximum",
                "minItems", "maxItems", "enum",
            ):
                if constraint in field:
                    projected[constraint] = field[constraint]
            fields.append(projected)
    return {
        "type": schema.get("type", "object"),
        "fields": fields,
        "additional_properties": schema.get("additionalProperties", True),
    }


def _routing_contract(cohort: str, transport: str) -> dict[str, Any]:
    labels = {
        "v2_effect_primary": (
            "Full governed agent runtime",
            "Read capabilities and governed actions were available. Actions still obey policy and approval requirements.",
        ),
        "v2_plan_primary": (
            "Planning-primary agent runtime",
            "The turn was routed through objective projection and durable-plan enforcement.",
        ),
        "v2_readonly_primary": (
            "Read-only agent runtime",
            "Only evidence-gathering capabilities were available; no domain effects could execute.",
        ),
    }
    label, explanation = labels.get(
        cohort,
        ("Agent runtime selected", "The server selected this runtime cohort from its rollout policy."),
    )
    return {
        "cohort": cohort,
        "label": label,
        "explanation": explanation,
        "transport": transport,
        "decision_authority": "server rollout and capability policy",
    }


def _summary(row: HarnessAgentRunRow) -> AgentTraceSummary:
    budget = dict(row.budget or {})
    usage = dict(row.usage or {})
    return AgentTraceSummary(
        run_id=row.run_id, conversation_id=row.conversation_id,
        owner_user_id=row.owner_user_id, runtime_cohort=row.runtime_cohort,
        transport=row.transport, status=row.status, stop_reason=row.stop_reason,
        # Historical rows may contain the user's raw request. The trace API never
        # exposes it; operators get deterministic routing/tool reason codes instead.
        objective_summary=None,
        model_calls=int(budget.get("model_calls_used", usage.get("model_calls", 0)) or 0),
        tool_calls=int(budget.get("tool_calls_used", usage.get("tool_calls", 0)) or 0),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cost_usd=float(
            usage.get("cost_usd", 0.0)
            or (
                float(usage.get("model_cost_usd", 0.0) or 0.0)
                + float(usage.get("capability_cost_usd", 0.0) or 0.0)
            )
        ),
        started_at=row.started_at, finished_at=row.finished_at,
    )
