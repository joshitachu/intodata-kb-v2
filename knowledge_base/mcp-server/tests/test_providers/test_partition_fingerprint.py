"""Per-sub-project fingerprints: one repo changing must not invalidate its siblings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.providers.custom import partition_planner as pp
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.partition_fingerprint import (
    UNATTRIBUTABLE_ARTIFACTS,
    compute_partition_fingerprints,
)

REPO = pp.REPO_MARKER
ZIP = pp.ZIP_MARKER


def _unit(name: str, group: str, source_root: str) -> UnitFact:
    return UnitFact(
        name=name,
        display_name=name,
        path=f"process/{name}.item",
        group=group,
        source_root=source_root,
    )


def _manifest(units: list[UnitFact]) -> FactsManifest:
    groups: dict[str, int] = {}
    for u in units:
        groups[u.group] = groups.get(u.group, 0) + 1
    return FactsManifest(
        project_id="proj",
        platform="talend",
        canonical_unit_label="Talend jobs",
        unit_count=len(units),
        groups=dict(sorted(groups.items())),
        units=list(units),
    )


def _write_scan(
    root: Path,
    *,
    jobs: dict[str, tuple[str, dict]],
    contexts: list[dict] | None = None,
    routines: list[dict] | None = None,
) -> None:
    """Materialise a minimal scan output tree.

    ``jobs`` maps unit id -> (physical group dir, job payload). The scanner's
    ``manifest.job_files`` index is written from it, exactly as intodoc's
    ``cli.py`` does.
    """
    scan = root / "scan"
    (scan / "jobs").mkdir(parents=True, exist_ok=True)
    job_files: dict[str, str] = {}
    for unit_id, (group_dir, payload) in jobs.items():
        target = scan / "jobs" / group_dir
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{unit_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        job_files[unit_id] = f"jobs/{group_dir}/{unit_id}.json"
    (scan / "manifest.json").write_text(json.dumps({"job_files": job_files}), encoding="utf-8")
    (scan / "contexts.json").write_text(json.dumps(contexts or []), encoding="utf-8")
    (scan / "routines.json").write_text(json.dumps(routines or []), encoding="utf-8")


def _two_repo_fixture(tmp_path: Path) -> tuple[Path, FactsManifest, pp.PartitionPlan]:
    units = [
        _unit("job_a1", "Ingest", f"Proj/repo_a{REPO}/REPO_A"),
        _unit("job_a2", "Ingest", f"Proj/repo_a{REPO}/REPO_A"),
        _unit("job_b1", "Export", f"Proj/repo_b{REPO}/REPO_B"),
    ]
    facts = _manifest(units)
    _write_scan(
        tmp_path,
        jobs={
            "job_a1": ("repo_a__Ingest", {"job_id": "job_a1", "components": ["tMap_1"]}),
            "job_a2": ("repo_a__Ingest", {"job_id": "job_a2", "components": ["tMap_2"]}),
            "job_b1": ("repo_b__Export", {"job_id": "job_b1", "components": ["tFileOutput_1"]}),
        },
        contexts=[
            {"name": "Default", "source_root": f"Proj/repo_a{REPO}/REPO_A", "variables": []},
            {"name": "Default", "source_root": f"Proj/repo_b{REPO}/REPO_B", "variables": []},
        ],
        routines=[{"name": "SharedUtil", "source_code": "int x = 1;", "is_system": False}],
    )
    plan = pp.plan_partitions(facts)
    return tmp_path, facts, plan


# --------------------------------------------------------------------------- #
# The core promise
# --------------------------------------------------------------------------- #

def test_repo_markers_produce_one_partition_per_repo(tmp_path):
    _, _, plan = _two_repo_fixture(tmp_path)
    assert plan.is_composite is True
    assert sorted(p.name for p in plan.partitions) == ["repo_a", "repo_b"]


def test_each_partition_gets_a_distinct_fingerprint(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    prints = compute_partition_fingerprints(root, plan, facts)
    assert set(prints) == {"repo_a", "repo_b"}
    assert all(prints.values()), "every partition must be determinable"
    assert prints["repo_a"] != prints["repo_b"]


def test_fingerprints_are_stable_across_recomputation(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    assert compute_partition_fingerprints(root, plan, facts) == compute_partition_fingerprints(
        root, plan, facts
    )


def test_editing_one_repo_leaves_the_other_fingerprint_untouched(tmp_path):
    """The whole feature in one assertion: a push to repo_b regenerates repo_b only."""
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)

    job = root / "scan" / "jobs" / "repo_b__Export" / "job_b1.json"
    payload = json.loads(job.read_text())
    payload["components"] = ["tFileOutput_1", "tLogRow_1"]
    job.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    after = compute_partition_fingerprints(root, plan, facts)
    assert after["repo_b"] != before["repo_b"]
    assert after["repo_a"] == before["repo_a"]


def test_in_file_edit_is_detected_even_though_unit_facts_are_identical(tmp_path):
    """A changed expression alters no UnitFact field.

    A fingerprint derived from the facts manifest would miss this and publish
    stale documentation, so the job artifact BYTES must be hashed.
    """
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)

    job = root / "scan" / "jobs" / "repo_a__Ingest" / "job_a1.json"
    payload = json.loads(job.read_text())
    # Same component count, same group, same path -- only an inner expression moves.
    payload["mapping_expression"] = "row1.Total * 1.21"
    job.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    after = compute_partition_fingerprints(root, plan, facts)
    assert after["repo_a"] != before["repo_a"]
    assert after["repo_b"] == before["repo_b"]


# --------------------------------------------------------------------------- #
# Scoping rules
# --------------------------------------------------------------------------- #

def test_context_change_scopes_to_its_own_repo(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)

    path = root / "scan" / "contexts.json"
    contexts = json.loads(path.read_text())
    for entry in contexts:
        if entry["source_root"].startswith(f"Proj/repo_b{REPO}"):
            entry["variables"] = [{"name": "HOST", "value": "changed"}]
    path.write_text(json.dumps(contexts), encoding="utf-8")

    after = compute_partition_fingerprints(root, plan, facts)
    assert after["repo_b"] != before["repo_b"]
    assert after["repo_a"] == before["repo_a"]


def test_context_reordering_alone_does_not_invalidate(tmp_path):
    """Scanner output order is not a semantic change."""
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)

    path = root / "scan" / "contexts.json"
    contexts = json.loads(path.read_text())
    path.write_text(json.dumps(list(reversed(contexts))), encoding="utf-8")

    assert compute_partition_fingerprints(root, plan, facts) == before


def test_shared_routine_change_invalidates_every_partition(tmp_path):
    """Routines carry no source_root, so they cannot be attributed to one repo.

    Over-invalidation is the safe direction; under-invalidating would publish
    documentation describing code that no longer exists.
    """
    assert "routines.json" in UNATTRIBUTABLE_ARTIFACTS
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)

    path = root / "scan" / "routines.json"
    path.write_text(
        json.dumps([{"name": "SharedUtil", "source_code": "int x = 2;", "is_system": False}]),
        encoding="utf-8",
    )

    after = compute_partition_fingerprints(root, plan, facts)
    assert after["repo_a"] != before["repo_a"]
    assert after["repo_b"] != before["repo_b"]


def test_root_qualified_routine_change_invalidates_only_its_partition(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    path = root / "scan" / "routines.json"
    path.write_text(json.dumps([
        {"routine_id": "a::r", "source_root": f"Proj/repo_a{REPO}/REPO_A",
         "source_code": "int x = 1;", "source_complete": True},
        {"routine_id": "b::r", "source_root": f"Proj/repo_b{REPO}/REPO_B",
         "source_code": "int y = 1;", "source_complete": True},
    ]), encoding="utf-8")
    before = compute_partition_fingerprints(root, plan, facts)
    routines = json.loads(path.read_text())
    routines[0]["source_code"] = "int x = 2;"
    path.write_text(json.dumps(routines), encoding="utf-8")
    after = compute_partition_fingerprints(root, plan, facts)
    assert after["repo_a"] != before["repo_a"]
    assert after["repo_b"] == before["repo_b"]


def test_project_wide_call_graph_change_does_not_invalidate_partitions(tmp_path):
    """manifest.json holds the project-wide graph, so any repo's change rewrites it.

    Including it would re-couple every partition. Cross-partition edges are
    carried by the upload-level cross-project index, which is recomputed on
    every run at zero model cost.
    """
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)

    path = root / "scan" / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["job_calls"] = {"job_a1": ["job_b1"]}
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert compute_partition_fingerprints(root, plan, facts) == before


def test_unit_moving_between_repos_invalidates_both_sides(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)

    moved = [
        _unit("job_a1", "Ingest", f"Proj/repo_a{REPO}/REPO_A"),
        _unit("job_a2", "Export", f"Proj/repo_b{REPO}/REPO_B"),
        _unit("job_b1", "Export", f"Proj/repo_b{REPO}/REPO_B"),
    ]
    moved_facts = _manifest(moved)
    after = compute_partition_fingerprints(root, pp.plan_partitions(moved_facts), moved_facts)
    assert after["repo_a"] != before["repo_a"]
    assert after["repo_b"] != before["repo_b"]


# --------------------------------------------------------------------------- #
# Fail-safe behaviour
# --------------------------------------------------------------------------- #

def test_unit_missing_from_job_files_forces_regeneration(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    path = root / "scan" / "manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["job_files"]["job_b1"]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    prints = compute_partition_fingerprints(root, plan, facts)
    assert prints["repo_b"] == "", "indeterminate input must never be skippable"
    assert prints["repo_a"], "a sibling stays determinable"


def test_unreadable_job_artifact_forces_regeneration(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    (root / "scan" / "jobs" / "repo_b__Export" / "job_b1.json").unlink()

    prints = compute_partition_fingerprints(root, plan, facts)
    assert prints["repo_b"] == ""
    assert prints["repo_a"]


def test_missing_routines_artifact_hashes_distinctly_from_empty(tmp_path):
    root, facts, plan = _two_repo_fixture(tmp_path)
    before = compute_partition_fingerprints(root, plan, facts)
    (root / "scan" / "routines.json").unlink()
    after = compute_partition_fingerprints(root, plan, facts)
    assert after["repo_a"] != before["repo_a"]


def test_non_composite_scan_yields_one_whole_project_fingerprint(tmp_path):
    """Single-root projects keep working: one partition, one determinable digest."""
    units = [_unit("only_job", "Global", "Proj/PLAIN")]
    facts = _manifest(units)
    _write_scan(tmp_path, jobs={"only_job": ("Global", {"job_id": "only_job"})})
    plan = pp.plan_partitions(facts)

    assert plan.is_composite is False
    prints = compute_partition_fingerprints(tmp_path, plan, facts)
    assert list(prints) == [pp.WHOLE_PROJECT_PARTITION]
    assert prints[pp.WHOLE_PROJECT_PARTITION]


def test_zip_and_repo_origins_partition_identically(tmp_path):
    """The marker generalisation must not privilege one origin over the other."""
    zip_units = [_unit("j1", "G", f"Proj/alpha{ZIP}/ALPHA")]
    repo_units = [_unit("j1", "G", f"Proj/alpha{REPO}/ALPHA")]
    zip_plan = pp.plan_partitions(_manifest(zip_units))
    repo_plan = pp.plan_partitions(_manifest(repo_units))
    assert [p.name for p in zip_plan.partitions] == [p.name for p in repo_plan.partitions] == ["alpha"]
