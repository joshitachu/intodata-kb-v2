"""Tests for GET /mcp/projects/{id}/lineage/field (S4 field-lineage query API).

The golden-fixture test uses `tests/fixtures/lineage/vw_maximo2assetcheck_sendlocation_lineage.json`,
a verbatim `intodoc.lineage.build_lineage_matrix()` output run over the same
real, credential-filtered scan fixture
(`knowledge_base/intodoc/tests/fixtures/lineage/Maximo2AssetCheck_SendLocation.json`)
that `knowledge_base/intodoc/tests/test_lineage/test_golden_vw.py` pins as its
scanner-side golden expectations -- see that file for the hand-verified
values this test asserts against (9 entries, row17 -> logVariables via
tStatCatcher_1 -> tSetGlobalVar_4, all "likely" confidence).
"""

import json
import shutil
from pathlib import Path

import pytest

# Reuse the RBAC fixture (authenticated user with no assignments) for the
# denied case so denial is proven against a project that actually has data.
from tests.test_api.test_rbac import non_owner_client  # noqa: F401

PROJECT_ID = "vwlineageproj"
JOB_NAME = "Maximo2AssetCheck_SendLocation"

_FIXTURE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "lineage" / "vw_maximo2assetcheck_sendlocation_lineage.json"
)


def _write_lineage(output_dir: Path, project_id: str = PROJECT_ID) -> None:
    scan_dir = output_dir / project_id / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(_FIXTURE_PATH, scan_dir / "lineage.json")


@pytest.fixture
def lineage_fixture(tmp_path):
    """Write the real golden lineage.json under the tmp intodoc_output dir the client uses."""
    from mcp_server.config import config

    _write_lineage(config.intodoc_output_dir)
    return config.intodoc_output_dir


async def test_lineage_field_up_resolves_real_fixture(client, lineage_fixture):
    """Message column's real lineage: two source refs (message, origin) via
    Var.Message, both from row17 through tStatCatcher_1 -> tSetGlobalVar_4."""
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "logVariables", "column": "Message", "direction": "up"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["project_id"] == PROJECT_ID
    assert data["job_name"] == JOB_NAME
    assert data["start"]["table"] == "logVariables"
    assert data["start"]["column"] == "Message"
    assert data["truncated"] is False

    assert len(data["edges"]) == 2
    sources = {(e["source_table"], e["source_column"]) for e in data["edges"]}
    assert sources == {("row17", "message"), ("row17", "origin")}

    for edge in data["edges"]:
        assert edge["source_component"] == "tStatCatcher_1"
        assert edge["target_component"] == "tSetGlobalVar_4"
        assert edge["target_table"] == "logVariables"
        assert edge["target_column"] == "Message"
        assert edge["confidence"] == "likely"
        assert edge["resolved"] is True
        # Var hop recorded, final "Var.Message" passthrough last.
        assert edge["transformations"][-1]["expression"] == "Var.Message"


async def test_lineage_field_description_column_flags_dynamic_refs(client, lineage_fixture):
    """Description does not go through Var and directly references
    globalMap.get -- must surface as dynamic, never silently dropped."""
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "logVariables", "column": "Description", "direction": "up"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["edges"]) == 2
    for edge in data["edges"]:
        assert edge["dynamic_refs"] == ["globalMap.get"]
        assert edge["resolved"] is True


async def test_lineage_field_down_from_source_fans_out(client, lineage_fixture):
    """row17.message feeds all five target columns (Message, Description,
    Severity, LogCode, Values) -- verified against the golden entry set."""
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "row17", "column": "message", "direction": "down"},
    )
    assert resp.status_code == 200
    data = resp.json()
    targets = {e["target_column"] for e in data["edges"]}
    assert targets == {"Message", "Description", "Severity", "LogCode", "Values"}


async def test_lineage_field_summary_matches_golden_entry_count(client, lineage_fixture):
    # Sanity check the fixture itself: 9 total entries across all target columns.
    raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(raw["entries"]) == 9


async def test_lineage_field_no_scan_data_returns_404(client):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "logVariables", "column": "Message"},
    )
    assert resp.status_code == 404


async def test_lineage_field_unknown_field_returns_empty_not_error(client, lineage_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "NoSuchTable", "column": "nope", "direction": "both"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["edges"] == []
    assert len(data["nodes"]) == 1


async def test_lineage_field_missing_required_query_param_returns_422(client, lineage_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "logVariables"},  # column missing
    )
    assert resp.status_code == 422


async def test_lineage_field_invalid_direction_returns_422(client, lineage_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "logVariables", "column": "Message", "direction": "sideways"},
    )
    assert resp.status_code == 422


async def test_lineage_field_max_depth_out_of_range_returns_422(client, lineage_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "logVariables", "column": "Message", "max_depth": 0},
    )
    assert resp.status_code == 422


async def test_lineage_field_rbac_denied(non_owner_client):  # noqa: F811
    """A user with no assignment to the project is denied even when lineage data exists."""
    from mcp_server.config import config

    _write_lineage(config.intodoc_output_dir)
    resp = await non_owner_client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/field",
        params={"job": JOB_NAME, "table": "logVariables", "column": "Message"},
    )
    assert resp.status_code == 403
