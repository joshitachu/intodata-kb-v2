"""Tests for composite-upload orchestration (one scoped run per sub-project)."""

from __future__ import annotations

import json
import shutil
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
    deleted_docs: list[tuple[str, str]] = []
    aliases: list[tuple] = []

    def __init__(self, log_manager=None):
        pass

    async def delete_document(self, project_id, filename):
        _FakeBridge.deleted_docs.append((project_id, filename))
        return {}

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
    _FakeBridge.deleted_docs = []
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
    # The skip decision reads the PER-PARTITION digest, not the whole-scan one.
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    monkeypatch.setattr(po, "_scan_fingerprint", lambda p: "fp-1")

    # First full run.
    coordinator = _FakeCoordinator(output_root)
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )
    assert len(coordinator.calls) == 2
    state = json.loads(po._partition_state_path("proj").read_text())
    assert state["Alpha"]["partition_fingerprint"] == "a-1"
    # Whole-scan digest is retained for diagnostics only.
    assert state["Alpha"]["scan_fingerprint"] == "fp-1"

    # Second run with the same digests: nothing re-runs.
    coordinator2 = _FakeCoordinator(output_root)
    outcomes = await po.run_partitioned_generation(
        "proj", 2, _FakeLogMgr(), coordinator2, plan=plan, facts=facts, resume=True,
    )
    assert coordinator2.calls == []
    assert all(o.skipped for o in outcomes)

    # Both digests moved: everything re-runs.
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-2", "Beta": "b-2"}
    )
    coordinator3 = _FakeCoordinator(output_root)
    await po.run_partitioned_generation(
        "proj", 3, _FakeLogMgr(), coordinator3, plan=plan, facts=facts, resume=True,
    )
    assert len(coordinator3.calls) == 2


async def test_only_the_changed_partition_reruns(orchestration_env, monkeypatch):
    """One repo changing must cost one repo's generation, not the project's.

    This is what a single whole-scan fingerprint could not express: it moved on
    any change anywhere, so every sub-project regenerated.
    """
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )

    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), _FakeCoordinator(output_root),
        plan=plan, facts=facts, resume=True,
    )

    # Only Beta's digest moves -- a push to Beta's repo.
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-2"}
    )
    coordinator = _FakeCoordinator(output_root)
    outcomes = await po.run_partitioned_generation(
        "proj", 2, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )

    assert [c["partition"] for c in coordinator.calls] == ["Beta"]
    by_name = {o.partition: o for o in outcomes}
    assert by_name["Alpha"].skipped is True
    assert by_name["Alpha"].success is True
    assert by_name["Beta"].skipped is False


async def test_indeterminate_partition_digest_never_skips(orchestration_env, monkeypatch):
    """An empty digest means the inputs are not trusted -- regenerate rather
    than leave possibly-stale docs published."""
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), _FakeCoordinator(output_root),
        plan=plan, facts=facts, resume=True,
    )

    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "", "Beta": "b-1"}
    )
    coordinator = _FakeCoordinator(output_root)
    await po.run_partitioned_generation(
        "proj", 2, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )
    assert [c["partition"] for c in coordinator.calls] == ["Alpha"]


# --------------------------------------------------------------------------- #
# decide_partition_actions -- the ONE skip rule
# --------------------------------------------------------------------------- #


def _mixed_facts(project_id: str = "proj") -> FactsManifest:
    """One marked sub-project plus an unmarked (pre-composite) source root.

    This is the shape a Git project takes when a second repo is added beside a
    single-repo clone: the old tree stays unmarked and lands in __shared__.
    """
    units = [
        _unit("a1", "Flows", f"B/Alpha{ZIP}/ALPHA"),
        _unit("s1", "Legacy", "B/LEGACY"),
    ]
    groups: dict[str, int] = {}
    for u in units:
        groups[u.group] = groups.get(u.group, 0) + 1
    return FactsManifest(
        project_id=project_id, platform="talend", canonical_unit_label="Talend jobs",
        unit_count=len(units), groups=groups, units=units,
    )


def _write_flat_docs(tmp_path: Path, project_id: str, count: int = 2) -> Path:
    docs = tmp_path / "output" / project_id / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (docs / f"0{index}_FLAT_DOC.md").write_text(f"# flat {index}\n", encoding="utf-8")
    (docs / "_internal.md").write_text("internal\n", encoding="utf-8")
    return docs


def test_reason_constants_match_the_api_contract():
    """The plan endpoint pins these values in a Literal; drift is a 500."""
    from mcp_server.schemas import GenerationPlanPartition

    field = GenerationPlanPartition.model_fields["reason"]
    literal_values = set(field.annotation.__args__)
    assert literal_values == set(po.PARTITION_REASONS)


def test_decisions_report_new_partitions(orchestration_env, monkeypatch):
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    decisions = po.decide_partition_actions("proj", plan, facts)

    assert decisions.composite is True
    assert decisions.scan_available is True
    assert decisions.will_generate == 2 and decisions.will_reuse == 0
    assert [(d.name, d.action, d.reason) for d in decisions.decisions] == [
        ("Alpha", po.ACTION_GENERATE, po.REASON_NEW),
        ("Beta", po.ACTION_GENERATE, po.REASON_NEW),
    ]
    assert po.NOTE_CROSS_PROJECT_REFS in decisions.notes


def test_decisions_report_fingerprint_unavailable(orchestration_env, monkeypatch):
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    monkeypatch.setattr(po, "_partition_fingerprints", lambda d, p, f: {})
    decisions = po.decide_partition_actions("proj", plan, facts)

    assert {d.reason for d in decisions.decisions} == {po.REASON_FINGERPRINT_UNAVAILABLE}
    assert po.NOTE_FINGERPRINT_UNAVAILABLE in decisions.notes


async def test_plan_endpoint_helper_agrees_with_the_run_path(orchestration_env, monkeypatch):
    """The dialog must predict exactly what the run then does.

    A second copy of the skip rule would be free to drift; this pins that the
    prediction and the behaviour come from the same function.
    """
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), _FakeCoordinator(output_root),
        plan=plan, facts=facts, resume=True,
    )

    # Only Beta's inputs move.
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-2"}
    )
    predicted = po.decide_partition_actions("proj", plan, facts)
    assert {d.name: d.action for d in predicted.decisions} == {
        "Alpha": po.ACTION_REUSE, "Beta": po.ACTION_GENERATE,
    }
    assert predicted.will_generate == 1 and predicted.will_reuse == 1
    reused = predicted.decision_for("Alpha")
    assert reused.doc_count == 2 and reused.last_generated_at is not None

    coordinator = _FakeCoordinator(output_root)
    outcomes = await po.run_partitioned_generation(
        "proj", 2, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )
    actual = {o.partition: ("reuse" if o.skipped else "generate") for o in outcomes}
    assert actual == {d.name: d.action for d in predicted.decisions}
    assert [c["partition"] for c in coordinator.calls] == ["Beta"]


async def test_decisions_report_missing_published_docs(orchestration_env, monkeypatch):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), _FakeCoordinator(output_root),
        plan=plan, facts=facts, resume=True,
    )
    shutil.rmtree(output_root / "Alpha" / "docs")

    decisions = po.decide_partition_actions("proj", plan, facts)
    by_name = {d.name: d for d in decisions.decisions}
    assert by_name["Alpha"].action == po.ACTION_GENERATE
    assert by_name["Alpha"].reason == po.REASON_NO_PUBLISHED_DOCS
    assert by_name["Beta"].action == po.ACTION_REUSE


async def test_forced_rebuild_generates_everything_but_still_reports_unchanged(
    orchestration_env, monkeypatch
):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), _FakeCoordinator(output_root),
        plan=plan, facts=facts, resume=True,
    )

    decisions = po.decide_partition_actions("proj", plan, facts, resume=False)
    assert decisions.will_generate == 2
    assert {d.reason for d in decisions.decisions} == {po.REASON_UNCHANGED}


# --------------------------------------------------------------------------- #
# Shared (unattributable) artifacts -> project-wide invalidation
# --------------------------------------------------------------------------- #


async def test_shared_routine_change_is_named_as_a_global_invalidation(
    orchestration_env, monkeypatch
):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    monkeypatch.setattr(po, "_shared_artifact_digest", lambda d: "routines-1")
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), _FakeCoordinator(output_root),
        plan=plan, facts=facts, resume=True,
    )
    state = json.loads(po._partition_state_path("proj").read_text())
    assert state[po.STATE_META_KEY]["shared_artifact_digest"] == "routines-1"

    # A routine edit moves the shared digest, and with it every per-partition
    # digest (the shared digest is mixed into all of them).
    monkeypatch.setattr(po, "_shared_artifact_digest", lambda d: "routines-2")
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-2", "Beta": "b-2"}
    )
    decisions = po.decide_partition_actions("proj", plan, facts)

    assert decisions.global_invalidation is not None
    assert decisions.global_invalidation.reason == po.GLOBAL_SHARED_ROUTINE_CHANGED
    assert "routine" in decisions.global_invalidation.detail.lower()
    assert {d.reason for d in decisions.decisions} == {po.REASON_SHARED_ARTIFACT_CHANGED}


def test_no_recorded_baseline_makes_no_global_claim(orchestration_env, monkeypatch):
    """Without a previous digest there is no evidence, so no accusation."""
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    monkeypatch.setattr(po, "_shared_artifact_digest", lambda d: "routines-9")
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    decisions = po.decide_partition_actions("proj", plan, facts)
    assert decisions.global_invalidation is None
    assert {d.reason for d in decisions.decisions} == {po.REASON_NEW}


def test_baseline_is_recorded_even_when_nothing_publishes(orchestration_env, monkeypatch):
    po.record_shared_artifact_baseline("proj", "routines-1")
    state = json.loads(po._partition_state_path("proj").read_text())
    assert state[po.STATE_META_KEY]["shared_artifact_digest"] == "routines-1"
    # An existing baseline is never silently replaced: it is the comparison point.
    po.record_shared_artifact_baseline("proj", "routines-2")
    state = json.loads(po._partition_state_path("proj").read_text())
    assert state[po.STATE_META_KEY]["shared_artifact_digest"] == "routines-1"


def test_reserved_meta_key_is_never_read_as_a_partition(orchestration_env):
    state = {po.STATE_META_KEY: {"partition_fingerprint": "a-1"}}
    assert po._partition_entry(state, po.STATE_META_KEY) is None
    assert po._partition_completed(state, po.STATE_META_KEY, "a-1", Path("/nope")) is False


# --------------------------------------------------------------------------- #
# Flat -> composite docs adoption
# --------------------------------------------------------------------------- #


async def test_flat_docs_are_adopted_instead_of_regenerated(orchestration_env, monkeypatch):
    """Adding a second sub-project must not re-buy the first one's documents."""
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    flat_docs = _write_flat_docs(tmp_path, "proj")
    # The additive-source endpoint records which sub-project inherited the tree.
    po.record_flat_source_migration("proj", "Alpha")
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )

    predicted = po.decide_partition_actions("proj", plan, facts)
    alpha = predicted.decision_for("Alpha")
    assert (alpha.action, alpha.reason) == (po.ACTION_REUSE, po.REASON_FLAT_LAYOUT_ADOPTION)
    assert alpha.doc_count == 2

    coordinator = _FakeCoordinator(output_root)
    outcomes = await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )

    # Only the NEW sub-project ran.
    assert [c["partition"] for c in coordinator.calls] == ["Beta"]
    by_name = {o.partition: o for o in outcomes}
    assert by_name["Alpha"].skipped is True and by_name["Alpha"].doc_count == 2

    # Docs moved, not copied, and the internal artifact travelled with them.
    assert not flat_docs.exists()
    assert (output_root / "Alpha" / "docs" / "00_FLAT_DOC.md").exists()
    assert (output_root / "Alpha" / "docs" / "_internal.md").exists()

    # State entry exists, carries the fresh fingerprint and its provenance.
    state = json.loads(po._partition_state_path("proj").read_text())
    assert state["Alpha"]["partition_fingerprint"] == "a-1"
    assert state["Alpha"]["adopted_from"] == "flat"
    assert state["Alpha"]["doc_count"] == 2

    # RAG has one copy per document, under the sub-project prefix.
    assert ("proj", "00_FLAT_DOC.md") in _FakeBridge.deleted_docs
    assert ("proj", "01_FLAT_DOC.md") in _FakeBridge.deleted_docs
    pushed = {d["filename"] for d in _FakeBridge.pushed}
    assert "Alpha/00_FLAT_DOC.md" in pushed
    assert "00_FLAT_DOC.md" not in pushed

    # And the adoption is durable: a second run skips both sub-projects.
    coordinator2 = _FakeCoordinator(output_root)
    await po.run_partitioned_generation(
        "proj", 2, _FakeLogMgr(), coordinator2, plan=plan, facts=facts, resume=True,
    )
    assert coordinator2.calls == []


async def test_unmarked_source_root_is_the_adoption_owner_without_a_hint(
    orchestration_env, monkeypatch
):
    """A Git project's pre-existing clone stays unmarked, so it is __shared__."""
    tmp_path = orchestration_env
    facts = _mixed_facts("proj")
    plan = pp.plan_partitions(facts)
    assert pp.SHARED_PARTITION in plan.names()
    output_root = tmp_path / "output" / "proj"
    _write_flat_docs(tmp_path, "proj")
    monkeypatch.setattr(
        po,
        "_partition_fingerprints",
        lambda d, p, f: {"Alpha": "a-1", pp.SHARED_PARTITION: "s-1"},
    )

    coordinator = _FakeCoordinator(output_root)
    await po.run_partitioned_generation(
        "proj", 1, _FakeLogMgr(), coordinator, plan=plan, facts=facts, resume=True,
    )

    assert [c["partition"] for c in coordinator.calls] == ["Alpha"]
    subdir = pp.partition_output_subdir(pp.SHARED_PARTITION)
    assert (output_root / subdir / "docs" / "00_FLAT_DOC.md").exists()


def test_adoption_is_skipped_when_the_owner_is_ambiguous(orchestration_env, monkeypatch):
    """No hint and no unmarked root: nothing may be adopted."""
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    _write_flat_docs(tmp_path, "proj")
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    decisions = po.decide_partition_actions("proj", plan, facts)
    assert {d.reason for d in decisions.decisions} == {po.REASON_NEW}
    assert (tmp_path / "output" / "proj" / "docs").exists()


def test_adoption_is_skipped_when_the_hint_names_a_missing_partition(
    orchestration_env, monkeypatch
):
    """A size-split (or renamed) sub-project cannot claim one flat doc set."""
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    _write_flat_docs(tmp_path, "proj")
    po.record_flat_source_migration("proj", "Alpha_part1")
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )
    decisions = po.decide_partition_actions("proj", plan, facts)
    assert {d.reason for d in decisions.decisions} == {po.REASON_NEW}


def test_adoption_never_overwrites_published_sub_project_docs(orchestration_env, monkeypatch):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    _write_flat_docs(tmp_path, "proj")
    po.record_flat_source_migration("proj", "Alpha")
    published = tmp_path / "output" / "proj" / "Alpha" / "docs"
    published.mkdir(parents=True)
    (published / "00_REAL.md").write_text("# real\n", encoding="utf-8")
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )

    decisions = po.decide_partition_actions("proj", plan, facts)
    alpha = decisions.decision_for("Alpha")
    # Something is already published there, so adoption is off the table and
    # the sub-project is treated as never-recorded: regenerate, do not clobber.
    assert alpha.action == po.ACTION_GENERATE
    assert alpha.reason == po.REASON_NEW
    assert (published / "00_REAL.md").exists()
    assert (tmp_path / "output" / "proj" / "docs").exists()


async def test_failed_adoption_regenerates_instead_of_losing_docs(
    orchestration_env, monkeypatch
):
    tmp_path = orchestration_env
    facts = _composite_facts("proj")
    plan = pp.plan_partitions(facts)
    output_root = tmp_path / "output" / "proj"
    flat_docs = _write_flat_docs(tmp_path, "proj")
    po.record_flat_source_migration("proj", "Alpha")
    monkeypatch.setattr(
        po, "_partition_fingerprints", lambda d, p, f: {"Alpha": "a-1", "Beta": "b-1"}
    )

    def _boom(self, target):
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "replace", _boom)
    decisions = po.decide_partition_actions("proj", plan, facts)
    decisions = await po._adopt_flat_layout(
        "proj", plan, facts, decisions=decisions, log_mgr=_FakeLogMgr()
    )
    monkeypatch.undo()

    alpha = decisions.decision_for("Alpha")
    assert alpha.action == po.ACTION_GENERATE
    assert flat_docs.exists()
    assert not (output_root / "Alpha" / "docs").exists()


# --------------------------------------------------------------------------- #
# build_generation_plan -- the read-only entry point behind the API
# --------------------------------------------------------------------------- #


def _write_scan(tmp_path: Path, project_id: str, units: list[tuple[str, str, str]]) -> None:
    scan_dir = tmp_path / "intodoc-output" / project_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "scan_result.json").write_text(
        json.dumps({
            "platform": "talend",
            "jobs": [
                {
                    "name": name,
                    "job_id": name,
                    "path": f"process/{name}.item",
                    "group": group,
                    "source_root": source_root,
                    "components": [],
                }
                for name, group, source_root in units
            ],
        }),
        encoding="utf-8",
    )


def test_build_generation_plan_reports_a_missing_scan(orchestration_env):
    plan = po.build_generation_plan("never-scanned")
    assert plan.scan_available is False
    assert plan.composite is False
    assert plan.decisions == []
    assert plan.will_generate == 1 and plan.will_reuse == 0


def test_build_generation_plan_reports_a_corrupt_scan(orchestration_env):
    tmp_path = orchestration_env
    scan_dir = tmp_path / "intodoc-output" / "broken"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_result.json").write_text("{not json", encoding="utf-8")
    plan = po.build_generation_plan("broken")
    assert plan.scan_available is False


def test_build_generation_plan_reports_a_single_sub_project(orchestration_env):
    tmp_path = orchestration_env
    _write_scan(tmp_path, "solo", [("u1", "G", "bundle/ROOT")])
    plan = po.build_generation_plan("solo")
    assert plan.scan_available is True
    assert plan.composite is False
    assert plan.partition_count == 1
    assert plan.will_generate == 1 and plan.will_reuse == 0
    assert plan.decisions == []


def test_build_generation_plan_composite(orchestration_env):
    tmp_path = orchestration_env
    _write_scan(
        tmp_path,
        "multi",
        [("a1", "Flows", f"B/Alpha{ZIP}/A"), ("b1", "Flows", f"B/Beta{ZIP}/B")],
    )
    plan = po.build_generation_plan("multi")
    assert plan.composite is True
    assert [d.name for d in plan.decisions] == ["Alpha", "Beta"]
    # No manifest.job_files in this scan, so digests are indeterminate: the
    # honest answer is "regenerate", never "reuse on a digest we do not trust".
    assert {d.reason for d in plan.decisions} == {po.REASON_FINGERPRINT_UNAVAILABLE}


def test_build_generation_plan_respects_the_orchestration_flag(orchestration_env, monkeypatch):
    tmp_path = orchestration_env
    _write_scan(
        tmp_path,
        "multi2",
        [("a1", "Flows", f"B/Alpha{ZIP}/A"), ("b1", "Flows", f"B/Beta{ZIP}/B")],
    )
    monkeypatch.setattr(config, "docgen_partition_orchestration", False)
    plan = po.build_generation_plan("multi2")
    # Orchestration off means the next run is a single legacy run, so reporting
    # a per-sub-project preview would describe a path that will not be taken.
    assert plan.composite is False
    assert plan.decisions == []


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
