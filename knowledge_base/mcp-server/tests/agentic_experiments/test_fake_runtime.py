from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mcp_server.experiments.domain.runtime import (
    ArtifactReference,
    CancelRequest,
    ModelCapability,
    OutputContract,
    RuntimeBudgets,
    RuntimeEventCategory,
    RuntimeTaskRequest,
    RuntimeTaskStatus,
    RuntimeUsage,
)
from mcp_server.experiments.infrastructure.runtime.fake import (
    FakeAgentRuntime,
    FakeRuntimeScenario,
    FakeToolCall,
)


def _request(*, task_id: str = "doc-09", idempotency_key: str = "request-1") -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        experiment_id="experiment-1",
        task_id=task_id,
        idempotency_key=idempotency_key,
        provider_id="deepseek",
        model_id="deepseek-v4-pro",
        semantic_prompt="Bounded task.",
        semantic_prompt_sha256="a" * 64,
        context_refs=(ArtifactReference("evidence", "b" * 64, "application/json", 8),),
        output_contract=OutputContract("09_EXECUTIVE_SUMMARY.md", "text/markdown"),
        allowed_tool_names=("nexus_experiment_read_evidence",),
        budgets=RuntimeBudgets(max_input_tokens=100, max_output_tokens=50, max_elapsed_ms=1000),
        deadline_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_fake_runtime_returns_deterministic_success_events_usage_and_tools():
    output = ArtifactReference("document", "c" * 64, "text/markdown", 12)
    scenario = FakeRuntimeScenario(
        output_refs=(output,),
        usage=RuntimeUsage(
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=2,
            billed_cost_usd=0.02,
            normalized_cost_usd=0.03,
        ),
        elapsed_ms=45,
        provider_latency_ms=30,
        retry_count=1,
        tool_calls=(FakeToolCall("nexus_experiment_read_evidence", 12),),
    )
    runtime = FakeAgentRuntime(
        models=(ModelCapability("deepseek", "deepseek-v4-pro", frozenset({"tools"})),),
        scenarios={"doc-09": scenario},
    )

    health = await runtime.health()
    capabilities = await runtime.capabilities()
    handle = await runtime.start_task(_request())
    repeat = await runtime.start_task(_request())
    events = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)

    assert health.availability == "available"
    assert capabilities.models[0].model_id == "deepseek-v4-pro"
    assert repeat == handle
    assert len(runtime.started_requests) == 1
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.category for event in events] == [
        RuntimeEventCategory.LIFECYCLE,
        RuntimeEventCategory.TOOL,
        RuntimeEventCategory.LIFECYCLE,
    ]
    assert result.status == RuntimeTaskStatus.SUCCEEDED
    assert result.output_refs == (output,)
    assert result.usage.input_tokens == 10
    assert result.usage.billed_cost_usd == 0.02
    assert result.usage.normalized_cost_usd == 0.03
    assert result.tool_calls == 1
    assert result.tool_bytes == 12
    assert result.retry_count == 1


@pytest.mark.asyncio
async def test_fake_runtime_configures_failure_and_redacts_failure_payload():
    runtime = FakeAgentRuntime(
        scenarios={
            "doc-09": FakeRuntimeScenario(
                outcome=RuntimeTaskStatus.FAILED,
                failure_category="provider_unavailable",
                failure_detail={"api_key": "must-not-leak", "message": "unavailable"},
            )
        }
    )

    handle = await runtime.start_task(_request())
    events = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)

    assert events[-1].category == RuntimeEventCategory.ERROR
    assert events[-1].payload["api_key"] == "[REDACTED]"
    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "provider_unavailable"
    assert result.failure_detail["api_key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_fake_runtime_cancellation_is_terminal_and_append_only():
    runtime = FakeAgentRuntime()
    request = _request()
    handle = await runtime.start_task(request)

    cancelled = await runtime.cancel(
        CancelRequest(
            experiment_id=request.experiment_id,
            task_id=request.task_id,
            session_id=handle.session_id,
            reason="operator-request",
        )
    )
    events = [event async for event in runtime.observe(handle, after_sequence=2)]
    result = await runtime.result(handle)

    assert cancelled.accepted is True
    assert cancelled.status == RuntimeTaskStatus.CANCELLED
    assert [event.sequence for event in events] == [3]
    assert events[0].task_status == RuntimeTaskStatus.CANCELLED
    assert result.status == RuntimeTaskStatus.CANCELLED
    assert result.failure_category == "cancelled"


@pytest.mark.asyncio
async def test_fake_runtime_rejects_idempotency_key_rebound_to_different_request():
    runtime = FakeAgentRuntime()
    await runtime.start_task(_request())

    with pytest.raises(ValueError, match="idempotency key"):
        await runtime.start_task(_request(task_id="doc-10"))
