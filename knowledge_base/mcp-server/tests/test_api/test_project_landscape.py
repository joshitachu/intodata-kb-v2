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
    assert response.json() == LANDSCAPE


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
