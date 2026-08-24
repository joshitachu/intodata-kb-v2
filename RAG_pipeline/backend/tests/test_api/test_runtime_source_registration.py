"""Registration guard: one runtime source per tenant, not per shared endpoint.

Talend Cloud puts every customer in a region behind one base URL. The duplicate
guard used to key on that URL, so onboarding a second customer was rejected as a
duplicate of the first. These tests cover both directions: a different account
must register, and the same account must still be refused however it is
presented.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.config import settings
from src.db.models import Role, User, runtime_data  # noqa: F401 - registers source tables
from src.runtime.providers.talend_tmc import TalendTmcRuntimeProvider
from src.runtime.store import RuntimeStoreRepository

_EU = "https://api.eu.cloud.talend.com"
_TENANT_A = ["env-a1", "env-a2"]
_TENANT_B = ["env-b1"]
_TOKENS = {"token-a": _TENANT_A, "token-a-rotated": _TENANT_A, "token-b": _TENANT_B}
_CSRF = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


def _mock_user() -> User:
    role = MagicMock(spec=Role)
    role.name = "admin"
    user = MagicMock(spec=User)
    user.id = 1
    user.username = "admin"
    user.roles = [role]
    return user


def _handler(request: httpx.Request) -> httpx.Response:
    token = request.headers["Authorization"].removeprefix("Bearer ")
    environment_ids = _TOKENS.get(token)
    if environment_ids is None:
        return httpx.Response(401, json={"error": "unauthorized"})
    return httpx.Response(
        200,
        json={
            "items": [{"id": item, "name": item.upper()} for item in environment_ids],
            "total": len(environment_ids),
        },
    )


def _body(token: str, label: str, project_id: str = "p1") -> dict:
    return {
        "project_id": project_id,
        "provider_key": "tmc",
        "label": label,
        "credentials": {"api_token": token, "base_url": _EU},
        "sync_interval_seconds": 60,
    }


@pytest.fixture
async def client(db_session, monkeypatch):
    from src.api.dependencies import get_current_user, get_db

    monkeypatch.setattr(settings, "source_credentials_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous", "")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_id", "primary")
    monkeypatch.setattr(settings, "source_credentials_encryption_key_previous_id", "previous")

    provider = TalendTmcRuntimeProvider(transport=httpx.MockTransport(_handler))

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()

    transport = ASGITransport(app=app)
    with (
        patch("src.api.dependencies.has_permission", return_value=True),
        patch("src.api.routes.runtime_sources.get_source_provider", return_value=provider),
        patch(
            "src.api.routes.runtime_sources.get_user_project_ids",
            AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=_CSRF
        ) as http_client:
            yield SimpleNamespace(
                http=http_client,
                repo=RuntimeStoreRepository(db_session),
                provider=provider,
            )

    app.dependency_overrides.clear()


async def _seed_source(client, *, token: str, identity: str, project_id: str, label: str) -> int:
    """Insert a source carrying an identity written under an older rule."""
    source = await client.repo.create_source_config(
        project_id=project_id, source_type="tmc", physical_identity=identity, label=label
    )
    await client.repo.write_credentials(
        source_id=source.id,
        plaintext_payload={"api_token": token, "base_url": _EU},
        credential_type="tmc",
        rotated_by="1",
    )
    return source.id


class TestCreate:
    async def test_second_account_on_the_same_endpoint_registers(self, client):
        first = await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        second = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2")
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["source"]["id"] != second.json()["source"]["id"]

    async def test_same_account_registered_twice_is_refused(self, client):
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        again = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A again", project_id="p2")
        )

        assert again.status_code == 409
        assert again.json()["detail"] == "That physical runtime source is already registered"

    async def test_same_account_under_a_second_token_is_refused(self, client):
        """The mirror case the guard exists for: one TMC, two registrations."""
        await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        mirror = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a-rotated", "Mirror", project_id="p2")
        )

        assert mirror.status_code == 409

    async def test_credentials_that_cannot_identify_an_account_are_rejected(self, client):
        response = await client.http.post("/api/v1/runtime/sources", json=_body("token-unknown", "Bad"))

        assert response.status_code == 400
        assert "token-unknown" not in response.text


class TestPreNarrowingRows:
    async def test_row_stored_under_the_endpoint_identity_is_repaired(self, client):
        source_id = await _seed_source(
            client, token="token-a", identity=f"tmc:{_EU}", project_id="p1", label="Customer A"
        )

        response = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2")
        )

        assert response.status_code == 200
        repaired = await client.repo.get_source_physical_identity(source_id)
        assert repaired.startswith(f"tmc:{_EU}#")

    async def test_repaired_row_still_blocks_its_own_account(self, client):
        await _seed_source(
            client, token="token-a", identity=f"tmc:{_EU}", project_id="p1", label="Customer A"
        )

        response = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-a", "Customer A again", project_id="p2")
        )

        assert response.status_code == 409

    async def test_dead_credentials_do_not_block_an_unrelated_registration(self, client):
        source_id = await _seed_source(
            client, token="token-revoked", identity=f"tmc:{_EU}", project_id="p1", label="Stale"
        )

        response = await client.http.post(
            "/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2")
        )

        assert response.status_code == 200
        assert await client.repo.get_source_physical_identity(source_id) == f"tmc:{_EU}"


class TestRotate:
    async def test_rotating_within_the_same_account_keeps_the_identity(self, client):
        created = await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        source_id = created.json()["source"]["id"]
        before = await client.repo.get_source_physical_identity(source_id)

        response = await client.http.post(
            f"/api/v1/runtime/sources/{source_id}/credentials/rotate",
            json={"credentials": {"api_token": "token-a-rotated", "base_url": _EU}},
        )

        assert response.status_code == 200
        assert await client.repo.get_source_physical_identity(source_id) == before

    async def test_rotating_onto_another_registered_account_is_refused(self, client):
        created = await client.http.post("/api/v1/runtime/sources", json=_body("token-a", "Customer A"))
        source_id = created.json()["source"]["id"]
        await client.http.post("/api/v1/runtime/sources", json=_body("token-b", "Customer B", project_id="p2"))
        before = await client.repo.get_source_physical_identity(source_id)

        response = await client.http.post(
            f"/api/v1/runtime/sources/{source_id}/credentials/rotate",
            json={"credentials": {"api_token": "token-b", "base_url": _EU}},
        )

        assert response.status_code == 409
        assert await client.repo.get_source_physical_identity(source_id) == before
