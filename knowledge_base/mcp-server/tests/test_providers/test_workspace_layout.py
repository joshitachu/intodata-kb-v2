"""Workspace layout + builder (plan slice 1).

The tests that matter here are the byte-accounting ones. The failure this lane
replaces was a transform that kept 25 KB of a 520 KB analysis and reported
nothing, so a workspace builder that cannot DETECT that shape has not addressed
the defect -- it has only moved the files around. Four of the cases below
reproduce that exact shape:

* a mirrored 520 KB design shard truncated to 25 KB on disk,
* a derived artifact carrying content with no artifact of record named,
* a projection that kept 5 of 100 elements without saying where the rest live,
* a design that exists in the scan but is reachable from no index.

The synthetic fixture is a real-shaped intodoc scan (approval receipt, sharded
``scan/`` tree, monolithic ``scan_result.json``) small enough to build in
milliseconds. One integration-flavoured test runs against the real ETTM scan
when it is present, which is also the only place the approval-receipt digest
algorithm is checked against a receipt this repo did not write.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_server.providers.workspace.builder import (
    Provenance,
    ScanApprovalError,
    WorkspaceBuildError,
    build_workspace,
    read_provenance,
    verify_no_silent_truncation,
    write_json_artifact,
    write_markdown,
)
from mcp_server.providers.workspace.layout import WorkspaceLayout, WorkspacePathError

REPO_ROOT = Path(__file__).resolve().parents[4]
ETTM_SCAN_DIR = REPO_ROOT / "knowledge_base" / "intodoc-output" / "ettm_TEST_Claude"

#: The directories the plan's "Design" block lists, written out literally so the
#: assertion is against the specification and not against the layout module's
#: own opinion of itself.
PLAN_DIRECTORIES = (
    "knowledge",
    "skills",
    "rules",
    "policies",
    "templates",
    "packs",
    "facts",
    "scan",
    "indexes",
    "evidence",
    "notes",
    "findings",
    "investigations",
    "agent-state",
    "output",
)

PLAN_SUBDIRECTORIES = (
    "facts/mappings",
    "scan/jobs",
    "evidence/code",
    "evidence/runtime",
    "evidence/external",
    "investigations/active",
    "investigations/completed",
)

#: A name with a non-ASCII codepoint. Generated Markdown must stay ASCII while
#: keeping the identifier addressable.
NON_ASCII_DESIGN = "maximoFourps·Betaling"

BIG_DESIGN_TARGET_BYTES = 520_000


# ── synthetic scan fixture ──────────────────────────────────────────────────


def _dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(files: dict[str, str]) -> str:
    """Independent implementation of ``sha256-relpath-pairs-1``.

    Deliberately not imported from the builder: the fixture must be able to
    produce a receipt the builder verifies without sharing its code, or the
    tampering tests below would only prove that a function equals itself.
    """
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative].encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _component(index: int, *, body: str = "") -> dict:
    return {
        "unique_name": f"tJavaRow_{index}",
        "component_type": "tJavaRow",
        "active": True,
        "code": body or f"output_row.id = input_row.id + {index};",
        "params": [{"name": "CODE", "value": body or "// none"}],
    }


def _design(
    name: str,
    *,
    group: str,
    component_count: int = 3,
    kind: str = "job",
    esb: bool = False,
    padding: str = "",
) -> dict:
    return {
        "name": name,
        "job_id": name,
        "source_root": group.split("/")[0],
        "source_design": "process",
        "path": f"{name}_0.1.item",
        "purpose": f"purpose of {name}",
        "author": "scanner",
        "version": "0.1",
        "creation_date": "2026-01-01",
        "modification_date": "2026-01-02",
        "components": [
            _component(index, body=padding if index == 0 else "")
            for index in range(component_count)
        ],
        "connections": [{"connector_name": "FLOW", "source": "tJavaRow_0", "target": "tJavaRow_1"}],
        "context_groups": [{"name": "AFAS", "variables": [{"name": "afas_base", "values": {}}]}],
        "tmap_data": [],
        "txmlmap_data": [],
        "trunjob_refs": [],
        "artifact_refs": [],
        "raw_routines": [],
        "group": group,
        "subgroup": "",
        "active": True,
        "is_esb_route": esb,
        "esb_component_types": ["cREST"] if esb else [],
        "artifact_kind": kind,
        "artifact_role": "standalone",
        "scan_quality": "complete",
    }


def make_scan(
    root: Path,
    *,
    approved: bool = True,
    include_approval: bool = True,
    allow_redaction_failures: bool = False,
    corrupt_tree_digest: bool = False,
    mutate_after_approval: bool = False,
) -> Path:
    """Write a real-shaped intodoc scan output directory and return it."""
    root.mkdir(parents=True, exist_ok=True)
    scan = root / "scan"

    padding = "// " + ("x" * 400) + "\n"
    # Sized so the shard comfortably exceeds the 520 KB of the run-258 analysis
    # the bundler truncated; measured at roughly 260 bytes per component.
    big_components = max(2, BIG_DESIGN_TARGET_BYTES // 200)
    designs = [
        _design("readerOne", group="G1/afas"),
        _design("readerTwo", group="G1/afas"),
        _design("routeOne", group="G1/misc", kind="route", esb=True),
        _design(NON_ASCII_DESIGN, group="G2/misc"),
        _design(
            "bigDesign",
            group="G1/misc",
            component_count=big_components,
            padding=padding,
        ),
    ]
    shard_names = {
        "readerOne": "G1_afas/readerOne-aaaa.json",
        "readerTwo": "G1_afas/readerTwo-bbbb.json",
        "routeOne": "G1_misc/routeOne-cccc.json",
        NON_ASCII_DESIGN: "G2_misc/maximoFourpsBetaling-dddd.json",
        "bigDesign": "G1_misc/bigDesign-eeee.json",
    }
    job_files = {name: f"jobs/{shard_names[name]}" for name in shard_names}

    # Three routines, one of which is a system routine. The scanner's
    # routines.json carries non-system routines only, which is a REAL reduction
    # (measured on ETTM: 19 of 37) and is what the coverage table must report.
    routines = [
        {"routine_id": "r1", "name": "DQTechnical", "is_system": False, "source_code": "class A {}"},
        {"routine_id": "r2", "name": "LogData", "is_system": False, "source_code": "class B {}"},
        {"routine_id": "r3", "name": "TalendString", "is_system": True, "source_code": "class C {}"},
    ]
    contexts = [
        {
            "name": "AFAS",
            "context_key": "AFAS",
            "context_id": "_ctx_afas",
            "source_root": "G1",
            "source_project": "G1",
            "source_role": "execution",
            "purpose": "",
            "description": "",
            "variables": [
                {"name": "afas_base", "type": "String", "values": {"PROD": "https://afas.example"}},
                {"name": "afas_token", "type": "Password", "values": {"PROD": "[PASSWORD_REDACTED]"}},
            ],
        }
    ]
    connections = [
        {
            "xmi_id": "_conn1",
            "name": "G1::QuintiqDB",
            "connection_type": "Microsoft SQL Server",
            "host": "[HOST_REDACTED]",
            "port": "context.port",
        }
    ]
    mapping_accounting = [
        {
            "table_id": "map-readerOne-tMap_1",
            "unit_id": "readerOne",
            "component_name": "tMap_1",
            "mapper_kind": "tMap",
            "offered_fields": 4,
            "offered_enumerable": True,
            "written_fields": 3,
            "mapped_from_input": 2,
            "constant_or_generated": 1,
            "lookup_derived": 0,
            "dynamic_unresolved": 0,
            "rows": [
                {"output_table": "out", "output_field": "id", "expression": "row1.id",
                 "classification": "mapped", "source_refs": ["row1.id"], "dynamic_refs": []},
                {"output_table": "out", "output_field": "name", "expression": "row1.name",
                 "classification": "mapped", "source_refs": ["row1.name"], "dynamic_refs": []},
                {"output_table": "out", "output_field": "stamp", "expression": "TalendDate.getDate()",
                 "classification": "constant_or_generated", "source_refs": [], "dynamic_refs": []},
            ],
            "unwritten_offered": ["row1.unused"],
            "lookup_tables": [],
            "filters": [],
            "reject_outputs": [],
            "unenumerable_inputs": [],
        },
        {
            "table_id": "map-readerTwo-tXMLMap_1",
            "unit_id": "readerTwo",
            "component_name": "tXMLMap_1",
            "mapper_kind": "tXMLMap",
            "offered_fields": 2,
            "offered_enumerable": True,
            "written_fields": 2,
            "mapped_from_input": 2,
            "constant_or_generated": 0,
            "lookup_derived": 0,
            "dynamic_unresolved": 0,
            "rows": [
                {"output_table": "out", "output_field": "a", "expression": "row.a",
                 "classification": "mapped", "source_refs": ["row.a"], "dynamic_refs": []},
            ],
            "unwritten_offered": [],
            "lookup_tables": [],
            "filters": [],
            "reject_outputs": [],
            "unenumerable_inputs": [],
        },
    ]
    capability_ledger = {"schema_version": 1, "parsed_fully": ["process"], "not_parsed": []}
    files_index = [
        {"path": f"{name}_0.1.item", "sha256": f"digest-{name}", "size": 100}
        for name in sorted(shard_names)
    ]
    manifest = {
        "job_calls": {"readerOne": ["readerTwo"]},
        "job_called_by": {"readerTwo": ["readerOne"]},
        "job_connections_used": {"readerOne": ["JMS · ettm.readerOne"]},
        "connection_used_by": {"G1::QuintiqDB": ["readerOne"]},
        "job_contexts_used": {"readerOne": ["AFAS"]},
        "routines_used_by": {"DQTechnical": ["readerOne"]},
        "routine_metadata": {"DQTechnical": {"path": "code/routines/DQTechnical_0.1.item"}},
        "job_routine_links": {
            "readerOne": [{"routine_id": "r1", "name": "DQTechnical", "resolution": "same_root"}]
        },
        "unresolved_routine_refs": {"routeOne": [{"name": "LogData", "resolution": "unresolved"}]},
        "artifact_versions": [],
        "job_groups": {name: designs[index]["group"] for index, name in enumerate(
            ["readerOne", "readerTwo", "routeOne", NON_ASCII_DESIGN, "bigDesign"])},
        "job_external_systems": {"readerOne": ["REST API"], "readerTwo": ["REST API"],
                                 "routeOne": ["JMS"]},
        "source_roots": [
            {"id": "G1", "path": "G1", "platform": "talend", "role": "execution", "label": "One"},
            {"id": "G2", "path": "G2", "platform": "talend", "role": "execution", "label": "Two"},
        ],
        "job_source_roots": {name: name.split("/")[0] for name in shard_names},
        "job_files": job_files,
        "unresolved_job_calls": {},
        "context_used_by": {"AFAS": ["readerOne"]},
        "context_metadata": {"AFAS": {"name": "AFAS", "context_id": "_ctx_afas"}},
        "job_context_links": {"readerOne": ["AFAS"]},
        "unresolved_context_refs": {"routeOne": [{"context_id": "_ctx_x", "resolution": "unresolved"}]},
        "job_support_roots": {},
        "artifact_kinds": {
            "readerOne": "job", "readerTwo": "job", "routeOne": "route",
            NON_ASCII_DESIGN: "job", "bigDesign": "job",
        },
        "artifact_relations": [
            {"source": "readerOne", "target": "readerTwo", "relation_type": "job_calls_job",
             "resolution": "same_root", "provenance": "tRunJob", "component_name": "tRunJob_1"},
            {"source": "routeOne", "target": "missingJob", "relation_type": "job_calls_job",
             "resolution": "unresolved", "provenance": "tRunJob", "component_name": "tRunJob_2"},
        ],
        "artifact_channels": [
            {"artifact": "routeOne", "channel_type": "jms", "channel": "\"ettm.route\"",
             "channel_binding": "literal", "channel_descriptor": None, "direction": "input",
             "component_name": "cJMS_1", "provenance": "cJMS"},
        ],
        "artifact_endpoints": [
            {"artifact": "routeOne", "endpoint_type": "rest", "direction": "input",
             "component_name": "cREST_1", "provenance": "cREST", "endpoint_descriptor": None},
        ],
        "artifact_resources": [],
        "evidence_package": {"index": "evidence/manifest.json"},
        "sanitized_workspace": {"files": 1},
        "resource_refs": [
            {"artifact": "readerTwo", "source_root": "G1", "component_name": "tXSLT_1",
             "param": "XSLT_FILE", "resource_type": "xslt", "resource": "",
             "resolution": "unresolved", "reason": "referenced_file_not_in_scan"},
        ],
    }
    meta = {
        "platform": "talend",
        "repo_root": "/srv/source",
        "scanned_at": "2026-01-02 03:04:05",
        "stats": {"jobs": len(designs)},
        "warnings": [{"code": "X", "message": "a warning"}],
        "source_inventory": {"G1": {"files": 3}},
    }

    _dump(scan / "meta.json", meta)
    _dump(scan / "connections.json", connections)
    _dump(scan / "contexts.json", contexts)
    _dump(scan / "tdm_maps.json", [])
    _dump(scan / "structures.json", [])
    _dump(scan / "xslt.json", [])
    _dump(scan / "services.json", [])
    _dump(scan / "mapping_accounting.json", mapping_accounting)
    _dump(scan / "capability_ledger.json", capability_ledger)
    _dump(scan / "routines.json", [r for r in routines if not r["is_system"]])
    _dump(scan / "files_index.json", files_index)
    _dump(scan / "landscape.json", {"applications": []})
    _dump(scan / "lineage.json", {"flows": []})
    _dump(scan / "summaries.json", {"designs": {}})
    for design in designs:
        _dump(scan / "jobs" / shard_names[design["name"]], design)
    _dump(scan / "manifest.json", manifest)

    # Excluded by the scanner's approval rule; present so the tests prove they
    # are never staged.
    _dump(scan / "evidence" / "manifest.json", {"fragments": []})
    _dump(scan / "evidence" / "abc.json", {"content": "routine source page"})
    _dump(scan / "sanitized" / "manifest.json", {"files": []})
    (scan / "sanitized" / "G1").mkdir(parents=True, exist_ok=True)
    (scan / "sanitized" / "G1" / "readerOne_0.1.item").write_text("<raw/>", encoding="utf-8")

    scan_result = {
        "platform": meta["platform"],
        "repo_root": meta["repo_root"],
        "scanned_at": meta["scanned_at"],
        "stats": meta["stats"],
        "warnings": meta["warnings"],
        "source_inventory": meta["source_inventory"],
        "jobs": designs,
        "manifest": manifest,
        "metadata_connections": connections,
        "context_groups": contexts,
        "routines": routines,
        "files": files_index,
        "structures": [],
        "schema_definitions": [{"name": "row1", "fields": ["id", "name"]}],
        "services": [],
        "tdm_maps": [],
        "xslt_transforms": [],
        "mapping_accounting": mapping_accounting,
        "capability_ledger": capability_ledger,
    }
    _dump(root / "scan_result.json", scan_result)
    _dump(root / "redaction_report.json", {"total_redactions": 2})

    if not include_approval:
        return root

    files = {}
    for path in sorted(scan.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(("scan/evidence/", "scan/sanitized/")):
            continue
        files[relative] = _sha256(path)
    digest = _tree_digest(files)
    approval = {
        "schema_version": 2,
        "approved": approved,
        "allow_redaction_failures": allow_redaction_failures,
        "artifacts": {
            "scan_result.json": _sha256(root / "scan_result.json"),
            "scan/manifest.json": _sha256(scan / "manifest.json"),
            "scan/lineage.json": _sha256(scan / "lineage.json"),
            "scan/landscape.json": _sha256(scan / "landscape.json"),
        },
        "verifications": {
            "scan_result.json": {"passed": True},
            "scan/manifest.json": {"passed": True},
            "scan/lineage.json": {"passed": True},
            "scan/landscape.json": {"passed": True},
        },
        "tree": {
            "algorithm": "sha256-relpath-pairs-1",
            "digest": "sha256:" + ("0" * 64) if corrupt_tree_digest else digest,
            "file_count": len(files),
            "files": files,
            "roots": ["scan"],
            "excluded_prefixes": ["scan/evidence/", "scan/sanitized/"],
        },
    }
    _dump(root / "publication_approval.json", approval)
    if mutate_after_approval:
        target = scan / "jobs" / shard_names["readerOne"]
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    return root


@pytest.fixture()
def scan_dir(tmp_path: Path) -> Path:
    return make_scan(tmp_path / "scan_output")


@pytest.fixture()
def built(tmp_path: Path, scan_dir: Path):
    return build_workspace(
        project_id="synthetic_project",
        scan_output_dir=scan_dir,
        workspaces_dir=tmp_path / "workspaces",
    )


# ── 1. the tree the plan specifies ──────────────────────────────────────────


def test_build_creates_every_directory_the_plan_lists(built) -> None:
    layout = built.layout
    for name in PLAN_DIRECTORIES + PLAN_SUBDIRECTORIES:
        assert layout.path(name).is_dir(), f"plan directory missing: {name}"
    assert layout.project_doc.is_file()
    assert layout.agent_guide.is_file()
    assert layout.build_receipt.is_file()
    for path in (
        layout.facts_units,
        layout.facts_dependencies,
        layout.facts_interfaces,
        layout.facts_configuration,
        layout.facts_scan_coverage,
        layout.facts_coverage_ledger,
        layout.facts_limitations,
        layout.facts_mappings_index,
        layout.index_estate,
        layout.index_systems,
        layout.index_dependencies,
        layout.index_unresolved,
        layout.scan_blob,
        layout.scan_manifest,
    ):
        assert path.is_file(), f"expected artifact missing: {layout.relative(path)}"


def test_scan_is_staged_sharded_and_addressable(built) -> None:
    layout = built.layout
    shards = sorted(p.name for p in layout.scan_jobs.rglob("*.json"))
    assert len(shards) == 5 == built.design_count
    units = json.loads(layout.facts_units.read_text(encoding="utf-8"))["units"]
    assert {row["unit_id"] for row in units} == {
        "readerOne", "readerTwo", "routeOne", "bigDesign", NON_ASCII_DESIGN,
    }
    for row in units:
        assert layout.path(row["shard"]).is_file()
    # The monolith stays addressable beside the shards -- it is the artifact of
    # record for anything a shard reduces.
    assert layout.scan_blob.is_file()


def test_excluded_scanner_lanes_are_never_staged(built) -> None:
    """``scan/sanitized/`` and ``scan/evidence/`` may not reach an agent."""
    layout = built.layout
    assert not (layout.scan / "sanitized").exists()
    assert not (layout.scan / "evidence").exists()
    assert list(layout.evidence_code.iterdir()) == []
    # ...and the workspace says so rather than leaving an unexplained gap.
    assert "scan/sanitized/" in layout.facts_limitations.read_text(encoding="utf-8")


# ── 2. byte accounting: the heart of the slice ──────────────────────────────


def test_clean_build_passes_its_own_truncation_check(built) -> None:
    assert verify_no_silent_truncation(built.layout) == []


def test_every_derived_artifact_names_its_artifact_of_record(built) -> None:
    layout = built.layout
    checked = 0
    for lane in (layout.facts, layout.indexes):
        for path in sorted(p for p in lane.rglob("*") if p.is_file()):
            provenance = read_provenance(path)
            assert provenance is not None, f"no provenance: {layout.relative(path)}"
            assert provenance.artifact_of_record, f"no artifact of record: {layout.relative(path)}"
            for source in provenance.artifact_of_record:
                assert source.startswith("scan/")
                assert layout.path(source.rstrip("/")).exists()
            checked += 1
    assert checked >= 10


def test_a_truncated_mirror_copy_is_detected(built) -> None:
    """The literal run-258 shape: a 520 KB artifact reduced to 25 KB.

    The bundler kept 25 KB of a 520 KB analysis and a zero-match parse was
    indistinguishable from "nothing here". Applied to the evidence mirror, the
    same shape is a staged shard whose bytes no longer match what was approved.
    """
    layout = built.layout
    big = next(p for p in layout.scan_jobs.rglob("*") if p.name.startswith("bigDesign"))
    original = big.read_bytes()
    assert len(original) > BIG_DESIGN_TARGET_BYTES, len(original)
    big.write_bytes(original[:25_000])

    problems = verify_no_silent_truncation(layout)
    assert any("does not match its approved digest" in p for p in problems), problems
    assert any("bigDesign" in p for p in problems), problems


def test_a_derived_artifact_without_provenance_is_detected(built) -> None:
    """A consumer-facing file that carries content but names no source.

    The shape of the defect: a real-content file with no statement of what it
    came from or what it left behind.  The since-deleted
    `scripts/bundle_analysis.py` wrote its `_bundle_core_a.md` exactly this way.
    """
    layout = built.layout
    rogue = layout.facts / "transformation_detail.md"
    rogue.write_text("# TRANSFORMATION DETAIL\n\nsome carried-over content\n", encoding="utf-8")
    problems = verify_no_silent_truncation(layout)
    assert any("declares no provenance" in p and "transformation_detail" in p for p in problems), (
        problems
    )


def test_a_projection_that_drops_elements_without_an_address_is_detected(built) -> None:
    layout = built.layout
    target = layout.facts / "partial_sections.md"
    write_markdown(
        target,
        Provenance(
            derivation_kind="projection",
            artifact_of_record=("scan/manifest.json",),
            source_elements=100,
            retained_elements=5,
        ),
        ["# Partial", "", "five of a hundred sections"],
    )
    problems = verify_no_silent_truncation(layout)
    assert any("dropped elements without saying where" in p for p in problems), problems

    # Naming a staged artifact that still holds all of them clears it.
    write_markdown(
        target,
        Provenance(
            derivation_kind="projection",
            artifact_of_record=("scan/manifest.json",),
            source_elements=100,
            retained_elements=5,
            dropped_elements_addressable_at=("scan/scan_result.json",),
        ),
        ["# Partial", "", "five of a hundred sections"],
    )
    assert verify_no_silent_truncation(layout) == []


def test_a_projection_must_account_for_elements_at_all(built) -> None:
    layout = built.layout
    write_json_artifact(
        layout.facts / "unaccounted.json",
        Provenance(derivation_kind="projection", artifact_of_record=("scan/manifest.json",)),
        {"rows": [1, 2, 3]},
    )
    problems = verify_no_silent_truncation(layout)
    assert any("projection without element accounting" in p for p in problems), problems


def test_an_artifact_of_record_outside_the_evidence_lane_is_rejected(built) -> None:
    layout = built.layout
    write_markdown(
        layout.indexes / "second_hand.md",
        Provenance(
            derivation_kind="index",
            artifact_of_record=("facts/units.json",),
        ),
        ["# Second hand", "", "derived from a derived view"],
    )
    problems = verify_no_silent_truncation(layout)
    assert any("artifact of record is itself derived" in p for p in problems), problems


def test_an_empty_derived_artifact_is_detected(built) -> None:
    """Total loss must be loud (plan decision 5a)."""
    layout = built.layout
    (layout.indexes / "empty.md").write_text("", encoding="utf-8")
    problems = verify_no_silent_truncation(layout)
    assert any("derived artifact is empty" in p for p in problems), problems


def test_a_design_dropped_from_the_unit_index_is_detected(built) -> None:
    layout = built.layout
    payload = json.loads(layout.facts_units.read_text(encoding="utf-8"))
    payload["units"] = [row for row in payload["units"] if row["unit_id"] != "readerTwo"]
    layout.facts_units.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    problems = verify_no_silent_truncation(layout)
    assert any("not reachable from facts/units.json: readerTwo" in p for p in problems), problems


def test_a_design_missing_from_the_estate_index_is_detected(built) -> None:
    layout = built.layout
    text = layout.index_estate.read_text(encoding="utf-8").replace("readerTwo", "somethingElse")
    layout.index_estate.write_text(text, encoding="utf-8")
    problems = verify_no_silent_truncation(layout)
    assert any("not reachable from indexes/estate.md: readerTwo" in p for p in problems), problems


def test_a_broken_coverage_promise_is_detected(built) -> None:
    layout = built.layout
    payload = json.loads(layout.facts_scan_coverage.read_text(encoding="utf-8"))
    payload["keys"].append({
        "key": "invented",
        "staged_path": "scan/invented.json",
        "source_elements": 10,
        "staged_elements": 1,
        "complete": False,
        "identical": False,
        "artifact_of_record": "scan/not_staged.json",
        "note": "",
    })
    layout.facts_scan_coverage.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    problems = verify_no_silent_truncation(layout)
    assert any("names an artifact of record that is not staged" in p for p in problems), problems


def test_scan_coverage_reports_the_measured_shard_reduction(built) -> None:
    """``routines.json`` is non-system only -- a real, reported reduction."""
    layout = built.layout
    coverage = json.loads(layout.facts_scan_coverage.read_text(encoding="utf-8"))["keys"]
    by_key = {entry["key"]: entry for entry in coverage}

    routines = by_key["routines"]
    assert routines["source_elements"] == 3
    assert routines["staged_elements"] == 2
    assert routines["complete"] is False
    assert routines["artifact_of_record"] == "scan/scan_result.json"
    assert layout.path(routines["artifact_of_record"]).is_file()

    # A key with no shard at all is accounted for by the monolith, not ignored.
    assert by_key["schema_definitions"]["staged_path"] == "scan/scan_result.json"

    # Designs are element-identical between the two representations.
    jobs = by_key["jobs"]
    assert jobs["source_elements"] == jobs["staged_elements"] == 5
    assert jobs["identical"] is True

    # And the reduction is surfaced where a human will read it.
    limitations = layout.facts_limitations.read_text(encoding="utf-8")
    assert "routines" in limitations
    assert "scan/scan_result.json" in limitations
    assert "routines" in " ".join(built.warnings)


def test_every_scan_result_key_is_accounted_for(built, scan_dir: Path) -> None:
    source_keys = set(json.loads((scan_dir / "scan_result.json").read_text(encoding="utf-8")))
    covered = {
        entry["key"]
        for entry in json.loads(built.layout.facts_scan_coverage.read_text(encoding="utf-8"))["keys"]
    }
    assert source_keys <= covered, source_keys - covered


# ── 3. determinism ──────────────────────────────────────────────────────────


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_building_twice_is_byte_identical(tmp_path: Path, scan_dir: Path) -> None:
    first = build_workspace(
        project_id="p", scan_output_dir=scan_dir, workspaces_dir=tmp_path / "a"
    )
    second = build_workspace(
        project_id="p", scan_output_dir=scan_dir, workspaces_dir=tmp_path / "b"
    )
    assert _snapshot(first.layout.root) == _snapshot(second.layout.root)

    # Rebuilding in place is idempotent too.
    before = _snapshot(first.layout.root)
    build_workspace(project_id="p", scan_output_dir=scan_dir, workspaces_dir=tmp_path / "a")
    assert _snapshot(first.layout.root) == before


def test_the_scan_fingerprint_is_path_independent(tmp_path: Path, scan_dir: Path) -> None:
    """One identity for "the same scan", so Slice 4 does not invent a second."""
    import shutil as _shutil

    copy = tmp_path / "copy_of_scan"
    _shutil.copytree(scan_dir, copy)
    first = build_workspace(project_id="p", scan_output_dir=scan_dir, workspaces_dir=tmp_path / "x")
    second = build_workspace(project_id="p", scan_output_dir=copy, workspaces_dir=tmp_path / "y")
    assert first.scan_fingerprint == second.scan_fingerprint
    assert first.scan_fingerprint.startswith("sha256:")
    receipt = json.loads(first.layout.build_receipt.read_text(encoding="utf-8"))
    assert receipt["scan"]["scan_fingerprint"] == first.scan_fingerprint


def test_building_in_another_process_is_byte_identical(tmp_path: Path, scan_dir: Path) -> None:
    """A different hash seed must not change one byte.

    Set iteration order is process-dependent, and this repo has already shipped
    one artifact that was not byte-stable across processes for exactly that
    reason (``_bounded_values`` sorting a set on casefold alone), which silently
    voided a provider prompt cache. A same-process comparison cannot see it.
    """
    reference = build_workspace(
        project_id="p", scan_output_dir=scan_dir, workspaces_dir=tmp_path / "ref"
    )
    other = tmp_path / "other"
    script = (
        "from pathlib import Path\n"
        "from mcp_server.providers.workspace.builder import build_workspace\n"
        f"build_workspace(project_id='p', scan_output_dir=Path({str(scan_dir)!r}),"
        f" workspaces_dir=Path({str(other)!r}))\n"
    )
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = "12345"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert _snapshot(reference.layout.root) == _snapshot(other / "p")


# ── 4. fail-closed on approval ──────────────────────────────────────────────


def test_a_scan_without_an_approval_receipt_is_refused(tmp_path: Path) -> None:
    scan = make_scan(tmp_path / "unapproved", include_approval=False)
    with pytest.raises(ScanApprovalError, match="no publication approval"):
        build_workspace(
            project_id="p", scan_output_dir=scan, workspaces_dir=tmp_path / "workspaces"
        )
    assert not (tmp_path / "workspaces" / "p").exists()


def test_a_scan_approved_false_is_refused(tmp_path: Path) -> None:
    scan = make_scan(tmp_path / "declined", approved=False)
    with pytest.raises(ScanApprovalError, match="does not approve publication"):
        build_workspace(
            project_id="p", scan_output_dir=scan, workspaces_dir=tmp_path / "workspaces"
        )
    assert not (tmp_path / "workspaces" / "p").exists()


def test_a_failed_verification_is_refused(tmp_path: Path) -> None:
    scan = make_scan(tmp_path / "failed_verification")
    approval_path = scan / "publication_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["verifications"]["scan/lineage.json"] = {"passed": False, "matches": 3}
    approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
    with pytest.raises(ScanApprovalError, match="failed verifications"):
        build_workspace(
            project_id="p", scan_output_dir=scan, workspaces_dir=tmp_path / "workspaces"
        )


def test_a_tampered_approval_tree_is_refused(tmp_path: Path) -> None:
    scan = make_scan(tmp_path / "tampered", corrupt_tree_digest=True)
    with pytest.raises(ScanApprovalError, match="tree digest does not recompute"):
        build_workspace(
            project_id="p", scan_output_dir=scan, workspaces_dir=tmp_path / "workspaces"
        )


def test_an_artifact_changed_after_approval_is_refused(tmp_path: Path) -> None:
    scan = make_scan(tmp_path / "mutated", mutate_after_approval=True)
    with pytest.raises(WorkspaceBuildError, match="has changed since approval"):
        build_workspace(
            project_id="p", scan_output_dir=scan, workspaces_dir=tmp_path / "workspaces"
        )


def test_a_relaxed_redaction_approval_is_reported(tmp_path: Path) -> None:
    scan = make_scan(tmp_path / "relaxed", allow_redaction_failures=True)
    receipt = build_workspace(
        project_id="p", scan_output_dir=scan, workspaces_dir=tmp_path / "workspaces"
    )
    assert any("allow-redaction-failures" in warning for warning in receipt.warnings)
    assert "allow-redaction-failures" in receipt.layout.facts_limitations.read_text(
        encoding="utf-8"
    )


def test_a_file_added_after_approval_is_reported_and_not_staged(tmp_path: Path) -> None:
    scan = make_scan(tmp_path / "extra")
    (scan / "scan" / "jobs" / "G1_afas" / ".DS_Store").write_bytes(b"junk")
    receipt = build_workspace(
        project_id="p", scan_output_dir=scan, workspaces_dir=tmp_path / "workspaces"
    )
    assert any("not covered by the approval receipt" in w for w in receipt.warnings)
    assert not (receipt.layout.scan_jobs / "G1_afas" / ".DS_Store").exists()


# ── 5. ASCII-only generated Markdown (house rule 4) ─────────────────────────


def test_generated_markdown_is_ascii_only(built) -> None:
    layout = built.layout
    checked = 0
    for path in sorted(layout.root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        offending = [(index, char) for index, char in enumerate(text) if ord(char) > 127]
        assert not offending, f"{layout.relative(path)} has non-ASCII at {offending[:3]}"
        checked += 1
    assert checked >= 6
    # The non-ASCII identifier survives as a reversible escape, not as '?'.
    estate = layout.index_estate.read_text(encoding="utf-8")
    assert "maximoFourps\\xb7Betaling" in estate
    assert "maximoFourps?Betaling" not in estate


def test_generated_json_is_ascii_escaped(built) -> None:
    layout = built.layout
    for path in (layout.facts_units, layout.facts_configuration, layout.build_receipt):
        assert path.read_text(encoding="utf-8").isascii(), layout.relative(path)


# ── 6. persistence and path safety ──────────────────────────────────────────


def test_rebuild_preserves_agent_written_state(tmp_path: Path, scan_dir: Path) -> None:
    receipt = build_workspace(
        project_id="p", scan_output_dir=scan_dir, workspaces_dir=tmp_path / "workspaces"
    )
    layout = receipt.layout
    (layout.knowledge / "terminology.md").write_text("VRI = ...\n", encoding="utf-8")
    (layout.investigations_completed / "afas.md").write_text("done\n", encoding="utf-8")
    (layout.agent_state / "checkpoint.json").write_text("{}\n", encoding="utf-8")
    (layout.notes / "scratch.md").write_text("thinking\n", encoding="utf-8")
    layout.project_doc.write_text("# Project\n\nAgent-maintained.\n", encoding="utf-8")

    build_workspace(
        project_id="p", scan_output_dir=scan_dir, workspaces_dir=tmp_path / "workspaces"
    )
    assert (layout.knowledge / "terminology.md").read_text(encoding="utf-8") == "VRI = ...\n"
    assert (layout.investigations_completed / "afas.md").is_file()
    assert (layout.agent_state / "checkpoint.json").is_file()
    assert (layout.notes / "scratch.md").is_file()
    assert "Agent-maintained" in layout.project_doc.read_text(encoding="utf-8")


def test_layout_refuses_to_leave_the_workspace(tmp_path: Path) -> None:
    layout = WorkspaceLayout.for_project(tmp_path, "project")
    with pytest.raises(WorkspacePathError):
        layout.path("../escape.md")
    with pytest.raises(WorkspacePathError):
        layout.path("facts/../../escape.md")
    with pytest.raises(WorkspacePathError):
        layout.path("/etc/passwd")
    with pytest.raises(WorkspacePathError):
        WorkspaceLayout.for_project(tmp_path, "..")
    assert layout.path("facts/units.json") == tmp_path / "project" / "facts" / "units.json"


def test_layout_normalises_both_shard_path_forms(tmp_path: Path) -> None:
    layout = WorkspaceLayout.for_project(tmp_path, "project")
    assert layout.scan_shard("scan/jobs/g/d.json") == layout.scan_shard("jobs/g/d.json")
    assert layout.scan_shard_relative("jobs/g/d.json") == "scan/jobs/g/d.json"


def test_a_project_id_cannot_be_a_path(tmp_path: Path) -> None:
    layout = WorkspaceLayout.for_project(tmp_path, "../../etc")
    assert layout.root.parent == tmp_path
    assert ".." not in layout.root.name


# ── 7. the real estate ──────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (ETTM_SCAN_DIR / "publication_approval.json").is_file(),
    reason="the ETTM scan cache is not present on this machine",
)
def test_real_ettm_scan_builds_and_verifies(tmp_path: Path) -> None:
    """One integration-flavoured run over the real 142-design scan.

    Also the only check that the approval-receipt digest algorithm agrees with a
    receipt this repository did not write.
    """
    receipt = build_workspace(
        project_id="ettm_TEST_Claude",
        scan_output_dir=ETTM_SCAN_DIR,
        workspaces_dir=tmp_path / "workspaces",
    )
    layout = receipt.layout
    assert receipt.design_count == 142
    assert receipt.scan_file_count == 157  # 156 approved artifacts + the monolith
    assert receipt.mapping_table_count == 111
    assert verify_no_silent_truncation(layout) == []

    coverage = {
        entry["key"]: entry
        for entry in json.loads(layout.facts_scan_coverage.read_text(encoding="utf-8"))["keys"]
    }
    # Measured this session: the shard tree is element-identical to the monolith
    # for all 142 designs, and routines.json is non-system only (19 of 37).
    assert coverage["jobs"]["source_elements"] == 142
    assert coverage["jobs"]["identical"] is True
    assert coverage["routines"]["source_elements"] == 37
    assert coverage["routines"]["staged_elements"] == 19
    assert coverage["routines"]["complete"] is False

    for path in sorted(layout.root.rglob("*.md")):
        assert path.read_text(encoding="utf-8").isascii(), layout.relative(path)

    assert not (layout.scan / "sanitized").exists()
    assert not (layout.scan / "evidence").exists()
