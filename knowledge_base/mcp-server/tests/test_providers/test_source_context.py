from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.custom.source_context import (
    classify_source_path,
    materialize_direct_redacted_source,
)


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


def test_classify_source_path_exposes_packaging_skip_semantics():
    assert classify_source_path("node_modules/pkg/index.js") == "skipped_noise"
    assert classify_source_path("image.png") == "skipped_binary"
    assert classify_source_path("Resources/qrv40_1.dat") == "skipped_static_resource"
    assert classify_source_path("Resources.Designer.cs") == "skipped_generated"
    assert classify_source_path("src/app.py") is None


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


def test_direct_source_packaging_redacts_reordered_xml_appsetting_values(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "generic",
        "jobs": [
            _scan_job(
                "Web.config",
                '<add value="plain-secret" key="DbPassword" />\n'
                '<add key="ClientSecret" value="another-secret" />\n',
                "root",
            ),
        ],
    }), encoding="utf-8")

    materialize_direct_redacted_source(
        scan_output_dir=scan_dir,
        preprocess_dir=tmp_path / "_preprocess",
    )

    bundle = (tmp_path / "_preprocess" / "root.md").read_text(encoding="utf-8")
    assert "plain-secret" not in bundle
    assert "another-secret" not in bundle
    assert bundle.count("[REDACTED]") >= 2


def test_talend_source_packaging_uses_structured_params(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "talend",
        "jobs": [{
            "name": "LoadCustomers",
            "path": "Customers/LoadCustomers.item",
            "group": "Customers",
            "components": [{
                "unique_name": "tMSSqlInput_1",
                "component_type": "tMSSqlInput",
                "params": [
                    {"name": "QUERY", "value": "SELECT * FROM Customer"},
                    {"name": "TABLE_NAME", "value": "Customer"},
                ],
            }],
            "trunjob_refs": [{"child_job": "ChildJob"}],
        }],
        "metadata_connections": [{"name": "DWH", "connection_type": "MSSQL", "host": "sql01"}],
    }), encoding="utf-8")

    groups, _ = materialize_direct_redacted_source(
        scan_output_dir=scan_dir,
        preprocess_dir=tmp_path / "_preprocess",
    )

    bundle = (tmp_path / "_preprocess" / "Customers.md").read_text(encoding="utf-8")
    assert groups == ["Customers"]
    assert "tMSSqlInput tMSSqlInput_1" in bundle
    assert "SELECT * FROM Customer" in bundle
    assert "ChildJob" in bundle


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


def test_direct_source_packaging_skips_static_and_generated_resources(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "generic",
        "jobs": [
            _scan_job("Webservices.Documents/Barcode/Resources/qrv40_1.dat", "binary-ish payload", "docs"),
            _scan_job("Webservices.Documents/Barcode/Resources.Designer.cs", "public class Resources {}", "docs"),
            _scan_job("Webservices.Documents/Barcode/Barcode Classes.cd", "<ClassDiagram />", "docs"),
        ],
    }), encoding="utf-8")

    _, coverage = materialize_direct_redacted_source(
        scan_output_dir=scan_dir,
        preprocess_dir=tmp_path / "_preprocess",
    )

    statuses = {record["path"]: record["status"] for record in coverage["files"]}
    assert statuses["Webservices.Documents/Barcode/Resources/qrv40_1.dat"] == "skipped_static_resource"
    assert statuses["Webservices.Documents/Barcode/Resources.Designer.cs"] == "skipped_generated"
    assert statuses["Webservices.Documents/Barcode/Barcode Classes.cd"] == "skipped_generated"
    assert coverage["summary"]["skipped_static_resource"] == 1
    assert coverage["summary"]["skipped_generated"] == 2


def test_direct_source_packaging_summarizes_low_relevance_vendor_code_but_keeps_db_code(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "generic",
        "jobs": [
            _scan_job(
                "Webservices.Documents/Barcode/CodeQr.cs",
                "namespace Webservices.Documents.Barcode { public class CodeQr { public void Draw() {} } }",
                "docs",
            ),
            _scan_job(
                "Webservices.Documents/Barcode/Repository.cs",
                "public class Repository { public void Load(){ var sql = \"SELECT * FROM Customers\"; } }",
                "docs",
            ),
        ],
    }), encoding="utf-8")

    _, coverage = materialize_direct_redacted_source(
        scan_output_dir=scan_dir,
        preprocess_dir=tmp_path / "_preprocess",
    )

    by_path = {record["path"]: record for record in coverage["files"]}
    assert by_path["Webservices.Documents/Barcode/CodeQr.cs"]["status"] == "summarized_low_relevance"
    assert by_path["Webservices.Documents/Barcode/Repository.cs"]["status"] == "included_full"
    bundle = (tmp_path / "_preprocess" / "docs.md").read_text(encoding="utf-8")
    assert "Full file content omitted from LLM context" in bundle
    assert "SELECT * FROM Customers" in bundle
