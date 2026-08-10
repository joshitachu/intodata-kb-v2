"""Slice 4 -- Tier-A per-unit deep docs, writer fan-out, and prod/test tiering.

Covers:
- the production/test classifier (general signals, err-toward-production,
  force-all-deep override, transparent manifest with reasons);
- the Tier-A coverage gate reusing the BatchPlan / BatchCoverageError contract;
- the generalized writing fan-out (one per-batch writer owning one deep document
  per unit, each depending only on its own batch's analysis);
- end-to-end staging: every production unit's writer packet carries its verbatim
  evidence (code/SQL) and a deterministic unit-doc plan with tier + reasons;
- Tier B: the 10 summary docs survive and are declared to cover all units.
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
)
from mcp_server.experiments.application.fact_ledger import compile_fact_ledger
from mcp_server.experiments.application.grounded_pack import (
    expand_grounded_fanout,
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    UNIT_DOC_PLAN_NAME,
    UNIT_EVIDENCE_DIRNAME,
    UNIT_TIERING_NAME,
    GroundedOpenCodeRunner,
    _UnitEvidencePlan,
)
from mcp_server.experiments.application.unit_tiering import (
    PRODUCTION,
    TEST,
    classify_units,
    production_unit_names,
    tiering_manifest,
)
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.runtime.fake import FakeAgentRuntime
from mcp_server.experiments.infrastructure.opencode.runtime import (
    OpenCodeGroundedTaskBuilder,
)


_RECIPE_ROOT = Path(__file__).parents[2] / "data" / "agentic_recipes"


def _alitalend_pack():
    return load_grounded_execution_pack(
        load_recipe(_RECIPE_ROOT / "alitalend_legacy_equivalence" / "recipe.yaml")
    )


# --------------------------------------------------------------------------
# Classifier: general signals, err-toward-production, force-all, manifest.
# --------------------------------------------------------------------------


def _ledger(units: list[dict], call_graph: list[dict] | None = None) -> dict:
    return {"canonical_units": units, "call_graph": call_graph or []}


def test_classifier_flags_name_and_group_tokens_as_test() -> None:
    ledger = _ledger(
        [
            {"name": "OrderSyncTest", "group": "OrderFlow", "component_count": 5, "version": "1.0"},
            {"name": "Loader", "group": "Sandbox", "component_count": 5, "version": "1.0"},
        ],
        [{"caller": "OrderSyncTest", "callees": ["Loader"]}],
    )
    by = {d.unit: d for d in classify_units(ledger)}
    assert by["OrderSyncTest"].tier == TEST
    assert by["Loader"].tier == TEST
    assert any("test" in r for r in by["OrderSyncTest"].reasons)
    assert any("sandbox" in r for r in by["Loader"].reasons)


def test_classifier_does_not_false_match_substrings() -> None:
    # "latest" / "contest" tokenise to whole words, never the 'test' token.
    ledger = _ledger(
        [{"name": "GetLatestContest", "group": "Reporting", "component_count": 4, "version": "2.1"}],
        [{"caller": "Root", "callees": ["GetLatestContest"]}],
    )
    (decision,) = classify_units(ledger)
    assert decision.tier == PRODUCTION


def test_classifier_errs_toward_production_on_single_weak_signal() -> None:
    # Orphan (no callers/callees) is a single weak signal -> stays production.
    ledger = _ledger(
        [{"name": "BillingExport", "group": "Billing", "component_count": 6, "version": "1.4"}]
    )
    (decision,) = classify_units(ledger)
    assert decision.tier == PRODUCTION
    assert any("err toward production" in r for r in decision.reasons)


def test_classifier_two_weak_signals_tip_to_test() -> None:
    # Orphan + zero components -> two weak signals -> test.
    ledger = _ledger(
        [{"name": "EmptyStub", "group": "Misc", "component_count": 0, "version": "1.0"}]
    )
    (decision,) = classify_units(ledger)
    assert decision.tier == TEST
    assert any("orphan" in r for r in decision.reasons)
    assert any("zero components" in r for r in decision.reasons)


def test_classifier_zero_x_version_is_a_weak_signal() -> None:
    # Orphan + 0.x draft version -> two weak signals -> test.
    ledger = _ledger(
        [{"name": "DraftFlow", "group": "Ingest", "component_count": 3, "version": "0.2"}]
    )
    (decision,) = classify_units(ledger)
    assert decision.tier == TEST
    assert any("0.x" in r for r in decision.reasons)


def test_force_all_deep_overrides_every_signal() -> None:
    ledger = _ledger(
        [{"name": "SandboxScratch", "group": "Playground", "component_count": 0, "version": "0.1"}]
    )
    (decision,) = classify_units(ledger, force_all_deep=True)
    assert decision.tier == PRODUCTION
    assert any("forced all-units-deep" in r for r in decision.reasons)


def test_tiering_manifest_lists_every_unit_with_reasons() -> None:
    ledger = _ledger(
        [
            {"name": "OrderSync", "group": "OrderFlow", "component_count": 5, "version": "1.0"},
            {"name": "PlaygroundJob", "group": "Playground", "component_count": 0, "version": "0.1"},
        ]
    )
    decisions = classify_units(ledger)
    manifest = tiering_manifest(decisions, force_all_deep=False)
    assert manifest["unit_count"] == 2
    assert manifest["production_count"] == 1
    assert manifest["test_count"] == 1
    assert manifest["force_all_units_deep"] is False
    names = {row["unit"]: row for row in manifest["units"]}
    assert names["OrderSync"]["tier"] == PRODUCTION
    assert names["PlaygroundJob"]["tier"] == TEST
    assert names["PlaygroundJob"]["reasons"]  # never empty


# --------------------------------------------------------------------------
# Coverage gate reuses the BatchPlan / BatchCoverageError contract.
# --------------------------------------------------------------------------


def _empty_manifest(batch_id: str) -> BoundaryManifest:
    return BoundaryManifest(batch_id, (), (), ())


def test_production_coverage_gate_fails_when_a_production_unit_has_no_doc() -> None:
    plan = BatchPlan(
        batches=(
            AnalysisBatch("b1", ("OrderSync",), 1, _empty_manifest("b1")),
            AnalysisBatch("b2", ("BillingExport",), 1, _empty_manifest("b2")),
        ),
        max_batch_bytes=100,
    )
    # Both production units are covered -> passes.
    plan.assert_production_covered(["OrderSync", "BillingExport"])
    # Drop a batch so a production unit loses its deep doc -> hard failure.
    truncated = BatchPlan(batches=plan.batches[:1], max_batch_bytes=100)
    with pytest.raises(BatchCoverageError, match="no deep per-unit document"):
        truncated.assert_production_covered(["OrderSync", "BillingExport"])


def test_production_coverage_gate_ignores_test_units() -> None:
    plan = BatchPlan(
        batches=(AnalysisBatch("b1", ("OrderSync",), 1, _empty_manifest("b1")),),
        max_batch_bytes=100,
    )
    # A classified test unit that is absent from the batches is allowed.
    plan.assert_production_covered(["OrderSync"])


# --------------------------------------------------------------------------
# Writing fan-out: one per-batch writer, one deep doc per unit.
# --------------------------------------------------------------------------


def test_writing_fanout_owns_one_deep_doc_per_unit_and_pairs_its_analysis() -> None:
    pack = _alitalend_pack()
    assert pack.writing_fanout_template is not None
    # Summary writers (Tier B) own exactly the 10 declared docs before expansion.
    summary_docs = sorted(
        p for p in pack.owned_artifacts if p.startswith("candidate/docs/")
    )
    assert len(summary_docs) == 10

    batches = (
        AnalysisBatch("analysis_batch_001", ("Alpha", "Beta"), 10, _empty_manifest("analysis_batch_001")),
        AnalysisBatch("analysis_batch_002", ("Gamma",), 10, _empty_manifest("analysis_batch_002")),
    )
    expanded = expand_grounded_fanout(pack, batches)

    writers = [role for role in expanded.roles if role.unit_docs]
    assert [w.id for w in writers] == [
        "unitdoc_analysis_batch_001",
        "unitdoc_analysis_batch_002",
    ]
    # One owned document per unit.
    first = writers[0]
    assert first.owns == (
        "candidate/docs/units/Alpha.md",
        "candidate/docs/units/Beta.md",
    )
    assert first.unit_docs == (
        ("Alpha", "candidate/docs/units/Alpha.md"),
        ("Beta", "candidate/docs/units/Beta.md"),
    )
    # Each writer depends only on ITS OWN batch analysis (+ the scorecard).
    assert first.depends_on == ("analysis_batch_001", "analysis-scorecard")
    assert writers[1].depends_on == ("analysis_batch_002", "analysis-scorecard")
    # The template is gone and the reviewer now waits on every per-unit writer.
    assert expanded.writing_fanout_template is None
    reviewer = next(role for role in expanded.roles if role.id == "reviewer")
    assert "unitdoc_analysis_batch_001" in reviewer.depends_on
    assert "unitdoc_analysis_batch_002" in reviewer.depends_on
    # Tier B still owns exactly the 10 summary docs.
    still_summary = sorted(
        p
        for role in expanded.roles
        if role.id in {"writer-foundation", "writer-operations"}
        for p in role.owns
    )
    assert len(still_summary) == 10


# --------------------------------------------------------------------------
# End-to-end staging on a multi-group fixture.
# --------------------------------------------------------------------------


def _component(unique: str, ctype: str, *, code: str = "", query: str = "") -> dict:
    params = []
    if query:
        params.append({"name": "QUERY", "value": query})
    comp: dict = {"unique_name": unique, "component_type": ctype, "params": params}
    if code:
        comp["code"] = code
    return comp


def _multi_group_sources(tmp_path: Path) -> tuple[tuple[str, Path], ...]:
    big = "row.out = row.in.toUpperCase(); // " + ("payload " * 120)
    sql = "SELECT id, status FROM orders WHERE status = 'NEW' ORDER BY id"

    def job(name: str, group: str, components: list[dict]) -> dict:
        return {
            "name": name,
            "group": group,
            "source_root": "ALI",
            "path": f"{name}.item",
            "source_design": "process",
            "components": components,
            "connections": [],
        }

    scan = tmp_path / "scan_result.json"
    scan.write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    job("OrderSync", "OrderFlow", [
                        _component("tJavaRow_1", "tJavaRow", code=big),
                        _component("tMSSqlInput_1", "tMSSqlInput", query=sql),
                    ]),
                    job("OrderSyncChild", "OrderFlow", [
                        _component("tJavaRow_2", "tJavaRow", code=big),
                    ]),
                    job("BillingExport", "Billing", [
                        _component("tJavaRow_3", "tJavaRow", code=big),
                    ]),
                    job("SandboxScratch", "Playground", [
                        _component("tJavaRow_4", "tJavaRow", code=big),
                    ]),
                ],
                "stats": {"total_jobs": 4},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"job_calls": {"OrderSync": ["OrderSyncChild"]}}),
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


def _stage(tmp_path: Path):
    source_items = _multi_group_sources(tmp_path)
    ledger = compile_fact_ledger(source_items, project_id="ali")
    unit_plan = _UnitEvidencePlan.from_frozen_sources(source_items, ledger)
    decisions = classify_units(ledger)
    # Small budget so units land in several batches.
    _planner, plan = AnalysisBatchPlanner.from_fact_ledger(
        ledger,
        evidence_bytes=unit_plan.evidence_bytes(),
        max_batch_bytes=1500,
    )
    plan.assert_covers([str(u["name"]) for u in ledger["canonical_units"]])
    plan.assert_production_covered(production_unit_names(decisions))
    pack = expand_grounded_fanout(_alitalend_pack(), plan.batches)
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
        experiment_id="s4-staging",
        source_items=source_items,
        docs_root=tmp_path / "docs",
        ledger=ledger,
        source_preflight={"status": "pass"},
        include_legacy_workflow_inputs=False,
        project_evidence_snapshot=None,
        unit_evidence_plan=unit_plan,
        batch_plan=plan,
        tier_decisions=decisions,
    )
    return runner, workspace, ledger, pack, decisions


def test_production_units_get_a_deep_doc_slot_with_verbatim_evidence(tmp_path: Path) -> None:
    _runner, workspace, _ledger, pack, decisions = _stage(tmp_path)
    by = {d.unit: d for d in decisions}
    # BillingExport is an orphan (single weak signal) but stays production.
    assert by["OrderSync"].tier == PRODUCTION
    assert by["OrderSyncChild"].tier == PRODUCTION
    assert by["BillingExport"].tier == PRODUCTION
    assert by["SandboxScratch"].tier == TEST

    writer_roles = [role for role in pack.roles if role.unit_docs]
    assert writer_roles

    # Every production unit is owned by exactly one Tier-A writer doc.
    doc_units = {unit for role in writer_roles for unit, _doc in role.unit_docs}
    for unit, decision in by.items():
        if decision.tier == PRODUCTION:
            assert unit in doc_units, unit

    # A production unit's writer packet carries its verbatim CODE and SQL, and a
    # deterministic unit-doc plan mapping each unit to a deep/summary document.
    for role in writer_roles:
        role_input = workspace / "roles" / role.id / "input"
        evidence_dir = role_input / UNIT_EVIDENCE_DIRNAME
        assert evidence_dir.is_dir()
        plan_path = role_input / UNIT_DOC_PLAN_NAME
        assert plan_path.is_file()
        doc_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        planned = {row["unit"]: row for row in doc_plan["units"]}
        for unit, doc in role.unit_docs:
            assert planned[unit]["document"] == doc
            assert planned[unit]["tier"] == by[unit].tier
            assert planned[unit]["reasons"]
            expected_depth = "deep" if by[unit].tier == PRODUCTION else "summary"
            assert planned[unit]["depth"] == expected_depth
            bundle = next(
                p for p in evidence_dir.glob("*.md")
                if unit in p.read_text(encoding="utf-8")
            )
            body = bundle.read_text(encoding="utf-8")
            assert "CUSTOM CODE (VERBATIM)" in body
            if unit == "OrderSync":
                assert "SQL QUERIES (VERBATIM)" in body
                assert "SELECT id, status FROM orders" in body


def test_tiering_manifest_is_persisted_in_the_workspace(tmp_path: Path) -> None:
    _runner, workspace, _ledger, _pack, decisions = _stage(tmp_path)
    manifest_path = workspace / "candidate" / "orchestration" / UNIT_TIERING_NAME
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["unit_count"] == 4
    assert manifest["production_count"] == 3
    assert manifest["test_count"] == 1
    names = {row["unit"] for row in manifest["units"]}
    assert names == {"OrderSync", "OrderSyncChild", "BillingExport", "SandboxScratch"}


def test_summary_docs_survive_and_are_declared_to_cover_all_units(tmp_path: Path) -> None:
    runner, workspace, ledger, pack, _decisions = _stage(tmp_path)
    output_names = tuple(
        p.removeprefix("candidate/docs/")
        for role in pack.roles
        if role.id in {"writer-foundation", "writer-operations"}
        for p in role.owns
    )
    assert len(output_names) == 10

    runner._refresh_deterministic_orchestration(workspace, ledger, {}, output_names)
    coverage = json.loads(
        (workspace / "candidate" / "orchestration" / "coverage_ledger.json").read_text()
    )
    all_units = sorted(str(u["name"]) for u in ledger["canonical_units"])
    for name in output_names:
        assert sorted(coverage["document_coverage"][name]) == all_units
