"""OpenCode 1.18.4 implementation of the frozen generic ``AgentRuntime`` port."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from mcp_server.experiments.application.grounded_runtime import (
    BOUNDED_BUDGET_MONITORING_CAPABILITY,
    GroundedRoleRuntimeContext,
)
from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    ArtifactReference,
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
    OutputContract,
    RuntimeBudgets,
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

# Cap on how many recent events _TaskState retains for observe()'s
# reconnect-replay. This adapter's own long-running tasks are the ones that
# blew memory: every distinct OpenCode event (redacted, but still carrying
# real payload content -- tool output, message text) was kept for the whole
# life of a role with no limit. A live-measured run climbed ~250-300MB every
# 10 seconds this way, hitting a 3GB container ceiling in under two minutes
# regardless of project size. Replay only ever needs to catch a reconnecting
# subscriber up on *recent* history, not the entire run -- 200 events is
# generous for that and keeps this bounded no matter how long a role runs.
_EVENT_REPLAY_LIMIT = 200


class OpenCodeGroundedTaskBuilder:
    """Bind a Grounded role packet to the pinned OpenCode workspace/tool surface."""

    def build(self, context: GroundedRoleRuntimeContext) -> RuntimeTaskRequest:
        packet_path = context.packet_root / "PACKET.json"
        packet_bytes = packet_path.read_bytes()
        return RuntimeTaskRequest(
            experiment_id=context.experiment_id,
            task_id=f"grounded-role:{context.role_id}:attempt:{context.attempt}",
            idempotency_key=(
                f"{context.experiment_id}:grounded-role:{context.role_id}:{context.attempt}"
            ),
            provider_id=context.provider_id,
            model_id=context.model_id,
            semantic_prompt=context.prompt,
            semantic_prompt_sha256=sha256(context.prompt.encode("utf-8")).hexdigest(),
            context_refs=(
                ArtifactReference(
                    logical_name=f"roles/{context.role_id}/PACKET.json",
                    sha256=sha256(packet_bytes).hexdigest(),
                    media_type="application/json",
                    byte_size=len(packet_bytes),
                ),
            ),
            output_contract=OutputContract(
                artifact_name=f"roles/{context.role_id}/candidate-bundle",
                media_type="application/vnd.nexus.grounded-role-bundle",
                max_bytes=context.max_context_bytes,
            ),
            allowed_tool_names=("read", "glob", "grep", "edit", "write"),
            budgets=RuntimeBudgets(
                max_input_tokens=context.max_input_tokens,
                max_tool_calls=context.max_tool_calls,
                max_elapsed_ms=max(1, int(context.timeout_seconds * 1000)),
            ),
            deadline_at=datetime.now(timezone.utc)
            + timedelta(seconds=context.timeout_seconds),
            agent_id=context.agent_id,
            runtime_options={
                "workspace_directory": str(context.runtime_directory),
                "session_title": (
                    f"Nexus grounded role {context.role_id} {context.experiment_id} "
                    f"attempt {context.attempt}"
                ),
                "disabled_tool_names": ("task",),
                "return_failed_handle": True,
            },
        )


@dataclass(slots=True)
class _TaskState:
    request: RuntimeTaskRequest
    handle: RuntimeTaskHandle
    started_at: datetime
    normalizer: OpenCodeEventNormalizer
    events: "deque[RuntimeTaskEvent]"
    # Running counters, updated as events arrive -- the actual source of
    # truth for tool_calls/tool_bytes fallbacks below. Independent of the
    # bounded `events` replay buffer, so trimming that buffer never loses
    # count accuracy.
    tool_call_count: int = 0
    tool_byte_total: int = 0
    cancelled: bool = False
    submission_failure: OpenCodeClientError | None = None


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
        supported = frozenset(
            {
                BOUNDED_BUDGET_MONITORING_CAPABILITY,
                *(capability for model in models for capability in model.capabilities),
            }
        )
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
        _ensure_budgets_are_monitorable(request)
        session = await self._client.create_session(
            title=_session_title(request),
            directory=_workspace_directory(request),
        )
        route = to_opencode_model_route(request.provider_id, request.model_id)
        payload: dict[str, Any] = {
            "model": route.as_payload(),
            "parts": [{"type": "text", "text": request.semantic_prompt}],
            "tools": {tool_name: True for tool_name in request.allowed_tool_names},
        }
        for tool_name in _disabled_tool_names(request):
            payload["tools"][tool_name] = False
        if request.agent_id:
            payload["agent"] = request.agent_id
        handle = RuntimeTaskHandle(
            runtime_id=self.runtime_id,
            session_id=session.session_id,
            message_id=None,
            experiment_id=request.experiment_id,
            task_id=request.task_id,
            idempotency_key=request.idempotency_key,
        )
        state = _TaskState(
            request=request,
            handle=handle,
            started_at=self._now(),
            normalizer=OpenCodeEventNormalizer(),
            events=deque(maxlen=_EVENT_REPLAY_LIMIT),
        )
        self._tasks[handle.session_id] = state
        self._idempotency_sessions[request.idempotency_key] = handle.session_id
        try:
            prompt_response = await self._client.prompt_async(
                session.session_id,
                payload=payload,
                idempotency_key=request.idempotency_key,
            )
        except OpenCodeClientError as exc:
            # A session was successfully allocated even though its prompt was
            # rejected. Preserve that identity as a terminal protocol task so
            # orchestration can cancel/audit an optional role without reaching
            # back into the provider client.
            if not _return_failed_handle(request):
                self._tasks.pop(handle.session_id, None)
                self._idempotency_sessions.pop(request.idempotency_key, None)
                raise
            state.submission_failure = exc
            return handle
        state.handle = RuntimeTaskHandle(
            runtime_id=handle.runtime_id,
            session_id=handle.session_id,
            message_id=_message_id(prompt_response),
            experiment_id=handle.experiment_id,
            task_id=handle.task_id,
            idempotency_key=handle.idempotency_key,
        )
        handle = state.handle
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
        cursor = after_sequence
        for event in tuple(state.events):
            if event.sequence > cursor:
                cursor = event.sequence
                yield event
        event_stream = self._client.events(handle.session_id)
        try:
            async for raw_event in event_stream:
                if event_session_id(raw_event) == handle.session_id:
                    event = self._append_normalized(state, raw_event)
                    if event is not None and event.sequence > cursor:
                        cursor = event.sequence
                        yield event
        except OpenCodeClientError:
            # Event delivery is not proof a task failed.  Reconcile with the
            # idempotent message-read endpoint instead of resubmitting a prompt.
            for message in await self._client.messages(handle.session_id):
                event = self._append_normalized(state, message)
                if event is not None and event.sequence > cursor:
                    cursor = event.sequence
                    yield event
        finally:
            await event_stream.aclose()

    async def poll_status(self, handle: RuntimeTaskHandle) -> RuntimeTaskStatus:
        """Answer from the O(1) session-status endpoint, not the full transcript.

        ``result()`` fetches ``GET /session/{id}/message`` -- the entire
        conversation from the start of the session, no cursor. This adapter is
        polled every ~50ms for a role's whole lifetime (`_wait()` in
        run_staged_grounded_candidate.py), so calling ``result()`` there was
        re-fetching and re-parsing an ever-growing transcript ~20 times a
        second; over a long tool-heavy role that transcript reaches tens of
        megabytes, and the repeated full re-parses are what drove mcp-server's
        RSS to the container memory ceiling (confirmed via dmesg cgroup-oom
        traces scaling to whatever limit it was given: ~976MB @ 1g, ~2.97GB @
        3g -- a growth-over-time signature, not a fixed footprint).

        ``session_status()`` is a single lightweight lookup in OpenCode's
        active-session map and is enough to resolve running/failed/cancelled
        without ever touching the transcript. It only falls through to the
        full ``result()`` fetch for the one genuinely ambiguous case: a
        session absent from that map, which OpenCode uses for both "still
        idle, never started" and "finished" -- and that happens once, right
        at completion, not on every tick.
        """

        state = self._state(handle)
        if state.cancelled:
            return RuntimeTaskStatus.CANCELLED
        if state.submission_failure is not None:
            return RuntimeTaskStatus.FAILED
        try:
            status_payload = await self._client.session_status(handle.session_id)
        except OpenCodeClientError:
            # A status-endpoint hiccup is not proof of anything either way;
            # fall back to the authoritative (if expensive) reconciliation.
            return (await self.result(handle)).status
        status, _ = _result_status(status_payload, None)
        if status == RuntimeTaskStatus.UNKNOWN:
            return (await self.result(handle)).status
        return status

    async def result(self, handle: RuntimeTaskHandle) -> RuntimeTaskResult:
        state = self._state(handle)
        if state.cancelled:
            return self._cancelled_result(state)
        if state.submission_failure is not None:
            return self._submission_failed_result(state, state.submission_failure)
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
        tool_calls = _tool_calls(messages, state.tool_call_count)
        tool_bytes = _tool_bytes_from_messages(messages, state.tool_byte_total)
        budget_failure = _budget_failure(
            state.request,
            usage=usage,
            tool_calls=tool_calls,
            elapsed_ms=elapsed_ms,
        )
        if budget_failure is not None:
            status = RuntimeTaskStatus.FAILED
            failure_detail = {"budget": budget_failure}
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
            tool_calls=tool_calls,
            tool_bytes=tool_bytes,
            failure_category=(
                "budget_exceeded"
                if budget_failure is not None
                else "runtime_reported_failure"
                if status == RuntimeTaskStatus.FAILED
                else None
            ),
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

    def _append_normalized(
        self,
        state: _TaskState,
        payload: Mapping[str, Any],
    ) -> RuntimeTaskEvent | None:
        event = state.normalizer.normalize(payload, received_at=self._now())
        if event is not None:
            state.events.append(event)
            if event.category == "tool":
                state.tool_call_count += 1
                state.tool_byte_total += _tool_bytes(event.payload)
        return event

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
            tool_calls=state.tool_call_count,
            tool_bytes=state.tool_byte_total,
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

    def _submission_failed_result(
        self,
        state: _TaskState,
        failure: OpenCodeClientError,
    ) -> RuntimeTaskResult:
        completed_at = self._now()
        return RuntimeTaskResult(
            status=RuntimeTaskStatus.FAILED,
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
            failure_category=failure.category,
            failure_detail=failure.detail,
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


def _ensure_budgets_are_monitorable(request: RuntimeTaskRequest) -> None:
    values = (
        request.budgets.max_input_tokens,
        request.budgets.max_output_tokens,
        request.budgets.max_tool_calls,
        request.budgets.max_tool_bytes,
        request.budgets.max_elapsed_ms,
    )
    if any(value is not None and value < 0 for value in values):
        raise ValueError("runtime task budgets must be non-negative")
    if request.deadline_at.tzinfo is None:
        raise ValueError("runtime task deadline must be timezone-aware")


def _budget_failure(
    request: RuntimeTaskRequest,
    *,
    usage: Any,
    tool_calls: int,
    elapsed_ms: int | None,
) -> str | None:
    checks = (
        (usage.input_tokens, request.budgets.max_input_tokens, "input_tokens"),
        (usage.output_tokens, request.budgets.max_output_tokens, "output_tokens"),
        (tool_calls, request.budgets.max_tool_calls, "tool_calls"),
        (elapsed_ms, request.budgets.max_elapsed_ms, "elapsed_ms"),
    )
    for actual, maximum, name in checks:
        if actual is not None and maximum is not None and actual > maximum:
            return name
    return None


def _session_title(request: RuntimeTaskRequest) -> str:
    configured = request.runtime_options.get("session_title")
    if isinstance(configured, str) and configured.strip():
        return configured
    return f"Nexus experiment {request.experiment_id} task {request.task_id}"


def _workspace_directory(request: RuntimeTaskRequest) -> str | None:
    """Read the adapter-specific session directory without leaking it into the port."""

    configured = request.runtime_options.get("workspace_directory")
    return configured if isinstance(configured, str) and configured.strip() else None


def _disabled_tool_names(request: RuntimeTaskRequest) -> tuple[str, ...]:
    configured = request.runtime_options.get("disabled_tool_names")
    if not isinstance(configured, (list, tuple)):
        return ()
    return tuple(
        tool_name
        for tool_name in configured
        if isinstance(tool_name, str) and tool_name and tool_name not in request.allowed_tool_names
    )


def _return_failed_handle(request: RuntimeTaskRequest) -> bool:
    return request.runtime_options.get("return_failed_handle") is True


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


def _tool_calls(messages: list[Mapping[str, Any]], observed_tool_calls: int) -> int:
    message_parts_count = sum(
        1 for message in messages for part in message_parts(message) if part.get("type") == "tool"
    )
    return max(message_parts_count, observed_tool_calls)


def _tool_bytes_from_messages(messages: list[Mapping[str, Any]], observed_tool_bytes: int) -> int:
    values = [
        _tool_bytes(part)
        for message in messages
        for part in message_parts(message)
        if part.get("type") == "tool"
    ]
    return sum(values) if values else observed_tool_bytes
