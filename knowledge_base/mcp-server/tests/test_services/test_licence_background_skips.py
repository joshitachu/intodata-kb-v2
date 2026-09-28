"""Background (non-HTTP) callers skip on a licence refusal: one warning, no raise."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import func, select

from mcp_server.db.models import Project, ScanRun
from mcp_server.licensing.runtime import set_policy
from tests.test_licensing.licence_fixtures import expired_token, install_policy

PROJECT = "bg-project"


@pytest.fixture(autouse=True)
def _reset_policy():
    yield
    set_policy(None)


@pytest.fixture
async def project(session_factory):
    async with session_factory() as db:
        db.add(
            Project(
                id=PROJECT,
                display_name=PROJECT,
                local_path="/tmp/bg-project",
                platform="talend",
                source_type="git",
                status="ready",
            )
        )
        await db.commit()
    return PROJECT


def _licence_warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("licence refused")]


async def test_git_reingestion_skips_a_refused_project_without_raising(
    session_factory, project, monkeypatch, caplog
):
    """The poller/webhook path: returns False, records why, leaves the commit marker."""
    import mcp_server.services.git_reingestion as git_reingestion

    monkeypatch.setattr(git_reingestion, "async_session_factory", session_factory)

    async def _must_not_pull(*_args, **_kwargs):
        raise AssertionError("a refused re-ingestion must stop before touching the repo")

    monkeypatch.setattr(git_reingestion, "pull_remote_ff_only", _must_not_pull)
    install_policy(expired_token())
    sink: dict = {}

    with caplog.at_level(logging.WARNING, logger="mcp_server.licensing.usage"):
        ok = await git_reingestion.run_git_reingestion(
            PROJECT, "a" * 40, "b" * 40, triggered_by="poller", log_mgr=None, outcome_sink=sink,
        )

    assert ok is False
    assert sink == {"skipped": False, "held": True, "held_reason_code": "licence_expired"}
    warnings = _licence_warnings(caplog)
    assert len(warnings) == 1
    assert "code=licence_expired" in warnings[0].getMessage()
    async with session_factory() as db:
        assert await db.scalar(select(func.count(ScanRun.id))) == 0
        refreshed = await db.get(Project, PROJECT)
        assert refreshed.status == "ready"  # never flipped to "scanning"
        assert refreshed.last_ingested_commit_sha is None  # retried after renewal


async def test_mcp_trigger_scan_tool_returns_a_licence_error(
    session_factory, project, monkeypatch, caplog
):
    from mcp_server.mcp import server as mcp_tools

    monkeypatch.setattr(mcp_tools, "async_session_factory", session_factory)
    install_policy(expired_token())

    with caplog.at_level(logging.WARNING, logger="mcp_server.licensing.usage"):
        result = await mcp_tools._dispatch("trigger_scan", {"project_id": PROJECT})

    assert result["licence"]["code"] == "licence_expired"
    assert result["error"]
    assert len(_licence_warnings(caplog)) == 1
    async with session_factory() as db:
        assert await db.scalar(select(func.count(ScanRun.id))) == 0
        assert (await db.get(Project, PROJECT)).status == "ready"


async def test_mcp_insert_to_rag_tool_is_refused(session_factory, project, monkeypatch):
    from mcp_server.mcp import server as mcp_tools

    monkeypatch.setattr(mcp_tools, "async_session_factory", session_factory)

    async def _must_not_push(*_args, **_kwargs):
        raise AssertionError("refused before pushing")

    monkeypatch.setattr(mcp_tools.rag_bridge, "push_docs", _must_not_push)
    install_policy(expired_token())

    result = await mcp_tools._dispatch("insert_to_rag", {"project_id": PROJECT})

    assert result["licence"]["code"] == "licence_expired"


async def test_auto_doc_update_defers_on_refusal_before_any_model_work(
    tmp_path, monkeypatch, caplog
):
    """Webhook, poller and repair-latest all enter the patch lane here; it spends
    tokens without a run record, so it must refuse before loading anything."""

    import mcp_server.services.auto_doc_update as auto_doc_update

    monkeypatch.setattr(auto_doc_update.config, "project_root", tmp_path)

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("a refused update must not reach the patch lane")

    service = auto_doc_update.AutoDocUpdateService(log_mgr=None)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_run_serialized", _must_not_run)
    install_policy(expired_token())

    with caplog.at_level(logging.WARNING, logger="mcp_server.licensing.usage"):
        ok = await service.run(project_id=PROJECT, before_sha="a" * 40, after_sha="b" * 40,
                               trigger="poller")

    assert ok is False
    assert len(_licence_warnings(caplog)) == 1
    journal_text = "\n".join(p.read_text() for p in (tmp_path / "output" / PROJECT).rglob("*") if p.is_file())
    assert "licence refused: licence_expired" in journal_text
    assert "deferred" in journal_text


async def test_auto_doc_update_runs_when_enforcement_is_off(tmp_path, monkeypatch):
    import mcp_server.services.auto_doc_update as auto_doc_update

    monkeypatch.setattr(auto_doc_update.config, "project_root", tmp_path)
    called = {}

    async def _serialized(**kwargs):
        called.update(kwargs)
        return True

    service = auto_doc_update.AutoDocUpdateService(log_mgr=None)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_run_serialized", _serialized)
    install_policy(None, enforce=False)

    assert await service.run(project_id=PROJECT, before_sha="a" * 40, after_sha="b" * 40) is True
    assert called["project_id"] == PROJECT


@pytest.mark.parametrize("tool,args", [
    ("_retain_findings", {"project_id": PROJECT, "findings": "x"}),
    ("_retain_process_lesson", {"lesson": "x"}),
])
async def test_hindsight_retain_tools_refuse_without_a_licence(tool, args, monkeypatch):
    """Hindsight runs a model on retain; the stdio tools must not spend it unlicensed."""
    import mcp_server.mcp.server as server

    monkeypatch.setattr(server.config, "hindsight_enabled", True)

    async def _must_not_retain(*_a, **_k):
        raise AssertionError("a refused retain must not reach Hindsight")

    for name in ("ensure_project_bank", "ensure_process_bank", "retain_project_findings",
                 "retain_process_lesson"):
        monkeypatch.setattr(server.memory_service, name, _must_not_retain)
    install_policy(expired_token())

    result = await getattr(server, tool)(args)
    assert result["licence"]["code"] == "licence_expired"
