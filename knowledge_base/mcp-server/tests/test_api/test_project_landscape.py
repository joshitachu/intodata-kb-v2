"""Tests for the read-only L1/L2 project landscape endpoint."""

from __future__ import annotations

import json

import pytest

from tests.test_api.test_rbac import non_owner_client  # noqa: F401

PROJECT_ID = "landscape-project"

LANDSCAPE = {
    "systems": [
        {
            "id": "system:maximo",
            "name": "Maximo",
            "kind": "rest_api",
            "evidence": [
                {
                    "signal_type": "context_group",
                    "label": "Maximo",
                    "detail": {"source_root": "MAXIMO_INTEGRATION"},
                }
            ],
            "source_roots": ["MAXIMO_INTEGRATION"],
        }
    ],
    "applications": [
        {
            "id": "application:assetcheck",
            "source_root": "ASSETCHECK",
            "source_roots": ["ASSETCHECK"],
            "project_id": "_assetcheck-project-id",
            "label": "Assetcheck",
            "description": "",
            "role": "execution",
            "artifact_count": 4,
            "kinds_breakdown": {"job": 3, "route": 1},
        }
    ],
    "edges": [
        {
            "source": "system:maximo",
            "target": "application:assetcheck",
            "source_kind": "system",
            "target_kind": "application",
            "dependency_type": "data",
            "mechanism": "rest",
            "direction": "in",
            "reliability": "synchronous",
            "confidence": "certain",
            "evidence_count": 1,
            "provenance": [
                {
                    "artifact_id": "FetchMaximo",
                    "qualified_artifact_id": "MAXIMO_INTEGRATION::FetchMaximo",
                    "source_root": "MAXIMO_INTEGRATION",
                    "component_name": "tRESTClient_1",
                    "fact_type": "artifact_resource",
                }
            ],
        }
    ],
    "channels": [
        {
            "channel_type": "jms",
            "channel": "assetcheck.requests",
            "direction": "out",
            "artifact_id": "FetchMaximo",
            "qualified_artifact_id": "MAXIMO_INTEGRATION::FetchMaximo",
            "source_root": "MAXIMO_INTEGRATION",
            "component_name": "cJMS_1",
            "fact_type": "artifact_channel",
            "redaction_descriptor": {
                "scheme": "jms",
                "category": "jms",
                "auth_present": True,
                "auth_type": "basic",
                "tls": False,
                "host_present": True,
            },
        }
    ],
    "interfaces": [
        {
            "endpoint_type": "rest",
            "direction": "in",
            "artifact_id": "AssetCheckRoute",
            "qualified_artifact_id": "ASSETCHECK::AssetCheckRoute",
            "source_root": "ASSETCHECK",
            "component_name": "cREST_1",
            "fact_type": "artifact_endpoint",
            "redaction_descriptor": {
                "scheme": "https",
                "category": "rest",
                "auth_present": True,
                "auth_type": "api_key",
                "tls": True,
                "host_present": True,
            },
        }
    ],
    "diagnostics": [
        {
            "kind": "unread_channel",
            "description": "DLQ has writers but no readers",
            "evidence": ["ASSETCHECK::rAssetCheck_ErrorReport"],
            "severity": "warning",
        }
    ],
    "semantic_topology": {
        "systems": [
            {
                "id": "semantic-system:maximo",
                "name": "Maximo",
                "kind": "software_system",
                "status": "proposed",
                "confidence": "likely",
                "aliases": ["Maximo"],
                "evidence": [],
            },
            {
                "id": "semantic-system:assetcheck",
                "name": "AssetCheck",
                "kind": "software_system",
                "status": "proposed",
                "confidence": "likely",
                "aliases": ["AssetCheck"],
                "evidence": [],
            },
        ],
        "integration_solutions": [
            {
                "id": "application:assetcheck",
                "name": "Assetcheck",
                "platform": "talend",
                "role": "execution",
                "source_roots": ["ASSETCHECK"],
                "artifact_count": 4,
            }
        ],
        "exchanges": [
            {
                "id": "exchange:maximo-assetcheck",
                "source_system_id": "semantic-system:maximo",
                "target_system_id": "semantic-system:assetcheck",
                "label": "Work orders",
                "payloads": ["Work orders"],
                "mechanisms": ["rest"],
                "reliability": "synchronous",
                "status": "proposed",
                "confidence": "likely",
                "implementation_solution_ids": ["application:assetcheck"],
                "artifact_ids": ["FetchMaximo"],
                "evidence": [
                    {
                        "signal_type": "directional_artifact_name",
                        "label": "Maximo2AssetCheck_SendWorkOrder",
                        "artifact_id": "FetchMaximo",
                        "qualified_artifact_id": "MAXIMO_INTEGRATION::FetchMaximo",
                        "source_root": "MAXIMO_INTEGRATION",
                        "component_name": "",
                    }
                ],
            }
        ],
        "unresolved": [],
        "summary": {
            "system_count": 2,
            "integration_solution_count": 1,
            "exchange_count": 1,
            "proposal_count": 3,
        },
    },
    "unmapped": [],
    "summary": {
        "system_count": 1,
        "application_count": 1,
        "edge_count": 1,
    },
}


def _write_landscape(intodoc_output_dir, payload=LANDSCAPE) -> None:
    scan_dir = intodoc_output_dir / PROJECT_ID / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "landscape.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


@pytest.fixture
def landscape_fixture():
    from mcp_server.config import config

    _write_landscape(config.intodoc_output_dir)
    return config.intodoc_output_dir


async def test_project_landscape_returns_scanner_artifact(
    client,
    landscape_fixture,
):
    response = await client.get(f"/mcp/projects/{PROJECT_ID}/landscape")

    assert response.status_code == 200, response.text
    payload = response.json()
    # Historic scanner semantic proposals are discarded at the API boundary.
    assert "semantic_topology" not in payload
    assert payload["business_topology"] is None
    assert payload["topology_evidence"] == {}
    assert payload["topology_state"] == {
        "available": False,
        "validated": False,
        "stale": False,
        "source": "none",
        "message": "Business topology has not been generated for this project.",
    }
    for key in (
        "systems",
        "applications",
        "edges",
        "channels",
        "interfaces",
        "diagnostics",
        "unmapped",
        "summary",
    ):
        assert payload[key] == LANDSCAPE[key]


async def test_project_landscape_preserves_edge_provenance(
    client,
    landscape_fixture,
):
    response = await client.get(f"/mcp/projects/{PROJECT_ID}/landscape")

    edge = response.json()["edges"][0]
    assert edge["dependency_type"] == "data"
    assert edge["provenance"] == [
        {
            "artifact_id": "FetchMaximo",
            "qualified_artifact_id": "MAXIMO_INTEGRATION::FetchMaximo",
            "source_root": "MAXIMO_INTEGRATION",
            "component_name": "tRESTClient_1",
            "fact_type": "artifact_resource",
        }
    ]
    assert response.json()["channels"][0]["channel"] == "assetcheck.requests"
    assert response.json()["channels"][0]["redaction_descriptor"]["tls"] is False
    assert response.json()["interfaces"][0]["endpoint_type"] == "rest"
    assert response.json()["interfaces"][0]["redaction_descriptor"]["auth_type"] == "api_key"


async def test_project_landscape_composes_validated_current_business_topology(
    client,
    landscape_fixture,
):
    from mcp_server.config import config
    from mcp_server.services.business_topology import build_business_topology_evidence

    topology_dir = config.project_root / "output" / PROJECT_ID / "docs" / "_topology"
    topology_dir.parent.mkdir(parents=True, exist_ok=True)
    scan_root = config.intodoc_output_dir / PROJECT_ID
    (scan_root / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    {
                        "name": "FetchMaximo",
                        "job_id": "job-1",
                        "source_root": "MAXIMO_INTEGRATION",
                        "artifact_kind": "job",
                        "artifact_role": "execution",
                        "components": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    bundle = build_business_topology_evidence(
        project_id=PROJECT_ID,
        scan_output_dir=scan_root,
        staging_dir=topology_dir.parent,
    )
    fingerprint = bundle.scan_fingerprint
    artifact_id = next(iter(bundle.artifact_ids))
    topology = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": fingerprint,
        "metadata": {
            "project_id": PROJECT_ID,
            "scan_fingerprint": fingerprint,
        },
        "systems": [],
        "integration_solutions": [{
            "id": "solution:1",
            "name": "Integration runtime",
            "description": "Implements the exchange.",
            "status": "inferred",
            "confidence": 0.8,
            "platform": "talend",
            "role": "execution",
            "artifact_ids": [artifact_id],
            "evidence_ids": [artifact_id],
        }],
        "business_objects": [],
        "data_streams": [],
        "delivery_paths": [],
        "controls": [],
        "risks": [],
        "unknowns": [],
        "artifact_annotations": [],
        "component_annotations": [],
        "lineage_annotations": [],
        "coverage": {
            "total_artifacts": 1,
            "cited_artifacts": 1,
            "total_evidence": len(bundle.evidence_ids),
            "cited_evidence": 1,
            "coverage_ratio": round(1 / len(bundle.evidence_ids), 6),
            "notes": [],
        },
    }
    (topology_dir / "business_topology.json").write_text(
        json.dumps(topology), encoding="utf-8"
    )
    (topology_dir / "validation_report.json").write_text(
        json.dumps({
            "validated": True,
            "scan_fingerprint": fingerprint,
            "project_id": PROJECT_ID,
            "errors": [],
            "warnings": [],
        }),
        encoding="utf-8",
    )
    artifacts_path = topology_dir / "evidence" / "artifacts.jsonl"
    artifact_record = json.loads(artifacts_path.read_text(encoding="utf-8").splitlines()[0])
    artifact_record["connections"] = [{"secret": "must-not-be-served"}]
    artifacts_path.write_text(
        json.dumps(artifact_record) + "\n",
        encoding="utf-8",
    )

    response = await client.get(f"/mcp/projects/{PROJECT_ID}/landscape")

    assert response.status_code == 200, response.text
    assert response.json()["business_topology"] == topology
    assert response.json()["topology_state"]["validated"] is True
    assert response.json()["topology_evidence"] == {
        "artifacts": [{
            "id": artifact_id,
            "name": "FetchMaximo",
            "job_id": "job-1",
            "source_root": "MAXIMO_INTEGRATION",
            "artifact_kind": "job",
            "artifact_role": "execution",
        }],
        "components": [],
        "lineage": [],
        "relations": [],
    }
    assert "secret" not in response.text


def test_topology_evidence_catalog_expands_invoked_design_context(tmp_path):
    from mcp_server.api.routes.projects import _load_topology_evidence_catalog

    topology_dir = tmp_path / "_topology"
    evidence_dir = topology_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    records = {
        "artifacts.jsonl": [
            {
                "id": "artifact:route",
                "name": "rAssetCheck2Vise_SendFormType",
                "job_id": "rAssetCheck2Vise_SendFormType",
                "source_root": "ASSETCHECK_WORKER",
                "artifact_kind": "route",
                "artifact_role": "execution",
            },
            {
                "id": "artifact:job",
                "name": "AssetCheck2Vise_SendFormType",
                "job_id": "AssetCheck2Vise_SendFormType",
                "source_root": "ASSETCHECK",
                "artifact_kind": "job",
                "artifact_role": "transformation",
                "secret": "must-not-be-served",
            },
        ],
        "relations.jsonl": [{
            "id": "relation:route-job",
            "source": "rAssetCheck2Vise_SendFormType",
            "target": "AssetCheck2Vise_SendFormType",
            "relation_type": "route_invokes_job",
            "component_name": "cTalendJob_1",
            "resolution": "unique_exact_name",
            "secret": "must-not-be-served",
        }],
        "components.jsonl": [{
            "id": "component:job-map",
            "artifact_id": "artifact:job",
            "name": "tMap_1",
            "component_type": "tMap",
            "secret": "must-not-be-served",
        }],
        "lineage.jsonl": [{
            "id": "lineage:form-type",
            "job_name": "AssetCheck2Vise_SendFormType",
            "source_table": "assetcheck_form",
            "source_column": "formType",
            "source_component": "tInput_1",
            "target_table": "vise_form",
            "target_column": "typeCode",
            "target_component": "tMap_1",
            "secret": "must-not-be-served",
        }],
    }
    for filename, rows in records.items():
        (evidence_dir / filename).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    result = _load_topology_evidence_catalog(topology_dir, {
        "data_streams": [{"artifact_ids": ["artifact:route"]}],
        "integration_solutions": [],
        "artifact_annotations": [],
        "component_annotations": [],
        "lineage_annotations": [],
    })

    assert [row["id"] for row in result["artifacts"]] == [
        "artifact:route", "artifact:job",
    ]
    assert result["components"] == [{
        "id": "component:job-map",
        "artifact_id": "artifact:job",
        "name": "tMap_1",
        "component_type": "tMap",
    }]
    assert result["lineage"][0]["source_column"] == "formType"
    assert result["lineage"][0]["target_column"] == "typeCode"
    assert result["relations"] == [{
        "id": "relation:route-job",
        "source": "rAssetCheck2Vise_SendFormType",
        "target": "AssetCheck2Vise_SendFormType",
        "relation_type": "route_invokes_job",
        "component_name": "cTalendJob_1",
        "resolution": "unique_exact_name",
    }]
    assert "secret" not in json.dumps(result)


async def test_project_landscape_rejects_stale_business_topology(
    client,
    landscape_fixture,
):
    from mcp_server.config import config

    topology_dir = config.project_root / "output" / PROJECT_ID / "docs" / "_topology"
    topology_dir.mkdir(parents=True, exist_ok=True)
    (topology_dir / "business_topology.json").write_text(
        json.dumps({
            "schema_version": "business-topology-v1",
            "scan_fingerprint": "old-scan",
            "metadata": {
                "project_id": PROJECT_ID,
                "scan_fingerprint": "old-scan",
            },
            "systems": [],
            "integration_solutions": [],
            "business_objects": [],
            "data_streams": [],
            "delivery_paths": [],
            "controls": [],
            "risks": [],
            "unknowns": [],
            "artifact_annotations": [],
            "component_annotations": [],
            "lineage_annotations": [],
            "coverage": {
                "total_artifacts": 0,
                "cited_artifacts": 0,
                "total_evidence": 0,
                "cited_evidence": 0,
                "coverage_ratio": 0,
                "notes": [],
            },
        }),
        encoding="utf-8",
    )
    (topology_dir / "validation_report.json").write_text(
        json.dumps({
            "validated": True,
            "scan_fingerprint": "old-scan",
            "project_id": PROJECT_ID,
            "errors": [],
            "warnings": [],
        }),
        encoding="utf-8",
    )

    response = await client.get(f"/mcp/projects/{PROJECT_ID}/landscape")

    assert response.status_code == 200, response.text
    assert response.json()["business_topology"] is None
    assert response.json()["topology_evidence"] == {}
    assert response.json()["topology_state"]["stale"] is True


@pytest.mark.parametrize(
    "failure",
    ["wrong-project", "wrong-schema", "unknown-evidence", "wrong-coverage"],
)
async def test_project_landscape_revalidates_published_topology_identity(
    client,
    landscape_fixture,
    failure: str,
):
    from mcp_server.config import config
    from mcp_server.services.business_topology import build_business_topology_evidence

    topology_dir = config.project_root / "output" / PROJECT_ID / "docs" / "_topology"
    topology_dir.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_business_topology_evidence(
        project_id=PROJECT_ID,
        scan_output_dir=config.intodoc_output_dir / PROJECT_ID,
        staging_dir=topology_dir.parent,
    )
    topology_project_id = "another-project" if failure == "wrong-project" else PROJECT_ID
    schema_version = "business-topology-v999" if failure == "wrong-schema" else "business-topology-v1"
    topology = {
        "schema_version": schema_version,
        "scan_fingerprint": bundle.scan_fingerprint,
        "metadata": {
            "project_id": topology_project_id,
            "scan_fingerprint": bundle.scan_fingerprint,
        },
        "coverage": {
            "total_artifacts": len(bundle.artifact_ids),
            "cited_artifacts": 0,
            "total_evidence": len(bundle.evidence_ids),
            "cited_evidence": 0,
            "coverage_ratio": 0,
        },
    }
    if failure == "unknown-evidence":
        topology["business_objects"] = [
            {
                "id": "object:unsupported",
                "name": "Unsupported object",
                "status": "inferred",
                "confidence": 0.5,
                "evidence_ids": ["evidence:not-in-ledger"],
            }
        ]
    if failure == "wrong-coverage":
        topology["coverage"]["total_evidence"] += 1
    (topology_dir / "business_topology.json").write_text(
        json.dumps(topology), encoding="utf-8"
    )
    (topology_dir / "validation_report.json").write_text(
        json.dumps(
            {
                "validated": True,
                "scan_fingerprint": bundle.scan_fingerprint,
                "project_id": topology_project_id,
                "errors": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    response = await client.get(f"/mcp/projects/{PROJECT_ID}/landscape")

    assert response.status_code == 200
    assert response.json()["business_topology"] is None
    assert response.json()["topology_state"]["validated"] is False


async def test_project_landscape_missing_artifact_returns_404(client):
    response = await client.get(f"/mcp/projects/{PROJECT_ID}/landscape")

    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"No landscape data for project: {PROJECT_ID}"
    )


async def test_project_landscape_invalid_artifact_returns_safe_500(client):
    from mcp_server.config import config

    scan_dir = config.intodoc_output_dir / PROJECT_ID / "scan"
    scan_dir.mkdir(parents=True, exist_ok=True)
    (scan_dir / "landscape.json").write_text("{broken", encoding="utf-8")

    response = await client.get(f"/mcp/projects/{PROJECT_ID}/landscape")

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Landscape data is invalid; rescan the project"
    )
    assert str(config.intodoc_output_dir) not in response.text


async def test_project_landscape_rbac_denied(non_owner_client):  # noqa: F811
    from mcp_server.config import config

    _write_landscape(config.intodoc_output_dir)
    response = await non_owner_client.get(
        f"/mcp/projects/{PROJECT_ID}/landscape"
    )

    assert response.status_code == 403


def test_nonproduction_artifact_classifier_excludes_scratch_folders():
    """Playground/Scratchpad/PoC/dev_*_tbd designs are non-production; real
    groups (e.g. maximoFourps) and routes (empty group) are kept. Classified by
    Talend folder only, never the job name, so name-based false positives can't
    slip in."""
    from mcp_server.api.routes.projects import _is_nonproduction_artifact

    nonproduction = [
        {"group": "Playground", "path": "Playground/TestMdG_0.1.item"},
        {"group": "Scratchpad", "path": ""},
        {"group": "PoC"},
        {"group": "dev_route_tbd"},
        {"group": "dev_jobs_tbd"},
        {"group": "utils", "name": "DumpContextVar"},
        {"group": "utils", "name": "jsonValidator"},
        # Talend "Copy_of_" duplicate prefix, even in a production folder.
        {"group": "maximoFourps", "name": "Copy_of_maximoFourpsInvoice"},
    ]
    production = [
        {"group": "maximoFourps", "name": "maximoFourpsInvoice"},
        {"group": "", "name": "rMaximo2Quintiq_Workorder"},
        {"group": "Maximo2RHM"},
        {"group": "MessageLogging"},
        {"group": "Talend_Support"},
        # A production job whose NAME contains "test" must NOT be excluded.
        {"group": "maximoFourps", "name": "latest_workorder"},
        # A production job whose name merely starts with "copy" but is not the
        # Talend "Copy_of_" duplicate prefix must NOT be excluded.
        {"group": "maximoFourps", "name": "copyright_sync"},
    ]
    for artifact in nonproduction:
        assert _is_nonproduction_artifact(artifact) is True, artifact
    for artifact in production:
        assert _is_nonproduction_artifact(artifact) is False, artifact
