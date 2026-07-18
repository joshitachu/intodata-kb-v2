"""Integration test: `intodoc scan` writes a credential-filtered scan/lineage.json.

Verifies the hook wired into `cli.py::scan` end to end, using a minimal
synthetic Talend project (same fixture-building pattern as
tests/test_platforms/test_talend_parser.py).
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from intodoc.cli import cli

_ITEM_XMLNS = (
    'xmlns:talendfile="platform:/resource/org.talend.model/model/TalendFile.xsd" '
    'xmlns:TalendMapper="http://www.talend.org/mapper" '
    'xmlns:xmi="http://www.omg.org/XMI" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)


def _write_job(project, rel_dir, name, version, body=""):
    job_dir = project / "process" / rel_dir
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{name}_{version}.item").write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
        f'<talendfile:ProcessType {_ITEM_XMLNS} defaultContext="DEV" jobType="Standard">'
        f"{body}</talendfile:ProcessType>",
        encoding="utf-8",
    )
    (job_dir / f"{name}_{version}.properties").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xmi:XMI xmi:version="2.0" xmlns:xmi="http://www.omg.org/XMI" '
        'xmlns:TalendProperties="http://www.talend.org/properties">'
        f'<TalendProperties:Property label="{name}" version="{version}"/>'
        "</xmi:XMI>",
        encoding="utf-8",
    )


def _synthetic_project(root):
    project = root / "SYNTH"
    (project / "process").mkdir(parents=True)
    (project / "metadata" / "connections").mkdir(parents=True)
    (project / "context").mkdir(parents=True)
    return project


# tMap wired between two other components via row/out connections, with an
# expression that embeds a fake credential inside a string literal --
# proving the CLI's lineage gate redacts it before scan/lineage.json lands
# on disk.
_TMAP_WITH_SECRET_BODY = (
    '<node componentName="tFileInputDelimited" componentVersion="0.1" posX="0" posY="0">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tFileInputDelimited_1"/>'
    "</node>"
    '<node componentName="tMap" componentVersion="0.1" posX="100" posY="100">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tMap_1"/>'
    '<nodeData xsi:type="TalendMapper:MapperData">'
    '<inputTables name="row1"/>'
    '<outputTables name="out1">'
    '<mapperTableEntries name="wonum" expression="row1.wonum" type="id_String"/>'
    '<mapperTableEntries name="note" '
    'expression="&quot;jdbc:sqlserver://admin:hunter2fake@myserver.example.com:1433/mydb&quot;'
    '.equalsIgnoreCase(row1.wonum)" type="id_String"/>'
    "</outputTables>"
    "</nodeData>"
    "</node>"
    '<node componentName="tFileOutputDelimited" componentVersion="0.1" posX="200" posY="100">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tFileOutputDelimited_1"/>'
    "</node>"
    '<connection connectorName="FLOW" label="row1" lineStyle="0" '
    'source="tFileInputDelimited_1" target="tMap_1"/>'
    '<connection connectorName="FLOW" label="out1" lineStyle="0" '
    'source="tMap_1" target="tFileOutputDelimited_1"/>'
)


def test_scan_cli_writes_credential_filtered_lineage_json(tmp_path):
    project = _synthetic_project(tmp_path)
    _write_job(project, "Iface", "MapJob", "1.0", body=_TMAP_WITH_SECRET_BODY)

    output_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(cli, ["scan", str(tmp_path), "-p", "talend", "-o", str(output_dir)])

    assert result.exit_code == 0, result.output

    lineage_path = output_dir / "scan" / "lineage.json"
    assert lineage_path.exists()

    raw = lineage_path.read_text(encoding="utf-8")
    assert "hunter2fake" not in raw
    assert "[CONNECTION_STRING_REDACTED]" in raw

    data = json.loads(raw)
    assert data["summary"]["total_entries"] >= 2
    targets = {(e["target_table"], e["target_column"]) for e in data["entries"]}
    assert ("out1", "wonum") in targets
    assert ("out1", "note") in targets

    wonum_entry = next(e for e in data["entries"] if e["target_column"] == "wonum")
    assert wonum_entry["source_component"] == "tFileInputDelimited_1"
    assert wonum_entry["target_component"] == "tFileOutputDelimited_1"
    assert wonum_entry["confidence"] == "certain"
