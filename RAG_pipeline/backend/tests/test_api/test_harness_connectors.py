import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from src.api.dependencies import get_current_user
from src.api.routes.harness_connectors import router
from src.db.models.user import User
from src.db.session import get_db


@pytest.fixture
async def client(db_session):
    app = FastAPI()
    app.include_router(router)
    admin = User(id=1, username="admin", email="admin@example.com", is_superadmin=True, mfa_enabled=True)

    async def override_db():
        yield db_session

    async def override_user():
        return admin

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.mark.asyncio
async def test_safe_connector_crud_health_and_binding(client):
    response = await client.post("/harness/connectors", json={
        "name": "fixture", "provider_key": "fixture_knowledge", "config": {},
        "auth_mode": "credential_reference", "credential_reference": "vault:fixture",
    })
    assert response.status_code == 201, response.text
    connector = response.json()
    assert connector["credential_present"] is True
    assert connector["credential_reference_type"] == "vault"
    assert "vault:fixture" not in response.text

    instance_id = connector["id"]
    response = await client.post(f"/harness/connectors/{instance_id}/bindings", json={
        "capability_id": "connector.fixture.search", "project_ids": ["alpha"],
        "redaction_fields": ["internal"],
    })
    assert response.status_code == 201, response.text
    response = await client.post(f"/harness/connectors/{instance_id}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "summary": "Fixture dataset is available"}

    response = await client.get("/harness/connectors")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert "credential_reference" not in response.json()["connectors"][0]
    assert "vault:fixture" not in response.text

    response = await client.patch(f"/harness/connectors/{instance_id}", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_provider_manifest_is_safe_and_fixture_is_read_only(client):
    response = await client.get("/harness/connectors/providers")
    assert response.status_code == 200
    manifest = response.json()[0]
    assert manifest["capability_ids"] == ["connector.fixture.search"]
    assert "credential" not in str(manifest["config_schema"]).lower()
