"""Tests for GET /mcp/projects/{id}/jobs/{job_name}/components/{unique_name}/graph
(Level-3 tMap column graph).

Reuses the same scan-fixture shape as test_job_graph.py: per-component
incoming/outgoing_connections are empty while the job-level connections
array is populated, and tmap_data entries carry no component name.
"""

import json

import pytest

# Reuse the RBAC fixture (authenticated user with no assignments) for the
# denied case so denial is proven against a project that actually has scan data.
from tests.test_api.test_rbac import non_owner_client  # noqa: F401

PROJECT_ID = "componentgraphproj"
GROUP = "Item"
JOB_NAME = "sjItem_OffRamp"
TMAP_NAME = "tMap_1"

JOB_SCAN = {
    "name": JOB_NAME,
    "group": GROUP,
    "purpose": "Test job",
    "components": [
        {
            "unique_name": "tFileInputDelimited_1",
            "component_type": "tFileInputDelimited",
            "metadata_connection_ref": "",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [],
        },
        {
            "unique_name": TMAP_NAME,
            "component_type": "tMap",
            "metadata_connection_ref": "",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [],
        },
        {
            "unique_name": "tDBOutput_1",
            "component_type": "tMSSqlOutput",
            "metadata_connection_ref": "Demo_MSSQL_Output",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [],
        },
        {
            "unique_name": "tLogRow_1",
            "component_type": "tLogRow",
            "metadata_connection_ref": "",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [],
        },
    ],
    "connections": [
        {"name": "row1", "source": "tFileInputDelimited_1", "target": TMAP_NAME,
         "connector_name": "FLOW", "line_style": 0},
        {"name": "out1", "source": TMAP_NAME, "target": "tDBOutput_1",
         "connector_name": "FLOW", "line_style": 0},
        {"name": "row2", "source": TMAP_NAME, "target": "tLogRow_1",
         "connector_name": "FLOW", "line_style": 0},
    ],
    "tmap_data": [
        {
            "input_tables": ["row1"],
            "output_tables": ["out1", "row2"],
            "entries": [
                {"table_name": "out1", "column_name": "id", "expression": "row1.id"},
                {"table_name": "out1", "column_name": "message", "expression": "Var.Message"},
                {"table_name": "row2", "column_name": "id", "expression": "row1.id"},
            ],
            "var_entries": [
                {"table_name": "Var", "column_name": "Message",
                 "expression": "row1.message.toUpperCase()"},
            ],
        },
    ],
    "trunjob_refs": [],
}


def _write_scan(intodoc_output_dir, project_id: str = PROJECT_ID) -> None:
    scan_dir = intodoc_output_dir / project_id / "scan"
    (scan_dir / "jobs" / GROUP).mkdir(parents=True, exist_ok=True)
    (scan_dir / "manifest.json").write_text(
        json.dumps({"job_groups": {JOB_NAME: GROUP}}), encoding="utf-8"
    )
    (scan_dir / "jobs" / GROUP / f"{JOB_NAME}.json").write_text(
        json.dumps(JOB_SCAN), encoding="utf-8"
    )


@pytest.fixture
def scan_fixture(tmp_path):
    """Write the scan cache under the tmp intodoc_output dir the client uses."""
    from mcp_server.config import config

    _write_scan(config.intodoc_output_dir)
    return config.intodoc_output_dir


def _url(component: str, project_id: str = PROJECT_ID, job_name: str = JOB_NAME) -> str:
    return f"/mcp/projects/{project_id}/jobs/{job_name}/components/{component}/graph"


async def test_tmap_component_graph_tables_and_columns(client, scan_fixture):
    resp = await client.get(_url(TMAP_NAME))
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["project_id"] == PROJECT_ID
    assert data["job_name"] == JOB_NAME
    assert data["component_unique_name"] == TMAP_NAME

    # Input table row name resolved to its upstream component via the job's
    # connections array (not incoming/outgoing_connections).
    assert len(data["input_tables"]) == 1
    row1 = data["input_tables"][0]
    assert row1["table_name"] == "row1"
    assert row1["source_component"] == "tFileInputDelimited_1"
    # No column capture for input tables until the S4 tokenizer.
    assert row1["columns"] == []

    # Var table populated from var_entries.
    assert data["var_table"]["table_name"] == "Var"
    assert data["var_table"]["columns"] == ["Message"]

    # Output tables carry their columns from tmap entries.
    by_name = {t["table_name"]: t for t in data["output_tables"]}
    assert set(by_name) == {"out1", "row2"}
    assert by_name["out1"]["columns"] == ["id", "message"]
    assert by_name["row2"]["columns"] == ["id"]


async def test_tmap_component_graph_edges_carry_raw_expressions(client, scan_fixture):
    resp = await client.get(_url(TMAP_NAME))
    assert resp.status_code == 200
    edges = resp.json()["edges"]

    # One edge per tmap entry + var entry; raw expression only, opaque
    # confidence, empty resolved_refs (real resolution lands in slice S4).
    assert len(edges) == 4
    for edge in edges:
        assert edge["resolved_refs"] == []
        assert edge["confidence"] == "opaque"

    by_target = {(e["target_table"], e["target_column"]): e["expression"] for e in edges}
    assert by_target[("out1", "id")] == "row1.id"
    assert by_target[("out1", "message")] == "Var.Message"
    assert by_target[("row2", "id")] == "row1.id"
    assert by_target[("Var", "Message")] == "row1.message.toUpperCase()"


async def test_non_tmap_component_returns_inspector_fallback(client, scan_fixture):
    resp = await client.get(_url("tDBOutput_1"))
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"fallback": "inspector"}


async def test_logging_component_returns_inspector_fallback(client, scan_fixture):
    resp = await client.get(_url("tLogRow_1"))
    assert resp.status_code == 404
    assert resp.json()["detail"] == {"fallback": "inspector"}


async def test_unknown_component_returns_plain_404(client, scan_fixture):
    """A component that doesn't exist in the job at all is a distinct 404
    from the non-tMap inspector fallback."""
    resp = await client.get(_url("NoSuchComponent"))
    assert resp.status_code == 404
    assert resp.json()["detail"] != {"fallback": "inspector"}


async def test_unknown_job_returns_404(client, scan_fixture):
    resp = await client.get(_url(TMAP_NAME, job_name="NoSuchJob"))
    assert resp.status_code == 404


async def test_no_scan_returns_404(client):
    resp = await client.get(_url(TMAP_NAME))
    assert resp.status_code == 404


async def test_component_graph_rbac_denied(non_owner_client):  # noqa: F811
    """A user with no assignment to the project is denied even when scan
    data exists -- RBAC scope is never trusted from the caller."""
    from mcp_server.config import config

    _write_scan(config.intodoc_output_dir)
    resp = await non_owner_client.get(_url(TMAP_NAME))
    assert resp.status_code == 403
