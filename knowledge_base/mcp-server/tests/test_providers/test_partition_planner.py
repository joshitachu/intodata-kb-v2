"""Unit tests for deterministic composite sub-project partitioning."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server.providers.custom import partition_planner as pp
from mcp_server.providers.custom.models import FactsManifest, UnitFact

ZIP = pp.ZIP_MARKER


def _unit(name: str, group: str, source_root: str, *, systems=None, comps=None) -> UnitFact:
    return UnitFact(
        name=name,
        display_name=name,
        path=f"process/{name}.item",
        group=group,
        source_root=source_root,
        component_count=sum((comps or {}).values()),
        component_types=comps or {},
        key_systems=systems or [],
    )


def _manifest(units: list[UnitFact], project_id: str = "proj") -> FactsManifest:
    groups: dict[str, int] = {}
    for u in units:
        groups[u.group] = groups.get(u.group, 0) + 1
    return FactsManifest(
        project_id=project_id,
        platform="talend",
        canonical_unit_label="Talend jobs",
        unit_count=len(units),
        groups=dict(sorted(groups.items())),
        units=list(units),
    )


# --------------------------------------------------------------------------- #
# subproject_key derivation
# --------------------------------------------------------------------------- #

def test_subproject_key_from_zip_stem():
    root = f"Bundle/Talend exports/Assetcheck{ZIP}/ASSETCHECK"
    assert pp.subproject_key(root) == "Assetcheck"


def test_subproject_key_references_folds_into_owning_zip():
    # A REFERENCES root nested inside a zip belongs to that zip, not its own bucket.
    root = f"Bundle/Talend exports/Assetcheck{ZIP}/REFERENCES"
    assert pp.subproject_key(root) == "Assetcheck"


def test_subproject_key_handles_backslashes():
    root = f"Bundle\\exports\\ETTM{ZIP}\\ERP_TO_THE_MAX"
    assert pp.subproject_key(root) == "ETTM"


def test_subproject_key_none_without_marker():
    assert pp.subproject_key("werkorder_bundle/ASSETCHECK") is None
    assert pp.subproject_key("") is None


# --------------------------------------------------------------------------- #
# plan_partitions — composite
# --------------------------------------------------------------------------- #

def test_composite_partitions_group_by_zip_stem_with_references_folded():
    units = [
        _unit("a1", "AssetCheck_Interface", f"B/Assetcheck{ZIP}/ASSETCHECK"),
        _unit("a2", "Global", f"B/Assetcheck{ZIP}/ASSETCHECK"),
        _unit("aref", "Global", f"B/Assetcheck{ZIP}/REFERENCES"),  # folds into Assetcheck
        _unit("e1", "ERP", f"B/ETTM{ZIP}/ERP_TO_THE_MAX"),
        _unit("e2", "Global", f"B/ETTM{ZIP}/ERP_TO_THE_MAX"),
        _unit("eref", "Global", f"B/ETTM{ZIP}/REFERENCES"),  # folds into ETTM
    ]
    plan = pp.plan_partitions(_manifest(units))
    assert plan.is_composite is True
    assert plan.names() == ["Assetcheck", "ETTM"]

    by_name = {p.name: p for p in plan.partitions}
    assert by_name["Assetcheck"].unit_count == 3  # a1, a2, aref
    assert by_name["ETTM"].unit_count == 3  # e1, e2, eref
    # REFERENCES unit landed in the owning zip's partition, not dropped.
    assert "aref" in by_name["Assetcheck"].unit_names
    assert "eref" in by_name["ETTM"].unit_names


def test_shared_label_group_split_per_partition_not_dropped():
    # "Global" spans both sub-projects; each partition keeps only its own Global units.
    units = [
        _unit("a1", "Global", f"B/Assetcheck{ZIP}/ASSETCHECK"),
        _unit("e1", "Global", f"B/ETTM{ZIP}/ERP_TO_THE_MAX"),
    ]
    facts = _manifest(units)
    plan = pp.plan_partitions(facts)
    a = pp.filter_manifest(facts, next(p for p in plan.partitions if p.name == "Assetcheck"))
    e = pp.filter_manifest(facts, next(p for p in plan.partitions if p.name == "ETTM"))
    assert a.groups == {"Global": 1}
    assert e.groups == {"Global": 1}
    assert a.unit_names() == {"a1"}
    assert e.unit_names() == {"e1"}


def test_composite_no_stem_root_goes_to_shared_partition():
    units = [
        _unit("a1", "G", f"B/Assetcheck{ZIP}/ASSETCHECK"),
        _unit("s1", "G", "B/Talend exports/REFERENCES"),  # composite mode, no zip stem
    ]
    plan = pp.plan_partitions(_manifest(units))
    assert plan.is_composite is True
    assert pp.SHARED_PARTITION in plan.names()
    shared = next(p for p in plan.partitions if p.name == pp.SHARED_PARTITION)
    assert shared.unit_names == ("s1",)


def test_partition_budget_within_cap():
    units = [_unit(f"u{i}", f"G{i % 30}", f"B/Big{ZIP}/X") for i in range(400)]
    plan = pp.plan_partitions(_manifest(units), cap=250)
    for part in plan.partitions:
        assert part.call_budget <= 250
        assert part.call_budget == pp._estimate_call_budget(
            part.group_count, cap=250, unit_count=part.unit_count
        )
        # Splits are sized by the work-plan estimate (P0.1): every split's
        # planned calls must fit the cap (single oversized groups excepted).
        assert part.budget_fits == (
            pp.estimate_partition_calls(part.unit_count, part.group_count) <= 250
        )
        if part.split_groups is not None and part.group_count > 1:
            assert part.budget_fits


def test_partition_estimate_scales_with_units_not_groups():
    """The verified werkorder failure: 142 units in 20 groups needed ~142
    Phase-2 calls but the old per-group budget granted 20 + 20*4 = 100. The
    work-plan estimate must scale with units so such partitions are split."""
    estimate = pp.estimate_partition_calls(142, 20)
    assert estimate > 142  # at least one call per Phase-2 unit packet
    small = pp.estimate_partition_calls(5, 2)
    assert small < estimate


# --------------------------------------------------------------------------- #
# plan_partitions — single root (regression guard)
# --------------------------------------------------------------------------- #

def test_single_root_yields_one_partition():
    units = [_unit(f"u{i}", "Global", "werkorder_bundle/ASSETCHECK") for i in range(10)]
    plan = pp.plan_partitions(_manifest(units))
    assert plan.is_composite is False
    assert plan.names() == [pp.WHOLE_PROJECT_PARTITION]
    assert plan.partitions[0].unit_count == 10


def test_empty_source_root_yields_one_partition():
    units = [_unit(f"u{i}", "Global", "") for i in range(5)]
    plan = pp.plan_partitions(_manifest(units))
    assert plan.is_composite is False
    assert plan.names() == [pp.WHOLE_PROJECT_PARTITION]


def test_whole_project_filter_returns_all_units_including_empty_roots():
    units = [_unit(f"u{i}", "Global", "") for i in range(5)]
    facts = _manifest(units)
    plan = pp.plan_partitions(facts)
    scoped = pp.filter_manifest(facts, plan.partitions[0])
    assert scoped.unit_count == 5
    assert scoped.unit_names() == facts.unit_names()


# --------------------------------------------------------------------------- #
# filter_manifest — no leakage, lossless union
# --------------------------------------------------------------------------- #

def test_filter_no_leakage_and_lossless_union():
    units = [
        _unit("a1", "AC", f"B/Assetcheck{ZIP}/ASSETCHECK", comps={"tMap": 2}, systems=["mssql"]),
        _unit("a2", "Global", f"B/Assetcheck{ZIP}/REFERENCES", comps={"tMap": 1}, systems=["rest"]),
        _unit("e1", "ERP", f"B/ETTM{ZIP}/ERP_TO_THE_MAX", comps={"tJava": 3}, systems=["mssql"]),
    ]
    facts = _manifest(units)
    plan = pp.plan_partitions(facts)

    all_ids = {(u.group, u.path, u.name, u.source_root) for u in facts.units}
    covered: set = set()
    total = 0
    for part in plan.partitions:
        scoped = pp.filter_manifest(facts, part)
        # No leakage: every scoped unit belongs to this partition's roots.
        assert all(u.source_root in set(part.source_roots) for u in scoped.units)
        ids = {(u.group, u.path, u.name, u.source_root) for u in scoped.units}
        assert not (covered & ids), "partitions overlap"
        covered |= ids
        total += scoped.unit_count
        # unit_count is consistent with the recomputed groups.
        assert sum(scoped.groups.values()) == scoped.unit_count

    assert covered == all_ids
    assert total == facts.unit_count


def test_filter_recomputes_component_counts_and_key_systems():
    units = [
        _unit("a1", "AC", f"B/Assetcheck{ZIP}/ASSETCHECK", comps={"tMap": 2}, systems=["mssql"]),
        _unit("e1", "ERP", f"B/ETTM{ZIP}/ERP_TO_THE_MAX", comps={"tJava": 3}, systems=["sap"]),
    ]
    facts = _manifest(units)
    plan = pp.plan_partitions(facts)
    ac = pp.filter_manifest(facts, next(p for p in plan.partitions if p.name == "Assetcheck"))
    assert ac.component_counts == {"tMap": 2}
    assert ac.key_systems == ["mssql"]


# --------------------------------------------------------------------------- #
# cross-project index
# --------------------------------------------------------------------------- #

def test_cross_project_index_reports_shared_systems():
    units = [
        _unit("a1", "AC", f"B/Assetcheck{ZIP}/ASSETCHECK", systems=["mssql", "rest"]),
        _unit("e1", "ERP", f"B/ETTM{ZIP}/ERP_TO_THE_MAX", systems=["mssql", "sap"]),
    ]
    facts = _manifest(units)
    plan = pp.plan_partitions(facts)
    idx = pp.build_cross_project_index(facts, plan)
    assert idx["is_composite"] is True
    assert set(idx["subprojects"]) == {"Assetcheck", "ETTM"}
    # mssql is referenced by both sub-projects; rest/sap are not shared.
    assert idx["shared_systems"] == {"mssql": ["Assetcheck", "ETTM"]}


# --------------------------------------------------------------------------- #
# Real werkorder scan (integration guard, skipped when scan absent)
# --------------------------------------------------------------------------- #

_WERKORDER = Path(__file__).resolve().parents[3] / "intodoc-output" / "werkorder"


@pytest.mark.skipif(
    not (_WERKORDER / "scan_result.json").exists(),
    reason="werkorder scan not present",
)
def test_real_werkorder_partitions_fit_budget():
    from mcp_server.providers.custom.validators import build_facts_manifest

    facts = build_facts_manifest("werkorder", _WERKORDER)
    plan = pp.plan_partitions(facts)
    assert plan.is_composite is True
    # Work-plan sizing (P0.1) splits the big zips further; the zip-level
    # sub-projects are recovered via parent folding for the connective layer.
    top = {p.name for p in pp.top_level_partitions(plan)}
    assert top == {
        "Assetcheck",
        "ETTM",
        "EnterpriseServiceBus",
        "VQ_MQ2RHM",
        "VQ_Quintiq2MX",
        "VQ_RHM2SF",
    }
    # Every run partition's planned work fits the 250-call budget (the old
    # per-group sizing let ETTM/ESB exhaust their budgets in Phase 2).
    assert all(p.budget_fits for p in plan.partitions)
    for part in plan.partitions:
        assert pp.estimate_partition_calls(part.unit_count, part.group_count) <= plan.cap
    # Lossless: union of partition units equals the full scan, no duplicates.
    total = sum(pp.filter_manifest(facts, p).unit_count for p in plan.partitions)
    assert total == facts.unit_count == 429


# --------------------------------------------------------------------------- #
# P3 — size-aware splitting of oversized partitions
# --------------------------------------------------------------------------- #

def test_oversized_single_root_splits_by_groups_within_cap():
    # 300 units' planned work far exceeds one 250-call run -> must split into
    # bins whose work-plan estimate each fits the cap.
    units = [
        _unit(f"u{i}", f"G{i % 100:03d}", "bundle/ROOT")
        for i in range(300)
    ]
    facts = _manifest(units)
    plan = pp.plan_partitions(facts, base=20, per_group=4, cap=250)
    assert plan.is_composite is False
    assert len(plan.partitions) > 1
    assert plan.names() == [f"part{i + 1}" for i in range(len(plan.partitions))]
    for part in plan.partitions:
        assert part.parent == pp.WHOLE_PROJECT_PARTITION
        assert part.split_groups is not None
        assert pp.estimate_partition_calls(part.unit_count, part.group_count) <= 250
        assert part.budget_fits

    # Disjoint + lossless through filter_manifest, whole groups only.
    covered: set = set()
    total = 0
    seen_groups: set = set()
    for part in plan.partitions:
        scoped = pp.filter_manifest(facts, part)
        ids = {(u.group, u.path, u.name) for u in scoped.units}
        assert not (covered & ids)
        covered |= ids
        total += scoped.unit_count
        assert not (seen_groups & set(scoped.groups)), "group straddles splits"
        seen_groups |= set(scoped.groups)
        # unit_names on the partition equals the filtered membership
        assert set(part.unit_names) == scoped.unit_names()
    assert total == facts.unit_count


def test_oversized_composite_partition_splits_with_zip_prefix():
    units = [
        _unit(f"a{i}", f"G{i % 80:03d}", f"B/Big{ZIP}/BIG")
        for i in range(160)
    ] + [_unit("s1", "Small", f"B/Small{ZIP}/SMALL")]
    plan = pp.plan_partitions(_manifest(units), base=20, per_group=4, cap=250)
    assert plan.is_composite is True
    names = plan.names()
    assert "Small" in names
    assert "Big_part1" in names and "Big_part2" in names
    big1 = next(p for p in plan.partitions if p.name == "Big_part1")
    assert big1.parent == "Big"
    assert big1.split_groups is not None


def test_small_projects_never_split():
    units = [_unit(f"u{i}", f"G{i % 5}", "bundle/ROOT") for i in range(50)]
    plan = pp.plan_partitions(_manifest(units), base=20, per_group=4, cap=250)
    assert plan.names() == [pp.WHOLE_PROJECT_PARTITION]
    assert plan.partitions[0].split_groups is None


# --------------------------------------------------------------------------- #
# P2 — cross-project call edges + landscape layers
# --------------------------------------------------------------------------- #

def _two_partition_facts():
    units = [
        _unit("A_Main", "Flows", f"B/Alpha{ZIP}/ALPHA"),
        _unit("A_Log-hash1", "Logging", f"B/Alpha{ZIP}/REFERENCES"),
        _unit("B_Worker", "Flows", f"B/Beta{ZIP}/BETA"),
        _unit("B_Log-hash2", "Logging", f"B/Beta{ZIP}/REFERENCES"),
    ]
    # Same display name in both partitions (shared REFERENCES copy).
    units[1] = units[1].model_copy(update={"display_name": "LogInfo"})
    units[3] = units[3].model_copy(update={"display_name": "LogInfo"})
    facts = _manifest(units)
    return facts, pp.plan_partitions(facts)


def test_cross_partition_call_edge_emitted_only_when_callee_absent_locally():
    facts, plan = _two_partition_facts()
    scan_manifest = {
        "job_calls": {
            # Cross edge: A_Main calls B_Worker, which exists only in Beta.
            "A_Main": ["B_Worker"],
            # NOT a cross edge: LogInfo exists in the caller's own partition
            # (same-partition-first resolution keeps shared REFERENCES local).
            "B_Worker": ["LogInfo"],
        },
    }
    idx = pp.build_cross_project_index(facts, plan, scan_manifest=scan_manifest)
    edges = idx["call_edges"]
    assert len(edges) == 1
    edge = edges[0]
    assert edge["from_subproject"] == "Alpha"
    assert edge["to_subproject"] == "Beta"
    assert edge["caller"] == "A_Main"
    assert edge["callee"] == "B_Worker"
    assert edge["caller_unit"] == "A_Main"
    assert edge["callee_unit"] == "B_Worker"
    assert edge["resolution"] == "resolved"


def test_unresolved_call_resolving_in_sibling_partition_is_flagged():
    facts, plan = _two_partition_facts()
    scan_manifest = {
        "job_calls": {},
        "unresolved_job_calls": {
            "A_Main": [{"child_job": "B_Worker", "resolution": "unresolved"}],
        },
    }
    idx = pp.build_cross_project_index(facts, plan, scan_manifest=scan_manifest)
    assert len(idx["call_edges"]) == 1
    assert idx["call_edges"][0]["resolution"] == "unresolved_in_own_subproject"


def test_landscape_layers_shared_systems_and_app_edges():
    facts, plan = _two_partition_facts()
    landscape = {
        "applications": [
            {"id": "app:alpha", "label": "Alpha", "source_root": f"B/Alpha{ZIP}/ALPHA"},
            {"id": "app:beta", "label": "Beta", "source_root": f"B/Beta{ZIP}/BETA"},
        ],
        "systems": [
            {"id": "system:logdb", "name": "LogDB", "kind": "database"},
        ],
        "edges": [
            {"source": "app:alpha", "target": "system:logdb", "mechanism": "db"},
            {"source": "app:beta", "target": "system:logdb", "mechanism": "db"},
            {"source": "app:alpha", "target": "app:beta",
             "dependency_type": "integration", "mechanism": "jms", "direction": "out"},
        ],
    }
    idx = pp.build_cross_project_index(facts, plan, landscape=landscape)
    shared = idx["shared_landscape_systems"]
    assert "system:logdb" in shared
    assert shared["system:logdb"]["subprojects"] == ["Alpha", "Beta"]
    assert shared["system:logdb"]["mechanisms"] == ["db"]
    app_edges = idx["application_edges"]
    assert len(app_edges) == 1
    assert app_edges[0]["from_subproject"] == "Alpha"
    assert app_edges[0]["to_subproject"] == "Beta"
    assert app_edges[0]["mechanism"] == "jms"


def test_render_cross_project_refs_md_lists_only_own_relationships():
    facts, plan = _two_partition_facts()
    scan_manifest = {"job_calls": {"A_Main": ["B_Worker"]}}
    idx = pp.build_cross_project_index(facts, plan, scan_manifest=scan_manifest)
    alpha_md = pp.render_cross_project_refs_md(idx, "Alpha")
    beta_md = pp.render_cross_project_refs_md(idx, "Beta")
    assert alpha_md is not None and "B_Worker" in alpha_md
    assert "call jobs in other sub-projects" in alpha_md
    assert beta_md is not None and "call jobs here" in beta_md


def test_render_cross_project_refs_md_none_for_isolated_partition():
    facts, plan = _two_partition_facts()
    idx = pp.build_cross_project_index(facts, plan)  # no manifest/landscape, no shared systems
    assert pp.render_cross_project_refs_md(idx, "Alpha") is None


def test_render_cross_project_topology_md_contains_partitions_and_edges():
    facts, plan = _two_partition_facts()
    scan_manifest = {"job_calls": {"A_Main": ["B_Worker"]}}
    idx = pp.build_cross_project_index(facts, plan, scan_manifest=scan_manifest)
    md = pp.render_cross_project_topology_md(idx)
    assert "# Cross-Project Topology" in md
    assert "Alpha" in md and "Beta" in md
    assert "A_Main" in md and "B_Worker" in md


# --------------------------------------------------------------------------- #
# Output routing helpers
# --------------------------------------------------------------------------- #

def test_partition_output_subdir():
    assert pp.partition_output_subdir(pp.WHOLE_PROJECT_PARTITION) is None
    assert pp.partition_output_subdir("Assetcheck") == "Assetcheck"
    assert pp.partition_output_subdir("VQ Quintiq/2MX") == "VQ_Quintiq_2MX"
    assert pp.partition_output_subdir("__shared__") == "shared"


@pytest.mark.skipif(
    not (_WERKORDER / "scan_result.json").exists(),
    reason="werkorder scan not present",
)
def test_real_werkorder_cross_project_index_has_connective_layers():
    import json as _json

    from mcp_server.providers.custom.validators import build_facts_manifest

    facts = build_facts_manifest("werkorder", _WERKORDER)
    plan = pp.plan_partitions(facts)
    scan_manifest = _json.loads((_WERKORDER / "scan" / "manifest.json").read_text())
    landscape = _json.loads((_WERKORDER / "scan" / "landscape.json").read_text())
    idx = pp.build_cross_project_index(
        facts, plan, scan_manifest=scan_manifest, landscape=landscape
    )
    # Every zip's job calls resolve inside its own export: no false cross edges
    # from the shared REFERENCES copies.
    assert idx["call_edges"] == []
    # The genuine connective layer: ETTM -> EnterpriseServiceBus over JMS, and
    # shared databases spanning several sub-projects.
    assert any(
        e["from_subproject"] == "ETTM" and e["to_subproject"] == "EnterpriseServiceBus"
        for e in idx["application_edges"]
    )
    assert len(idx["shared_landscape_systems"]) >= 3
    md = pp.render_cross_project_topology_md(idx)
    assert "EnterpriseServiceBus" in md
    # The index is keyed at the zip level: size-split run partitions look up
    # their parent's entry (mirrors _materialize_cross_project_refs).
    top_names = {p.name for p in pp.top_level_partitions(plan)}
    assert not any(name.endswith(tuple(f"_part{i}" for i in range(1, 10))) for name in top_names)
    for part in plan.partitions:
        index_name = part.parent or part.name
        refs = pp.render_cross_project_refs_md(idx, index_name)
        assert refs is not None, f"{index_name} should reference shared systems"
