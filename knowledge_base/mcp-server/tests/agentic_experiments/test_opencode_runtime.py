from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

import httpx
import pytest

from mcp_server.experiments.domain.runtime import (
    CancelRequest,
    OutputContract,
    RuntimeBudgets,
    RuntimeTaskRequest,
    RuntimeTaskStatus,
)
from mcp_server.experiments.infrastructure.opencode.client import OpenCodeHttpClient
from mcp_server.experiments.infrastructure.opencode.runtime import OpenCodeAgentRuntime


def _request() -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        experiment_id="experiment-1",
        task_id="doc-09",
        idempotency_key="request-1",
        provider_id="deepseek",
        model_id="deepseek-v4-pro",
        semantic_prompt="Write the bounded requested document.",
        semantic_prompt_sha256="a" * 64,
        context_refs=(),
        output_contract=OutputContract("09_EXECUTIVE_SUMMARY.md", "text/markdown"),
        allowed_tool_names=("nexus_experiment_get_contract",),
        budgets=RuntimeBudgets(max_input_tokens=100, max_output_tokens=50, max_elapsed_ms=1000),
        deadline_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        required_capabilities=frozenset({"tools"}),
    )


def _runtime(handler) -> tuple[OpenCodeAgentRuntime, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenCodeHttpClient(
        base_url="http://opencode.test",
        username="nexus",
        password="test-password-not-a-real-secret",
        client=http_client,
    )
    def fixed_now() -> datetime:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    return OpenCodeAgentRuntime(client, now=fixed_now), http_client


def _provider_response() -> dict[str, object]:
    return {
        "connected": ["deepseek"],
        "all": [
            {
                "id": "deepseek",
                "models": {
                    "deepseek-v4-pro": {
                        "status": "available",
                        "capabilities": ["tools"],
                        "limit": {"context": 128000, "output": 32000},
                    }
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_runtime_preflight_start_observe_poll_fallback_result_and_cancel():
    seen_prompt: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            return httpx.Response(200, json=_provider_response())
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-1"})
        if request.url.path == "/session/session-1/prompt_async":
            seen_prompt.update(json.loads(request.content))
            assert request.headers["Idempotency-Key"] == "request-1"
            return httpx.Response(204)
        if request.url.path == "/event":
            return httpx.Response(503, json={"error": "stream unavailable"})
        if request.url.path == "/session/session-1/message":
            return httpx.Response(200, json=[{"info": {"id": "message-1", "sessionID": "session-1", "role": "assistant", "api_key": "must-not-leak", "model": {"providerID": "deepseek", "modelID": "deepseek-v4-pro"}, "time": {"completed": "2026-01-01T00:00:01Z"}}, "parts": [{"type": "step-finish", "reason": "stop", "tokens": {"input": 10, "output": 4, "cache": {"read": 2, "write": 1}}, "cost": 0.02, "latencyMs": 8}]}])
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/session/session-1/abort":
            return httpx.Response(200, json={"status": "aborted"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    runtime, http_client = _runtime(handler)
    health = await runtime.health()
    capabilities = await runtime.capabilities()
    handle = await runtime.start_task(_request())
    events = [event async for event in runtime.observe(handle)]
    result = await runtime.result(handle)
    cancelled = await runtime.cancel(CancelRequest("experiment-1", "doc-09", handle.session_id, "test"))

    assert health.availability == "available"
    assert capabilities.models[0].max_input_tokens == 128000
    assert capabilities.models[0].max_output_tokens == 32000
    assert seen_prompt["model"] == {"providerID": "deepseek", "modelID": "deepseek-v4-pro"}
    assert seen_prompt["tools"] == {"nexus_experiment_get_contract": True}
    assert handle.message_id is None
    assert len(events) == 1
    assert result.status == RuntimeTaskStatus.SUCCEEDED
    assert result.usage.input_tokens == 10
    assert result.usage.billed_cost_usd == 0.02
    assert result.usage.normalized_cost_usd is None
    assert result.usage.cache_read_tokens == 2
    assert result.usage.cache_write_tokens == 1
    assert "must-not-leak" not in str(events[0].payload)
    assert cancelled.accepted is True
    await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_map", "parts", "expected"),
    [
        ({"session-1": {"type": "busy"}}, [{"type": "step-finish", "reason": "stop"}], RuntimeTaskStatus.RUNNING),
        ({}, [{"type": "text", "text": "still incomplete"}], RuntimeTaskStatus.UNKNOWN),
        ({}, [{"type": "step-finish", "reason": "error", "error": "provider"}], RuntimeTaskStatus.FAILED),
    ],
)
async def test_runtime_requires_idle_completed_assistant_message_for_success(status_map, parts, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            return httpx.Response(200, json=_provider_response())
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-1"})
        if request.url.path.endswith("prompt_async"):
            return httpx.Response(204)
        if request.url.path == "/session/session-1/message":
            return httpx.Response(
                200,
                json=[{"info": {"id": "message-1", "role": "assistant"}, "parts": parts}],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json=status_map)
        raise AssertionError(f"unexpected {request.url.path}")

    runtime, http_client = _runtime(handler)
    handle = await runtime.start_task(_request())
    result = await runtime.result(handle)

    assert result.status == expected
    await http_client.aclose()


@pytest.mark.asyncio
async def test_runtime_refuses_provider_not_listed_as_connected():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            response = _provider_response()
            response["connected"] = []
            return httpx.Response(200, json=response)
        raise AssertionError("unconnected provider must not create a session")

    runtime, http_client = _runtime(handler)
    with pytest.raises(Exception, match="provider/model route"):
        await runtime.start_task(_request())
    await http_client.aclose()


@pytest.mark.asyncio
async def test_runtime_rejects_unsupported_version_and_missing_model_without_starting_session():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "9.9.9"})
        if request.url.path == "/provider":
            raise AssertionError("provider discovery must not occur after version preflight failure")
        raise AssertionError("session must not be created")

    runtime, http_client = _runtime(handler)
    health = await runtime.health()
    assert health.availability == "unavailable"
    with pytest.raises(Exception, match="health/version preflight"):
        await runtime.start_task(_request())
    assert paths == ["/global/health", "/global/health"]
    await http_client.aclose()


@pytest.mark.asyncio
async def test_runtime_deduplicates_sse_events():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            return httpx.Response(200, json=_provider_response())
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-1"})
        if request.url.path.endswith("prompt_async"):
            return httpx.Response(204)
        if request.url.path == "/event":
            assert request.url.query == b""
            return httpx.Response(200, content=b'data: {"type":"tool","properties":{"sessionID":"another-session","part":{"id":"other","returnedBytes":7}}}\n\ndata: {"type":"tool","properties":{"sessionID":"session-1","part":{"id":"same","returnedBytes":5}}}\n\ndata: {"type":"tool","properties":{"sessionID":"session-1","part":{"id":"same","returnedBytes":5}}}\n\n')
        raise AssertionError(f"unexpected {request.url.path}")

    runtime, http_client = _runtime(handler)
    handle = await runtime.start_task(_request())
    events = [event async for event in runtime.observe(handle)]

    assert len(events) == 1
    assert events[0].event_id == "same"
    assert events[0].category == "tool"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_runtime_observe_yields_a_normalized_event_before_sse_closure():
    class HangingAfterEvent(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            yield (
                b'data: {"type":"usage","properties":{"sessionID":"session-1",'
                b'"id":"usage-1","tokens":{"input":101,"output":1}}}\n\n'
            )
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.closed = True

    stream = HangingAfterEvent()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            return httpx.Response(200, json=_provider_response())
        if request.url.path == "/session" and request.method == "POST":
            return httpx.Response(200, json={"id": "session-1"})
        if request.url.path.endswith("prompt_async"):
            return httpx.Response(204)
        if request.url.path == "/event":
            return httpx.Response(200, stream=stream)
        raise AssertionError(request.url.path)

    runtime, http_client = _runtime(handler)
    handle = await runtime.start_task(_request())
    observer = runtime.observe(handle)

    event = await asyncio.wait_for(observer.__anext__(), timeout=0.1)
    await observer.aclose()
    await http_client.aclose()

    assert event.event_id == "usage-1"
    assert event.category == "usage"
    assert event.payload == {"input_tokens": 101, "output_tokens": 1}
    assert stream.closed is True


@pytest.mark.asyncio
async def test_runtime_normalizes_terminal_usage_over_budget_as_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            return httpx.Response(200, json=_provider_response())
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "session-1"})
        if request.url.path.endswith("prompt_async"):
            return httpx.Response(204)
        if request.url.path == "/session/session-1/message":
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {"role": "assistant"},
                        "parts": [
                            {
                                "type": "step-finish",
                                "reason": "stop",
                                "tokens": {"input": 10, "output": 51},
                            }
                        ],
                    }
                ],
            )
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    runtime, http_client = _runtime(handler)
    handle = await runtime.start_task(_request())
    result = await runtime.result(handle)
    await http_client.aclose()

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "budget_exceeded"
    assert result.failure_detail == {"budget": "output_tokens"}
