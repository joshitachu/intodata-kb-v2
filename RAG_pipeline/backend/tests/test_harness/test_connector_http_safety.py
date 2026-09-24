import httpx
import pytest

from src.harness.connectors.http_safety import PinnedHostTransport


@pytest.mark.asyncio
async def test_pinned_transport_connects_to_checked_ip_with_original_host_and_sni():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        transport=PinnedHostTransport(
            hostname="evidence.example.com",
            address="8.8.8.8",
            delegate=httpx.MockTransport(handler),
        ),
        trust_env=False,
    ) as client:
        response = await client.get("https://evidence.example.com/search")

    assert response.status_code == 200
    assert captured[0].url.host == "8.8.8.8"
    assert captured[0].headers["host"] == "evidence.example.com"
    assert captured[0].extensions["sni_hostname"] == "evidence.example.com"


@pytest.mark.asyncio
async def test_pinned_transport_rejects_unapproved_destinations():
    transport = PinnedHostTransport(
        hostname="evidence.example.com",
        address="8.8.8.8",
        delegate=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        with pytest.raises(ValueError, match="escaped"):
            await client.get("https://other.example.com/search")

    with pytest.raises(ValueError, match="public"):
        PinnedHostTransport(hostname="evidence.example.com", address="127.0.0.1")
