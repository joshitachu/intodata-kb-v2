"""Bounded, startup-safe OpenAI Agents SDK adapter for Nexus experiments.

The optional SDK is imported only for an enabled runtime. Provider execution
starts only after an injected durable store atomically reserves the task. Nexus
continues to own evidence authorization, tool budgets, artifact writes, and
artifact receipts; this adapter owns the bounded SDK loop and normalization.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import inspect
import json
import logging
import math
import os
from time import monotonic
from typing import Any, Protocol

logger = logging.getLogger(__name__)

from mcp_server.experiments.application.grounded_runtime import (
    BOUNDED_BUDGET_MONITORING_CAPABILITY,
)
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
from mcp_server.experiments.mcp_tools.config_compiler import NEXUS_EXPERIMENT_TOOLS


VALIDATED_OPENAI_AGENTS_VERSION = "0.19.3"
_SDK_CAPABILITIES = frozenset(
    {"tools", "structured-output", "bounded-agent-loop", "streaming", "usage-reporting"}
)


class NexusToolFactory(Protocol):
    """Build token-scoped local Nexus functions for one immutable task."""

    def build_tools(
        self,
        *,
        request: RuntimeTaskRequest,
        allowed_tool_names: tuple[str, ...],
    ) -> Sequence[Any]: ...


class OutputReferenceResolver(Protocol):
    """Resolve Nexus-owned artifact receipts without returning artifact content."""

    def __call__(
        self,
        request: RuntimeTaskRequest,
        final_output: Any,
    ) -> tuple[ArtifactReference, ...] | Awaitable[tuple[ArtifactReference, ...]]: ...


class UsageCostEstimator(Protocol):
    """Price cumulative SDK usage from a frozen Nexus price-card route."""

    def __call__(
        self,
        request: RuntimeTaskRequest,
        usage: RuntimeUsage,
    ) -> float | None: ...


@dataclass(frozen=True, slots=True)
class RuntimeTaskReservation:
    """Result of an atomic, durable idempotency reservation."""

    handle: RuntimeTaskHandle
    request_sha256: str
    created: bool
    result: RuntimeTaskResult | None = None


class RuntimeTaskStateStore(Protocol):
    """Durable seam required before any provider request can be sent."""

    durable: bool

    async def reserve(
        self,
        *,
        idempotency_key: str,
        request_sha256: str,
        proposed_handle: RuntimeTaskHandle,
    ) -> RuntimeTaskReservation: ...

    async def complete(
        self,
        *,
        idempotency_key: str,
        request_sha256: str,
        result: RuntimeTaskResult,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class OpenAIAgentModel:
    """Operator-declared route; capability discovery performs no model call."""

    model_id: str
    capabilities: frozenset[str] = _SDK_CAPABILITIES
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    trustworthy_usage: bool = True


@dataclass(frozen=True, slots=True)
class OpenAIAgentsRuntimeConfig:
    """Default-off production-neutral configuration."""

    enabled: bool = False
    provider_id: str = "openai"
    models: tuple[OpenAIAgentModel, ...] = ()
    api_key_env_var: str = "OPENAI_API_KEY"
    max_turns_per_role: int = 8
    external_tracing_enabled: bool = False
    validated_sdk_version: str = VALIDATED_OPENAI_AGENTS_VERSION

    def __post_init__(self) -> None:
        if not self.provider_id or not self.api_key_env_var or not self.validated_sdk_version:
            raise ValueError(
                "OpenAI Agents provider, key environment, and SDK version are required"
            )
        if self.max_turns_per_role < 1:
            raise ValueError("OpenAI Agents max_turns_per_role must be positive")
        if self.external_tracing_enabled:
            raise ValueError("external tracing is not supported by the Nexus adapter")
        if any(not model.model_id for model in self.models):
            raise ValueError("OpenAI Agents models must have non-empty ids")
        if len({model.model_id for model in self.models}) != len(self.models):
            raise ValueError("OpenAI Agents model ids must be unique")


@dataclass(slots=True)
class _TaskState:
    request: RuntimeTaskRequest
    request_sha256: str
    handle: RuntimeTaskHandle
    started_at: datetime
    started_monotonic: float
    events: list[RuntimeTaskEvent]
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    finish_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: RuntimeTaskResult | None = None
    task: asyncio.Task[None] | None = None
    streamed_run: Any | None = None
    completed: bool = False
    tool_call_ids: set[str] = field(default_factory=set)
    tool_output_ids: set[str] = field(default_factory=set)
    tool_calls: int = 0
    tool_bytes: int = 0
    usage: RuntimeUsage = field(default_factory=RuntimeUsage)


class _RuntimeCeilingExceeded(RuntimeError):
    pass


class _UntrustworthyUsage(RuntimeError):
    pass


class _InvalidArtifactReceipt(ValueError):
    pass


class OpenAIAgentsRuntime(AgentRuntime):
    """OpenAI Agents implementation of the generic runtime port.

    The adapter never supplies hosted tools, sandboxes, handoffs, conversation
    storage, shell, filesystem, or MCP discovery. Only functions returned by the
    injected Nexus tool factory are passed to the SDK.
    """

    runtime_id = "openai-agents"

    def __init__(
        self,
        *,
        config: OpenAIAgentsRuntimeConfig | None = None,
        tool_factory: NexusToolFactory | None = None,
        output_reference_resolver: OutputReferenceResolver | None = None,
        task_state_store: RuntimeTaskStateStore | None = None,
        cost_estimator: UsageCostEstimator | None = None,
        model_resolver: Callable[[str], Any] | None = None,
        sdk: Any | None = None,
        api_key_available: Callable[[], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config or OpenAIAgentsRuntimeConfig()
        self._tool_factory = tool_factory
        self._output_reference_resolver = output_reference_resolver
        self._task_state_store = task_state_store
        self._cost_estimator = cost_estimator
        self._model_resolver = model_resolver
        self._sdk = sdk
        self._api_key_available = api_key_available or (
            lambda: bool(os.environ.get(self._config.api_key_env_var))
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._tasks: dict[str, _TaskState] = {}
        self._idempotency_sessions: dict[str, str] = {}
        self._idempotency_locks: dict[str, asyncio.Lock] = {}

    async def health(self) -> RuntimeHealth:
        reason = self._unavailable_reason()
        return RuntimeHealth(
            runtime_id=self.runtime_id,
            runtime_version=self._runtime_version(),
            availability=(
                RuntimeAvailability.AVAILABLE if reason is None else RuntimeAvailability.UNAVAILABLE
            ),
            checked_at=self._now(),
            detail={} if reason is None else {"failure_category": reason},
        )

    async def capabilities(self) -> RuntimeCapabilities:
        health = await self.health()
        models: tuple[ModelCapability, ...] = ()
        if health.availability == RuntimeAvailability.AVAILABLE:
            cost_capability = (
                frozenset({"cost-accounting"}) if self._cost_estimator else frozenset()
            )
            model_routes: list[ModelCapability] = []
            for model in self._config.models:
                usage_is_trustworthy = model.trustworthy_usage and {
                    "bounded-agent-loop",
                    "streaming",
                    "usage-reporting",
                }.issubset(model.capabilities)
                bounded_capability = (
                    frozenset({BOUNDED_BUDGET_MONITORING_CAPABILITY})
                    if usage_is_trustworthy
                    else frozenset()
                )
                # Never accept a caller's bare capability assertion. Nexus only
                # advertises bounded monitoring when this adapter can inspect
                # trusted cumulative usage at every completed model turn and
                # cancel before another turn starts. Tool/time ceilings remain
                # enforced continuously by Nexus-owned boundaries.
                declared = model.capabilities - frozenset({BOUNDED_BUDGET_MONITORING_CAPABILITY})
                model_routes.append(
                    ModelCapability(
                        provider_id=self._config.provider_id,
                        model_id=model.model_id,
                        capabilities=declared | cost_capability | bounded_capability,
                        available=usage_is_trustworthy,
                        max_input_tokens=model.max_input_tokens,
                        max_output_tokens=model.max_output_tokens,
                        detail={
                            "budget_monitoring": (
                                "turn-boundary-cumulative"
                                if usage_is_trustworthy
                                else "unavailable"
                            ),
                            "usage_reporting": "required",
                        },
                    )
                )
            models = tuple(model_routes)
        return RuntimeCapabilities(
            runtime_id=self.runtime_id,
            runtime_version=health.runtime_version,
            checked_at=self._now(),
            models=models,
            supported_capabilities=frozenset(
                capability
                for model in models
                if model.available
                for capability in model.capabilities
            ),
        )

    async def start_task(self, request: RuntimeTaskRequest) -> RuntimeTaskHandle:
        lock = self._idempotency_locks.setdefault(request.idempotency_key, asyncio.Lock())
        async with lock:
            existing_session = self._idempotency_sessions.get(request.idempotency_key)
            if existing_session is not None:
                state = self._tasks[existing_session]
                if state.request != request:
                    raise ValueError("idempotency key is bound to a different runtime request")
                return state.handle

            capabilities = await self.capabilities()
            self._assert_request_is_runnable(request, capabilities)
            request_sha256 = _request_digest(request)
            proposed_handle = _handle_for(request)
            store = self._state_store_or_raise()
            reservation = await store.reserve(
                idempotency_key=request.idempotency_key,
                request_sha256=request_sha256,
                proposed_handle=proposed_handle,
            )
            _assert_reservation(request, request_sha256, proposed_handle, reservation)
            state = _TaskState(
                request=request,
                request_sha256=request_sha256,
                handle=reservation.handle,
                started_at=self._now(),
                started_monotonic=monotonic(),
                events=[],
            )
            self._tasks[state.handle.session_id] = state
            self._idempotency_sessions[request.idempotency_key] = state.handle.session_id
            if not reservation.created:
                result = reservation.result or self._failure_result(
                    state, RuntimeTaskStatus.FAILED, "restart_reconciliation_required"
                )
                await self._publish_terminal(state, result)
                return state.handle

            await self._append_event(
                state,
                RuntimeEventCategory.LIFECYCLE,
                RuntimeTaskStatus.RUNNING,
                {"phase": "started"},
            )
            state.task = asyncio.create_task(
                self._run(state), name=f"nexus-openai-agents-{state.handle.session_id}"
            )
            return state.handle

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
        cursor = max(0, after_sequence)
        while True:
            async with state.condition:
                await state.condition.wait_for(
                    lambda: len(state.events) > cursor or state.completed
                )
                fresh = tuple(state.events[cursor:])
                cursor = len(state.events)
                completed = state.completed
            for event in fresh:
                yield event
            if completed and cursor == len(state.events):
                return

    async def poll_status(self, handle: RuntimeTaskHandle) -> RuntimeTaskStatus:
        # This adapter's state is already in-process/in-memory (no HTTP round
        # trip), so result() is already O(1) -- no separate cheap path needed.
        return (await self.result(handle)).status

    async def result(self, handle: RuntimeTaskHandle) -> RuntimeTaskResult:
        state = self._state(handle)
        return state.result if state.result is not None else self._running_result(state)

    async def cancel(self, request: CancelRequest) -> CancelResult:
        state = self._tasks.get(request.session_id)
        if (
            state is None
            or state.handle.experiment_id != request.experiment_id
            or state.handle.task_id != request.task_id
        ):
            return CancelResult(
                accepted=False,
                status=RuntimeTaskStatus.UNKNOWN,
                runtime_id=self.runtime_id,
                session_id=request.session_id,
                requested_at=self._now(),
                detail={"failure_category": "unknown_or_unbound_session"},
            )
        if not state.completed:
            if state.streamed_run is not None:
                state.streamed_run.cancel(mode="immediate")
            if state.task is not None:
                state.task.cancel()
            await self._finish_failure(state, RuntimeTaskStatus.CANCELLED, "cancelled")
            if state.task is not None:
                with suppress(asyncio.CancelledError):
                    await state.task
        status = state.result.status if state.result is not None else RuntimeTaskStatus.CANCELLED
        return CancelResult(
            accepted=True,
            status=status,
            runtime_id=self.runtime_id,
            session_id=request.session_id,
            requested_at=self._now(),
        )

    async def _run(self, state: _TaskState) -> None:
        try:
            if state.completed:
                return
            sdk = self._sdk_or_raise()
            agent = self._build_agent(sdk, state.request)
            run_config = sdk.RunConfig(
                tracing_disabled=True,
                trace_include_sensitive_data=False,
                tool_execution=sdk.ToolExecutionConfig(max_function_tool_concurrency=1),
            )
            state.streamed_run = sdk.Runner.run_streamed(
                agent,
                state.request.semantic_prompt,
                max_turns=_bounded_turns(state.request, self._config.max_turns_per_role),
                run_config=run_config,
            )
            timeout_seconds = _timeout_seconds(state.request, self._now())
            async with asyncio.timeout(timeout_seconds):
                async for event in state.streamed_run.stream_events():
                    await self._handle_sdk_event(state, event, sdk)
                    await self._check_live_usage(state)
            usage = _usage_from_result(state.streamed_run, require_complete=True)
            state.usage = usage
            self._reconcile_tool_usage(state, state.streamed_run.new_items, sdk)
            estimated_cost = self._assert_observed_budgets(state, usage)
            output_refs = await self._resolve_output_refs(
                state.request, state.streamed_run.final_output
            )
            result = RuntimeTaskResult(
                status=RuntimeTaskStatus.SUCCEEDED,
                runtime_id=self.runtime_id,
                runtime_version=self._runtime_version(),
                provider_id=state.request.provider_id,
                model_id=state.request.model_id,
                session_id=state.handle.session_id,
                message_id=state.handle.message_id,
                output_refs=output_refs,
                usage=RuntimeUsage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    billed_cost_usd=None,
                    normalized_cost_usd=estimated_cost,
                ),
                timings=self._timings(state, self._now()),
                tool_calls=state.tool_calls,
                tool_bytes=state.tool_bytes,
            )
            await self._append_event(
                state,
                RuntimeEventCategory.USAGE,
                None,
                _usage_event_payload(result.usage, state.tool_calls, state.tool_bytes),
            )
            await self._persist_and_publish_terminal(state, result)
        except asyncio.CancelledError:
            await self._finish_failure(state, RuntimeTaskStatus.CANCELLED, "cancelled")
        except TimeoutError:
            if state.streamed_run is not None:
                state.streamed_run.cancel(mode="immediate")
            logger.warning(
                "OpenAI Agents task timed out (session=%s, role=%s, provider=%s/%s)",
                state.handle.session_id,
                state.request.agent_id,
                state.request.provider_id,
                state.request.model_id,
            )
            await self._finish_failure(state, RuntimeTaskStatus.FAILED, "timeout")
        except Exception as exc:  # Never PERSIST provider exception text (DB/UI stays redacted).
            if state.streamed_run is not None:
                state.streamed_run.cancel(mode="immediate")
            # Operator-only diagnostics: server logs are not exposed through any
            # API or dashboard surface, unlike the failure_category persisted
            # below. This is the only place the real exception is observable.
            logger.exception(
                "OpenAI Agents task failed (session=%s, role=%s, provider=%s/%s, category=%s)",
                state.handle.session_id,
                state.request.agent_id,
                state.request.provider_id,
                state.request.model_id,
                _failure_category(exc),
            )
            await self._finish_failure(state, RuntimeTaskStatus.FAILED, _failure_category(exc))

    def _build_agent(self, sdk: Any, request: RuntimeTaskRequest) -> Any:
        tools = self._tools_for(request)
        model_settings = sdk.ModelSettings(
            max_tokens=request.budgets.max_output_tokens,
            parallel_tool_calls=False,
            store=False,
            include_usage=True,
        )
        kwargs: dict[str, Any] = {
            "name": _agent_name(request),
            "instructions": _agent_instructions(request),
            "model": (
                self._model_resolver(request.model_id)
                if self._model_resolver is not None
                else request.model_id
            ),
            "model_settings": model_settings,
            "tools": tools,
        }
        output_type = request.runtime_options.get("structured_output_type")
        if output_type is not None:
            kwargs["output_type"] = output_type
        return sdk.Agent(**kwargs)

    async def _handle_sdk_event(self, state: _TaskState, event: Any, sdk: Any) -> None:
        if not isinstance(event, sdk.RunItemStreamEvent):
            return
        item = event.item
        if isinstance(item, sdk.ToolCallItem):
            call_id = _tool_item_id(item)
            if call_id in state.tool_call_ids:
                return
            state.tool_call_ids.add(call_id)
            state.tool_calls += 1
            await self._append_event(
                state,
                RuntimeEventCategory.TOOL,
                None,
                {"phase": "called", "tool_name": _tool_name(item)},
            )
            self._assert_tool_budgets(state)
        elif isinstance(item, sdk.ToolCallOutputItem):
            call_id = _tool_item_id(item)
            if call_id in state.tool_output_ids:
                return
            state.tool_output_ids.add(call_id)
            returned_bytes = _output_byte_size(item.output)
            state.tool_bytes += returned_bytes
            await self._append_event(
                state,
                RuntimeEventCategory.TOOL,
                None,
                {"phase": "output", "returned_bytes": returned_bytes},
            )
            self._assert_tool_budgets(state)

    async def _check_live_usage(self, state: _TaskState) -> None:
        usage = _usage_from_result(state.streamed_run, require_complete=False)
        if usage.input_tokens is None:
            return
        if usage == state.usage:
            return
        state.usage = usage
        self._assert_observed_budgets(state, usage)
        await self._append_event(
            state,
            RuntimeEventCategory.USAGE,
            None,
            _usage_event_payload(usage, state.tool_calls, state.tool_bytes),
        )

    def _reconcile_tool_usage(self, state: _TaskState, items: Sequence[Any], sdk: Any) -> None:
        calls: dict[str, Any] = {}
        outputs: dict[str, Any] = {}
        for item in items:
            if isinstance(item, sdk.ToolCallItem):
                calls.setdefault(_tool_item_id(item), item)
            elif isinstance(item, sdk.ToolCallOutputItem):
                outputs.setdefault(_tool_item_id(item), item)
        state.tool_call_ids.update(calls)
        state.tool_output_ids.update(outputs)
        if state.tool_output_ids != state.tool_call_ids:
            raise _UntrustworthyUsage("SDK tool calls and outputs cannot be reconciled")
        state.tool_calls = len(state.tool_call_ids)
        state.tool_bytes = sum(_output_byte_size(item.output) for item in outputs.values())
        self._assert_tool_budgets(state)

    async def _finish_failure(
        self,
        state: _TaskState,
        status: RuntimeTaskStatus,
        category: str,
    ) -> None:
        async with state.finish_lock:
            if state.completed:
                return
            result = self._failure_result(state, status, category)
            try:
                await self._state_store_or_raise().complete(
                    idempotency_key=state.request.idempotency_key,
                    request_sha256=state.request_sha256,
                    result=result,
                )
            except Exception:
                result = self._failure_result(
                    state, RuntimeTaskStatus.FAILED, "task_state_store_failure"
                )
            await self._publish_terminal(state, result)

    async def _persist_and_publish_terminal(
        self,
        state: _TaskState,
        result: RuntimeTaskResult,
    ) -> None:
        async with state.finish_lock:
            if state.completed:
                return
            try:
                await self._state_store_or_raise().complete(
                    idempotency_key=state.request.idempotency_key,
                    request_sha256=state.request_sha256,
                    result=result,
                )
            except Exception:
                result = self._failure_result(
                    state, RuntimeTaskStatus.FAILED, "task_state_store_failure"
                )
            await self._publish_terminal(state, result)

    async def _publish_terminal(
        self,
        state: _TaskState,
        result: RuntimeTaskResult,
    ) -> None:
        state.result = result
        category = (
            RuntimeEventCategory.ERROR
            if result.status == RuntimeTaskStatus.FAILED
            else RuntimeEventCategory.LIFECYCLE
        )
        payload = (
            {"failure_category": result.failure_category}
            if result.failure_category
            else {"phase": "finished"}
        )
        await self._append_event(state, category, result.status, payload, complete=True)

    def _failure_result(
        self,
        state: _TaskState,
        status: RuntimeTaskStatus,
        category: str,
    ) -> RuntimeTaskResult:
        return RuntimeTaskResult(
            status=status,
            runtime_id=self.runtime_id,
            runtime_version=self._runtime_version(),
            provider_id=state.request.provider_id,
            model_id=state.request.model_id,
            session_id=state.handle.session_id,
            message_id=state.handle.message_id,
            usage=state.usage,
            timings=self._timings(state, self._now()),
            tool_calls=state.tool_calls,
            tool_bytes=state.tool_bytes,
            failure_category=category,
            failure_detail={"error_type": category},
        )

    def _assert_request_is_runnable(
        self,
        request: RuntimeTaskRequest,
        capabilities: RuntimeCapabilities,
    ) -> None:
        if capabilities.models == ():
            raise RuntimeError("OpenAI Agents runtime is unavailable or has no model routes")
        if (
            sha256(request.semantic_prompt.encode("utf-8")).hexdigest()
            != request.semantic_prompt_sha256
        ):
            raise ValueError("semantic prompt hash does not match the runtime request")
        if set(request.runtime_options) - {"max_turns", "structured_output_type"}:
            raise ValueError("runtime request contains unsupported OpenAI Agents options")
        if not request.allowed_tool_names or len(set(request.allowed_tool_names)) != len(
            request.allowed_tool_names
        ):
            raise ValueError("runtime task must declare a non-empty unique Nexus tool surface")
        if any(tool not in NEXUS_EXPERIMENT_TOOLS for tool in request.allowed_tool_names):
            raise ValueError("runtime task requested a tool outside the Nexus evidence surface")
        if (
            "structured-output" in request.required_capabilities
            and request.runtime_options.get("structured_output_type") is None
        ):
            raise ValueError("structured-output requires a declared structured_output_type")
        model = next(
            (
                candidate
                for candidate in capabilities.models
                if candidate.provider_id == request.provider_id
                and candidate.model_id == request.model_id
            ),
            None,
        )
        if (
            model is None
            or not model.available
            or not request.required_capabilities.issubset(model.capabilities)
            or "usage-reporting" not in model.capabilities
        ):
            raise ValueError(
                "requested provider/model is unavailable or lacks trustworthy required capabilities"
            )
        if request.budgets.max_billed_cost_usd is not None and self._cost_estimator is None:
            raise ValueError("a frozen cost estimator is required for a hard cost ceiling")
        if (
            request.budgets.max_input_tokens is not None
            and model.max_input_tokens is not None
            and request.budgets.max_input_tokens > model.max_input_tokens
        ):
            raise ValueError("input-token budget exceeds configured model capability")
        if (
            request.budgets.max_output_tokens is not None
            and model.max_output_tokens is not None
            and request.budgets.max_output_tokens > model.max_output_tokens
        ):
            raise ValueError("output-token budget exceeds configured model capability")
        if request.deadline_at <= self._now():
            raise ValueError("runtime task deadline has already elapsed")

    def _tools_for(self, request: RuntimeTaskRequest) -> list[Any]:
        if self._tool_factory is None:
            raise RuntimeError("Nexus tool factory is not configured")
        tools = list(
            self._tool_factory.build_tools(
                request=request, allowed_tool_names=request.allowed_tool_names
            )
        )
        tool_names = tuple(str(getattr(tool, "name", "")) for tool in tools)
        if set(tool_names) != set(request.allowed_tool_names) or len(tool_names) != len(
            request.allowed_tool_names
        ):
            raise ValueError("tool factory must expose exactly the authorized Nexus tools")
        return tools

    async def _resolve_output_refs(
        self,
        request: RuntimeTaskRequest,
        final_output: Any,
    ) -> tuple[ArtifactReference, ...]:
        if self._output_reference_resolver is None:
            raise RuntimeError("Nexus output-reference resolver is not configured")
        refs = self._output_reference_resolver(request, final_output)
        resolved = await refs if inspect.isawaitable(refs) else refs
        if not isinstance(resolved, tuple):
            raise ValueError("Nexus output-reference resolver must return a tuple")
        _validate_output_refs(request, resolved)
        return resolved

    def _assert_tool_budgets(self, state: _TaskState) -> None:
        budgets = state.request.budgets
        if budgets.max_tool_calls is not None and state.tool_calls > budgets.max_tool_calls:
            raise _RuntimeCeilingExceeded("tool_call_budget_exceeded")
        if budgets.max_tool_bytes is not None and state.tool_bytes > budgets.max_tool_bytes:
            raise _RuntimeCeilingExceeded("tool_byte_budget_exceeded")

    def _assert_observed_budgets(
        self,
        state: _TaskState,
        usage: RuntimeUsage,
    ) -> float | None:
        if any(
            value is None
            for value in (
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
            )
        ):
            raise _UntrustworthyUsage("provider usage is incomplete")
        budgets = state.request.budgets
        if budgets.max_input_tokens is not None and usage.input_tokens > budgets.max_input_tokens:
            raise _RuntimeCeilingExceeded("input_token_budget_exceeded")
        if (
            budgets.max_output_tokens is not None
            and usage.output_tokens > budgets.max_output_tokens
        ):
            raise _RuntimeCeilingExceeded("output_token_budget_exceeded")
        self._assert_tool_budgets(state)
        estimate: float | None = None
        if self._cost_estimator is not None:
            estimate = self._cost_estimator(state.request, usage)
            if estimate is None or not math.isfinite(estimate) or estimate < 0:
                raise _UntrustworthyUsage("cost estimate is unavailable or invalid")
        if budgets.max_billed_cost_usd is not None:
            if estimate is None:
                raise _UntrustworthyUsage("hard cost ceiling has no estimator")
            if estimate > budgets.max_billed_cost_usd:
                raise _RuntimeCeilingExceeded("cost_budget_exceeded")
        return estimate

    async def _append_event(
        self,
        state: _TaskState,
        category: RuntimeEventCategory,
        status: RuntimeTaskStatus | None,
        payload: Mapping[str, Any],
        *,
        complete: bool = False,
    ) -> None:
        async with state.condition:
            sequence = len(state.events) + 1
            state.events.append(
                RuntimeTaskEvent(
                    event_id=f"{state.handle.session_id}:{sequence}",
                    sequence=sequence,
                    category=category,
                    occurred_at=self._now(),
                    payload=dict(payload),
                    task_status=status,
                )
            )
            if complete:
                state.completed = True
            state.condition.notify_all()

    def _state(self, handle: RuntimeTaskHandle) -> _TaskState:
        state = self._tasks.get(handle.session_id)
        if state is None or state.handle != handle:
            raise ValueError("unknown runtime task handle")
        return state

    def _state_store_or_raise(self) -> RuntimeTaskStateStore:
        store = self._task_state_store
        if store is None or not getattr(store, "durable", False):
            raise RuntimeError("a durable runtime task-state store is required")
        return store

    def _unavailable_reason(self) -> str | None:
        if not self._config.enabled:
            return "runtime_disabled"
        if not self._api_key_available():
            return "api_key_not_configured"
        if self._task_state_store is None or not getattr(self._task_state_store, "durable", False):
            return "durable_task_state_store_not_configured"
        if self._tool_factory is None:
            return "nexus_tool_factory_not_configured"
        if self._output_reference_resolver is None:
            return "output_reference_resolver_not_configured"
        try:
            self._sdk_or_raise()
        except ModuleNotFoundError:
            return "sdk_not_installed"
        if self._runtime_version() != self._config.validated_sdk_version:
            return "unsupported_sdk_version"
        return None

    def _sdk_or_raise(self) -> Any:
        if self._sdk is None:
            self._sdk = importlib.import_module("agents")
        return self._sdk

    def _runtime_version(self) -> str | None:
        version = getattr(self._sdk, "__version__", None) if self._sdk is not None else None
        return str(version) if version is not None else None

    def _timings(self, state: _TaskState, completed_at: datetime) -> RuntimeTimings:
        return RuntimeTimings(
            started_at=state.started_at,
            completed_at=completed_at,
            elapsed_ms=max(0, int((monotonic() - state.started_monotonic) * 1000)),
        )

    def _running_result(self, state: _TaskState) -> RuntimeTaskResult:
        return RuntimeTaskResult(
            status=RuntimeTaskStatus.RUNNING,
            runtime_id=self.runtime_id,
            runtime_version=self._runtime_version(),
            provider_id=state.request.provider_id,
            model_id=state.request.model_id,
            session_id=state.handle.session_id,
            message_id=state.handle.message_id,
            usage=state.usage,
            timings=RuntimeTimings(started_at=state.started_at),
            tool_calls=state.tool_calls,
            tool_bytes=state.tool_bytes,
        )


def _handle_for(request: RuntimeTaskRequest) -> RuntimeTaskHandle:
    digest = sha256(request.idempotency_key.encode("utf-8")).hexdigest()[:24]
    return RuntimeTaskHandle(
        runtime_id=OpenAIAgentsRuntime.runtime_id,
        session_id=f"openai-agents-{digest}",
        message_id=None,
        experiment_id=request.experiment_id,
        task_id=request.task_id,
        idempotency_key=request.idempotency_key,
    )


def _request_digest(request: RuntimeTaskRequest) -> str:
    structured_type = request.runtime_options.get("structured_output_type")
    structured_name = (
        f"{structured_type.__module__}.{structured_type.__qualname__}"
        if isinstance(structured_type, type)
        else None
    )
    payload = {
        "experiment_id": request.experiment_id,
        "task_id": request.task_id,
        "idempotency_key": request.idempotency_key,
        "provider_id": request.provider_id,
        "model_id": request.model_id,
        "semantic_prompt_sha256": request.semantic_prompt_sha256,
        "context_refs": [asdict(reference) for reference in request.context_refs],
        "output_contract": asdict(request.output_contract),
        "allowed_tool_names": list(request.allowed_tool_names),
        "budgets": asdict(request.budgets),
        "deadline_at": request.deadline_at.isoformat(),
        "required_capabilities": sorted(request.required_capabilities),
        "agent_id": request.agent_id,
        "max_turns": request.runtime_options.get("max_turns"),
        "structured_output_type": structured_name,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def _assert_reservation(
    request: RuntimeTaskRequest,
    request_sha256: str,
    proposed_handle: RuntimeTaskHandle,
    reservation: RuntimeTaskReservation,
) -> None:
    handle = reservation.handle
    if (
        reservation.request_sha256 != request_sha256
        or handle != proposed_handle
        or handle.idempotency_key != request.idempotency_key
    ):
        raise ValueError("durable reservation does not match the runtime request")


def _agent_name(request: RuntimeTaskRequest) -> str:
    return f"Nexus {request.agent_id or 'nexus-role'}"[:128]


def _agent_instructions(request: RuntimeTaskRequest) -> str:
    return (
        "You are a bounded Nexus task role. Use only the provided Nexus evidence tools. "
        "Do not request web, shell, filesystem, hosted, handoff, or external tools. "
        "First read the task contract, then write every artifact it declares and submit "
        f"the task. Nexus will derive {request.output_contract.artifact_name} as the "
        "immutable receipt over those declared artifacts."
    )


def _bounded_turns(request: RuntimeTaskRequest, configured_limit: int) -> int:
    requested = request.runtime_options.get("max_turns", configured_limit)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("runtime max_turns must be a positive integer")
    return min(requested, configured_limit)


def _timeout_seconds(request: RuntimeTaskRequest, now: datetime) -> float:
    deadline_seconds = (request.deadline_at - now).total_seconds()
    budget_seconds = (
        request.budgets.max_elapsed_ms / 1000
        if request.budgets.max_elapsed_ms is not None
        else deadline_seconds
    )
    timeout = min(deadline_seconds, budget_seconds)
    if timeout <= 0:
        raise ValueError("runtime task timeout has elapsed")
    return timeout


def _usage_from_result(result: Any, *, require_complete: bool) -> RuntimeUsage:
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    requests = _nonnegative_int(_field(usage, "requests"))
    input_tokens = _nonnegative_int(_field(usage, "input_tokens"))
    output_tokens = _nonnegative_int(_field(usage, "output_tokens"))
    details = _field(usage, "input_tokens_details")
    cache_read = _nonnegative_int(_field(details, "cached_tokens"))
    cache_write = _nonnegative_int(_field(details, "cache_write_tokens"))
    complete = (
        requests is not None
        and requests > 0
        and input_tokens is not None
        and input_tokens > 0
        and output_tokens is not None
        and cache_read is not None
        and cache_write is not None
    )
    if not complete:
        if require_complete:
            raise _UntrustworthyUsage("provider did not report complete cumulative usage")
        return RuntimeUsage()
    return RuntimeUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def _tool_item_id(item: Any) -> str:
    raw = getattr(item, "raw_item", None)
    value = _field(raw, "call_id") or _field(raw, "id")
    if not isinstance(value, str) or not value:
        raise _UntrustworthyUsage("SDK tool item omitted its call id")
    return value


def _tool_name(item: Any) -> str:
    value = _field(getattr(item, "raw_item", None), "name")
    return str(value) if value else "nexus_tool"


def _output_byte_size(output: Any) -> int:
    if isinstance(output, bytes):
        return len(output)
    if isinstance(output, str):
        return len(output.encode("utf-8"))
    if hasattr(output, "model_dump"):
        output = output.model_dump(mode="json")
    try:
        return len(
            json.dumps(output, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise _UntrustworthyUsage("SDK tool output cannot be byte-accounted") from exc


def _validate_output_refs(
    request: RuntimeTaskRequest,
    references: tuple[ArtifactReference, ...],
) -> None:
    contract = request.output_contract
    names = [reference.logical_name for reference in references]
    if len(names) != len(set(names)):
        raise _InvalidArtifactReceipt("Nexus artifact receipts must be unique")
    matching = [
        reference for reference in references if reference.logical_name == contract.artifact_name
    ]
    if contract.required and len(matching) != 1:
        raise _InvalidArtifactReceipt("required Nexus artifact receipt is missing or duplicated")
    if any(reference.logical_name != contract.artifact_name for reference in references):
        raise _InvalidArtifactReceipt("Nexus artifact receipt is outside the output contract")
    for reference in references:
        if (
            reference.media_type != contract.media_type
            or len(reference.sha256) != 64
            or any(character not in "0123456789abcdef" for character in reference.sha256)
            or reference.byte_size is None
            or isinstance(reference.byte_size, bool)
            or not isinstance(reference.byte_size, int)
            or reference.byte_size < 0
            or (contract.max_bytes is not None and reference.byte_size > contract.max_bytes)
        ):
            raise _InvalidArtifactReceipt(
                "Nexus artifact receipt does not satisfy the output contract"
            )


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage_event_payload(
    usage: RuntimeUsage,
    tool_calls: int,
    tool_bytes: int,
) -> dict[str, int | None]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "tool_calls": tool_calls,
        "tool_bytes": tool_bytes,
    }


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, _RuntimeCeilingExceeded):
        return "budget_exceeded"
    if isinstance(exc, _UntrustworthyUsage):
        return "usage_unavailable"
    if isinstance(exc, _InvalidArtifactReceipt):
        return "invalid_artifact_receipt"
    if isinstance(exc, PermissionError):
        return "grounded_contract_violation"
    name = type(exc).__name__.casefold()
    if "maxturn" in name:
        return "max_turns_exceeded"
    if "output" in name or "schema" in name or "validation" in name:
        return "invalid_output"
    if "tool" in name:
        return "tool_failure"
    return "sdk_error"
