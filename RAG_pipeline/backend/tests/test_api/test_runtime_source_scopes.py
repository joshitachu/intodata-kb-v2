"""Scope discovery and non-secret config editing for runtime sources.

An operator registering a TMC source needs to see which environments the token
can reach before committing to poll all of them, and needs to change that
selection later without re-typing the API token. These tests pin the two routes
that make that possible plus the guards that keep secrets and identity fields
out of the editable set.
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
_TOKEN = "token-a"
_CSRF = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}
_ENVIRONMENTS = [
    {"id": "env-dev", "name": "DEV"},
    {"id": "env-prod", "name": "PROD"},
]
_TASK_COUNTS = {"env-dev": 3, "env-prod": 12}


def _mock_user() -> User:
    role = MagicMock(spec=Role)
    role.name = "admin"
    user = MagicMock(spec=User)
    user.id = 1
    user.username = "admin"
    user.roles = [role]
    return user


def _handler(request: httpx.Request) -> httpx.Response:
    if request.headers["Authorization"].removeprefix("Bearer ") != _TOKEN:
        return httpx.Response(401, json={"error": "unauthorized"})
    if request.url.path == "/orchestration/environments":
        return httpx.Response(200, json={"items": _ENVIRONMENTS, "total": len(_ENVIRONMENTS)})
    if request.url.path == "/orchestration/executables/tasks":
        environment_id = request.url.params.get("environmentId")
        return httpx.Response(200, json={"items": [], "total": _TASK_COUNTS.get(environment_id, 0)})
    raise AssertionError(f"Unexpected request {request.url.path}")


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

    with (
        patch("src.api.dependencies.has_permission", return_value=True),
        patch("src.api.routes.runtime_sources.get_source_provider", return_value=provider),
        patch(
            "src.api.routes.runtime_sources.get_user_project_ids",
            AsyncMock(return_value=None),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF
        ) as http_client:
            yield SimpleNamespace(http=http_client, repo=RuntimeStoreRepository(db_session))

    app.dependency_overrides.clear()


async def _create(client, environment_ids=("env-prod",)) -> int:
    response = await client.http.post(
        "/api/v1/runtime/sources",
        json={
            "project_id": "p1",
            "provider_key": "tmc",
            "label": "Customer A",
            "credentials": {
                "api_token": _TOKEN,
                "base_url": _EU,
                "environment_ids": list(environment_ids),
            },
            "sync_interval_seconds": 60,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["source"]["id"]


class TestDiscover:
    async def test_valid_credentials_return_the_reachable_scopes(self, client):
        response = await client.http.post(
            "/api/v1/runtime/sources/discover",
            json={"provider_key": "tmc", "credentials": {"api_token": _TOKEN, "base_url": _EU}},
        )

        assert response.status_code == 200
        assert response.json()["scopes"] == [
            {"id": "env-dev", "label": "DEV", "detail": "3 tasks", "default_selected": True},
            {"id": "env-prod", "label": "PROD", "detail": "12 tasks", "default_selected": True},
        ]

    async def test_bad_credentials_fail_the_connection_test(self, client):
        response = await client.http.post(
            "/api/v1/runtime/sources/discover",
            json={"provider_key": "tmc", "credentials": {"api_token": "nope", "base_url": _EU}},
        )

        assert response.status_code == 400
        assert "nope" not in response.text

    async def test_discovery_writes_nothing(self, client):
        await client.http.post(
            "/api/v1/runtime/sources/discover",
            json={"provider_key": "tmc", "credentials": {"api_token": _TOKEN, "base_url": _EU}},
        )

        assert await client.repo.list_source_configs(None) == []

    async def test_a_registered_source_lists_scopes_from_its_stored_credentials(self, client):
        source_id = await _create(client)

        response = await client.http.post(f"/api/v1/runtime/sources/{source_id}/scopes")

        assert response.status_code == 200
        assert [scope["id"] for scope in response.json()["scopes"]] == ["env-dev", "env-prod"]


class TestScopeIsRequired:
    async def test_registering_without_a_scope_is_refused(self, client):
        response = await client.http.post(
            "/api/v1/runtime/sources",
            json={
                "project_id": "p1",
                "provider_key": "tmc",
                "label": "Customer A",
                "credentials": {"api_token": _TOKEN, "base_url": _EU},
                "sync_interval_seconds": 60,
            },
        )

        assert response.status_code == 400
        assert "environment" in response.json()["detail"].lower()

    async def test_registering_with_an_empty_scope_is_refused(self, client):
        response = await client.http.post(
            "/api/v1/runtime/sources",
            json={
                "project_id": "p1",
                "provider_key": "tmc",
                "label": "Customer A",
                "credentials": {"api_token": _TOKEN, "base_url": _EU, "environment_ids": []},
                "sync_interval_seconds": 60,
            },
        )

        assert response.status_code == 400


class TestConfigPatch:
    async def test_scope_can_be_changed_without_resupplying_the_token(self, client):
        source_id = await _create(client, environment_ids=("env-prod",))

        response = await client.http.patch(
            f"/api/v1/runtime/sources/{source_id}/config",
            json={"config": {"environment_ids": ["env-dev", "env-prod"]}},
        )

        assert response.status_code == 200
        stored, _key_id = await client.repo.read_credentials(source_id)
        assert stored["environment_ids"] == ["env-dev", "env-prod"]
        assert stored["api_token"] == _TOKEN

    async def test_other_non_secret_settings_are_editable_too(self, client):
        source_id = await _create(client)

        response = await client.http.patch(
            f"/api/v1/runtime/sources/{source_id}/config",
            json={"config": {"history_days": "30", "task_name_filter": "orders"}},
        )

        assert response.status_code == 200
        stored, _key_id = await client.repo.read_credentials(source_id)
        assert stored["history_days"] == "30"
        assert stored["task_name_filter"] == "orders"

    async def test_the_secret_cannot_be_written_through_the_config_route(self, client):
        source_id = await _create(client)

        response = await client.http.patch(
            f"/api/v1/runtime/sources/{source_id}/config",
            json={"config": {"api_token": "stolen"}},
        )

        assert response.status_code == 400
        stored, _key_id = await client.repo.read_credentials(source_id)
        assert stored["api_token"] == _TOKEN

    async def test_identity_fields_stay_frozen(self, client):
        """Repointing base_url would keep the history ingested from the old account."""
        source_id = await _create(client)

        response = await client.http.patch(
            f"/api/v1/runtime/sources/{source_id}/config",
            json={"config": {"base_url": "https://api.us.cloud.talend.com"}},
        )

        assert response.status_code == 400
        assert "base_url" in response.json()["detail"]

    async def test_clearing_a_required_scope_is_refused(self, client):
        source_id = await _create(client)

        response = await client.http.patch(
            f"/api/v1/runtime/sources/{source_id}/config",
            json={"config": {"environment_ids": []}},
        )

        assert response.status_code == 400
        stored, _key_id = await client.repo.read_credentials(source_id)
        assert stored["environment_ids"] == ["env-prod"]

    async def test_an_invalid_setting_is_rejected_before_it_is_stored(self, client):
        source_id = await _create(client)

        response = await client.http.patch(
            f"/api/v1/runtime/sources/{source_id}/config",
            json={"config": {"history_days": "400"}},
        )

        assert response.status_code == 400
        stored, _key_id = await client.repo.read_credentials(source_id)
        assert "history_days" not in stored


class TestRotatePreservesConfig:
    async def test_rotating_the_token_keeps_the_ingest_scope(self, client):
        """A renewed token must not silently widen the source back to the tenant."""
        source_id = await _create(client, environment_ids=("env-prod",))

        response = await client.http.post(
            f"/api/v1/runtime/sources/{source_id}/credentials/rotate",
            json={"credentials": {"api_token": _TOKEN, "base_url": _EU}},
        )

        assert response.status_code == 200
        stored, _key_id = await client.repo.read_credentials(source_id)
        assert stored["environment_ids"] == ["env-prod"]
