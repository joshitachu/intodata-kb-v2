"""HTTP behaviour of the licence gates: 402 shape, view-only reads, 2xx webhook skip."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from sqlalchemy import func, select

from mcp_server.db.models import GenerateRun, ScanRun
from mcp_server.licensing.runtime import set_policy
from mcp_server.services.project_registry import ProjectRegistry
from tests.test_licensing.licence_fixtures import expired_token, install_policy, issue_token


@pytest.fixture(autouse=True)
def _reset_policy():
    yield
    set_policy(None)


async def _add_project(client, project_id: str) -> None:
    resp = await client.post(
        "/mcp/add-repo", json={"url": "zip", "project_id": project_id, "platform": "talend"}
    )
    assert resp.status_code in (200, 201), resp.text


async def _generate(client, monkeypatch, project_id: str):
    import mcp_server.api.routes.projects as projects_mod

    async def _no_background(*_args, **_kwargs):
        return None

    monkeypatch.setattr(projects_mod, "_run_generate_background", _no_background)
    try:
        return await client.post("/mcp/generate", json={"project_id": project_id})
    finally:
        projects_mod._currently_generating.discard(project_id)


async def _finish_all(session_factory, project_id: str, status: str = "completed") -> None:
    async with session_factory() as db:
        registry = ProjectRegistry(db)
        for run in await registry.list_generate_runs(project_id):
            if run.status == "running":
                await registry.complete_generate_run(run.id, status=status)
        await registry.update_status(project_id, "ready")
        await db.commit()


async def test_fourth_generation_returns_402_quota_exhausted(client, session_factory, monkeypatch):
    install_policy(issue_token(generations=3))
    await _add_project(client, "quota-http")
    for _ in range(3):
        resp = await _generate(client, monkeypatch, "quota-http")
        assert resp.status_code == 200, resp.text
        await _finish_all(session_factory, "quota-http")

    resp = await _generate(client, monkeypatch, "quota-http")

    assert resp.status_code == 402
    detail = resp.json()["detail"]
    assert detail["code"] == "quota_exhausted"
    assert detail["capability"] == "generation"
    assert detail["used"] == 3 and detail["limit"] == 3
    # The refused request left no trace: status rolled back, no fourth run.
    async with session_factory() as db:
        assert (await ProjectRegistry(db).get_project("quota-http")).status == "ready"
        assert await db.scalar(select(func.count(GenerateRun.id))) == 3


async def test_failed_generation_frees_its_slot_over_http(client, session_factory, monkeypatch):
    install_policy(issue_token(generations=1))
    await _add_project(client, "quota-failed")
    assert (await _generate(client, monkeypatch, "quota-failed")).status_code == 200
    await _finish_all(session_factory, "quota-failed", status="failed")
    assert (await _generate(client, monkeypatch, "quota-failed")).status_code == 200


async def test_expired_licence_is_view_only(client, session_factory, monkeypatch):
    install_policy(issue_token())
    await _add_project(client, "view-only")
    install_policy(expired_token())

    scan = await client.post("/mcp/scan", json={"project_id": "view-only"})
    assert scan.status_code == 402
    assert scan.json()["detail"]["code"] == "licence_expired"

    generate = await _generate(client, monkeypatch, "view-only")
    assert generate.status_code == 402
    assert generate.json()["detail"]["code"] == "licence_expired"

    # Reads stay open.
    listing = await client.get("/mcp/projects")
    assert listing.status_code == 200
    assert "view-only" in listing.text
    assert (await client.get("/mcp/licence/status")).status_code == 200
    async with session_factory() as db:
        assert await db.scalar(select(func.count(ScanRun.id))) == 0


@pytest.mark.parametrize(
    ("path", "body", "code"),
    [
        ("/mcp/push-to-rag", {"project_id": "gated"}, "licence_expired"),
        ("/mcp/projects/gated/generate-summaries", None, "licence_expired"),
        ("/mcp/projects/gated/git-updates/full-rebuild", None, "licence_expired"),
        ("/mcp/projects/gated/git-updates/repair-latest", None, "licence_expired"),
    ],
)
async def test_route_gates_refuse_before_the_handler(client, path, body, code):
    install_policy(expired_token())
    resp = await client.post(path, json=body) if body is not None else await client.post(path)
    # The gate runs before the handler, so the missing project is never reached.
    assert resp.status_code == 402, resp.text
    assert resp.json()["detail"]["code"] == code


async def test_full_rebuild_precheck_applies_the_quota(client, session_factory, monkeypatch):
    install_policy(issue_token(generations=1))
    await _add_project(client, "rebuild-quota")
    assert (await _generate(client, monkeypatch, "rebuild-quota")).status_code == 200
    await _finish_all(session_factory, "rebuild-quota")

    resp = await client.post("/mcp/projects/rebuild-quota/git-updates/full-rebuild")

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "quota_exhausted"


async def test_enforcement_off_leaves_routes_unaffected(client, monkeypatch):
    import mcp_server.api.routes.projects as projects_mod

    install_policy(expired_token(), enforce=False)
    await _add_project(client, "unenforced")

    async def _no_scan(*_args, **_kwargs):
        return None

    monkeypatch.setattr(projects_mod, "_run_scan_background", _no_scan)
    assert (await client.post("/mcp/scan", json={"project_id": "unenforced"})).status_code == 200
    assert (await _generate(client, monkeypatch, "unenforced")).status_code == 200


async def test_status_endpoint_shape_with_a_valid_licence(client, session_factory, monkeypatch):
    install_policy(issue_token(generations=3))
    await _add_project(client, "status-proj")
    assert (await _generate(client, monkeypatch, "status-proj")).status_code == 200

    body = (await client.get("/mcp/licence/status")).json()

    assert body["state"] == "valid"
    assert body["enforcement"] == "enforce"
    assert body["plan"] == "basic"
    assert body["features"] == ["chat", "generation"]
    assert body["days_remaining"] >= 28
    assert body["usage"] == {"generations_used": 1, "generations_limit": 3}
    assert "token" not in json.dumps(body).lower()


async def test_status_endpoint_on_an_expired_licence_does_not_count(client, monkeypatch):
    from mcp_server.licensing import usage

    install_policy(expired_token())

    async def _no_counting(*_args, **_kwargs):
        raise AssertionError("an invalid licence must not be counted")

    monkeypatch.setattr(usage, "generations_used", _no_counting)
    resp = await client.get("/mcp/licence/status")

    assert resp.status_code == 200  # readable exactly when the banner needs it
    body = resp.json()
    assert body["state"] == "expired"
    assert body["usage"] == {"generations_used": None, "generations_limit": None}


async def test_ops_control_generate_propagates_402(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "ops_control_key", "ops-key")
    install_policy(issue_token())
    await _add_project(client, "ops-proj")
    install_policy(expired_token())

    resp = await client.post(
        "/ops/control/generate/ops-proj", headers={"Authorization": "Bearer ops-key"}
    )

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "licence_expired"


def _signed(secret: str, payload: bytes, event: str) -> dict[str, str]:
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }


@pytest.mark.parametrize("event", ["pull_request", "push"])
async def test_webhook_acknowledges_a_refused_delivery_with_2xx(
    client, session_factory, monkeypatch, tmp_path, event
):
    """GitHub retries non-2xx deliveries; a retry cannot fix a licence."""
    import mcp_server.api.routes.webhook as webhook_mod
    from mcp_server.config import config

    secret = "test-webhook-secret"
    monkeypatch.setattr(config, "github_webhook_secret", secret)
    background: list[object] = []

    async def _record_background(*args, **kwargs):
        background.append((args, kwargs))

    monkeypatch.setattr(webhook_mod, "_webhook_background", _record_background)
    async with session_factory() as db:
        await ProjectRegistry(db).add_project(
            "hook-proj",
            str(tmp_path),
            repo_url="https://github.com/example/hook",
            platform="generic",
            source_type="git",
        )
        await db.commit()
    install_policy(expired_token())
    payload = json.dumps(
        {
            "repository": {"clone_url": "https://github.com/example/hook.git"},
            "before": "a" * 40,
            "after": "b" * 40,
        }
    ).encode()

    resp = await client.post(
        "/webhook/github/webhook", content=payload, headers=_signed(secret, payload, event)
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ignored" and body["reason"] == "licence_refused"
    assert body["licence"]["code"] == "licence_expired"
    assert background == []
    async with session_factory() as db:
        assert await db.scalar(select(func.count(ScanRun.id))) == 0
        project = await ProjectRegistry(db).get_project("hook-proj")
        assert project.status != "scanning"
        assert project.git_update_summary["webhook"]["reason"] == "licence_refused"
