"""Slice 5 -- route prepared Tier-3 documented mappings into the docs.

Covers:
- the deterministic mapping-to-unit association (general identifier signals);
- the redacted "Documented mappings (Tier-3 context)" section, byte-bounded so
  it never displaces verbatim code;
- unmatched mappings surviving at the project tier (never dropped);
- end-to-end staging: a matched mapping reaches the unit's evidence bundle and
  its UNIT_DOC_PLAN mapping_refs, and the full set reaches the Tier-B summary
  writers.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_server.experiments.application.analysis_batch_planner import AnalysisBatchPlanner
from mcp_server.experiments.application.fact_ledger import compile_fact_ledger
from mcp_server.experiments.application.grounded_pack import (
    expand_grounded_fanout,
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.mapping_association import (
    DocumentedMapping,
    build_mapping_association_plan,
    unit_signal_tokens,
)
from mcp_server.experiments.application.prepare_supporting_evidence import (
    SupportingEvidencePreparationService,
)
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    DOCUMENTED_MAPPINGS_NAME,
    UNIT_DOC_PLAN_NAME,
    UNIT_EVIDENCE_DIRNAME,
    GroundedOpenCodeRunner,
    _UnitEvidencePlan,
)
from mcp_server.experiments.application.snapshot_project_evidence import (
    CreateProjectEvidenceSnapshotRequest,
    ProjectEvidenceSnapshotService,
)
from mcp_server.experiments.application.unit_tiering import (
    classify_units,
    production_unit_names,
)
from mcp_server.experiments.domain.sources import SourceDescriptor
from mcp_server.experiments.infrastructure.opencode.runtime import OpenCodeGroundedTaskBuilder
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.runtime.fake import FakeAgentRuntime
from mcp_server.experiments.infrastructure.sources.prepared_directory import (
    PreparedDirectoryEvidenceAdapter,
)
from mcp_server.experiments.infrastructure.sources.registry import SourceAdapterRegistry


_RECIPE_ROOT = Path(__file__).parents[2] / "data" / "agentic_recipes"


def _alitalend_pack():
    return load_grounded_execution_pack(
        load_recipe(_RECIPE_ROOT / "alitalend_legacy_equivalence" / "recipe.yaml")
    )


# --------------------------------------------------------------------------
# Pure association: general signals, bounded section, never-drop.
# --------------------------------------------------------------------------


def _unit(name: str, group: str, *, query: str = "", components=None) -> dict:
    comps = list(components or [])
    if query:
        comps.append(
            {
                "unique_name": "tMSSqlInput_1",
                "component_type": "tMSSqlInput",
                "params": [{"name": "QUERY", "value": query}],
            }
        )
    return {"name": name, "group": group, "components": comps, "connections": []}


def test_unit_signal_tokens_use_concrete_identifiers_only() -> None:
    tokens = unit_signal_tokens(
        _unit("OrderSync", "OrderFlow", query="SELECT custref, status FROM orders WHERE status='NEW'")
    )
    # Distinctive identifiers are present: table, unit/group names, and the
    # column names a mapping sheet would reference.
    assert "orders" in tokens
    assert "ordersync" in tokens
    assert "orderflow" in tokens
    assert "custref" in tokens
    # Generic SQL keywords are filtered out as stopwords.
    assert "select" not in tokens
    assert "from" not in tokens
    assert "where" not in tokens


def test_mapping_matching_a_unit_table_attaches_and_records_refs() -> None:
    units = {
        "OrderSync": _unit("OrderSync", "OrderFlow", query="SELECT id FROM orders"),
        "BillingExport": _unit("BillingExport", "Billing"),
    }
    matched = DocumentedMapping(
        evidence_id="m-orders",
        logical_identity="proj:prepared/field_map.txt",
        claim_class="intended_documented",
        text="Field mapping sheet: CustomerName maps to the orders table for the OrderSync job.",
    )
    unmatched = DocumentedMapping(
        evidence_id="m-generic",
        logical_identity="proj:prepared/glossary.txt",
        claim_class="intended_documented",
        text="General glossary describing retention windows and archived record policy.",
    )
    plan = build_mapping_association_plan([matched, unmatched], units)

    # (d) The mapping that shares identifiers with OrderSync attaches to it.
    assert plan.unit_refs["OrderSync"] == ("m-orders",)
    assert "BillingExport" not in plan.unit_refs
    tokens = plan.unit_matched_tokens["OrderSync"]["m-orders"]
    assert "orders" in tokens or "ordersync" in tokens

    section = plan.unit_section("OrderSync", max_bytes=100_000)
    assert section is not None
    assert "DOCUMENTED MAPPINGS (TIER-3 CONTEXT)" in section
    assert "NEVER overrides" in section
    assert "orders table for the OrderSync job" in section
    assert "[evidence:m-orders]" in section
    assert plan.unit_section("BillingExport", max_bytes=100_000) is None

    # (e) The unmatched mapping is not dropped: project-only + in project section.
    assert plan.project_only_ids == ("m-generic",)
    project = plan.project_section(max_bytes=200_000)
    assert project is not None
    assert "[evidence:m-generic]" in project
    assert "[evidence:m-orders]" in project  # full set, matched and unmatched
    assert "retention windows" in project


def test_unit_section_bounds_mappings_without_touching_code_budget() -> None:
    units = {"OrderSync": _unit("OrderSync", "OrderFlow", query="SELECT id FROM orders")}
    big = DocumentedMapping(
        evidence_id="m-big",
        logical_identity="proj:prepared/big.txt",
        claim_class="intended_documented",
        text="orders " + ("payload detail " * 5000),
    )
    plan = build_mapping_association_plan([big], units)
    section = plan.unit_section("OrderSync", max_bytes=2000)
    assert section is not None
    assert len(section.encode("utf-8")) <= 2000
    assert "truncated" in section  # explicit, logged truncation -- never silent


def test_empty_supplemental_returns_no_project_section() -> None:
    plan = build_mapping_association_plan([], {"OrderSync": _unit("OrderSync", "OrderFlow")})
    assert plan.total_mappings == 0
    assert plan.project_section(max_bytes=1000) is None


# --------------------------------------------------------------------------
# End-to-end staging with a real prepared supporting-evidence snapshot.
# --------------------------------------------------------------------------


def _component(unique: str, ctype: str, *, code: str = "", query: str = "") -> dict:
    params = []
    if query:
        params.append({"name": "QUERY", "value": query})
    comp: dict = {"unique_name": unique, "component_type": ctype, "params": params}
    if code:
        comp["code"] = code
    return comp


def _scan_sources(tmp_path: Path) -> tuple[tuple[str, Path], ...]:
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
                    job("BillingExport", "Billing", [
                        _component("tJavaRow_3", "tJavaRow", code=big),
                    ]),
                ],
                "stats": {"total_jobs": 2},
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"job_calls": {}}), encoding="utf-8")
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps({"platform": "talend", "stats": {"job_count": 2, "route_count": 0}}),
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


def _supporting_snapshot(tmp_path: Path):
    workspace = tmp_path / "supporting"
    workspace.mkdir()
    # Matches OrderSync via the 'orders' table and the unit name.
    (workspace / "field_map.txt").write_text(
        "Field mapping sheet: source column CustomerName maps to the orders table; "
        "this documents the OrderSync interface.\n",
        encoding="utf-8",
    )
    # Matches no unit -> must survive at the project tier only.
    (workspace / "glossary.txt").write_text(
        "General glossary describing retention windows and archived record policy.\n",
        encoding="utf-8",
    )
    descriptor = SourceDescriptor(
        id="managed-project-workspace",
        source_kind="directory_evidence",
        source_role="supporting_reference",
        source_path=workspace,
        provenance_locator="project-workspace://werkorder",
        capabilities=frozenset({"searchable_text"}),
        trust_level="managed_workspace_unredacted",
        metadata={},
    )
    preparation_root = tmp_path / "prepared"
    prepared = SupportingEvidencePreparationService().prepare(
        descriptor, output_root=preparation_root
    )
    return ProjectEvidenceSnapshotService(
        registry=SourceAdapterRegistry(
            (PreparedDirectoryEvidenceAdapter(allowed_roots=(preparation_root,)),)
        )
    ).create(CreateProjectEvidenceSnapshotRequest(descriptors=(prepared.descriptor,)))


def test_mappings_reach_unit_evidence_docplan_and_summary_tier(tmp_path: Path) -> None:
    source_items = _scan_sources(tmp_path)
    ledger = compile_fact_ledger(source_items, project_id="ali")
    unit_plan = _UnitEvidencePlan.from_frozen_sources(source_items, ledger)
    decisions = classify_units(ledger)
    _planner, plan = AnalysisBatchPlanner.from_fact_ledger(
        ledger,
        evidence_bytes=unit_plan.evidence_bytes(),
        max_batch_bytes=1500,
    )
    plan.assert_covers([str(u["name"]) for u in ledger["canonical_units"]])
    plan.assert_production_covered(production_unit_names(decisions))
    pack = expand_grounded_fanout(_alitalend_pack(), plan.batches)
    snapshot = _supporting_snapshot(tmp_path)

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
        experiment_id="s5-staging",
        source_items=source_items,
        docs_root=tmp_path / "docs",
        ledger=ledger,
        source_preflight={"status": "pass"},
        include_legacy_workflow_inputs=False,
        project_evidence_snapshot=snapshot,
        unit_evidence_plan=unit_plan,
        batch_plan=plan,
        tier_decisions=decisions,
    )

    # (d) The OrderSync unit-doc writer carries the mapping in its evidence
    # bundle and lists it under mapping_refs in the deterministic doc plan.
    writer_roles = [role for role in pack.roles if role.unit_docs]
    ordersync_writer = next(
        role for role in writer_roles if any(u == "OrderSync" for u, _ in role.unit_docs)
    )
    role_input = workspace / "roles" / ordersync_writer.id / "input"
    bundle = next(
        p
        for p in (role_input / UNIT_EVIDENCE_DIRNAME).glob("*.md")
        if "Unit Evidence: OrderSync" in p.read_text(encoding="utf-8")
    )
    bundle_text = bundle.read_text(encoding="utf-8")
    assert "CUSTOM CODE (VERBATIM)" in bundle_text  # code still present, first
    assert "DOCUMENTED MAPPINGS (TIER-3 CONTEXT)" in bundle_text
    assert "orders table" in bundle_text
    # Verbatim code appears before the appended Tier-3 mapping section.
    assert bundle_text.index("CUSTOM CODE (VERBATIM)") < bundle_text.index(
        "DOCUMENTED MAPPINGS (TIER-3 CONTEXT)"
    )

    doc_plan = json.loads((role_input / UNIT_DOC_PLAN_NAME).read_text(encoding="utf-8"))
    ordersync_row = next(row for row in doc_plan["units"] if row["unit"] == "OrderSync")
    assert ordersync_row["mapping_refs"]  # non-empty: the field_map mapping id

    # (e) The full mapping set -- including the unmatched glossary -- reaches the
    # Tier-B summary writers via the project-tier documented-mappings file.
    summary_doc = (
        workspace / "roles" / "writer-foundation" / "input" / DOCUMENTED_MAPPINGS_NAME
    )
    assert summary_doc.is_file()
    summary_text = summary_doc.read_text(encoding="utf-8")
    assert "retention windows" in summary_text  # the unmatched mapping survived
    assert "orders table" in summary_text        # matched one is present too
