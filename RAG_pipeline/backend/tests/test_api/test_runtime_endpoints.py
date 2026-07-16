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
_BILLING_EXEC = "billing-route-2190"


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
        assert ids == {_FAILED_EXEC, _SUCCESS_EXEC, _BILLING_EXEC}
        assert body["total"] == 3

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

    async def test_offset_page_keeps_honest_total(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions?limit=1&offset=1")
        assert resp.status_code == 200
        assert len(resp.json()["executions"]) == 1
        assert resp.json()["total"] == 3


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


class TestSyncStatus:
    async def test_scoped_sync_status(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/sync-status?source_key=mock")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["states"][0]["project_id"] == _ESB
        assert body["states"][0]["source_key"] == "mock"
        assert body["states"][0]["status"] == "success"
        assert body["states"][0]["last_synced_at"] is not None

    async def test_project_id_filter_cannot_widen_sync_status_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/sync-status?source_key=mock&project_id={_OTHER}"
            )
        assert resp.status_code == 200
        assert resp.json() == {"states": [], "total": 0}

    async def test_empty_scope_returns_empty_sync_status(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/sync-status")
        assert resp.status_code == 200
        assert resp.json() == {"states": [], "total": 0}


class TestRuntimeHealth:
    async def test_scoped_health(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(item["project_id"] == _ESB for item in body["health"])
        assert body["health"][0]["total_executions"] >= 1

    async def test_project_id_filter_cannot_widen_health_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/health?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"health": [], "total": 0}

    async def test_empty_scope_returns_empty_health(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/health")
        assert resp.status_code == 200
        assert resp.json() == {"health": [], "total": 0}


class TestRuntimeInventory:
    async def test_scoped_sources(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/sources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["sources"][0]["project_id"] == _ESB
        assert body["sources"][0]["source_key"] == "mock"
        assert body["sources"][0]["is_demo"] is True

    async def test_project_id_filter_cannot_widen_sources_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/sources?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"sources": [], "total": 0}

    async def test_scoped_engines(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/engines")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        names = {engine["name"] for engine in body["engines"]}
        assert names == {"remote-engine-prod-01", "remote-engine-prod-02"}
        assert {engine["status"] for engine in body["engines"]} == {"online", "degraded"}

    async def test_project_id_filter_cannot_widen_engines_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/engines?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"engines": [], "total": 0}

    async def test_scoped_engine_health(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/engine-health")
        assert resp.status_code == 200
        body = resp.json()
        engine_health = next(
            item for item in body["health"] if item["engine_id"] == "engine-prod-02"
        )
        assert engine_health["engine_name"] == "remote-engine-prod-02"
        assert engine_health["engine_status"] == "degraded"
        assert engine_health["total_runs"] == 2
        assert engine_health["failed_runs"] == 1
        assert engine_health["failure_rate"] == 0.5
        assert engine_health["current_failure_streak"] == 1
        assert engine_health["latest_state"] == "failed"

    async def test_project_id_filter_cannot_widen_engine_health_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/engine-health?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"health": [], "total": 0}

    async def test_scoped_overview(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/overview")
        assert resp.status_code == 200
        overview = resp.json()["overview"]
        assert overview["sources_total"] == 1
        assert overview["sources_demo"] == 1
        assert overview["engines_total"] == 2
        assert overview["engines_online"] == 1
        assert overview["engines_degraded"] == 1
        assert overview["environments_total"] == 2
        assert overview["workspaces_total"] == 1
        assert overview["engines_unhealthy"] == 1
        assert overview["jobs_unhealthy"] == 1
        assert overview["plans_unhealthy"] == 1
        assert overview["sources_stale"] == 1

    async def test_empty_scope_returns_empty_overview(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/overview")
        assert resp.status_code == 200
        assert resp.json()["overview"]["engines_total"] == 0
        assert resp.json()["overview"]["engines_unhealthy"] == 0


class TestRuntimeJobs:
    async def test_scoped_jobs(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/jobs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2
        names = {job["name"] for job in body["jobs"]}
        assert {"esb_order_sync_route", "esb_billing_export_route"}.issubset(names)
        order_job = next(job for job in body["jobs"] if job["name"] == "esb_order_sync_route")
        assert order_job["engine_name"] == "remote-engine-prod-02"
        assert order_job["environment_name"] == "Production"

    async def test_project_id_filter_cannot_widen_jobs_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/jobs?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"jobs": [], "total": 0}

    async def test_jobs_offset_page_keeps_honest_total(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/jobs?limit=1&offset=1")
        assert resp.status_code == 200
        assert len(resp.json()["jobs"]) == 1
        assert resp.json()["total"] >= 2

    async def test_scoped_job_runs(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/job-runs")
        assert resp.status_code == 200
        body = resp.json()
        run_ids = {run["run_id"] for run in body["runs"]}
        assert {_FAILED_EXEC, _SUCCESS_EXEC, _BILLING_EXEC}.issubset(run_ids)
        failed = next(run for run in body["runs"] if run["run_id"] == _FAILED_EXEC)
        assert failed["state"] == "failed"
        assert failed["execution_id"] == _FAILED_EXEC
        assert failed["engine_name"] == "remote-engine-prod-02"

    async def test_project_id_filter_cannot_widen_job_runs_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/job-runs?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"runs": [], "total": 0}

    async def test_scoped_job_health(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/job-health")
        assert resp.status_code == 200
        body = resp.json()
        order_health = next(item for item in body["health"] if item["job_id"] == "job-order-sync")
        assert order_health["total_runs"] == 2
        assert order_health["failed_runs"] == 1
        assert order_health["failure_rate"] == 0.5
        assert order_health["current_failure_streak"] == 1
        assert order_health["latest_state"] == "failed"

    async def test_empty_scope_returns_empty_job_health(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/job-health")
        assert resp.status_code == 200
        assert resp.json() == {"health": [], "total": 0}


class TestRuntimePlans:
    async def test_scoped_plans(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/plans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        plan = body["plans"][0]
        assert plan["plan_id"] == "plan-nightly-order-flow"
        assert plan["display_name"] == "Nightly Order Flow"
        assert {step["job_id"] for step in plan["steps"]} == {
            "job-order-sync",
            "job-billing-export",
        }

    async def test_project_id_filter_cannot_widen_plans_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/plans?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"plans": [], "total": 0}

    async def test_scoped_plan_runs(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/plan-runs")
        assert resp.status_code == 200
        body = resp.json()
        run_ids = {run["run_id"] for run in body["runs"]}
        assert {"plan-nightly-order-flow-7841", "plan-nightly-order-flow-7840"} == run_ids
        failed = next(run for run in body["runs"] if run["state"] == "failed")
        assert failed["plan_id"] == "plan-nightly-order-flow"
        assert failed["step_results"][0]["execution_id"] == _FAILED_EXEC

    async def test_project_id_filter_cannot_widen_plan_runs_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/plan-runs?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json() == {"runs": [], "total": 0}

    async def test_scoped_plan_health(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/plan-health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        health = body["health"][0]
        assert health["plan_id"] == "plan-nightly-order-flow"
        assert health["total_runs"] == 2
        assert health["failed_runs"] == 1
        assert health["failure_rate"] == 0.5
        assert health["current_failure_streak"] == 1
        assert health["latest_state"] == "failed"

    async def test_empty_scope_returns_empty_plan_health(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/plan-health")
        assert resp.status_code == 200
        assert resp.json() == {"health": [], "total": 0}


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
