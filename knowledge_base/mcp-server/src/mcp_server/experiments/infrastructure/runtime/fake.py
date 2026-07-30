"""Deterministic offline implementation of the generic agent runtime port."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    ArtifactReference,
    CancelRequest,
    CancelResult,
    ModelCapability,
    RuntimeAvailability,
    RuntimeCapabilities,
    RuntimeEventCategory,
    RuntimeHealth,
    RuntimeTaskEvent,
    RuntimeTaskHandle,
    RuntimeTaskRequest,
    RuntimeTaskResult,
    RuntimeTaskStatus,
    RuntimeTimings,
    RuntimeUsage,
)


@dataclass(frozen=True, slots=True)
class FakeToolCall:
    name: str
    returned_bytes: int = 0
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FakeRuntimeScenario:
    """One deterministic outcome selected by task ID or idempotency key."""

    outcome: RuntimeTaskStatus = RuntimeTaskStatus.SUCCEEDED
    output_refs: tuple[ArtifactReference, ...] = ()
    usage: RuntimeUsage = field(default_factory=RuntimeUsage)
    elapsed_ms: int = 0
    provider_latency_ms: int | None = None
    retry_count: int = 0
    tool_calls: tuple[FakeToolCall, ...] = ()
    failure_category: str | None = None
    failure_detail: Mapping[str, Any] = field(default_factory=dict)
    extra_events: tuple[RuntimeTaskEvent, ...] = ()


@dataclass(slots=True)
class _StoredTask:
    handle: RuntimeTaskHandle
    request: RuntimeTaskRequest
    scenario: FakeRuntimeScenario
    started_at: datetime
    events: tuple[RuntimeTaskEvent, ...]
    cancelled_at: datetime | None = None


class FakeAgentRuntime(AgentRuntime):
    """An async-safe runtime fake with no I/O, credentials, or wall-clock sleeps."""

    runtime_id = "fake"
    runtime_version = "1.0"

    def __init__(
        self,
        *,
        models: tuple[ModelCapability, ...] = (),
        scenarios: Mapping[str, FakeRuntimeScenario] | None = None,
        availability: RuntimeAvailability = RuntimeAvailability.AVAILABLE,
        now: datetime | None = None,
    ) -> None:
        self._models = models
        self._scenarios = dict(scenarios or {})
        self._availability = availability
        self._now = now or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._tasks_by_session: dict[str, _StoredTask] = {}
        self._sessions_by_idempotency: dict[str, str] = {}
        self.started_requests: list[RuntimeTaskRequest] = []
        self.cancel_requests: list[CancelRequest] = []

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            availability=self._availability,
            checked_at=self._now,
        )

    async def capabilities(self) -> RuntimeCapabilities:
        all_capabilities = frozenset(
            capability for model in self._models for capability in model.capabilities
        )
        return RuntimeCapabilities(
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            checked_at=self._now,
            models=self._models,
            supported_capabilities=all_capabilities,
        )

    async def start_task(self, request: RuntimeTaskRequest) -> RuntimeTaskHandle:
        existing_session = self._sessions_by_idempotency.get(request.idempotency_key)
        if existing_session is not None:
            existing = self._tasks_by_session[existing_session]
            if existing.request != request:
                raise ValueError("idempotency key is already bound to a different runtime request")
            return existing.handle

        session_id = self._stable_id("session", request)
        message_id = self._stable_id("message", request)
        handle = RuntimeTaskHandle(
            runtime_id=self.runtime_id,
            session_id=session_id,
            message_id=message_id,
            experiment_id=request.experiment_id,
            task_id=request.task_id,
            idempotency_key=request.idempotency_key,
        )
        scenario = self._scenarios.get(request.idempotency_key, self._scenarios.get(request.task_id))
        scenario = scenario or FakeRuntimeScenario()
        events = self._build_events(handle, scenario)
        self._tasks_by_session[session_id] = _StoredTask(
            handle=handle,
            request=request,
            scenario=scenario,
            started_at=self._now,
            events=events,
        )
        self._sessions_by_idempotency[request.idempotency_key] = session_id
        self.started_requests.append(request)
        return handle

    async def result(self, handle: RuntimeTaskHandle) -> RuntimeTaskResult:
        stored = self._stored(handle)
        if stored.cancelled_at is not None:
            return self._result_for_cancelled(stored)
        return self._result_from_scenario(stored)

    async def cancel(self, request: CancelRequest) -> CancelResult:
        stored = self._tasks_by_session.get(request.session_id)
        self.cancel_requests.append(request)
        if stored is None or stored.handle.experiment_id != request.experiment_id or stored.handle.task_id != request.task_id:
            return CancelResult(
                accepted=False,
                status=RuntimeTaskStatus.UNKNOWN,
                runtime_id=self.runtime_id,
                session_id=request.session_id,
                requested_at=self._now,
                detail={"reason": "unknown_or_unbound_session"},
            )
        if stored.cancelled_at is None:
            stored.cancelled_at = self._now
            stored.events = (*stored.events, self._cancel_event(stored))
        return CancelResult(
            accepted=True,
            status=RuntimeTaskStatus.CANCELLED,
            runtime_id=self.runtime_id,
            session_id=request.session_id,
            requested_at=self._now,
        )

    async def _observe(
        self,
        handle: RuntimeTaskHandle,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[RuntimeTaskEvent]:
        stored = self._stored(handle)
        for event in stored.events:
            if event.sequence > after_sequence:
                yield event

    def observe(
        self,
        handle: RuntimeTaskHandle,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[RuntimeTaskEvent]:
        return self._observe(handle, after_sequence=after_sequence)

    def _stored(self, handle: RuntimeTaskHandle) -> _StoredTask:
        stored = self._tasks_by_session.get(handle.session_id)
        if stored is None or stored.handle != handle:
            raise ValueError("unknown runtime task handle")
        return stored

    def _build_events(
        self,
        handle: RuntimeTaskHandle,
        scenario: FakeRuntimeScenario,
    ) -> tuple[RuntimeTaskEvent, ...]:
        events = [
            RuntimeTaskEvent(
                event_id=f"{handle.session_id}:1",
                sequence=1,
                category=RuntimeEventCategory.LIFECYCLE,
                occurred_at=self._now,
                task_status=RuntimeTaskStatus.RUNNING,
                payload={"phase": "started"},
            )
        ]
        sequence = 2
        for tool_call in scenario.tool_calls:
            events.append(
                RuntimeTaskEvent(
                    event_id=f"{handle.session_id}:{sequence}",
                    sequence=sequence,
                    category=RuntimeEventCategory.TOOL,
                    occurred_at=self._now,
                    payload={
                        "tool_name": tool_call.name,
                        "returned_bytes": tool_call.returned_bytes,
                        "detail": tool_call.payload,
                    },
                )
            )
            sequence += 1
        for event in scenario.extra_events:
            events.append(
                replace(
                    event,
                    event_id=f"{handle.session_id}:{sequence}",
                    sequence=sequence,
                    occurred_at=self._now,
                )
            )
            sequence += 1
        events.append(
            RuntimeTaskEvent(
                event_id=f"{handle.session_id}:{sequence}",
                sequence=sequence,
                category=(
                    RuntimeEventCategory.ERROR
                    if scenario.outcome == RuntimeTaskStatus.FAILED
                    else RuntimeEventCategory.LIFECYCLE
                ),
                occurred_at=self._now,
                task_status=scenario.outcome,
                payload=(
                    scenario.failure_detail
                    if scenario.outcome == RuntimeTaskStatus.FAILED
                    else {"phase": "finished"}
                ),
            )
        )
        return tuple(events)

    def _result_from_scenario(self, stored: _StoredTask) -> RuntimeTaskResult:
        scenario = stored.scenario
        completed_at = stored.started_at + timedelta(milliseconds=scenario.elapsed_ms)
        timing = RuntimeTimings(
            started_at=stored.started_at,
            completed_at=completed_at,
            elapsed_ms=scenario.elapsed_ms,
            provider_latency_ms=scenario.provider_latency_ms,
        )
        return RuntimeTaskResult(
            status=scenario.outcome,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            provider_id=stored.request.provider_id,
            model_id=stored.request.model_id,
            session_id=stored.handle.session_id,
            message_id=stored.handle.message_id,
            output_refs=scenario.output_refs if scenario.outcome == RuntimeTaskStatus.SUCCEEDED else (),
            usage=scenario.usage,
            timings=timing,
            retry_count=scenario.retry_count,
            tool_calls=len(scenario.tool_calls),
            tool_bytes=sum(call.returned_bytes for call in scenario.tool_calls),
            failure_category=scenario.failure_category,
            failure_detail=scenario.failure_detail,
        )

    def _result_for_cancelled(self, stored: _StoredTask) -> RuntimeTaskResult:
        return RuntimeTaskResult(
            status=RuntimeTaskStatus.CANCELLED,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            provider_id=stored.request.provider_id,
            model_id=stored.request.model_id,
            session_id=stored.handle.session_id,
            message_id=stored.handle.message_id,
            usage=stored.scenario.usage,
            timings=RuntimeTimings(
                started_at=stored.started_at,
                completed_at=stored.cancelled_at,
                elapsed_ms=stored.scenario.elapsed_ms,
                provider_latency_ms=stored.scenario.provider_latency_ms,
            ),
            retry_count=stored.scenario.retry_count,
            tool_calls=len(stored.scenario.tool_calls),
            tool_bytes=sum(call.returned_bytes for call in stored.scenario.tool_calls),
            failure_category="cancelled",
        )

    def _cancel_event(self, stored: _StoredTask) -> RuntimeTaskEvent:
        return RuntimeTaskEvent(
            event_id=f"{stored.handle.session_id}:{len(stored.events) + 1}",
            sequence=len(stored.events) + 1,
            category=RuntimeEventCategory.LIFECYCLE,
            occurred_at=stored.cancelled_at or self._now,
            task_status=RuntimeTaskStatus.CANCELLED,
            payload={"phase": "cancelled"},
        )

    @staticmethod
    def _stable_id(kind: str, request: RuntimeTaskRequest) -> str:
        value = ":".join((kind, request.experiment_id, request.task_id, request.idempotency_key))
        return f"fake-{kind}-{sha256(value.encode()).hexdigest()[:16]}"
