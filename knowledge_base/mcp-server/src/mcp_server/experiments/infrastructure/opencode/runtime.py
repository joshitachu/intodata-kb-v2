"""OpenCode 1.18.4 implementation of the frozen generic ``AgentRuntime`` port."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    CancelRequest,
    CancelResult,
    ModelCapability,
    RuntimeAvailability,
    RuntimeCapabilities,
    RuntimeHealth,
    RuntimeTaskEvent,
    RuntimeTaskHandle,
    RuntimeTaskRequest,
    RuntimeTaskResult,
    RuntimeTaskStatus,
    RuntimeTimings,
    redact_payload,
)
from mcp_server.experiments.infrastructure.opencode.client import (
    OpenCodeClientError,
    OpenCodeHttpClient,
    OpenCodeProviderModelUnavailable,
    OpenCodeUnsupportedVersion,
)
from mcp_server.experiments.infrastructure.opencode.event_normalizer import (
    OpenCodeEventNormalizer,
    completed_assistant_message,
    event_session_id,
    message_failure,
    message_info,
    message_parts,
    normalize_status,
    normalize_usage,
)
from mcp_server.experiments.infrastructure.opencode.provider_mapping import to_opencode_model_route


PINNED_OPENCODE_VERSION = "1.18.4"


@dataclass(slots=True)
class _TaskState:
    request: RuntimeTaskRequest
    handle: RuntimeTaskHandle
    started_at: datetime
    normalizer: OpenCodeEventNormalizer
    events: list[RuntimeTaskEvent]
    cancelled: bool = False


class OpenCodeAgentRuntime(AgentRuntime):
    """Adapter with no access to source files, artifacts, or provider credentials."""

    runtime_id = "opencode"

    def __init__(
        self,
        client: OpenCodeHttpClient,
        *,
        supported_version: str = PINNED_OPENCODE_VERSION,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._supported_version = supported_version
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._runtime_version: str | None = None
        self._tasks: dict[str, _TaskState] = {}
        self._idempotency_sessions: dict[str, str] = {}

    async def health(self) -> RuntimeHealth:
        try:
            payload = await self._client.health()
            version = _version(payload)
            self._assert_supported_version(version)
            self._runtime_version = version
            return RuntimeHealth(
                runtime_id=self.runtime_id,
                runtime_version=version,
                availability=RuntimeAvailability.AVAILABLE,
                checked_at=self._now(),
                detail={"server": redact_payload(payload)},
            )
        except OpenCodeClientError as exc:
            return RuntimeHealth(
                runtime_id=self.runtime_id,
                runtime_version=self._runtime_version,
                availability=RuntimeAvailability.UNAVAILABLE,
                checked_at=self._now(),
                detail={"failure_category": exc.category, "detail": exc.detail},
            )

    async def capabilities(self) -> RuntimeCapabilities:
        payload = await self._client.providers()
        models = tuple(_models_from_provider_payload(payload))
        supported = frozenset(capability for model in models for capability in model.capabilities)
        return RuntimeCapabilities(
            runtime_id=self.runtime_id,
            runtime_version=self._runtime_version,
            checked_at=self._now(),
            models=models,
            supported_capabilities=supported,
        )

    async def start_task(self, request: RuntimeTaskRequest) -> RuntimeTaskHandle:
        existing_session_id = self._idempotency_sessions.get(request.idempotency_key)
        if existing_session_id is not None:
            state = self._tasks[existing_session_id]
            if state.request != request:
                raise ValueError("idempotency key is already bound to a different runtime request")
            return state.handle

        if self._runtime_version is None:
            health = await self.health()
            if health.availability != RuntimeAvailability.AVAILABLE:
                raise OpenCodeUnsupportedVersion(
                    "OpenCode health/version preflight must pass before task execution",
                    detail=health.detail,
                )

        capabilities = await self.capabilities()
        _ensure_route_is_available(request, capabilities.models)
        session = await self._client.create_session(title=_session_title(request))
        route = to_opencode_model_route(request.provider_id, request.model_id)
        payload: dict[str, Any] = {
            "model": route.as_payload(),
            "parts": [{"type": "text", "text": request.semantic_prompt}],
            "tools": {tool_name: True for tool_name in request.allowed_tool_names},
        }
        if request.agent_id:
            payload["agent"] = request.agent_id
        prompt_response = await self._client.prompt_async(
            session.session_id,
            payload=payload,
            idempotency_key=request.idempotency_key,
        )
        handle = RuntimeTaskHandle(
            runtime_id=self.runtime_id,
            session_id=session.session_id,
            message_id=_message_id(prompt_response),
            experiment_id=request.experiment_id,
            task_id=request.task_id,
            idempotency_key=request.idempotency_key,
        )
        self._tasks[handle.session_id] = _TaskState(
            request=request,
            handle=handle,
            started_at=self._now(),
            normalizer=OpenCodeEventNormalizer(),
            events=[],
        )
        self._idempotency_sessions[request.idempotency_key] = handle.session_id
        return handle

    def observe(
        self,
        handle: RuntimeTaskHandle,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[RuntimeTaskEvent]:
        return self._observe(handle, after_sequence=after_sequence)

    async def _observe(
        self,
        handle: RuntimeTaskHandle,
        *,
        after_sequence: int,
    ) -> AsyncIterator[RuntimeTaskEvent]:
        state = self._state(handle)
        try:
            async for raw_event in self._client.events(handle.session_id):
                if event_session_id(raw_event) == handle.session_id:
                    self._append_normalized(state, raw_event)
        except OpenCodeClientError:
            # Event delivery is not proof a task failed.  Reconcile with the
            # idempotent message-read endpoint instead of resubmitting a prompt.
            for message in await self._client.messages(handle.session_id):
                self._append_normalized(state, message)
        for event in state.events:
            if event.sequence > after_sequence:
                yield event

    async def result(self, handle: RuntimeTaskHandle) -> RuntimeTaskResult:
        state = self._state(handle)
        if state.cancelled:
            return self._cancelled_result(state)
        try:
            messages = await self._client.messages(handle.session_id)
            status_payload = await self._client.session_status(handle.session_id)
        except OpenCodeClientError as exc:
            return self._failed_result(state, exc.category, exc.detail)
        for message in messages:
            self._append_normalized(state, message)
        latest = _latest_assistant_message(messages)
        status, failure_detail = _result_status(status_payload, latest)
        telemetry_source = latest or status_payload or {}
        usage = normalize_usage(telemetry_source)
        completed_at = _completed_at(telemetry_source) or self._now() if status in {
            RuntimeTaskStatus.SUCCEEDED,
            RuntimeTaskStatus.FAILED,
            RuntimeTaskStatus.CANCELLED,
        } else None
        elapsed_ms = (
            int((completed_at - state.started_at).total_seconds() * 1000)
            if completed_at is not None
            else None
        )
        return RuntimeTaskResult(
            status=status,
            runtime_id=self.runtime_id,
            runtime_version=self._runtime_version,
            provider_id=_actual_route(telemetry_source, state.request.provider_id, state.request.model_id)[0],
            model_id=_actual_route(telemetry_source, state.request.provider_id, state.request.model_id)[1],
            session_id=handle.session_id,
            message_id=_message_id(telemetry_source),
            usage=usage,
            timings=RuntimeTimings(
                started_at=state.started_at,
                completed_at=completed_at,
                elapsed_ms=elapsed_ms,
                provider_latency_ms=_latency_ms(telemetry_source),
            ),
            tool_calls=_tool_calls(messages, state.events),
            tool_bytes=_tool_bytes_from_messages(messages, state.events),
            failure_category=("runtime_reported_failure" if status == RuntimeTaskStatus.FAILED else None),
            failure_detail=failure_detail,
        )

    async def cancel(self, request: CancelRequest) -> CancelResult:
        state = self._tasks.get(request.session_id)
        if state is None or state.handle.experiment_id != request.experiment_id or state.handle.task_id != request.task_id:
            return CancelResult(
                accepted=False,
                status=RuntimeTaskStatus.UNKNOWN,
                runtime_id=self.runtime_id,
                session_id=request.session_id,
                requested_at=self._now(),
                detail={"failure_category": "unknown_or_unbound_session"},
            )
        try:
            await self._client.abort(request.session_id)
        except OpenCodeClientError as exc:
            return CancelResult(
                accepted=False,
                status=RuntimeTaskStatus.UNKNOWN,
                runtime_id=self.runtime_id,
                session_id=request.session_id,
                requested_at=self._now(),
                detail={"failure_category": exc.category, "detail": exc.detail},
            )
        state.cancelled = True
        return CancelResult(
            accepted=True,
            status=RuntimeTaskStatus.CANCELLED,
            runtime_id=self.runtime_id,
            session_id=request.session_id,
            requested_at=self._now(),
        )

    def _append_normalized(self, state: _TaskState, payload: Mapping[str, Any]) -> None:
        event = state.normalizer.normalize(payload, received_at=self._now())
        if event is not None:
            state.events.append(event)

    def _state(self, handle: RuntimeTaskHandle) -> _TaskState:
        state = self._tasks.get(handle.session_id)
        if state is None or state.handle != handle:
            raise ValueError("unknown runtime task handle")
        return state

    def _assert_supported_version(self, version: str | None) -> None:
        if version != self._supported_version:
            raise OpenCodeUnsupportedVersion(
                "OpenCode version is not the pinned compatible version",
                detail={"reported_version": version, "supported_version": self._supported_version},
            )

    def _cancelled_result(self, state: _TaskState) -> RuntimeTaskResult:
        completed_at = self._now()
        return RuntimeTaskResult(
            status=RuntimeTaskStatus.CANCELLED,
            runtime_id=self.runtime_id,
            runtime_version=self._runtime_version,
            provider_id=state.request.provider_id,
            model_id=state.request.model_id,
            session_id=state.handle.session_id,
            message_id=state.handle.message_id,
            timings=RuntimeTimings(
                started_at=state.started_at,
                completed_at=completed_at,
                elapsed_ms=int((completed_at - state.started_at).total_seconds() * 1000),
            ),
            tool_calls=sum(1 for event in state.events if event.category == "tool"),
            tool_bytes=sum(_tool_bytes(event.payload) for event in state.events if event.category == "tool"),
            failure_category="cancelled",
        )

    def _failed_result(
        self,
        state: _TaskState,
        category: str,
        detail: Mapping[str, Any],
    ) -> RuntimeTaskResult:
        completed_at = self._now()
        return RuntimeTaskResult(
            status=RuntimeTaskStatus.UNKNOWN,
            runtime_id=self.runtime_id,
            runtime_version=self._runtime_version,
            provider_id=state.request.provider_id,
            model_id=state.request.model_id,
            session_id=state.handle.session_id,
            message_id=state.handle.message_id,
            timings=RuntimeTimings(
                started_at=state.started_at,
                completed_at=completed_at,
                elapsed_ms=int((completed_at - state.started_at).total_seconds() * 1000),
            ),
            failure_category=category,
            failure_detail=detail,
        )


def _version(payload: Mapping[str, Any]) -> str | None:
    for key in ("version", "buildVersion"):
        if payload.get(key) is not None:
            return str(payload[key])
    return None


def _models_from_provider_payload(payload: Mapping[str, Any] | list[Any]) -> list[ModelCapability]:
    providers = payload.get("all", payload.get("providers", [])) if isinstance(payload, Mapping) else payload
    if not isinstance(providers, list):
        raise ValueError("OpenCode provider discovery response was malformed")
    connected = _connected_provider_ids(payload) if isinstance(payload, Mapping) else set()
    output: list[ModelCapability] = []
    for provider in providers:
        if not isinstance(provider, Mapping):
            continue
        provider_id = str(provider.get("id") or provider.get("providerID") or "")
        model_values = provider.get("models", [])
        if isinstance(model_values, Mapping):
            model_values = [dict(value, id=key) if isinstance(value, Mapping) else {"id": key} for key, value in model_values.items()]
        if not provider_id or not isinstance(model_values, list):
            continue
        for model in model_values:
            if not isinstance(model, Mapping):
                continue
            model_id = str(model.get("id") or model.get("modelID") or "")
            if not model_id:
                continue
            caps = model.get("capabilities") or model.get("capability") or []
            if isinstance(caps, Mapping):
                caps = [name for name, enabled in caps.items() if enabled]
            output.append(
                ModelCapability(
                    provider_id=provider_id,
                    model_id=model_id,
                    capabilities=frozenset(str(capability) for capability in caps if capability),
                    available=provider_id in connected and _model_is_available(model),
                    max_input_tokens=_int_or_none(
                        (_mapping(model.get("limit")) or {}).get("context")
                        or model.get("maxInputTokens")
                        or model.get("contextWindow")
                    ),
                    max_output_tokens=_int_or_none(
                        (_mapping(model.get("limit")) or {}).get("output")
                        or model.get("maxOutputTokens")
                    ),
                    detail=redact_payload(model),
                )
            )
    return output


def _ensure_route_is_available(request: RuntimeTaskRequest, models: tuple[ModelCapability, ...]) -> None:
    for model in models:
        if model.provider_id == request.provider_id and model.model_id == request.model_id:
            if not model.available:
                break
            if not request.required_capabilities.issubset(model.capabilities):
                break
            return
    raise OpenCodeProviderModelUnavailable(
        "requested provider/model route is unavailable or lacks required capabilities",
        detail={"provider_id": request.provider_id, "model_id": request.model_id},
    )


def _session_title(request: RuntimeTaskRequest) -> str:
    return f"Nexus experiment {request.experiment_id} task {request.task_id}"


def _message_id(payload: Mapping[str, Any]) -> str | None:
    info = message_info(payload)
    for key in ("id", "messageID", "message_id"):
        if info.get(key) is not None:
            return str(info[key])
    return None


def _latency_ms(payload: Mapping[str, Any]) -> int | None:
    info = message_info(payload)
    for key in ("latencyMs", "latency_ms", "providerLatencyMs"):
        value = info.get(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            continue
    return None


def _completed_at(payload: Mapping[str, Any]) -> datetime | None:
    info = message_info(payload)
    time = _mapping(info.get("time")) or {}
    raw = time.get("completed") or time.get("finished")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _actual_route(payload: Mapping[str, Any], fallback_provider: str, fallback_model: str) -> tuple[str, str]:
    model = _mapping(message_info(payload).get("model")) or {}
    return (
        str(model.get("providerID") or model.get("providerId") or fallback_provider),
        str(model.get("modelID") or model.get("modelId") or fallback_model),
    )


def _tool_bytes(payload: Mapping[str, Any]) -> int:
    for key in ("returned_bytes", "returnedBytes", "bytes"):
        value = payload.get(key)
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            continue
    return 0


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _connected_provider_ids(payload: Mapping[str, Any]) -> set[str]:
    connected = payload.get("connected", ())
    if isinstance(connected, Mapping):
        return {str(provider_id) for provider_id, active in connected.items() if active}
    if isinstance(connected, list):
        return {str(provider_id) for provider_id in connected}
    return set()


def _model_is_available(model: Mapping[str, Any]) -> bool:
    status = str(model.get("status") or "available").lower()
    return bool(model.get("available", True)) and status not in {"unavailable", "disabled", "error"}


def _latest_assistant_message(messages: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return next(
        (
            message
            for message in reversed(messages)
            if str(message_info(message).get("role") or "").lower() == "assistant"
        ),
        None,
    )


def _result_status(
    session_status: Mapping[str, Any] | None,
    latest_assistant: Mapping[str, Any] | None,
) -> tuple[RuntimeTaskStatus, Mapping[str, Any]]:
    if session_status is not None:
        state = normalize_status(session_status.get("status") or session_status.get("state") or session_status.get("type"))
        if state == RuntimeTaskStatus.RUNNING:
            return RuntimeTaskStatus.RUNNING, {}
        if state == RuntimeTaskStatus.FAILED:
            return RuntimeTaskStatus.FAILED, redact_payload(session_status)
        if state == RuntimeTaskStatus.CANCELLED:
            return RuntimeTaskStatus.CANCELLED, redact_payload(session_status)
        if state != RuntimeTaskStatus.UNKNOWN:
            return state, {}
    # OpenCode's active-session status map omits idle sessions.  Only after
    # message reconciliation can an absent entry mean a completed task.
    if latest_assistant is None:
        return RuntimeTaskStatus.UNKNOWN, {"reason": "idle_without_assistant_completion"}
    failure = message_failure(latest_assistant)
    if failure:
        return RuntimeTaskStatus.FAILED, redact_payload(failure)
    if completed_assistant_message(latest_assistant):
        return RuntimeTaskStatus.SUCCEEDED, {}
    return RuntimeTaskStatus.UNKNOWN, {"reason": "idle_without_completed_assistant_message"}


def _tool_calls(messages: list[Mapping[str, Any]], events: list[RuntimeTaskEvent]) -> int:
    message_parts_count = sum(
        1 for message in messages for part in message_parts(message) if part.get("type") == "tool"
    )
    return max(message_parts_count, sum(1 for event in events if event.category == "tool"))


def _tool_bytes_from_messages(messages: list[Mapping[str, Any]], events: list[RuntimeTaskEvent]) -> int:
    values = [
        _tool_bytes(part)
        for message in messages
        for part in message_parts(message)
        if part.get("type") == "tool"
    ]
    return sum(values) if values else sum(_tool_bytes(event.payload) for event in events if event.category == "tool")
