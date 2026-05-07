from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.custom.source_context import materialize_direct_redacted_source


def _scan_job(path: str, content: str, group: str = "src") -> dict:
    return {
        "name": Path(path).stem,
        "path": path,
        "group": group,
        "components": [{
            "unique_name": Path(path).stem,
            "component_type": "CodeFile",
            "params": [
                {"name": "file_path", "value": path},
                {"name": "content", "value": content},
            ],
        }],
    }


def test_direct_source_packaging_preserves_redacted_content_and_coverage(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "generic",
        "jobs": [
            _scan_job("Web.config", "<add key=\"ApiKey\" value=\"EXAMPLE_VALUE\" />\n", "root"),
            _scan_job("src/app.py", "TOKEN = EXAMPLE_VALUE\nprint('hello')\n", "src"),
        ],
    }), encoding="utf-8")
    preprocess = tmp_path / "_preprocess"

    groups, coverage = materialize_direct_redacted_source(
        scan_output_dir=scan_dir,
        preprocess_dir=preprocess,
    )

    assert groups == ["root", "src"]
    root_bundle = (preprocess / "root.md").read_text(encoding="utf-8")
    src_bundle = (preprocess / "src.md").read_text(encoding="utf-8")
    assert "## File: Web.config" in root_bundle
    assert "## File: src/app.py" in src_bundle
    assert "EXAMPLE_VALUE" not in root_bundle
    assert "EXAMPLE_VALUE" not in src_bundle
    assert "[REDACTED]" in root_bundle
    assert "[REDACTED]" in src_bundle
    assert coverage["summary"]["total_files"] == 2
    assert coverage["summary"]["included_full"] == 2
    assert (preprocess / "_source_coverage.json").exists()
    assert (preprocess / "_source_context" / "coverage.json").exists()


def test_direct_source_packaging_chunks_oversized_files(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    content = "line\n" * 100
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "generic",
        "jobs": [_scan_job("src/large.txt", content, "src")],
    }), encoding="utf-8")

    groups, coverage = materialize_direct_redacted_source(
        scan_output_dir=scan_dir,
        preprocess_dir=tmp_path / "_preprocess",
        inline_max_bytes=50,
        chunk_bytes=40,
    )

    assert groups == ["src"]
    record = coverage["files"][0]
    assert record["status"] == "included_chunked"
    assert record["chunk_paths"]
    assert coverage["summary"]["retrievable"] == 1


def test_direct_source_packaging_records_skipped_noise(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "generic",
        "jobs": [_scan_job("node_modules/pkg/index.js", "console.log('noise')", "node_modules")],
    }), encoding="utf-8")

    _, coverage = materialize_direct_redacted_source(
        scan_output_dir=scan_dir,
        preprocess_dir=tmp_path / "_preprocess",
    )

    assert coverage["files"][0]["status"] == "skipped_noise"
    assert coverage["summary"]["skipped_noise"] == 1
