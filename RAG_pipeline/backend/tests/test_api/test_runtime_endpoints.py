"""RBAC + behavior tests for the read-only runtime logs API (EP-005 slice 2).

Two projects are seeded into a real SQLite session: `esbmain` (rich ESB run with the
ORA-12170 failure) and `other-proj` (basic mock fixtures). Project scope is controlled
by patching `get_user_project_ids` so each test asserts the route threads the
authenticated scope into the repository and never widens it from a query param.

`TestSharedReadGrant` covers the other direction, added when the router moved onto
`RuntimeRead` / `RuntimeReadById`: what a project reading a SHARED runtime mirror
through `runtime_source_read_grants` may see. The properties there are the ones a
grant is either worthless or dangerous without -- a granted reader gets the
grantor's rows AND its own in full, a narrowed grant does not leak the
environments or the history it excluded, the drill-down reaches the rows the list
just showed, an ungranted project is byte-identical to the predicate it had
before any of this existed, and a read grant authorizes no write.
"""

import contextlib
import datetime
import json
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.api.routes.runtime import ExecutionListResponse
from src.db.models import Role, User
from src.runtime.models import (
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
)
from src.runtime.store import RuntimeStoreRepository
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


@contextlib.contextmanager
def _scope(value):
    """Pin the authenticated project scope, wherever the handler reads it.

    Two bindings, because the router has two resolution points by design: the
    `RuntimeRead` / `RuntimeReadById` dependencies in `src.api.dependencies`
    resolve the read set for every endpoint whose repository call can carry
    `read_terms`, and `src.api.routes.runtime` still calls `get_user_project_ids`
    directly for the lanes that must NOT be widened (writes, `/sources`,
    `/job-insights`, `/job-scan-maps`, the incident family). Patching one alone
    would leave half the router reading a real database through a MagicMock user.
    """
    with (
        patch("src.api.dependencies.get_user_project_ids", AsyncMock(return_value=value)),
        patch("src.api.routes.runtime.get_user_project_ids", AsyncMock(return_value=value)),
    ):
        yield


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
        # `project_id` is gone from this payload: what the row holds is the
        # stamp its mirrored data carries, which is what a scope is matched
        # against, and it is now published under that name.
        assert body["sources"][0]["estate_key"] == _ESB
        assert body["sources"][0]["is_ownerless"] is False
        # A row written by a sync batch rather than registered: its estate key
        # names a project, but no attachment row records one. Empty is the
        # truthful answer, not a missing lookup.
        assert body["sources"][0]["linked_project_ids"] == []
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
        # Every row now says what it IS and what one of its rows MEANS. The
        # fixture is a mock source, whose provider declares `job: execution`.
        assert order_health["executable_kind"] == "job"
        assert order_health["liveness"] == "execution"
        # The design the runtime row was deployed from, carried off the
        # inventory row so a consumer can join on it instead of guessing.
        assert order_health["artifact_name"] == "esb-order-sync-1.8.4"

    async def test_job_health_defaults_to_every_executable_kind(self, seeded_client):
        """The default is ALL kinds; a kind is only excluded when asked for.

        The fixture estate is all `job`-kind, so this pins the CONTRACT rather
        than a count -- `tests/test_runtime/test_job_health_semantics.py` holds
        the multi-kind estate. A request naming a kind must still narrow.
        """
        with _scope([_ESB]):
            everything = await seeded_client.get("/api/v1/runtime/job-health")
            jobs_only = await seeded_client.get(
                "/api/v1/runtime/job-health?executable_kind=job"
            )
            routes_only = await seeded_client.get(
                "/api/v1/runtime/job-health?executable_kind=route"
            )
        assert everything.status_code == 200
        assert jobs_only.json() == everything.json()
        assert routes_only.json() == {"health": [], "total": 0}

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


# ---------------------------------------------------------------------------
# Shared reads through a stored grant
# ---------------------------------------------------------------------------

_GRANTOR = "proj-grantor"
_READER = "proj-reader"
_SHARED_SRC = "tmc"
_PROD = "env-prod"
_ACC = "env-acc"
_NOW = datetime.datetime.now(UTC)
_RECENT = _NOW - datetime.timedelta(days=1)
#: Older than any grant window used here, so a window can be shown to bite.
_ANCIENT = _NOW - datetime.timedelta(days=200)

_GRANTOR_PROD_EXEC = "grantor-prod-1"
_GRANTOR_ACC_EXEC = "grantor-acc-1"
_GRANTOR_ANCIENT_EXEC = "grantor-prod-ancient"
_READER_EXEC = "reader-acc-1"

#: CSRF double-submit pair. The middleware is on in tests, so a write without it
#: is a 403 and would pass a "writes are refused" test for the wrong reason.
_CSRF = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}

#: Every store method the execution/failure/log endpoints reach. Their inputs
#: carry no `project_id`, so `read_terms` is the ONLY thing that narrows a
#: granting project's contribution once `read.scope` has been widened to include
#: it -- which is what `test_no_widened_scope_reaches_the_store_without_its_terms`
#: pins.
_TERM_BEARING_READS = (
    "list_executions",
    "count_executions",
    "get_execution_details",
    "detect_failures",
    "count_failures",
    "get_execution_logs",
    "count_execution_logs",
)


async def _mirror_run(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    run_id: str,
    job_name: str,
    environment_id: str,
    started_at: datetime.datetime,
    message: str,
) -> None:
    """One run as the sync writer records it: job run + execution + log line.

    All three carry the same id because the TMC provider derives the execution
    FROM the job run. That identity is what lets an environment-narrowed grant
    reach `runtime_executions` and `runtime_log_events`, neither of which has an
    environment column of its own.
    """
    await store.upsert_job_run(
        project_id,
        RuntimeJobRun(
            run_id=run_id,
            job_id=f"job-{job_name}",
            job_name=job_name,
            source_key=_SHARED_SRC,
            execution_id=run_id,
            state=RuntimeExecutionState.FAILED,
            environment_id=environment_id,
            environment_name=environment_id.upper(),
            started_at=started_at,
        ),
        _SHARED_SRC,
        _RECENT,
    )
    await store.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=run_id,
            job_name=job_name,
            state=RuntimeExecutionState.FAILED,
            started_at=started_at,
            ended_at=started_at + datetime.timedelta(seconds=1),
            duration_ms=1000,
        ),
        _SHARED_SRC,
        _RECENT,
    )
    await store.upsert_log_event(
        project_id,
        RuntimeLogEvent(
            execution_id=run_id,
            timestamp=started_at,
            severity=RuntimeLogSeverity.ERROR,
            message=message,
            line_ordinal=0,
        ),
        _SHARED_SRC,
        _RECENT,
    )


@pytest.fixture
async def shared_client(db_session):
    """A grantor tenant with PROD and ACC rows, plus the reader's own ACC mirror.

    No grant is created here: each test adds the grant it needs, so the same
    seeded estate answers "ungranted", "granted" and "granted but narrowed".
    """
    from src.api.dependencies import get_current_user, get_db

    store = RuntimeStoreRepository(db_session)
    for project_id, job_name, environment_id in (
        (_GRANTOR, "Grantor_Prod_Job", _PROD),
        (_GRANTOR, "Grantor_Acc_Job", _ACC),
        (_READER, "Reader_Acc_Job", _ACC),
    ):
        await store.upsert_job(
            project_id,
            RuntimeJob(
                job_id=f"job-{job_name}",
                source_key=_SHARED_SRC,
                name=job_name,
                environment_id=environment_id,
                environment_name=environment_id.upper(),
            ),
            _SHARED_SRC,
            _RECENT,
        )
    await _mirror_run(
        store,
        project_id=_GRANTOR,
        run_id=_GRANTOR_PROD_EXEC,
        job_name="Grantor_Prod_Job",
        environment_id=_PROD,
        started_at=_RECENT,
        message="grantor prod line",
    )
    await _mirror_run(
        store,
        project_id=_GRANTOR,
        run_id=_GRANTOR_ACC_EXEC,
        job_name="Grantor_Acc_Job",
        environment_id=_ACC,
        started_at=_RECENT,
        message="grantor acc line",
    )
    await _mirror_run(
        store,
        project_id=_GRANTOR,
        run_id=_GRANTOR_ANCIENT_EXEC,
        job_name="Grantor_Prod_Job",
        environment_id=_PROD,
        started_at=_ANCIENT,
        message="grantor prod line from long ago",
    )
    await _mirror_run(
        store,
        project_id=_READER,
        run_id=_READER_EXEC,
        job_name="Reader_Acc_Job",
        environment_id=_ACC,
        started_at=_RECENT,
        message="reader acc line",
    )

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _grant(
    db_session, *, environment_ids: list[str] | None = None, window_days: int | None = None
) -> None:
    """Give READER a stored read grant over a source owned by GRANTOR."""
    store = RuntimeStoreRepository(db_session)
    source = await store.create_source_config(
        project_id=_GRANTOR, source_type="mock", label="Shared tenant"
    )
    await store.add_read_grant(
        source_id=source.id,
        project_id=_READER,
        environment_ids=environment_ids,
        window_days=window_days,
        created_by="1",
    )


@contextlib.contextmanager
def _recording_store():
    """Record `(method, scope, params)` for every term-bearing store read.

    A behavioural test can only catch a dropped `read_terms=` when the fixture
    happens to contain a row the grant excludes. This records the CALL instead,
    so the invariant is checked directly on every endpoint it drives.
    """
    calls: list[tuple[str, object, object]] = []

    def wrap(name: str):
        original = getattr(RuntimeStoreRepository, name)

        async def recorded(self, scope, params, *args, **kwargs):
            calls.append((name, scope, params))
            return await original(self, scope, params, *args, **kwargs)

        return patch.object(RuntimeStoreRepository, name, recorded)

    with contextlib.ExitStack() as stack:
        for name in _TERM_BEARING_READS:
            stack.enter_context(wrap(name))
        yield calls


class TestSharedReadGrant:
    async def test_granted_reader_sees_the_grantors_rows_and_all_of_its_own(
        self, shared_client, db_session
    ):
        await _grant(db_session)
        with _scope([_READER]):
            resp = await shared_client.get(
                f"/api/v1/runtime/executions?project_id={_READER}&limit=200"
            )
        body = resp.json()

        assert resp.status_code == 200
        assert {item["execution_id"] for item in body["executions"]} == {
            _GRANTOR_PROD_EXEC,
            _GRANTOR_ACC_EXEC,
            _GRANTOR_ANCIENT_EXEC,
            _READER_EXEC,
        }
        # A page whose total describes a different population is a total nobody
        # can page to.
        assert body["total"] == len(body["executions"])

    async def test_a_prod_grant_does_not_return_the_grantors_acc_executions(
        self, shared_client, db_session
    ):
        """The three assertions one flat environment filter cannot satisfy at once."""
        await _grant(db_session, environment_ids=[_PROD])
        with _scope([_READER]):
            resp = await shared_client.get(
                f"/api/v1/runtime/executions?project_id={_READER}&limit=200"
            )
        ids = {item["execution_id"] for item in resp.json()["executions"]}

        assert _GRANTOR_PROD_EXEC in ids
        # A flat union of the environment selections would keep this one.
        assert _GRANTOR_ACC_EXEC not in ids
        # A flat intersection would drop the reader's OWN acc row.
        assert _READER_EXEC in ids

    async def test_a_prod_grant_does_not_return_the_grantors_acc_failures(
        self, shared_client, db_session
    ):
        await _grant(db_session, environment_ids=[_PROD])
        with _scope([_READER]):
            resp = await shared_client.get(
                f"/api/v1/runtime/failures?project_id={_READER}&limit=200"
            )
        body = resp.json()

        assert body["total"] == len(body["failures"])
        assert _GRANTOR_ACC_EXEC not in {item["execution_id"] for item in body["failures"]}

    async def test_a_prod_grant_does_not_expose_the_grantors_acc_log_lines(
        self, shared_client, db_session
    ):
        """An execution id is not secret: the same id exists in the grantor's mirror."""
        await _grant(db_session, environment_ids=[_PROD])
        with _scope([_READER]):
            excluded = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_ACC_EXEC}/logs"
            )
            included = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}/logs"
            )

        assert excluded.status_code == 404
        assert included.status_code == 200
        assert [event["message"] for event in included.json()["events"]] == [
            "grantor prod line"
        ]

    async def test_a_window_grant_bounds_the_history_it_returns(
        self, shared_client, db_session
    ):
        await _grant(db_session, window_days=30)
        with _scope([_READER]):
            resp = await shared_client.get(
                f"/api/v1/runtime/executions?project_id={_READER}&limit=200"
            )
        ids = {item["execution_id"] for item in resp.json()["executions"]}

        assert _GRANTOR_PROD_EXEC in ids
        assert _GRANTOR_ANCIENT_EXEC not in ids
        # The reader's own term carries no window at all.
        assert _READER_EXEC in ids

    async def test_the_drill_down_reaches_a_granted_row_by_id(
        self, shared_client, db_session
    ):
        """The list, the detail and the logs must agree, or the feature dead-ends."""
        await _grant(db_session, environment_ids=[_PROD])
        with _scope([_READER]):
            detail = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}"
            )
            logs = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}/logs"
            )
            correlation = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}/correlation"
            )

        assert detail.status_code == 200
        assert detail.json()["execution"]["execution_id"] == _GRANTOR_PROD_EXEC
        assert logs.status_code == 200
        assert logs.json()["total"] == 1
        assert correlation.status_code == 200
        assert correlation.json()["execution_id"] == _GRANTOR_PROD_EXEC

    async def test_without_a_grant_the_drill_down_still_404s(self, shared_client):
        with _scope([_READER]):
            detail = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}"
            )
            logs = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}/logs"
            )
            correlation = await shared_client.get(
                f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}/correlation"
            )

        assert detail.status_code == 404
        assert logs.status_code == 404
        assert correlation.status_code == 404

    async def test_an_ungranted_project_is_byte_identical_to_the_old_predicate(
        self, shared_client, db_session
    ):
        """No grant means no change -- in the body, not merely in the row ids."""
        with _scope([_READER]):
            resp = await shared_client.get(
                f"/api/v1/runtime/executions?project_id={_READER}"
            )

        repo = RuntimeStoreRepository(db_session)
        # Exactly the call the route made before read scopes existed: the
        # intersected scope, and no read terms anywhere.
        params = ListExecutionsInput(limit=50, offset=0)
        expected = ExecutionListResponse(
            executions=await repo.list_executions([_READER], params),
            total=await repo.count_executions([_READER], params),
        )

        assert resp.json() == json.loads(expected.model_dump_json())
        assert [item["execution_id"] for item in resp.json()["executions"]] == [
            _READER_EXEC
        ]

    async def test_no_widened_scope_reaches_the_store_without_its_terms(
        self, shared_client, db_session
    ):
        """The guard against the one mistake this design cannot survive.

        `read.scope` is WIDENED to include the granting project while the grant's
        environment and window live in `read.terms`, so a handler that passes the
        first without the second returns the grantor's rows with the grant's own
        narrowing switched off. Every call recorded below must therefore name, in
        its terms, exactly the projects its scope allows.

        Only project-named and by-id requests are driven: a request that names no
        project is DEFINED as unwidened (no terms, unwidened scope), so including
        one here would assert the opposite of the contract.
        """
        await _grant(db_session, environment_ids=[_PROD], window_days=30)
        paths = (
            f"/api/v1/runtime/executions?project_id={_READER}",
            f"/api/v1/runtime/failures?project_id={_READER}",
            f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}",
            f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}/logs",
            f"/api/v1/runtime/executions/{_GRANTOR_PROD_EXEC}/correlation",
        )
        with _scope([_READER]), _recording_store() as calls:
            for path in paths:
                assert (await shared_client.get(path)).status_code == 200

        assert {name for name, _scope_arg, _params in calls} == set(_TERM_BEARING_READS)
        for name, scope_arg, params in calls:
            assert params.read_terms, (
                f"{name} was called with scope={scope_arg} and no read_terms: "
                "the grant's environment and window are not being applied"
            )
            assert [term.project_id for term in params.read_terms] == list(scope_arg), (
                f"{name} was called with a scope its terms do not cover"
            )

    async def test_a_read_grant_does_not_authorize_a_write(
        self, shared_client, db_session
    ):
        """Read permission is not write permission, however wide the read got."""
        await _grant(db_session)
        with _scope([_READER]):
            created = await shared_client.post(
                "/api/v1/runtime/job-scan-maps",
                headers=_CSRF,
                json={
                    "project_id": _GRANTOR,
                    "source_key": _SHARED_SRC,
                    "runtime_job_id": "job-Grantor_Prod_Job",
                    "static_job_id": "static-1",
                },
            )
            deleted = await shared_client.delete(
                f"/api/v1/runtime/job-scan-maps/{_GRANTOR}/{_SHARED_SRC}/job-Grantor_Prod_Job",
                headers=_CSRF,
            )

        assert created.status_code == 404
        assert deleted.status_code == 404

    async def test_a_read_grant_does_not_widen_the_source_configuration_list(
        self, shared_client, db_session
    ):
        """`/sources` is the registration surface, not the mirror inventory."""
        await _grant(db_session)
        with _scope([_READER]):
            resp = await shared_client.get(f"/api/v1/runtime/sources?project_id={_READER}")

        assert resp.status_code == 200
        assert resp.json() == {"sources": [], "total": 0}


class TestDocReadScopeEndpoint:
    """`GET /doc-read-scope`: the picker an operator configures a scope in.

    It is the read-side companion of the documentation lane, so it has to
    describe the same read that lane will perform. Two ways it can be wrong and
    both are silent: offering an environment a grant excludes produces a
    selection that resolves to no evidence, and reporting a narrowed grant as if
    it were the flat scope publishes a bounded read as a whole one.
    """

    _URL = "/api/v1/runtime/doc-read-scope"

    async def _offered(self, client, project_id: str) -> dict:
        resp = await client.get(f"{self._URL}?project_id={project_id}")
        assert resp.status_code == 200
        return resp.json()

    async def test_an_ungranted_project_is_offered_only_its_own_mirror(
        self, shared_client
    ):
        with _scope([_READER]):
            body = await self._offered(shared_client, _READER)

        assert {item["environment_id"] for item in body["available_environments"]} == {
            _ACC
        }
        assert body["scope"]["shared_read"] is False
        assert body["scope"]["grant_limits"] == []

    async def test_a_grant_offers_the_granted_tenants_environments(
        self, shared_client, db_session
    ):
        await _grant(db_session)
        with _scope([_READER]):
            body = await self._offered(shared_client, _READER)

        assert {item["environment_id"] for item in body["available_environments"]} == {
            _ACC,
            _PROD,
        }

    async def test_a_narrowed_grant_stops_offering_what_it_excludes(
        self, shared_client, db_session
    ):
        """An ACC-only grant must not offer the grantor's PROD environment."""
        await _grant(db_session, environment_ids=[_ACC])
        with _scope([_READER]):
            body = await self._offered(shared_client, _READER)

        assert {item["environment_id"] for item in body["available_environments"]} == {
            _ACC
        }

    async def test_a_narrowed_grant_does_not_count_the_runs_it_excludes(
        self, shared_client, db_session
    ):
        """The reader and the grantor share an environment ID.

        A set assertion cannot see the leak when it does: `env-acc` is offered
        either way. The run count is what distinguishes "your own ACC" from
        "your own ACC plus the grantor's", and it is published alongside the id.
        """
        await _grant(db_session, environment_ids=[_PROD])
        with _scope([_READER]):
            body = await self._offered(shared_client, _READER)

        counts = {
            item["environment_id"]: item["run_count"]
            for item in body["available_environments"]
        }
        # The reader's own ACC run, and NOT the grantor's.
        assert counts[_ACC] == 1
        # Both of the grantor's PROD runs, including the 200-day-old one: this
        # grant narrows by environment and carries no window at all.
        assert counts[_PROD] == 2

    async def test_the_scope_record_names_the_grants_limits(
        self, shared_client, db_session
    ):
        await _grant(db_session, environment_ids=[_PROD], window_days=30)
        with _scope([_READER]):
            body = await self._offered(shared_client, _READER)

        limits = body["scope"]["grant_limits"]
        assert [limit["project_id"] for limit in limits] == [_GRANTOR]
        assert limits[0]["environment_ids"] == [_PROD]
        assert body["scope"]["shared_read"] is True

    async def test_a_project_outside_the_callers_scope_is_not_found(
        self, shared_client, db_session
    ):
        """A grant widens what the reader READS; it is not a directory of the
        grantor's projects to browse."""
        await _grant(db_session)
        with _scope([_READER]):
            resp = await shared_client.get(f"{self._URL}?project_id={_GRANTOR}")

        assert resp.status_code == 404


class TestRuntimeReadDependency:
    """The FastAPI contract `RuntimeRead` is built on, pinned rather than assumed.

    `get_runtime_read_scope` declares `project_id: str | None = None`, so every
    endpoint using it gains an optional query parameter. The router relies on two
    consequences of that, and both are framework behaviour rather than anything
    this codebase controls: a handler declaring its OWN `project_id: str` keeps
    its own requiredness (the dependency's default must not make it optional),
    and the dependency is handed the same value the handler is. If either ever
    stopped holding, an endpoint would silently start resolving its read set for
    a different project than the one it filters on.

    Exercised on a throwaway app: the claim is about the dependency, and pinning
    it to whichever route happens to declare a required `project_id` today would
    make it disappear the moment that route changed.
    """

    @pytest.fixture
    async def probe_client(self, db_session, shared_client):
        from fastapi import FastAPI

        from src.api.dependencies import RuntimeRead, get_current_user, get_db

        probe = FastAPI()

        @probe.get("/required")
        async def _required(read: RuntimeRead, project_id: str) -> dict:
            return {
                "project_id": project_id,
                "scope": read.scope,
                "terms": [term.project_id for term in read.terms],
            }

        @probe.get("/optional")
        async def _optional(read: RuntimeRead) -> dict:
            return {
                "scope": read.scope,
                "terms": [term.project_id for term in read.terms],
            }

        probe.dependency_overrides[get_db] = lambda: db_session
        probe.dependency_overrides[get_current_user] = lambda: _mock_user()
        transport = ASGITransport(app=probe)
        async with AsyncClient(transport=transport, base_url="http://probe") as client:
            yield client

    async def test_a_route_declaring_project_id_keeps_its_own_requiredness(
        self, probe_client
    ):
        with _scope([_READER]):
            resp = await probe_client.get("/required")
        assert resp.status_code == 422

    async def test_the_dependency_sees_the_same_project_the_route_does(
        self, probe_client, db_session
    ):
        await _grant(db_session)
        with _scope([_READER]):
            resp = await probe_client.get(f"/required?project_id={_READER}")
        body = resp.json()

        assert resp.status_code == 200
        assert body["project_id"] == _READER
        # Resolved FROM that value: the reader first, then what its grant exposes.
        assert body["terms"] == [_READER, _GRANTOR]
        assert body["scope"] == [_READER, _GRANTOR]

    async def test_naming_no_project_is_not_widened(self, probe_client, db_session):
        await _grant(db_session)
        with _scope([_READER]):
            resp = await probe_client.get("/optional")

        assert resp.json() == {"scope": [_READER], "terms": []}


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
