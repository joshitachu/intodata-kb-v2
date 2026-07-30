from __future__ import annotations

import httpx
import pytest

from mcp_server.experiments.infrastructure.opencode.client import (
    OpenCodeAmbiguousSubmission,
    OpenCodeHttpClient,
    OpenCodeMalformedResponse,
)


def _client(handler) -> OpenCodeHttpClient:
    return OpenCodeHttpClient(
        base_url="http://opencode.test",
        username="nexus",
        password="test-password-not-a-real-secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_client_uses_basic_auth_without_exposing_password_in_repr():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"version": "1.18.4"})

    client = _client(handler)
    assert "test-password-not-a-real-secret" not in repr(client)
    assert await client.health() == {"version": "1.18.4"}
    await client._client.aclose()


@pytest.mark.asyncio
async def test_client_parses_sse_and_rejects_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/event":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b'data: {"id":"event-1","type":"message.updated"}\n\ndata: {"id":"event-2","type":"tool"}\n\n',
            )
        return httpx.Response(200, content=b"not-json")

    client = _client(handler)
    events = [event async for event in client.events("session-1")]
    assert [event["id"] for event in events] == ["event-1", "event-2"]
    with pytest.raises(OpenCodeMalformedResponse):
        await client.health()
    await client._client.aclose()


@pytest.mark.asyncio
async def test_async_prompt_transport_failure_is_ambiguous_and_is_not_retried():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("offline", request=request)

    client = _client(handler)
    with pytest.raises(OpenCodeAmbiguousSubmission):
        await client.prompt_async("session-1", payload={"parts": []}, idempotency_key="idempotency-1")
    assert attempts == 1
    await client._client.aclose()


@pytest.mark.asyncio
async def test_async_prompt_accepts_opencode_no_content_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/session/session-1/prompt_async"
        return httpx.Response(204)

    client = _client(handler)
    assert await client.prompt_async("session-1", payload={"parts": []}, idempotency_key="idempotency-1") == {}
    await client._client.aclose()


@pytest.mark.asyncio
async def test_client_reads_and_validates_session_identity():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/session/session-1"
        return httpx.Response(
            200,
            json={
                "id": "session-1",
                "title": "test",
                "directory": "/runtime/workspaces/experiment-1",
            },
        )

    client = _client(handler)
    session = await client.session("session-1")
    assert session.session_id == "session-1"
    assert session.raw["title"] == "test"
    await client._client.aclose()
