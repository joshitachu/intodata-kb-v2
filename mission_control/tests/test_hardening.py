"""Channel hardening: constant-time key checks and the clients.json registry."""

from __future__ import annotations

import json
import os

from mission_control.clients import ClientRegistry

from .conftest import API_KEY


def test_api_key_check_uses_constant_time_compare(mc_env, monkeypatch, client):
    from mission_control import auth

    calls = []
    real = auth.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    assert client.get("/ops/clients", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/ops/clients",
                      headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 200
    assert calls == [(b"wrong", API_KEY.encode()), (API_KEY.encode(), API_KEY.encode())]


def test_no_plain_equality_on_the_api_key():
    import inspect

    from mission_control import auth, receiver

    assert "!= settings.api_key" not in inspect.getsource(receiver)
    assert "compare_digest" in inspect.getsource(auth)


def test_registry_reloads_when_the_file_changes(tmp_path):
    path = tmp_path / "clients.json"
    registry = ClientRegistry(path)
    assert registry.get("acme") is None

    path.write_text(json.dumps({"acme": {"control_key": "one"}}))
    assert registry.get("acme") == {"control_key": "one"}

    path.write_text(json.dumps({"acme": {"control_key": "two-longer"}}))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert registry.get("acme") == {"control_key": "two-longer"}


def test_registry_tolerates_a_malformed_file(tmp_path, caplog):
    path = tmp_path / "clients.json"
    path.write_text('{"acme": {"control_key": "sekrit"')
    assert ClientRegistry(path).get("acme") is None
    assert "sekrit" not in caplog.text


def test_clients_config_defaults_next_to_the_snapshots_dir(tmp_path, monkeypatch):
    from mission_control.config import Settings

    monkeypatch.delenv("MC_CLIENTS_CONFIG", raising=False)
    monkeypatch.delenv("MC_LICENCE_LEDGER_DIR", raising=False)
    s = Settings(api_key="k", snapshots_dir=tmp_path / "snapshots")
    assert s.clients_config_path == tmp_path / "clients.json"
    assert s.licence_ledger_path == tmp_path / "licences"

    monkeypatch.setenv("MC_CLIENTS_CONFIG", str(tmp_path / "elsewhere.json"))
    assert Settings(api_key="k").clients_config_path == tmp_path / "elsewhere.json"


def test_load_client_config_reads_the_configured_file(mc_env):
    from mission_control.clients import load_client_config

    mc_env["add_client"]("acme", control_key="abc")
    assert load_client_config("acme") == {"control_key": "abc",
                                          "mcp_url": "https://acme.example"}
    assert load_client_config("ghost") == {}


def test_registry_treats_a_directory_as_no_clients(tmp_path, caplog):
    """Docker creates a directory for an absent bind-mounted file."""
    path = tmp_path / "clients.json"
    path.mkdir()
    assert ClientRegistry(path).get("acme") is None
    assert ClientRegistry(path).all() == {}
    assert "Failed to read" not in caplog.text
