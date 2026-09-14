from __future__ import annotations

import hashlib
import json

from intodoc.models import ProjectManifest, Routine, ScanResult
from intodoc.scanner.credential_filter import CredentialFilter
from intodoc.scanner.evidence_store import (
    write_evidence_package,
    write_sanitized_workspace,
)


def test_routine_evidence_pages_are_lossless_and_manifest_addressable(tmp_path):
    source = "".join(f"line-{index}\n" for index in range(451))
    result = ScanResult(
        platform="talend", repo_root="/sanitized/source",
        routines=[Routine(name="Rules", routine_id="root::rules", source_root="root",
                          path="code/routines/Rules.item", version="1.0",
                          source_code=source, source_complete=True)],
        manifest=ProjectManifest(source_roots=[{"path": "root", "role": "execution"}]),
    )
    write_evidence_package(result, tmp_path)
    manifest = json.loads((tmp_path / "evidence" / "manifest.json").read_text())
    fragments = [entry for entry in manifest["fragments"] if entry["owner"] == "root::rules"]
    assert len(fragments) == 3
    rebuilt = "".join(json.loads((tmp_path / entry["artifact"]).read_text())["content"]
                      for entry in fragments)
    assert rebuilt == source
    assert fragments[-1]["line_end"] == 451
    assert result.manifest.evidence_package["path"] == "scan/evidence/manifest.json"


def test_incomplete_legacy_routine_is_not_presented_as_complete_evidence(tmp_path):
    result = ScanResult(
        platform="talend", repo_root="/sanitized/source",
        routines=[Routine(name="Legacy", source_code="preview", source_complete=False)],
    )
    write_evidence_package(result, tmp_path)
    manifest = json.loads((tmp_path / "evidence" / "manifest.json").read_text())
    assert manifest["fragments"] == []


def test_sanitized_workspace_mirrors_textual_sources_and_refuses_the_rest(tmp_path):
    """The mirror is complete for text it can prove clean, and explicit
    about everything it refuses."""
    repo = tmp_path / "repo"
    (repo / "ORDERS" / "process").mkdir(parents=True)
    job = repo / "ORDERS" / "process" / "Orders_Load_1.0.item"
    job.write_text(
        '<?xml version="1.0"?><talendfile:ProcessType>'
        '<node componentName="tDBInput">'
        '<elementParameter name="DbPassword" type="id_Password" value="Hunter2Secret"/>'
        '<elementParameter name="TABLE" value="STG_ORDERS"/>'
        "</node></talendfile:ProcessType>",
        encoding="utf-8",
    )
    (repo / "ORDERS" / "lib").mkdir(parents=True)
    (repo / "ORDERS" / "lib" / "driver.jar").write_text("binary-ish", encoding="utf-8")
    (repo / "ORDERS" / "notes.txt").write_text(
        "datasource.password=Hunter2Secret\n", encoding="utf-8",
    )

    result = ScanResult(
        platform="talend", repo_root=str(repo),
        manifest=ProjectManifest(source_roots=[{"path": "ORDERS", "role": "execution"}]),
        source_inventory=[
            {"path": "ORDERS/process/Orders_Load_1.0.item", "kind": "job",
             "outcome": "parsed", "reason": "supported_artifact_extracted"},
            {"path": "ORDERS/lib/driver.jar", "kind": "unknown",
             "outcome": "unsupported", "reason": "unrecognized_file_type"},
            {"path": "ORDERS/notes.txt", "kind": "resource", "outcome": "indexed",
             "reason": "classified_not_proof_of_parse"},
            {"path": "ORDERS/.settings/x.prefs", "kind": "unknown",
             "outcome": "excluded", "reason": "hidden_path"},
        ],
    )
    cred_filter = CredentialFilter()
    manifest = write_sanitized_workspace(result, tmp_path / "scan", cred_filter)

    written = {entry["path"]: entry for entry in manifest["files"]}
    assert list(written) == ["ORDERS/process/Orders_Load_1.0.item"]
    mirrored = (
        tmp_path / "scan" / "sanitized" / "ORDERS/process/Orders_Load_1.0.item"
    ).read_bytes()
    # The digest is of the SANITIZED bytes, so a consumer verifies what it
    # actually reads (the source digest lives in files_index.json).
    assert hashlib.sha256(mirrored).hexdigest() == written[
        "ORDERS/process/Orders_Load_1.0.item"
    ]["sha256"]
    assert b"Hunter2Secret" not in mirrored
    assert b'value="STG_ORDERS"' in mirrored

    refused = {entry["path"]: entry for entry in manifest["excluded"]}
    assert refused["ORDERS/lib/driver.jar"]["reason"] == "non_textual_extension"
    assert refused["ORDERS/.settings/x.prefs"]["reason"] == "hidden_path"
    assert refused["ORDERS/notes.txt"] == {
        "path": "ORDERS/notes.txt", "source_root": "ORDERS", "kind": "resource",
        "reason": "residual_redaction_findings",
        "categories": ["key_value_secret"],
    }
    # Categories only -- never the value, never a hash of the value.
    assert "Hunter2Secret" not in json.dumps(manifest)
    assert manifest["residual_free"] is True
    assert result.manifest.sanitized_workspace == {
        "schema_version": "1",
        "path": "scan/sanitized/manifest.json",
        "sha256": result.manifest.sanitized_workspace["sha256"],
        "file_count": "1",
        "excluded_count": "3",
        "capability_version": "talend-capability-2",
    }


def test_sensitive_attribute_names_are_redacted_in_the_sanitized_workspace(tmp_path):
    """A connection element carries the secret as an ATTRIBUTE NAME, not a name/value pair."""
    from intodoc.models import ProjectManifest, ScanResult
    from intodoc.scanner.credential_filter import CredentialFilter
    from intodoc.scanner.evidence_store import write_sanitized_workspace

    repo = tmp_path / "source"
    (repo / "metadata" / "connections").mkdir(parents=True)
    item = repo / "metadata" / "connections" / "OrdersDb_0.1.item"
    item.write_text(
        '<?xml version="1.0"?><xmi:XMI xmlns:xmi="http://www.omg.org/XMI">'
        '<connection:DatabaseConnection xmlns:connection="http://www.talend.org/metadata/connection/2010" '
        'xmi:id="_c" name="OrdersDb" DatabaseType="MSSQL" Port="1433" SID="ORDERS" '
        'Password="Sup3rSecret!" ApiKey="AKIA0000EXAMPLE" KeyColumn="order_id"/></xmi:XMI>',
        encoding="utf-8",
    )
    result = ScanResult(
        platform="talend", repo_root=str(repo),
        manifest=ProjectManifest(source_roots=[{"path": ".", "role": "execution"}]),
        source_inventory=[{"path": "metadata/connections/OrdersDb_0.1.item", "kind": "connection",
                           "outcome": "indexed", "reason": "classified"}],
    )
    scan_dir = tmp_path / "scan"
    cred_filter = CredentialFilter()
    manifest = write_sanitized_workspace(result, scan_dir, cred_filter)
    assert manifest["excluded"] == [], manifest["excluded"]
    text = (scan_dir / "sanitized" / "metadata" / "connections" / "OrdersDb_0.1.item").read_text()
    assert "Sup3rSecret" not in text and "AKIA0000EXAMPLE" not in text
    assert 'Password="[REDACTED]"' in text and 'ApiKey="[REDACTED]"' in text
    # Labels and structural identifiers are not values: they survive.
    assert 'name="OrdersDb"' in text and 'KeyColumn="order_id"' in text and 'SID="ORDERS"' in text
    # The detector is the gate that fails closed if the redaction misses a shape.
    assert cred_filter.detect_named_value_secrets('<x Password="abc" name="x"/>') == {"sensitive_attribute_secret": 1}
    assert cred_filter.detect_named_value_secrets('<x Password="[REDACTED]" name="db_password" value="[REDACTED]"/>') == {}
