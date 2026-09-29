"""Characterisation tests for the scan/generate trigger proxy (pre-existing behaviour)."""

from __future__ import annotations

import httpx
import pytest

from .conftest import AUTH

KINDS = ["scan", "generate"]


@pytest.mark.parametrize("kind", KINDS)
def test_trigger_proxies_to_the_instance_with_the_control_key(kind, mc_env, instance, client):
    mc_env["add_client"]("acme", mcp_url="https://acme.example/")
    instance["handler"] = lambda r: httpx.Response(200, json={"queued": True})

    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json() == {"queued": True}
    (req,) = instance["requests"]
    assert req.method == "POST"
    assert str(req.url) == f"https://acme.example/ops/control/{kind}/p1"
    assert req.headers["authorization"] == "Bearer ctl-key"


@pytest.mark.parametrize("kind", KINDS)
def test_trigger_passes_through_the_instance_error_status(kind, mc_env, instance, client):
    mc_env["add_client"]("acme")
    instance["handler"] = lambda r: httpx.Response(409, text="busy")

    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)

    assert resp.status_code == 409
    assert resp.json() == {"detail": "busy"}


@pytest.mark.parametrize("kind", KINDS)
def test_trigger_unreachable_instance_is_502(kind, mc_env, instance, client):
    mc_env["add_client"]("acme")

    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    instance["handler"] = boom
    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)

    assert resp.status_code == 502
    assert resp.json()["detail"].startswith("Could not reach VPS:")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("snapshot", [None, {"mcp_url": "https://heartbeat.example"}])
def test_trigger_for_a_client_without_clients_entry_is_404(kind, snapshot, mc_env, instance,
                                                           client):
    """A heartbeat alone does not make a client addressable."""
    mc_env["add_client"]("ghost", mcp_url=None, control_key=None, snapshot=snapshot)
    resp = client.post(f"/ops/clients/ghost/trigger/{kind}/p1", headers=AUTH)
    assert resp.status_code == 404
    assert "clients.json" in resp.json()["detail"]
    assert instance["requests"] == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("snapshot", [None, {"mcp_url": "https://heartbeat.example"}])
def test_trigger_without_pinned_mcp_url_is_422_even_with_a_heartbeat_url(kind, snapshot, mc_env,
                                                                         instance, client):
    mc_env["add_client"]("acme", mcp_url=None, snapshot=snapshot)
    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)
    assert resp.status_code == 422
    assert "pin mcp_url for 'acme' in clients.json" in resp.json()["detail"]
    assert instance["requests"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_trigger_ignores_the_heartbeat_url(kind, mc_env, instance, client):
    mc_env["add_client"]("acme", mcp_url="https://pinned.example",
                         snapshot={"mcp_url": "https://evil.example"})
    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)
    assert resp.status_code == 200
    (req,) = instance["requests"]
    assert req.url.host == "pinned.example"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("url,words", [
    ("http://acme.example", "https://"),
    ("https://user:pw@acme.example", "credentials"),
    ("https://user@acme.example", "credentials"),
    ("ftp://acme.example", "https://"),
    ("acme.example", "https://"),
    ("https://", "https://"),
    ("https://acme.example/?x=1", "query"),
    ("https://acme.example:notaport", "valid URL"),
])
def test_trigger_with_an_unsafe_pinned_url_is_422(kind, url, words, mc_env, instance, client):
    mc_env["add_client"]("acme", mcp_url=url)
    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)
    assert resp.status_code == 422
    assert words in resp.json()["detail"]
    assert instance["requests"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_trigger_without_control_key_is_422(kind, mc_env, instance, client):
    mc_env["add_client"]("acme", control_key=None)
    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)
    assert resp.status_code == 422
    assert "control_key" in resp.json()["detail"]
    assert instance["requests"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_trigger_requires_the_api_key(kind, mc_env, instance, client):
    mc_env["add_client"]("acme")
    bad = client.post(f"/ops/clients/acme/trigger/{kind}/p1",
                      headers={"Authorization": "Bearer wrong"})
    none = client.post(f"/ops/clients/acme/trigger/{kind}/p1")
    assert bad.status_code == 401
    assert none.status_code in (401, 403)
    assert instance["requests"] == []


def _registry(entry):
    return type("R", (), {"get": staticmethod(lambda slug: entry)})()


@pytest.mark.parametrize("url", ["http://localhost:8002", "http://127.0.0.1:8002/",
                                 "http://[::1]:8002", "https://acme.example/base/"])
def test_loopback_http_and_any_https_are_addressable(url):
    from mission_control.instance_client import InstanceControlClient

    target = InstanceControlClient(
        registry=_registry({"control_key": "k", "mcp_url": url})).resolve("acme")
    assert (target.mcp_url, target.control_key) == (url.rstrip("/"), "k")


@pytest.mark.parametrize("entry,failure", [
    (None, "no_client_entry"),
    ({"mcp_url": "https://acme.example"}, "no_control_key"),
    ({"control_key": "k"}, "no_pinned_url"),
    ({"control_key": "k", "mcp_url": "  "}, "no_pinned_url"),
    ({"control_key": "k", "mcp_url": "http://10.0.0.5:8002"}, "insecure_url"),
    ({"control_key": "k", "mcp_url": "https://a:b@acme.example"}, "insecure_url"),
    ({"control_key": "k", "mcp_url": "javascript:alert(1)"}, "invalid_url"),
])
def test_resolve_failure_modes(entry, failure):
    from mission_control.instance_client import InstanceControlClient, InstanceResolutionError

    with pytest.raises(InstanceResolutionError) as info:
        InstanceControlClient(registry=_registry(entry)).resolve("acme")
    assert info.value.failure.value == failure
