"""A failing client-readiness verdict holds the swap and the RAG push."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock


async def test_client_readiness_verdict_holds_swap_and_keeps_baseline(client, session_factory, tmp_path, monkeypatch):
    import mcp_server.api.routes.projects as projects_mod
    from mcp_server.config import config
    from mcp_server.services.project_registry import ProjectRegistry

    project_id = "enterprise-hold"
    output_dir = config.project_root / "output" / project_id
    staging = output_dir / "docs_staging"
    docs = output_dir / "docs"
    (staging / "_assessment").mkdir(parents=True)
    docs.mkdir(parents=True)
    (staging / "INTEGRATION_CATALOGUE.md").write_text("# Integration Catalogue\n\ndraft", encoding="utf-8")
    (docs / "INTEGRATION_CATALOGUE.md").write_text("# OLD client-ready catalogue", encoding="utf-8")
    (staging / "_assessment" / "publication_verdict.json").write_text(json.dumps({
        "schema_version": "enterprise-publication-verdict-1", "ok": False, "contract": "talend-enterprise-v1",
        "checks": {"mapping_depth": False}, "blocking": ["INTEGRATION__x: exact mapping table abc not embedded"],
        "warnings": [], "draft_label": "draft: held by client-readiness policy", "evaluated_at": 0,
    }), encoding="utf-8")

    async with session_factory() as session:
        registry = ProjectRegistry(session)
        await registry.add_project(project_id, str(tmp_path / "repo-ent"), source_type="git")
        run = await registry.create_generate_run(project_id, triggered_by="manual")
        await session.commit()
        run_id = run.id

    async def fake_generate_docs(*args, **kwargs):
        return {"success": True, "model_name": "opencode", "write_model_name": "opencode"}

    monkeypatch.setattr(projects_mod.DocGenerationCoordinator, "generate_docs", fake_generate_docs)
    swap_spy = MagicMock()
    monkeypatch.setattr(projects_mod, "swap_docs", swap_spy)
    push_spy = AsyncMock()
    monkeypatch.setattr(projects_mod, "_push_with_retry", push_spy)

    from mcp_server.api.app import app as _app

    projects_mod._currently_generating.add(project_id)
    await projects_mod._run_generate_background(project_id, run_id, _app.state.log_manager, resume=True)

    swap_spy.assert_not_called()
    push_spy.assert_not_called()
    assert (docs / "INTEGRATION_CATALOGUE.md").read_text(encoding="utf-8") == "# OLD client-ready catalogue"
    assert (staging / "INTEGRATION_CATALOGUE.md").exists()
    async with session_factory() as session:
        registry = ProjectRegistry(session)
        project = await registry.get_project(project_id)
        assert project.status == "error"
        assert "client-readiness policy blocked publication" in project.error_message
        runs = await registry.list_generate_runs(project_id)
        assert runs[0].run_verdict == "held_client_readiness"
