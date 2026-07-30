"""Integration test: `intodoc scan` writes scan/landscape.json alongside
scan/lineage.json.

Verifies the hook wired into `cli.py::scan` end to end, using the same
minimal synthetic Talend project pattern as
tests/test_lineage/test_cli_scan_lineage.py.
"""

from __future__ import annotations

import json
import hashlib

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


_SIMPLE_BODY = (
    '<node componentName="tFileInputDelimited" componentVersion="0.1" posX="0" posY="0">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tFileInputDelimited_1"/>'
    "</node>"
    '<node componentName="tFileOutputDelimited" componentVersion="0.1" posX="200" posY="100">'
    '<elementParameter field="TEXT" name="UNIQUE_NAME" value="tFileOutputDelimited_1"/>'
    "</node>"
    '<connection connectorName="FLOW" label="row1" lineStyle="0" '
    'source="tFileInputDelimited_1" target="tFileOutputDelimited_1"/>'
)


def test_scan_cli_writes_landscape_json(tmp_path):
    project = _synthetic_project(tmp_path)
    _write_job(project, "Iface", "SimpleJob", "1.0", body=_SIMPLE_BODY)

    output_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(cli, ["scan", str(tmp_path), "-p", "talend", "-o", str(output_dir)])

    assert result.exit_code == 0, result.output
    assert "Landscape saved" in result.output

    landscape_path = output_dir / "scan" / "landscape.json"
    assert landscape_path.exists()

    data = json.loads(landscape_path.read_text(encoding="utf-8"))
    assert "applications" in data and "systems" in data and "edges" in data
    assert len(data["applications"]) == 1
    assert data["applications"][0]["artifact_count"] == 1

    approval = json.loads((output_dir / "publication_approval.json").read_text(encoding="utf-8"))
    assert approval["approved"] is True
    assert approval["allow_redaction_failures"] is False
    assert set(approval["artifacts"]) == {
        "scan_result.json",
        "scan/manifest.json",
        "scan/lineage.json",
        "scan/landscape.json",
    }
    for relative_path, expected_hash in approval["artifacts"].items():
        actual = hashlib.sha256((output_dir / relative_path).read_bytes()).hexdigest()
        assert expected_hash == f"sha256:{actual}"
        assert approval["verifications"][relative_path]["passed"] is True


def test_scan_cli_with_redaction_escape_hatch_never_writes_publication_approval(tmp_path):
    project = _synthetic_project(tmp_path)
    _write_job(project, "Iface", "SimpleJob", "1.0", body=_SIMPLE_BODY)

    output_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(cli, [
        "scan", str(tmp_path), "-p", "talend", "-o", str(output_dir),
        "--allow-redaction-failures",
    ])

    assert result.exit_code == 0, result.output
    assert "Publication approval withheld" in result.output
    assert not (output_dir / "publication_approval.json").exists()
