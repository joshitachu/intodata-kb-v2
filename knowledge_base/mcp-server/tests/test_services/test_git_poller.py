"""Tests for Git polling auto re-ingestion."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from mcp_server.services.project_registry import ProjectRegistry


def test_next_git_poll_before_midday_uses_same_day_noon():
    import mcp_server.services.git_poller as poller

    now = datetime.datetime(2026, 5, 12, 8, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))

    next_run = poller.next_git_poll_at(
        now,
        timezone_name="Europe/Amsterdam",
        raw_hours="0,12",
    )

    assert next_run == datetime.datetime(2026, 5, 12, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


def test_next_git_poll_after_midday_uses_next_midnight():
    import mcp_server.services.git_poller as poller

    now = datetime.datetime(2026, 5, 12, 12, 1, tzinfo=ZoneInfo("Europe/Amsterdam"))

    next_run = poller.next_git_poll_at(
        now,
        timezone_name="Europe/Amsterdam",
        raw_hours="0,12",
    )

    assert next_run == datetime.datetime(2026, 5, 13, 0, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


def test_next_git_poll_uses_valid_amsterdam_time_across_dst_transition():
    import mcp_server.services.git_poller as poller

    now = datetime.datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))

    next_run = poller.next_git_poll_at(
        now,
        timezone_name="Europe/Amsterdam",
        raw_hours="0,12",
    )

    assert next_run == datetime.datetime(2026, 3, 29, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    assert next_run.utcoffset() == datetime.timedelta(hours=2)


async def test_poller_ignores_non_git_projects(session_factory, monkeypatch, log_manager):
    import mcp_server.services.git_poller as poller

    monkeypatch.setattr(poller, "async_session_factory", session_factory)
    fetch = AsyncMock()
    monkeypatch.setattr(poller, "_fetch_remote", fetch)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            "local-project",
            "/tmp/local-project",
            source_type="local",
        )
        await session.commit()

    await poller.poll_git_projects_once(log_manager)

    fetch.assert_not_awaited()


async def test_poller_does_nothing_when_remote_sha_matches(
    session_factory,
    monkeypatch,
    log_manager,
):
    import mcp_server.services.git_poller as poller

    sha = "a" * 40
    monkeypatch.setattr(poller, "async_session_factory", session_factory)
    monkeypatch.setattr(poller, "_fetch_remote", AsyncMock())
    monkeypatch.setattr(poller, "_remote_head_sha", AsyncMock(return_value=sha))
    reingest = AsyncMock()
    monkeypatch.setattr(poller, "run_webhook_diff_update", reingest)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "git-project",
            "/tmp/git-project",
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = sha
        await session.commit()

    await poller.poll_git_projects_once(log_manager)

    reingest.assert_not_awaited()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("git-project")
        assert project is not None
        assert project.auto_reingest_last_checked_at is not None
        assert project.auto_reingest_error is None


async def test_poller_triggers_diff_update_when_remote_sha_changes(
    session_factory,
    monkeypatch,
    log_manager,
):
    import mcp_server.services.git_poller as poller

    before_sha = "a" * 40
    after_sha = "b" * 40
    monkeypatch.setattr(poller, "async_session_factory", session_factory)
    monkeypatch.setattr(poller, "_fetch_remote", AsyncMock())
    monkeypatch.setattr(poller, "_remote_head_sha", AsyncMock(return_value=after_sha))
    reingest = AsyncMock(return_value=True)
    monkeypatch.setattr(poller, "run_webhook_diff_update", reingest)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "git-project",
            "/tmp/git-project",
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = before_sha
        await session.commit()

    await poller.poll_git_projects_once(log_manager)

    reingest.assert_awaited_once_with(
        project_id="git-project",
        before_sha=before_sha,
        after_sha=after_sha,
        log_mgr=log_manager,
        trigger="poller",
    )
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("git-project")
        assert project is not None
        assert project.auto_reingest_error is None


async def test_failed_reingestion_does_not_advance_commit(
    session_factory,
    monkeypatch,
    log_manager,
):
    import mcp_server.services.git_poller as poller

    before_sha = "a" * 40
    after_sha = "b" * 40
    monkeypatch.setattr(poller, "async_session_factory", session_factory)
    monkeypatch.setattr(poller, "_fetch_remote", AsyncMock())
    monkeypatch.setattr(poller, "_remote_head_sha", AsyncMock(return_value=after_sha))
    monkeypatch.setattr(poller, "run_webhook_diff_update", AsyncMock(return_value=False))

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "git-project",
            "/tmp/git-project",
            repo_url="https://github.com/example/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = before_sha
        await session.commit()

    await poller.poll_git_projects_once(log_manager)

    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("git-project")
        assert project is not None
        assert project.last_ingested_commit_sha == before_sha
        assert project.auto_reingest_error == (
            f"Diff-only auto update failed for remote commit {after_sha}"
        )
