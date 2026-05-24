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
    scan_result.write_text(json.dumps({"jobs": [{"name": "j1"}, {"name": "j2"}]}))

    mock_proc = MagicMock()
    mock_proc.stdout.readline = MagicMock(return_value="")
    mock_proc.stderr.readline = MagicMock(return_value="")
    def _wait_success():
        scan_result.write_text(json.dumps({"jobs": [{"name": "j1"}, {"name": "j2"}]}))
        return 0
    mock_proc.wait.side_effect = _wait_success

    with patch("shutil.which", return_value="/usr/bin/intodoc"), \
         patch("subprocess.Popen", return_value=mock_proc):
        result = await scanner.run_scan("proj", str(repo), output_dir=str(out_dir))

    assert result["success"] is True
    assert result["job_count"] == 2


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
