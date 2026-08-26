"""Route-level contract for `GET /api/v1/runtime/doc-evidence`.

Nothing pinned this endpoint before: `grep verify_machine_topology_ingest_api_key
tests/` returned zero hits, while the documentation generator depends on every
property below.  Two of them are load-bearing in a way a repository test cannot
reach:

* the caller authenticates with a MACHINE key, and the key is empty in the
  mcp-server container today -- dev allows keyless, production must not, and the
  consumer swallows every exception, so a regression here is a silent and
  permanent absence of runtime evidence on an otherwise green run;
* scope is pinned to the requested `project_id` rather than left unrestricted,
  because a machine caller has no user scope to derive.  Two projects that run a
  job of the same name are seeded so that a scope leak is visible as a wrong
  number, not just a missing filter.

The bounds tests exist because exceeding `_DOC_EVIDENCE_MAX_UNITS` returns 422
and the client converts a 422 into "no runtime data for the entire project" --
so the cap is part of the contract the client batches against, not an
implementation detail.
"""

import datetime
from datetime import UTC

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.api.routes.runtime import _DOC_EVIDENCE_MAX_UNITS
from src.config import settings
from src.runtime.models import (
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJobRun,
)
from src.runtime.store import RuntimeStoreRepository

_URL = "/api/v1/runtime/doc-evidence"
_P = "proj-docs"
_OTHER = "proj-other"
_SRC = "tmc"
_T0 = datetime.datetime.now(UTC) - datetime.timedelta(days=2)
_KEY = "machine-key-under-test"

# Present in BOTH projects: a scope leak shows up as the wrong run count.
_SHARED_JOB = "AssetCheck_GetLocation"
_ORPHAN_JOB = "Unscanned_NightlyRollup"


async def _run(
    store: RuntimeStoreRepository,
    *,
    project_id: str,
    execution_id: str,
    job_name: str,
    state: str = "success",
    offset_hours: int = 0,
) -> None:
    started = _T0 + datetime.timedelta(hours=offset_hours)
    await store.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState(state),
            started_at=started,
            ended_at=started + datetime.timedelta(milliseconds=250),
            duration_ms=250,
        ),
        _SRC,
        _T0,
    )


@pytest.fixture
async def client(db_session):
    """Two projects, one shared job name, machine auth left keyless by default."""
    from src.api.dependencies import get_db

    store = RuntimeStoreRepository(db_session)
    # proj-docs: two runs of the shared job, one run of a job nothing scanned.
    await _run(store, project_id=_P, execution_id="p-1", job_name=_SHARED_JOB)
    await _run(store, project_id=_P, execution_id="p-2", job_name=_SHARED_JOB, offset_hours=1)
    await _run(store, project_id=_P, execution_id="p-3", job_name=_ORPHAN_JOB)
    # proj-other: five runs of the SAME job name plus a failure of its own.
    for index in range(5):
        await _run(
            store,
            project_id=_OTHER,
            execution_id=f"o-{index}",
            job_name=_SHARED_JOB,
            state="failed",
            offset_hours=index,
        )
        await store.upsert_failure(
            _OTHER,
            RuntimeFailure(
                execution_id=f"o-{index}",
                job_name=_SHARED_JOB,
                failed_at=_T0 + datetime.timedelta(hours=index),
                error_summary="OTHER-PROJECT-ONLY marker",
            ),
            _SRC,
            _T0,
        )

    app.dependency_overrides[get_db] = lambda: db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def keyless_dev(monkeypatch):
    """Default posture for these tests: local dev, no machine key configured."""
    monkeypatch.setattr(settings, "mcp_ingest_api_key", "", raising=False)
    monkeypatch.setattr(settings, "app_env", "development", raising=False)


def _params(**overrides) -> dict:
    params = {"project_id": _P, "units": [_SHARED_JOB]}
    params.update(overrides)
    return params


class TestMachineAuth:
    async def test_keyless_dev_is_allowed(self, client):
        resp = await client.get(_URL, params=_params())
        assert resp.status_code == 200

    async def test_keyless_non_dev_is_disabled_not_open(self, client, monkeypatch):
        """Staging has no production-startup validation; the request-time gate
        must still fail closed rather than serve runtime data anonymously."""
        monkeypatch.setattr(settings, "app_env", "staging", raising=False)
        resp = await client.get(_URL, params=_params())
        assert resp.status_code == 503

    async def test_configured_key_rejects_a_missing_header(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(_URL, params=_params())
        assert resp.status_code == 401

    async def test_configured_key_rejects_a_wrong_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(
            _URL, params=_params(), headers={"Authorization": "Bearer not-the-key"}
        )
        assert resp.status_code == 401

    async def test_configured_key_rejects_a_wrong_scheme(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(_URL, params=_params(), headers={"Authorization": f"Basic {_KEY}"})
        assert resp.status_code == 401

    async def test_configured_key_accepts_the_right_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "mcp_ingest_api_key", _KEY, raising=False)
        resp = await client.get(
            _URL, params=_params(), headers={"Authorization": f"Bearer {_KEY}"}
        )
        assert resp.status_code == 200


class TestRequestBounds:
    async def test_unit_cap_is_enforced(self, client):
        """One over the cap is a 422 the client must batch around, not absorb."""
        units = [f"Unit_{index}" for index in range(_DOC_EVIDENCE_MAX_UNITS + 1)]
        resp = await client.get(_URL, params=_params(units=units))
        assert resp.status_code == 422

    async def test_the_cap_itself_is_accepted(self, client):
        units = [f"Unit_{index}" for index in range(_DOC_EVIDENCE_MAX_UNITS)]
        resp = await client.get(_URL, params=_params(units=units))
        assert resp.status_code == 200

    async def test_units_are_required(self, client):
        resp = await client.get(_URL, params={"project_id": _P})
        assert resp.status_code == 422

    async def test_project_id_is_required(self, client):
        resp = await client.get(_URL, params={"units": [_SHARED_JOB]})
        assert resp.status_code == 422

    @pytest.mark.parametrize("window_days", [0, -1, 366])
    async def test_window_days_out_of_range(self, client, window_days):
        resp = await client.get(_URL, params=_params(window_days=window_days))
        assert resp.status_code == 422

    @pytest.mark.parametrize("window_days", [1, 90, 365])
    async def test_window_days_in_range(self, client, window_days):
        resp = await client.get(_URL, params=_params(window_days=window_days))
        assert resp.status_code == 200
        assert resp.json()["window_days"] == window_days

    @pytest.mark.parametrize("max_failures", [-1, 21])
    async def test_max_failures_out_of_range(self, client, max_failures):
        resp = await client.get(_URL, params=_params(max_failures_per_unit=max_failures))
        assert resp.status_code == 422

    @pytest.mark.parametrize("max_failures", [0, 5, 20])
    async def test_max_failures_in_range(self, client, max_failures):
        resp = await client.get(_URL, params=_params(max_failures_per_unit=max_failures))
        assert resp.status_code == 200


class TestScopePinning:
    async def test_only_the_requested_project_is_measured(self, client):
        """`proj-other` ran the same job name five times, all failures."""
        resp = await client.get(_URL, params=_params())
        unit = resp.json()["units"][0]
        assert unit["total_runs"] == 2
        assert unit["failed_runs"] == 0
        assert unit["recent_failures"] == []

    async def test_another_projects_failure_text_never_appears(self, client):
        resp = await client.get(_URL, params=_params())
        assert "OTHER-PROJECT-ONLY" not in resp.text

    async def test_a_unit_that_only_exists_elsewhere_is_unmatched(self, client):
        resp = await client.get(_URL, params=_params(project_id="proj-nothing-here"))
        body = resp.json()
        assert body["units"] == []
        assert body["unmatched_units"] == [_SHARED_JOB]
        assert body["orphan_runtime_jobs"] == []


class TestSharedRead:
    """Scope widens through a stored grant, and through nothing else.

    This endpoint authenticates with a machine key and has no user scope behind
    it, so the grant row is the ONLY gate. A caller that could name its own read
    set would be reading anything it liked.
    """

    async def test_no_grant_means_no_widening(self, client, db_session):
        resp = await client.get(_URL, params=_params())
        body = resp.json()

        assert body["units"][0]["total_runs"] == 2
        assert body["read_scope"]["shared_read"] is False
        assert body["read_scope"]["read_project_ids"] == [_P]

    async def test_a_grant_widens_the_read_to_the_granted_project(self, client, db_session):
        store = RuntimeStoreRepository(db_session)
        source = await store.create_source_config(
            project_id=_OTHER, source_type="mock", label="Shared tenant"
        )
        await store.add_read_grant(
            source_id=source.id,
            project_id=_P,
            environment_ids=None,
            window_days=None,
            created_by="1",
        )

        resp = await client.get(_URL, params=_params())
        body = resp.json()

        # 2 runs of its own + the 5 the granting project mirrored.
        assert body["units"][0]["total_runs"] == 7
        assert body["read_scope"]["shared_read"] is True
        assert body["read_scope"]["shared_from"] == ["Shared tenant"]
        assert body["read_scope"]["read_project_ids"] == [_P, _OTHER]

    async def test_a_caller_cannot_name_its_own_read_set(self, client):
        """The widening parameter does not exist, and adding one is inert.

        Pinned as a test rather than left to code review: this endpoint has no
        second gate, so a future signature change that accepted a project list
        would be an authorization bypass with nothing behind it.
        """
        resp = await client.get(
            _URL,
            params={
                "project_id": _P,
                "units": [_SHARED_JOB],
                "project_ids": [_P, _OTHER],
                "read_project_ids": [_OTHER],
            },
        )
        body = resp.json()

        assert resp.status_code == 200
        assert body["units"][0]["total_runs"] == 2
        assert body["read_scope"]["read_project_ids"] == [_P]
        assert "OTHER-PROJECT-ONLY" not in resp.text


class TestResponseShape:
    async def test_orphan_runtime_jobs_are_reported(self, client):
        resp = await client.get(_URL, params=_params())
        assert resp.json()["orphan_runtime_jobs"] == [_ORPHAN_JOB]

    async def test_full_coverage_reports_no_orphans(self, client):
        resp = await client.get(_URL, params=_params(units=[_SHARED_JOB, _ORPHAN_JOB]))
        body = resp.json()
        assert body["orphan_runtime_jobs"] == []
        assert body["unmatched_units"] == []

    async def test_a_failure_carries_three_aggregate_fields_and_nothing_else(self, client):
        """Aggregates only: the failure record IS the whole redaction surface.

        Asserted against `proj-other`, the seeded project that actually fails,
        so the assertion runs over a populated list rather than an empty one.
        """
        resp = await client.get(_URL, params=_params(project_id=_OTHER))
        failures = resp.json()["units"][0]["recent_failures"]
        assert failures, "the seeded project must report a failure mode"
        for failure in failures:
            assert set(failure) == {"failed_at", "error_summary", "occurrences"}

    async def test_the_unit_payload_exposes_no_log_or_execution_detail(self, client):
        resp = await client.get(_URL, params=_params())
        unit = resp.json()["units"][0]
        forbidden = {"log", "logs", "message", "messages", "executions", "execution_id", "detail"}
        assert forbidden.isdisjoint(unit)


class TestCadenceFields:
    """The gap quantiles a downstream classifier reads off this endpoint.

    Its rule needs the MEDIAN gap and the spread; `runs_per_day` is a mean and
    cannot support it (werkorder's `Maximo2RHM_Workorders`: mean gap 2204 s,
    median 1801 s).  Two properties are contractual at the route level: the
    fields are always PRESENT in the JSON, and they are always NULLABLE -- a
    consumer that only sees them when a job happens to have enough runs would
    have to distinguish "absent" from "not enough data", and the client
    swallows exceptions, so a KeyError becomes a silently empty document.
    """

    _CADENCE_FIELDS = (
        "gap_p25_seconds",
        "gap_p50_seconds",
        "gap_p75_seconds",
        "gap_sample_size",
    )

    async def test_the_fields_are_present_even_with_no_cadence_evidence(self, client):
        """Two executions and no job-run mirror: present, and null."""
        resp = await client.get(_URL, params=_params())
        unit = resp.json()["units"][0]
        for field in self._CADENCE_FIELDS:
            assert field in unit, "the consumer reads these unconditionally"
            assert unit[field] is None

    async def test_a_scheduled_job_reports_its_quantiles(self, client, db_session):
        """A half-hourly PROD series, reported as the interval it really runs on."""
        store = RuntimeStoreRepository(db_session)
        job = "Maximo2RHM_Workorders"
        for index in range(25):
            started = _T0 + datetime.timedelta(seconds=1800 * index)
            # Both tables: `runtime_executions` is what MATCHES the unit,
            # `runtime_job_runs` is the only one carrying `environment_name`
            # and therefore the only source the gap series can come from.
            await store.upsert_execution(
                _P,
                RuntimeExecution(
                    execution_id=f"sched-{index}",
                    job_name=job,
                    state=RuntimeExecutionState.SUCCESS,
                    started_at=started,
                    ended_at=started + datetime.timedelta(milliseconds=250),
                    duration_ms=250,
                ),
                _SRC,
                _T0,
            )
            await store.upsert_job_run(
                _P,
                RuntimeJobRun(
                    run_id=f"sched-{index}",
                    job_id="job-scheduled",
                    job_name=job,
                    source_key=_SRC,
                    execution_id=f"sched-{index}",
                    state=RuntimeExecutionState.SUCCESS,
                    environment_name="PROD",
                    started_at=started,
                ),
                _SRC,
                _T0,
            )
        resp = await client.get(_URL, params=_params(units=[job]))
        unit = resp.json()["units"][0]
        assert unit["gap_sample_size"] == 24
        assert unit["gap_p25_seconds"] == 1800
        assert unit["gap_p50_seconds"] == 1800
        assert unit["gap_p75_seconds"] == 1800
        # The qualifier a document must print alongside the interval.
        assert unit["environments"] == ["PROD"]

    async def test_no_per_run_timestamps_are_added_alongside_them(self, client):
        """Quantiles are an aggregate; a list of start times is not.

        The payload stays aggregate-only, so the new fields must not have
        dragged a run series in with them.
        """
        resp = await client.get(_URL, params=_params())
        unit = resp.json()["units"][0]
        forbidden = {"starts", "started_at", "run_starts", "gaps", "gap_seconds", "runs"}
        assert forbidden.isdisjoint(unit)
        cadence = {field: value for field, value in unit.items() if field.startswith("gap")}
        assert set(cadence) == set(self._CADENCE_FIELDS)
        assert all(value is None or isinstance(value, int) for value in cadence.values())
