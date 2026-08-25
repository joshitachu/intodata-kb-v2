"""RBAC + behavior tests for the runtime insight API endpoints (EP-006 slice 2).

Seeded data mirrors test_runtime_endpoints.py: two projects — `esbmain` (rich ESB
run with ORA-12170 failure) and `other-proj` (basic mock). Insight rows are built
via `build_insights=True` so all five new endpoint groups have data to assert.

Scope is controlled by patching `src.api.routes.runtime.get_user_project_ids`.
"""

import datetime
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Role, User
from src.runtime.store.seed import seed_mock_runtime_data, seed_rich_runtime_data

_SYNCED_AT = datetime.datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
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
    """Seed two projects; build insights for esbmain. Yield an ASGI client."""
    from src.api.dependencies import get_current_user, get_db

    await seed_rich_runtime_data(
        db_session, project_id=_ESB, synced_at=_SYNCED_AT, build_insights=True
    )
    await seed_mock_runtime_data(db_session, project_id=_OTHER, synced_at=_SYNCED_AT)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _mock_user()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


def _scope(value):
    return patch("src.api.routes.runtime.get_user_project_ids", AsyncMock(return_value=value))


class TestJobOperationalInsights:
    async def test_returns_bounded_dominant_diagnosis_and_coverage(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/job-insights?project_id={_ESB}&limit=500"
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["insights"]) == 2
        insight = next(
            item for item in body["insights"] if item["job_name"] == "esb_order_sync_route"
        )
        assert insight["job_name"] == "esb_order_sync_route"
        assert insight["total_runs"] == 2
        assert insight["failed_runs"] == 1
        assert insight["failures_with_logs"] == 1
        assert insight["diagnosed_failures"] == 1
        assert insight["evidence_completeness"] == "complete"
        assert insight["pattern_id"] == "oracle.net.timeout"
        assert insight["failed_component"] == "tOracleConnection_1"
        assert insight["causal_message"]
        assert insight["root_cause_candidate"]
        assert insight["next_checks"]
        assert "logs" not in insight

    async def test_out_of_scope_project_returns_no_data(self, seeded_client):
        with _scope([_OTHER]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/job-insights?project_id={_ESB}"
            )
        assert resp.status_code == 200
        assert resp.json() == {"insights": [], "total": 0}


# ---------------------------------------------------------------------------
# Execution summary
# ---------------------------------------------------------------------------


class TestExecutionSummary:
    async def test_failed_execution_has_expected_diagnostic_fields(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/summary"
            )
        assert resp.status_code == 200
        body = resp.json()["summary"]
        assert body["job_name"] == "esb_order_sync_route"
        assert body["failed_component"] == "tOracleConnection_1"
        assert body["primary_error_code"] == "ORA-12170"
        assert body["primary_exception_class"] == "SQLRecoverableException"
        assert body["warning_count"] > 0
        assert body["error_count"] > 0
        assert body["state"] == "failed"
        assert body["has_incident"] is True
        assert body["incident_id"] is not None

    async def test_success_execution_has_no_incident(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_SUCCESS_EXEC}/summary"
            )
        assert resp.status_code == 200
        body = resp.json()["summary"]
        assert body["error_count"] == 0
        assert body["has_incident"] is False
        assert body["incident_id"] is None
        assert body["state"] == "success"

    async def test_cross_project_summary_returns_404(self, seeded_client):
        with _scope([_OTHER]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/summary"
            )
        assert resp.status_code == 404

    async def test_unknown_execution_returns_404(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                "/api/v1/runtime/executions/does-not-exist/summary"
            )
        assert resp.status_code == 404

    async def test_empty_scope_returns_404(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/summary"
            )
        assert resp.status_code == 404

    async def test_evidence_refs_present(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/summary"
            )
        body = resp.json()["summary"]
        assert isinstance(body["evidence_refs"], list)
        assert len(body["evidence_refs"]) > 0

    async def test_failed_execution_has_estate_context(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/summary"
            )
        assert resp.status_code == 200
        context = resp.json()["summary"]["estate_context"]
        assert context["source_key"] == "mock"
        assert context["source_label"] == "Demo runtime source"
        assert context["source_is_demo"] is True
        assert context["environment_name"] == "Production"
        assert context["workspace_name"] == "ESB Main"
        assert context["engine_id"] == "engine-prod-02"
        assert context["engine_name"] == "remote-engine-prod-02"
        assert context["job_id"] == "job-order-sync"
        assert context["job_run_id"] == _FAILED_EXEC
        assert context["plan_id"] == "plan-nightly-order-flow"
        assert context["plan_run_id"] == "plan-nightly-order-flow-7841"


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


class TestTimeline:
    async def test_in_scope_timeline_returns_ordered_events(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/timeline"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["execution_id"] == _FAILED_EXEC
        assert body["total"] > 0
        timestamps = [e["timestamp"] for e in body["events"]]
        assert timestamps == sorted(timestamps)

    async def test_timeline_enriched_fields_present_on_error_lines(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/timeline?min_severity=error"
            )
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) > 0
        assert all("message" not in e for e in events)
        assert all("signal" in e for e in events)
        # At least one error-level event should have an extracted component
        components = [e["component_name"] for e in events if e["component_name"]]
        assert len(components) > 0
        # At least one event should have ORA-12170
        error_codes = [e["error_code"] for e in events if e["error_code"]]
        assert any("ORA-12170" in code for code in error_codes)

    async def test_min_severity_filters_out_lower_events(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/timeline?min_severity=error"
            )
        events = resp.json()["events"]
        assert all(e["severity"] == "error" for e in events)

    async def test_success_exec_with_error_min_severity_returns_empty_200(self, seeded_client):
        # Success execution has only info-level logs — filtering to error returns empty list,
        # not 404 (execution exists in scope).
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_SUCCESS_EXEC}/timeline?min_severity=error"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["events"] == []
        assert body["total"] == 0

    async def test_cross_project_timeline_returns_404(self, seeded_client):
        with _scope([_OTHER]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/timeline"
            )
        assert resp.status_code == 404

    async def test_unknown_execution_returns_404(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                "/api/v1/runtime/executions/no-such-exec/timeline"
            )
        assert resp.status_code == 404

    async def test_limit_over_max_is_422(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/executions/{_FAILED_EXEC}/timeline?limit=9999"
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Incident list
# ---------------------------------------------------------------------------


class TestIncidentList:
    async def test_scoped_user_sees_only_own_incidents(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/incidents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["incidents"][0]["execution_id"] == _FAILED_EXEC

    async def test_unrestricted_scope_sees_all_in_scope_incidents(self, seeded_client):
        with _scope(None):
            resp = await seeded_client.get("/api/v1/runtime/incidents?limit=200")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    async def test_project_id_filter_cannot_widen_scope(self, seeded_client):
        # User scoped to esbmain asks for other-proj — must return empty, not their incidents.
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents?project_id={_OTHER}")
        assert resp.status_code == 200
        assert resp.json()["incidents"] == []

    async def test_empty_scope_returns_empty(self, seeded_client):
        with _scope([]):
            resp = await seeded_client.get("/api/v1/runtime/incidents")
        assert resp.json() == {"incidents": [], "total": 0}

    async def test_incidents_have_job_name_and_state(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/incidents")
        incident = resp.json()["incidents"][0]
        assert incident["job_name"] == "esb_order_sync_route"
        assert incident["state"] == "failed"

    async def test_incident_offset_does_not_change_total(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/incidents?limit=1&offset=1")
        assert resp.status_code == 200
        assert resp.json()["incidents"] == []
        assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# Incident detail
# ---------------------------------------------------------------------------


class TestIncidentDetail:
    def _incident_id(self, seeded_client_fixture):
        return None  # obtained per-test via list endpoint

    async def _get_incident_id(self, seeded_client) -> str:
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/incidents")
        return resp.json()["incidents"][0]["incident_id"]

    async def test_incident_has_required_fields(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}")
        assert resp.status_code == 200
        body = resp.json()["incident"]
        assert body["incident_id"] == inc_id
        assert body["execution_id"] == _FAILED_EXEC
        assert body["project_id"] == _ESB
        assert body["signature_hash"] is not None
        assert body["confidence"] in ("high", "medium", "low")
        assert isinstance(body["next_checks"], list)
        assert len(body["next_checks"]) > 0

    async def test_root_cause_mentions_source_database_timeout(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}")
        candidate = resp.json()["incident"]["root_cause_candidate"].lower()
        assert "source database" in candidate
        assert "timeout" in candidate

    async def test_cross_project_incident_returns_404(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_OTHER]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}")
        assert resp.status_code == 404

    async def test_unknown_incident_returns_404(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/incidents/00000000000000000000000000000000")
        assert resp.status_code == 404

    async def test_empty_scope_returns_404(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Incident evidence
# ---------------------------------------------------------------------------


class TestIncidentEvidence:
    async def _get_incident_id(self, seeded_client) -> str:
        with _scope([_ESB]):
            resp = await seeded_client.get("/api/v1/runtime/incidents")
        return resp.json()["incidents"][0]["incident_id"]

    async def test_evidence_has_expected_structure(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}/evidence")
        assert resp.status_code == 200
        body = resp.json()
        assert body["incident_id"] == inc_id
        assert body["total"] > 0
        for ev in body["evidence"]:
            assert "log_event_id" in ev
            assert "timestamp" in ev
            assert "severity" in ev
            # evidence_role and excerpt may be None but must be present as keys
            assert "evidence_role" in ev
            assert "excerpt" in ev

    async def test_evidence_excerpts_are_bounded(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}/evidence")
        for ev in resp.json()["evidence"]:
            if ev["excerpt"]:
                assert len(ev["excerpt"]) <= 300

    async def test_evidence_has_severity_from_log_event(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_ESB]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}/evidence")
        severities = {ev["severity"] for ev in resp.json()["evidence"]}
        # Evidence for the ORA-12170 incident should include both warnings and errors
        assert "error" in severities

    async def test_cross_project_evidence_returns_404(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_OTHER]):
            resp = await seeded_client.get(f"/api/v1/runtime/incidents/{inc_id}/evidence")
        assert resp.status_code == 404

    async def test_unknown_incident_evidence_returns_404(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                "/api/v1/runtime/incidents/00000000000000000000000000000000/evidence"
            )
        assert resp.status_code == 404

    async def test_limit_over_max_is_422(self, seeded_client):
        inc_id = await self._get_incident_id(seeded_client)
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/incidents/{inc_id}/evidence?limit=999"
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    async def test_unauthenticated_summary_is_401(self, db_session):
        from src.api.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/v1/runtime/executions/{_FAILED_EXEC}/summary")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 401

    async def test_unauthenticated_incidents_is_401(self, db_session):
        from src.api.dependencies import get_db

        app.dependency_overrides[get_db] = lambda: db_session
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/runtime/incidents")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Similar failures
# ---------------------------------------------------------------------------


class TestSimilarFailures:
    async def _get_incident_id(self, client, scope_val) -> str:
        with _scope(scope_val):
            resp = await client.get("/api/v1/runtime/incidents?limit=5")
        assert resp.status_code == 200
        incidents = resp.json()["incidents"]
        assert len(incidents) > 0
        return incidents[0]["incident_id"]

    async def test_returns_recurrence_for_in_scope_incident(self, seeded_client):
        incident_id = await self._get_incident_id(seeded_client, [_ESB])
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/incidents/{incident_id}/similar"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["incident_id"] == incident_id
        assert "recurrence" in body
        assert "similar_incidents" in body
        r = body["recurrence"]
        assert isinstance(r["occurrence_count"], int)
        assert isinstance(r["is_recurring"], bool)
        assert len(r["alert_fingerprint"]) > 0
        assert len(r["dedupe_key"]) > 0

    async def test_cross_project_scope_returns_404(self, seeded_client):
        incident_id = await self._get_incident_id(seeded_client, [_ESB])
        with _scope([_OTHER]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/incidents/{incident_id}/similar"
            )
        assert resp.status_code == 404

    async def test_empty_scope_returns_404(self, seeded_client):
        incident_id = await self._get_incident_id(seeded_client, [_ESB])
        with _scope([]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/incidents/{incident_id}/similar"
            )
        assert resp.status_code == 404

    async def test_unknown_incident_returns_404(self, seeded_client):
        with _scope([_ESB]):
            resp = await seeded_client.get(
                "/api/v1/runtime/incidents/deadbeefdeadbeefdeadbeefdeadbeef/similar"
            )
        assert resp.status_code == 404

    async def test_oversized_limit_returns_422(self, seeded_client):
        incident_id = await self._get_incident_id(seeded_client, [_ESB])
        with _scope([_ESB]):
            resp = await seeded_client.get(
                f"/api/v1/runtime/incidents/{incident_id}/similar?limit=100"
            )
        assert resp.status_code == 422
