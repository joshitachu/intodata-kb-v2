import json
from unittest.mock import MagicMock

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.evidence_bundles import EvidenceBundleBuilder
from mcp_server.providers.custom.investigator import _relevance_window_text
from mcp_server.providers.custom.sql_usage import write_sql_usage_artifacts
from mcp_server.services.plugin_schema import PluginPackManifest


def test_relevance_windows_keep_late_sql():
    text = "x" * 35_000 + "\npublic class InvoiceFlatService {}\n" + (
        "string qry = @\"SELECT T1.[INVOICEID] FROM [dbo].[CUSTINVOICETRANS] T1 "
        "INNER JOIN [dbo].[CUSTTRANS] T2 ON T1.INVOICEID = T2.VOUCHER\";"
    )

    windowed = _relevance_window_text("chunk_1", text, ["InvoiceFlatService"])

    assert "FROM [dbo].[CUSTINVOICETRANS]" in windowed
    assert "T1.INVOICEID = T2.VOUCHER" in windowed


def test_sql_usage_artifact_extracts_tables_and_fields(tmp_path):
    scan_dir = tmp_path / "scan"
    staging = tmp_path / "staging"
    scan_dir.mkdir()
    staging.mkdir()
    (scan_dir / "scan_result.json").write_text(
        json.dumps({
            "jobs": [{
                "name": "Webservices.ServiceInterface",
                "group": "Webservices",
                "path": "Webservices.ServiceInterface",
                "components": [{
                    "params": [
                        {"name": "file_path", "value": "InvoiceFlatService.cs"},
                        {"name": "content", "value": (
                            "string qry = @\"SELECT [INVOICEID], T1.[ITEMID] "
                            "FROM [dbo].[CUSTINVOICETRANS] T1 "
                            "INNER JOIN [dbo].[CUSTTRANS] T2 "
                            "ON T1.INVOICEID = T2.VOUCHER "
                            "WHERE T2.TRANSTYPE = 2 "
                            "ORDER BY INVOICEID\"; "
                            "fact.OpenDbConnection(\"axprod\");"
                        )},
                    ],
                }],
            }],
        }),
        encoding="utf-8",
    )

    write_sql_usage_artifacts(scan_dir, staging)

    payload = json.loads((staging / "sql_usage.json").read_text(encoding="utf-8"))
    record = payload["records"][0]
    tables = {table["table"]: table for table in record["tables"]}
    assert record["connection_name"] == "axprod"
    assert "dbo.CUSTINVOICETRANS" in tables
    assert "dbo.CUSTTRANS" in tables
    assert "INVOICEID" in tables["dbo.CUSTINVOICETRANS"]["all_fields"]
    assert "ITEMID" in tables["dbo.CUSTINVOICETRANS"]["all_fields"]
    assert "VOUCHER" in tables["dbo.CUSTTRANS"]["join_fields"]
    assert "TRANSTYPE" in tables["dbo.CUSTTRANS"]["filter_fields"]


def test_evidence_bundle_includes_sql_usage_sections(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "sql_usage.md").write_text(
        "# SQL Usage\n\n"
        "## SQL-ABC - InvoiceFlatService\n"
        "Source: InvoiceFlatService.cs\n\n"
        "| Table | All fields |\n"
        "|-------|------------|\n"
        "| dbo.CUSTINVOICETRANS | INVOICEID, ITEMID |\n",
        encoding="utf-8",
    )
    manifest = PluginPackManifest.model_validate({
        "name": "test-pack",
        "version": "1.0.0",
        "docs": [{"name": "TABLE_INVENTORY", "evidence_profile": "p1"}],
        "evidence_profiles": {"p1": {"include": ["sql_queries"]}},
        "steps": [{"id": "run", "type": "harness"}],
    })

    result = EvidenceBundleBuilder(staging, manifest).build("p1", manifest.evidence_profiles["p1"])
    payload = json.loads((staging / result["json"]).read_text(encoding="utf-8"))

    snippets = [entry["snippet"] for entry in payload["categories"]["sql_queries"]]
    assert any("dbo.CUSTINVOICETRANS" in snippet for snippet in snippets)


def test_sql_usage_reconciliation_reclassifies_ddl_only_rows(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "sql_usage.json").write_text(
        json.dumps({
            "records": [{
                "tables": [{"table": "dbo.CUSTINVOICETRANS"}],
            }],
        }),
        encoding="utf-8",
    )
    doc = staging / "TABLE_INVENTORY.md"
    doc.write_text(
        "| Table | Usage | Classification |\n"
        "|---|---|---|\n"
        "| dbo.CUSTINVOICETRANS | DDL only | PRESENT_IN_DDL_ONLY |\n",
        encoding="utf-8",
    )
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["G"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    changed = CustomHarnessProvider(MagicMock())._reconcile_sql_usage_classification(ctx)

    assert changed == 1
    text = doc.read_text(encoding="utf-8")
    assert "PRESENT_IN_DDL_ONLY" not in text
    assert "DDL only" not in text
    assert "USED_BY_CURRENT_CODE" in text
