"""Unit tests for the Elasticsearch runtime ingest provider.

No test reaches a live cluster: `_FakeElastic` is a `httpx.MockTransport`
handler that behaves like a real cluster for the three endpoints this provider
uses, including honouring the emitted range filter, so window bounding and the
`basic_date_time` format are exercised rather than asserted about in isolation.

Fixtures are built here rather than copied from the reference MCP repos, so a
change in either project cannot quietly invalidate the other's tests.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from src.runtime.models import (
    RuntimeExecutableKind,
    RuntimeExecutionState,
    RuntimeLogSeverity,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.elastic import (
    ElasticRuntimeProvider,
    _ingest_window,
    _total_hits,
    parse_elastic_config,
)
from src.runtime.providers.elastic_schemas import (
    BASIC_DATE_TIME,
    GENERIC_PRESET,
    PROCESS_MONITORING_PRESET,
    STRICT_DATE_OPTIONAL_TIME,
    format_es_datetime,
    parse_es_datetime,
    resolve_preset_for_index,
)
from src.runtime.store.repository import log_event_fingerprint

_ENDPOINT = "https://logs.example.internal:9200"
_PM_INDEX = "process_monitoring-2026.09"
_APP_INDEX = "app-logs-2026.09"
_PROJECT = "werkorder"

#: `basic_date_time`, the format process_monitoring* actually uses.
_PM_TIMESTAMP_RE = re.compile(r"^\d{8}T\d{6}\.\d{3}Z$")


# --- fake cluster ------------------------------------------------------------


class _FakeElastic:
    """A minimal, honest stand-in for a cluster over `httpx.MockTransport`.

    It filters documents by the range filter the provider actually emitted,
    which is what makes a wrong date format fail the test the same way it fails
    in production: zero rows, no error.
    """

    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        version: str = "8.13.0",
        total_shape: str = "object",
        resolve_available: bool = True,
        stats_available: bool = True,
        failing_indices: tuple[str, ...] = (),
        overflow_above_seconds: int | None = None,
    ) -> None:
        self.documents = documents
        self.version = version
        self.total_shape = total_shape
        self.resolve_available = resolve_available
        self.stats_available = stats_available
        self.failing_indices = failing_indices
        # Report an over-max_result_window total for any range wider than this,
        # so the subdivision path can be exercised without indexing 10,001 docs.
        self.overflow_above_seconds = overflow_above_seconds
        self.searches: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/":
            return httpx.Response(
                200,
                json={
                    "cluster_name": "example-cluster",
                    "cluster_uuid": "cluster-uuid-1",
                    "version": {"number": self.version},
                },
            )
        if path == "/_resolve/index/*":
            if not self.resolve_available:
                return httpx.Response(400, json={"error": "no handler"})
            names = sorted({str(item["_index"]) for item in self.documents})
            return httpx.Response(
                200, json={"indices": [{"name": name} for name in names], "aliases": []}
            )
        if path == "/_alias":
            names = sorted({str(item["_index"]) for item in self.documents})
            return httpx.Response(200, json={name: {"aliases": {}} for name in names})
        if path == "/_stats/docs":
            if not self.stats_available:
                return httpx.Response(403, json={"error": "forbidden"})
            counts: dict[str, Any] = {}
            for item in self.documents:
                index = str(item["_index"])
                bucket = counts.setdefault(index, {"primaries": {"docs": {"count": 0}}})
                bucket["primaries"]["docs"]["count"] += 1
            return httpx.Response(200, json={"indices": counts})
        if path.endswith("/_search"):
            return self._search(request, path)
        return httpx.Response(404, json={"error": f"no route for {path}"})

    def _search(self, request: httpx.Request, path: str) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        params = {key: value for key, value in request.url.params.items()}
        self.searches.append((path, body, params))
        expressions = path[1 : -len("/_search")].split(",")
        if any(item in self.failing_indices for item in expressions):
            return httpx.Response(503, json={"error": "shard failure"})
        matched = sorted(
            (
                item
                for item in self.documents
                if self._index_matches(str(item["_index"]), expressions)
                and self._in_range(item, body)
            ),
            key=lambda item: str(item["_source"].get(self._sort_field(body), "")),
        )
        offset = int(body.get("from", 0))
        size = int(body.get("size", 10))
        page = matched[offset : offset + size]
        total = len(matched)
        if self._range_is_wide(body):
            total = 10_001
        return httpx.Response(200, json={"hits": {"total": self._total(total), "hits": page}})

    def _range_is_wide(self, body: dict[str, Any]) -> bool:
        if self.overflow_above_seconds is None:
            return False
        clause = body["query"]["bool"]["filter"][0]["range"]
        bounds = clause[next(iter(clause))]
        lower = parse_es_datetime(bounds["gte"], bounds["format"])
        upper = parse_es_datetime(bounds["lt"], bounds["format"])
        if lower is None or upper is None:
            return False
        return (upper - lower).total_seconds() > self.overflow_above_seconds

    def _total(self, count: int) -> Any:
        # Pre-7.0 clusters send a bare number; 7.0+ send {value, relation}.
        if self.total_shape == "number":
            return count
        return {"value": count, "relation": "eq"}

    @staticmethod
    def _index_matches(index: str, expressions: list[str]) -> bool:
        return any(fnmatch.fnmatch(index, expression) for expression in expressions)

    @staticmethod
    def _sort_field(body: dict[str, Any]) -> str:
        return next(iter(body["sort"][0]))

    def _in_range(self, document: dict[str, Any], body: dict[str, Any]) -> bool:
        clause = body["query"]["bool"]["filter"][0]["range"]
        field = next(iter(clause))
        bounds = clause[field]
        date_format = bounds["format"]
        lower = parse_es_datetime(bounds["gte"], date_format)
        upper = parse_es_datetime(bounds["lt"], date_format)
        if lower is None or upper is None:
            # A bound the field's own format cannot parse: real Elasticsearch
            # rejects or silently misses. Matching nothing reproduces the
            # production symptom.
            return False
        value = parse_es_datetime(document["_source"].get(field), date_format)
        return value is not None and lower <= value < upper


def _provider(cluster: _FakeElastic) -> ElasticRuntimeProvider:
    return ElasticRuntimeProvider(transport=httpx.MockTransport(cluster))


# --- fixtures ----------------------------------------------------------------


def _recent_hour(hours_ago: int = 3) -> datetime:
    """A whole hour safely inside the window and outside the excluded newest hour."""
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).replace(
        minute=0, second=0, microsecond=0
    )


def _pm_step(
    *,
    run_id: str,
    step_nr: int,
    step_total: int,
    status: str,
    started_at: datetime,
    duration_seconds: int = 5,
    interface: str | None = "AssetCheck_GetLocation",
    environment: str | None = "PROD",
    index: str = _PM_INDEX,
    doc_id: str | None = None,
    step_name: str | None = None,
    error: str | None = None,
    message_in: str | None = None,
    message_out: str | None = None,
    sub_number: str | None = None,
    ended: bool = True,
    description: str | None = "processed",
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "InterfaceRunId": run_id,
        "ProcessingStepNr": str(step_nr),
        "ProcessingStepTotal": str(step_total),
        "ProcessingStepStatus": status,
        "ProcessingStep": step_name or f"step-{step_nr}",
        "start": format_es_datetime(started_at, BASIC_DATE_TIME),
    }
    if ended:
        source["end"] = format_es_datetime(
            started_at + timedelta(seconds=duration_seconds), BASIC_DATE_TIME
        )
    if interface is not None:
        source["Interface"] = interface
    if environment is not None:
        source["Environment"] = environment
    if description is not None:
        source["Description"] = description
    if error is not None:
        source["Error"] = error
    if message_in is not None:
        source["MessageIn"] = message_in
    if message_out is not None:
        source["MessageOut"] = message_out
    if sub_number is not None:
        source["InterfaceRunId_SubNr"] = sub_number
    return {
        "_index": index,
        "_id": doc_id or f"{run_id}-{step_nr}",
        "_source": source,
    }


def _app_log(
    *,
    doc_id: str,
    timestamp: datetime,
    level: str = "ERROR",
    message: str = "connection refused",
    service: str = "order-api",
    index: str = _APP_INDEX,
) -> dict[str, Any]:
    return {
        "_index": index,
        "_id": doc_id,
        "_source": {
            "@timestamp": format_es_datetime(timestamp, STRICT_DATE_OPTIONAL_TIME),
            "log": {"level": level},
            "service": {"name": service},
            "message": message,
        },
    }


def _credentials(**overrides: Any) -> dict[str, Any]:
    credentials: dict[str, Any] = {
        "endpoint_url": _ENDPOINT,
        "api_key": "es-api-key-value",
        "index_names": ["process_monitoring*"],
        "window_days": "1",
    }
    credentials.update(overrides)
    return credentials


async def _sync(cluster: _FakeElastic, **credential_overrides: Any):
    provider = _provider(cluster)
    return await provider.sync(
        credentials=_credentials(**credential_overrides), project_id=_PROJECT
    )


# --- tier 1: process-monitoring run reconstruction ---------------------------


class TestProcessMonitoringRunReconstruction:
    """One InterfaceRunId is one real run, not one row per log document."""

    async def test_multi_step_run_becomes_one_run_and_one_execution(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=3, status="success", started_at=started),
                _pm_step(
                    run_id="run-1",
                    step_nr=2,
                    step_total=3,
                    status="success",
                    started_at=started + timedelta(seconds=10),
                ),
                _pm_step(
                    run_id="run-1",
                    step_nr=3,
                    step_total=3,
                    status="success",
                    started_at=started + timedelta(seconds=20),
                ),
            ]
        )

        batch = await _sync(cluster)

        assert len(batch.job_runs) == 1
        assert len(batch.executions) == 1
        # Three log documents, ONE run. A message count is never a run count.
        assert len(batch.log_events) == 3

        run = batch.job_runs[0]
        assert run.execution_id == "run-1"
        assert run.state == RuntimeExecutionState.SUCCESS
        assert run.executable_kind == RuntimeExecutableKind.JOB
        assert run.started_at == started
        assert run.ended_at == started + timedelta(seconds=25)
        assert run.duration_ms == 25_000
        assert run.environment_id == "PROD"
        # The index is this provider's container key.
        assert run.workspace_id == _PM_INDEX
        # No exchange counters were invented from the step count.
        assert run.total_exchanges is None
        assert run.completed_exchanges is None

        assert batch.executions[0].execution_id == "run-1"
        assert batch.executions[0].state == RuntimeExecutionState.SUCCESS

    async def test_log_events_are_execution_scoped_and_attributable(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=2, status="success", started_at=started),
                _pm_step(
                    run_id="run-1",
                    step_nr=2,
                    step_total=2,
                    status="success",
                    started_at=started + timedelta(seconds=1),
                ),
            ]
        )

        batch = await _sync(cluster)

        assert all(event.execution_id == "run-1" for event in batch.log_events)
        assert all(event.scope == "execution" for event in batch.log_events)
        assert all(event.attribution_safe for event in batch.log_events)
        # The declared step number is the ordinal, so it survives any change of
        # fetch order or window boundary.
        assert sorted(event.line_ordinal for event in batch.log_events) == [1, 2]

    async def test_log_rows_join_to_their_job_through_the_execution_id(self):
        """`runtime_log_events` has no job column; the join is execution_id."""
        started = _recent_hour()
        cluster = _FakeElastic(
            [_pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=started)]
        )

        batch = await _sync(cluster)

        run_ids = {run.execution_id for run in batch.job_runs}
        execution_ids = {execution.execution_id for execution in batch.executions}
        assert {event.execution_id for event in batch.log_events} <= run_ids
        assert run_ids == execution_ids
        assert batch.job_runs[0].job_name == "AssetCheck_GetLocation"

    async def test_sub_run_numbers_split_one_correlation_id_into_two_runs(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    sub_number="1",
                    doc_id="a",
                ),
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    sub_number="2",
                    doc_id="b",
                ),
            ]
        )

        batch = await _sync(cluster)

        assert sorted(run.execution_id for run in batch.job_runs) == ["run-1#1", "run-1#2"]

    async def test_a_correlation_id_spanning_two_interfaces_is_not_filed_under_one(self):
        """The chain case: each interface gets its own run, not the first one's.

        Nothing in the index enforces one interface per correlation id, and
        attributing a whole hand-off to whichever interface sorted first is a
        plausible-looking wrong answer.
        """
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="chain-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    interface="Order_Receive",
                    doc_id="a",
                ),
                _pm_step(
                    run_id="chain-1",
                    step_nr=1,
                    step_total=1,
                    status="failed",
                    started_at=started + timedelta(seconds=5),
                    interface="Order_Dispatch",
                    doc_id="b",
                    error="downstream refused",
                ),
            ]
        )

        batch = await _sync(cluster)

        assert sorted(job.job_id for job in batch.jobs) == ["Order_Dispatch", "Order_Receive"]
        assert len(batch.job_runs) == 2
        assert len(batch.executions) == 2
        # Distinct execution ids, or the two runs would collide on
        # runtime_executions' (project_id, execution_id) uniqueness.
        assert len({item.execution_id for item in batch.executions}) == 2
        assert all(item.execution_id.startswith("chain-1#") for item in batch.executions)
        # The failure lands on the interface that actually failed.
        by_job = {run.job_name: run.state for run in batch.job_runs}
        assert by_job["Order_Receive"] == RuntimeExecutionState.SUCCESS
        assert by_job["Order_Dispatch"] == RuntimeExecutionState.FAILED
        assert [failure.job_name for failure in batch.failures] == ["Order_Dispatch"]
        # No hand-off topology is invented from the shared correlation id.
        assert batch.job_dependencies == []

    async def test_the_normal_case_keeps_the_bare_correlation_id(self):
        """One id, one interface: execution_id is the InterfaceRunId itself."""
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=2, status="success", started_at=started),
                _pm_step(
                    run_id="run-1",
                    step_nr=2,
                    step_total=2,
                    status="success",
                    started_at=started + timedelta(seconds=1),
                ),
            ]
        )

        batch = await _sync(cluster)

        assert [item.execution_id for item in batch.executions] == ["run-1"]

    async def test_a_failed_step_makes_the_run_failed_and_records_a_failure(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=2, status="success", started_at=started),
                _pm_step(
                    run_id="run-1",
                    step_nr=2,
                    step_total=2,
                    status="FAILED",
                    started_at=started + timedelta(seconds=2),
                    error="java.sql.SQLException: could not reach db-prod.example.internal",
                ),
            ]
        )

        batch = await _sync(cluster)

        assert batch.job_runs[0].state == RuntimeExecutionState.FAILED
        # The verbatim provider status is preserved even though `state` is a
        # lossy projection of it.
        assert batch.job_runs[0].raw_state == "FAILED"
        assert len(batch.failures) == 1
        failure = batch.failures[0]
        assert failure.execution_id == "run-1"
        assert failure.job_name == "AssetCheck_GetLocation"
        # The fault survives redaction; the host does not.
        assert "java.sql.SQLException" in failure.error_summary
        assert "db-prod.example.internal" not in failure.error_summary
        error_events = [
            event for event in batch.log_events if event.severity == RuntimeLogSeverity.ERROR
        ]
        assert len(error_events) == 1

    async def test_an_unmapped_status_is_unknown_and_never_success(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="VERWERKT_MET_OPMERKING",
                    started_at=started,
                )
            ]
        )

        batch = await _sync(cluster)

        assert batch.job_runs[0].state == RuntimeExecutionState.UNKNOWN
        assert batch.job_runs[0].raw_state == "VERWERKT_MET_OPMERKING"
        # An unmapped status must not manufacture a failure either.
        assert batch.failures == []

    async def test_an_incomplete_run_does_not_claim_success(self):
        """`ProcessingStepTotal` withholds success; it never asserts failure."""
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=5, status="success", started_at=started),
                _pm_step(
                    run_id="run-1",
                    step_nr=2,
                    step_total=5,
                    status="success",
                    started_at=started + timedelta(seconds=1),
                ),
            ]
        )

        batch = await _sync(cluster)

        assert batch.job_runs[0].state == RuntimeExecutionState.UNKNOWN
        assert batch.failures == []

    async def test_a_step_with_no_end_is_still_running(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    ended=False,
                )
            ]
        )

        batch = await _sync(cluster)

        assert batch.job_runs[0].state == RuntimeExecutionState.RUNNING
        assert batch.job_runs[0].ended_at is None
        assert batch.job_runs[0].duration_ms is None


class TestJobIdentityIsVerbatim:
    """The logged identifier is stored as logged. Any normaliser merges reals."""

    async def test_similar_names_stay_separate_jobs(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    interface="AssetCheck_GetLocation",
                ),
                _pm_step(
                    run_id="run-2",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    interface="AssetCheck_GetLocations",
                ),
            ]
        )

        batch = await _sync(cluster)

        assert sorted(job.job_id for job in batch.jobs) == [
            "AssetCheck_GetLocation",
            "AssetCheck_GetLocations",
        ]
        # job_id IS the logged name: consumers read job_id as the unit name and
        # do no fuzzy matching, so no environment prefix and no case folding.
        assert all(job.job_id == job.name for job in batch.jobs)

    async def test_a_name_seen_in_two_environments_keeps_no_job_environment(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    environment="PROD",
                ),
                _pm_step(
                    run_id="run-2",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    environment="TEST",
                ),
            ]
        )

        batch = await _sync(cluster)

        assert len(batch.jobs) == 1
        # Asserting either environment would be a coin flip that changes between
        # syncs; the per-run environment is authoritative and stays on the runs.
        assert batch.jobs[0].environment_id is None
        assert sorted(run.environment_id or "" for run in batch.job_runs) == ["PROD", "TEST"]
        assert sorted(item.environment_id for item in batch.environments) == ["PROD", "TEST"]

    async def test_an_overlong_name_stays_inside_its_column_without_colliding(self):
        """The 255-char columns force a length guard; it must not merge names."""
        started = _recent_hour()
        prefix = "Interface_" + "x" * 300
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    interface=f"{prefix}_A",
                ),
                _pm_step(
                    run_id="run-2",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    interface=f"{prefix}_B",
                ),
            ]
        )

        batch = await _sync(cluster)

        assert len(batch.jobs) == 2
        for job in batch.jobs:
            assert len(job.job_id) <= 255
            assert len(job.name) <= 255
            # The invariant consumers rely on survives the guard.
            assert job.job_id == job.name
        # A hash suffix, not a truncation: the two names stay two jobs.
        assert len({job.job_id for job in batch.jobs}) == 2
        assert {run.job_name for run in batch.job_runs} == {job.name for job in batch.jobs}

    async def test_a_run_with_no_logged_identifier_is_a_gap_not_a_guess(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started,
                    interface=None,
                )
            ]
        )

        batch = await _sync(cluster)

        assert batch.jobs == []
        assert batch.job_runs == []
        assert batch.executions == []
        # The evidence is kept, but flagged as unattributable rather than
        # attributed to a plausible-looking reconciled name.
        assert len(batch.log_events) == 1
        assert batch.log_events[0].attribution_safe is False
        assert batch.log_events[0].scope == "engine"


# --- the date-format trap ----------------------------------------------------


class TestRangeQueryDateFormat:
    """`process_monitoring*` uses basic_date_time; ISO bounds match NOTHING."""

    async def test_process_monitoring_range_uses_basic_date_time(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [_pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=started)]
        )

        await _sync(cluster)

        ranges = [
            body["query"]["bool"]["filter"][0]["range"]
            for _path, body, _params in cluster.searches
        ]
        assert ranges, "no search was issued"
        for clause in ranges:
            assert set(clause) == {"start"}
            bounds = clause["start"]
            assert bounds["format"] == BASIC_DATE_TIME
            # Both bounds must be rendered in the field's own format, not ISO.
            assert _PM_TIMESTAMP_RE.match(bounds["gte"]), bounds["gte"]
            assert _PM_TIMESTAMP_RE.match(bounds["lt"]), bounds["lt"]

    async def test_generic_range_uses_strict_date_optional_time(self):
        cluster = _FakeElastic([_app_log(doc_id="a", timestamp=_recent_hour())])

        await _sync(cluster, index_names=[_APP_INDEX])

        clause = cluster.searches[0][1]["query"]["bool"]["filter"][0]["range"]
        assert set(clause) == {"@timestamp"}
        assert clause["@timestamp"]["format"] == STRICT_DATE_OPTIONAL_TIME
        assert clause["@timestamp"]["gte"].endswith("Z")
        assert "T" in clause["@timestamp"]["gte"]

    def test_basic_date_time_round_trips(self):
        value = datetime(2026, 8, 31, 2, 0, 4, 123_000, tzinfo=UTC)
        rendered = format_es_datetime(value, BASIC_DATE_TIME)
        assert rendered == "20260831T020004.123Z"
        assert parse_es_datetime(rendered, BASIC_DATE_TIME) == value

    def test_iso_bounds_against_a_basic_date_time_field_would_match_nothing(self):
        """Pins the failure mode the preset's date format exists to prevent."""
        iso_bound = format_es_datetime(
            datetime(2026, 8, 31, tzinfo=UTC), STRICT_DATE_OPTIONAL_TIME
        )
        assert iso_bound == "2026-08-31T00:00:00Z"
        # A real `basic_date_time` parser cannot read an ISO bound. The fake
        # cluster models that as "matches nothing", exactly as production does.
        assert datetime.strptime is not None
        with pytest.raises(ValueError):
            datetime.strptime(iso_bound, "%Y%m%dT%H%M%S.%f%z")


# --- tier 2: generic, log-only ----------------------------------------------


class TestGenericIndexIsLogOnly:
    """A generic index cannot name what ran, so it must never produce a run."""

    async def test_no_runs_jobs_executions_or_failures(self):
        timestamp = _recent_hour()
        cluster = _FakeElastic(
            [
                _app_log(doc_id="a", timestamp=timestamp),
                _app_log(doc_id="b", timestamp=timestamp + timedelta(seconds=1)),
                _app_log(doc_id="c", timestamp=timestamp + timedelta(seconds=2)),
            ]
        )

        batch = await _sync(cluster, index_names=[_APP_INDEX])

        assert batch.jobs == []
        assert batch.job_runs == []
        assert batch.executions == []
        assert batch.failures == []
        assert batch.plan_runs == []
        assert batch.component_metrics == []
        assert len(batch.log_events) == 3

    async def test_log_rows_are_engine_scoped_and_not_attribution_safe(self):
        cluster = _FakeElastic([_app_log(doc_id="a", timestamp=_recent_hour())])

        batch = await _sync(cluster, index_names=[_APP_INDEX])

        event = batch.log_events[0]
        assert event.scope == "engine"
        assert event.attribution_safe is False
        assert event.severity == RuntimeLogSeverity.ERROR
        assert "connection refused" in event.message

    async def test_the_synthetic_grouping_key_matches_no_execution(self):
        cluster = _FakeElastic([_app_log(doc_id="a", timestamp=_recent_hour())])

        batch = await _sync(cluster, index_names=[_APP_INDEX])

        execution_id = batch.log_events[0].execution_id
        assert execution_id.startswith("elastic-log:")
        assert _APP_INDEX in execution_id
        assert "order-api" in execution_id
        # The point of the prefix: nothing presents it as a real run.
        assert execution_id not in {item.execution_id for item in batch.executions}

    async def test_configurable_field_names_are_honoured(self):
        timestamp = _recent_hour()
        cluster = _FakeElastic(
            [
                {
                    "_index": "custom-logs",
                    "_id": "a",
                    "_source": {
                        "ts": format_es_datetime(timestamp, STRICT_DATE_OPTIONAL_TIME),
                        "severity": "warn",
                        "app": "billing",
                        "body": "retrying batch",
                    },
                }
            ]
        )

        batch = await _sync(
            cluster,
            index_names=["custom-logs"],
            field_timestamp="ts",
            field_level="severity",
            field_service="app",
            field_message="body",
        )

        assert len(batch.log_events) == 1
        assert batch.log_events[0].severity == RuntimeLogSeverity.WARNING
        assert "retrying batch" in batch.log_events[0].message
        assert cluster.searches[0][1]["sort"][0].get("ts") is not None

    def test_a_fixed_preset_refuses_field_overrides(self):
        overridden = PROCESS_MONITORING_PRESET.with_field_overrides({"timestamp": "ts"})
        assert overridden.timestamp_field == "start"

        generic = GENERIC_PRESET.with_field_overrides({"timestamp": "ts"})
        assert generic.timestamp_field == "ts"


class TestMixedAllowlist:
    async def test_both_tiers_are_ingested_in_one_cycle_with_their_own_rules(self):
        moment = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=moment),
                _app_log(doc_id="a", timestamp=moment),
            ]
        )

        batch = await _sync(cluster, index_names=["process_monitoring*", _APP_INDEX])

        assert len(batch.job_runs) == 1
        assert len(batch.log_events) == 2
        by_scope = {event.scope: event for event in batch.log_events}
        assert by_scope["execution"].attribution_safe is True
        assert by_scope["engine"].attribution_safe is False
        # One workspace per ingested index: the index is the container key.
        assert sorted(item.workspace_id for item in batch.workspaces) == sorted(
            [_APP_INDEX, _PM_INDEX]
        )

    async def test_one_unreadable_index_group_is_partial_not_total_loss(self):
        moment = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=moment),
                _app_log(doc_id="a", timestamp=moment),
            ],
            failing_indices=(_APP_INDEX,),
        )

        batch = await _sync(cluster, index_names=["process_monitoring*", _APP_INDEX])

        assert batch.partial is True
        assert batch.failure_reason_code == "index_group_unreadable"
        assert len(batch.job_runs) == 1

    async def test_every_index_group_failing_raises(self):
        cluster = _FakeElastic(
            [_app_log(doc_id="a", timestamp=_recent_hour())], failing_indices=(_APP_INDEX,)
        )

        with pytest.raises(RuntimeProviderError):
            await _sync(cluster, index_names=[_APP_INDEX])


# --- hits.total in both shapes ----------------------------------------------


class TestHitsTotalShapes:
    @pytest.mark.parametrize("total_shape", ["object", "number"])
    async def test_both_total_shapes_produce_the_same_records(self, total_shape: str):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=2, status="success", started_at=started),
                _pm_step(
                    run_id="run-1",
                    step_nr=2,
                    step_total=2,
                    status="success",
                    started_at=started + timedelta(seconds=1),
                ),
            ],
            total_shape=total_shape,
        )

        batch = await _sync(cluster)

        assert len(batch.job_runs) == 1
        assert len(batch.log_events) == 2
        assert batch.job_runs[0].state == RuntimeExecutionState.SUCCESS

    def test_total_hits_reads_a_bare_number(self):
        assert _total_hits({"total": 42}) == (42, False)

    def test_total_hits_reads_the_object_shape(self):
        assert _total_hits({"total": {"value": 42, "relation": "eq"}}) == (42, False)

    def test_total_hits_reports_a_capped_count_as_a_lower_bound(self):
        """`relation: gte` means the number is not the real total."""
        assert _total_hits({"total": {"value": 10_000, "relation": "gte"}}) == (10_000, True)

    def test_total_hits_tolerates_a_missing_total(self):
        assert _total_hits({}) == (None, False)


# --- bounded ingest ----------------------------------------------------------


class TestBoundedIngest:
    async def test_the_document_cap_stops_the_cycle(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id=f"run-{index}",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started + timedelta(seconds=index),
                )
                for index in range(150)
            ]
        )

        # 100 is the operator-settable floor: a cap low enough to be useless is
        # itself a way to make a source look like an empty estate.
        batch = await _sync(cluster, max_documents_per_sync="100")

        assert len(batch.log_events) == 100
        # Reaching a configured cap is designed behaviour, not a sync failure:
        # marking it partial would record a failure every cycle and auto-disable
        # a healthy source after three.
        assert batch.partial is False

    async def test_the_window_bounds_every_query(self):
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="old",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=_recent_hour() - timedelta(days=40),
                ),
                _pm_step(
                    run_id="recent", step_nr=1, step_total=1, status="success", started_at=_recent_hour()
                ),
            ]
        )

        batch = await _sync(cluster, window_days="1")

        assert [run.execution_id for run in batch.job_runs] == ["recent"]
        lower_bounds = [
            parse_es_datetime(
                body["query"]["bool"]["filter"][0]["range"]["start"]["gte"], BASIC_DATE_TIME
            )
            for _path, body, _params in cluster.searches
        ]
        earliest = min(bound for bound in lower_bounds if bound is not None)
        assert earliest >= datetime.now(UTC) - timedelta(days=1, hours=1)

    async def test_no_query_is_ever_unbounded(self):
        cluster = _FakeElastic([_pm_step(
            run_id="run-1", step_nr=1, step_total=1, status="success", started_at=_recent_hour()
        )])

        await _sync(cluster)

        for _path, body, _params in cluster.searches:
            filters = body["query"]["bool"]["filter"]
            assert len(filters) == 1
            assert "range" in filters[0]
            assert body["size"] <= 1_000
            assert body["track_total_hits"] is True

    @pytest.mark.parametrize(
        "field,value",
        [
            ("window_days", "0"),
            ("window_days", "91"),
            ("window_days", "many"),
            ("max_documents_per_sync", "10"),
            ("max_documents_per_sync", "500000"),
            ("payload_max_chars", "999999"),
        ],
    )
    def test_out_of_range_bounds_are_rejected(self, field: str, value: str):
        with pytest.raises(RuntimeProviderError):
            parse_elastic_config(_credentials(**{field: value}))

    def test_the_defaults_are_conservative(self):
        config = parse_elastic_config(
            {"endpoint_url": _ENDPOINT, "api_key": "k", "index_names": ["process_monitoring*"]}
        )
        assert config.window_days == 7
        assert config.max_documents == 20_000
        assert config.capture_payloads is False

    def test_the_window_excludes_the_newest_partial_hour(self):
        now = datetime(2026, 9, 10, 14, 37, 12, tzinfo=UTC)
        start, end = _ingest_window(now=now, window_days=2, since=None)
        assert end == datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
        assert start == datetime(2026, 9, 8, 14, 0, tzinfo=UTC)

    def test_a_watermark_narrows_the_window_but_never_widens_it(self):
        now = datetime(2026, 9, 10, 14, 37, tzinfo=UTC)
        narrowed, _end = _ingest_window(
            now=now, window_days=7, since=datetime(2026, 9, 10, 9, 5, tzinfo=UTC)
        )
        assert narrowed == datetime(2026, 9, 10, 9, 0, tzinfo=UTC)

        stale, _end = _ingest_window(
            now=now, window_days=1, since=datetime(2020, 1, 1, tzinfo=UTC)
        )
        # A stale watermark must not turn one cycle into an unbounded backfill.
        assert stale == datetime(2026, 9, 9, 14, 0, tzinfo=UTC)

    async def test_an_incremental_watermark_shrinks_the_queried_range(self):
        started = _recent_hour(hours_ago=3)
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=started),
                _pm_step(
                    run_id="run-old",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=started - timedelta(hours=10),
                ),
            ]
        )
        provider = _provider(cluster)
        provider._config = parse_elastic_config(_credentials())

        batch = await provider.fetch_runtime_batch(
            project_id=_PROJECT, since=started - timedelta(minutes=30)
        )
        await provider.aclose()

        assert [run.execution_id for run in batch.job_runs] == ["run-1"]
        # The watermark is floored to its hour, so the boundary bucket is still
        # fetched WHOLE -- that is what keeps the ordinals stable. Here that
        # means reading back to `started - 1h`, four hour-slices, rather than
        # the 24 an unwatermarked one-day window would have needed.
        assert len(cluster.searches) == 4
        lower_bounds = [
            parse_es_datetime(
                body["query"]["bool"]["filter"][0]["range"]["start"]["gte"], BASIC_DATE_TIME
            )
            for _path, body, _params in cluster.searches
        ]
        assert min(bound for bound in lower_bounds if bound is not None) == started - timedelta(
            hours=1
        )


class TestResultWindowOverflow:
    """A slice bigger than `index.max_result_window` is subdivided, not truncated."""

    async def test_an_overflowing_hour_is_split_into_ten_minute_slices(self):
        started = _recent_hour()
        # Every range wider than 10 minutes reports 10,001 hits.
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=started),
            ],
            overflow_above_seconds=600,
        )
        provider = _provider(cluster)
        provider._config = replace(
            parse_elastic_config(_credentials()), window_days=1, max_documents=50_000
        )

        batch = await provider.fetch_runtime_batch(
            project_id=_PROJECT, since=started - timedelta(minutes=5)
        )
        await provider.aclose()

        spans = {
            self._span_seconds(body)
            for _path, body, _params in cluster.searches
        }
        # The hour probe, then the 10-minute subslices it was split into.
        assert 3600.0 in spans
        assert 600.0 in spans
        # The probe page is discarded before subdividing, so the one real
        # document is collected exactly once rather than twice.
        assert len(batch.log_events) == 1
        assert len(batch.job_runs) == 1

    async def test_a_ten_minute_slice_that_still_overflows_is_reported_not_duplicated(self):
        started = _recent_hour()
        # Nothing can shrink below the 10-minute floor, so every slice overflows.
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=started),
            ],
            overflow_above_seconds=0,
        )
        provider = _provider(cluster)
        provider._config = replace(
            parse_elastic_config(_credentials()), window_days=1, max_documents=50_000
        )

        batch = await provider.fetch_runtime_batch(
            project_id=_PROJECT, since=started - timedelta(minutes=5)
        )
        await provider.aclose()

        # The reachable prefix is kept once. Silent truncation would read
        # downstream as "this estate has no traffic"; a duplicate would multiply
        # the mirror on every cycle.
        assert len(batch.log_events) == 1
        # An unreachable tail is a volume condition, not a source fault: marking
        # it partial would record a sync failure and auto-disable a live source.
        assert batch.partial is False

    @staticmethod
    def _span_seconds(body: dict[str, Any]) -> float:
        clause = body["query"]["bool"]["filter"][0]["range"]
        bounds = clause[next(iter(clause))]
        lower = parse_es_datetime(bounds["gte"], bounds["format"])
        upper = parse_es_datetime(bounds["lt"], bounds["format"])
        assert lower is not None and upper is not None
        return (upper - lower).total_seconds()


class TestIdempotentReingest:
    async def test_two_cycles_over_the_same_window_produce_identical_row_keys(self):
        """The uniqueness key must be stable, or every cycle duplicates the mirror."""
        started = _recent_hour()
        documents = [
            _pm_step(
                run_id="run-1",
                step_nr=step,
                step_total=3,
                status="success",
                started_at=started + timedelta(seconds=step),
            )
            for step in (1, 2, 3)
        ] + [
            _app_log(doc_id="a", timestamp=started),
            # A byte-identical repeated log line: valid data that must be kept.
            _app_log(doc_id="b", timestamp=started),
        ]

        first = await _sync(_FakeElastic(list(documents)), index_names=["process_monitoring*", _APP_INDEX])
        second = await _sync(_FakeElastic(list(documents)), index_names=["process_monitoring*", _APP_INDEX])

        def keys(batch) -> set[tuple[str, int, str]]:
            return {
                (event.execution_id, event.line_ordinal, log_event_fingerprint(event))
                for event in batch.log_events
            }

        assert keys(first) == keys(second)
        # Five documents, five distinct rows: the duplicate line is preserved
        # rather than collapsed.
        assert len(keys(first)) == 5

    async def test_a_partially_fetched_bucket_yields_a_prefix_of_the_same_ordinals(self):
        """A cut-short bucket must not renumber the rows it did read.

        The cap is driven below its operator-settable floor here on purpose:
        this is the internal invariant that makes a capped or overflowing cycle
        safe to repeat, and it has to hold at any budget.
        """
        started = _recent_hour()
        documents = [
            _app_log(doc_id=f"doc-{index}", timestamp=started, message="identical line")
            for index in range(4)
        ]

        async def ordinals(max_documents: int) -> list[int]:
            provider = _provider(_FakeElastic(list(documents)))
            base = parse_elastic_config(_credentials(index_names=[_APP_INDEX]))
            provider._config = replace(base, max_documents=max_documents)
            batch = await provider.fetch_runtime_batch(project_id=_PROJECT)
            await provider.aclose()
            return sorted(event.line_ordinal for event in batch.log_events)

        capped = await ordinals(2)
        full = await ordinals(100)

        assert len(capped) == 2
        assert len(full) == 4
        assert len(set(full)) == 4
        # A prefix, not a different set: re-ingesting adds the missing rows
        # instead of writing a second copy of the ones already stored.
        assert capped == full[:2]


# --- credential safety -------------------------------------------------------


class TestPayloadSafety:
    async def test_payload_fields_are_not_even_requested_by_default(self):
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=_recent_hour(),
                    message_in="<order><password>hunter2</password></order>",
                )
            ]
        )

        batch = await _sync(cluster)

        requested = set(cluster.searches[0][1]["_source"])
        assert "MessageIn" not in requested
        assert "MessageOut" not in requested
        # An unrequested body never crosses the network, so it cannot leak.
        assert "hunter2" not in batch.log_events[0].message

    async def test_captured_payloads_are_redacted_and_bounded(self):
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=_recent_hour(),
                    message_in="password=hunter2 endpoint=https://sap-prod.customer.internal/api",
                    message_out="x" * 5_000,
                )
            ]
        )

        batch = await _sync(cluster, capture_payloads="true", payload_max_chars="200")

        requested = set(cluster.searches[0][1]["_source"])
        assert {"MessageIn", "MessageOut"} <= requested
        message = batch.log_events[0].message
        assert "hunter2" not in message
        assert "<redacted>" in message
        # The identifying host shape is normalised, not just keyword-filtered.
        assert "sap-prod.customer.internal" not in message
        assert len(message) <= 4_000

    async def test_error_text_in_a_log_message_is_redacted(self):
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="failed",
                    started_at=_recent_hour(),
                    error="auth failed for jdbc:mysql://svc:s3cr3t@db.customer.internal/orders",
                )
            ]
        )

        batch = await _sync(cluster)

        message = batch.log_events[0].message
        assert "s3cr3t" not in message
        assert "db.customer.internal" not in message
        assert batch.failures[0].error_summary is not None
        assert "s3cr3t" not in batch.failures[0].error_summary

    def test_credentials_in_the_endpoint_url_are_refused(self):
        with pytest.raises(RuntimeProviderError):
            parse_elastic_config(_credentials(endpoint_url="https://user:pass@es.example.com:9200"))

    def test_the_credential_schema_omits_the_doc_read_scope_fields(self):
        """`editable_config_fields` appends those for every provider already."""
        names = {field["name"] for field in ElasticRuntimeProvider().credential_schema()}
        assert "doc_environment_ids" not in names
        assert "doc_window_days" not in names
        # The ingest scope is declared by scope_selection, not the schema.
        assert "index_names" not in names

    def test_the_only_secret_fields_are_the_credentials(self):
        secrets = {
            field["name"]
            for field in ElasticRuntimeProvider().credential_schema()
            if field.get("type") == "secret"
        }
        assert secrets == {"api_key", "password"}


# --- identity ----------------------------------------------------------------


class TestIdentity:
    def test_the_endpoint_identity_is_offline_and_secret_free(self):
        provider = ElasticRuntimeProvider()
        identity = provider.physical_source_identity(_credentials())
        assert identity == f"elastic:{_ENDPOINT.lower()}"
        assert "es-api-key-value" not in identity

    def test_the_frozen_field_is_the_endpoint_only(self):
        assert ElasticRuntimeProvider().identity_credential_fields() == {"endpoint_url"}

    async def test_the_stored_identity_narrows_the_cluster_by_index_allowlist(self):
        provider = ElasticRuntimeProvider()
        endpoint_identity = provider.physical_source_identity(_credentials())

        pm_identity = await provider.resolve_physical_identity(
            _credentials(index_names=["process_monitoring*"])
        )
        app_identity = await provider.resolve_physical_identity(
            _credentials(index_names=[_APP_INDEX])
        )

        assert pm_identity.startswith(f"{endpoint_identity}#")
        # One cluster legitimately backs two sources with different attribution
        # rules; an endpoint-only identity would reject the second as a duplicate.
        assert pm_identity != app_identity
        assert "es-api-key-value" not in pm_identity

    async def test_a_different_cluster_gets_a_different_identity(self):
        provider = ElasticRuntimeProvider()
        first = await provider.resolve_physical_identity(_credentials())
        second = await provider.resolve_physical_identity(
            _credentials(endpoint_url="https://other.example.internal:9200")
        )
        assert first != second

    async def test_the_identity_is_order_and_case_insensitive_over_the_allowlist(self):
        provider = ElasticRuntimeProvider()
        one = await provider.resolve_physical_identity(
            _credentials(index_names=["app-logs-2026.09", "process_monitoring*"])
        )
        two = await provider.resolve_physical_identity(
            _credentials(index_names=["PROCESS_MONITORING*", "APP-LOGS-2026.09"])
        )
        assert one == two

    async def test_the_identity_follows_an_edited_ingest_scope(self):
        """Otherwise re-registering the same scope slips past the duplicate guard."""
        from src.runtime.sync import RuntimeSyncBatch

        provider = ElasticRuntimeProvider()
        before = await provider.resolve_physical_identity(
            _credentials(index_names=["process_monitoring*"])
        )
        after = provider.physical_identity_from_batch(
            _credentials(index_names=["process_monitoring*", _APP_INDEX]), RuntimeSyncBatch()
        )
        assert after is not None
        assert after != before
        assert provider.physical_identity_from_batch(
            _credentials(index_names=["process_monitoring*"]), RuntimeSyncBatch()
        ) == before

    def test_an_unselected_scope_leaves_the_stored_identity_alone(self):
        from src.runtime.sync import RuntimeSyncBatch

        provider = ElasticRuntimeProvider()
        credentials = _credentials()
        credentials.pop("index_names")
        assert provider.physical_identity_from_batch(credentials, RuntimeSyncBatch()) is None


# --- index-selection validation ---------------------------------------------


class TestIndexSelection:
    @pytest.mark.parametrize(
        "selection",
        [
            ["*"],
            ["_all"],
            ["a*"],
            ["foo,bar"],
            ["-process_monitoring*"],
            ["../etc/passwd"],
            ["<logs-{now/d}>"],
            ["logs/_search"],
        ],
    )
    def test_unsafe_or_unbounded_selections_are_refused(self, selection: list[str]):
        with pytest.raises(RuntimeProviderError):
            parse_elastic_config(_credentials(index_names=selection))

    def test_a_prefix_pattern_is_accepted_and_normalised(self):
        config = parse_elastic_config(
            _credentials(index_names=["Process_Monitoring*", "process_monitoring*", "app-logs-2026.09"])
        )
        assert config.index_names == ("process_monitoring*", "app-logs-2026.09")

    async def test_sync_refuses_to_run_with_no_indices_selected(self):
        credentials = _credentials()
        credentials.pop("index_names")
        provider = _provider(_FakeElastic([]))
        with pytest.raises(RuntimeProviderError, match="no indices selected"):
            await provider.sync(credentials=credentials, project_id=_PROJECT)

    def test_the_index_name_picks_the_preset(self):
        assert resolve_preset_for_index(_PM_INDEX).key == "process_monitoring"
        assert resolve_preset_for_index("process_monitoring*").key == "process_monitoring"
        # Anything unclaimed falls back to log-only, the safe direction: a
        # wrongly-generic PM index under-reports, a wrongly-claimed generic one
        # would fabricate runs.
        assert resolve_preset_for_index(_APP_INDEX).key == "generic"
        assert resolve_preset_for_index(_APP_INDEX).tier == "log"

    def test_an_explicit_preset_override_wins(self):
        assert resolve_preset_for_index("esb-monitoring", override="process_monitoring").key == (
            "process_monitoring"
        )
        with pytest.raises(RuntimeProviderError):
            parse_elastic_config(_credentials(schema_preset="nonsense"))


# --- connection test, discovery, version tolerance --------------------------


class TestConnectionAndDiscovery:
    async def test_validate_credentials_succeeds_against_a_reachable_cluster(self):
        provider = _provider(_FakeElastic([]))
        ok, error = await provider.validate_credentials(_credentials())
        assert ok is True
        assert error is None

    async def test_validate_credentials_does_not_require_a_chosen_scope(self):
        """The connection test runs before the operator can pick an index."""
        credentials = _credentials()
        credentials.pop("index_names")
        provider = _provider(_FakeElastic([]))
        ok, _error = await provider.validate_credentials(credentials)
        assert ok is True

    async def test_an_unreachable_cluster_reports_a_sanitized_failure(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        provider = ElasticRuntimeProvider(transport=httpx.MockTransport(handler))
        ok, error = await provider.validate_credentials(_credentials())
        assert ok is False
        assert error is not None
        assert "es-api-key-value" not in error

    @pytest.mark.parametrize("version", ["7.10.2", "8.13.0", "9.0.0", "6.8.23"])
    async def test_every_major_version_takes_the_same_code_path(self, version: str):
        cluster = _FakeElastic(
            [_pm_step(run_id="run-1", step_nr=1, step_total=1, status="success", started_at=_recent_hour())],
            version=version,
        )

        batch = await _sync(cluster)

        # Version is recorded for reporting, never used to fail closed.
        assert len(batch.job_runs) == 1

    async def test_discovery_collapses_a_rolling_index_set_into_its_pattern(self):
        cluster = _FakeElastic(
            [
                _pm_step(
                    run_id="run-1",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=_recent_hour(),
                    index="process_monitoring-2026.09.01",
                ),
                _pm_step(
                    run_id="run-2",
                    step_nr=1,
                    step_total=1,
                    status="success",
                    started_at=_recent_hour(),
                    index="process_monitoring-2026.09.02",
                ),
                _app_log(doc_id="a", timestamp=_recent_hour()),
            ]
        )

        scopes = await _provider(cluster).discover_scopes(_credentials())

        by_id = {scope.id: scope for scope in scopes}
        assert "process_monitoring*" in by_id
        assert by_id["process_monitoring*"].default_selected is True
        assert "2 indices" in (by_id["process_monitoring*"].detail or "")
        # The dated members are not offered individually; the selection would
        # go stale the moment the index rolls.
        assert "process_monitoring-2026.09.01" not in by_id
        assert _APP_INDEX in by_id
        assert by_id[_APP_INDEX].default_selected is False

    async def test_discovery_degrades_when_resolve_index_is_unavailable(self):
        """`_resolve/index` needs 7.9+; an older cluster must still be usable."""
        cluster = _FakeElastic(
            [_app_log(doc_id="a", timestamp=_recent_hour())], resolve_available=False
        )

        scopes = await _provider(cluster).discover_scopes(_credentials())

        assert [scope.id for scope in scopes] == [_APP_INDEX]

    async def test_discovery_survives_unreadable_index_stats(self):
        cluster = _FakeElastic(
            [_app_log(doc_id="a", timestamp=_recent_hour())], stats_available=False
        )

        scopes = await _provider(cluster).discover_scopes(_credentials())

        assert [scope.id for scope in scopes] == [_APP_INDEX]
        assert scopes[0].detail is None

    async def test_basic_auth_is_used_when_no_api_key_is_given(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["authorization"] = request.headers.get("Authorization", "")
            return httpx.Response(200, json={"version": {"number": "8.13.0"}})

        provider = ElasticRuntimeProvider(transport=httpx.MockTransport(handler))
        credentials = _credentials(username="svc_nexus", password="pw")
        credentials.pop("api_key")
        ok, _error = await provider.validate_credentials(credentials)

        assert ok is True
        assert seen["authorization"].startswith("Basic ")

    def test_half_a_basic_auth_pair_is_refused(self):
        credentials = _credentials(username="svc_nexus")
        credentials.pop("api_key")
        with pytest.raises(RuntimeProviderError):
            parse_elastic_config(credentials)


# --- provider contract -------------------------------------------------------


class TestProviderContract:
    def test_the_provider_key_and_container_key(self):
        provider = ElasticRuntimeProvider()
        assert provider.provider_key() == "elastic"
        assert provider.container_key_field() == "workspace_id"
        assert provider.static_ref_strategy() is None
        assert provider.source_type_aliases() == {"elastic"}

    def test_it_satisfies_the_connector_protocol(self):
        from src.runtime.providers.base import RuntimeSourceProvider

        assert isinstance(ElasticRuntimeProvider(), RuntimeSourceProvider)

    def test_it_is_registered(self):
        from src.runtime.source_registry import get_source_provider

        provider = get_source_provider("elastic")
        assert provider is not None
        assert provider.provider_key() == "elastic"

    def test_the_ingest_scope_is_the_index_allowlist(self):
        selection = ElasticRuntimeProvider().scope_selection()
        assert selection is not None
        assert selection.field == "index_names"
        assert selection.required is True
        assert selection.multiple is True

class TestPersistenceAgainstTheRealSchema:
    """Every emitted row must fit the existing tables -- no migration needed.

    This is the check that answers "does this provider need a schema change?".
    It writes a full two-tier batch through the same repository calls the sync
    service makes, then reads it back, so a missing column or a violated
    uniqueness constraint fails here rather than on a live cluster.
    """

    async def _persist(self, batch, db_session) -> None:
        from src.runtime.store import RuntimeStoreRepository

        repo = RuntimeStoreRepository(db_session)
        synced_at = batch.synced_at or datetime.now(UTC)
        for environment in batch.environments:
            await repo.upsert_environment(_PROJECT, environment, "elastic", synced_at)
        for workspace in batch.workspaces:
            await repo.upsert_workspace(_PROJECT, workspace, "elastic", synced_at)
        for job in batch.jobs:
            await repo.upsert_job(_PROJECT, job, "elastic", synced_at)
        for execution in batch.executions:
            await repo.upsert_execution(_PROJECT, execution, "elastic", synced_at)
        for failure in batch.failures:
            await repo.upsert_failure(_PROJECT, failure, "elastic", synced_at)
        for job_run in batch.job_runs:
            await repo.upsert_job_run(_PROJECT, job_run, "elastic", synced_at)
        for event in batch.log_events:
            await repo.upsert_log_event(_PROJECT, event, "elastic", synced_at)

    async def _batch(self):
        started = _recent_hour()
        cluster = _FakeElastic(
            [
                _pm_step(run_id="run-1", step_nr=1, step_total=2, status="success", started_at=started),
                _pm_step(
                    run_id="run-1",
                    step_nr=2,
                    step_total=2,
                    status="failed",
                    started_at=started + timedelta(seconds=2),
                    error="ORA-12545: target host does not exist",
                ),
                _app_log(doc_id="a", timestamp=started),
                _app_log(doc_id="b", timestamp=started),
            ]
        )
        return await _sync(cluster, index_names=["process_monitoring*", _APP_INDEX])

    async def test_a_two_tier_batch_persists_and_reads_back(self, db_session):
        from sqlalchemy import func, select

        from src.db.models.runtime_data import (
            RuntimeExecutionRow,
            RuntimeJobRow,
            RuntimeJobRunRow,
            RuntimeLogEventRow,
        )

        batch = await self._batch()
        await self._persist(batch, db_session)

        log_rows = (await db_session.execute(select(RuntimeLogEventRow))).scalars().all()
        assert len(log_rows) == 4
        assert {row.scope for row in log_rows} == {"execution", "engine"}
        assert {row.attribution_safe for row in log_rows} == {True, False}
        # The diagnosis survives to the stored row: redaction removes addresses,
        # not the fault that an operator needs to read.
        assert any("ORA-12545" in row.message for row in log_rows)
        assert all(row.message for row in log_rows)

        job_rows = (await db_session.execute(select(RuntimeJobRow))).scalars().all()
        assert [row.job_id for row in job_rows] == ["AssetCheck_GetLocation"]

        run_rows = (await db_session.execute(select(RuntimeJobRunRow))).scalars().all()
        assert len(run_rows) == 1
        assert run_rows[0].state == "failed"
        assert run_rows[0].raw_state == "failed"
        assert run_rows[0].environment_name == "PROD"

        execution_rows = (await db_session.execute(select(RuntimeExecutionRow))).scalars().all()
        assert [row.execution_id for row in execution_rows] == ["run-1"]

        # The log-to-job join the mirror actually uses.
        joined = (
            await db_session.execute(
                select(func.count())
                .select_from(RuntimeLogEventRow)
                .join(
                    RuntimeExecutionRow,
                    RuntimeExecutionRow.execution_id == RuntimeLogEventRow.execution_id,
                )
            )
        ).scalar_one()
        assert joined == 2

    async def test_re_persisting_the_same_batch_writes_no_second_copy(self, db_session):
        from sqlalchemy import func, select

        from src.db.models.runtime_data import RuntimeLogEventRow

        batch = await self._batch()
        await self._persist(batch, db_session)
        await self._persist(await self._batch(), db_session)

        count = (
            await db_session.execute(select(func.count()).select_from(RuntimeLogEventRow))
        ).scalar_one()
        # Idempotency at the table, not just in the provider: the mirror is
        # already ~868 MB, and a cycle that duplicates it every 30 minutes is
        # the failure the ordinal scheme exists to prevent.
        assert count == 4


class TestProviderContractExtras:
    def test_the_declared_source_row_is_deliberately_empty(self):
        """The self-service path does not persist one; declaring it made a phantom."""
        from src.runtime.providers.elastic import _BatchBuilder

        builder = _BatchBuilder(
            source_key="elastic",
            project_id=_PROJECT,
            synced_at=datetime.now(UTC),
            config=parse_elastic_config(_credentials()),
        )
        batch = builder.build(partial=False, failure_reason_code=None, error_summary=None)
        assert batch.sources == []
