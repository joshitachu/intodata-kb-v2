from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import Role, User

CSRF = {"cookie": "csrf_token=t", "X-CSRF-Token": "t"}


@pytest.fixture
async def owner_client(db_session):
    owner = User(
        username="connector_owner",
        email="connector-owner@example.com",
        is_superadmin=True,
        mfa_enabled=True,
    )
    owner.roles = [Role(name="connector_admin", description="")]
    db_session.add(owner)
    await db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=CSRF
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def test_owner_can_configure_health_check_and_bind_a_connector(owner_client) -> None:
    providers = await owner_client.get("/api/v1/connectors/providers")
    assert providers.status_code == 200
    assert {item["provider_key"] for item in providers.json()["providers"]} >= {
        "fixture_knowledge",
        "http_elastic",
    }
    assert "mcp_elastic" not in {item["provider_key"] for item in providers.json()["providers"]}

    created = await owner_client.post(
        "/api/v1/connectors",
        json={
            "name": "Operations knowledge",
            "provider_key": "fixture_knowledge",
            "config": {"dataset": "default", "max_results": 5},
        },
    )
    assert created.status_code == 201, created.text
    connector_id = created.json()["id"]

    health = await owner_client.post(f"/api/v1/connectors/{connector_id}/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    binding = await owner_client.post(
        f"/api/v1/connectors/{connector_id}/bindings",
        json={
            "capability_id": "connector.fixture.search",
            "project_ids": ["VW_FULL_LANDSCAPE"],
        },
    )
    assert binding.status_code == 201, binding.text
    assert binding.json()["project_ids"] == ["VW_FULL_LANDSCAPE"]


async def test_owner_can_reuse_existing_nexus_elastic_without_secret_entry(owner_client, monkeypatch) -> None:
    import src.api.routes.connectors as route

    class Bridge:
        async def prepare_existing_elastic(self, access_token: str):
            assert access_token == "existing-user-session"
            return {
                "connector_id": "mcp-connector-1",
                "display_name": "Elastic Stack",
                "source_ids": ["source-errors", "source-monitoring"],
                "source_labels": ["Errors", "Monitoring"],
            }

    monkeypatch.setattr(route, "McpConnectorBridge", Bridge)
    response = await owner_client.post(
        "/api/v1/connectors/existing-elastic/use",
        headers={"cookie": "access_token=existing-user-session; csrf_token=t", "X-CSRF-Token": "t"},
        json={"project_id": "VW_FULL_LANDSCAPE"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["provider_key"] == "mcp_elastic"
    assert response.json()["credential_present"] is False
    assert response.json()["config"]["source_ids"] == ["source-errors", "source-monitoring"]
    bindings = await owner_client.get(f"/api/v1/connectors/{response.json()['id']}/bindings")
    assert bindings.status_code == 200
    assert bindings.json()["bindings"][0]["project_ids"] == ["VW_FULL_LANDSCAPE"]


async def test_http_binding_cannot_accidentally_be_global(owner_client) -> None:
    created = await owner_client.post(
        "/api/v1/connectors",
        json={"name": "Scoped connector", "provider_key": "fixture_knowledge"},
    )
    connector_id = created.json()["id"]

    refused = await owner_client.post(
        f"/api/v1/connectors/{connector_id}/bindings",
        json={"capability_id": "connector.fixture.search", "project_ids": []},
    )

    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "project_scope_required"


async def test_owner_preview_is_bound_to_selected_project(owner_client) -> None:
    created = await owner_client.post(
        "/api/v1/connectors",
        json={"name": "Preview fixture", "provider_key": "fixture_knowledge"},
    )
    connector_id = created.json()["id"]
    await owner_client.post(f"/api/v1/connectors/{connector_id}/health")
    await owner_client.post(
        f"/api/v1/connectors/{connector_id}/bindings",
        json={
            "capability_id": "connector.fixture.search",
            "project_ids": ["VW_FULL_LANDSCAPE"],
        },
    )

    allowed = await owner_client.post(
        f"/api/v1/connectors/{connector_id}/preview",
        json={
            "capability_id": "connector.fixture.search",
            "project_id": "VW_FULL_LANDSCAPE",
            "query": "Payments",
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert {item["source_ref"] for item in allowed.json()["items"]} == {
        "fixture:runbook", "fixture:ownership"
    }

    denied = await owner_client.post(
        f"/api/v1/connectors/{connector_id}/preview",
        json={
            "capability_id": "connector.fixture.search",
            "project_id": "another_project",
            "query": "Payments",
        },
    )
    assert denied.status_code == 422


async def test_binding_access_can_change_but_never_become_global(owner_client) -> None:
    created = await owner_client.post(
        "/api/v1/connectors",
        json={"name": "Scoped fixture", "provider_key": "fixture_knowledge"},
    )
    connector_id = created.json()["id"]
    bound = await owner_client.post(
        f"/api/v1/connectors/{connector_id}/bindings",
        json={"capability_id": "connector.fixture.search", "project_ids": ["project_a"]},
    )
    binding_id = bound.json()["id"]

    changed = await owner_client.patch(
        f"/api/v1/connectors/{connector_id}/bindings/{binding_id}",
        json={"project_ids": ["project_b"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["project_ids"] == ["project_b"]

    refused = await owner_client.patch(
        f"/api/v1/connectors/{connector_id}/bindings/{binding_id}",
        json={"project_ids": []},
    )
    assert refused.status_code == 422
