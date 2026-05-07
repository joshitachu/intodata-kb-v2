"""Tests for GitHub webhook helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest

from mcp_server.api.routes.webhook import (
    ChangedFile,
    _parse_name_status_diff,
    _resolve_changed_groups,
)


def _signed_headers(secret: str, payload: bytes, event: str) -> dict[str, str]:
    signature = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Signature-256": signature,
        "Content-Type": "application/json",
    }


def _repo_payload(clone_url: str) -> bytes:
    return json.dumps({"repository": {"clone_url": clone_url}}).encode()


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
        await registry.add_project(
            "registered-repo",
            str(local_path),
            repo_url="https://github.com/example/repo",
            platform="generic",
            source_type="git",
        )
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


async def test_ping_with_invalid_signature_returns_401(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "github_webhook_secret", "test-webhook-secret")

    payload = _repo_payload("https://github.com/example/repo.git")
    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Signature-256": "sha256=invalid",
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 401


@pytest.mark.parametrize("event", ["push", "pull_request"])
async def test_signed_supported_events_keep_existing_registered_flow(
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

    resp = await client.post(
        "/webhook/github/webhook",
        content=payload,
        headers=_signed_headers(secret, payload, event),
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "webhook_triggered", "project_id": "existing-flow"}
    background_mock.assert_awaited_once()


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
            ChangedFile(status="R", path="src/renamed/job.json", previous_path="src/GroupB/old.json"),
        ],
        is_first_push=False,
    )

    assert groups == {"GroupA", "GroupB"}


def test_resolve_changed_groups_falls_back_to_all_when_unmapped(tmp_path, monkeypatch):
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

    assert groups == {"GroupA", "GroupB"}
