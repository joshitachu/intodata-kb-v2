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


# ── The agent-eligible artifact tree ─────────────────────────────────
#
# The four hashes in `artifacts` bind the aggregates.  A documentation
# workspace stages none of those four directly: it stages every
# `scan/jobs/<group>/<design>.json` shard and the estate collections, so
# before the tree existed a shard could be edited after approval and nothing
# downstream could tell -- the redaction verification that makes an approval
# meaningful had run against different bytes.
#
# Both tests below recompute the tree independently, from the rule the receipt
# itself states, which is exactly what the consumer
# (`mcp_server.providers.agent_workspace.workspace._verify_approval_tree`)
# does.  Importing the scanner's own helper would only assert that the code
# agrees with itself.


def _recompute_tree(output_dir, tree):
    """Walk the receipt's stated rule and return `{relpath: sha256}`."""

    files = {}
    for root in tree["roots"]:
        base = output_dir / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(output_dir).as_posix()
            if any(relative.startswith(p) for p in tree["excluded_prefixes"]):
                continue
            files[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _tree_digest(files):
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative].encode("utf-8"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _scan_once(tmp_path):
    project = _synthetic_project(tmp_path)
    _write_job(project, "Iface", "SimpleJob", "1.0", body=_SIMPLE_BODY)
    _write_job(project, "Iface", "SecondJob", "1.0", body=_SIMPLE_BODY)
    output_dir = tmp_path / "out"
    result = CliRunner().invoke(
        cli, ["scan", str(tmp_path), "-p", "talend", "-o", str(output_dir)]
    )
    assert result.exit_code == 0, result.output
    approval = json.loads(
        (output_dir / "publication_approval.json").read_text(encoding="utf-8")
    )
    return output_dir, approval


def test_the_approval_binds_every_agent_eligible_artifact(tmp_path):
    output_dir, approval = _scan_once(tmp_path)
    tree = approval["tree"]

    assert approval["schema_version"] == 2
    assert tree["algorithm"] == "sha256-relpath-pairs-1"
    assert tree["file_count"] == len(tree["files"])
    assert list(tree["files"]) == sorted(tree["files"]), "paths must be sorted"

    # The per-design shards and the estate collections -- the bytes a
    # documentation workspace actually stages -- are covered, and the receipt
    # agrees with the tree on disk.
    covered = set(tree["files"])
    assert any(path.startswith("scan/jobs/") for path in covered)
    for collection in ("scan/contexts.json", "scan/routines.json", "scan/meta.json"):
        assert collection in covered
    assert covered == set(_recompute_tree(output_dir, tree))
    assert _tree_digest(tree["files"]) == tree["digest"]

    # The source mirror and the routine-source fragments are deliberately out:
    # neither may reach a documentation agent under any circumstance.
    assert not [p for p in covered if p.startswith(("scan/sanitized/", "scan/evidence/"))]
    assert (output_dir / "scan" / "sanitized").is_dir(), "the mirror still exists"


def test_a_job_shard_mutated_after_approval_no_longer_matches_the_receipt(tmp_path):
    """The gap this closes, stated as the consumer sees it.

    The consumer refuses to stage a byte on this disagreement
    (`test_agent_workspace/test_workspace.py::TestApprovalTree`); here we
    prove the receipt can see it at all, which it could not before.
    """

    output_dir, approval = _scan_once(tmp_path)
    tree = approval["tree"]

    shards = sorted((output_dir / "scan" / "jobs").rglob("*.json"))
    assert shards, "the scan produced no design shards"
    payload = json.loads(shards[0].read_text(encoding="utf-8"))
    payload["purpose"] = "injected after approval"
    shards[0].write_text(json.dumps(payload), encoding="utf-8")

    recomputed = _recompute_tree(output_dir, tree)
    relative = shards[0].relative_to(output_dir).as_posix()
    assert recomputed[relative] != tree["files"][relative]
    assert _tree_digest(recomputed) != tree["digest"]
