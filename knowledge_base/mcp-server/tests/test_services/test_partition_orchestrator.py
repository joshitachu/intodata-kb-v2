"""Tests for composite-upload orchestration (one scoped run per sub-project)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.custom import partition_planner as pp
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.services import partition_orchestrator as po

ZIP = pp.ZIP_MARKER
_INTODOC_OUTPUT = Path(__file__).resolve().parents[3] / "intodoc-output"


def _unit(name: str, group: str, source_root: str) -> UnitFact:
    return UnitFact(
        name=name, display_name=name, path=f"process/{name}.item",
        group=group, source_root=source_root,
    )


def _composite_facts(project_id: str = "proj") -> FactsManifest:
    units = [
        _unit("a1", "Flows", f"B/Alpha{ZIP}/ALPHA"),
        _unit("a2", "Logging", f"B/Alpha{ZIP}/REFERENCES"),
        _unit("b1", "Flows", f"B/Beta{ZIP}/BETA"),
    ]
    groups: dict[str, int] = {}
    for u in units:
        groups[u.group] = groups.get(u.group, 0) + 1
    return FactsManifest(
        project_id=project_id, platform="talend", canonical_unit_label="Talend jobs",
        unit_count=len(units), groups=groups, units=units,
    )


# --------------------------------------------------------------------------- #
# plan_composite_generation gating
# --------------------------------------------------------------------------- #

def test_plan_gating_single_partition_returns_none(monkeypatch, tmp_path):
    facts = FactsManifest(
        project_id="p", platform="talend", canonical_unit_label="Talend jobs",
        unit_count=1, groups={"G": 1},
        units=[_unit("u1", "G", "bundle/ROOT")],
    )
    monkeypatch.setattr(
        "mcp_server.services.partition_orchestrator.build_facts_manifest",
        lambda pid, sdir: facts,
    )
    assert po.plan_composite_generation("p") is None


def test_plan_gating_disabled_flag_returns_none(monkeypatch):
    monkeypatch.setattr(config, "docgen_partition_orchestration", False)
    assert po.plan_composite_generation("anything") is None


def test_plan_gating_scan_error_returns_none(monkeypatch):
    def _boom(pid, sdir):
        raise RuntimeError("scan missing")

    monkeypatch.setattr(
        "mcp_server.services.partition_orchestrator.build_facts_manifest", _boom
    )
    assert po.plan_composite_generation("p") is None


def test_plan_gating_composite_returns_plan(monkeypatch):
    facts = _composite_facts()
    monkeypatch.setattr(
        "mcp_server.services.partition_orchestrator.build_facts_manifest",
        lambda pid, sdir: facts,
    )
    planned = po.plan_composite_generation("proj")
    assert planned is not None
    plan, planned_facts = planned
    assert plan.is_composite is True
    assert plan.names() == ["Alpha", "Beta"]
    assert planned_facts is facts


@pytest.mark.skipif(
    not (_INTODOC_OUTPUT / "alitalend" / "scan_result.json").exists(),
    reason="alitalend scan not present",
)
def test_plan_gating_real_single_root_alitalend_uses_legacy_path(monkeypatch):
    """Single-root upload => None => the caller keeps today's single-run path."""
    monkeypatch.setattr(config, "intodoc_output_dir", _INTODOC_OUTPUT)
    assert po.plan_composite_generation("alitalend") is None


@pytest.mark.skipif(
    not (_INTODOC_OUTPUT / "werkorder" / "scan_result.json").exists(),
    reason="werkorder scan not present",
)
def test_plan_gating_real_werkorder_plans_six_runs(monkeypatch):
    from mcp_server.providers.custom.partition_planner import top_level_partitions

    monkeypatch.setattr(config, "intodoc_output_dir", _INTODOC_OUTPUT)
    planned = po.plan_composite_generation("werkorder")
    assert planned is not None
    plan, facts = planned
    # Work-plan sizing (P0.1) splits the big zips into budget-fitting run
    # partitions; the six zip-level sub-projects remain the connective layer.
    assert {p.name for p in top_level_partitions(plan)} == {
        "Assetcheck", "ETTM", "EnterpriseServiceBus",
        "VQ_MQ2RHM", "VQ_Quintiq2MX", "VQ_RHM2SF",
    }
    assert all(p.budget_fits for p in plan.partitions)
    assert facts.unit_count == 429


# --------------------------------------------------------------------------- #
# run_partitioned_generation — fakes
# --------------------------------------------------------------------------- #

class _FakeRegistry:
    """Records registry interactions; shared across sessions via `calls`."""

    calls: list[tuple] = []
    _next_run_id = 100

    def __init__(self, db):
        pass

    async def create_generate_run(
        self, project_id, triggered_by="api", model_name=None, partition_name=None
    ):
        _FakeRegistry._next_run_id += 1
        run_id = _FakeRegistry._next_run_id
        label = f"{triggered_by}:{partition_name}" if partition_name else triggered_by
        _FakeRegistry.calls.append(("create_run", project_id, label, run_id))

        class _Run:
            id = run_id

        return _Run()

    async def complete_generate_run(self, run_id, **kwargs):
        _FakeRegistry.calls.append(("complete_run", run_id, kwargs.get("status"), kwargs))

    async def update_status(self, project_id, status, error_message=None):
        _FakeRegistry.calls.append(("status", project_id, status, error_message))

    async def update_generate_result(self, project_id, doc_count=0):
        _FakeRegistry.calls.append(("doc_count", project_id, doc_count))


class _FakeDB:
    async def commit(self):
        pass


@asynccontextmanager
async def _fake_session_factory():
    yield _FakeDB()


class _FakeBridge:
    pushed: list[dict] = []
    internal_deleted: list[str] = []
    aliases: list[tuple] = []

    def __init__(self, log_manager=None):
        pass

    async def push_docs(self, project_id, docs=None, **kwargs):
        for doc in docs or []:
            _FakeBridge.pushed.append({"project_id": project_id, **doc})
        return {"pushed": len(docs or []), "skipped": 0, "errors": []}

    async def push_markdown(self, project_id, filename, content, metadata=None, **kwargs):
        _FakeBridge.pushed.append(
            {"project_id": project_id, "filename": filename, "content": content}
        )
        return {}

    async def delete_internal_documents(self, project_id):
        _FakeBridge.internal_deleted.append(project_id)

    async def post_entity_aliases(self, project_id, aliases):
        _FakeBridge.aliases.append((project_id, len(aliases)))
        return {"inserted": len(aliases)}


class _FakeCoordinator:
    """Writes a plausible staging tree per partition instead of calling any LLM."""

    def __init__(self, output_root: Path, fail_partitions: set[str] | None = None):
        self.output_root = output_root
        self.fail_partitions = fail_partitions or set()
        self.calls: list[dict] = []

    async def generate_docs(self, project_id, *, resume=True, run_id=None,
                            partition=None, **kwargs):
        self.calls.append({
            "project_id": project_id,
            "run_id": run_id,
            "partition": partition.name if partition is not None else None,
            "resume": resume,
        })
        subdir = pp.partition_output_subdir(partition.name) or partition.name
        part_output = self.output_root / subdir
        if partition.name in self.fail_partitions:
            return {"success": False, "error": "boom", "model_name": "m"}
        staging = part_output / "docs_staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "00_AGENT_BRIEF.md").write_text(
            f"# Brief for {partition.name}\n", encoding="utf-8"
        )
        (staging / "01_ARCHITECTURE_MAP.md").write_text(
            f"# Architecture for {partition.name}\n", encoding="utf-8"
        )
        return {
            "success": True, "error": None, "model_name": "m", "write_model_name": "w",
            "cost_usd": 1.5, "duration_ms": 1000,
            "input_tokens": 10, "output_tokens": 5,
        }


@pytest.fixture
def orchestration_env(monkeypatch, tmp_path):
    _FakeRegistry.calls = []
    _FakeBridge.pushed = []
    _FakeBridge.internal_deleted = []
    _FakeBridge.aliases = []
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(po, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(po, "ProjectRegistry", _FakeRegistry)
    monkeypatch.setattr(po, "RagBridge", _FakeBridge)
    # doc quality scoring reads a whole heuristics stack — stub for these tests
    monkeypatch.setattr(po, "_score_partition_docs", lambda pid, d: 4.0)
    return tmp_path


class _FakeLogMgr:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def emit(self, project_id, message, level="info"):
        self.messages.append((level, message))

    def get_current_log_path(self, project_id):
        return None


async def test_composite_orchestration_runs_each_partition_and_publishes(orchestration_env):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    coordinator = _FakeCoordinator(output_root)
    log_mgr = _FakeLogMgr()

    outcomes = await po.run_partitioned_generation(
        "proj", 1, log_mgr, coordinator, plan=plan, facts=facts, resume=True,
    )

    # One scoped run per partition, each with its own child run id + partition.
    assert [c["partition"] for c in coordinator.calls] == ["Alpha", "Beta"]
    child_ids = [c["run_id"] for c in coordinator.calls]
    assert len(set(child_ids)) == 2 and 1 not in child_ids

    # Docs swapped into per-sub-project dirs; staging gone.
    for sub in ("Alpha", "Beta"):
        assert (output_root / sub / "docs" / "00_AGENT_BRIEF.md").exists()
        assert not (output_root / sub / "docs_staging").exists()

    # Upload-level deterministic topology artifacts exist.
    assert pp.cross_project_index_path("proj").exists()
    assert (pp.upload_topology_dir("proj") / po.CROSS_PROJECT_TOPOLOGY_DOC).exists()

    # RAG push used subproject-prefixed filenames plus the upload-level map.
    filenames = {d["filename"] for d in _FakeBridge.pushed}
    assert "Alpha/00_AGENT_BRIEF.md" in filenames
    assert "Beta/01_ARCHITECTURE_MAP.md" in filenames
    assert po.CROSS_PROJECT_TOPOLOGY_DOC in filenames

    # DB: two child runs completed + orchestration run completed + status ready.
    statuses = [c for c in _FakeRegistry.calls if c[0] == "status"]
    assert statuses[-1][2] == "ready"
    completes = [c for c in _FakeRegistry.calls if c[0] == "complete_run"]
    assert {c[2] for c in completes if c[1] != 1} == {"completed"}
    orch = next(c for c in completes if c[1] == 1)
    assert orch[2] == "completed"
    assert orch[3]["doc_count"] == 4  # 2 docs x 2 sub-projects
    assert orch[3]["cost_usd"] == 3.0

    assert all(o.success for o in outcomes)


async def test_partition_failure_is_isolated_and_reported(orchestration_env):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    coordinator = _FakeCoordinator(output_root, fail_partitions={"Alpha"})
    log_mgr = _FakeLogMgr()

    outcomes = await po.run_partitioned_generation(
        "proj", 1, log_mgr, coordinator, plan=plan, facts=facts, resume=True,
    )

    by_name = {o.partition: o for o in outcomes}
    assert by_name["Alpha"].success is False
    assert by_name["Beta"].success is True
    # Beta stays published despite Alpha's failure.
    assert (output_root / "Beta" / "docs" / "00_AGENT_BRIEF.md").exists()
    assert not (output_root / "Alpha" / "docs").exists()

    statuses = [c for c in _FakeRegistry.calls if c[0] == "status"]
    assert statuses[-1][2] == "error"
    assert "Alpha" in (statuses[-1][3] or "")
    orch = next(c for c in _FakeRegistry.calls if c[0] == "complete_run" and c[1] == 1)
    assert orch[2] == "failed"


async def test_resume_skips_up_to_date_partition(orchestration_env, monkeypatch):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    monkeypatch.setattr(po, "_scan_fingerprint", lambda p: "fp-1")

    # First full run.
    coordinator = _FakeCoordinator(output_root)
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )
    assert len(coordinator.calls) == 2
    state = json.loads(po._partition_state_path("proj").read_text())
    assert state["Alpha"]["scan_fingerprint"] == "fp-1"

    # Second run with the same fingerprint: nothing re-runs.
    coordinator2 = _FakeCoordinator(output_root)
    outcomes = await po.run_partitioned_generation(
        "proj", 2, _FakeLogMgr(), coordinator2, plan=plan, facts=facts, resume=True,
    )
    assert coordinator2.calls == []
    assert all(o.skipped for o in outcomes)

    # Scan changed: everything re-runs.
    monkeypatch.setattr(po, "_scan_fingerprint", lambda p: "fp-2")
    coordinator3 = _FakeCoordinator(output_root)
    await po.run_partitioned_generation(
        "proj", 3, _FakeLogMgr(), coordinator3, plan=plan, facts=facts, resume=True,
    )
    assert len(coordinator3.calls) == 2


@pytest.mark.skipif(
    not (_INTODOC_OUTPUT / "werkorder" / "scan_result.json").exists(),
    reason="werkorder scan not present",
)
async def test_real_werkorder_orchestration_dry_run(orchestration_env, monkeypatch):
    """End-to-end dry run against the real werkorder scan (no LLM): six scoped
    runs, six output dirs, per-run budgets under the cap, upload-level index."""
    from mcp_server.providers.custom.validators import build_facts_manifest

    tmp_path = orchestration_env
    # Real scan inputs; outputs land under the tmp project_root.
    monkeypatch.setattr(config, "intodoc_output_dir", _INTODOC_OUTPUT)
    facts = build_facts_manifest("werkorder", _INTODOC_OUTPUT / "werkorder")
    plan = pp.plan_partitions(facts)
    # Work-plan sizing (P0.1) splits the big zips into more, budget-fitting
    # run partitions; the six zip-level sub-projects stay the connective layer.
    assert len(plan.partitions) >= 6
    top_names = {p.name for p in pp.top_level_partitions(plan)}
    assert top_names == {
        "Assetcheck", "ETTM", "EnterpriseServiceBus",
        "VQ_MQ2RHM", "VQ_Quintiq2MX", "VQ_RHM2SF",
    }

    output_root = tmp_path / "output" / "werkorder"
    coordinator = _FakeCoordinator(output_root)
    outcomes = await po.run_partitioned_generation(
        "werkorder", 1, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )

    assert [c["partition"] for c in coordinator.calls] == [p.name for p in plan.partitions]
    # Distinct child run ids, none reusing the orchestration run id.
    run_ids = [c["run_id"] for c in coordinator.calls]
    assert len(set(run_ids)) == len(plan.partitions) and 1 not in run_ids
    # One published docs dir per run partition.
    for part in plan.partitions:
        sub = pp.partition_output_subdir(part.name) or part.name
        assert (output_root / sub / "docs" / "00_AGENT_BRIEF.md").exists()
    # Each partition fits its own budget under the cap.
    assert all(p.call_budget <= plan.cap and p.budget_fits for p in plan.partitions)
    # Upload-level connective layer stays at the zip level (no _part entries).
    index = json.loads(pp.cross_project_index_path("werkorder").read_text())
    assert set(index["subprojects"]) == top_names
    assert any(
        e["from_subproject"] == "ETTM" and e["to_subproject"] == "EnterpriseServiceBus"
        for e in index["application_edges"]
    )
    assert len(index["shared_landscape_systems"]) >= 3
    assert all(o.success for o in outcomes)
