"""Tests for Slice 5 (Living-Document Reingestion, US-7/US-8):

- drift counters exposed on the read-only GET .../reingestion-status surface
  (no scan/generation/patch/RAG-sync side effects), and
- the explicit POST .../git-updates/full-rebuild manual trigger, which is
  the only path that may set full_rederive=True on run_git_reingestion.
"""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, patch


async def test_reingestion_status_reports_drift_counters_from_change_history(
    client, session_factory, tmp_path
):
    from mcp_server.config import config
    from mcp_server.services.doc_version_history import DocVersionHistoryRepository, record_push_outcome
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "drift-status"
    repo = tmp_path / "repo-drift"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    output_dir = config.project_root / "output" / project_id

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id,
            str(repo),
            repo_url="https://github.com/acme/drift.git",
            platform="generic",
            source_type="git",
        )
        await session.commit()

    # AC1: three amendments since the (nonexistent) last rebuild.
    for i in range(3):
        record_push_outcome(
            project_id,
            {
                "before": "a" * 40,
                "after": "b" * 40,
                "outcome": "bounded_patch",
                "touched_docs": [f"doc_{i}.md"],
                "reason_code": "true_patch_succeeded",
            },
            output_dir=output_dir,
        )
    # AC2: one of those pushes also failed the S2 fact-check for one group.
    record_push_outcome(
        project_id,
        {
            "before": "b" * 40,
            "after": "c" * 40,
            "outcome": "bounded_patch",
            "touched_docs": ["doc_x.md"],
            "reason_code": "true_patch_succeeded",
            "fallback_reasons": {"group_a": "diff_fidelity_failed"},
        },
        output_dir=output_dir,
    )

    with (
        patch("mcp_server.api.routes.projects.ScannerService.run_scan", new=AsyncMock()) as scan,
        patch(
            "mcp_server.api.routes.projects.DocGenerationCoordinator.generate_docs",
            new=AsyncMock(),
        ) as generate,
        patch("mcp_server.api.routes.projects.AutoDocUpdateService.run", new=AsyncMock()) as patcher,
        patch("mcp_server.api.routes.projects.run_git_reingestion", new=AsyncMock()) as reingest,
        patch("mcp_server.api.routes.projects.RagBridge.push_docs", new=AsyncMock()) as rag_push,
    ):
        resp = await client.get(f"/mcp/projects/{project_id}/reingestion-status")

    assert resp.status_code == 200
    data = resp.json()
    drift = data["drift"]
    assert drift["amendments_since_rebuild"] == 4, "AC1: N amendments since last rebuild"
    assert drift["failed_fact_checks_since_rebuild"] == 1, "AC2: failed-check counter increments"
    assert drift["last_reconciled_after_sha"] is None, (
        "AC3: no manual rebuild has happened yet in this history"
    )

    # AC3: the status surface must perform NO scan/generation/patch/sync work.
    scan.assert_not_awaited()
    generate.assert_not_awaited()
    patcher.assert_not_awaited()
    reingest.assert_not_awaited()
    rag_push.assert_not_awaited()

    repo_check = DocVersionHistoryRepository(project_id, output_dir)
    assert len(repo_check.read_all()) == 4, "route must not have written anything either"


async def test_reingestion_status_reports_last_reconciled_after_manual_rebuild(
    client, session_factory, tmp_path
):
    from mcp_server.config import config
    from mcp_server.services.doc_version_history import record_push_outcome
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "drift-reconciled"
    repo = tmp_path / "repo-reconciled"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    output_dir = config.project_root / "output" / project_id

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id,
            str(repo),
            repo_url="https://github.com/acme/reconciled.git",
            platform="generic",
            source_type="git",
        )
        await session.commit()

    record_push_outcome(
        project_id,
        {
            "before": "a" * 40,
            "after": "b" * 40,
            "outcome": "bounded_patch",
            "touched_docs": ["doc.md"],
            "reason_code": "true_patch_succeeded",
        },
        output_dir=output_dir,
    )
    record_push_outcome(
        project_id,
        {
            "before": "b" * 40,
            "after": "c" * 40,
            "outcome": "full_regen",
            "touched_docs": ["01_ARCHITECTURE_MAP.md"],
            "reason_code": config.manual_full_rebuild_reason_code,
        },
        output_dir=output_dir,
    )
    record_push_outcome(
        project_id,
        {
            "before": "c" * 40,
            "after": "d" * 40,
            "outcome": "bounded_patch",
            "touched_docs": ["doc2.md"],
            "reason_code": "true_patch_succeeded",
        },
        output_dir=output_dir,
    )

    resp = await client.get(f"/mcp/projects/{project_id}/reingestion-status")
    assert resp.status_code == 200
    drift = resp.json()["drift"]
    assert drift["amendments_since_rebuild"] == 1, "only the push AFTER the reset should count"
    assert drift["last_reconciled_before_sha"] == "b" * 40
    assert drift["last_reconciled_after_sha"] == "c" * 40


async def test_full_rebuild_trigger_invokes_full_rederive_and_returns_202_style_ack(
    client, session_factory, tmp_path, monkeypatch
):
    """AC4: triggering the manual full-rebuild action runs a full generation
    (full_rederive=True) as a background task."""
    import mcp_server.api.routes.projects as projects_mod
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "full-rebuild-trigger"
    repo = tmp_path / "repo-trigger"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            project_id,
            str(repo),
            repo_url="https://github.com/acme/trigger.git",
            platform="generic",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        project.status = "ready"
        await session.commit()

    captured: dict[str, object] = {}

    async def fake_reingestion(project_id, before_sha, after_sha, **kwargs):
        captured["project_id"] = project_id
        captured["before_sha"] = before_sha
        captured["after_sha"] = after_sha
        captured["kwargs"] = kwargs
        return True

    monkeypatch.setattr(projects_mod, "run_git_reingestion", fake_reingestion)

    with patch(
        "mcp_server.api.routes.projects.run_git",
        new=AsyncMock(return_value="c" * 40),
    ), patch("mcp_server.api.routes.projects.pull_remote_ff_only", new=AsyncMock()):
        resp = await client.post(f"/mcp/projects/{project_id}/git-updates/full-rebuild")

    assert resp.status_code == 200
    assert resp.json()["status"] == "full_rebuild_triggered"

    # background_tasks execute synchronously in the ASGI test transport used
    # by this project's client fixture (see other repair-latest/generate
    # tests in test_projects.py for the same assumption); if not, this
    # assertion is exercised on the next event-loop tick by TestClient.
    import asyncio

    await asyncio.sleep(0)
    assert captured.get("project_id") == project_id
    assert captured.get("before_sha") == "a" * 40
    assert captured.get("after_sha") == "c" * 40
    assert captured["kwargs"]["full_rederive"] is True
    assert captured["kwargs"]["targeted_docs"] is None


async def test_full_rebuild_trigger_rejects_non_git_project(client, session_factory, tmp_path):
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "full-rebuild-nongit"
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id,
            str(tmp_path / "local-only"),
            source_type="local",
            platform="generic",
        )
        await session.commit()

    resp = await client.post(f"/mcp/projects/{project_id}/git-updates/full-rebuild")
    assert resp.status_code == 400


async def test_full_rebuild_trigger_rejects_concurrent_generation(client, session_factory, tmp_path):
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "full-rebuild-busy"
    repo = tmp_path / "repo-busy"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            project_id,
            str(repo),
            repo_url="https://github.com/acme/busy.git",
            source_type="git",
            platform="generic",
        )
        project.status = "generating"
        await session.commit()

    resp = await client.post(f"/mcp/projects/{project_id}/git-updates/full-rebuild")
    assert resp.status_code == 409


async def test_full_rebuild_trigger_accepts_explicit_after_sha_without_git_calls(
    client, session_factory, tmp_path
):
    """When the caller already knows after_sha, no local git subprocess should run."""
    import mcp_server.api.routes.projects as projects_mod
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "full-rebuild-explicit-sha"
    repo = tmp_path / "repo-explicit"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            project_id,
            str(repo),
            repo_url="https://github.com/acme/explicit.git",
            source_type="git",
            platform="generic",
        )
        project.last_ingested_commit_sha = "a" * 40
        project.status = "ready"
        await session.commit()

    async def fake_reingestion(project_id, before_sha, after_sha, **kwargs):
        return True

    with (
        patch.object(projects_mod, "run_git_reingestion", new=fake_reingestion),
        patch("mcp_server.api.routes.projects.run_git", new=AsyncMock()) as run_git_mock,
        patch("mcp_server.api.routes.projects.pull_remote_ff_only", new=AsyncMock()) as pull_mock,
    ):
        resp = await client.post(
            f"/mcp/projects/{project_id}/git-updates/full-rebuild",
            json={"after_sha": "f" * 40},
        )

    assert resp.status_code == 200
    run_git_mock.assert_not_awaited()
    pull_mock.assert_not_awaited()
