"""Tests for GET /mcp/projects/{id}/lineage/job (job/group lineage rollup).

Route-level concerns only: query-param contract (the "at least one of job or
group" 422, the job-count cap, offset/limit bounds), the two DISTINGUISHABLE
404s, paging fields, and RBAC. The selection and aggregation rules themselves
are covered in `tests/test_services/test_lineage_job_rollup.py`.

The fixture is a small synthetic scan pair (`lineage.json` + `manifest.json`)
written under the tmp `intodoc_output_dir` the client fixture points at. It
reproduces the two properties that matter at this boundary: both job-naming
spaces in one file, and a manifest group stored as a long source path.
"""

import json
from pathlib import Path

import pytest

# Reuse the RBAC fixture (authenticated user with no assignments) for the
# denied case so denial is proven against a project that actually has data.
from tests.test_api.test_rbac import non_owner_client  # noqa: F401

PROJECT_ID = "rolluproutproj"
JOB_A = "RHM2Smartflow_Workorders"
JOB_B = "RHM2Smartflow_Employee"
JOB_COMPOSITE = "VolkerVessels_Workorder_Talend_exports_VQ_RHM2SF___intod-31f9ad59e878"
GROUP_PATH = (
    "VolkerVessels_Workorder/Talend exports/VQ_RHM2SF.__intodoc_zip__/VQ_RHM2SF/RHM2Smartflow"
)


def _entry(job_name: str, target_column: str, *, resolved: bool = True) -> dict:
    return {
        "source_system": "",
        "source_table": "row1" if resolved else "",
        "source_column": f"row1/{target_column}" if resolved else "",
        "source_component": "tDBInput_1" if resolved else "",
        "source_job_name": "",
        "target_table": "out1",
        "target_column": f"out1/{target_column}",
        "target_component": "tMap_1",
        "job_name": job_name,
        "confidence": "certain" if resolved else "dynamic",
        "resolved": resolved,
        "dynamic_refs": [] if resolved else ["globalMap.get"],
        "transformations": [
            {
                "transformation_type": "direct_map" if resolved else "dynamic",
                "expression": f"row1.{target_column}" if resolved else 'globalMap.get("x")',
                "human_explanation": "",
            }
        ],
    }


def _write_scan(output_dir: Path, project_id: str = PROJECT_ID) -> None:
    scan_dir = output_dir / project_id / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    entries = (
        # Unresolved first on disk, so file order alone cannot satisfy the
        # resolved-before-unresolved ordering assertion below.
        [_entry(JOB_A, f"dyn{i}", resolved=False) for i in range(3)]
        + [_entry(JOB_A, "WorkorderNr"), _entry(JOB_A, "Status")]
        + [_entry(JOB_B, "EmployeeNr")]
        + [_entry(JOB_COMPOSITE, "Ref")]
    )
    (scan_dir / "lineage.json").write_text(
        json.dumps({"entries": entries, "unmapped_columns": [], "summary": {}}),
        encoding="utf-8",
    )
    (scan_dir / "manifest.json").write_text(
        json.dumps(
            {
                "job_groups": {
                    JOB_A: GROUP_PATH,
                    JOB_B: GROUP_PATH,
                    JOB_COMPOSITE: GROUP_PATH,
                }
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def scan_fixture(tmp_path):
    from mcp_server.config import config

    _write_scan(config.intodoc_output_dir)
    return config.intodoc_output_dir


async def test_job_rollup_returns_edges_and_totals(client, scan_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": JOB_A}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["project_id"] == PROJECT_ID
    assert data["jobs"] == [JOB_A]
    assert data["totals"]["entries"] == 5
    assert data["totals"]["resolved"] == 2
    assert data["totals"]["unresolved"] == 3
    assert data["totals"]["confidence"] == {
        "certain": 2, "likely": 0, "dynamic": 3, "inferred": 0, "opaque": 0
    }
    assert data["target_tables"] == ["out1"]
    assert data["total_edges"] == 5
    assert data["returned"] == 5
    assert data["offset"] == 0
    assert data["limit"] == 50
    assert data["has_more"] is False


async def test_job_rollup_resolved_edges_come_first(client, scan_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": JOB_A}
    )
    resolved_flags = [edge["resolved"] for edge in resp.json()["edges"]]
    assert resolved_flags == [True, True, False, False, False]


async def test_job_rollup_repeated_job_param_unions(client, scan_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params=[("job", JOB_A), ("job", JOB_B)]
    )
    assert resp.status_code == 200
    assert resp.json()["jobs"] == sorted([JOB_A, JOB_B])


async def test_job_rollup_group_resolves_via_manifest(client, scan_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"group": "RHM2Smartflow"}
    )
    assert resp.status_code == 200
    assert resp.json()["jobs"] == sorted([JOB_A, JOB_B, JOB_COMPOSITE])


async def test_job_rollup_unknown_job_is_absent_from_jobs_list(client, scan_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job",
        params=[("job", JOB_A), ("job", "NoSuchJob")],
    )
    assert resp.status_code == 200
    assert resp.json()["jobs"] == [JOB_A]


async def test_job_rollup_paging_is_stable_across_offsets(client, scan_fixture):
    first = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job",
        params={"job": JOB_A, "offset": 0, "limit": 2},
    )
    second = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job",
        params={"job": JOB_A, "offset": 2, "limit": 2},
    )
    whole = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": JOB_A, "limit": 200}
    )

    assert first.json()["has_more"] is True
    assert second.json()["has_more"] is True
    stitched = first.json()["edges"] + second.json()["edges"]
    assert stitched == whole.json()["edges"][:4]
    # totals describe the whole selection, not the page.
    assert first.json()["total_edges"] == 5


async def test_job_rollup_no_lineage_file_returns_404_naming_the_project(client):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": JOB_A}
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["reason"] == "lineage_artifact_absent"
    assert "No lineage data for project" in detail["message"]


async def test_job_rollup_unmatched_job_returns_a_different_404(client, scan_fixture):
    """Same status, different reason code: bad name, not a missing scan."""
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": "NoSuchJob"}
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["reason"] == "lineage_selection_empty"
    assert "No lineage entries for" in detail["message"]
    assert "NoSuchJob" in detail["message"]
    assert "No lineage data for project" not in detail["message"]


async def test_job_rollup_unmatched_group_404_names_the_group(client, scan_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"group": "NoSuchGroup"}
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["reason"] == "lineage_selection_empty"
    assert "NoSuchGroup" in detail["message"]


async def test_job_rollup_404_bodies_are_machine_discriminable(client, scan_fixture):
    """The consumer must never have to pattern-match prose to tell these apart.

    Mapping them backwards tells a user to re-run a full scan because of a
    typo, which is the harm the split exists to prevent -- so assert the two
    reason codes actually differ over the wire, not just in the service layer.
    """
    absent = await client.get(
        "/mcp/projects/noscanatall/lineage/job", params={"job": JOB_A}
    )
    empty = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": "NoSuchJob"}
    )

    assert absent.status_code == empty.status_code == 404
    absent_reason = absent.json()["detail"]["reason"]
    empty_reason = empty.json()["detail"]["reason"]
    assert absent_reason == "lineage_artifact_absent"
    assert empty_reason == "lineage_selection_empty"
    assert absent_reason != empty_reason


async def test_job_rollup_404_detail_has_exactly_reason_and_message(client, scan_fixture):
    """Additive change: the human-readable prose is kept, not replaced."""
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": "NoSuchJob"}
    )
    detail = resp.json()["detail"]
    assert set(detail) == {"reason", "message"}
    assert isinstance(detail["message"], str) and detail["message"]


async def test_job_rollup_404_shape_is_documented_in_openapi(client, scan_fixture):
    """The consumer reads OpenAPI; the reason codes must be discoverable there."""
    schema = (await client.get("/openapi.json")).json()
    responses = schema["paths"]["/mcp/projects/{project_id}/lineage/job"]["get"]["responses"]
    assert "404" in responses

    detail_schema = schema["components"]["schemas"]["LineageErrorDetail"]
    assert set(detail_schema["properties"]["reason"]["enum"]) == {
        "lineage_artifact_absent",
        "lineage_selection_empty",
    }


async def test_job_rollup_requires_job_or_group(client, scan_fixture):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/lineage/job")
    assert resp.status_code == 422
    assert "job" in str(resp.json()["detail"])


async def test_job_rollup_rejects_more_than_twenty_jobs(client, scan_fixture):
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job",
        params=[("job", f"Job{i}") for i in range(21)],
    )
    assert resp.status_code == 422


async def test_job_rollup_accepts_exactly_twenty_jobs(client, scan_fixture):
    """The cap is inclusive -- 20 names is a valid request, not a 422."""
    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job",
        params=[("job", JOB_A)] + [("job", f"Job{i}") for i in range(19)],
    )
    assert resp.status_code == 200
    assert resp.json()["jobs"] == [JOB_A]


@pytest.mark.parametrize(
    "params",
    [
        {"job": JOB_A, "limit": 0},
        {"job": JOB_A, "limit": 201},
        {"job": JOB_A, "offset": -1},
    ],
)
async def test_job_rollup_paging_bounds_return_422(client, scan_fixture, params):
    resp = await client.get(f"/mcp/projects/{PROJECT_ID}/lineage/job", params=params)
    assert resp.status_code == 422


async def test_job_rollup_expression_is_clipped_and_redacted(client, scan_fixture):
    """End-to-end credential safety: this payload is LLM prompt material."""
    from mcp_server.config import config

    scan_dir = config.intodoc_output_dir / PROJECT_ID / "scan"
    # Credential early, filler after: proves redaction happened AND that the
    # 400-char clip still applies to what remains.
    leaky = '"ftp://host/p?password=RAW(sup3rs3cr3t)&f=" + "' + ("x" * 500) + '"'
    entry = _entry(JOB_A, "Uri")
    entry["transformations"][0]["expression"] = leaky
    (scan_dir / "lineage.json").write_text(
        json.dumps({"entries": [entry], "unmapped_columns": [], "summary": {}}),
        encoding="utf-8",
    )

    resp = await client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": JOB_A}
    )
    assert resp.status_code == 200
    expression = resp.json()["edges"][0]["expression"]
    assert len(expression) <= 400
    assert "sup3rs3cr3t" not in expression
    assert "REDACTED-CREDENTIAL" in expression


async def test_job_rollup_rbac_denied(non_owner_client):  # noqa: F811
    """A user with no assignment is denied even when lineage data exists."""
    from mcp_server.config import config

    _write_scan(config.intodoc_output_dir)
    resp = await non_owner_client.get(
        f"/mcp/projects/{PROJECT_ID}/lineage/job", params={"job": JOB_A}
    )
    assert resp.status_code == 403


async def test_job_rollup_rbac_denied_before_param_validation(non_owner_client):  # noqa: F811
    """No job/group either -- the access check must still win, 403 not 422."""
    resp = await non_owner_client.get(f"/mcp/projects/{PROJECT_ID}/lineage/job")
    assert resp.status_code == 403
