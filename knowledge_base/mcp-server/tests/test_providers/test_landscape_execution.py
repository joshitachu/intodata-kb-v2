from types import SimpleNamespace

import pytest

from mcp_server.providers import opencode_landscape
from mcp_server.providers.custom.knowledge_contracts import (
    KnowledgeManifest, KnowledgePlan, KnowledgeTask, KnowledgeRequirement, write_json, LANDSCAPE_ROOT,
)
from mcp_server.services.generation_profile import normalize_profile


def test_old_profile_does_not_silently_adopt_semantic_scopes():
    assert normalize_profile({})["analysis_version"] == 3
    assert normalize_profile({"contract": "talend-landscape-v2"})["analysis_version"] == 2


def test_preflight_rejects_first_n_analysis(monkeypatch, tmp_path):
    from mcp_server.providers.custom import knowledge_requirements
    from mcp_server.providers.opencode_cli import OpenCodeCompatibilityError
    manifest = KnowledgeManifest(snapshot_id="s", project_id="p", requirements=[], evidence={})
    tasks = [KnowledgeTask(task_id=str(i), snapshot_id="s", group="g", unit_ids=[str(i)],
        requirement_ids=[], evidence_ids=[], input_artifact="in.json", output_artifact="out.json")
        for i in range(3)]
    monkeypatch.setattr(knowledge_requirements, "prepare_landscape", lambda **kw:
                        KnowledgePlan(snapshot_id="s", tasks=tasks, manifest=manifest))
    ctx = SimpleNamespace(staging_dir=tmp_path, project_id="p", generation_profile={
        "contract": "talend-landscape-v2", "budgets": {"max_analysis_tasks": 2}})
    with pytest.raises(OpenCodeCompatibilityError, match="needs 3 scopes"):
        opencode_landscape.plan_landscape_analysis(ctx, None)
    assert (tmp_path / LANDSCAPE_ROOT / "analysis_preflight.json").exists()


def test_retention_is_not_explanation_coverage(tmp_path):
    manifest = KnowledgeManifest(snapshot_id="s", project_id="p", evidence={}, requirements=[
        KnowledgeRequirement(requirement_id="raw", unit_id="u", group="g", concern="identity",
                             description="raw", explanation_required=False),
        KnowledgeRequirement(requirement_id="meaning", unit_id="u", group="g", concern="flow",
                             description="Explain flow"),
    ])
    write_json(tmp_path / LANDSCAPE_ROOT / "requirements.json", manifest)
    report = opencode_landscape.landscape_coverage(tmp_path)
    assert report.expected_count == 1
    assert report.coverage_count == 0
    assert not report.ok


def test_every_scope_runs_before_repair_and_resume_skips_accepted(monkeypatch, tmp_path):
    import asyncio
    import json
    from mcp_server.providers.custom import knowledge_contracts, flow_analysis
    requirements = [KnowledgeRequirement(requirement_id=f"r{i}", unit_id=f"u{i}", group="g",
                                        concern="identity", description="identity") for i in range(3)]
    manifest = KnowledgeManifest(snapshot_id="s", project_id="p", requirements=requirements, evidence={})
    write_json(tmp_path / LANDSCAPE_ROOT / "requirements.json", manifest)
    tasks = [KnowledgeTask(task_id=str(i), snapshot_id="s", group="g", unit_ids=[f"u{i}"],
        requirement_ids=[f"r{i}"], evidence_ids=[], input_artifact=f"in{i}.json", output_artifact=f"out{i}.json")
        for i in range(3)]
    for task in tasks:
        (tmp_path / task.input_artifact).write_text(json.dumps({"evidence_artifacts": []}))
    plan = KnowledgePlan(snapshot_id="s", tasks=tasks, manifest=manifest)
    monkeypatch.setattr(opencode_landscape, "plan_landscape_analysis", lambda *a: plan)
    claims = []
    monkeypatch.setattr(knowledge_contracts, "accepted_knowledge_claims", lambda *a: claims)
    monkeypatch.setattr(knowledge_contracts, "render_knowledge_group", lambda *a: "analysis")
    monkeypatch.setattr(flow_analysis, "build_source_journeys", lambda *a: None)
    calls = []
    async def step(**kwargs):
        task = kwargs["contracts"][0].metadata["knowledge_task"]
        calls.append(task["task_id"])
        if calls.count(task["task_id"]) == 2:
            claims.append(SimpleNamespace(kind="fact", requirement_ids=task["requirement_ids"]))
        return None
    async def log(*args):
        pass
    ctx = SimpleNamespace(staging_dir=tmp_path, project_id="p", generation_profile={})
    def run():
        asyncio.run(opencode_landscape.execute_landscape_analysis(
            SimpleNamespace(_execute_step=step), resolved="unused", ctx=ctx,
            facts=SimpleNamespace(groups=["g"]), ledger=None, log_cb=log, on_complete=lambda *a: None))
    run()
    assert calls[:3] == ["0", "1", "2"]
    assert len(calls) == 6
    receipt = json.loads((tmp_path / LANDSCAPE_ROOT / "analysis_progress.json").read_text())
    assert receipt["planned_scopes"] == receipt["attempted_scopes"] == receipt["resolved_scopes"] == 3
    run()
    assert len(calls) == 6


def test_source_journeys_stay_inside_partition(monkeypatch, tmp_path):
    import json
    from mcp_server.providers.custom import flow_analysis
    manifest = KnowledgeManifest(snapshot_id="s", project_id="p", evidence={}, requirements=[
        KnowledgeRequirement(requirement_id="r", unit_id="included", group="g",
                             concern="identity", description="Identity"),
    ])
    write_json(tmp_path / LANDSCAPE_ROOT / "requirements.json", manifest)
    monkeypatch.setattr(flow_analysis, "load_scan", lambda *args: ({"jobs": [
        {"id": "included"}, {"id": "other-partition"},
    ]}, None))
    monkeypatch.setattr(flow_analysis, "canonical_job_id", lambda job: job["id"])
    flow_analysis.build_source_journeys(tmp_path, tmp_path)
    payload = json.loads((tmp_path / LANDSCAPE_ROOT / "source_journeys.json").read_text())
    assert [j["unit_id"] for j in payload["journeys"]] == ["included"]


@pytest.mark.parametrize(
    ("budgets", "reason"),
    [
        (
            {"max_analysis_total_processed_tokens": 100},
            "analysis_total_processed_token_budget",
        ),
        (
            {"analysis_cost_dispatch_ceiling_usd": 0.005},
            "analysis_cost_dispatch_ceiling",
        ),
    ],
)
def test_total_budget_stops_new_scopes_and_records_resumable_gaps(
    monkeypatch, tmp_path, budgets, reason
):
    import asyncio
    import json
    from mcp_server.config import config
    from mcp_server.providers.custom import knowledge_contracts, flow_analysis
    requirements = [KnowledgeRequirement(requirement_id=f"r{i}", unit_id=f"u{i}", group="g",
                                          concern="identity", description="identity") for i in range(3)]
    manifest = KnowledgeManifest(snapshot_id="s", project_id="p", requirements=requirements, evidence={})
    write_json(tmp_path / LANDSCAPE_ROOT / "requirements.json", manifest)
    tasks = [KnowledgeTask(task_id=str(i), snapshot_id="s", group="g", unit_ids=[f"u{i}"],
        requirement_ids=[f"r{i}"], evidence_ids=[], input_artifact=f"in{i}.json", output_artifact=f"out{i}.json")
        for i in range(3)]
    for task in tasks:
        (tmp_path / task.input_artifact).write_text(json.dumps({"evidence_artifacts": []}))
    monkeypatch.setattr(opencode_landscape, "plan_landscape_analysis", lambda *a:
                        KnowledgePlan(snapshot_id="s", tasks=tasks, manifest=manifest))
    monkeypatch.setattr(knowledge_contracts, "accepted_knowledge_claims", lambda *a: [])
    monkeypatch.setattr(knowledge_contracts, "render_knowledge_group", lambda *a: "analysis")
    monkeypatch.setattr(flow_analysis, "build_source_journeys", lambda *a: None)
    calls = []
    async def step(**kwargs):
        calls.append(kwargs["contracts"][0].metadata["knowledge_task"]["task_id"])
        return SimpleNamespace(input_tokens=60, output_tokens=50, processed_tokens=110, cost_usd=0.01)
    async def log(*args):
        pass
    ctx = SimpleNamespace(staging_dir=tmp_path, project_id="p", generation_profile={
        "budgets": {**budgets, "max_analysis_followups": 0}})
    monkeypatch.setattr(config, "opencode_analyze_concurrency", 1)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path)
    asyncio.run(opencode_landscape.execute_landscape_analysis(
        SimpleNamespace(_execute_step=step), resolved="unused", ctx=ctx,
        facts=SimpleNamespace(groups=["g"]), ledger=None, log_cb=log, on_complete=lambda *a: None))
    assert calls == ["0"]
    receipt = json.loads((tmp_path / LANDSCAPE_ROOT / "analysis_progress.json").read_text())
    assert receipt["attempted_scopes"] == 1
    assert receipt["resolved_scopes"] == 0
    assert receipt["tasks"]["1"]["reason"] == reason
