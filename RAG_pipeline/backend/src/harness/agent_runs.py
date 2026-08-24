"""Durable Agent Run and sanitized public-event authority for Slice 6."""

from __future__ import annotations

import datetime
import re
import secrets
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    HarnessAgentPublicEventRow,
    HarnessAgentRunRow,
    HarnessAgentStepRow,
)
from src.api.metrics import (
    agent_run_model_calls,
    agent_run_public_events_total,
    agent_run_stops_total,
    agent_run_tool_calls,
)

RunStatus = Literal["running", "waiting", "completed", "failed", "interrupted"]
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "interrupted"})
_METRIC_STOP_REASONS = frozenset({
    "answered", "clarification_required", "model_timeout", "tool_timeout",
    "soft_deadline_exceeded", "hard_deadline_exceeded", "model_call_limit",
    "tool_call_limit", "cost_budget_exceeded", "invalid_model_output",
    "state_conflict", "runtime_failed",
})

_PUBLIC_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "run.started": frozenset({"status", "cohort", "transport", "objective"}),
    "runtime.update": frozenset({"status", "phase", "summary"}),
    # Live, server-authored progress emitted *while* the model is generating.
    # There is deliberately no `content` field: the answer text it is producing
    # has not passed the grounding validator yet, so only the phase, a
    # server-authored label and elapsed time may be published.
    "assistant.progress": frozenset({"status", "phase", "summary", "elapsed_ms"}),
    "assistant.delta": frozenset({"content"}),
    "tool.proposed": frozenset({"tool_label", "status", "call_ref"}),
    "tool.running": frozenset({"tool_label", "status", "call_ref"}),
    "tool.awaiting_approval": frozenset({"tool_label", "status", "summary", "proposal_id"}),
    # `targets` carries topology navigation targets verbatim: the canvas needs them
    # to highlight and navigate, and their ids belong to whichever id space the
    # canvas is rendering. They are never rewritten here.
    "tool.completed": frozenset({"tool_label", "status", "summary", "evidence_refs", "receipt_ref", "workflow_ref", "targets"}),
    "tool.denied": frozenset({"tool_label", "status", "summary"}),
    "workflow.started": frozenset({"workflow_run_id", "status", "summary"}),
    "workflow.waiting": frozenset({"workflow_run_id", "status", "summary"}),
    "plan.updated": frozenset({"plan_id", "version", "status", "summary"}),
    "assistant.completed": frozenset({"message_id", "status"}),
    "run.completed": frozenset({"status", "stop_reason", "usage", "latency_ms"}),
    "run.failed": frozenset({"status", "stop_reason", "summary", "latency_ms"}),
    "run.interrupted": frozenset({"status", "stop_reason", "summary"}),
}
_PRIVATE_KEYS = frozenset({
    "arguments", "prompt", "messages", "reasoning", "thinking", "chain_of_thought",
    "authorization", "api_key", "password", "secret", "token", "access_token",
    "refresh_token", "raw_error", "traceback", "sql", "evidence_body", "policy_snapshot",
})
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b"),
)


class AgentRunConflict(RuntimeError):
    """A durable run identity was requested outside its owner/message scope."""


class PublicAgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    run_id: str
    sequence: int = Field(ge=1)
    type: str
    created_at: datetime.datetime
    public_payload: dict[str, Any]
    replayable: bool = True


class AgentRunProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    conversation_id: int
    owner_user_id: int
    user_message_id: int
    assistant_message_id: int | None
    runtime_cohort: str
    transport: str
    status: RunStatus
    stop_reason: str | None
    next_event_sequence: int
    started_at: datetime.datetime
    finished_at: datetime.datetime | None


class AgentRunService:
    """The only writer/reader for public run identity and replayable events."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def start_or_load(
        self,
        *,
        conversation_id: int,
        owner_user_id: int,
        user_message_id: int,
        runtime_cohort: str,
        transport: str,
        budget: Mapping[str, Any],
        objective_summary: str | None = None,
        manifest_snapshot_hash: str | None = None,
    ) -> tuple[AgentRunProjection, bool]:
        now = datetime.datetime.now(datetime.UTC)
        public_id = f"run-{secrets.token_urlsafe(18)}"
        statement = _insert_for_session(self._db, HarnessAgentRunRow.__table__).values(
            run_id=public_id,
            conversation_id=conversation_id,
            owner_user_id=owner_user_id,
            user_message_id=user_message_id,
            runtime_cohort=str(runtime_cohort)[:40],
            transport=str(transport)[:16],
            manifest_snapshot_hash=manifest_snapshot_hash,
            status="running",
            objective_summary=_clean_text(objective_summary or "", 500) or None,
            budget=_bounded_mapping(dict(budget)),
            usage={},
            timing={},
            next_event_sequence=1,
            started_at=now,
            created_at=now,
            updated_at=now,
        ).on_conflict_do_nothing(index_elements=["user_message_id"]).returning(
            HarnessAgentRunRow.id
        )
        inserted_id = (await self._db.execute(statement)).scalar_one_or_none()
        created = inserted_id is not None
        row = (
            await self._db.get(HarnessAgentRunRow, inserted_id)
            if created
            else (
                await self._db.execute(
                    select(HarnessAgentRunRow).where(
                        HarnessAgentRunRow.user_message_id == user_message_id
                    )
                )
            ).scalar_one()
        )
        if row is None or row.owner_user_id != owner_user_id or row.conversation_id != conversation_id:
            raise AgentRunConflict("Agent run identity is outside the requested owner scope")
        await self._db.commit()
        return _project_run(row), created

    async def get_owned(
        self, *, run_id: str, conversation_id: int, owner_user_id: int
    ) -> AgentRunProjection | None:
        row = (
            await self._db.execute(
                select(HarnessAgentRunRow).where(
                    HarnessAgentRunRow.run_id == run_id,
                    HarnessAgentRunRow.conversation_id == conversation_id,
                    HarnessAgentRunRow.owner_user_id == owner_user_id,
                )
            )
        ).scalar_one_or_none()
        return _project_run(row) if row else None

    async def append_public_event(
        self,
        *,
        run_id: str,
        event_type: str,
        public_payload: Mapping[str, Any] | None = None,
        replayable: bool = True,
    ) -> PublicAgentEvent:
        if event_type not in _PUBLIC_EVENT_FIELDS:
            raise ValueError(f"Unsupported public event type: {event_type}")
        allocation = (
            await self._db.execute(
                update(HarnessAgentRunRow)
                .where(
                    HarnessAgentRunRow.run_id == run_id,
                    HarnessAgentRunRow.status.not_in(TERMINAL_RUN_STATUSES),
                )
                .values(
                    next_event_sequence=HarnessAgentRunRow.next_event_sequence + 1
                )
                .returning(
                    HarnessAgentRunRow.id,
                    HarnessAgentRunRow.run_id,
                    HarnessAgentRunRow.next_event_sequence,
                )
            )
        ).one_or_none()
        if allocation is None:
            raise AgentRunConflict("Terminal agent runs are immutable")
        internal_run_id, public_run_id, next_sequence = allocation
        sequence = next_sequence - 1
        now = datetime.datetime.now(datetime.UTC)
        payload = sanitize_public_payload(event_type, public_payload or {})
        event_id = f"{public_run_id}:evt:{sequence:06d}"
        event = HarnessAgentPublicEventRow(
            event_id=event_id,
            agent_run_id=internal_run_id,
            sequence=sequence,
            event_type=event_type,
            public_payload=payload,
            replayable=replayable,
            occurred_at=now,
        )
        self._db.add(event)
        await self._db.commit()
        agent_run_public_events_total.labels(event_type).inc()
        return PublicAgentEvent(
            event_id=event_id,
            run_id=public_run_id,
            sequence=sequence,
            type=event_type,
            created_at=now,
            public_payload=payload,
            replayable=replayable,
        )

    async def replay(
        self,
        *,
        run_id: str,
        conversation_id: int,
        owner_user_id: int,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[PublicAgentEvent]:
        run = (
            await self._db.execute(
                select(HarnessAgentRunRow).where(
                    HarnessAgentRunRow.run_id == run_id,
                    HarnessAgentRunRow.conversation_id == conversation_id,
                    HarnessAgentRunRow.owner_user_id == owner_user_id,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            return []
        rows = (
            await self._db.execute(
                select(HarnessAgentPublicEventRow)
                .where(
                    HarnessAgentPublicEventRow.agent_run_id == run.id,
                    HarnessAgentPublicEventRow.sequence > max(0, after_sequence),
                    HarnessAgentPublicEventRow.replayable.is_(True),
                )
                .order_by(HarnessAgentPublicEventRow.sequence)
                .limit(max(1, min(limit, 1000)))
            )
        ).scalars().all()
        return [_project_event(run.run_id, row) for row in rows]

    async def record_step(
        self,
        *,
        run_id: str,
        sequence: int,
        kind: str,
        capability_id: str | None = None,
        semantic_name: str | None = None,
        call_ref: str | None = None,
        parent_call_ref: str | None = None,
        attempt: int | None = None,
        phase: str | None = None,
        reason_code: str | None = None,
        task_type: str | None = None,
        trigger: str | None = None,
        recovery_from: str | None = None,
        arguments_hash: str | None = None,
        argument_shape: Mapping[str, Any] | None = None,
        validation_errors: Sequence[Mapping[str, Any]] = (),
        details: Mapping[str, Any] | None = None,
        workflow_ref: Mapping[str, Any] | None = None,
        receipt_ref: Mapping[str, Any] | None = None,
        policy_status: str | None = None,
        observation_status: str | None = None,
        stop_reason: str | None = None,
        latency_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        evidence_refs: Sequence[str] = (),
        started_at: datetime.datetime | None = None,
        finished_at: datetime.datetime | None = None,
    ) -> None:
        run = (await self._db.execute(select(HarnessAgentRunRow).where(HarnessAgentRunRow.run_id == run_id))).scalar_one()
        now = datetime.datetime.now(datetime.UTC)
        self._db.add(HarnessAgentStepRow(
            agent_run_id=run.id,
            sequence=sequence,
            kind=_clean_text(kind, 32),
            capability_id=_clean_text(capability_id or "", 160) or None,
            semantic_name=_clean_text(semantic_name or "", 120) or None,
            call_ref=_clean_text(call_ref or "", 160) or None,
            parent_call_ref=_clean_text(parent_call_ref or "", 160) or None,
            attempt=max(1, int(attempt)) if attempt is not None else None,
            phase=_clean_text(phase or "", 48) or None,
            reason_code=_clean_text(reason_code or "", 80) or None,
            task_type=_clean_text(task_type or "", 80) or None,
            trigger=_clean_text(trigger or "", 80) or None,
            recovery_from=_clean_text(recovery_from or "", 160) or None,
            arguments_hash=_clean_text(arguments_hash or "", 64) or None,
            argument_shape=_bounded_mapping(dict(argument_shape or {})),
            validation_errors=[
                _bounded_mapping(dict(item)) for item in validation_errors[:20]
            ],
            details=_bounded_mapping(dict(details or {})),
            workflow_ref=_bounded_mapping(dict(workflow_ref)) if workflow_ref else None,
            receipt_ref=_bounded_mapping(dict(receipt_ref)) if receipt_ref else None,
            policy_status=_clean_text(policy_status or "", 48) or None,
            observation_status=_clean_text(observation_status or "", 48) or None,
            stop_reason=_clean_text(stop_reason or "", 64) or None,
            latency_ms=max(0, int(latency_ms)),
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            cost_usd=max(0.0, float(cost_usd)),
            evidence_refs=[_clean_text(str(item), 240) for item in evidence_refs[:20]],
            started_at=started_at or now,
            finished_at=finished_at,
        ))
        await self._db.commit()

    async def finish(
        self,
        *,
        run_id: str,
        status: Literal["completed", "failed", "interrupted"],
        stop_reason: str,
        assistant_message_id: int | None = None,
        usage: Mapping[str, Any] | None = None,
        timing: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        manifest_snapshot_hash: str | None = None,
        terminal_payload: Mapping[str, Any] | None = None,
    ) -> AgentRunProjection:
        event_type = f"run.{status}"
        now = datetime.datetime.now(datetime.UTC)
        safe_usage = _bounded_mapping(dict(usage or {}))
        safe_timing = _bounded_mapping(dict(timing or {}))
        values: dict[str, Any] = {
            "status": status,
            "stop_reason": _clean_text(stop_reason, 64),
            "assistant_message_id": assistant_message_id,
            "usage": safe_usage,
            "timing": safe_timing,
            "finished_at": now,
            "next_event_sequence": HarnessAgentRunRow.next_event_sequence + 1,
        }
        if budget is not None:
            values["budget"] = _bounded_mapping(dict(budget))
        if manifest_snapshot_hash:
            values["manifest_snapshot_hash"] = _clean_text(manifest_snapshot_hash, 64)
        allocation = (
            await self._db.execute(
                update(HarnessAgentRunRow)
                .where(
                    HarnessAgentRunRow.run_id == run_id,
                    HarnessAgentRunRow.status.not_in(TERMINAL_RUN_STATUSES),
                )
                .values(**values)
                .returning(
                    HarnessAgentRunRow.id,
                    HarnessAgentRunRow.run_id,
                    HarnessAgentRunRow.next_event_sequence,
                )
            )
        ).one_or_none()
        if allocation is None:
            row = (
                await self._db.execute(
                    select(HarnessAgentRunRow).where(HarnessAgentRunRow.run_id == run_id)
                )
            ).scalar_one()
            return _project_run(row)
        internal_run_id, public_run_id, next_sequence = allocation
        sequence = next_sequence - 1
        payload = sanitize_public_payload(
            event_type,
            terminal_payload
            or {"status": status, "stop_reason": stop_reason, "usage": usage or {}},
        )
        self._db.add(HarnessAgentPublicEventRow(
            event_id=f"{public_run_id}:evt:{sequence:06d}",
            agent_run_id=internal_run_id,
            sequence=sequence,
            event_type=event_type,
            public_payload=payload,
            replayable=True,
            occurred_at=now,
        ))
        self._db.add(HarnessAgentStepRow(
            agent_run_id=internal_run_id,
            sequence=sequence,
            kind="final",
            phase="terminal",
            reason_code="runtime_terminal_outcome",
            trigger="runtime",
            policy_status=status,
            observation_status=status,
            stop_reason=_clean_text(stop_reason, 64),
            latency_ms=max(0, int((timing or {}).get("latency_ms", 0) or 0)),
            input_tokens=max(0, int((usage or {}).get("input_tokens", 0) or 0)),
            output_tokens=max(0, int((usage or {}).get("output_tokens", 0) or 0)),
            cost_usd=max(0.0, float(
                (usage or {}).get("cost_usd", 0.0)
                or (
                    float((usage or {}).get("model_cost_usd", 0.0) or 0.0)
                    + float((usage or {}).get("capability_cost_usd", 0.0) or 0.0)
                )
            )),
            evidence_refs=[],
            argument_shape={},
            validation_errors=[],
            details=_bounded_mapping({
                "event_type": event_type,
                "model": (timing or {}).get("model"),
                "provider": (timing or {}).get("provider"),
            }),
            started_at=now,
            finished_at=now,
        ))
        await self._db.commit()
        agent_run_public_events_total.labels(event_type).inc()
        metric_reason = stop_reason if stop_reason in _METRIC_STOP_REASONS else "other"
        agent_run_stops_total.labels(status, metric_reason).inc()
        if budget is not None:
            agent_run_model_calls.observe(max(0, int(budget.get("model_calls_used", 0))))
            agent_run_tool_calls.observe(max(0, int(budget.get("tool_calls_used", 0))))
        row = await self._db.get(HarnessAgentRunRow, internal_run_id)
        if row is None:
            raise RuntimeError("Finished Agent Run could not be reloaded")
        return _project_run(row)


def sanitize_public_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = _PUBLIC_EVENT_FIELDS.get(event_type)
    if allowed is None:
        raise ValueError(f"Unsupported public event type: {event_type}")
    return {
        key: _sanitize_value(value, depth=0, exact=key in _EXACT_VALUE_FIELDS)
        for key, value in payload.items()
        if key in allowed and key.lower() not in _PRIVATE_KEYS
    }


#: Six levels, not four: a topology navigation target nests
#: target -> level -> selection -> id under its list, and a *relationship*
#: selection nests one deeper still (selection -> streamKeys -> key). Truncating
#: either leaf produces a chip that navigates nowhere. Width (20 items) and
#: string length (1000 chars) still bound the payload.
_MAX_PUBLIC_PAYLOAD_DEPTH = 6

#: Allowlisted fields whose leaf strings must survive verbatim.
#:
#: Whitespace folding is a prose normalization; applied to an identifier it is
#: silent corruption. Topology edge keys join their endpoints with a unit
#: separator (\x1f), which ``str.split()`` treats as whitespace, so folding
#: rewrites the key and the client navigates nowhere.
#:
#: ``content`` (``assistant.delta``) is here for the mirror-image reason: it is
#: prose, but *chunked* prose that the client concatenates back together. Folding
#: each chunk collapses the answer's newlines into spaces and trims the space at
#: every chunk boundary, so the reassembled text is neither valid Markdown nor
#: equal to the stored answer. Now that the trusted answer streams while it is
#: generated, that folded text is what the user reads for the whole generation.
#:
#: These subtrees are still credential-redacted, length-bounded, depth-bounded and
#: width-bounded — only the re-folding is skipped.
_EXACT_VALUE_FIELDS: frozenset[str] = frozenset({"content", "targets"})


def _sanitize_value(value: Any, *, depth: int, exact: bool = False) -> Any:
    if depth >= _MAX_PUBLIC_PAYLOAD_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str):
        return _redact_text(value, 1000) if exact else _clean_text(value, 1000)
    if isinstance(value, Mapping):
        return {
            _clean_text(str(key), 80): _sanitize_value(item, depth=depth + 1, exact=exact)
            for key, item in list(value.items())[:20]
            if str(key).lower() not in _PRIVATE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [
            _sanitize_value(item, depth=depth + 1, exact=exact) for item in list(value)[:20]
        ]
    return _clean_text(str(value), 200)


def _redact_text(value: str, limit: int) -> str:
    """Credential-redact and bound a string without normalizing its whitespace."""

    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted[:limit]


def _clean_text(value: str, limit: int) -> str:
    return _redact_text(" ".join(value.split()), limit)


def _bounded_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key)[:80]: _sanitize_value(item, depth=0) for key, item in list(value.items())[:30]}


def _project_run(row: HarnessAgentRunRow) -> AgentRunProjection:
    return AgentRunProjection(
        run_id=row.run_id,
        conversation_id=row.conversation_id,
        owner_user_id=row.owner_user_id,
        user_message_id=row.user_message_id,
        assistant_message_id=row.assistant_message_id,
        runtime_cohort=row.runtime_cohort,
        transport=row.transport,
        status=row.status,
        stop_reason=row.stop_reason,
        next_event_sequence=row.next_event_sequence,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _project_event(run_id: str, row: HarnessAgentPublicEventRow) -> PublicAgentEvent:
    return PublicAgentEvent(
        event_id=row.event_id,
        run_id=run_id,
        sequence=row.sequence,
        type=row.event_type,
        created_at=row.occurred_at,
        public_payload=dict(row.public_payload),
        replayable=row.replayable,
    )


def _insert_for_session(db: AsyncSession, table):  # noqa: ANN001, ANN202
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql_insert(table)
    if dialect == "sqlite":
        return sqlite_insert(table)
    raise RuntimeError(f"Unsupported Agent Run database dialect: {dialect}")


__all__ = [
    "AgentRunConflict",
    "AgentRunProjection",
    "AgentRunService",
    "PublicAgentEvent",
    "TERMINAL_RUN_STATUSES",
    "sanitize_public_payload",
]
