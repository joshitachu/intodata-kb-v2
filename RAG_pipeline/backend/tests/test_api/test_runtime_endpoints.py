"""RBAC + behavior tests for the read-only runtime logs API (EP-005 slice 2).

Two projects are seeded into a real SQLite session: `esbmain` (rich ESB run with the
ORA-12170 failure) and `other-proj` (basic mock fixtures). Project scope is controlled
by patching `get_user_project_ids` so each test asserts the route threads the
authenticated scope into the repository and never widens it from a query param.
"""

import datetime
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Role, User
from src.runtime.store.seed import seed_mock_runtime_data, seed_rich_runtime_data

_SYNCED_AT = datetime.datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
_ESB = "esbmain"
_OTHER = "other-proj"
_FAILED_EXEC = "esbmain-route-7841"
_SUCCESS_EXEC = "esbmain-route-7840"


def _mock_user(id: int = 1) -> User:
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = id
    user.roles = [role]
    return user


@pytest.fixture
async def seeded_client(db_session):
    """Seed two projects, override db + current user, yield an ASGI client.

    Tests patch `src.api.routes.runtime.get_user_project_ids` to set the scope.
    """
    from src.api.dependencies import get_current_user, get_db

    await seed_rich_runtime_data(db_session, project_id=_ESB, synced_at=_SYNCED_AT)
    await seed_mock_runtime_data(db_session, project_id=_OTHER, synced_at=_SYNCED_AT)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


def _scope(value):
    return patch("src.api.routes.runtime.get_user_project_ids", AsyncMock(return_value=value))


class TestListExecutions:
    async def test_scoped_user_sees_only_assigned_project(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions")
        assert resp.status_code == 200
        body = resp.json()
        ids = {e["execution_id"] for e in body["executions"]}
        assert ids == {_FAILED_EXEC, _SUCCESS_EXEC}
        assert body["total"] == 2

    async def test_unrestricted_scope_sees_both_projects(self, seeded_client):
        with _scope(None):
            resp = await seeded_client.get("/api/v1/runtime/executions?limit=200")
        ids = {e["execution_id"] for e in resp.json()["executions"]}
        assert _FAILED_EXEC in ids
        # other-proj mock executions are present too (ids differ from the esb run)
        assert any(i not in {_FAILED_EXEC, _SUCCESS_EXEC} for i in ids)

    async def test_state_filter(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions?state=failed")
        executions = resp.json()["executions"]
        assert [e["execution_id"] for e in executions] == [_FAILED_EXEC]
        assert executions[0]["state"] == "failed"

    async def test_project_id_filter_cannot_widen_scope(self, seeded_client):
        # User scoped to esbmain asks for other-proj data -> empty, not other's rows.
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/executions?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json()["executions"] == []

    async def test_empty_scope_returns_empty(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/executions")
        assert resp.status_code == 200
        assert resp.json() == {"executions": [], "total": 0}

    async def test_limit_over_max_is_422(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions?limit=500")
        assert resp.status_code == 422


class TestExecutionDetail:
    async def test_in_scope_returns_execution(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/executions/{_FAILED_EXEC}")
        assert resp.status_code == 200
        assert resp.json()["execution"]["state"] == "failed"

    async def test_cross_project_lookup_returns_404(self, seeded_client):
        # esbmain execution requested by an other-proj user must 404 (no existence leak).
        with _scope([_OTHER]):
            resp = await seeded_client.get(f"/api/v1/runtime/executions/{_FAILED_EXEC}")
        assert resp.status_code == 404

    async def test_unknown_execution_returns_404(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions/does-not-exist")
        assert resp.status_code == 404


class TestExecutionLogs:
    async def test_in_scope_returns_all_log_lines(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/executions/{_FAILED_EXEC}/logs?limit=1000")
        assert resp.status_code == 200
        body = resp.json()
        assert body["execution_id"] == _FAILED_EXEC
        assert body["total"] == 16

    async def test_min_severity_error_filters_lower_severities(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/logs?min_severity=error"
            )
        events = resp.json()["events"]
        assert len(events) == 4
        assert all(e["severity"] == "error" for e in events)

    async def test_in_scope_execution_with_no_matching_severity_is_empty_not_404(self, seeded_client):
        # The success run has only info lines; filtering to error yields an empty list,
        # and a 200 (the execution exists in scope), never a 404.
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_SUCCESS_EXEC}/logs?min_severity=error"
            )
        assert resp.status_code == 200
        assert resp.json() == {"execution_id": _SUCCESS_EXEC, "events": [], "total": 0}

    async def test_cross_project_logs_return_404(self, seeded_client):
        with _scope([_OTHER]):
            resp = await seeded_client.get(f"/api/v1/runtime/executions/{_FAILED_EXEC}/logs")
        assert resp.status_code == 404


class TestFailures:
    async def test_scoped_failures(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/failures")
        failures = resp.json()["failures"]
        assert [f["execution_id"] for f in failures] == [_FAILED_EXEC]
        assert "ORA-12170" in failures[0]["error_summary"]

    async def test_project_id_filter_cannot_widen_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/failures?project_id={_OTHER}")
        assert resp.json()["failures"] == []

    async def test_empty_scope_returns_empty(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/failures")
        assert resp.json() == {"failures": [], "total": 0}


class TestAuthentication:
    async def test_unauthenticated_request_is_401(self, db_session):
        # Only get_db is overridden so the dependency resolves; real get_current_user
        # runs and rejects the missing access_token cookie.
        from src.api.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/runtime/executions")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 401
