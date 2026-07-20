"""Tests for GET /mcp/projects/{id}/jobs/{job_name}/graph (Level-2 job graph).

The scan fixture mirrors the real per-job scan JSON shape (verified against
intodoc-output/cdc): per-component incoming/outgoing_connections are empty
while the job-level connections array is populated, tmap_data entries carry
no component name, and credential param values arrive pre-redacted.
"""

import json

import pytest

# Reuse the RBAC fixture (authenticated user with no assignments) for the
# denied case so denial is proven against a project that actually has scan data.
from tests.test_api.test_rbac import non_owner_client  # noqa: F401

PROJECT_ID = "graphproj"
GROUP = "Item"
JOB_NAME = "sjItem_OffRamp"

# Representative job scan JSON: input -> tMap -> DB output, a logging row,
# a prejob trigger, custom code, and a tRunJob portal to a child job.
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
            "params": [
                {"name": "FILE_NAME", "value": '"/data/in.csv"', "field_type": "TEXT"},
            ],
        },
        {
            "unique_name": "tMap_1",
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
            "params": [
                {"name": "TABLE", "value": '"dbo.Items"', "field_type": "TEXT"},
                {"name": "HOST", "value": '"db.internal"', "field_type": "TEXT"},
                {"name": "USER", "value": '"svc_items"', "field_type": "TEXT"},
                {"name": "PASS", "value": "[REDACTED]", "field_type": "PASSWORD"},
                {"name": "PASSWORD", "value": "[REDACTED]", "field_type": "PASSWORD"},
                {"name": "API_TOKEN", "value": "super-secret", "field_type": "TEXT"},
                {"name": "ENDPOINT_URL", "value": '"https://user:hunter2@api.example.com/v1"', "field_type": "TEXT"},
            ],
        },
        {
            "unique_name": "tLogRow_1",
            "component_type": "tLogRow",
            "metadata_connection_ref": "",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [],
        },
        {
            "unique_name": "tPrejob_1",
            "component_type": "tPrejob",
            "metadata_connection_ref": "",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [],
        },
        {
            "unique_name": "tJava_1",
            "component_type": "tJava",
            "metadata_connection_ref": "",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [
                {"name": "CODE", "value": 'System.out.println("hi");', "field_type": "MEMO"},
            ],
        },
        {
            "unique_name": "tRunJob_1",
            "component_type": "tRunJob",
            "metadata_connection_ref": "",
            "incoming_connections": [],
            "outgoing_connections": [],
            "params": [
                {"name": "PROCESS", "value": "Child_Job", "field_type": "PROCESS_TYPE"},
            ],
        },
    ],
    "connections": [
        {"name": "row1", "source": "tFileInputDelimited_1", "target": "tMap_1",
         "connector_name": "FLOW", "line_style": 0},
        {"name": "out1", "source": "tMap_1", "target": "tDBOutput_1",
         "connector_name": "FLOW", "line_style": 0},
        {"name": "row2", "source": "tMap_1", "target": "tLogRow_1",
         "connector_name": "FLOW", "line_style": 0},
        {"name": "OnComponentOk1", "source": "tPrejob_1", "target": "tJava_1",
         "connector_name": "COMPONENT_OK", "line_style": 3},
        {"name": "If1", "source": "tDBOutput_1", "target": "tRunJob_1",
         "connector_name": "RUN_IF", "line_style": 6},
    ],
    "tmap_data": [
        {
            "input_tables": ["row1"],
            "output_tables": ["out1", "row2"],
            "entries": [
                {"table_name": "out1", "column_name": "id", "expression": "row1.id"},
            ],
            "var_entries": [],
        },
    ],
    "trunjob_refs": [
        {"child_job": "Child_Job", "child_job_xmi_id": "_x1", "context_params": []},
    ],
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


def _write_inventory_doc(project_root, project_id: str = PROJECT_ID) -> None:
    """Write a generated inventory doc with a per-job component table.

    The description ("Role") column is intentionally not the last column, and
    tMap_1 has no doc row, to exercise header-based detection and the
    anti-fabrication default.
    """
    docs_dir = project_root / "output" / project_id / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "02_COMPONENT_INVENTORY.md").write_text(
        """# 02 Component Inventory

## Components

### Job: sjItem_OffRamp (7 components)

| Component ID | Type | Role | Active | Key Properties / Notes |
|--------------|------|------|--------|------------------------|
| tFileInputDelimited_1 | tFileInputDelimited | Reads the inbound CSV file | Yes | HEADER=1. |
| tDBOutput_1 | tMSSqlOutput | Inserts rows into table `Items` in MSSQL | Yes | batch 10000. |
""",
        encoding="utf-8",
    )


@pytest.fixture
def scan_fixture(tmp_path):
    """Write the scan cache under the tmp intodoc_output dir the client uses."""
    from mcp_server.config import config

    _write_scan(config.intodoc_output_dir)
    return config.intodoc_output_dir


async def test_job_graph_nodes_match_components(client, scan_fixture):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["project_id"] == PROJECT_ID
    assert data["job_name"] == JOB_NAME
    assert data["group"] == GROUP

    # Node count equals component count in the scan JSON.
    assert len(data["nodes"]) == len(JOB_SCAN["components"])

    by_name = {n["unique_name"]: n for n in data["nodes"]}
    assert by_name["tFileInputDelimited_1"]["role"] == "input"
    assert by_name["tMap_1"]["role"] == "transform"
    assert by_name["tDBOutput_1"]["role"] == "output"
    assert by_name["tLogRow_1"]["role"] == "logging"
    assert by_name["tPrejob_1"]["role"] == "orchestration"
    assert by_name["tRunJob_1"]["role"] == "orchestration"
    assert by_name["tJava_1"]["role"] == "transform"

    # has_subgraph only for tMap today.
    assert by_name["tMap_1"]["has_subgraph"] is True
    assert all(
        n["has_subgraph"] is False
        for n in data["nodes"]
        if n["unique_name"] != "tMap_1"
    )

    # tRunJob portal node resolves the child job.
    assert by_name["tRunJob_1"]["subjob_target"] == "Child_Job"
    assert by_name["tMap_1"]["subjob_target"] is None

    # Connection ref for the system badge.
    assert by_name["tDBOutput_1"]["connection_ref"] == "Demo_MSSQL_Output"

    # has_code from the CODE param.
    assert by_name["tJava_1"]["has_code"] is True
    assert by_name["tMap_1"]["has_code"] is False


async def test_job_graph_param_summary_filters_credentials(client, scan_fixture):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 200
    by_name = {n["unique_name"]: n for n in resp.json()["nodes"]}

    summary = by_name["tDBOutput_1"]["param_summary"]
    # High-signal params survive.
    assert summary["TABLE"] == '"dbo.Items"'
    assert summary["HOST"] == '"db.internal"'
    # Credential-named params never appear, redacted or not.
    assert "PASS" not in summary
    assert "PASSWORD" not in summary
    assert "API_TOKEN" not in summary
    # URL userinfo secrets are masked even inside allowed params.
    assert "hunter2" not in json.dumps(resp.json())
    assert "ENDPOINT_URL" in summary and ":***@" in summary["ENDPOINT_URL"]
    # No redacted placeholders leak as values.
    assert "[REDACTED]" not in summary.values()

    # Input node keeps its file param.
    assert by_name["tFileInputDelimited_1"]["param_summary"]["FILE_NAME"] == '"/data/in.csv"'


async def test_job_graph_edges_from_connections_array(client, scan_fixture):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 200
    edges = resp.json()["edges"]

    # All edges come from the job's connections array (per-component
    # incoming/outgoing_connections are empty in the fixture, as on real scans).
    assert len(edges) == len(JOB_SCAN["connections"])

    by_edge_name = {e["name"]: e for e in edges}
    # Data-flow rows.
    for name in ("row1", "out1", "row2"):
        edge = by_edge_name[name]
        assert edge["is_control_flow"] is False
        assert edge["connector_name"] == "FLOW"
        assert edge["line_style"] == 0
    # Control-flow triggers are distinguished.
    assert by_edge_name["OnComponentOk1"]["is_control_flow"] is True
    assert by_edge_name["OnComponentOk1"]["connector_name"] == "COMPONENT_OK"
    assert by_edge_name["If1"]["is_control_flow"] is True
    assert by_edge_name["If1"]["line_style"] == 6

    # Source/target round-trip intact.
    assert by_edge_name["row1"]["source"] == "tFileInputDelimited_1"
    assert by_edge_name["row1"]["target"] == "tMap_1"


async def test_job_graph_tmap_tables(client, scan_fixture):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 200
    tmap_tables = resp.json()["tmap_tables"]

    assert set(tmap_tables) == {"tMap_1"}
    assert tmap_tables["tMap_1"]["input_tables"] == ["row1"]
    assert tmap_tables["tMap_1"]["output_tables"] == ["out1", "row2"]


async def test_job_graph_populates_descriptions_from_inventory_doc(client, scan_fixture):
    from mcp_server.config import config

    _write_inventory_doc(config.project_root)
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 200, resp.text
    by_name = {n["unique_name"]: n for n in resp.json()["nodes"]}

    # Doc rows are surfaced; backtick noise is stripped.
    assert by_name["tFileInputDelimited_1"]["description"] == "Reads the inbound CSV file"
    assert by_name["tDBOutput_1"]["description"] == "Inserts rows into table Items in MSSQL"
    # A component with no doc row keeps the anti-fabrication empty default.
    assert by_name["tMap_1"]["description"] == ""


async def test_job_graph_descriptions_empty_without_docs(client, scan_fixture):
    """No generated docs -> every description empty (behaves exactly as before)."""
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 200
    assert all(n["description"] == "" for n in resp.json()["nodes"])


async def test_job_graph_unknown_job_returns_404(client, scan_fixture):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/NoSuchJob/graph")
    assert resp.status_code == 404


async def test_job_graph_no_scan_returns_404(client):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 404


async def test_job_graph_invalid_job_name_returns_400(client, scan_fixture):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/jobs/evil..job/graph")
    assert resp.status_code == 400


async def test_job_graph_rbac_denied(non_owner_client):  # noqa: F811
    """A user with no assignment to the project is denied even when scan data exists."""
    from mcp_server.config import config

    _write_scan(config.intodoc_output_dir)
    resp = await non_owner_client.get(f"/mcp/projects/{PROJECT_ID}/jobs/{JOB_NAME}/graph")
    assert resp.status_code == 403
