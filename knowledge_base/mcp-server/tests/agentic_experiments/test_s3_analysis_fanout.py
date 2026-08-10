"""Slice 3 -- Phase 2 analysis fans out deterministically with the project.

Covers the deterministic evidence-batch planner (coupling seams, byte-based
bin-packing, coverage partition, boundary manifests), the recipe fan-out
template expansion (N analysis workers, not two), and the runner staging that
pushes each batch's verbatim evidence plus its boundary manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.experiments.application.analysis_batch_planner import (
    AnalysisBatch,
    AnalysisBatchPlanner,
    BatchCoverageError,
    BatchPlan,
    BoundaryManifest,
    UnitFact,
)
from mcp_server.experiments.application.fact_ledger import compile_fact_ledger
from mcp_server.experiments.application.grounded_pack import (
    expand_grounded_analysis_fanout,
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    BOUNDARY_MANIFEST_NAME,
    UNIT_EVIDENCE_DIRNAME,
    UNIT_EVIDENCE_INDEX_NAME,
    GroundedOpenCodeRunner,
    _UnitEvidencePlan,
)
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.runtime.fake import FakeAgentRuntime
from mcp_server.experiments.infrastructure.opencode.runtime import (
    OpenCodeGroundedTaskBuilder,
)


_RECIPE_ROOT = Path(__file__).parents[2] / "data" / "agentic_recipes"


# --------------------------------------------------------------------------
# Planner: coupling seams, byte packing, atomic units, coverage gate.
# --------------------------------------------------------------------------


def _units(*specs: tuple[str, str, int]) -> tuple[UnitFact, ...]:
    return tuple(
        UnitFact(name=name, group=group, evidence_bytes=size, summary=f"{name} summary")
        for name, group, size in specs
    )


def test_planner_keeps_caller_and_callees_in_one_batch_when_budget_allows() -> None:
    units = _units(
        ("Parent", "OrderFlow", 100),
        ("ChildA", "OrderFlow", 100),
        ("ChildB", "OrderFlow", 100),
        ("Lonely", "Reporting", 100),
    )
    planner = AnalysisBatchPlanner(max_batch_bytes=1000)
    plan = planner.plan(units, call_edges=[("Parent", ["ChildA", "ChildB"])])

    batch_of = {
        unit: batch.batch_id for batch in plan.batches for unit in batch.unit_names
    }
    assert batch_of["Parent"] == batch_of["ChildA"] == batch_of["ChildB"]
    plan.assert_covers([u.name for u in units])


def test_planner_pulls_shared_connection_units_together() -> None:
    units = _units(
        ("BillingWriter", "Billing", 100),
        ("ShippingReader", "Shipping", 100),
        ("Other", "Misc", 100),
    )
    planner = AnalysisBatchPlanner(max_batch_bytes=1000)
    plan = planner.plan(
        units,
        connection_usage=[("sharedDb", ["BillingWriter", "ShippingReader"])],
    )

    batch_of = {
        unit: batch.batch_id for batch in plan.batches for unit in batch.unit_names
    }
    assert batch_of["BillingWriter"] == batch_of["ShippingReader"]


def test_planner_fans_out_by_evidence_bytes_and_partitions_every_unit() -> None:
    # Ten independent single-unit groups of 100 bytes each, budget 250 -> the
    # count of batches scales with evidence size, not a fixed role count.
    units = _units(*[(f"U{i}", f"G{i}", 100) for i in range(10)])
    planner = AnalysisBatchPlanner(max_batch_bytes=250)
    plan = planner.plan(units)

    assert len(plan.batches) > 2
    assert all(batch.total_evidence_bytes <= 250 for batch in plan.batches)
    assigned = [unit for batch in plan.batches for unit in batch.unit_names]
    assert sorted(assigned) == sorted(u.name for u in units)
    assert len(assigned) == len(set(assigned))  # no unit in two batches
    plan.assert_covers([u.name for u in units])


def test_planner_never_splits_a_unit_even_when_larger_than_budget() -> None:
    units = _units(("Huge", "Big", 5000), ("Small", "Big", 100))
    planner = AnalysisBatchPlanner(max_batch_bytes=1000)
    plan = planner.plan(units, call_edges=[("Huge", ["Small"])])

    huge_batches = [b for b in plan.batches if "Huge" in b.unit_names]
    assert len(huge_batches) == 1
    assert huge_batches[0].unit_names == ("Huge",)  # over budget but whole
    plan.assert_covers([u.name for u in units])


def test_coverage_gate_fails_when_a_unit_is_dropped() -> None:
    units = _units(("A", "G", 100), ("B", "G2", 100), ("C", "G3", 100))
    planner = AnalysisBatchPlanner(max_batch_bytes=100)
    plan = planner.plan(units)

    # Drop the last batch's units -> not a partition -> the gate must fail.
    truncated = BatchPlan(batches=plan.batches[:-1], max_batch_bytes=100)
    with pytest.raises(BatchCoverageError, match="not an exact partition"):
        truncated.assert_covers([u.name for u in units])


def test_coverage_gate_fails_when_a_unit_is_double_assigned() -> None:
    empty_manifest = BoundaryManifest("b", (), (), ())
    plan = BatchPlan(
        batches=(
            AnalysisBatch("b1", ("A",), 1, empty_manifest),
            AnalysisBatch("b2", ("A",), 1, empty_manifest),
        ),
        max_batch_bytes=100,
    )
    with pytest.raises(BatchCoverageError, match="more than one batch"):
        plan.assert_covers(["A"])


def test_planner_dedupes_duplicate_named_copies_and_records_multiplicity() -> None:
    # A shared logging/monitoring route (MessageMonitoring) copied into six
    # export roots: same name AND group, only the source path differs. This is
    # exactly the werkorder crash input. The planner must NOT raise; it keeps one
    # unit per name (first-wins, matching the evidence/doc side) and records the
    # duplication rather than dropping it silently.
    copies = tuple(
        UnitFact(
            name="MessageMonitoring",
            group="MessageLogging",
            evidence_bytes=100,
            summary="MessageMonitoring summary",
            source_path=f"root{i}/REFERENCES/routes/MessageLogging/MessageMonitoring_0.1.item",
        )
        for i in range(6)
    )
    others = _units(("ReceiveDataLog", "MessageLogging", 100), ("OrderSync", "OrderFlow", 100))
    planner = AnalysisBatchPlanner(max_batch_bytes=10_000)
    plan = planner.plan(copies + others)

    # Every batch is a partition over UNIQUE names; MessageMonitoring appears once.
    assigned = [unit for batch in plan.batches for unit in batch.unit_names]
    assert sorted(assigned) == ["MessageMonitoring", "OrderSync", "ReceiveDataLog"]
    assert len(assigned) == len(set(assigned))

    # Coverage passes on the unique-name set (the raw physical list has repeats).
    plan.assert_covers(["MessageMonitoring", "OrderSync", "ReceiveDataLog"])

    # Multiplicity is surfaced, not dropped: 6 physical copies, 5 extra paths.
    dup_index = plan.duplicate_index()
    assert set(dup_index) == {"MessageMonitoring"}
    multiplicity = dup_index["MessageMonitoring"]
    assert multiplicity.copy_count == 6
    assert len(multiplicity.copy_source_paths) == 5  # the extra copies (kept one excluded)
    assert multiplicity.copy_source_paths == tuple(sorted(multiplicity.copy_source_paths))
    summary = plan.duplicate_summary()
    assert summary is not None
    assert "1 unit name(s) duplicated" in summary
    assert "5 extra physical copies" in summary
    assert "3 unique unit(s)" in summary


def test_planner_no_duplicate_summary_when_all_names_unique() -> None:
    units = _units(("A", "G", 100), ("B", "G2", 100))
    plan = AnalysisBatchPlanner(max_batch_bytes=1000).plan(units)
    assert plan.duplicates == ()
    assert plan.duplicate_summary() is None
    assert plan.duplicate_index() == {}


def test_split_cluster_emits_boundary_manifest_for_cut_edges() -> None:
    # A coupled cluster that exceeds the budget is split; the cut caller/callee
    # edge and shared connection surface in the boundary manifest.
    units = _units(
        ("Parent", "OrderFlow", 700),
        ("ChildA", "OrderFlow", 700),
        ("BillingWriter", "Billing", 700),
        ("ShippingReader", "Shipping", 700),
    )
    planner = AnalysisBatchPlanner(max_batch_bytes=1000)
    plan = planner.plan(
        units,
        call_edges=[("Parent", ["ChildA"])],
        connection_usage=[("sharedDb", ["BillingWriter", "ShippingReader"])],
    )
    plan.assert_covers([u.name for u in units])

    parent_batch = next(b for b in plan.batches if "Parent" in b.unit_names)
    assert "ChildA" not in parent_batch.unit_names  # edge was cut by the budget
    manifest = parent_batch.boundary_manifest
    assert any(
        edge.callee == "ChildA" and edge.direction == "outbound"
        for edge in manifest.cross_batch_call_edges
    )
    assert any(ref.unit == "ChildA" for ref in manifest.neighbor_summaries)

    billing_batch = next(b for b in plan.batches if "BillingWriter" in b.unit_names)
    assert "ShippingReader" not in billing_batch.unit_names
    assert any(
        shared.connection == "sharedDb"
        for shared in billing_batch.boundary_manifest.shared_connections
    )


# --------------------------------------------------------------------------
# Recipe fan-out template expansion.
# --------------------------------------------------------------------------


def _alitalend_pack():
    return load_grounded_execution_pack(
        load_recipe(_RECIPE_ROOT / "alitalend_legacy_equivalence" / "recipe.yaml")
    )


def _empty_manifest(batch_id: str) -> BoundaryManifest:
    return BoundaryManifest(batch_id, (), (), ())


def test_fanout_expands_to_n_analysis_roles_not_two() -> None:
    pack = _alitalend_pack()
    assert pack.analysis_fanout_template is not None

    batches = tuple(
        AnalysisBatch(
            batch_id=f"analysis_batch_{i:03d}",
            unit_names=(f"Unit{i}",),
            total_evidence_bytes=10,
            boundary_manifest=_empty_manifest(f"analysis_batch_{i:03d}"),
        )
        for i in range(1, 5)  # four batches
    )
    expanded = expand_grounded_analysis_fanout(pack, batches)

    analysis_batch_roles = [
        role
        for role in expanded.roles
        if role.stage == "analysis" and role.id.startswith("analysis_batch_")
    ]
    assert len(analysis_batch_roles) == 4
    # Distinct, generated per-batch output filenames.
    owned = [role.owns[0] for role in analysis_batch_roles]
    assert owned == [
        "candidate/intermediates/phase2/analysis_batch_001.md",
        "candidate/intermediates/phase2/analysis_batch_002.md",
        "candidate/intermediates/phase2/analysis_batch_003.md",
        "candidate/intermediates/phase2/analysis_batch_004.md",
    ]
    assert len(set(owned)) == len(owned)
    # Distinct assigned units, one per batch here.
    units = [role.units for role in analysis_batch_roles]
    assert units == [("Unit1",), ("Unit2",), ("Unit3",), ("Unit4",)]

    # The template itself is gone; dependents now see every batch role.
    assert expanded.analysis_fanout_template is None
    writer = next(role for role in expanded.roles if role.id == "writer-foundation")
    assert set(writer.depends_on) == {
        "analysis_batch_001",
        "analysis_batch_002",
        "analysis_batch_003",
        "analysis_batch_004",
        "analysis-scorecard",
    }
    topology = next(role for role in expanded.roles if role.id == "business-topology")
    assert "analysis_batch_003" in topology.depends_on
    # Concurrency is unchanged: batches run in bounded waves.
    assert expanded.max_parallelism == pack.max_parallelism


# --------------------------------------------------------------------------
# End-to-end: real ledger -> plan -> expand -> staged packets.
# --------------------------------------------------------------------------


def _component(unique: str, code: str) -> dict:
    return {
        "unique_name": unique,
        "component_type": "tJava",
        "code": code,
        "params": [],
    }


def _multi_group_sources(tmp_path: Path) -> tuple[tuple[str, Path], ...]:
    big = "x = 1; // " + ("payload " * 120)  # ~1 KB of verbatim code per unit

    def job(name: str, group: str) -> dict:
        return {
            "name": name,
            "group": group,
            "source_root": "ALI",
            "path": f"{name}.item",
            "source_design": "process",
            "components": [_component(f"tJava_{name}", big)],
            "connections": [],
        }

    scan = tmp_path / "scan_result.json"
    scan.write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    job("Parent", "OrderFlow"),
                    job("ChildA", "OrderFlow"),
                    job("BillingWriter", "Billing"),
                    job("ShippingReader", "Shipping"),
                ],
                "stats": {"total_jobs": 4},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "job_calls": {"Parent": ["ChildA"]},
                "connection_used_by": {"sharedDb": ["BillingWriter", "ShippingReader"]},
            }
        ),
        encoding="utf-8",
    )
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps({"platform": "talend", "stats": {"job_count": 4, "route_count": 0}}),
        encoding="utf-8",
    )
    redaction = tmp_path / "redaction.json"
    redaction.write_text(
        json.dumps({"redaction_level": "strict", "total_redactions": 0, "summary": {}}),
        encoding="utf-8",
    )
    return (
        ("scan:scan_result.json", scan),
        ("scan:scan/manifest.json", manifest),
        ("scan:scan/meta.json", meta),
        ("scan:redaction_report.json", redaction),
    )


def test_staged_batch_packets_carry_verbatim_evidence_and_boundary_manifest(
    tmp_path: Path,
) -> None:
    source_items = _multi_group_sources(tmp_path)
    ledger = compile_fact_ledger(source_items, project_id="ali")
    unit_plan = _UnitEvidencePlan.from_frozen_sources(source_items, ledger)

    # Small budget forces the coupled clusters to split so cross-batch edges
    # and shared connections appear.
    planner, plan = AnalysisBatchPlanner.from_fact_ledger(
        ledger,
        evidence_bytes=unit_plan.evidence_bytes(),
        max_batch_bytes=1500,
    )
    plan.assert_covers(
        [str(u["name"]) for u in ledger["canonical_units"]]
    )
    assert len(plan.batches) >= 2

    pack = expand_grounded_analysis_fanout(_alitalend_pack(), plan.batches)
    runner = GroundedOpenCodeRunner(
        runtime=FakeAgentRuntime(),
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=tmp_path / "workspaces",
        execution_pack=pack,
        timeout_seconds=2,
    )
    workspace = runner._prepare_workspace(
        experiment_id="fanout-staging",
        source_items=source_items,
        docs_root=tmp_path / "docs",
        ledger=ledger,
        source_preflight={"status": "pass"},
        include_legacy_workflow_inputs=False,
        project_evidence_snapshot=None,
        unit_evidence_plan=unit_plan,
        batch_plan=plan,
    )

    roles_root = workspace / "roles"
    batch_ids = [batch.batch_id for batch in plan.batches]
    for batch in plan.batches:
        role_input = roles_root / batch.batch_id / "input"
        # Verbatim per-unit evidence is staged with a reader-visible text
        # extension (never .bin), one bundle per assigned unit.
        evidence_dir = role_input / UNIT_EVIDENCE_DIRNAME
        assert evidence_dir.is_dir()
        staged = sorted(p.name for p in evidence_dir.glob("*"))
        assert staged and all(name.endswith(".md") for name in staged)
        assert (role_input / UNIT_EVIDENCE_INDEX_NAME).is_file()
        for unit in batch.unit_names:
            bundle = next(
                p for p in evidence_dir.glob("*.md")
                if unit in p.read_text(encoding="utf-8")
            )
            assert "CUSTOM CODE (VERBATIM)" in bundle.read_text(encoding="utf-8")
        # Boundary manifest lists cross-batch dependencies.
        manifest_path = role_input / BOUNDARY_MANIFEST_NAME
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["batch_id"] == batch.batch_id
        for ref in manifest["neighbor_summaries"]:
            assert ref["batch_id"] in batch_ids
            assert ref["batch_id"] != batch.batch_id

    # The split OrderFlow call edge and the split shared connection are captured.
    parent_batch = next(b for b in plan.batches if "Parent" in b.unit_names)
    parent_manifest = json.loads(
        (roles_root / parent_batch.batch_id / "input" / BOUNDARY_MANIFEST_NAME).read_text()
    )
    billing_batch = next(b for b in plan.batches if "BillingWriter" in b.unit_names)
    billing_manifest = json.loads(
        (roles_root / billing_batch.batch_id / "input" / BOUNDARY_MANIFEST_NAME).read_text()
    )
    assert any(
        edge["callee"] == "ChildA"
        for edge in parent_manifest["cross_batch_call_edges"]
    )
    assert any(
        shared["connection"] == "sharedDb"
        for shared in billing_manifest["shared_connections"]
    )
