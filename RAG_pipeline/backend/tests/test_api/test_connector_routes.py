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
