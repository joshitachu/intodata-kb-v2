"""Tests for project management REST API endpoints."""

from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
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
    assert resp.json()["total"] == 1


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


async def test_generate_run_compare_includes_models_quality_deltas_and_template_diffs(client, session_factory):
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
        template_snapshot={"overview": 1, "runbook": 1},
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
        template_snapshot={"overview": 2, "runbook": 1},
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
    assert data["template_diffs"] == [{"slug": "overview", "version_a": 1, "version_b": 2}]


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
