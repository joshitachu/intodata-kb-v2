"""Tests for ProjectRegistry service."""

from __future__ import annotations

import pytest

from mcp_server.services.project_registry import ProjectRegistry


async def test_add_project(db_session):
    registry = ProjectRegistry(db_session)
    p = await registry.add_project("test-proj", "/tmp/test", platform="talend")
    await db_session.commit()

    assert p.id == "test-proj"
    assert p.display_name == "test-proj"
    assert p.platform == "talend"
    assert p.status == "registered"
    assert p.source_type == "local"


async def test_add_project_with_display_name(db_session):
    registry = ProjectRegistry(db_session)
    p = await registry.add_project(
        "proj2", "/tmp/proj2", display_name="My Project", platform="mulesoft"
    )
    await db_session.commit()

    assert p.display_name == "My Project"
    assert p.platform == "mulesoft"


async def test_add_project_updates_existing(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("proj", "/tmp/v1")
    await db_session.commit()

    p2 = await registry.add_project("proj", "/tmp/v2", display_name="Updated")
    await db_session.commit()

    assert p2.local_path == "/tmp/v2"
    assert p2.display_name == "Updated"
    assert p2.status == "registered"


async def test_get_project(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("find-me", "/tmp/find")
    await db_session.commit()

    found = await registry.get_project("find-me")
    assert found is not None
    assert found.id == "find-me"


async def test_get_project_not_found(db_session):
    registry = ProjectRegistry(db_session)
    result = await registry.get_project("nonexistent")
    assert result is None


async def test_list_projects(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("a", "/tmp/a")
    await registry.add_project("b", "/tmp/b")
    await db_session.commit()

    projects = await registry.list_projects()
    assert len(projects) == 2
    ids = {p.id for p in projects}
    assert ids == {"a", "b"}


async def test_update_status(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("s", "/tmp/s")
    await db_session.commit()

    await registry.update_status("s", "scanning")
    await db_session.commit()

    p = await registry.get_project("s")
    assert p.status == "scanning"


async def test_update_status_with_error(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("e", "/tmp/e")
    await db_session.commit()

    await registry.update_status("e", "error", error_message="Something broke")
    await db_session.commit()

    p = await registry.get_project("e")
    assert p.status == "error"
    assert p.error_message == "Something broke"


async def test_record_webhook_observation_merges_with_existing_update_summary(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("webhook-proj", "/tmp/webhook")
    await registry.update_git_update_summary(
        "webhook-proj",
        {"action": "patched", "touched_docs": ["01_ARCHITECTURE_MAP.md"]},
    )
    await db_session.commit()

    await registry.record_webhook_observation(
        "webhook-proj",
        {
            "event": "push",
            "status": "queued",
            "observations": ["webhook_received", "project_matched"],
        },
    )
    await db_session.commit()

    project = await registry.get_project("webhook-proj")
    assert project is not None
    assert project.git_update_summary["action"] == "patched"
    assert project.git_update_summary["webhook"]["event"] == "push"
    assert project.auto_reingest_last_checked_at is not None

    await registry.update_git_update_summary(
        "webhook-proj",
        {"action": "manual_review", "reason": "needs operator"},
    )
    await db_session.commit()

    project = await registry.get_project("webhook-proj")
    assert project is not None
    assert project.git_update_summary["action"] == "manual_review"
    assert project.git_update_summary["webhook"]["status"] == "queued"

    await registry.update_git_remote_metadata(
        "webhook-proj",
        summary={"action": "reingestion_readiness_repair", "repaired": True},
    )
    await db_session.commit()

    project = await registry.get_project("webhook-proj")
    assert project is not None
    assert project.git_update_summary["action"] == "reingestion_readiness_repair"
    assert project.git_update_summary["webhook"]["event"] == "push"


async def test_update_scan_result(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("scan", "/tmp/scan")
    await db_session.commit()

    await registry.update_scan_result(
        "scan", scan_result_path="/out/scan.json", output_dir="/out", job_count=5
    )
    await db_session.commit()

    p = await registry.get_project("scan")
    assert p.status == "ready"
    assert p.job_count == 5
    assert p.last_scan_at is not None


async def test_create_and_complete_scan_run(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("sr", "/tmp/sr")
    await db_session.commit()

    run = await registry.create_scan_run("sr", triggered_by="api")
    await db_session.commit()
    assert run.status == "running"

    await registry.complete_scan_run(run.id, status="completed", job_count=3)
    await db_session.commit()


async def test_create_and_complete_generate_run(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("gr", "/tmp/gr")
    await db_session.commit()

    run = await registry.create_generate_run("gr", triggered_by="api")
    await db_session.commit()
    assert run.status == "running"

    await registry.complete_generate_run(
        run.id,
        status="completed",
        cost_usd=0.50,
        duration_ms=10000,
        doc_count=3,
    )
    await db_session.commit()


async def test_list_generate_runs(db_session):
    registry = ProjectRegistry(db_session)
    await registry.add_project("runs", "/tmp/runs")
    await db_session.commit()

    await registry.create_generate_run("runs", triggered_by="api")
    await registry.create_generate_run("runs", triggered_by="mcp")
    await db_session.commit()

    runs = await registry.list_generate_runs("runs")
    assert len(runs) == 2


async def test_backfill_generate_run_costs(db_session, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(
        config,
        "model_pricing_json",
        '{"deepseek-v4-pro":{"input":0.28,"output":0.88}}',
    )
    registry = ProjectRegistry(db_session)
    await registry.add_project("priced", "/tmp/priced")
    await db_session.commit()

    known = await registry.create_generate_run("priced", triggered_by="api")
    unknown = await registry.create_generate_run("priced", triggered_by="api")
    await db_session.commit()

    await registry.complete_generate_run(
        known.id,
        status="completed",
        model_name="openai/deepseek-v4-pro",
        input_tokens=1_200_000,
        output_tokens=157_800,
    )
    await registry.complete_generate_run(
        unknown.id,
        status="completed",
        model_name="unknown-model",
        input_tokens=1_000,
        output_tokens=1_000,
    )
    await db_session.commit()

    updated = await registry.backfill_generate_run_costs("priced")
    await db_session.commit()

    assert updated == 1
    runs = {run.id: run for run in await registry.list_generate_runs("priced")}
    assert runs[known.id].cost_usd == 0.474864
    assert runs[unknown.id].cost_usd is None
