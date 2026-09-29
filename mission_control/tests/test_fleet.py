"""Fleet client registry + API + per-client heartbeat keys (ADR-039)."""

from __future__ import annotations

import json
import logging
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from .conftest import AUTH

CTL = "ctl-registry-key-0123456789"


def _register(client, slug="acme", mcp_url="https://acme.example", control_key=CTL):
    return client.post("/ops/fleet/clients", headers=AUTH,
                       json={"slug": slug, "mcp_url": mcp_url, "control_key": control_key})


def _beat(client, slug, key, body=None):
    return client.post(f"/ops/clients/{slug}/telemetry",
                       headers={"Authorization": f"Bearer {key}"},
                       json=body if body is not None else {"schema_version": "ops.telemetry.v1"})


# -- CRUD happy paths -------------------------------------------------------------

def test_register_returns_the_entry_the_key_once_and_the_env_lines(mc_env, monkeypatch, client):
    from mission_control.config import settings

    monkeypatch.setattr(settings, "public_url", "https://mc.example/")
    resp = _register(client, mcp_url="https://acme.example/")

    assert resp.status_code == 201
    body = resp.json()
    key = body["telemetry_key"]
    assert len(key) >= 43
    assert body["client_env"] == {"MCP_OPS_TELEMETRY_URL": "https://mc.example",
                                  "MCP_OPS_TELEMETRY_API_KEY": key,
                                  "MCP_OPS_CLIENT_SLUG": "acme"}
    entry = body["client"]
    assert entry["slug"] == "acme"
    assert entry["mcp_url"] == "https://acme.example"
    assert entry["has_control_key"] is True
    assert entry["heartbeat_auth"] == "client_key"
    assert entry["source"] == "registry"
    assert entry["created_at"] and entry["updated_at"]
    assert entry["last_heartbeat_at"] is None
    assert set(entry) == {"slug", "mcp_url", "has_control_key", "heartbeat_auth", "source",
                          "created_at", "updated_at", "last_heartbeat_at"}


def test_list_shows_registry_and_file_clients_and_the_base_url(mc_env, client):
    mc_env["add_client"]("legacy", mcp_url="https://legacy.example", control_key="file-key")
    _register(client)

    resp = client.get("/ops/fleet/clients", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["telemetry_base_url"] is None
    assert body["registry_unavailable"] is False
    by_slug = {c["slug"]: c for c in body["clients"]}
    assert list(by_slug) == ["acme", "legacy"]
    assert by_slug["acme"]["source"] == "registry"
    assert by_slug["legacy"] == {
        "slug": "legacy", "mcp_url": "https://legacy.example", "has_control_key": True,
        "heartbeat_auth": "shared_key_only", "source": "file", "created_at": None,
        "updated_at": None, "last_heartbeat_at": None}


def test_register_without_control_key_then_patch_it_in(mc_env, client):
    for blank in (None, "", "   "):
        slug = f"c{len(blank or '')}{'n' if blank is None else ''}"
        resp = _register(client, slug=slug, control_key=blank)
        assert resp.status_code == 201
        assert resp.json()["client"]["has_control_key"] is False

    resp = client.patch("/ops/fleet/clients/c0n", headers=AUTH, json={"control_key": CTL})
    assert resp.status_code == 200
    assert resp.json()["client"]["has_control_key"] is True
    assert CTL not in resp.text

    resp = client.patch("/ops/fleet/clients/c0n", headers=AUTH,
                        json={"mcp_url": "https://moved.example"})
    assert resp.json()["client"]["mcp_url"] == "https://moved.example"
    assert resp.json()["client"]["has_control_key"] is True  # untouched when absent

    resp = client.patch("/ops/fleet/clients/c0n", headers=AUTH, json={"control_key": None})
    assert resp.json()["client"]["has_control_key"] is False


def test_delete_removes_the_entry_but_keeps_the_snapshot(mc_env, client):
    key = _register(client).json()["telemetry_key"]
    assert _beat(client, "acme", key).status_code == 204

    assert client.delete("/ops/fleet/clients/acme", headers=AUTH).status_code == 204
    assert client.get("/ops/fleet/clients", headers=AUTH).json()["clients"] == []
    assert (mc_env["snapshots"] / "acme.json").exists()
    assert _beat(client, "acme", key).status_code == 401  # its key went with it


def test_last_heartbeat_at_comes_from_the_snapshot(mc_env, client):
    key = _register(client).json()["telemetry_key"]
    _beat(client, "acme", key)
    stored = json.loads((mc_env["snapshots"] / "acme.json").read_text())

    (entry,) = client.get("/ops/fleet/clients", headers=AUTH).json()["clients"]
    assert entry["last_heartbeat_at"] == stored["_mc_received_at"]
    assert stored["_mc_auth"] == "client_key"


# -- error codes ------------------------------------------------------------------

@pytest.mark.parametrize("slug", ["", "Acme", "-acme", "a_b", "a" * 64, "../x", "a b"])
def test_register_invalid_slug_is_422(slug, mc_env, client):
    resp = _register(client, slug=slug)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_slug"
    assert not mc_env["registry"].exists()


@pytest.mark.parametrize("url,code", [
    ("http://acme.example", "insecure_url"),
    ("https://user:pw@acme.example", "insecure_url"),
    ("ftp://acme.example", "invalid_url"),
    ("acme.example", "invalid_url"),
    ("https://acme.example/?x=1", "invalid_url"),
    ("https://acme.example/#f", "invalid_url"),
    ("https://acme.example:notaport", "invalid_url"),
    ("", "invalid_url"),
    ("https://" + "a" * 2050 + ".example", "invalid_url"),
])
def test_register_bad_url_is_422_with_the_resolver_codes(url, code, mc_env, client):
    resp = _register(client, mcp_url=url)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == code
    assert resp.json()["detail"]["message"]


@pytest.mark.parametrize("url", ["http://localhost:8002", "http://127.0.0.1:8002",
                                 "http://[::1]:8002"])
def test_register_loopback_http_is_allowed(url, mc_env, client):
    assert _register(client, mcp_url=url).status_code == 201


@pytest.mark.parametrize("key", ["has space", "new\nline", "x" * 513, "café"])
def test_register_header_unsafe_control_key_is_422(key, mc_env, client):
    resp = _register(client, control_key=key)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_control_key"


def test_register_existing_slug_is_409_for_both_sources(mc_env, client):
    mc_env["add_client"]("legacy")
    assert _register(client).status_code == 201

    for slug in ("acme", "legacy"):
        resp = _register(client, slug=slug)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "client_exists"


def test_unknown_client_is_404_on_every_mutation(mc_env, client):
    calls = [
        client.patch("/ops/fleet/clients/ghost", headers=AUTH, json={"control_key": "k"}),
        client.post("/ops/fleet/clients/ghost/telemetry-key", headers=AUTH),
        client.delete("/ops/fleet/clients/ghost", headers=AUTH),
    ]
    for resp in calls:
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "client_not_found"


def test_file_entries_are_immutable_through_the_api(mc_env, client):
    mc_env["add_client"]("legacy", control_key="file-key")
    before = mc_env["clients"].read_text()
    calls = [
        client.patch("/ops/fleet/clients/legacy", headers=AUTH, json={"control_key": "k"}),
        client.post("/ops/fleet/clients/legacy/telemetry-key", headers=AUTH),
        client.delete("/ops/fleet/clients/legacy", headers=AUTH),
    ]
    for resp in calls:
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "managed_by_file"
    assert mc_env["clients"].read_text() == before
    assert not mc_env["registry"].exists()


def test_patch_rejects_bad_url_and_clearing_the_url(mc_env, client):
    _register(client)
    bad = client.patch("/ops/fleet/clients/acme", headers=AUTH, json={"mcp_url": "http://x.io"})
    assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "insecure_url")
    cleared = client.patch("/ops/fleet/clients/acme", headers=AUTH, json={"mcp_url": None})
    assert (cleared.status_code, cleared.json()["detail"]["code"]) == (422, "invalid_url")


def test_fleet_api_requires_the_api_key(mc_env, client):
    _register(client)
    bad = {"Authorization": "Bearer wrong"}
    assert client.get("/ops/fleet/clients", headers=bad).status_code == 401
    assert client.post("/ops/fleet/clients", headers=bad,
                       json={"slug": "x", "mcp_url": "https://x.io"}).status_code == 401
    assert client.patch("/ops/fleet/clients/acme", headers=bad, json={}).status_code == 401
    assert client.post("/ops/fleet/clients/acme/telemetry-key", headers=bad).status_code == 401
    assert client.delete("/ops/fleet/clients/acme", headers=bad).status_code == 401


def test_a_client_telemetry_key_cannot_call_the_fleet_api(mc_env, client):
    key = _register(client).json()["telemetry_key"]
    resp = client.get("/ops/fleet/clients", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 401


def test_corrupt_registry_refuses_writes_and_keeps_the_file(mc_env, client):
    mc_env["registry"].parent.mkdir(parents=True)
    mc_env["registry"].write_text('{"clients": {"acme": {"control_key": "sekrit"')
    mc_env["add_client"]("legacy")

    resp = _register(client, slug="new")
    assert (resp.status_code, resp.json()["detail"]["code"]) == (503, "registry_unavailable")
    assert "sekrit" in mc_env["registry"].read_text()  # not overwritten
    # file-defined clients keep working
    listing = client.get("/ops/fleet/clients", headers=AUTH).json()
    assert [c["slug"] for c in listing["clients"]] == ["legacy"]
    assert listing["registry_unavailable"] is True


# -- key handling -----------------------------------------------------------------

def test_keys_never_appear_in_get_responses_or_logs(mc_env, client, caplog):
    caplog.set_level(logging.DEBUG)
    mc_env["add_client"]("legacy", control_key="file-control-key-xyz")
    created = _register(client).json()
    telemetry_key = created["telemetry_key"]
    rotated = client.post("/ops/fleet/clients/acme/telemetry-key", headers=AUTH).json()
    client.patch("/ops/fleet/clients/acme", headers=AUTH, json={"control_key": CTL + "-2"})
    _beat(client, "acme", rotated["telemetry_key"])

    listing = client.get("/ops/fleet/clients", headers=AUTH).text
    snapshot = client.get("/ops/clients/acme/snapshot", headers=AUTH).text
    secrets = [CTL, CTL + "-2", "file-control-key-xyz", telemetry_key, rotated["telemetry_key"]]
    stored = json.loads(mc_env["registry"].read_text())["clients"]["acme"]
    for secret in secrets:
        assert secret not in listing
        assert secret not in snapshot
        assert secret not in caplog.text
    assert stored["telemetry_key_sha256"] and rotated["telemetry_key"] not in json.dumps(stored)
    assert "\"control_key\"" not in listing and "sha256" not in listing


def test_registry_stores_only_the_hash_of_the_telemetry_key(mc_env, client):
    from mission_control.fleet import hash_telemetry_key

    key = _register(client).json()["telemetry_key"]
    raw = mc_env["registry"].read_text()
    assert key not in raw
    assert json.loads(raw)["clients"]["acme"]["telemetry_key_sha256"] == hash_telemetry_key(key)


def test_rotation_returns_a_new_key_once_and_invalidates_the_old(mc_env, client):
    old = _register(client).json()["telemetry_key"]
    resp = client.post("/ops/fleet/clients/acme/telemetry-key", headers=AUTH)

    assert resp.status_code == 200
    new = resp.json()["telemetry_key"]
    assert new != old
    assert resp.json()["client_env"]["MCP_OPS_TELEMETRY_API_KEY"] == new
    assert _beat(client, "acme", old).status_code == 401
    assert _beat(client, "acme", new).status_code == 204


# -- heartbeat auth ---------------------------------------------------------------

def test_client_key_heartbeat_for_its_own_slug_is_accepted(mc_env, client):
    key = _register(client).json()["telemetry_key"]
    assert _beat(client, "acme", key).status_code == 204
    assert (mc_env["snapshots"] / "acme.json").exists()


def test_client_key_for_another_slug_is_401_and_writes_nothing(mc_env, client):
    key = _register(client).json()["telemetry_key"]
    _register(client, slug="other")
    for slug in ("other", "unregistered"):
        assert _beat(client, slug, key).status_code == 401
        assert not (mc_env["snapshots"] / f"{slug}.json").exists()


def test_shared_key_heartbeat_still_works_and_is_logged_once_per_slug(mc_env, client, caplog):
    from mission_control import auth

    auth._shared_key_accepted.clear()
    caplog.set_level(logging.INFO, logger="mission_control.auth")
    for _ in range(3):
        assert _beat(client, "acme", AUTH["Authorization"].split()[1]).status_code == 204
    notes = [r for r in caplog.records if "shared-key heartbeat" in r.getMessage()]
    assert len(notes) == 1 and "acme" in notes[0].getMessage()
    stored = json.loads((mc_env["snapshots"] / "acme.json").read_text())
    assert stored["_mc_auth"] == "shared_key"


def test_wrong_or_missing_heartbeat_key_is_rejected(mc_env, client):
    _register(client)
    assert _beat(client, "acme", "wrong").status_code == 401
    assert client.post("/ops/clients/acme/telemetry", json={}).status_code in (401, 403)


def test_heartbeat_hash_comparison_is_constant_time(mc_env, client, monkeypatch):
    from mission_control import fleet

    key = _register(client).json()["telemetry_key"]
    calls = []
    real = fleet.hmac.compare_digest
    monkeypatch.setattr(fleet.hmac, "compare_digest",
                        lambda a, b: calls.append((a, b)) or real(a, b))
    assert _beat(client, "acme", key).status_code == 204
    assert (fleet.hash_telemetry_key(key).encode(), fleet.hash_telemetry_key(key).encode()) in calls


@pytest.mark.parametrize("body", [[1, 2], "text", 3])
def test_heartbeat_body_must_be_an_object(body, mc_env, client):
    key = _register(client).json()["telemetry_key"]
    resp = _beat(client, "acme", key, body=body)
    assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "invalid_payload")


def test_shared_key_heartbeat_with_invalid_slug_is_422(mc_env, client):
    resp = _beat(client, "Bad_Slug", AUTH["Authorization"].split()[1])
    assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "invalid_slug")


# -- precedence and resolution ------------------------------------------------------

def test_registry_takes_precedence_over_the_file(mc_env, instance, client):
    mc_env["add_client"]("acme", mcp_url="https://file.example", control_key="file-key")
    # Registered directly (the API refuses a slug the file already has).
    from mission_control.fleet import default_fleet_registry

    default_fleet_registry.create("acme", mcp_url="https://registry.example",
                                  control_key="registry-key", telemetry_key_sha256="0" * 64)

    (entry,) = client.get("/ops/fleet/clients", headers=AUTH).json()["clients"]
    assert (entry["source"], entry["mcp_url"]) == ("registry", "https://registry.example")

    instance["handler"] = lambda r: httpx.Response(200, json={"queued": True})
    assert client.post("/ops/clients/acme/trigger/scan/p1", headers=AUTH).status_code == 200
    (req,) = instance["requests"]
    assert req.url.host == "registry.example"
    assert req.headers["authorization"] == "Bearer registry-key"

    # Removing the registry entry falls back to the file entry.
    assert client.delete("/ops/fleet/clients/acme", headers=AUTH).status_code == 204
    (entry,) = client.get("/ops/fleet/clients", headers=AUTH).json()["clients"]
    assert entry["source"] == "file"


@pytest.mark.parametrize("kind", ["scan", "generate"])
def test_trigger_reaches_a_registry_added_client(kind, mc_env, instance, client):
    _register(client, mcp_url="https://acme.example/")
    instance["handler"] = lambda r: httpx.Response(200, json={"queued": True})

    resp = client.post(f"/ops/clients/acme/trigger/{kind}/p1", headers=AUTH)

    assert resp.status_code == 200
    (req,) = instance["requests"]
    assert str(req.url) == f"https://acme.example/ops/control/{kind}/p1"
    assert req.headers["authorization"] == f"Bearer {CTL}"


def test_trigger_ignores_the_heartbeat_url_for_registry_clients(mc_env, instance, client):
    key = _register(client).json()["telemetry_key"]
    _beat(client, "acme", key, body={"mcp_url": "https://evil.example"})
    client.post("/ops/clients/acme/trigger/scan/p1", headers=AUTH)
    (req,) = instance["requests"]
    assert req.url.host == "acme.example"


def test_registry_client_without_control_key_cannot_be_pushed_to(mc_env, instance, client):
    _register(client, control_key=None)

    trig = client.post("/ops/clients/acme/trigger/scan/p1", headers=AUTH)
    assert trig.status_code == 422
    assert "control_key" in trig.json()["detail"]

    from mission_control.instance_client import InstanceControlClient, InstanceResolutionError

    with pytest.raises(InstanceResolutionError) as info:
        InstanceControlClient().resolve("acme")
    assert info.value.failure.value == "no_control_key"
    assert instance["requests"] == []


# -- storage ------------------------------------------------------------------------

def test_registry_write_is_atomic_0600_and_leaves_no_temp_files(mc_env, client):
    _register(client)
    _register(client, slug="two")
    client.post("/ops/fleet/clients/acme/telemetry-key", headers=AUTH)
    client.delete("/ops/fleet/clients/two", headers=AUTH)

    path = mc_env["registry"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    leftovers = [p.name for p in path.parent.iterdir()
                 if p.name not in (path.name, path.name + ".lock")]
    assert leftovers == []
    data = json.loads(path.read_text())
    assert data["version"] == 1 and list(data["clients"]) == ["acme"]


def test_a_failed_write_leaves_the_old_file_and_no_temp(mc_env, client, monkeypatch):
    from mission_control import storage

    _register(client)
    before = mc_env["registry"].read_text()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", boom)
    with pytest.raises(OSError):
        from mission_control.fleet import default_fleet_registry
        default_fleet_registry.delete("acme")
    assert mc_env["registry"].read_text() == before
    assert sorted(p.name for p in mc_env["registry"].parent.iterdir()) == [
        "fleet_clients.json", "fleet_clients.json.lock"]


def test_concurrent_register_of_the_same_slug_gives_one_201_and_one_409(mc_env, client):
    barrier = threading.Barrier(2)

    def attempt(_):
        barrier.wait()
        return _register(client).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(attempt, range(2)))
    assert codes == [201, 409]


def test_registry_lock_holds_across_separate_instances(tmp_path):
    """Two registry objects (as in two uvicorn workers) on one file: flock serialises
    their read-modify-write, so no update is lost and a slug is created once."""
    from mission_control.fleet import ClientExists, JsonFleetRegistry

    path = tmp_path / "fleet_clients.json"
    workers = [JsonFleetRegistry(path) for _ in range(8)]

    def create(i):
        reg = workers[i % len(workers)]
        reg.create(f"c{i}", mcp_url="https://x.example", control_key=None,
                   telemetry_key_sha256="0" * 64)
        try:
            reg.create("dup", mcp_url="https://x.example", control_key=None,
                       telemetry_key_sha256="0" * 64)
            return 1
        except ClientExists:
            return 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        created_dup = sum(pool.map(create, range(40)))
    assert created_dup == 1
    assert len(JsonFleetRegistry(path).list()) == 41


# -- shared-key heartbeat rules (security review) ---------------------------------

SHARED = AUTH["Authorization"].split()[1]


@pytest.fixture(autouse=True)
def _fresh_once_sets():
    from mission_control import auth

    auth._shared_key_accepted.clear()
    auth._shared_key_refused.clear()
    yield


def test_shared_key_is_rejected_for_a_slug_with_its_own_key(mc_env, client):
    key = _register(client).json()["telemetry_key"]
    refused = _beat(client, "acme", SHARED)
    wrong = _beat(client, "acme", "not-a-key")

    assert refused.status_code == 401
    assert refused.json() == wrong.json() == {"detail": "Invalid token"}
    assert not (mc_env["snapshots"] / "acme.json").exists()
    assert _beat(client, "acme", key).status_code == 204  # its own key still works


def test_shared_key_still_works_for_a_file_only_client(mc_env, client):
    mc_env["add_client"]("legacy")
    assert _beat(client, "legacy", SHARED).status_code == 204


def test_shared_key_is_refused_everywhere_on_the_repo_default(mc_env, client, monkeypatch):
    from mission_control.config import REPO_DEFAULT_API_KEY, settings

    monkeypatch.setattr(settings, "api_key", REPO_DEFAULT_API_KEY)
    resp = _beat(client, "unkeyed", REPO_DEFAULT_API_KEY)
    assert (resp.status_code, resp.json()) == (401, {"detail": "Invalid token"})
    assert not (mc_env["snapshots"] / "unkeyed.json").exists()


def test_startup_warns_when_running_on_the_repo_default(mc_env, monkeypatch, caplog):
    from mission_control.config import REPO_DEFAULT_API_KEY, settings
    from mission_control.main import log_heartbeat_auth_posture

    caplog.set_level(logging.INFO, logger="mission_control.main")
    log_heartbeat_auth_posture()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    monkeypatch.setattr(settings, "api_key", REPO_DEFAULT_API_KEY)
    log_heartbeat_auth_posture()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "repo default" in warnings[0].getMessage()
    assert REPO_DEFAULT_API_KEY not in caplog.text


def test_allow_shared_heartbeat_false_accepts_only_client_keys(mc_env, client, monkeypatch):
    from mission_control.config import settings

    key = _register(client).json()["telemetry_key"]
    monkeypatch.setattr(settings, "allow_shared_heartbeat", False)
    assert _beat(client, "unkeyed", SHARED).status_code == 401
    assert not (mc_env["snapshots"] / "unkeyed.json").exists()
    assert _beat(client, "acme", key).status_code == 204


def test_allow_shared_heartbeat_reads_its_env_var(monkeypatch):
    from mission_control.config import Settings

    monkeypatch.setenv("MC_ALLOW_SHARED_HEARTBEAT", "false")
    assert Settings().allow_shared_heartbeat is False
    monkeypatch.delenv("MC_ALLOW_SHARED_HEARTBEAT")
    assert Settings().allow_shared_heartbeat is True


def test_shared_key_is_refused_while_the_registry_is_unreadable(mc_env, client):
    mc_env["registry"].parent.mkdir(parents=True)
    mc_env["registry"].write_text("{not json")
    # We cannot tell whether the slug has its own key, so fail closed.
    assert _beat(client, "acme", SHARED).status_code == 401


def test_slug_is_validated_before_any_key_check_or_log(mc_env, client, caplog, monkeypatch):
    from mission_control import auth, fleet

    lookups = []
    real = fleet.FleetDirectory.heartbeat_lookup
    monkeypatch.setattr(fleet.FleetDirectory, "heartbeat_lookup",
                        lambda self, slug: lookups.append(slug) or real(self, slug))
    caplog.set_level(logging.DEBUG)
    for key in (SHARED, "wrong"):
        resp = _beat(client, "Bad_Slug%0Aforged", key)
        assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "invalid_slug")
    assert lookups == []
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("mission_control")]
    assert not [m for m in ours if "Bad_Slug" in m]
    assert len(auth._shared_key_accepted) == 0 and len(auth._shared_key_refused) == 0


def test_once_per_slug_log_set_is_bounded():
    from mission_control.auth import _OncePerSlug

    seen = _OncePerSlug(cap=3)
    assert [seen.first(s) for s in ("a", "b", "a", "c", "d", "e")] == [
        True, True, False, True, False, False]
    assert len(seen) == 3


def test_refused_shared_key_is_logged_once_per_slug(mc_env, client, caplog):
    _register(client)
    caplog.set_level(logging.INFO, logger="mission_control.auth")
    for _ in range(3):
        assert _beat(client, "acme", SHARED).status_code == 401
    notes = [r for r in caplog.records if "shared-key heartbeat refused" in r.getMessage()]
    assert len(notes) == 1 and "own telemetry key" in notes[0].getMessage()
    assert SHARED not in caplog.text


# -- heartbeat body limits --------------------------------------------------------

def _raw_beat(client, slug, key, content, headers=None):
    return client.post(f"/ops/clients/{slug}/telemetry", content=content,
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json", **(headers or {})})


def test_deeply_nested_body_is_422_not_500(mc_env, client):
    key = _register(client).json()["telemetry_key"]
    depth = 100_000  # fits in 512 KiB; overflows the parser's recursion limit
    for bomb in ("[" * depth + "]" * depth, '{"a":' + "[" * depth + "]" * depth + "}"):
        resp = _raw_beat(client, "acme", key, bomb)
        assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "invalid_payload")
    assert not (mc_env["snapshots"] / "acme.json").exists()


def test_body_that_parses_but_cannot_be_serialised_is_422(mc_env, client, monkeypatch):
    """The parse limit and the pretty-printer's recursion depth differ by
    interpreter; a body between the two must still be a 422."""
    from mission_control import receiver

    key = _register(client).json()["telemetry_key"]

    def overflow(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(receiver, "_render_snapshot", overflow)
    resp = _beat(client, "acme", key)
    assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "invalid_payload")
    assert not (mc_env["snapshots"] / "acme.json").exists()


@pytest.mark.parametrize("content", [b"{not json", b"\xff\xfe{}", b""])
def test_undecodable_body_is_422(content, mc_env, client):
    key = _register(client).json()["telemetry_key"]
    resp = _raw_beat(client, "acme", key, content)
    assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "invalid_payload")


def test_oversized_body_is_413_and_writes_nothing(mc_env, client):
    from mission_control.receiver import MAX_HEARTBEAT_BYTES

    key = _register(client).json()["telemetry_key"]
    big = json.dumps({"pad": "x" * MAX_HEARTBEAT_BYTES}).encode()
    resp = _raw_beat(client, "acme", key, big)
    assert (resp.status_code, resp.json()["detail"]["code"]) == (413, "payload_too_large")
    assert not (mc_env["snapshots"] / "acme.json").exists()

    fits = json.dumps({"pad": "x" * (MAX_HEARTBEAT_BYTES - 100)}).encode()
    assert _raw_beat(client, "acme", key, fits).status_code == 204


def test_oversized_streamed_body_without_content_length_is_413(mc_env, client):
    from mission_control.receiver import MAX_HEARTBEAT_BYTES

    key = _register(client).json()["telemetry_key"]

    def chunks():
        yield b'{"pad": "'
        for _ in range(MAX_HEARTBEAT_BYTES // 65536 + 2):
            yield b"x" * 65536
        yield b'"}'

    resp = _raw_beat(client, "acme", key, chunks())
    assert (resp.status_code, resp.json()["detail"]["code"]) == (413, "payload_too_large")


def test_body_is_not_read_before_auth(mc_env, client):
    from mission_control.receiver import MAX_HEARTBEAT_BYTES

    resp = _raw_beat(client, "acme", "wrong", b"x" * (MAX_HEARTBEAT_BYTES + 1))
    assert resp.status_code == 401


# -- registry read cache ----------------------------------------------------------

def test_registry_read_cache_avoids_reparse_and_writes_invalidate(mc_env, client, monkeypatch):
    from mission_control import fleet

    key = _register(client).json()["telemetry_key"]
    parses = []
    real = fleet.json.loads
    monkeypatch.setattr(fleet.json, "loads", lambda text: parses.append(1) or real(text))

    for _ in range(5):
        assert _beat(client, "acme", "unknown-bearer").status_code == 401
    assert len(parses) == 1  # the first read fills the cache; the rest are hits

    rotated = client.post("/ops/fleet/clients/acme/telemetry-key", headers=AUTH).json()
    assert _beat(client, "acme", key).status_code == 401  # old key: cache was dropped
    assert _beat(client, "acme", rotated["telemetry_key"]).status_code == 204


def test_registry_cache_sees_an_external_rewrite(mc_env, client):
    from mission_control.fleet import hash_telemetry_key

    _register(client)
    assert _beat(client, "acme", "hand-set-key").status_code == 401
    data = json.loads(mc_env["registry"].read_text())
    data["clients"]["acme"]["telemetry_key_sha256"] = hash_telemetry_key("hand-set-key")
    tmp = mc_env["registry"].with_name("replacement.json")
    tmp.write_text(json.dumps(data))
    tmp.replace(mc_env["registry"])  # new inode, like another worker's write
    assert _beat(client, "acme", "hand-set-key").status_code == 204


def test_corrupt_registry_is_logged_once_per_version(mc_env, client, caplog):
    mc_env["registry"].parent.mkdir(parents=True)
    mc_env["registry"].write_text("{not json")
    caplog.set_level(logging.ERROR, logger="mission_control.fleet")
    for _ in range(4):
        _beat(client, "acme", "unknown-bearer")
    assert len([r for r in caplog.records if "malformed" in r.getMessage()]) == 1
