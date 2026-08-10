from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server.experiments.domain.runtime import (
    AgentRuntime,
    ArtifactReference,
    CancelRequest,
    OutputContract,
    RuntimeBudgets,
    RuntimeTaskHandle,
    RuntimeTaskRequest,
    RuntimeTaskResult,
    RuntimeTaskStatus,
)
from mcp_server.experiments.application.grounded_runtime import (
    BOUNDED_BUDGET_MONITORING_CAPABILITY,
    GroundedRoleRuntimeContext,
)
from mcp_server.experiments.application.grounded_pack import (
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    GroundedRuntimeRunner,
    _reset_role_owned_artifacts,
)
from mcp_server.experiments.infrastructure.openai_agents.composition import (
    compose_openai_agents_grounded_runtime,
)
from mcp_server.experiments.infrastructure.openai_agents.grounded import (
    OpenAIAgentsGroundedTaskBuilder,
)
from mcp_server.experiments.infrastructure.openai_agents.runtime import (
    OpenAIAgentModel,
    OpenAIAgentsRuntime,
    OpenAIAgentsRuntimeConfig,
    RuntimeTaskReservation,
)
from mcp_server.experiments.infrastructure.openai_agents.state_store import (
    FileRuntimeTaskStateStore,
)
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.mcp_tools.config_compiler import NEXUS_EXPERIMENT_TOOLS
from mcp_server.config import MCPServerConfig
from mcp_server.experiments.infrastructure.openai_agents.preflight import (
    verify_openai_agents_route,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DEFAULT_ESTIMATOR = object()


@dataclass
class _Tool:
    name: str


class _ToolFactory:
    def build_tools(self, *, request: RuntimeTaskRequest, allowed_tool_names: tuple[str, ...]):
        return [_Tool(name) for name in allowed_tool_names]


class _DurableStore:
    durable = True

    def __init__(self) -> None:
        self.records: dict[str, RuntimeTaskReservation] = {}

    async def reserve(self, *, idempotency_key, request_sha256, proposed_handle):
        existing = self.records.get(idempotency_key)
        if existing is not None:
            if existing.request_sha256 != request_sha256:
                raise ValueError("idempotency request mismatch")
            return replace(existing, created=False)
        reservation = RuntimeTaskReservation(
            handle=proposed_handle,
            request_sha256=request_sha256,
            created=True,
        )
        self.records[idempotency_key] = reservation
        return reservation

    async def complete(self, *, idempotency_key, request_sha256, result):
        existing = self.records[idempotency_key]
        if existing.request_sha256 != request_sha256:
            raise ValueError("completion request mismatch")
        self.records[idempotency_key] = replace(existing, result=result)


class _Agent:
    last_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        type(self).last_kwargs = kwargs


class _ModelSettings:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _RunConfig:
    last_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs


class _ToolExecutionConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FunctionTool:
    def __init__(self, **kwargs: Any) -> None:
        self.name = kwargs["name"]
        self.kwargs = kwargs


@dataclass
class _RawToolCall:
    call_id: str
    name: str
    id: str | None = None


@dataclass
class _RawToolOutput:
    call_id: str


@dataclass
class _ToolCallItem:
    raw_item: _RawToolCall
    type: str = "tool_call_item"


@dataclass
class _ToolCallOutputItem:
    raw_item: _RawToolOutput
    output: Any
    type: str = "tool_call_output_item"


@dataclass
class _RunItemStreamEvent:
    name: str
    item: Any
    type: str = "run_item_stream_event"


@dataclass
class _InputDetails:
    cached_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class _Usage:
    requests: int = 1
    input_tokens: int = 7
    output_tokens: int = 3
    input_tokens_details: _InputDetails = field(default_factory=lambda: _InputDetails(2, 4))


class _StreamedResult:
    def __init__(
        self,
        *,
        events: list[Any],
        new_items: list[Any],
        usage: _Usage | None = None,
        gate_after_first: asyncio.Event | None = None,
    ) -> None:
        self._events = events
        self.new_items = new_items
        self.final_output = _StructuredOutput()
        self.context_wrapper = SimpleNamespace(usage=usage or _Usage())
        self.gate_after_first = gate_after_first
        self.cancelled = False

    async def stream_events(self):
        for index, event in enumerate(self._events):
            yield event
            if index == 0 and self.gate_after_first is not None:
                await self.gate_after_first.wait()

    def cancel(self, mode="immediate") -> None:
        assert mode == "immediate"
        self.cancelled = True


class _Runner:
    next_stream: _StreamedResult | None = None
    calls: list[dict[str, Any]] = []

    @classmethod
    def run_streamed(cls, agent: Any, prompt: str, **kwargs: Any) -> _StreamedResult:
        cls.calls.append({"agent": agent, "prompt": prompt, **kwargs})
        assert cls.next_stream is not None
        return cls.next_stream


class _OpenAIProvider:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def get_model(self, model_id: str) -> Any:
        return SimpleNamespace(model_id=model_id)


class _SDK:
    __version__ = "0.19.3"
    Agent = _Agent
    ModelSettings = _ModelSettings
    RunConfig = _RunConfig
    ToolExecutionConfig = _ToolExecutionConfig
    FunctionTool = _FunctionTool
    Runner = _Runner
    RunItemStreamEvent = _RunItemStreamEvent
    ToolCallItem = _ToolCallItem
    ToolCallOutputItem = _ToolCallOutputItem
    OpenAIProvider = _OpenAIProvider


class _StructuredOutput:
    pass


def _tool_items(output: Any = "ok") -> tuple[_ToolCallItem, _ToolCallOutputItem]:
    return (
        _ToolCallItem(_RawToolCall(call_id="call-1", name="nexus_experiment_get_contract")),
        _ToolCallOutputItem(_RawToolOutput(call_id="call-1"), output),
    )


def _stream(
    *,
    usage: _Usage | None = None,
    duplicate_call: bool = False,
    gate_after_first: asyncio.Event | None = None,
    output: Any = "ok",
) -> _StreamedResult:
    call, tool_output = _tool_items(output)
    items = [call, call, tool_output] if duplicate_call else [call, tool_output]
    events = [
        _RunItemStreamEvent("tool_called", item)
        if isinstance(item, _ToolCallItem)
        else _RunItemStreamEvent("tool_output", item)
        for item in items
    ]
    return _StreamedResult(
        events=events,
        new_items=items,
        usage=usage,
        gate_after_first=gate_after_first,
    )


def _request(**overrides: Any) -> RuntimeTaskRequest:
    values: dict[str, Any] = {
        "experiment_id": "experiment-1",
        "task_id": "role-1",
        "idempotency_key": "request-1",
        "provider_id": "openai",
        "model_id": "gpt-test",
        "semantic_prompt": "Read the evidence and write the bounded artifact.",
        "semantic_prompt_sha256": "af0b7a57d15b355a8f4dac0cb5cb5d78b0e46b61edac97d2a15a9eb39f120f1e",
        "context_refs": (),
        "output_contract": OutputContract("result.md", "text/markdown", max_bytes=1024),
        "allowed_tool_names": ("nexus_experiment_get_contract",),
        "budgets": RuntimeBudgets(
            max_input_tokens=10,
            max_output_tokens=5,
            max_tool_calls=2,
            max_tool_bytes=20,
            max_elapsed_ms=60_000,
            max_billed_cost_usd=0.01,
        ),
        "deadline_at": NOW + timedelta(hours=1),
        "required_capabilities": frozenset(
            {"tools", "structured-output", "usage-reporting", "cost-accounting"}
        ),
        "agent_id": "writer",
        "runtime_options": {"max_turns": 20, "structured_output_type": _StructuredOutput},
    }
    values.update(overrides)
    return RuntimeTaskRequest(**values)


def _runtime(
    *,
    store: _DurableStore | None = None,
    output_reference_resolver=None,
    cost_estimator=_DEFAULT_ESTIMATOR,
) -> OpenAIAgentsRuntime:
    config = OpenAIAgentsRuntimeConfig(
        enabled=True,
        models=(OpenAIAgentModel("gpt-test", max_input_tokens=100, max_output_tokens=50),),
        max_turns_per_role=3,
    )
    return OpenAIAgentsRuntime(
        config=config,
        tool_factory=_ToolFactory(),
        output_reference_resolver=output_reference_resolver
        or (
            lambda request, output: (
                ArtifactReference(
                    request.output_contract.artifact_name,
                    "b" * 64,
                    request.output_contract.media_type,
                    12,
                ),
            )
        ),
        task_state_store=store or _DurableStore(),
        cost_estimator=(
            (lambda request, usage: 0.001)
            if cost_estimator is _DEFAULT_ESTIMATOR
            else cost_estimator
        ),
        sdk=_SDK,
        api_key_available=lambda: True,
        now=lambda: NOW,
    )


@pytest.fixture(autouse=True)
def _reset_runner():
    _Runner.calls = []
    _Runner.next_stream = _stream()


@pytest.mark.asyncio
async def test_disabled_runtime_is_startup_safe_without_sdk_key_or_wiring():
    runtime = OpenAIAgentsRuntime()

    health = await runtime.health()
    capabilities = await runtime.capabilities()

    assert health.availability == "unavailable"
    assert health.detail == {"failure_category": "runtime_disabled"}
    assert capabilities.models == ()
    assert isinstance(runtime, AgentRuntime)


@pytest.mark.asyncio
async def test_streamed_sdk_run_is_bounded_private_and_accounts_real_item_shapes_once():
    _Runner.next_stream = _stream(duplicate_call=True, output={"status": "ok"})
    runtime = _runtime()
    request = _request()

    handle = await runtime.start_task(request)
    events = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)

    assert _Runner.calls[0]["max_turns"] == 3
    assert _Agent.last_kwargs is not None
    settings = _Agent.last_kwargs["model_settings"].kwargs
    assert settings == {
        "max_tokens": 5,
        "parallel_tool_calls": False,
        "store": False,
        "include_usage": True,
    }
    assert _RunConfig.last_kwargs is not None
    assert _RunConfig.last_kwargs["tracing_disabled"] is True
    assert _RunConfig.last_kwargs["trace_include_sensitive_data"] is False
    assert _RunConfig.last_kwargs["tool_execution"].kwargs == {"max_function_tool_concurrency": 1}
    assert result.status == RuntimeTaskStatus.SUCCEEDED
    assert result.usage.input_tokens == 7
    assert result.usage.cache_read_tokens == 2
    assert result.usage.cache_write_tokens == 4
    assert result.usage.normalized_cost_usd == 0.001
    assert result.tool_calls == 1
    assert result.tool_bytes == len('{"status":"ok"}'.encode())
    assert events[-1].task_status == RuntimeTaskStatus.SUCCEEDED
    assert request.semantic_prompt not in str(events)


@pytest.mark.asyncio
async def test_observe_yields_live_events_before_the_run_is_terminal():
    gate = asyncio.Event()
    _Runner.next_stream = _stream(gate_after_first=gate)
    runtime = _runtime()
    handle = await runtime.start_task(_request())
    observer = runtime.observe(handle)

    started = await observer.__anext__()
    called = await observer.__anext__()
    running = await runtime.result(handle)

    assert started.task_status == RuntimeTaskStatus.RUNNING
    assert called.category == "tool"
    assert called.payload == {
        "phase": "called",
        "tool_name": "nexus_experiment_get_contract",
    }
    assert running.status == RuntimeTaskStatus.RUNNING

    gate.set()
    remaining = [event async for event in observer]
    assert remaining[-1].task_status == RuntimeTaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_immediate_cancel_before_runner_coroutine_starts_is_terminal_and_observable():
    runtime = _runtime()
    handle = await runtime.start_task(_request())

    cancelled = await runtime.cancel(
        CancelRequest("experiment-1", "role-1", handle.session_id, "immediate")
    )
    events = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)

    assert cancelled.accepted is True
    assert cancelled.status == RuntimeTaskStatus.CANCELLED
    assert result.status == RuntimeTaskStatus.CANCELLED
    assert events[-1].task_status == RuntimeTaskStatus.CANCELLED
    assert len([event for event in events if event.task_status == RuntimeTaskStatus.CANCELLED]) == 1


@pytest.mark.asyncio
async def test_durable_reservation_blocks_duplicate_provider_traffic_after_restart():
    store = _DurableStore()
    gate = asyncio.Event()
    first_stream = _stream(gate_after_first=gate)
    _Runner.next_stream = first_stream
    first_runtime = _runtime(store=store)
    request = _request()
    first_handle = await first_runtime.start_task(request)
    await asyncio.sleep(0)
    assert len(_Runner.calls) == 1

    second_runtime = _runtime(store=store)
    second_handle = await second_runtime.start_task(request)
    second_result = await second_runtime.result(second_handle)

    assert second_handle == first_handle
    assert len(_Runner.calls) == 1
    assert second_result.status == RuntimeTaskStatus.FAILED
    assert second_result.failure_category == "restart_reconciliation_required"

    await first_runtime.cancel(
        CancelRequest("experiment-1", "role-1", first_handle.session_id, "test cleanup")
    )


@pytest.mark.asyncio
async def test_missing_usage_and_live_budget_breach_cancel_the_stream_fail_closed():
    missing = _Usage(requests=0, input_tokens=0, output_tokens=0)
    missing_stream = _stream(usage=missing)
    _Runner.next_stream = missing_stream
    runtime = _runtime()
    handle = await runtime.start_task(_request())
    _ = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "usage_unavailable"
    assert missing_stream.cancelled is True

    over_stream = _stream(usage=_Usage(input_tokens=11))
    _Runner.next_stream = over_stream
    runtime = _runtime()
    over_request = _request(idempotency_key="request-over")
    handle = await runtime.start_task(over_request)
    _ = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "budget_exceeded"
    assert over_stream.cancelled is True
    assert result.retry_count == 0

    cost_stream = _stream()
    _Runner.next_stream = cost_stream
    runtime = _runtime(cost_estimator=lambda request, usage: 0.02)
    cost_request = _request(idempotency_key="request-cost-over")
    handle = await runtime.start_task(cost_request)
    _ = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)
    assert result.failure_category == "budget_exceeded"
    assert cost_stream.cancelled is True


@pytest.mark.asyncio
async def test_rejects_untrusted_route_non_nexus_tools_and_missing_cost_preflight():
    runtime = _runtime()
    with pytest.raises(ValueError, match="outside the Nexus evidence surface"):
        await runtime.start_task(_request(allowed_tool_names=("web_search",)))
    with pytest.raises(ValueError, match="provider/model"):
        await runtime.start_task(_request(provider_id="other"))

    config = OpenAIAgentsRuntimeConfig(
        enabled=True,
        models=(OpenAIAgentModel("gpt-test", trustworthy_usage=False),),
    )
    unavailable = OpenAIAgentsRuntime(
        config=config,
        tool_factory=_ToolFactory(),
        output_reference_resolver=lambda request, output: (),
        task_state_store=_DurableStore(),
        sdk=_SDK,
        api_key_available=lambda: True,
        now=lambda: NOW,
    )
    with pytest.raises(ValueError, match="trustworthy"):
        await unavailable.start_task(_request(required_capabilities=frozenset({"tools"})))

    no_cost = _runtime(cost_estimator=None)
    with pytest.raises(ValueError, match="cost estimator"):
        await no_cost.start_task(
            _request(
                idempotency_key="no-cost",
                required_capabilities=frozenset({"tools", "structured-output", "usage-reporting"}),
            )
        )


@pytest.mark.asyncio
async def test_invalid_artifact_receipt_fails_at_adapter_boundary():
    runtime = _runtime(
        output_reference_resolver=lambda request, output: (
            ArtifactReference("wrong.md", "not-a-hash", "text/plain", None),
        )
    )
    handle = await runtime.start_task(_request())
    _ = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "invalid_artifact_receipt"
    assert result.output_refs == ()


@pytest.mark.asyncio
async def test_completed_durable_result_replays_after_restart_without_provider_traffic():
    store = _DurableStore()
    first = _runtime(store=store)
    request = _request()
    handle = await first.start_task(request)
    _ = [event async for event in first.observe(handle)]
    expected: RuntimeTaskResult = await first.result(handle)
    assert expected.status == RuntimeTaskStatus.SUCCEEDED
    assert len(_Runner.calls) == 1

    second = _runtime(store=store)
    replayed_handle = await second.start_task(request)
    replayed_events = [event async for event in second.observe(replayed_handle)]
    replayed = await second.result(replayed_handle)

    assert replayed == expected
    assert replayed_events[-1].task_status == RuntimeTaskStatus.SUCCEEDED
    assert len(_Runner.calls) == 1


@pytest.mark.asyncio
async def test_read_only_preflight_verifies_streaming_usage_and_cost_capabilities():
    runtime = _runtime()

    route = await verify_openai_agents_route(
        runtime,
        provider_id="openai",
        model_id="gpt-test",
        required_capabilities=frozenset({"tools", "structured-output", "cost-accounting"}),
    )

    assert route.runtime_version == "0.19.3"
    assert route.capabilities >= {
        "tools",
        "structured-output",
        "streaming",
        "usage-reporting",
        "cost-accounting",
        BOUNDED_BUDGET_MONITORING_CAPABILITY,
    }
    assert _Runner.calls == []


@pytest.mark.asyncio
async def test_bounded_monitoring_is_withheld_without_trusted_cumulative_usage():
    config = OpenAIAgentsRuntimeConfig(
        enabled=True,
        models=(
            OpenAIAgentModel(
                "gpt-test",
                capabilities=frozenset({BOUNDED_BUDGET_MONITORING_CAPABILITY}),
            ),
        ),
    )
    runtime = OpenAIAgentsRuntime(
        config=config,
        tool_factory=_ToolFactory(),
        output_reference_resolver=lambda request, output: (),
        task_state_store=_DurableStore(),
        sdk=_SDK,
        api_key_available=lambda: True,
        now=lambda: NOW,
    )

    capabilities = await runtime.capabilities()

    assert capabilities.models[0].available is False
    assert BOUNDED_BUDGET_MONITORING_CAPABILITY not in capabilities.models[0].capabilities
    assert BOUNDED_BUDGET_MONITORING_CAPABILITY not in capabilities.supported_capabilities


@pytest.mark.asyncio
async def test_default_off_composition_statically_selects_only_allowlisted_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    settings = MCPServerConfig(
        agentic_grounded_runtime_id="opencode",
        agentic_openai_agents_enabled=True,
        agentic_openai_agents_provider_id="openai",
        agentic_openai_agents_model_ids="gpt-test",
        model_pricing_json=json.dumps(
            {"gpt-test": {"input": 1.0, "output": 2.0}}
        ),
    )

    composed = await compose_openai_agents_grounded_runtime(
        settings=settings,
        provider_id="openai",
        model_id="gpt-test",
        workspace_root=tmp_path,
        sdk=_SDK,
    )

    assert composed.verified_route.runtime_id == "openai-agents"
    assert BOUNDED_BUDGET_MONITORING_CAPABILITY in composed.verified_route.capabilities
    assert _Runner.calls == []
    with pytest.raises(ValueError, match="operator-allowed"):
        await compose_openai_agents_grounded_runtime(
            settings=settings,
            provider_id="openai",
            model_id="not-allowed",
            workspace_root=tmp_path,
            sdk=_SDK,
        )


@pytest.mark.asyncio
async def test_file_task_state_store_replays_completed_result_after_restart(
    tmp_path: Path,
):
    store = FileRuntimeTaskStateStore(tmp_path / "state")
    handle = RuntimeTaskHandle(
        runtime_id="openai-agents",
        session_id="session-1",
        message_id=None,
        experiment_id="experiment-1",
        task_id="task-1",
        idempotency_key="idempotency-1",
    )
    first = await store.reserve(
        idempotency_key="idempotency-1",
        request_sha256="a" * 64,
        proposed_handle=handle,
    )
    result = RuntimeTaskResult(
        status=RuntimeTaskStatus.SUCCEEDED,
        runtime_id="openai-agents",
        runtime_version="0.19.3",
        provider_id="openai",
        model_id="gpt-test",
        session_id="session-1",
        message_id=None,
    )
    await store.complete(
        idempotency_key="idempotency-1",
        request_sha256="a" * 64,
        result=result,
    )

    replayed = await FileRuntimeTaskStateStore(tmp_path / "state").reserve(
        idempotency_key="idempotency-1",
        request_sha256="a" * 64,
        proposed_handle=handle,
    )

    assert first.created is True
    assert replayed.created is False
    assert replayed.result == result


def _grounded_pack():
    root = Path(__file__).parents[2] / "data" / "agentic_recipes"
    return load_grounded_execution_pack(load_recipe(root / "logistics_operations" / "recipe.yaml"))


def _write_grounded_outputs(root: Path, role_id: str) -> None:
    outputs = {
        "shipment-evidence": {
            "candidate/orchestration/evidence_packets/shipments.json": '{"claims":[]}',
        },
        "operations-analysis": {
            "candidate/intermediates/operations_analysis.md": "# Analysis\n\nGrounded static analysis.\n",
        },
        "operations-writer": {
            "candidate/docs/operations_runbook.md": "# Operations\n\nGrounded in the frozen source set.\n",
            "candidate/docs/exception_inventory.md": "# Exceptions\n\nGrounded in the frozen source set.\n",
        },
        "operations-reviewer": {
            "candidate/orchestration/review_report.md": "\n".join(
                (
                    "## Factual review",
                    "Passed.",
                    "## Coverage review",
                    "Passed.",
                    "## Cross-document consistency",
                    "Passed.",
                    "## Secret and evidence-path review",
                    "Passed.",
                    "## Resolved findings",
                    "None.",
                    "## Final verdict",
                    "Pass.",
                )
            ),
        },
    }
    for logical_name, content in outputs[role_id].items():
        destination = root.joinpath(*logical_name.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_grounded_runner_executes_end_to_end_through_openai_agents_fake_sdk(
    tmp_path: Path,
):
    now = datetime.now(timezone.utc)
    experiment_id = "00000000-0000-4000-8000-000000000301"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "shipment-events.txt"
    source.write_text("shipment A arrived\n", encoding="utf-8")
    builder = OpenAIAgentsGroundedTaskBuilder(now=lambda: now, sdk=_SDK)
    requests: list[RuntimeTaskRequest] = []

    def resolve(request: RuntimeTaskRequest, output: Any):
        requests.append(request)
        role_id = request.task_id.split(":")[1]
        _write_grounded_outputs(
            workspace_root / experiment_id / "roles" / role_id,
            role_id,
        )
        return builder.resolve_output_references(request, output)

    runtime = OpenAIAgentsRuntime(
        config=OpenAIAgentsRuntimeConfig(
            enabled=True,
            models=(OpenAIAgentModel("gpt-test"),),
        ),
        tool_factory=builder,
        output_reference_resolver=resolve,
        task_state_store=_DurableStore(),
        sdk=_SDK,
        api_key_available=lambda: True,
        now=lambda: now,
    )

    result = await GroundedRuntimeRunner(
        runtime=runtime,
        task_builder=builder,
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_grounded_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=(("upload:events/shipment-events.txt", source),),
        provider_id="openai",
        model_id="gpt-test",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )

    assert len(_Runner.calls) == 4
    assert len(requests) == 4
    assert result.input_tokens == 28
    assert result.output_tokens == 12
    assert all(call["max_turns"] == 8 for call in _Runner.calls)
    for call in _Runner.calls:
        assert tuple(tool.name for tool in call["agent"].kwargs["tools"]) == (
            NEXUS_EXPERIMENT_TOOLS
        )
    assert all(request.context_refs for request in requests)
    assert all(
        not any(
            reference.logical_name.startswith("candidate/") for reference in request.context_refs
        )
        for request in requests
    )
    assert all(not request.runtime_options for request in requests)
    assert all(
        BOUNDED_BUDGET_MONITORING_CAPABILITY in request.required_capabilities
        for request in requests
    )
    assert all(request.semantic_prompt.startswith("Nexus tool binding:") for request in requests)
    manifest = next(
        artifact
        for artifact in result.artifacts
        if artifact.logical_name == "receipts/grounded_execution.json"
    )
    payload = json.loads(
        (
            tmp_path
            / "output"
            / "logistics"
            / "experiments"
            / experiment_id
            / manifest.relative_path
        ).read_text(encoding="utf-8")
    )
    assert payload["runtime"] == "openai-agents/0.19.3"
    assert all(role["session_id"].startswith("openai-agents-") for role in payload["role_sessions"])


@pytest.mark.asyncio
async def test_repair_attempt_rewrites_an_owned_artifact_after_reset(tmp_path: Path):
    """Reproduces today's production incident and proves the fix.

    A repair attempt re-prompts the same role against the same on-disk
    ``role_root``/packet_root. The real ``nexus_experiment_write_artifact``
    tool writes exclusively (write-once) -- without clearing the role's own
    owned-artifact files first, attempt 2's write collides with the file
    attempt 1 already wrote and raises ``PermissionError``, exactly as seen
    in production. ``_reset_role_owned_artifacts`` (called by
    ``GroundedRuntimeRunner`` at the top of a repair attempt) fixes that.
    """
    role_root = tmp_path / "roles" / "evidence-structure"
    (role_root / "input").mkdir(parents=True)
    (role_root / "input" / "FACT_LEDGER.json").write_text("{}", encoding="utf-8")
    (role_root / "PACKET.json").write_text("{}", encoding="utf-8")
    owned_artifacts = ("candidate/output.json",)
    builder = OpenAIAgentsGroundedTaskBuilder(now=lambda: NOW, sdk=_SDK)

    def build_request(attempt: int) -> RuntimeTaskRequest:
        return builder.build(
            GroundedRoleRuntimeContext(
                experiment_id="experiment-1",
                role_id="evidence-structure",
                agent_id="nexus-doc-evidence",
                prompt="Write the evidence packet artifact.",
                packet_root=role_root,
                runtime_directory=tmp_path / "runtime",
                owned_artifacts=owned_artifacts,
                provider_id="openai",
                model_id="gpt-test",
                attempt=attempt,
                max_context_bytes=50_000,
                max_input_tokens=1_000,
                max_tool_calls=10,
                timeout_seconds=60,
            )
        )

    async def write_artifact(request: RuntimeTaskRequest, content: str) -> None:
        tools = builder.build_tools(request=request, allowed_tool_names=NEXUS_EXPERIMENT_TOOLS)
        tool = next(tool for tool in tools if tool.name == "nexus_experiment_write_artifact")
        await tool.kwargs["on_invoke_tool"](
            None,
            json.dumps(
                {
                    "logical_name": "candidate/output.json",
                    "media_type": "application/json",
                    "content": content,
                }
            ),
        )

    output_path = role_root / "candidate" / "output.json"
    attempt_1 = build_request(1)
    await write_artifact(attempt_1, '{"claimed": "wrong"}')
    assert output_path.read_text(encoding="utf-8") == '{"claimed": "wrong"}'

    # Nexus's own acceptance check rejected attempt 1 (not modeled here --
    # this test starts from that rejection) and triggers a repair. The repair
    # reuses the exact same role_root/packet_root.
    attempt_2 = build_request(2)
    with pytest.raises(PermissionError, match="write-once"):
        await write_artifact(attempt_2, '{"claimed": "corrected"}')

    # A downstream role's already-accepted dependency artifact lives under
    # input/accepted, never under candidate/ -- the reset must never touch it.
    accepted_marker = role_root / "input" / "accepted" / "structure.json"
    accepted_marker.parent.mkdir(parents=True, exist_ok=True)
    accepted_marker.write_text('{"upstream": true}', encoding="utf-8")

    _reset_role_owned_artifacts(role_root, owned_artifacts)

    assert accepted_marker.read_text(encoding="utf-8") == '{"upstream": true}'
    await write_artifact(attempt_2, '{"claimed": "corrected"}')
    assert output_path.read_text(encoding="utf-8") == '{"claimed": "corrected"}'
