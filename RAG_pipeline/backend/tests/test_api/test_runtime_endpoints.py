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
# The two "capture state" runs seed_rich_runtime_data always adds alongside the
# three above (src/runtime/store/seed.py::_demo_capture_state_executions) --
# the widened outcome vocabulary's own fixtures: a misfire never started, a
# timeout was killed by its own limit.
_MISFIRED_JOB = "esb_inventory_sync_task"
_MISFIRED_EXEC = "esbmain-task-4417"
_TIMED_OUT_JOB = "esb_nightly_reconcile_batch"
_TIMED_OUT_EXEC = "esbmain-batch-5501"
_ESB_ALL_EXECS = {_FAILED_EXEC, _SUCCESS_EXEC, _BILLING_EXEC, _MISFIRED_EXEC, _TIMED_OUT_EXEC}


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
        assert ids == _ESB_ALL_EXECS
        assert body["total"] == 5

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
        assert resp.json()["total"] == 5


class TestWidenedExecutionStates:
    """MISFIRED, TIMED_OUT and TERMINATED, plus the outcome-provenance fields
    (raw_state, misfire_reason, execution_type) mixed into runs/executions/plan
    runs by `RuntimeOutcomeProvenance`. Before this vocabulary widened, both
    seeded runs here reported as `unknown` and were indistinguishable from a
    genuine gap in the data.
    """

    async def test_state_filter_accepts_misfired(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions?state=misfired")
        assert resp.status_code == 200
        executions = resp.json()["executions"]
        assert [e["execution_id"] for e in executions] == [_MISFIRED_EXEC]

    async def test_state_filter_accepts_timed_out(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions?state=timed_out")
        assert resp.status_code == 200
        executions = resp.json()["executions"]
        assert [e["execution_id"] for e in executions] == [_TIMED_OUT_EXEC]

    async def test_state_filter_accepts_terminated_with_no_matches(self, seeded_client):
        # No TERMINATED run is seeded; the point of this test is that the value
        # is a valid enum member (200 + empty), not a 422 from an unknown state.
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions?state=terminated")
        assert resp.status_code == 200
        assert resp.json() == {"executions": [], "total": 0}

    async def test_misfired_execution_carries_provenance(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/executions/{_MISFIRED_EXEC}")
        assert resp.status_code == 200
        execution = resp.json()["execution"]
        assert execution["state"] == "misfired"
        assert execution["raw_state"] == "EXECUTION_MISFIRED"
        assert execution["misfire_reason"] == "PARALLEL_EXECUTION_FORBIDDEN"
        assert execution["execution_type"] == "SCHEDULED"
        # A misfire never ran, so it has no end time and no duration.
        assert execution["ended_at"] is None
        assert execution["duration_ms"] is None

    async def test_timed_out_execution_has_no_misfire_reason(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/executions/{_TIMED_OUT_EXEC}")
        assert resp.status_code == 200
        execution = resp.json()["execution"]
        assert execution["state"] == "timed_out"
        assert execution["raw_state"] == "EXECUTION_TIMEOUT"
        # misfire_reason is misfire-specific; a timeout must not carry one.
        assert execution.get("misfire_reason") is None
        assert execution["execution_type"] == "SCHEDULED"
        assert execution["ended_at"] is not None

    async def test_job_run_mirrors_carry_the_same_provenance(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/job-runs?state=misfired")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert [r["run_id"] for r in runs] == [_MISFIRED_EXEC]
        assert runs[0]["misfire_reason"] == "PARALLEL_EXECUTION_FORBIDDEN"
        assert runs[0]["execution_type"] == "SCHEDULED"

    async def test_job_reliability_breaks_out_the_three_new_counters(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/job-reliability")
        assert resp.status_code == 200
        by_job = {job["job_name"]: job for job in resp.json()["jobs"]}
        misfired_job = by_job[_MISFIRED_JOB]
        assert misfired_job["misfired_runs"] == 1
        assert misfired_job["failed_runs"] == 0  # never counted as a failure
        timed_out_job = by_job[_TIMED_OUT_JOB]
        assert timed_out_job["timed_out_runs"] == 1
        assert timed_out_job["failed_runs"] == 0


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
        # engine-prod-02 was already unhealthy from the ORA-12170 failure;
        # engine-prod-01 joins it because the misfired/timed-out capture-state
        # jobs default onto it (see seed.py's job_context fallback) and both
        # states are attention-worthy (RuntimeStoreRepository._ATTENTION_STATES).
        assert overview["engines_unhealthy"] == 2
        # order-sync (failed) + the misfired and timed-out capture-state jobs.
        assert overview["jobs_unhealthy"] == 3
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


class TestOutcomeTrendEndpoint:
    async def test_returns_bucketed_points(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/trend?bucket=day")
        assert resp.status_code == 200
        body = resp.json()
        assert body["bucket"] == "day"
        assert body["total"] == len(body["points"])
        assert sum(point["total_runs"] for point in body["points"]) == 5
        assert sum(point["failed_runs"] for point in body["points"]) == 1
        # A misfire/timeout must never inflate `failed_runs` -- each has its
        # own counter (widened outcome vocabulary contract).
        assert sum(point["misfired_runs"] for point in body["points"]) == 1
        assert sum(point["timed_out_runs"] for point in body["points"]) == 1
        assert sum(point["terminated_runs"] for point in body["points"]) == 0
        assert body["coverage"]["observed_from"] is not None
        assert body["coverage"]["observed_until"] is not None
        assert body["coverage"]["completeness"] == "unknown"

    async def test_bucket_is_validated(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/trend?bucket=fortnight")
        assert resp.status_code == 422

    async def test_project_id_filter_cannot_widen_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/trend?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json()["points"] == []
        assert resp.json()["coverage"]["observed_from"] is None

    async def test_empty_scope_returns_empty(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/trend")
        assert resp.status_code == 200
        assert resp.json()["points"] == []
        assert resp.json()["coverage"] == {
            "observed_from": None,
            "observed_until": None,
            "completeness": "unknown",
        }


class TestJobReliabilityEndpoint:
    async def test_reports_per_job_reliability(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/job-reliability")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["jobs"])
        failing = next(job for job in body["jobs"] if job["failed_runs"] > 0)
        assert failing["current_failure_streak"] >= 1
        assert failing["latest_state"] == "failed"
        assert failing["latest_unfinished"] is False
        assert failing["p50_duration_ms"] is not None

    async def test_attention_only_returns_currently_unresolved_jobs(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/job-reliability?attention_only=true")
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        # Attention widens to FAILED, MISFIRED and TIMED_OUT -- never TERMINATED,
        # which the backend deliberately excludes (an operator already looked).
        assert all(
            job["latest_state"] in {"failed", "misfired", "timed_out"} or job["latest_unfinished"]
            for job in jobs
        )
        # The two states that used to hide as "unknown" must actually surface here.
        by_job = {job["job_name"]: job for job in jobs}
        assert by_job[_MISFIRED_JOB]["latest_state"] == "misfired"
        assert by_job[_MISFIRED_JOB]["misfired_runs"] == 1
        assert by_job[_TIMED_OUT_JOB]["latest_state"] == "timed_out"
        assert by_job[_TIMED_OUT_JOB]["timed_out_runs"] == 1

    async def test_pagination_keeps_total_honest(self, seeded_client):
        with _scope([_ESB]):
            full = await seeded_client.get("/api/v1/runtime/job-reliability")
            page = await seeded_client.get("/api/v1/runtime/job-reliability?limit=1")
        assert len(page.json()["jobs"]) == 1
        assert page.json()["total"] == full.json()["total"]

    async def test_project_id_filter_cannot_widen_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/job-reliability?project_id={_OTHER}")
        assert resp.json() == {"jobs": [], "total": 0}

    async def test_empty_scope_returns_empty(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/job-reliability")
        assert resp.json() == {"jobs": [], "total": 0}


class TestComponentAndThroughputEndpoints:
    async def test_component_hotspots_respond(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/component-hotspots")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["components"])

    async def test_job_throughput_responds(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/job-throughput")
        assert resp.status_code == 200
        assert resp.json()["total"] == len(resp.json()["jobs"])

    async def test_empty_scope_returns_empty(self, seeded_client):
        with _scope([]):
            hotspots = await seeded_client.get("/api/v1/runtime/component-hotspots")
            throughput = await seeded_client.get("/api/v1/runtime/job-throughput")
        assert hotspots.json() == {"components": [], "total": 0}
        assert throughput.json() == {"jobs": [], "total": 0}

    async def test_project_id_filter_cannot_widen_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/component-hotspots?project_id={_OTHER}"
            )
        assert resp.json() == {"components": [], "total": 0}


class TestErrorSignatureEndpoint:
    async def test_responds_with_signature_envelope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/error-signatures")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["signatures"])

    async def test_empty_scope_returns_empty(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/error-signatures")
        assert resp.json() == {"signatures": [], "total": 0}

    async def test_project_id_filter_cannot_widen_scope(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/error-signatures?project_id={_OTHER}")
        assert resp.json() == {"signatures": [], "total": 0}


class TestStuckRunFilters:
    async def test_min_duration_filter_narrows_and_keeps_total_honest(self, seeded_client):
        with _scope([_ESB]):
            baseline = await seeded_client.get("/api/v1/runtime/executions")
            filtered = await seeded_client.get(
                "/api/v1/runtime/executions?min_duration_ms=999999999"
            )
        assert baseline.json()["total"] == 5
        # No seeded run is anywhere near this long, so the page and its total agree at 0.
        assert filtered.json()["executions"] == []
        assert filtered.json()["total"] == 0

    async def test_unfinished_filter_is_accepted_and_consistent(self, seeded_client):
        with _scope([_ESB]):
            unfinished = await seeded_client.get("/api/v1/runtime/executions?unfinished=true")
            finished = await seeded_client.get("/api/v1/runtime/executions?unfinished=false")
        assert unfinished.status_code == 200
        assert finished.status_code == 200
        assert unfinished.json()["total"] + finished.json()["total"] == 5
        # MISFIRED is the only capture-state run with no ended_at (it never
        # started, so nothing could stamp an end) -- see
        # RuntimeStoreRepository._OPEN_ENDED_STATES.
        assert unfinished.json()["total"] == 1
        assert [e["execution_id"] for e in unfinished.json()["executions"]] == [_MISFIRED_EXEC]

    async def test_negative_min_duration_is_rejected(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/executions?min_duration_ms=-1")
        assert resp.status_code == 422


class TestHonestTotals:
    """`total` must be the full scoped match count, not the length of the page."""

    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/v1/runtime/job-runs", "runs"),
            ("/api/v1/runtime/plan-runs", "runs"),
            ("/api/v1/runtime/artifacts", "artifacts"),
            ("/api/v1/runtime/connections", "connections"),
            ("/api/v1/runtime/dependencies", "dependencies"),
        ],
    )
    async def test_first_page_total_matches_unpaginated_total(self, seeded_client, path, key):
        with _scope([_ESB]):
            full = await seeded_client.get(f"{path}?limit=500")
            page = await seeded_client.get(f"{path}?limit=1")
        assert full.status_code == 200
        assert page.status_code == 200
        expected = len(full.json()[key])
        assert full.json()["total"] == expected
        assert page.json()["total"] == expected
        if expected > 1:
            # The page is genuinely truncated while the total still reports everything.
            assert len(page.json()[key]) == 1

    @pytest.mark.parametrize(
        ("path", "key"),
        [
            ("/api/v1/runtime/job-runs", "runs"),
            ("/api/v1/runtime/plan-runs", "runs"),
            ("/api/v1/runtime/artifacts", "artifacts"),
            ("/api/v1/runtime/connections", "connections"),
            ("/api/v1/runtime/dependencies", "dependencies"),
        ],
    )
    async def test_offset_paging_is_supported(self, seeded_client, path, key):
        with _scope([_ESB]):
            first = await seeded_client.get(f"{path}?limit=1&offset=0")
            second = await seeded_client.get(f"{path}?limit=1&offset=1")
        assert first.status_code == 200
        assert second.status_code == 200
        if first.json()["total"] > 1:
            assert first.json()[key] != second.json()[key]


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
