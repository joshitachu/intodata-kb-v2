"""Tests for ScannerService."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.services.scanner_service import ScannerService
from mcp_server.services.scan_output_lock import acquire_scan_output_lock


@pytest.fixture
def scanner(log_manager):
    return ScannerService(log_manager=log_manager)


async def test_run_scan_command_not_found(scanner, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "intodoc_command", "nonexistent_binary_xyz")

    result = await scanner.run_scan("proj", "/tmp/repo", output_dir=str(tmp_path))
    assert result["success"] is False
    assert "not found" in result["error"]


async def test_run_scan_refuses_output_directory_owned_by_another_process(scanner, tmp_path):
    output_dir = tmp_path / "output"
    lock = acquire_scan_output_lock(output_dir)
    assert lock is not None
    try:
        result = await scanner.run_scan("proj", str(tmp_path), output_dir=str(output_dir))
    finally:
        lock.release()

    assert result["success"] is False
    assert "already in use" in result["error"]


async def test_run_scan_success(scanner, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "project_root", tmp_path)

    repo = tmp_path / "repo"
    repo.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    scan_result = out_dir / "scan_result.json"
    scan_result.write_text(json.dumps({
        "jobs": [{"name": "j1", "artifact_kind": "job"}, {"name": "r1", "artifact_kind": "route"}],
        "stats": {"total_artifacts": 2, "job_count": 1, "route_count": 1},
    }))

    mock_proc = MagicMock()
    mock_proc.stdout.readline = MagicMock(return_value="")
    mock_proc.stderr.readline = MagicMock(return_value="")
    def _wait_success():
        scan_result.write_text(json.dumps({
            "jobs": [{"name": "j1", "artifact_kind": "job"}, {"name": "r1", "artifact_kind": "route"}],
            "stats": {"total_artifacts": 2, "job_count": 1, "route_count": 1},
        }))
        return 0
    mock_proc.wait.side_effect = _wait_success

    with patch("shutil.which", return_value="/usr/bin/intodoc"), \
         patch("subprocess.Popen", return_value=mock_proc):
        result = await scanner.run_scan("proj", str(repo), output_dir=str(out_dir))

    assert result["success"] is True
    assert result["job_count"] == 2
    assert result["artifact_counts"] == {
        "total_artifacts": 2, "job_count": 1, "route_count": 1,
        "joblet_count": 0, "routelet_count": 0, "empty_or_unparsed_count": 0,
    }


async def test_run_scan_rejects_zero_job_talend_result(scanner, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "project_root", tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    mock_proc = MagicMock()
    mock_proc.stdout.readline = MagicMock(return_value="")
    mock_proc.stderr.readline = MagicMock(return_value="")
    def _write_zero_result():
        (out_dir / "scan_result.json").write_text(
            json.dumps({"jobs": [], "warnings": [{"message": "No Talend project structure found"}]})
        )
        return 0
    mock_proc.wait.side_effect = _write_zero_result

    with patch("shutil.which", return_value="/usr/bin/intodoc"), patch("subprocess.Popen", return_value=mock_proc):
        result = await scanner.run_scan("proj", str(repo), platform="talend", output_dir=str(out_dir))

    assert result["success"] is False
    assert "zero jobs" in result["error"]


async def test_run_scan_clears_stale_scan_and_fails_when_new_output_missing(scanner, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "project_root", tmp_path)

    repo = tmp_path / "repo"
    repo.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "scan_result.json").write_text(json.dumps({"jobs": [{"name": "stale"}]}))
    (out_dir / "publication_approval.json").write_text(json.dumps({"approved": True}))

    mock_proc = MagicMock()
    mock_proc.stdout.readline = MagicMock(return_value="")
    mock_proc.stderr.readline = MagicMock(return_value="")
    mock_proc.wait.return_value = 0

    with patch("shutil.which", return_value="/usr/bin/intodoc"), \
         patch("subprocess.Popen", return_value=mock_proc):
        result = await scanner.run_scan("proj", str(repo), output_dir=str(out_dir))

    assert result["success"] is False
    assert "did not produce" in result["error"]
    assert not (out_dir / "publication_approval.json").exists()


async def test_run_scan_nonzero_exit(scanner, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "project_root", tmp_path)

    repo = tmp_path / "repo"
    repo.mkdir()
    out_dir = tmp_path / "output"
    out_dir.mkdir()

    mock_proc = MagicMock()
    mock_proc.stdout.readline = MagicMock(return_value="")
    mock_proc.stderr.readline = MagicMock(return_value="")
    mock_proc.wait.return_value = 1

    with patch("shutil.which", return_value="/usr/bin/intodoc"), \
         patch("subprocess.Popen", return_value=mock_proc):
        result = await scanner.run_scan("proj", str(repo), output_dir=str(out_dir))

    assert result["success"] is False


async def test_run_scan_missing_repo_path_fails_before_subprocess(scanner, monkeypatch, tmp_path):
    from mcp_server.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config, "project_root", tmp_path)
    missing_repo = tmp_path / "missing"
    out_dir = tmp_path / "output"

    with patch("shutil.which", return_value="/usr/bin/intodoc"), \
         patch("subprocess.Popen") as popen:
        result = await scanner.run_scan("proj", str(missing_repo), output_dir=str(out_dir))

    assert result["success"] is False
    assert "Project repository path does not exist" in result["error"]
    assert str(tmp_path / "data" / "repos" / "proj") in result["error"]
    popen.assert_not_called()


def test_read_scan_json_monolithic(scanner, tmp_path):
    scan = {"platform": "talend", "repo_root": "/x", "jobs": [{"name": "j", "group": "G"}]}
    (tmp_path / "scan_result.json").write_text(json.dumps(scan))

    result = scanner.read_scan_json(str(tmp_path), section="meta")
    assert result["platform"] == "talend"


def test_read_scan_json_missing(scanner, tmp_path):
    result = scanner.read_scan_json(str(tmp_path / "nonexistent"), section="all")
    assert "error" in result


def test_composite_segmented_chunk_round_trip_preserves_canonical_ids(scanner, tmp_path):
    scan = tmp_path / "scan"
    first = scan / "jobs" / "one__Jobs"
    second = scan / "jobs" / "two__Jobs"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    parent_id = "one::Parent"
    child_id = "two::Child"
    (first / "one__Parent.json").write_text(json.dumps({
        "name": "Parent", "job_id": parent_id, "source_root": "one", "group": "Jobs",
        "components": [], "raw_routines": [],
    }))
    (second / "two__Child.json").write_text(json.dumps({
        "name": "Child", "job_id": child_id, "source_root": "two", "group": "Jobs",
        "components": [], "raw_routines": [],
    }))
    manifest = {
        "job_calls": {parent_id: [child_id]},
        "job_called_by": {child_id: [parent_id]},
        "job_connections_used": {}, "connection_used_by": {},
        "job_contexts_used": {}, "routines_used_by": {},
        "job_groups": {parent_id: "one/Jobs", child_id: "two/Jobs"},
        "source_roots": [{"id": "one", "path": "one"}, {"id": "two", "path": "two"}],
        "job_source_roots": {parent_id: "one", child_id: "two"},
        "job_files": {
            parent_id: "jobs/one__Jobs/one__Parent.json",
            child_id: "jobs/two__Jobs/two__Child.json",
        },
    }
    (scan / "manifest.json").write_text(json.dumps(manifest))

    definitions = scanner.get_chunk_definitions(str(tmp_path))
    assert {name for chunk in definitions["chunks"] for name in chunk["job_names"]} == {parent_id, child_id}
    # Both jobs share the clean functional group "Jobs" even though the composite
    # scanner stored them under two escaped/hashed physical directories
    # (one__Jobs, two__Jobs). Grouping MUST key on the canonical job.group, not
    # the directory name, so the two collapse into a single "Jobs" group -- this
    # is what keeps preprocess groups in lockstep with the facts manifest, which
    # also keys on clean job.group.
    assert {chunk["group"] for chunk in definitions["chunks"]} == {"Jobs"}
    group_chunk = next(chunk for chunk in definitions["chunks"] if parent_id in chunk["job_names"])
    data = scanner.read_chunk_data(str(tmp_path), group_chunk["chunk_id"])
    # Canonical ids and per-root provenance survive the round trip with no loss.
    assert sorted(job["job_id"] for job in data["jobs"]) == [parent_id, child_id]
    assert data["manifest"]["job_calls"] == {parent_id: [child_id]}
    assert data["manifest"]["job_source_roots"] == {parent_id: "one", child_id: "two"}


def test_composite_zip_preprocess_groups_match_facts_manifest(scanner, tmp_path):
    """Regression: composite-ZIP preprocess groups must equal the facts groups.

    The Phase 2 harness contract (harness.py:1723) requires every preprocess
    group to have canonical units in the facts manifest and ``_group_units`` to
    resolve non-empty. That holds only if both derivation sites key on the same
    identity. A composite-ZIP scan escapes+hashes the physical composite path
    into the ``scan/jobs/<dir>`` name, so several physical directories map to a
    single clean functional ``job.group``. Keying chunk groups on the directory
    name (the old bug) produced a group key space disjoint from the facts
    manifest, failing the contract for every group.
    """
    from pathlib import Path

    from mcp_server.providers.custom.validators import build_facts_manifest

    scan = tmp_path / "scan"
    # Two clean functional groups (AssetCheck_Interface, MessageLogging), each
    # split across two escaped/hashed physical composite directories.
    layout = {
        "Vendor_exports_Assetcheck.__intodoc_zip-aaaa1111": [
            ("AC_GetLocation", "AssetCheck_Interface", "root_a"),
            ("AC_GetWorkOrder", "AssetCheck_Interface", "root_a"),
            ("Log_A", "MessageLogging", "root_a"),
        ],
        "Vendor_exports_ETTM.__intodoc_zip-bbbb2222": [
            ("AC_SendForm", "AssetCheck_Interface", "root_b"),
            ("Log_B", "MessageLogging", "root_b"),
        ],
    }
    job_files: dict[str, str] = {}
    for phys_dir, jobs in layout.items():
        d = scan / "jobs" / phys_dir
        d.mkdir(parents=True)
        for job_id, group, root in jobs:
            rel = f"jobs/{phys_dir}/{job_id}.json"
            job_files[job_id] = rel
            (scan / rel).write_text(json.dumps({
                "name": job_id, "job_id": job_id, "group": group,
                "source_root": root, "components": [], "raw_routines": [],
            }))
    (scan / "manifest.json").write_text(json.dumps({"job_files": job_files}))
    # build_facts_manifest reads the monolithic scan_result.json.
    all_jobs = [
        {"name": j, "job_id": j, "group": g, "source_root": r,
         "components": [], "raw_routines": []}
        for jobs in layout.values() for j, g, r in jobs
    ]
    (tmp_path / "scan_result.json").write_text(
        json.dumps({"platform": "talend", "jobs": all_jobs, "manifest": {"job_files": job_files}})
    )

    definitions = scanner.get_chunk_definitions(str(tmp_path))
    preprocess_groups = {chunk["group"] for chunk in definitions["chunks"]}

    facts = build_facts_manifest("composite_fixture", tmp_path)
    facts_groups = set(facts.groups.keys())

    # Preprocess groups are the clean functional groups, not the physical dirs.
    assert preprocess_groups == {"AssetCheck_Interface", "MessageLogging"}
    # The exact harness.py:1723 contract: every preprocess group is present in
    # the facts manifest with non-empty canonical units.
    assert preprocess_groups == facts_groups
    for group in preprocess_groups:
        units = [u.name for u in facts.units if u.group == group]
        assert units, f"_group_units empty for {group}"


def test_chunk_includes_linked_support_context_by_canonical_key(scanner, tmp_path):
    scan = tmp_path / "scan"
    jobs_dir = scan / "jobs" / "Jobs"
    jobs_dir.mkdir(parents=True)
    job_file = jobs_dir / "Consumer.json"
    job_file.write_text(json.dumps({
        "name": "Consumer",
        "job_id": "Consumer",
        "source_root": "client",
        "group": "Jobs",
        "components": [],
        "raw_routines": [],
    }))
    (scan / "contexts.json").write_text(json.dumps([{
        "name": "Runtime",
        "context_key": "shared_Runtime-123",
        "source_root": "shared",
        "source_project": "Shared Configuration",
        "source_role": "support",
        "variables": [{
            "name": "API_SECRET",
            "type": "id_String",
            "values": {"DEV": "[REDACTED]"},
        }],
    }]))
    (scan / "manifest.json").write_text(json.dumps({
        "job_calls": {},
        "job_called_by": {},
        "job_connections_used": {},
        "connection_used_by": {},
        "job_contexts_used": {"Consumer": ["shared_Runtime-123"]},
        "context_used_by": {"shared_Runtime-123": ["Consumer"]},
        "context_metadata": {
            "shared_Runtime-123": {
                "name": "Runtime",
                "source_root": "shared",
                "source_project": "Shared Configuration",
            },
        },
        "job_context_links": {
            "Consumer": [{
                "context_key": "shared_Runtime-123",
                "context_id": "_runtime",
                "resolution": "repository_id",
            }],
        },
        "job_support_roots": {"Consumer": ["shared"]},
        "job_groups": {"Consumer": "Jobs"},
        "job_source_roots": {"Consumer": "client"},
        "job_files": {"Consumer": "jobs/Jobs/Consumer.json"},
        "source_roots": [
            {"id": "client", "path": "client", "role": "execution"},
            {"id": "shared", "path": "shared", "role": "support"},
        ],
    }))

    chunk_id = scanner.get_chunk_definitions(str(tmp_path))["chunks"][0]["chunk_id"]
    data = scanner.read_chunk_data(str(tmp_path), chunk_id)

    assert [context["context_key"] for context in data["related_contexts"]] == [
        "shared_Runtime-123"
    ]
    assert data["manifest"]["context_used_by"] == {
        "shared_Runtime-123": ["Consumer"]
    }
    assert data["manifest"]["job_support_roots"] == {"Consumer": ["shared"]}


def test_get_chunk_definitions(scanner, tmp_path):
    scan = {"jobs": [
        {"name": "j1", "group": "A"},
        {"name": "j2", "group": "A"},
        {"name": "j3", "group": "B"},
    ]}
    (tmp_path / "scan_result.json").write_text(json.dumps(scan))

    result = scanner.get_chunk_definitions(str(tmp_path))
    assert result["total_chunks"] >= 2
    assert result["total_jobs"] == 3


def test_read_chunk_data(scanner, tmp_path):
    scan = {
        "jobs": [{"name": "j1", "group": "A", "components": []}],
        "manifest": {},
        "metadata_connections": [],
        "context_groups": [],
    }
    (tmp_path / "scan_result.json").write_text(json.dumps(scan))

    chunks = scanner.get_chunk_definitions(str(tmp_path))
    chunk_id = chunks["chunks"][0]["chunk_id"]

    result = scanner.read_chunk_data(str(tmp_path), chunk_id)
    assert result["job_count"] == 1
    assert result["group"] == "A"


def test_read_chunk_data_not_found(scanner, tmp_path):
    scan = {"jobs": [{"name": "j1", "group": "A"}]}
    (tmp_path / "scan_result.json").write_text(json.dumps(scan))

    result = scanner.read_chunk_data(str(tmp_path), "nonexistent_chunk")
    assert "error" in result


def _write_composite_scan(tmp_path):
    """Two zips; the shared REFERENCES job name exists in both with distinct ids."""
    scan = tmp_path / "scan"
    layout = {
        "Vendor_A.__intodoc_zip-aaaa": [
            ("A_Main-h1", "Flows", "root_a"),
            ("LogInfo-ha", "Logging", "root_a"),
        ],
        "Vendor_B.__intodoc_zip-bbbb": [
            ("B_Main-h2", "Flows", "root_b"),
            ("LogInfo-hb", "Logging", "root_b"),
        ],
    }
    job_files = {}
    display = {"LogInfo-ha": "LogInfo", "LogInfo-hb": "LogInfo"}
    for phys_dir, jobs in layout.items():
        d = scan / "jobs" / phys_dir
        d.mkdir(parents=True)
        for job_id, group, root in jobs:
            rel = f"jobs/{phys_dir}/{job_id}.json"
            job_files[job_id] = rel
            (scan / rel).write_text(json.dumps({
                "name": display.get(job_id, job_id), "job_id": job_id, "group": group,
                "source_root": root, "components": [], "raw_routines": [],
            }))
    (scan / "manifest.json").write_text(json.dumps({"job_files": job_files}))
    return job_files


def test_get_chunk_definitions_unit_allowlist_scopes_without_dropping_shared_names(scanner, tmp_path):
    _write_composite_scan(tmp_path)

    # Unscoped: all four units, groups span both zips.
    full = scanner.get_chunk_definitions(str(tmp_path))
    assert full["total_jobs"] == 4

    # Scoped to zip A: exactly its own two units, including its OWN copy of the
    # shared-name LogInfo job. Zip B's copy (same display name, same group,
    # different canonical id) is excluded, not cross-wired.
    allow_a = {"A_Main-h1", "LogInfo-ha"}
    scoped = scanner.get_chunk_definitions(str(tmp_path), unit_allowlist=allow_a)
    names = {n for c in scoped["chunks"] for n in c["job_names"]}
    assert names == allow_a
    assert scoped["total_jobs"] == 2
    # The Logging group survives with only the partition's copy.
    logging_chunks = [c for c in scoped["chunks"] if c["group"] == "Logging"]
    assert logging_chunks and logging_chunks[0]["job_names"] == ["LogInfo-ha"]


def test_read_chunk_data_unit_allowlist_round_trip(scanner, tmp_path):
    _write_composite_scan(tmp_path)
    allow_b = {"B_Main-h2", "LogInfo-hb"}
    defs = scanner.get_chunk_definitions(str(tmp_path), unit_allowlist=allow_b)
    for chunk in defs["chunks"]:
        data = scanner.read_chunk_data(
            str(tmp_path), chunk["chunk_id"], unit_allowlist=allow_b
        )
        loaded = {job["job_id"] for job in data["jobs"]}
        assert loaded <= allow_b
        assert loaded == set(chunk["job_names"])


def test_get_chunk_definitions_without_allowlist_unchanged(scanner, tmp_path):
    """No-allowlist behaviour is byte-identical to before the scoping seam."""
    _write_composite_scan(tmp_path)
    a = scanner.get_chunk_definitions(str(tmp_path))
    b = scanner.get_chunk_definitions(str(tmp_path), unit_allowlist=None)
    assert a == b
