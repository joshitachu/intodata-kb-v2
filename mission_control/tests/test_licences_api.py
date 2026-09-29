"""Operator licence issuing API: plans, issue (+push), history."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mission_control.licensing.issuer import add_months, public_key_pem
from mission_control.licensing.ledger import JsonlLedger, LedgerEntry

from .conftest import AUTH

KID = "test-kid"


@pytest.fixture
def signing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mc_env) -> Ed25519PrivateKey:
    from mission_control.config import settings

    key = Ed25519PrivateKey.generate()
    pem = tmp_path / "signing.pem"
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    pem.chmod(0o400)
    monkeypatch.setattr(settings, "licence_signing_key_file", pem)
    monkeypatch.setattr(settings, "licence_signing_kid", KID)
    return key


def _ledger_text(mc_env) -> str:
    path = mc_env["tmp"] / "licences" / "issued.jsonl"
    return path.read_text() if path.exists() else ""


def _verify(token: str, key: Ed25519PrivateKey) -> dict:
    return jwt.decode(token, public_key_pem(key), algorithms=["EdDSA"], issuer="intodata-nexus",
                      options={"verify_exp": False, "verify_nbf": False})


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _seed_ledger(mc_env, client: str, period_end: datetime, push_ok: bool | None,
                 lid: str) -> None:
    JsonlLedger(mc_env["tmp"] / "licences").append(LedgerEntry(
        licence_id=lid, client=client, plan="basic",
        period_start=(period_end - timedelta(days=30)).isoformat(),
        period_end=period_end.isoformat(), issued_at=datetime.now(UTC).isoformat(),
        issued_by=None, kid=KID, push_ok=push_ok, push_status_code=None))


NO_PUSH = {"attempted": False, "ok": False, "status_code": None, "detail": None,
           "instance_licence": None, "slot": None, "next_licence": None}

INSTANCE_LICENCE = {"state": "valid", "reason": None, "plan": "basic",
                    "features": ["chat", "generation"], "limits": {"generations_per_period": 3},
                    "period_start": "2026-10-01T00:00:00+00:00",
                    "period_end": "2026-11-01T00:00:00+00:00", "days_remaining": 31,
                    "near_expiry": False}


# -- plans ------------------------------------------------------------------------

def test_plans_without_a_key_reports_issuing_disabled(mc_env, client):
    resp = client.get("/ops/licensing/plans", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuing_enabled"] is False
    basic = next(p for p in body["plans"] if p["name"] == "basic")
    assert basic["features"] == ["chat", "generation"]
    assert basic["limits"] == {"generations_per_period": 3}
    assert basic["description"]


def test_plans_with_a_key_reports_issuing_enabled(signing_key, client):
    assert client.get("/ops/licensing/plans", headers=AUTH).json()["issuing_enabled"] is True


def test_plans_with_an_unreadable_key_reports_disabled(mc_env, monkeypatch, client, tmp_path):
    from mission_control.config import settings

    bad = tmp_path / "bad.pem"
    bad.write_text("not a key")
    monkeypatch.setattr(settings, "licence_signing_key_file", bad)
    monkeypatch.setattr(settings, "licence_signing_kid", KID)
    assert client.get("/ops/licensing/plans", headers=AUTH).json()["issuing_enabled"] is False


def test_licensing_endpoints_require_the_api_key(mc_env, client):
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/ops/licensing/plans", headers=bad).status_code == 401
    assert client.post("/ops/clients/acme/licences", json={"plan": "basic"},
                       headers=bad).status_code == 401
    assert client.get("/ops/clients/acme/licences", headers=bad).status_code == 401
    assert client.get("/ops/licensing/plans").status_code in (401, 403)


# -- issue ------------------------------------------------------------------------

def test_issue_without_a_key_is_503_and_records_nothing(mc_env, instance, client):
    mc_env["add_client"]("acme")
    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic"}, headers=AUTH)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "issuing_disabled"
    assert _ledger_text(mc_env) == ""
    assert instance["requests"] == []


def test_issue_push_false_records_ledger_and_returns_no_token(signing_key, mc_env, instance,
                                                              client):
    # No snapshot and no clients.json entry: push=false must still work.
    resp = client.post("/ops/clients/newco/licences",
                       json={"plan": "basic", "months": 3, "push": False, "issued_by": "ops"},
                       headers=AUTH)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "token" not in body
    assert body["client"] == "newco" and body["plan"] == "basic" and body["issued_by"] == "ops"
    assert body["push"] == NO_PUSH
    start, end = _parse(body["period_start"]), _parse(body["period_end"])
    assert end == add_months(start, 3)
    assert abs((start - datetime.now(UTC)).total_seconds()) < 60
    assert instance["requests"] == []

    lines = _ledger_text(mc_env).splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["licence_id"] == body["licence_id"]
    assert entry["client"] == "newco" and entry["push_ok"] is None
    assert "token" not in entry
    assert "eyJ" not in lines[0]  # no JWT segment of any kind


def test_return_token_includes_a_token_verifying_with_the_public_key(signing_key, mc_env, client):
    resp = client.post("/ops/clients/acme/licences",
                       json={"plan": "basic", "push": False, "return_token": True},
                       headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    claims = _verify(body["token"], signing_key)
    assert jwt.get_unverified_header(body["token"])["kid"] == KID
    assert claims["sub"] == "acme" and claims["lid"] == body["licence_id"]
    assert claims["exp"] == int(_parse(body["period_end"]).timestamp())
    assert claims["iat"] == int(_parse(body["issued_at"]).timestamp())
    assert body["token"] not in _ledger_text(mc_env)


def test_push_success_installs_and_reports_the_instance_licence(signing_key, mc_env, instance,
                                                                client):
    mc_env["add_client"]("acme", mcp_url="https://acme.example/")
    instance["handler"] = lambda r: httpx.Response(
        200, json={"installed": True, "licence": INSTANCE_LICENCE})

    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic"}, headers=AUTH)

    assert resp.status_code == 201, resp.text
    push = resp.json()["push"]
    assert push == {"attempted": True, "ok": True, "status_code": 200, "detail": None,
                    "instance_licence": INSTANCE_LICENCE, "slot": None, "next_licence": None}
    (req,) = instance["requests"]
    assert str(req.url) == "https://acme.example/ops/control/licence"
    assert req.headers["authorization"] == "Bearer ctl-key"
    token = json.loads(req.content)["token"]
    assert _verify(token, signing_key)["sub"] == "acme"
    assert "token" not in resp.json()
    entry = json.loads(_ledger_text(mc_env))
    assert entry["push_ok"] is True and entry["push_status_code"] == 200
    assert token not in _ledger_text(mc_env)


def test_push_rejected_by_instance_is_still_201_with_the_reason(signing_key, mc_env, instance,
                                                               client):
    mc_env["add_client"]("acme")
    detail = {"code": "licence_rejected", "state": "invalid", "reason": "wrong client"}
    instance["handler"] = lambda r: httpx.Response(422, json={"detail": detail})

    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic"}, headers=AUTH)

    assert resp.status_code == 201
    push = resp.json()["push"]
    assert push == {"attempted": True, "ok": False, "status_code": 422, "detail": detail,
                    "instance_licence": None, "slot": None, "next_licence": None}
    entry = json.loads(_ledger_text(mc_env))
    assert entry["push_ok"] is False and entry["push_status_code"] == 422


def test_push_connection_error_is_still_201(signing_key, mc_env, instance, client):
    mc_env["add_client"]("acme")

    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    instance["handler"] = boom
    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic"}, headers=AUTH)

    assert resp.status_code == 201
    push = resp.json()["push"]
    assert push["attempted"] is True and push["ok"] is False and push["status_code"] is None
    assert push["detail"].startswith("Could not reach VPS:")
    entry = json.loads(_ledger_text(mc_env))
    assert entry["push_ok"] is False and entry["push_status_code"] is None


def test_push_queued_as_next_passes_slot_and_next_licence_through(signing_key, mc_env,
                                                                   instance, client):
    mc_env["add_client"]("acme")
    queued = {**INSTANCE_LICENCE, "period_start": "2026-11-01T00:00:00+00:00",
              "period_end": "2026-12-01T00:00:00+00:00"}
    instance["handler"] = lambda r: httpx.Response(200, json={
        "installed": True, "slot": "next", "licence": INSTANCE_LICENCE, "next_licence": queued})

    push = client.post("/ops/clients/acme/licences", json={"plan": "basic"},
                       headers=AUTH).json()["push"]

    assert push["ok"] is True
    assert push["slot"] == "next" and push["next_licence"] == queued
    assert push["instance_licence"] == INSTANCE_LICENCE


@pytest.mark.parametrize("reply", [
    {"installed": True, "slot": "later", "next_licence": "nope"},
    {"installed": True, "slot": None, "next_licence": None},
])
def test_push_with_unexpected_slot_or_next_licence_reports_none(reply, signing_key, mc_env,
                                                               instance, client):
    mc_env["add_client"]("acme")
    instance["handler"] = lambda r: httpx.Response(200, json=reply)
    push = client.post("/ops/clients/acme/licences", json={"plan": "basic"},
                       headers=AUTH).json()["push"]
    assert push["ok"] is True and push["slot"] is None and push["next_licence"] is None


@pytest.mark.parametrize("snapshot", [None, {"mcp_url": "https://heartbeat.example"}])
def test_push_to_a_client_without_clients_entry_is_404_and_issues_nothing(
        snapshot, signing_key, mc_env, instance, client):
    """A heartbeat (anyone with MC_API_KEY can write one) never makes a client addressable."""
    mc_env["add_client"]("acme", mcp_url=None, control_key=None, snapshot=snapshot)
    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic"}, headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_client"
    assert _ledger_text(mc_env) == ""
    assert instance["requests"] == []


@pytest.mark.parametrize("snapshot", [None, {"mcp_url": "https://heartbeat.example"}])
def test_push_without_pinned_mcp_url_is_422_and_issues_nothing(snapshot, signing_key, mc_env,
                                                               instance, client):
    mc_env["add_client"]("acme", mcp_url=None, snapshot=snapshot)
    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic"}, headers=AUTH)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "instance_not_addressable"
    assert detail["reason"] == "no_pinned_url"
    assert "pin mcp_url for 'acme' in clients.json" in detail["message"]
    assert _ledger_text(mc_env) == ""
    assert instance["requests"] == []


@pytest.mark.parametrize("setup,reason", [
    ({"control_key": None}, "no_control_key"),
    ({"mcp_url": "http://acme.example"}, "insecure_url"),
    ({"mcp_url": "https://ops:secret@acme.example"}, "insecure_url"),
    ({"mcp_url": "file:///etc/passwd"}, "invalid_url"),
])
def test_push_to_an_unaddressable_client_is_422_and_issues_nothing(setup, reason, signing_key,
                                                                  mc_env, instance, client):
    mc_env["add_client"]("acme", **setup)
    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic"}, headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "instance_not_addressable"
    assert resp.json()["detail"]["reason"] == reason
    assert _ledger_text(mc_env) == ""
    assert instance["requests"] == []


def test_push_goes_to_the_pinned_url_not_the_heartbeat_url(signing_key, mc_env, instance,
                                                           client):
    mc_env["add_client"]("acme", mcp_url="https://pinned.example",
                         snapshot={"mcp_url": "https://evil.example"})
    instance["handler"] = lambda r: httpx.Response(200, json={"installed": True})
    assert client.post("/ops/clients/acme/licences", json={"plan": "basic"},
                       headers=AUTH).status_code == 201
    (req,) = instance["requests"]
    assert req.url.host == "pinned.example"


def test_return_token_with_push_is_422_and_issues_nothing(signing_key, mc_env, instance, client):
    mc_env["add_client"]("acme")
    resp = client.post("/ops/clients/acme/licences",
                       json={"plan": "basic", "push": True, "return_token": True}, headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "return_token_with_push"
    assert _ledger_text(mc_env) == ""
    assert instance["requests"] == []


def test_repo_default_api_key_disables_issuing(signing_key, mc_env, monkeypatch, instance,
                                               client):
    from mission_control.config import REPO_DEFAULT_API_KEY, settings

    monkeypatch.setattr(settings, "api_key", REPO_DEFAULT_API_KEY)
    auth = {"Authorization": f"Bearer {REPO_DEFAULT_API_KEY}"}
    assert client.get("/ops/licensing/plans", headers=auth).json()["issuing_enabled"] is False
    resp = client.post("/ops/clients/acme/licences", json={"plan": "basic", "push": False},
                       headers=auth)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "issuing_disabled"
    assert _ledger_text(mc_env) == ""


# -- start_after_current (ledger, never the snapshot) ---------------------------------

def _renew(client) -> dict:
    resp = client.post("/ops/clients/acme/licences",
                       json={"plan": "basic", "months": 1, "start_after_current": True,
                             "push": False}, headers=AUTH)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _future(days: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0)


def test_start_after_current_starts_at_the_latest_pushed_ledger_end(signing_key, mc_env,
                                                                    client):
    current_end = _future(10)
    _seed_ledger(mc_env, "acme", _future(5), True, "lic-a")
    _seed_ledger(mc_env, "acme", current_end, True, "lic-b")
    _seed_ledger(mc_env, "acme", _future(40), False, "lic-failed")  # push failed: not preferred
    _seed_ledger(mc_env, "other", _future(90), True, "lic-other")   # another client
    body = _renew(client)
    assert _parse(body["period_start"]) == current_end
    assert _parse(body["period_end"]) == add_months(current_end, 1)


def test_start_after_current_uses_the_latest_unpushed_entry_when_none_succeeded(
        signing_key, mc_env, client):
    latest = _future(20)
    _seed_ledger(mc_env, "acme", _future(7), None, "lic-manual")
    _seed_ledger(mc_env, "acme", latest, False, "lic-failed")
    assert _parse(_renew(client)["period_start"]) == latest


def test_start_after_current_ignores_a_forged_snapshot(signing_key, mc_env, client):
    far = _future(700)
    mc_env["add_client"]("acme", snapshot={"licence": {
        "state": "valid", "period_end": far.isoformat(), "days_remaining": 700}})
    start = _parse(_renew(client)["period_start"])
    assert abs((start - datetime.now(UTC)).total_seconds()) < 60


@pytest.mark.parametrize("seed", ["empty", "expired", "garbage"])
def test_start_after_current_falls_back_to_now(seed, signing_key, mc_env, client):
    if seed == "expired":
        _seed_ledger(mc_env, "acme", datetime(2020, 1, 1, tzinfo=UTC), True, "lic-old")
    elif seed == "garbage":
        path = mc_env["tmp"] / "licences" / "issued.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"licence_id": "x", "client": "acme", "plan": "basic",
                                    "period_end": "garbage", "push_ok": True}) + "\n")
    start = _parse(_renew(client)["period_start"])
    assert abs((start - datetime.now(UTC)).total_seconds()) < 60


def test_explicit_start_and_end(signing_key, mc_env, client):
    resp = client.post("/ops/clients/acme/licences",
                       json={"plan": "basic", "start": "2030-01-01T00:00:00",
                             "end": "2030-03-15T00:00:00Z", "push": False}, headers=AUTH)
    assert resp.status_code == 201
    body = resp.json()
    assert body["period_start"] == "2030-01-01T00:00:00+00:00"
    assert body["period_end"] == "2030-03-15T00:00:00+00:00"


@pytest.mark.parametrize("payload,code", [
    ({"plan": "platinum"}, "unknown_plan"),
    ({"plan": "basic", "start": "2030-02-01T00:00:00Z", "end": "2030-01-01T00:00:00Z"},
     "invalid_period"),
    ({"plan": "basic", "end": "2020-01-01T00:00:00Z"}, "invalid_period"),
    ({"plan": "basic", "months": None}, "invalid_period"),
    ({"plan": "basic", "start": "2030-01-01T00:00:00Z", "start_after_current": True},
     "invalid_period"),
    ({"plan": "basic", "start": "2030-01-01T00:00:00Z", "end": "2030-01-01T00:00:00Z"},
     "invalid_period"),
    ({"plan": "basic", "start": "2030-01-01T00:00:00Z", "end": "2033-02-01T00:00:01Z"},
     "period_too_long"),
    ({"plan": "basic", "end": "2099-01-01T00:00:00Z"}, "period_too_long"),
])
def test_bad_plan_or_period_is_422(payload, code, signing_key, mc_env, client):
    resp = client.post("/ops/clients/acme/licences", json={**payload, "push": False},
                       headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == code
    assert _ledger_text(mc_env) == ""


def test_period_of_exactly_37_months_is_allowed(signing_key, mc_env, client):
    resp = client.post("/ops/clients/acme/licences",
                       json={"plan": "basic", "start": "2030-01-01T00:00:00Z",
                             "end": "2033-02-01T00:00:00Z", "push": False}, headers=AUTH)
    assert resp.status_code == 201, resp.text


@pytest.mark.parametrize("hours_ago,status_code", [(1, 201), (23, 201), (25, 422), (24 * 400, 422)])
def test_start_may_be_backdated_at_most_24_hours(hours_ago, status_code, signing_key, mc_env,
                                                 client):
    start = datetime.now(UTC) - timedelta(hours=hours_ago)
    resp = client.post("/ops/clients/acme/licences",
                       json={"plan": "basic", "start": start.isoformat(), "push": False},
                       headers=AUTH)
    assert resp.status_code == status_code, resp.text
    if status_code == 422:
        assert resp.json()["detail"]["code"] == "start_in_past"
        assert _ledger_text(mc_env) == ""


@pytest.mark.parametrize("months", [0, 37])
def test_months_out_of_range_is_422(months, signing_key, mc_env, client):
    resp = client.post("/ops/clients/acme/licences",
                       json={"plan": "basic", "months": months, "push": False}, headers=AUTH)
    assert resp.status_code == 422
    assert _ledger_text(mc_env) == ""


def test_invalid_slug_is_422(signing_key, mc_env, client):
    resp = client.post("/ops/clients/Bad_Slug/licences", json={"plan": "basic", "push": False},
                       headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_client"


# -- history ----------------------------------------------------------------------

def test_history_is_newest_first_per_client_and_never_contains_a_token(signing_key, mc_env,
                                                                       client):
    ids, tokens = [], []
    for slug, months in [("acme", 1), ("other", 1), ("acme", 3), ("acme", 12)]:
        resp = client.post(f"/ops/clients/{slug}/licences",
                           json={"plan": "basic", "months": months, "push": False,
                                 "return_token": True}, headers=AUTH)
        assert resp.status_code == 201
        tokens.append(resp.json()["token"])
        if slug == "acme":
            ids.append(resp.json()["licence_id"])

    resp = client.get("/ops/clients/acme/licences", headers=AUTH)
    assert resp.status_code == 200
    entries = resp.json()["licences"]
    assert [e["licence_id"] for e in entries] == list(reversed(ids))
    assert set(entries[0]) >= {"licence_id", "client", "plan", "period_start", "period_end",
                               "issued_at", "issued_by", "push_ok", "push_status_code"}
    assert all(e["client"] == "acme" for e in entries)
    for token in tokens:
        assert token not in resp.text
        assert token not in _ledger_text(mc_env)
    assert client.get("/ops/clients/ghost/licences", headers=AUTH).json() == {"licences": []}
