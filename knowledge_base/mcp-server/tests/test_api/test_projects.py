"""Tests for project management REST API endpoints."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import zipfile
from unittest.mock import AsyncMock, patch

from sqlalchemy import select


# --- Add Repo ---


async def test_add_repo_local_path(client, tmp_path):
    # Create a local path that exists
    local = tmp_path / "data" / "repos" / "myproject"
    local.mkdir(parents=True)

    resp = await client.post("/mcp/add-repo", json={
        "url": str(local),
        "project_id": "myproject",
        "platform": "talend",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "myproject"
    assert data["platform"] == "talend"
    assert data["status"] == "registered"


async def test_add_repo_zip_placeholder(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "zipproj",
        "platform": "talend",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "zipproj"
    assert data["status"] == "registered"


async def test_add_repo_invalid_url(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "not_a_valid_url_or_path",
        "project_id": "bad",
        "platform": "talend",
    })
    assert resp.status_code == 400


async def test_add_repo_invalid_project_id_path_traversal(client):
    """Security: project_id with path traversal must be rejected."""
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "../etc",
        "platform": "talend",
    })
    assert resp.status_code == 422


async def test_add_repo_invalid_project_id_semicolon(client):
    """Security: project_id with shell metacharacters must be rejected."""
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "foo;rm",
        "platform": "talend",
    })
    assert resp.status_code == 422


async def test_add_repo_invalid_project_id_slash(client):
    """Security: project_id with slash must be rejected."""
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "a/b",
        "platform": "talend",
    })
    assert resp.status_code == 422


async def test_add_repo_valid_project_id_with_hyphens_underscores(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "valid-project_1",
        "platform": "talend",
    })
    assert resp.status_code == 200
    assert resp.json()["id"] == "valid-project_1"


# --- Upload ZIP ---


async def test_upload_zip_success(client, tmp_path):
    # Create a valid ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    buf.seek(0)

    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.zip", buf, "application/zip")},
        data={"project_id": "ziptest", "platform": "talend"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "ziptest"


async def test_upload_zip_invalid_project_id(client):
    """Security: upload_zip form endpoint validates project_id."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    buf.seek(0)

    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.zip", buf, "application/zip")},
        data={"project_id": "../etc/passwd", "platform": "talend"},
    )
    assert resp.status_code == 400
    assert "Invalid project_id" in resp.json()["detail"]


async def test_upload_zip_not_zip_file(client):
    buf = io.BytesIO(b"not a zip")
    resp = await client.post(
        "/mcp/upload-zip",
        files={"file": ("test.txt", buf, "text/plain")},
        data={"project_id": "proj", "platform": "talend"},
    )
    assert resp.status_code == 400


# --- Project List ---


async def test_list_projects_empty(client):
    resp = await client.get("/mcp/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["projects"] == []


async def test_list_projects_returns_added(client):
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "p1", "platform": "talend"
    })
    resp = await client.get("/mcp/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["projects"][0]["plugin_pack_id"] is None


# --- Status ---


async def test_get_status(client):
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "statproj", "platform": "talend"
    })
    resp = await client.get("/mcp/status/statproj")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == "statproj"
    assert data["status"] == "registered"


async def test_get_status_not_found(client):
    resp = await client.get("/mcp/status/nonexistent")
    assert resp.status_code == 404


# --- Reset Status ---


async def test_reset_status_not_generating(client):
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "resetproj", "platform": "talend"
    })
    resp = await client.post("/mcp/reset-status", json={"project_id": "resetproj"})
    assert resp.status_code == 400


async def test_reset_status_cancels_live_generation_task(client, session_factory):
    from mcp_server.api.routes import projects as project_routes
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "cancelproj"
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id=project_id,
            local_path="/tmp/cancelproj",
            platform="generic",
            source_type="zip",
        )
        await registry.update_status(project_id, "generating")
        run = await registry.create_generate_run(project_id, triggered_by="api")
        await session.commit()

    task = asyncio.create_task(asyncio.sleep(60))
    project_routes._generation_tasks[project_id] = task
    project_routes._currently_generating.add(project_id)

    resp = await client.post("/mcp/reset-status", json={"project_id": project_id})

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert task.cancelled() or task.cancelling()
    assert project_id not in project_routes._generation_tasks
    assert project_id not in project_routes._currently_generating

    async with session_factory() as session:
        from mcp_server.db.models import GenerateRun, Project

        project = (await session.execute(select(Project).where(Project.id == project_id))).scalar_one()
        saved_run = (await session.execute(select(GenerateRun).where(GenerateRun.id == run.id))).scalar_one()
        assert project.status == "error"
        assert project.error_message == "Generation cancelled by user"
        assert saved_run.status == "failed"
        assert saved_run.error_message == "Generation cancelled by user"
        assert saved_run.run_verdict == "failed"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_scan_repairs_stale_managed_repo_path(client, session_factory, tmp_path):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    repo = config.repos_dir / "nexus"
    repo.mkdir(parents=True)
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id="nexus",
            local_path="/home/intodata/intodata-kb/knowledge_base/data/repos/nexus",
            platform="generic",
            source_type="zip",
        )
        await session.commit()

    with patch(
        "mcp_server.api.routes.projects.ScannerService.run_scan",
        new=AsyncMock(
            return_value={
                "success": True,
                "job_count": 0,
                "scan_result_path": str(tmp_path / "scan_result.json"),
                "output_dir": str(tmp_path / "output"),
            }
        ),
    ) as run_scan:
        resp = await client.post("/mcp/scan", json={"project_id": "nexus"})

    assert resp.status_code == 200
    run_scan.assert_awaited_once()
    assert run_scan.await_args.kwargs["repo_path"] == str(repo)

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.get_project("nexus")
        assert project is not None
        assert project.local_path == str(repo)


async def test_reingestion_readiness_repair_cleans_remote_without_workflow_side_effects(
    client,
    session_factory,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://user:credential@example.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    output_dir = config.project_root / "output" / "gitproj"
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "group.md").write_text("analysis", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "gitproj",
            str(repo),
            repo_url="https://user:credential@example.com/acme/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()

    report_resp = await client.get("/mcp/projects/gitproj/reingestion-readiness")
    assert report_resp.status_code == 200
    report = report_resp.json()
    assert report["status"] == "needs_remote_repair"
    assert report["origin_had_credentials"] is True
    assert report["repo_url_had_credentials"] is True
    assert report["has_docs"] is True
    assert report["has_rag_support"] is True
    assert report["rag_support_analysis_count"] == 1
    assert report["analysis_cache_count"] == 1
    assert report["targeted_regen_ready"] is True
    assert report["requires_full_rebaseline"] is False
    assert "user:credential@" not in json.dumps(report)

    repair_resp = await client.post("/mcp/projects/gitproj/reingestion-readiness/repair")
    assert repair_resp.status_code == 200
    repaired = repair_resp.json()
    assert repaired["repaired"] is True
    assert repaired["origin_had_credentials"] is False
    assert repaired["repo_url_had_credentials"] is False
    assert repaired["ready"] is True
    assert repaired["targeted_regen_ready"] is True
    assert repaired["requires_full_rebaseline"] is False
    assert repaired["reason_codes"] == []
    assert "user:credential@" not in json.dumps(repaired)

    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    assert origin == "https://example.com/acme/repo.git"

    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project("gitproj")
        assert project is not None
        assert project.repo_url == "https://example.com/acme/repo.git"
        assert project.last_ingested_commit_sha == "a" * 40
        assert project.last_scan_at is None
        assert project.last_generate_at is None
        assert project.last_rag_push_at is None
        assert project.git_update_summary["action"] == "reingestion_readiness_repair"
        assert project.git_update_summary["targeted_regen_ready"] is True
        assert project.git_update_summary["requires_full_rebaseline"] is False


async def test_reingestion_readiness_reports_full_rebaseline_for_missing_support(
    client,
    session_factory,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    repo = tmp_path / "repo-missing-support"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    output_dir = config.project_root / "output" / "gitproj-cache-gap"
    docs = output_dir / "docs"
    cache = output_dir / "analysis_cache"
    docs.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            "gitproj-cache-gap",
            str(repo),
            repo_url="https://example.com/acme/repo.git",
            source_type="git",
        )
        project.last_ingested_commit_sha = "a" * 40
        await session.commit()

    resp = await client.get("/mcp/projects/gitproj-cache-gap/reingestion-readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "requires_full_rebaseline"
    assert data["ready"] is False
    assert data["has_docs"] is True
    assert data["has_analysis_cache"] is True
    assert data["has_rag_support"] is False
    assert data["targeted_regen_ready"] is False
    assert data["requires_full_rebaseline"] is True
    assert data["reason_codes"] == ["missing_rag_support"]


async def test_reingestion_status_aggregates_operator_evidence_without_workflow_side_effects(
    client,
    session_factory,
    tmp_path,
):
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "status-operator"
    repo = tmp_path / "repo-status"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/acme/status.git"],
        cwd=repo,
        check=True,
    )

    output_dir = config.project_root / "output" / project_id
    docs = output_dir / "docs"
    support = docs / "_rag_support" / "phase2_analysis"
    cache = output_dir / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("# Architecture", encoding="utf-8")
    (support / "group.md").write_text("analysis", encoding="utf-8")
    (cache / "group.md").write_text("analysis", encoding="utf-8")

    before_sha = "a" * 40
    after_sha = "b" * 40
    summary = {
        "action": "failed",
        "outcome": "failed",
        "reason_code": "rag_sync_failed",
        "reason": "RAG push failed: rag unavailable",
        "error": "RAG push failed: rag unavailable",
        "before": before_sha,
        "after": after_sha,
        "decision_strategy": "bounded_patch",
        "fallback_used": False,
        "targeted_regen_ready": True,
        "requires_full_rebaseline": False,
        "touched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
        "patched_docs": ["04_BUSINESS_RULES_AND_TRANSFORMATIONS.md"],
        "rag_result": {"pushed": 0, "errors": ["rag unavailable"]},
        "webhook": {
            "event": "push",
            "delivery_id": "delivery-status",
            "repo_url": "https://github.com/acme/status.git",
            "canonical_repo": "https://github.com/acme/status",
            "registered": True,
            "project_id": project_id,
            "status": "queued",
            "observations": ["webhook_received", "project_matched"],
            "before_sha": before_sha,
            "after_sha": after_sha,
        },
    }

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            project_id,
            str(repo),
            repo_url="https://github.com/acme/status.git",
            platform="generic",
            source_type="git",
        )
        project.status = "ready"
        project.github_webhook_id = 42
        project.last_ingested_commit_sha = before_sha
        project.git_update_summary = summary
        await session.commit()

    with (
        patch("mcp_server.api.routes.projects.ScannerService.run_scan", new=AsyncMock()) as scan,
        patch(
            "mcp_server.api.routes.projects.DocGenerationCoordinator.generate_docs",
            new=AsyncMock(),
        ) as generate,
        patch("mcp_server.api.routes.projects.AutoDocUpdateService.run", new=AsyncMock()) as patcher,
        patch("mcp_server.api.routes.projects.RagBridge.push_docs", new=AsyncMock()) as rag_push,
    ):
        resp = await client.get(f"/mcp/projects/{project_id}/reingestion-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == project_id
    assert data["project_status"] == "ready"
    assert data["last_ingested_commit_sha"] == before_sha
    assert data["webhook"]["registered"] is True
    assert data["webhook"]["status"] == "active"
    assert data["webhook"]["webhook_id"] == 42
    assert data["webhook"]["last_observation"]["event"] == "push"
    assert data["webhook"]["last_observation"]["delivery_id"] == "delivery-status"
    assert data["webhook"]["last_observation"]["before_sha"] == before_sha
    assert data["webhook"]["last_observation"]["after_sha"] == after_sha
    assert data["last_update"]["action"] == "failed"
    assert data["last_update"]["outcome"] == "failed"
    assert data["last_update"]["reason_code"] == "rag_sync_failed"
    assert data["last_update"]["error"] == "RAG push failed: rag unavailable"
    assert data["last_update"]["rag_result"]["errors"] == ["rag unavailable"]
    assert data["remote_hygiene"]["status"] == "ready"
    assert data["remote_hygiene"]["ready"] is True
    assert data["remote_hygiene"]["origin_matches_repo_url"] is True
    assert data["remote_hygiene"]["origin_had_credentials"] is False
    assert data["cache"]["has_last_ingested_commit"] is True
    assert data["cache"]["has_docs"] is True
    assert data["cache"]["has_rag_support"] is True
    assert data["cache"]["has_analysis_cache"] is True
    assert data["cache"]["targeted_regen_ready"] is True
    assert data["cache"]["requires_full_rebaseline"] is False

    scan.assert_not_awaited()
    generate.assert_not_awaited()
    patcher.assert_not_awaited()
    rag_push.assert_not_awaited()

    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project(project_id)
        assert project is not None
        assert project.last_ingested_commit_sha == before_sha
        assert project.last_scan_at is None
        assert project.last_generate_at is None
        assert project.last_rag_push_at is None
        assert project.git_update_summary == summary


async def test_reingestion_status_surfaces_held_and_gate_fields(client, session_factory, tmp_path):
    """Slice 1: a held commit exposes additive `held` + `gate` fields."""
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "status-held"
    output_dir = config.project_root / "output" / project_id
    (output_dir / "docs").mkdir(parents=True)
    (output_dir / "docs" / "01_X.md").write_text("# X", encoding="utf-8")

    summary = {
        "trigger": "webhook",
        "mode": "git_reingestion",
        "action": "manual_review",
        "outcome": "manual_review",
        "reason_code": "correctness_gate_failed",
        "reason": "1 changed unit(s) absent from generated docs: job_alpha",
        "manual_review_reason": "1 changed unit(s) absent from generated docs: job_alpha",
        "before": "a" * 40,
        "after": "b" * 40,
        "held": True,
        "gate": {
            "passed": False,
            "evaluated": True,
            "missing_units": ["job_alpha"],
            "empty_analysis_groups": [],
            "failing_groups": ["A"],
            "reason": "1 changed unit(s) absent from generated docs: job_alpha",
        },
    }

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.add_project(
            project_id,
            str(tmp_path / "repo-held"),
            repo_url="https://github.com/acme/held.git",
            platform="generic",
            source_type="git",
        )
        project.status = "error"
        project.last_ingested_commit_sha = "a" * 40
        project.git_update_summary = summary
        await session.commit()

    resp = await client.get(f"/mcp/projects/{project_id}/reingestion-status")

    assert resp.status_code == 200
    data = resp.json()
    last = data["last_update"]
    assert last["held"] is True
    assert last["reason_code"] == "correctness_gate_failed"
    assert last["manual_review_reason"].endswith("job_alpha")
    assert last["gate"]["missing_units"] == ["job_alpha"]
    assert last["gate"]["passed"] is False
    # SHA held at the prior commit.
    assert data["last_ingested_commit_sha"] == "a" * 40


async def test_run_generate_background_gate_fail_skips_swap_and_holds(
    client, session_factory, tmp_path, monkeypatch
):
    """A failed pre-swap gate must not swap docs and must mark error+manual_review."""
    from unittest.mock import AsyncMock, MagicMock

    import mcp_server.api.routes.projects as projects_mod
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry
    from mcp_server.services.reingestion_gate import GateOutcome

    project_id = "gate-fail"
    output_dir = config.project_root / "output" / project_id
    staging = output_dir / "docs_staging"
    docs = output_dir / "docs"
    staging.mkdir(parents=True)
    docs.mkdir(parents=True)
    (staging / "01_X.md").write_text("# new staging doc", encoding="utf-8")
    (docs / "01_X.md").write_text("# OLD published doc", encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(
            project_id, str(tmp_path / "repo-gf"), source_type="git"
        )
        run = await registry.create_generate_run(project_id, triggered_by="webhook")
        await session.commit()
        run_id = run.id

    async def fake_generate_docs(*args, **kwargs):
        return {"success": True, "model_name": "stub", "write_model_name": "stub"}

    monkeypatch.setattr(
        projects_mod.DocGenerationCoordinator, "generate_docs", fake_generate_docs
    )
    swap_spy = MagicMock()
    monkeypatch.setattr(projects_mod, "swap_docs", swap_spy)

    def failing_gate(_staging_dir):
        return GateOutcome(
            passed=False, evaluated=True, missing_units=["job_alpha"], failing_groups=["A"],
            reason="1 changed unit(s) absent from generated docs: job_alpha",
        )

    from mcp_server.api.app import app as _app

    projects_mod._currently_generating.add(project_id)
    await projects_mod._run_generate_background(
        project_id, run_id, _app.state.log_manager,
        resume=True, pre_swap_gate=failing_gate,
    )

    swap_spy.assert_not_called()
    # Old published docs untouched; staging preserved for revisit.
    assert (docs / "01_X.md").read_text(encoding="utf-8") == "# OLD published doc"
    assert (staging / "01_X.md").exists()
    async with session_factory() as session:
        project = await ProjectRegistry(session).get_project(project_id)
        assert project is not None
        assert project.status == "error"
        assert project.error_message.startswith("manual_review:")


# --- Logs ---


async def test_list_logs_empty(client):
    resp = await client.get("/mcp/logs/testproj")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


async def test_log_content_path_traversal(client):
    """Security: .. in log filename must be rejected."""
    await client.post("/mcp/add-repo", json={
        "url": "zip", "project_id": "logproj", "platform": "talend"
    })
    resp = await client.get("/mcp/logs/logproj/logproj_..evil.log")
    assert resp.status_code == 400
    assert "Invalid filename" in resp.json()["detail"]


# --- EP-000 metadata and review endpoints ---


async def _completed_run(session_factory, project_id: str, **kwargs) -> int:
    from mcp_server.db.models import GenerateRun
    from mcp_server.services.project_registry import ProjectRegistry

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        run = await registry.create_generate_run(project_id)
        await registry.complete_generate_run(run.id, status="completed", **kwargs)
        await session.commit()
        result = await session.execute(select(GenerateRun).where(GenerateRun.id == run.id))
        return result.scalar_one().id


async def test_generate_run_compare_includes_models_quality_deltas(client, session_factory):
    from mcp_server.config import config

    await client.post("/mcp/add-repo", json={"url": "zip", "project_id": "cmp", "platform": "talend"})
    run_a = await _completed_run(
        session_factory,
        "cmp",
        doc_count=2,
        cost_usd=1.0,
        duration_ms=1000,
        input_tokens=100,
        output_tokens=50,
        model_name="moonshot/kimi-k2.6",
        write_model_name="openai/deepseek-v4-pro",
        quality_score=4.5,
    )
    run_b = await _completed_run(
        session_factory,
        "cmp",
        doc_count=3,
        cost_usd=1.4,
        duration_ms=900,
        input_tokens=140,
        output_tokens=70,
        model_name="openai/gpt-4o",
        write_model_name="anthropic/claude-sonnet-4-6",
        quality_score=4.0,
    )

    logs_dir = config.project_root / "output" / "cmp" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / f"run_metrics_{run_a}.json").write_text(json.dumps({
        "run_id": run_a,
        "analysis_model": "moonshot/kimi-k2.6",
        "write_model": "openai/deepseek-v4-pro",
        "provider": "custom",
        "cost_usd": 1.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "duration_ms": 1000,
        "doc_quality": {"overall": 4.5, "source_alignment": 4.0},
    }), encoding="utf-8")
    (logs_dir / f"run_metrics_{run_b}.json").write_text(json.dumps({
        "run_id": run_b,
        "analysis_model": "openai/gpt-4o",
        "write_model": "anthropic/claude-sonnet-4-6",
        "provider": "custom",
        "cost_usd": 1.4,
        "input_tokens": 140,
        "output_tokens": 70,
        "duration_ms": 900,
        "doc_quality": {"overall": 4.0, "source_alignment": 4.2},
    }), encoding="utf-8")

    resp = await client.get(f"/mcp/generate-runs/cmp/compare?a={run_a}&b={run_b}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_a"]["analysis_model"] == "moonshot/kimi-k2.6"
    assert data["run_b"]["write_model"] == "anthropic/claude-sonnet-4-6"
    assert data["delta"]["cost_usd"] == 0.4
    assert data["delta"]["quality"] == -0.5
    assert data["verdict"] == "FAIL"
    assert data["run_a"]["doc_quality"]["source_alignment"] == 4.0


async def test_generate_run_compare_warns_when_metrics_missing(client, session_factory):
    await client.post("/mcp/add-repo", json={"url": "zip", "project_id": "cmp-missing", "platform": "talend"})
    run_a = await _completed_run(session_factory, "cmp-missing", doc_count=1, quality_score=4.2)
    run_b = await _completed_run(session_factory, "cmp-missing", doc_count=1, quality_score=4.1)

    resp = await client.get(f"/mcp/generate-runs/cmp-missing/compare?a={run_a}&b={run_b}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_a"]["metrics_found"] is False
    assert any(check["metric"] == "metrics" and check["result"] == "WARN" for check in data["checks"])


async def test_quality_history_flags_regression(client):
    from mcp_server.config import config

    logs_dir = config.project_root / "output" / "hist" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "run_metrics_1.json").write_text(json.dumps({
        "run_id": 1,
        "started_at": "2026-01-01T00:00:00Z",
        "doc_quality": {"overall": 4.5},
    }), encoding="utf-8")
    (logs_dir / "run_metrics_2.json").write_text(json.dumps({
        "run_id": 2,
        "started_at": "2026-01-02T00:00:00Z",
        "doc_quality": {"overall": 4.0},
    }), encoding="utf-8")

    resp = await client.get("/mcp/projects/hist/quality-history")
    assert resp.status_code == 200
    history = resp.json()["history"]
    assert history[1]["regression"] is True
    assert history[1]["quality_delta_pct"] == -11.11


async def test_validation_questionnaire_submit_and_aggregate(client):
    resp = await client.get("/mcp/projects/review-proj/questionnaire?run_id=7")
    assert resp.status_code == 200
    questions = resp.json()["questions"]

    review = {
        "run_id": 7,
        "reviewer": "qa",
        "overall": 8,
        "questions": [{**q, "score": 4} for q in questions],
        "feedback": "ready",
        "reviewed_at": "2026-01-01T00:00:00Z",
    }
    submit = await client.post("/mcp/projects/review-proj/reviews", json=review)
    assert submit.status_code == 201

    aggregate = await client.get("/mcp/projects/review-proj/reviews?run_id=7")
    assert aggregate.status_code == 200
    data = aggregate.json()
    assert data["total_reviews"] == 1
    assert data["average_overall"] == 8.0
    assert data["pass_result"] is True
    assert data["average_per_question"]["sections_present"] == 4.0


async def test_instruction_pack_metadata_endpoint(client):
    from mcp_server.config import config

    pack_dir = config.project_root / "packs" / "default"
    pack_dir.mkdir(parents=True)
    (pack_dir / "version.txt").write_text("v-test", encoding="utf-8")
    (pack_dir / "phase2_preamble.md").write_text("Phase 2", encoding="utf-8")

    resp = await client.get("/mcp/instruction-pack")
    assert resp.status_code == 200
    data = resp.json()
    assert data["relative_path"] == "packs/default"
    assert data["version"] == "v-test"
    assert data["supported"] is True
    assert data["preambles"][0]["key"] == "phase2_preamble"
