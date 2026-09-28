import socket

import httpx
import pytest

from mcp_server.connectors.errors import ConnectorError
from mcp_server.connectors.transport import (
    ConnectorTransport,
    OutboundPolicy,
    PinnedConnectorTransport,
    normalize_endpoint,
    validate_endpoint,
)


def test_endpoint_rejects_embedded_credentials_and_non_https():
    with pytest.raises(ConnectorError, match="credentials"):
        normalize_endpoint("https://token@example.com")
    with pytest.raises(ConnectorError, match="HTTPS"):
        normalize_endpoint("http://example.com")


@pytest.mark.asyncio
async def test_private_and_loopback_addresses_fail_closed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 0))])
    with pytest.raises(ConnectorError, match="not allowed"):
        await validate_endpoint("https://elastic.example", OutboundPolicy())


@pytest.mark.asyncio
async def test_explicit_private_hostname_is_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.4", 0))])
    assert await validate_endpoint(
        "https://elastic.internal", OutboundPolicy(allow_private_hosts=frozenset({"elastic.internal"}))
    ) == "https://elastic.internal"


@pytest.mark.asyncio
async def test_transport_rejects_write_methods_before_network_access():
    with pytest.raises(ConnectorError, match="read methods only"):
        await ConnectorTransport().request_bytes("DELETE", "https://elastic.example")


@pytest.mark.asyncio
async def test_transport_sanitizes_streamed_authentication_failure(monkeypatch):
    request = httpx.Request("GET", "https://203.0.113.10/")
    response = httpx.Response(401, request=request, content=b'{"error":"must not leak"}')

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def build_request(self, method, url, **kwargs):
            return httpx.Request(method, url, headers=kwargs.get("headers"))

        async def send(self, _request, *, stream):
            assert stream is True
            return response

    async def validated(_url, _policy):
        return "https://elastic.example", "elastic.example", "203.0.113.10"

    monkeypatch.setattr("mcp_server.connectors.transport._validated_endpoint_address", validated)
    monkeypatch.setattr("mcp_server.connectors.transport.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    with pytest.raises(ConnectorError) as caught:
        await ConnectorTransport().request_bytes("GET", "https://elastic.example")

    assert caught.value.code == "authentication_failed"
    assert caught.value.status_code == 401
    assert "must not leak" not in str(caught.value)
    assert response.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("PUT", "https://mcp.example.test/mcp"),
        ("POST", "https://attacker.example.test/mcp"),
        ("POST", "http://mcp.example.test/mcp"),
    ],
)
async def test_streaming_mcp_transport_cannot_escape_approved_origin(method, url):
    transport = PinnedConnectorTransport(
        endpoint="https://mcp.example.test/mcp",
        hostname="mcp.example.test",
        address="203.0.113.10",
        policy=OutboundPolicy(require_https=True),
    )
    try:
        with pytest.raises(ConnectorError, match="approved endpoint"):
            await transport.handle_async_request(httpx.Request(method, url))
    finally:
        await transport.aclose()
