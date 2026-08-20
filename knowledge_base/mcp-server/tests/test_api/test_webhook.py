"""Tests for GitHub webhook helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest

from mcp_server.api.routes.webhook import (
    ChangedFile,
    _branch_from_ref,
    _find_plugin_pack_by_clone_url,
    _parse_name_status_diff,
    _resolve_changed_groups,
)


def _signed_headers(secret: str, payload: bytes, event: str) -> dict[str, str]:
    signature = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }


def _repo_payload(clone_url: str) -> bytes:
    return json.dumps({"repository": {"clone_url": clone_url}}).encode()


def test_branch_from_ref_extracts_branch_name() -> None:
    assert _branch_from_ref("refs/heads/main") == "main"
    assert _branch_from_ref("refs/heads/feature/custom") == "feature/custom"
    assert _branch_from_ref("refs/tags/v1") is None


async def test_find_plugin_pack_by_clone_url_prefers_matching_branch(session_factory) -> None:
    from mcp_server.db.models import PluginPack

    async with session_factory() as session:
        legacy = PluginPack(
            name="legacy-pack",
            manifest_name="pack",
            version="1.0.0",
            path="/tmp/legacy",
            repo_url="https://github.com/example/swap-pack",
            repo_branch=None,
            auto_update=True,
        )
        feature = PluginPack(
            name="feature-pack",
            manifest_name="pack",
            version="1.0.0",
            path="/tmp/feature",
            repo_url="https://github.com/example/swap-pack",
            repo_branch="feature/custom",
            auto_update=True,
        )
        session.add_all([legacy, feature])
        await session.commit()

        assert (
            await _find_plugin_pack_by_clone_url(
                session,
                "https://github.com/example/swap-pack.git",
                "feature/custom",
            )
            == feature.id
        )
        assert (
            await _find_plugin_pack_by_clone_url(
                session,
                "https://github.com/example/swap-pack.git",
                "main",
            )
            == legacy.id
        )


async def test_signed_ping_registered_repo_returns_ping_received_and_logs(
    client,
    session_factory,
    monkeypatch,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    secret = "test-webhook-secret"
    clone_url = "https://github.com/example/repo.git"
    monkeypatch.setattr(config, "github_webhook_secret", secret)

    local_path = tmp_path / "repo"
    local_path.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "registered-repo",
            str(local_path),
            repo_url="https://github.com/example/repo",
            platform="generic",
            source_type="git",
        )
        project.github_webhook_id = 101
        await session.commit()

    payload = _repo_payload(clone_url)
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, "ping"),
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ping_received",
        "registered": True,
        "project_id": "registered-repo",
    }
    log_path = tmp_path / "data" / "logs" / "registered-repo_test.log"
    assert "GitHub webhook ping received" in log_path.read_text()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("registered-repo")
        assert project is not None
        assert project.git_update_summary["webhook"]["event"] == "ping"
        assert project.git_update_summary["webhook"]["status"] == "matched"
        assert project.git_update_summary["webhook"]["observations"] == [
            "webhook_received",
            "project_matched",
        ]

    status_resp = await client.get("/mcp/projects/registered-repo/reingestion-status")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["webhook"]["registered"] is True
    assert status["webhook"]["status"] == "active"
    assert status["webhook"]["last_observation"]["event"] == "ping"
    assert status["webhook"]["last_observation"]["status"] == "matched"
    assert status["webhook"]["last_observation"]["observations"] == [
        "webhook_received",
        "project_matched",
    ]


async def test_signed_ping_unregistered_repo_returns_ping_received(
    client,
    monkeypatch,
):
    from mcp_server.config import config

    secret = "test-webhook-secret"
    monkeypatch.setattr(config, "github_webhook_secret", secret)

    payload = _repo_payload("https://github.com/example/missing.git")
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, "ping"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ping_received", "registered": False}


async def test_ping_matching_handles_tokenized_stored_url_and_git_suffix(
    client,
    session_factory,
    monkeypatch,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    secret = "test-webhook-secret"
    monkeypatch.setattr(config, "github_webhook_secret", secret)

    local_path = tmp_path / "repo"
    local_path.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "tokenized-url",
            str(local_path),
            repo_url="https://x-access-token:stored-secret@github.com/example/repo",
            platform="generic",
            source_type="git",
        )
        await session.commit()

    payload = _repo_payload("https://github.com/example/repo.git")
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, "ping"),
    )

    assert resp.status_code == 200
    assert resp.json()["project_id"] == "tokenized-url"
    assert "stored-secret" not in json.dumps(resp.json())


async def test_ping_with_invalid_signature_returns_401(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_webhook_secret", "test-webhook-secret")

    payload = _repo_payload("https://github.com/example/repo.git")
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 401


async def test_legacy_github_signature_header_is_still_accepted(client, monkeypatch):
    from mcp_server.config import config

    secret = "test-webhook-secret"
    monkeypatch.setattr(config, "github_webhook_secret", secret)

    payload = _repo_payload("https://github.com/example/missing.git")
    signature = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ping_received", "registered": False}


@pytest.mark.parametrize("event", ["push", "pull_request"])
async def test_signed_supported_events_keep_existing_registered_flow_and_record_receipt(
    client,
    session_factory,
    monkeypatch,
    tmp_path,
    event,
):
    from mcp_server.config import config
    import mcp_server.api.routes.webhook as webhook_mod
    from mcp_server.services.project_registry import ProjectRegistry

    secret = "test-webhook-secret"
    clone_url = "https://github.com/example/repo.git"
    monkeypatch.setattr(config, "github_webhook_secret", secret)
    background_mock = AsyncMock()
    monkeypatch.setattr(webhook_mod, "_webhook_background", background_mock)

    local_path = tmp_path / "repo"
    local_path.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "existing-flow",
            str(local_path),
            repo_url=clone_url,
            platform="generic",
            source_type="git",
        )
        await session.commit()

    payload_dict = {"repository": {"clone_url": clone_url}}
    if event == "push":
        payload_dict.update({"before": "0" * 40, "after": "1" * 40})
    payload = json.dumps(payload_dict).encode()
    headers = _signed_headers(secret, payload, event)
    headers["X-GitHub-Delivery"] = "delivery-123"

    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "webhook_triggered", "project_id": "existing-flow"}
    background_mock.assert_awaited_once()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("existing-flow")
        assert project is not None
        webhook = project.git_update_summary["webhook"]
        assert webhook["event"] == event
        assert webhook["status"] == "queued"
        assert webhook["delivery_id"] == "delivery-123"
        assert webhook["repo_url"] == clone_url
        assert webhook["observations"] == ["webhook_received", "project_matched"]
        if event == "push":
            assert webhook["before_sha"] == "0" * 40
            assert webhook["after_sha"] == "1" * 40
            assert webhook["is_first_push"] is True


async def test_push_records_sanitized_observation_before_background(
    client,
    session_factory,
    monkeypatch,
    tmp_path,
):
    from mcp_server.config import config
    import mcp_server.api.routes.webhook as webhook_mod
    from mcp_server.services.project_registry import ProjectRegistry

    secret = "test-webhook-secret"
    clone_url = "https://x-access-token:raw-secret@github.com/example/repo.git"
    monkeypatch.setattr(config, "github_webhook_secret", secret)
    background_mock = AsyncMock()
    monkeypatch.setattr(webhook_mod, "_webhook_background", background_mock)

    local_path = tmp_path / "repo"
    local_path.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "sanitized-push",
            str(local_path),
            repo_url="https://github.com/example/repo",
            platform="generic",
            source_type="git",
        )
        await session.commit()

    payload = json.dumps(
        {
            "repository": {"clone_url": clone_url},
            "before": "a" * 40,
            "after": "b" * 40,
        }
    ).encode()
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, "push"),
    )

    assert resp.status_code == 200
    background_mock.assert_awaited_once()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("sanitized-push")
        assert project is not None
        dumped = json.dumps(project.git_update_summary)
        assert "raw-secret" not in dumped
        webhook = project.git_update_summary["webhook"]
        assert webhook["repo_url"] == "https://github.com/example/repo.git"
        assert webhook["canonical_repo"] == "https://github.com/example/repo"


async def test_unregistered_push_does_not_schedule_background(client, monkeypatch):
    from mcp_server.config import config
    import mcp_server.api.routes.webhook as webhook_mod

    secret = "test-webhook-secret"
    monkeypatch.setattr(config, "github_webhook_secret", secret)
    background_mock = AsyncMock()
    monkeypatch.setattr(webhook_mod, "_webhook_background", background_mock)

    payload = json.dumps(
        {
            "repository": {"clone_url": "https://github.com/example/missing.git"},
            "before": "a" * 40,
            "after": "b" * 40,
        }
    ).encode()
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, "push"),
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ignored",
        "reason": "unregistered_repo",
        "repo_url": "https://github.com/example/missing.git",
    }
    background_mock.assert_not_called()


async def test_invalid_push_sha_records_rejection_and_does_not_schedule_background(
    client,
    session_factory,
    monkeypatch,
    tmp_path,
):
    from mcp_server.config import config
    import mcp_server.api.routes.webhook as webhook_mod
    from mcp_server.services.project_registry import ProjectRegistry

    secret = "test-webhook-secret"
    clone_url = "https://github.com/example/repo.git"
    monkeypatch.setattr(config, "github_webhook_secret", secret)
    background_mock = AsyncMock()
    monkeypatch.setattr(webhook_mod, "_webhook_background", background_mock)

    local_path = tmp_path / "repo"
    local_path.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "bad-sha",
            str(local_path),
            repo_url=clone_url,
            platform="generic",
            source_type="git",
        )
        await session.commit()

    payload = json.dumps(
        {"repository": {"clone_url": clone_url}, "before": "not-a-sha", "after": "b" * 40}
    ).encode()
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, "push"),
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ignored",
        "reason": "invalid_sha_payload",
        "project_id": "bad-sha",
    }
    background_mock.assert_not_called()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("bad-sha")
        assert project is not None
        webhook = project.git_update_summary["webhook"]
        assert webhook["status"] == "rejected"
        assert webhook["reason"] == "invalid_sha_payload"
        assert project.auto_reingest_error == "invalid_sha_payload"


async def test_unsupported_matched_event_records_ignored_reason(
    client,
    session_factory,
    monkeypatch,
    tmp_path,
):
    from mcp_server.config import config
    import mcp_server.api.routes.webhook as webhook_mod
    from mcp_server.services.project_registry import ProjectRegistry

    secret = "test-webhook-secret"
    clone_url = "https://github.com/example/repo.git"
    monkeypatch.setattr(config, "github_webhook_secret", secret)
    background_mock = AsyncMock()
    monkeypatch.setattr(webhook_mod, "_webhook_background", background_mock)

    local_path = tmp_path / "repo"
    local_path.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "unsupported-event",
            str(local_path),
            repo_url=clone_url,
            platform="generic",
            source_type="git",
        )
        await session.commit()

    payload = _repo_payload(clone_url)
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, "issues"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "reason": "unsupported_event", "event": "issues"}
    background_mock.assert_not_called()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("unsupported-event")
        assert project is not None
        webhook = project.git_update_summary["webhook"]
        assert webhook["status"] == "ignored"
        assert webhook["reason"] == "unsupported_event"
        assert project.auto_reingest_error == "unsupported_event"


async def test_pull_request_background_scan_completes(
    session_factory,
    monkeypatch,
    tmp_path,
    log_manager,
):
    import mcp_server.api.routes.webhook as webhook_mod
    from mcp_server.services.project_registry import ProjectRegistry

    monkeypatch.setattr(webhook_mod, "async_session_factory", session_factory)

    class ScannerStub:
        def __init__(self, log_manager):
            self.log_manager = log_manager

        async def run_scan(self, *, project_id, repo_path, platform):
            return {
                "success": True,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "out"),
                "job_count": 2,
            }

    monkeypatch.setattr(webhook_mod, "ScannerService", ScannerStub)
    local_path = tmp_path / "repo"
    local_path.mkdir()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "pr-project",
            str(local_path),
            repo_url="https://github.com/example/repo.git",
            platform="generic",
            source_type="git",
        )
        run = await registry.create_scan_run("pr-project", triggered_by="webhook")
        await session.commit()
        run_id = run.id

    await webhook_mod._webhook_background(
        "pr-project",
        str(local_path),
        "generic",
        run_id,
        log_manager,
        is_push=False,
        before_sha="",
        after_sha="",
        is_first_push=False,
    )

    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("pr-project")
        assert project is not None
        assert project.status == "ready"
        assert project.job_count == 2


def test_parse_name_status_diff_handles_core_statuses():
    changes = _parse_name_status_diff(
        "A\tjobs/new.json\n"
        "M\tjobs/existing.json\n"
        "D\tjobs/deleted.json\n"
        "R100\tjobs/old.json\tjobs/new-name.json\n"
    )

    assert changes == [
        ChangedFile(status="A", path="jobs/new.json", previous_path=None),
        ChangedFile(status="M", path="jobs/existing.json", previous_path=None),
        ChangedFile(status="D", path="jobs/deleted.json", previous_path=None),
        ChangedFile(status="R", path="jobs/new-name.json", previous_path="jobs/old.json"),
    ]


def test_resolve_changed_groups_uses_current_and_previous_paths(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path)
    scan_jobs = tmp_path / "proj" / "scan" / "jobs"
    (scan_jobs / "GroupA").mkdir(parents=True)
    (scan_jobs / "GroupB").mkdir()

    groups = _resolve_changed_groups(
        "proj",
        [
            ChangedFile(status="M", path="src/GroupA/job.json"),
            ChangedFile(
                status="R",
                path="src/renamed/job.json",
                previous_path="src/GroupB/old.json",
            ),
        ],
        is_first_push=False,
    )

    assert groups == {"GroupA", "GroupB"}


def test_resolve_changed_groups_returns_empty_when_unmapped(tmp_path, monkeypatch):
    """When files change but none map to a scan group, result is empty (skip)."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path)
    scan_jobs = tmp_path / "proj" / "scan" / "jobs"
    (scan_jobs / "GroupA").mkdir(parents=True)
    (scan_jobs / "GroupB").mkdir()

    groups = _resolve_changed_groups(
        "proj",
        [ChangedFile(status="M", path="README.md")],
        is_first_push=False,
    )

    assert groups == set()
