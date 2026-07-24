"""Tests for ScannerService."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.services.scanner_service import ScannerService


@pytest.fixture
def scanner(log_manager):
    return ScannerService(log_manager=log_manager)


async def test_run_scan_command_not_found(scanner, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "intodoc_command", "nonexistent_binary_xyz")

    result = await scanner.run_scan("proj", "/tmp/repo", output_dir=str(tmp_path))
    assert result["success"] is False
    assert "not found" in result["error"]


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

    mock_proc = MagicMock()
    mock_proc.stdout.readline = MagicMock(return_value="")
    mock_proc.stderr.readline = MagicMock(return_value="")
    mock_proc.wait.return_value = 0

    with patch("shutil.which", return_value="/usr/bin/intodoc"), \
         patch("subprocess.Popen", return_value=mock_proc):
        result = await scanner.run_scan("proj", str(repo), output_dir=str(out_dir))

    assert result["success"] is False
    assert "did not produce" in result["error"]


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
    parent_chunk = next(chunk for chunk in definitions["chunks"] if parent_id in chunk["job_names"])
    data = scanner.read_chunk_data(str(tmp_path), parent_chunk["chunk_id"])
    assert [job["job_id"] for job in data["jobs"]] == [parent_id]
    assert data["manifest"]["job_calls"] == {parent_id: [child_id]}
    assert data["manifest"]["job_source_roots"] == {parent_id: "one"}


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
