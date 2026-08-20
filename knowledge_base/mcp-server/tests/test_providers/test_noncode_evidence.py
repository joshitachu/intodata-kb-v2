"""Tests for the Tier-3 non-code documented-context lane (legacy pipeline).

All fixtures are synthetic; no client data. They mirror the shapes the real
walk sees: a source tree with a code subtree the scanner already owns, an
OOXML workbook, an XSLT mapping, business notes, and un-/pre-extracted ZIPs.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from mcp_server.providers.custom import noncode_evidence as nc
from mcp_server.providers.custom.target_evidence import TargetEvidenceBuilder


# ── helpers ──────────────────────────────────────────────────────────────────


def _xlsx_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    """Build a minimal valid .xlsx whose cells use the shared-string table."""
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(value: str) -> int:
        if value not in index:
            index[value] = len(strings)
            strings.append(value)
        return index[value]

    matrix = [headers, *rows]
    sheet_rows = []
    for r, record in enumerate(matrix, start=1):
        cells = "".join(
            f'<c r="A{r}" t="s"><v>{sid(v)}</v></c>' for v in record
        )
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0"?><worksheet '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    shared_xml = (
        '<?xml version="1.0"?><sst '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in strings)
        + "</sst>"
    )
    content_types = (
        '<?xml version="1.0"?><Types '
        'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        "</Types>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("xl/sharedStrings.xml", shared_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


def _plain_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


# ── probes ─────────────────────────────────────────────────────────────────


def test_ooxml_probe_resolves_shared_strings_headers_and_values():
    raw = _xlsx_bytes(
        ["WorkOrderId", "AssetTag", "Status"],
        [["1001", "PUMP-7", "OPEN"], ["1002", "VALVE-3", "CLOSED"]],
    )
    out = nc.OfficeOpenXmlProbe().extract(raw, max_bytes=64 * 1024)
    assert out is not None
    text = out.decode("utf-8")
    # Column headers AND row values resolve, not opaque <v> indices.
    assert "WorkOrderId | AssetTag | Status" in text
    assert "1001 | PUMP-7 | OPEN" in text
    assert "VALVE-3" in text


def test_utf_text_probe_accepts_text_rejects_binary():
    probe = nc.UtfTextProbe()
    assert probe.extract(b"hello\r\nworld", max_bytes=1024) == b"hello\nworld"
    assert probe.extract(b"\x00\x01\x02binary", max_bytes=1024) is None


def test_redaction_strips_credentials_and_is_idempotent():
    text = 'user=admin\npassword=Sup3rSecret!\nAuthorization: Bearer abcdef123456789'
    safe, count = nc.redact_text(text)
    assert count >= 2
    assert "Sup3rSecret" not in safe
    assert "abcdef123456789" not in safe
    assert nc.REDACTED_CREDENTIAL in safe
    assert "user=admin" in safe  # key names and non-credential params survive
    # Idempotent: re-redacting produces identical text.
    again, _ = nc.redact_text(safe)
    assert again == safe


# ── credential redaction: tabular columns and inline parameters ──────────────
#
# Run-74 leak class: an Excel export with a credential-named column rendered
# as delimiter rows, and Talend/Camel `password=RAW(...)` parameters embedded
# in URI/param strings, both sailed through into published docs.


def test_credential_column_values_redacted_header_preserved():
    text = (
        "server | ftp_user | ftp_pw | directory\n"
        "transfer.example.nl | svc-account | S3cr3t!Pass | /data/in\n"
        "backup.example.nl | svc-account2 | 0therS3cret | /data/out"
    )
    safe, count = nc.redact_text(text)
    assert count >= 2
    assert "S3cr3t!Pass" not in safe
    assert "0therS3cret" not in safe
    assert safe.count(nc.REDACTED_CREDENTIAL) == 2
    # Header row survives so docs can say "password column present (redacted)".
    assert "ftp_pw" in safe
    # Non-credential columns are untouched.
    assert "transfer.example.nl" in safe
    assert "svc-account" in safe
    assert "/data/in" in safe


def test_wachtwoord_column_redacted_semicolon_delimiter():
    text = "gebruiker;wachtwoord;omgeving\npiet;GeheimW8w;prod"
    safe, _ = nc.redact_text(text)
    assert "GeheimW8w" not in safe
    assert "wachtwoord" in safe  # header preserved
    assert "gebruiker" in safe and "prod" in safe


def test_xlsx_password_column_redacted_end_to_end(tmp_path: Path):
    # xlsx probe renders pipe-joined rows; the walk must redact the column.
    raw = _xlsx_bytes(
        ["host", "user", "password"],
        [["ftp01", "svc-intf", "TopS3cret!x"]],
    )
    (tmp_path / "esb_ftp.xlsx").write_bytes(raw)
    result = nc.NonCodeEvidenceExtractor(tmp_path).extract()
    item = next(i for i in result.items if i.relative_path == "esb_ftp.xlsx")
    assert "TopS3cret" not in item.text
    assert "password" in item.text  # header preserved
    assert nc.REDACTED_CREDENTIAL in item.text
    assert item.redactions >= 1


def test_talend_raw_password_redacted_preserving_wrapper():
    text = (
        "ftp://host/dir?user=sa-talend&password=RAW(P@ss w0rd!X)&passiveMode=true"
    )
    safe, count = nc.redact_text(text)
    assert count >= 1
    assert "P@ss w0rd!X" not in safe
    # Key name AND the RAW() wrapper structure are preserved.
    assert "password=RAW([REDACTED-CREDENTIAL])" in safe
    assert "user=sa-talend" in safe
    assert "passiveMode=true" in safe
    again, _ = nc.redact_text(safe)
    assert again == safe  # idempotent


def test_inline_credentials_in_connection_strings_and_urls():
    text = (
        "Server=db1;Database=app;User Id=svc;Pwd=Hunter2!x;Encrypt=true\n"
        "https://api.example.com/v1?client_id=abc&token=tok_9f8e7d6c&size=10\n"
        "header X-API-Key: 9f8e7d6c5b4a\n"
        "props: host=h1, secret=s3cr3tv4l, timeout=30"
    )
    safe, _ = nc.redact_text(text)
    assert "Hunter2!x" not in safe
    assert "tok_9f8e7d6c" not in safe
    assert "9f8e7d6c5b4a" not in safe
    assert "s3cr3tv4l" not in safe
    # Key names and surrounding structure preserved; non-credential params kept.
    assert "Pwd=" in safe and "Server=db1" in safe and "Database=app" in safe
    assert "client_id=abc" in safe and "size=10" in safe
    assert "timeout=30" in safe


def test_inline_raw_redaction_applies_to_all_text_artifact_types(tmp_path: Path):
    body = "endpoint ftp://h/in?password=RAW(Sup3r Secret!)&binary=true"
    (tmp_path / "map.xslt").write_text(f"<xsl:stylesheet><!-- {body} --></xsl:stylesheet>", encoding="utf-8")
    (tmp_path / "sample.xml").write_text(f"<cfg uri='{body}'/>", encoding="utf-8")
    (tmp_path / "note.txt").write_text(body, encoding="utf-8")
    (tmp_path / "export.csv").write_text(f"name,uri\nftp-in,{body}", encoding="utf-8")
    result = nc.NonCodeEvidenceExtractor(tmp_path).extract()
    assert len(result.items) == 4
    for item in result.items:
        assert "Sup3r Secret" not in item.text, item.relative_path
        assert "password=RAW([REDACTED-CREDENTIAL])" in item.text, item.relative_path


def test_shared_string_pool_never_emitted_flat():
    # Run-74 leak shape: a shared-string pool dumped as ONE space-joined line
    # (headers and cell values mixed) has no recoverable column structure, so
    # no value redactor can safely act on it. The probe must never emit it --
    # even when no worksheet is detected in the package.
    shared_xml = (
        '<?xml version="1.0"?><sst '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>ftp_pw</t></si><si><t>S3cr3t!777</t></si></sst>"
    )
    workbook_xml = '<?xml version="1.0"?><workbook><sheets/></workbook>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/sharedStrings.xml", shared_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)  # no worksheets member
    out = nc.OfficeOpenXmlProbe().extract(buf.getvalue(), max_bytes=64 * 1024)
    assert out is None or b"S3cr3t!777" not in out


def test_non_credential_content_untouched():
    text = (
        "WorkOrderId | AssetTag | Status\n"
        "1001 | PUMP-7 | OPEN\n"
        "See https://example.com/path?order=123&limit=5 for details.\n"
        "user=admin host=db1 port=1433\n"
        "Primary Key | Pass/Fail\n"
        "id | pass"
    )
    safe, count = nc.redact_text(text)
    assert safe == text
    assert count == 0


# ── code-tree exclusion derivation ───────────────────────────────────────────


def test_derive_code_exclusions_covers_extraction_subtree_and_raw_zip():
    scan_files = [
        {"relative_path": "Proj/Talend exports/App.__intodoc_zip__/APP/process/Job_0.1.item"},
        {"relative_path": "Proj/Talend exports/App.__intodoc_zip__/APP/contexts/Ctx.item"},
        {"relative_path": "Proj/plain/process/Job2_0.1.item"},  # non-composite scan file
    ]
    exact, prefixes = nc.derive_code_exclusions(scan_files)
    assert "Proj/Talend exports/App.__intodoc_zip__/APP/process/Job_0.1.item" in exact
    # whole extraction subtree AND its raw zip are excluded
    assert "Proj/Talend exports/App.__intodoc_zip__" in prefixes
    assert "Proj/Talend exports/App.zip" in prefixes
    # a non-composite scan file has no marker, so only the exact path excludes it
    assert "Proj/plain/process/Job2_0.1.item" in exact

    is_excl = lambda r: nc._is_excluded(r, exact, prefixes)
    assert is_excl("Proj/Talend exports/App.__intodoc_zip__/APP/anything.txt")
    assert is_excl("Proj/Talend exports/App.zip")
    assert not is_excl("Proj/Mappings/thing.xslt")


# ── extractor: walk, zip handling, no silent drops ────────────────────────────


def _build_source_tree(root: Path) -> None:
    # code subtree (scanner-owned) -- must be skipped
    code = root / "Talend exports" / "App.__intodoc_zip__" / "APP" / "process"
    code.mkdir(parents=True)
    (code / "Job_0.1.item").write_text("<talend job/>", encoding="utf-8")
    (root / "Talend exports" / "App.zip").write_bytes(_plain_zip({"APP/x": b"code"}))

    # non-code: xslt mapping mentioning distinctive identifiers
    maps = root / "Mappings"
    maps.mkdir()
    (maps / "workorder_map.xslt").write_text(
        '<xsl:stylesheet><!-- maps WorkOrderId and AssetTag -->'
        "<template>WorkOrderId AssetTag</template></xsl:stylesheet>",
        encoding="utf-8",
    )
    # a business note carrying a credential to prove redaction on the walk
    notes = root / "Notes"
    notes.mkdir()
    (notes / "readme.txt").write_text(
        "WorkOrder sync notes. password=Hunter2Secret\nAssetTag feed", encoding="utf-8"
    )


def test_extractor_skips_code_ingests_noncode_and_redacts(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    _build_source_tree(root)
    scan_files = [
        {"relative_path": "Talend exports/App.__intodoc_zip__/APP/process/Job_0.1.item"},
    ]
    exact, prefixes = nc.derive_code_exclusions(scan_files)
    result = nc.NonCodeEvidenceExtractor(
        root, exact_exclusions=exact, exclusion_prefixes=prefixes
    ).extract()
    rels = {i.relative_path for i in result.items}
    assert "Mappings/workorder_map.xslt" in rels
    assert "Notes/readme.txt" in rels
    # code subtree + raw code zip are excluded, never ingested
    assert not any("Talend exports" in r for r in rels)
    # credential in the note is redacted before it leaves the module
    note = next(i for i in result.items if i.relative_path == "Notes/readme.txt")
    assert "Hunter2Secret" not in note.text
    assert nc.REDACTED_CREDENTIAL in note.text


def test_zip_pre_extracted_sibling_skips_recursion(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "Data.__intodoc_zip__" / "inner").mkdir(parents=True)
    (root / "Data.__intodoc_zip__" / "inner" / "sheet.txt").write_text(
        "WorkOrderId AssetTag content", encoding="utf-8"
    )
    (root / "Data.zip").write_bytes(_plain_zip({"inner/sheet.txt": b"dup"}))
    result = nc.NonCodeEvidenceExtractor(root).extract()
    rels = {i.relative_path for i in result.items}
    # extracted file is walked; raw zip is recorded as pre-extracted (not recursed)
    assert "Data.__intodoc_zip__/inner/sheet.txt" in rels
    assert not any("!/" in r for r in rels)  # no zip-recursion members
    assert any(s["status"] == nc.STATUS_ZIP_PRE_EXTRACTED for s in result.skipped)


def test_zip_recursion_safety_net_ingests_when_not_pre_extracted(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    xlsx = _xlsx_bytes(["WorkOrderId", "AssetTag"], [["9", "X"]])
    (root / "bundle.zip").write_bytes(
        _plain_zip({"schema.xlsx": xlsx, "notes.txt": b"WorkOrderId note"})
    )
    result = nc.NonCodeEvidenceExtractor(root).extract()
    rels = {i.relative_path for i in result.items}
    # no pre-extraction sibling -> recurse the zip (OOXML-safe), members ingested
    assert any(r.startswith("bundle.zip!/") for r in rels)
    assert "bundle.zip!/schema.xlsx" in rels


# ── association precision ─────────────────────────────────────────────────────


def _unit(name: str, table: str, extra: str = "") -> dict:
    return {
        "name": name,
        "group": "G",
        "components": [
            {
                "unique_name": "tDBInput_1",
                "component_type": "tDBInput",
                "params": [{"name": "QUERY", "value": f"SELECT * FROM {table} {extra}"}],
            }
        ],
    }


def test_redaction_token_never_drives_association():
    # Every unit carries a redacted value; every item mentions [REDACTED].
    units = {
        f"Job_{i}": {
            "name": f"Job_{i}",
            "group": "G",
            "context_groups": [
                {"name": "ctx", "variables": [{"name": "pw", "values": {"Prod": "[REDACTED]"}}]}
            ],
        }
        for i in range(10)
    }
    items = [
        nc.PreparedNonCodeItem(
            evidence_id="only-redacted",
            relative_path="Notes/a.txt",
            probe_id="utf-text",
            text="password=[REDACTED] token=[REDACTED]",
            size_bytes=1,
            redactions=2,
        )
    ]
    plan = nc.build_noncode_association_plan(items, units)
    # "redacted" is excluded, so this item matches no unit and is project-only.
    assert plan.unit_refs == {}
    assert "only-redacted" in plan.project_only_ids


def test_min_overlap_and_df_threshold_tighten_association():
    # 20 units all reference GENERIC_TABLE; only 2 also reference WORKORDER_TABLE.
    units = {}
    for i in range(20):
        units[f"Job_{i}"] = _unit(f"Job_{i}", "GENERIC_TABLE")
    units["Job_wo_a"] = _unit("Job_wo_a", "WORKORDER_TABLE", "JOIN ASSET_TABLE")
    units["Job_wo_b"] = _unit("Job_wo_b", "WORKORDER_TABLE", "JOIN ASSET_TABLE")

    item = nc.PreparedNonCodeItem(
        evidence_id="wo-map",
        relative_path="Mappings/wo.xslt",
        probe_id="utf-text",
        text="WORKORDER_TABLE ASSET_TABLE GENERIC_TABLE",
        size_bytes=1,
        redactions=0,
    )
    # min_overlap=2: GENERIC_TABLE alone (1 token) does not associate; the two
    # workorder jobs share two distinctive tokens and do.
    plan = nc.build_noncode_association_plan(
        [item], units, unit_token_df_threshold=0.5, min_distinctive_overlap=2
    )
    matched = {u for u, refs in plan.unit_refs.items() if "wo-map" in refs}
    assert matched == {"Job_wo_a", "Job_wo_b"}


def test_unmatched_items_surface_at_project_tier():
    units = {"Job_1": _unit("Job_1", "SOME_TABLE")}
    item = nc.PreparedNonCodeItem(
        evidence_id="orphan",
        relative_path="Notes/unrelated.txt",
        probe_id="utf-text",
        text="entirely unrelated distinctive vocabulary zzztopic qqxmarker",
        size_bytes=1,
        redactions=0,
    )
    plan = nc.build_noncode_association_plan([item], units)
    assert "orphan" in plan.project_only_ids
    section = plan.project_section(max_bytes=10_000)
    assert section is not None
    assert "orphan" in section  # full set surfaced regardless of association


# ── Tier-3 attachment to the target-evidence bundle ──────────────────────────


def _write_scan(scan_dir: Path) -> None:
    import json

    jobs_dir = scan_dir / "scan" / "jobs" / "G"
    jobs_dir.mkdir(parents=True)
    job = {
        "name": "Job_WO",
        "job_id": "Job_WO",
        "group": "G",
        "path": "G/Job_WO_0.1.item",
        "components": [
            {
                "unique_name": "tJavaRow_1",
                "component_type": "tJavaRow",
                "code": 'globalMap.put("WORKORDER_TABLE", row.id);',
                "params": [],
            },
            {
                "unique_name": "tDBInput_1",
                "component_type": "tDBInput",
                "params": [{"name": "QUERY", "value": "SELECT id FROM WORKORDER_TABLE"}],
            },
        ],
    }
    (jobs_dir / "Job_WO.json").write_text(json.dumps(job), encoding="utf-8")


def test_tier3_section_appended_after_verbatim_and_within_budget(tmp_path: Path):
    scan_dir = tmp_path / "scan_out"
    scan_dir.mkdir()
    _write_scan(scan_dir)
    staging = tmp_path / "staging"
    staging.mkdir()

    item = nc.PreparedNonCodeItem(
        evidence_id="wo-doc",
        relative_path="Mappings/wo.xslt",
        probe_id="utf-text",
        text="WORKORDER_TABLE documented field mapping intent",
        size_bytes=1,
        redactions=0,
    )
    units = {
        "Job_WO": {
            "name": "Job_WO",
            "group": "G",
            "components": [
                {
                    "unique_name": "tDBInput_1",
                    "component_type": "tDBInput",
                    "params": [{"name": "QUERY", "value": "SELECT id FROM WORKORDER_TABLE"}],
                }
            ],
        }
    }
    plan = nc.build_noncode_association_plan(
        [item], units, unit_token_df_threshold=1.0, min_distinctive_overlap=1
    )
    builder = TargetEvidenceBuilder(
        scan_output_dir=scan_dir,
        staging_dir=staging,
        max_bytes=120_000,
        noncode_plan=plan,
        noncode_section_bytes=40_000,
    )
    ev = builder.build("01_UNIT__Job_WO", "Job_WO")
    assert ev is not None
    text = ev.path.read_text(encoding="utf-8")
    assert "## CUSTOM CODE (VERBATIM)" in text
    assert nc.NONCODE_SECTION_HEADING in text
    # Verbatim code precedes the additive Tier-3 section.
    assert text.index("## CUSTOM CODE (VERBATIM)") < text.index(nc.NONCODE_SECTION_HEADING)
    assert 'globalMap.put("WORKORDER_TABLE"' in text


def test_tier3_dropped_before_verbatim_when_over_budget(tmp_path: Path):
    scan_dir = tmp_path / "scan_out"
    scan_dir.mkdir()
    _write_scan(scan_dir)
    staging = tmp_path / "staging"
    staging.mkdir()
    item = nc.PreparedNonCodeItem(
        evidence_id="big",
        relative_path="Mappings/wo.xslt",
        probe_id="utf-text",
        text="WORKORDER_TABLE " + "context " * 5000,
        size_bytes=1,
        redactions=0,
    )
    units = {
        "Job_WO": {
            "name": "Job_WO",
            "group": "G",
            "components": [
                {"unique_name": "tDBInput_1", "component_type": "tDBInput",
                 "params": [{"name": "QUERY", "value": "SELECT id FROM WORKORDER_TABLE"}]}
            ],
        }
    }
    plan = nc.build_noncode_association_plan(
        [item], units, unit_token_df_threshold=1.0, min_distinctive_overlap=1
    )
    # Tiny overall budget: verbatim code must survive, Tier-3 must yield first.
    builder = TargetEvidenceBuilder(
        scan_output_dir=scan_dir,
        staging_dir=staging,
        max_bytes=600,
        noncode_plan=plan,
        noncode_section_bytes=40_000,
    )
    ev = builder.build("01_UNIT__Job_WO", "Job_WO")
    text = ev.path.read_text(encoding="utf-8")
    assert 'globalMap.put("WORKORDER_TABLE"' in text  # verbatim retained
    assert nc.NONCODE_SECTION_HEADING not in text  # additive context dropped
