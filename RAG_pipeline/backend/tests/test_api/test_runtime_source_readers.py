"""Grant routes: `/api/v1/runtime/sources/{id}/readers`.

A grant widens what a documentation run sees, and the consumer of that widening
-- `GET /runtime/doc-evidence` -- authenticates with a machine key and has no
user scope to check it against. This route is therefore the ONLY gate on the
whole feature, which is why authorization is pinned here in as much detail as
the behaviour is.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Role, User, runtime_data  # noqa: F401 - registers source tables
from src.runtime.store import RuntimeStoreRepository

_CSRF = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}
_OWNER = "proj-owner"
_READER = "proj-reader"
_OUTSIDER = "proj-outsider"


def _mock_user() -> User:
    role = MagicMock(spec=Role)
    role.name = "admin"
    user = MagicMock(spec=User)
    user.id = 1
    user.username = "admin"
    user.roles = [role]
    return user


def _scoped_to(project_ids: list[str] | None):
    """Pin the caller's project scope. None is an unrestricted operator."""
    return patch(
        "src.api.routes.runtime_source_readers.get_user_project_ids",
        AsyncMock(return_value=project_ids),
    )


@pytest.fixture
async def client(db_session):
    from src.api.dependencies import get_current_user, get_db

    store = RuntimeStoreRepository(db_session)
    source = await store.create_source_config(
        project_id=_OWNER, source_type="mock", label="Talend EU"
    )

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()

    with (
        patch("src.api.dependencies.has_permission", return_value=True),
        patch("src.runtime.audit.log_runtime_source_event", AsyncMock()),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=_CSRF
        ) as http:
            yield SimpleNamespace(http=http, store=store, source_id=source.id)

    app.dependency_overrides.clear()


def _url(source_id: int, suffix: str = "") -> str:
    return f"/api/v1/runtime/sources/{source_id}/readers{suffix}"


class TestAuthorization:
    async def test_an_unrestricted_operator_may_grant(self, client):
        with _scoped_to(None):
            resp = await client.http.post(
                _url(client.source_id), json={"project_id": _READER}
            )

        assert resp.status_code == 200
        assert resp.json()["grant"]["project_id"] == _READER

    async def test_holding_both_sides_is_enough(self, client):
        with _scoped_to([_OWNER, _READER]):
            resp = await client.http.post(
                _url(client.source_id), json={"project_id": _READER}
            )

        assert resp.status_code == 200

    async def test_holding_only_the_source_side_is_refused(self, client):
        """Granting into a project you cannot see hands data to a stranger."""
        with _scoped_to([_OWNER]):
            resp = await client.http.post(
                _url(client.source_id), json={"project_id": _OUTSIDER}
            )

        assert resp.status_code == 404

    async def test_holding_only_the_reader_side_is_refused(self, client):
        """...and granting FROM a source you cannot see steals data for yourself."""
        with _scoped_to([_READER]):
            resp = await client.http.post(
                _url(client.source_id), json={"project_id": _READER}
            )

        assert resp.status_code == 404

    async def test_the_reader_may_revoke_its_own_grant(self, client):
        """Dropping an access you hold is not a privilege escalation, and
        requiring the source owner would strand a reader who no longer wants a
        shared tenant in its documents."""
        with _scoped_to(None):
            await client.http.post(_url(client.source_id), json={"project_id": _READER})

        with _scoped_to([_READER]):
            resp = await client.http.delete(_url(client.source_id, f"/{_READER}"))

        assert resp.status_code == 204
        assert await client.store.resolve_runtime_read_projects(_READER) == [_READER]


class TestGrantBehaviour:
    async def test_a_grant_states_every_project_it_exposes(self, client):
        """A grant filters by project id, so it also reaches whatever the
        source's workspace map re-points rows to. Returned, never implied."""
        await client.store.add_container_mapping(
            source_id=client.source_id,
            container_key="workspace-b",
            container_name="Workspace B",
            project_id="proj-third",
            created_by="1",
        )

        with _scoped_to(None):
            resp = await client.http.post(
                _url(client.source_id), json={"project_id": _READER}
            )

        assert resp.json()["grant"]["exposes_project_ids"] == [_OWNER, "proj-third"]

    async def test_granting_to_the_owning_project_is_a_conflict(self, client):
        with _scoped_to(None):
            resp = await client.http.post(
                _url(client.source_id), json={"project_id": _OWNER}
            )

        assert resp.status_code == 409

    async def test_a_grant_is_idempotent_and_updates_its_scope(self, client):
        with _scoped_to(None):
            first = await client.http.post(
                _url(client.source_id),
                json={"project_id": _READER, "environment_ids": ["env-prod"]},
            )
            second = await client.http.post(
                _url(client.source_id),
                json={"project_id": _READER, "environment_ids": ["env-acc"], "window_days": 30},
            )
            listed = await client.http.get(_url(client.source_id))

        assert first.json()["grant"]["id"] == second.json()["grant"]["id"]
        assert second.json()["grant"]["environment_ids"] == ["env-acc"]
        assert second.json()["grant"]["window_days"] == 30
        assert listed.json()["total"] == 1

    async def test_an_unknown_source_is_a_404(self, client):
        with _scoped_to(None):
            resp = await client.http.post(_url(9999), json={"project_id": _READER})

        assert resp.status_code == 404

    async def test_window_days_out_of_range_is_rejected(self, client):
        with _scoped_to(None):
            resp = await client.http.post(
                _url(client.source_id), json={"project_id": _READER, "window_days": 400}
            )

        assert resp.status_code == 422
