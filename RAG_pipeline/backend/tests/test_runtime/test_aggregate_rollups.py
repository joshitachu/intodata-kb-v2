"""Repository tests for the SQL-computed runtime aggregate rollups.

Covers the surfaces that previously had no read path: outcome trend, job
reliability, component hotspots, job throughput, and error signatures — plus the
new duration/completion filters on the execution list.

Two projects (`proj-a`, `proj-b`) are seeded so every test can assert that project
scope narrows results and that an empty scope denies everything. Fixtures use
hand-picked durations so the expected percentile is unambiguous: percentiles are
discrete (the value at the ceil-ranked position), matching `_percentile`.
"""

import datetime
from datetime import UTC

import pytest

from src.db.models.runtime_insights import RuntimeErrorSignatureRow
from src.runtime.models import (
    ListExecutionsInput,
    ListRuntimeComponentHotspotsInput,
    ListRuntimeErrorSignaturesInput,
    ListRuntimeJobReliabilityInput,
    ListRuntimeJobThroughputInput,
    RuntimeComponentMetric,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeTrendBucket,
    RuntimeTrendInput,
)
from src.runtime.store import RuntimeStoreRepository

_A = "proj-a"
_B = "proj-b"
_SRC = "src-1"
# Day boundary anchor: runs are placed inside a single UTC day plus one the next day,
# so a day bucket must split them 6/1 regardless of the backend's session timezone.
_DAY_ONE = datetime.datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
_DAY_TWO = datetime.datetime(2026, 8, 11, 3, 0, tzinfo=UTC)


async def _add_execution(
    repo: RuntimeStoreRepository,
    *,
    project_id: str,
    execution_id: str,
    job_name: str,
    state: str,
    started_at: datetime.datetime,
    duration_ms: int | None,
    ended: bool = True,
) -> None:
    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState(state),
            started_at=started_at,
            ended_at=(
                started_at + datetime.timedelta(milliseconds=duration_ms or 0) if ended else None
            ),
            duration_ms=duration_ms,
        ),
        _SRC,
        _DAY_ONE,
    )


@pytest.fixture
async def repo(db_session):
    """Seed a deterministic two-project runtime dataset.

    proj-a / jobA : 5 finished runs, durations 100..500, the two most recent failed.
    proj-a / jobB : 1 finished success (1_000_000 ms) + 1 never-completed unknown run.
    proj-b / jobZ : 1 finished success — the row that must never leak into proj-a.
    """
    store = RuntimeStoreRepository(db_session)
    job_a = [
        ("a1", "success", 100),
        ("a2", "success", 200),
        ("a3", "success", 300),
        ("a4", "failed", 400),
        ("a5", "failed", 500),
    ]
    for index, (execution_id, state, duration) in enumerate(job_a):
        await _add_execution(
            store,
            project_id=_A,
            execution_id=execution_id,
            job_name="jobA",
            state=state,
            started_at=_DAY_ONE + datetime.timedelta(hours=index),
            duration_ms=duration,
        )
    await _add_execution(
        store,
        project_id=_A,
        execution_id="b1",
        job_name="jobB",
        state="success",
        started_at=_DAY_ONE + datetime.timedelta(hours=5),
        duration_ms=1_000_000,
    )
    await _add_execution(
        store,
        project_id=_A,
        execution_id="b2",
        job_name="jobB",
        state="unknown",
        started_at=_DAY_TWO,
        duration_ms=None,
        ended=False,
    )
    await _add_execution(
        store,
        project_id=_B,
        execution_id="z1",
        job_name="jobZ",
        state="success",
        started_at=_DAY_ONE,
        duration_ms=42,
    )

    for execution_id, component, rows, rejected, duration in [
        ("a1", "tIn", 10, 1, 50),
        ("a2", "tIn", 20, 0, 150),
        ("a4", "tOut", 5, 5, 900),
    ]:
        await store.upsert_component_metric(
            _A,
            RuntimeComponentMetric(
                execution_id=execution_id,
                component_key=component,
                component_type=component,
                source_connector_type="srcConn",
                target_connector_type="tgtConn",
                rows_total=rows,
                rows_rejected=rejected,
                duration_ms=duration,
                measured_at=_DAY_ONE,
            ),
            _SRC,
            _DAY_ONE,
        )
    await store.upsert_component_metric(
        _B,
        RuntimeComponentMetric(
            execution_id="z1",
            component_key="tOther",
            rows_total=7,
            duration_ms=7,
            measured_at=_DAY_ONE,
        ),
        _SRC,
        _DAY_ONE,
    )

    db_session.add(
        RuntimeErrorSignatureRow(
            project_id=_A,
            signature_hash="sig-truncation",
            error_code="8152",
            exception_class="BatchUpdateException",
            component_name="tOut",
            summary_text="String or binary data would be truncated",
            occurrence_count=16,
            first_seen_at=_DAY_ONE,
            last_seen_at=_DAY_TWO,
        )
    )
    db_session.add(
        RuntimeErrorSignatureRow(
            project_id=_A,
            signature_hash="sig-timeout",
            exception_class="TimeoutException",
            occurrence_count=2,
            first_seen_at=_DAY_ONE,
            last_seen_at=_DAY_ONE,
        )
    )
    db_session.add(
        RuntimeErrorSignatureRow(
            project_id=_B,
            signature_hash="sig-other",
            occurrence_count=99,
            last_seen_at=_DAY_TWO,
        )
    )
    await db_session.flush()
    return store


class TestOutcomeTrend:
    async def test_day_buckets_split_on_utc_boundary(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY, limit=10)
        )
        assert [p.bucket_start.date().isoformat() for p in points] == [
            "2026-08-11",
            "2026-08-10",
        ]
        newest, oldest = points
        assert (newest.total_runs, newest.failed_runs, newest.unknown_runs) == (1, 0, 1)
        assert (oldest.total_runs, oldest.success_runs, oldest.failed_runs) == (6, 4, 2)

    async def test_percentiles_are_discrete_observed_durations(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY, limit=10)
        )
        oldest = points[-1]
        # Day-one durations sorted: 100 200 300 400 500 1_000_000 (n=6).
        # p50 -> ceil(6*0.5)=3 -> 300.  p95 -> ceil(6*0.95)=6 -> 1_000_000.
        assert oldest.p50_duration_ms == 300
        assert oldest.p95_duration_ms == 1_000_000
        assert oldest.max_duration_ms == 1_000_000
        assert oldest.failure_rate == pytest.approx(2 / 6)

    async def test_bucket_with_only_null_durations_reports_no_percentiles(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY, limit=10)
        )
        newest = points[0]
        assert newest.p50_duration_ms is None
        assert newest.p95_duration_ms is None

    async def test_hour_buckets_separate_runs(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.HOUR, limit=50)
        )
        assert len(points) == 7
        assert all(p.total_runs == 1 for p in points)

    async def test_week_bucket_groups_the_whole_window(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.WEEK, limit=10)
        )
        assert len(points) == 1
        assert points[0].total_runs == 7
        # 2026-08-10 is a Monday, so the week bucket starts on the same day.
        assert points[0].bucket_start.date().isoformat() == "2026-08-10"

    async def test_since_until_window_filters(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY, since=_DAY_TWO, limit=10)
        )
        assert len(points) == 1
        assert points[0].total_runs == 1

    async def test_job_name_filter(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.WEEK, job_name="jobA", limit=10)
        )
        assert points[0].total_runs == 5

    async def test_limit_caps_returned_buckets(self, repo):
        points = await repo.get_outcome_trend(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY, limit=1)
        )
        assert [p.bucket_start.date().isoformat() for p in points] == ["2026-08-11"]

    async def test_scope_excludes_other_project(self, repo):
        scoped = await repo.get_outcome_trend([_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.WEEK))
        unrestricted = await repo.get_outcome_trend(None, RuntimeTrendInput(bucket=RuntimeTrendBucket.WEEK))
        assert scoped[0].total_runs == 7
        assert unrestricted[0].total_runs == 8

    async def test_empty_scope_denies_all(self, repo):
        assert await repo.get_outcome_trend([], RuntimeTrendInput()) == []

    async def test_coverage_bounds_ignore_display_window_but_honor_scope(self, repo):
        coverage = await repo.get_outcome_trend_coverage(
            [_A], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY, since=_DAY_TWO)
        )
        assert coverage.observed_from == _DAY_ONE
        assert coverage.observed_until == _DAY_TWO
        assert coverage.completeness == "unknown"

        denied = await repo.get_outcome_trend_coverage([], RuntimeTrendInput())
        assert denied.observed_from is None
        assert denied.observed_until is None


class TestJobReliability:
    async def test_counts_streak_and_percentiles(self, repo):
        rows = await repo.list_job_reliability([_A], ListRuntimeJobReliabilityInput())
        by_job = {row.job_name: row for row in rows}
        job_a = by_job["jobA"]
        assert (job_a.total_runs, job_a.success_runs, job_a.failed_runs) == (5, 3, 2)
        assert job_a.failure_rate == pytest.approx(0.4)
        # a4 and a5 are the two most recent runs and both failed.
        assert job_a.current_failure_streak == 2
        assert job_a.latest_state is RuntimeExecutionState.FAILED
        assert job_a.latest_unfinished is False
        # durations 100..500 (n=5): p50 -> ceil(2.5)=3 -> 300; p95 -> ceil(4.75)=5 -> 500.
        assert (job_a.p50_duration_ms, job_a.p95_duration_ms) == (300, 500)
        assert job_a.max_duration_ms == 500
        assert job_a.first_run_at == _DAY_ONE
        assert job_a.last_run_at == _DAY_ONE + datetime.timedelta(hours=4)

    async def test_streak_is_zero_when_latest_run_succeeded(self, repo):
        rows = await repo.list_job_reliability(
            [_A], ListRuntimeJobReliabilityInput(job_name="jobB")
        )
        assert rows[0].current_failure_streak == 0

    async def test_unfinished_runs_are_counted_and_excluded_from_percentiles(self, repo):
        rows = await repo.list_job_reliability(
            [_A], ListRuntimeJobReliabilityInput(job_name="jobB")
        )
        job_b = rows[0]
        assert job_b.total_runs == 2
        assert job_b.unfinished_runs == 1
        assert job_b.latest_state is RuntimeExecutionState.UNKNOWN
        assert job_b.latest_unfinished is True
        # Only b1 has a duration, so both percentiles collapse onto it.
        assert (job_b.p50_duration_ms, job_b.p95_duration_ms) == (1_000_000, 1_000_000)

    async def test_attention_only_keeps_only_latest_unresolved_state(self, repo):
        # jobC had a failure, but its later successful run resolved it. jobD is
        # still running and therefore is not "never finished" attention.
        await _add_execution(
            repo,
            project_id=_A,
            execution_id="c1",
            job_name="jobC",
            state="failed",
            started_at=_DAY_ONE,
            duration_ms=100,
        )
        await _add_execution(
            repo,
            project_id=_A,
            execution_id="c2",
            job_name="jobC",
            state="success",
            started_at=_DAY_TWO + datetime.timedelta(hours=1),
            duration_ms=100,
        )
        await _add_execution(
            repo,
            project_id=_A,
            execution_id="d1",
            job_name="jobD",
            state="running",
            started_at=_DAY_TWO + datetime.timedelta(hours=2),
            duration_ms=None,
            ended=False,
        )

        params = ListRuntimeJobReliabilityInput(attention_only=True)
        rows = await repo.list_job_reliability([_A], params)
        assert [row.job_name for row in rows] == ["jobB", "jobA"]
        assert await repo.count_job_reliability([_A], params) == 2

    async def test_ordering_puts_most_failures_first(self, repo):
        rows = await repo.list_job_reliability([_A], ListRuntimeJobReliabilityInput())
        assert [row.job_name for row in rows] == ["jobA", "jobB"]

    async def test_pagination_and_total(self, repo):
        params = ListRuntimeJobReliabilityInput(limit=1, offset=1)
        rows = await repo.list_job_reliability([_A], params)
        assert [row.job_name for row in rows] == ["jobB"]
        assert await repo.count_job_reliability([_A], params) == 2

    async def test_scope_excludes_other_project(self, repo):
        rows = await repo.list_job_reliability([_A], ListRuntimeJobReliabilityInput())
        assert "jobZ" not in {row.job_name for row in rows}
        assert await repo.count_job_reliability([_B], ListRuntimeJobReliabilityInput()) == 1

    async def test_empty_scope_denies_all(self, repo):
        assert await repo.list_job_reliability([], ListRuntimeJobReliabilityInput()) == []
        assert await repo.count_job_reliability([], ListRuntimeJobReliabilityInput()) == 0


class TestComponentHotspots:
    async def test_rolls_up_rows_and_durations_per_component(self, repo):
        rows = await repo.list_component_hotspots([_A], ListRuntimeComponentHotspotsInput())
        by_component = {row.component_key: row for row in rows}
        t_in = by_component["tIn"]
        assert t_in.executions == 2
        assert (t_in.rows_total, t_in.rows_rejected) == (30, 1)
        assert t_in.reject_ratio == pytest.approx(1 / 30)
        assert t_in.avg_duration_ms == 100
        # durations 50,150 (n=2): p95 -> ceil(1.9)=2 -> 150.
        assert t_in.p95_duration_ms == 150
        assert t_in.max_duration_ms == 150
        assert t_in.source_connector_type == "srcConn"

    async def test_full_reject_ratio(self, repo):
        rows = await repo.list_component_hotspots([_A], ListRuntimeComponentHotspotsInput())
        t_out = {row.component_key: row for row in rows}["tOut"]
        assert t_out.reject_ratio == pytest.approx(1.0)

    async def test_ordering_is_highest_volume_first(self, repo):
        rows = await repo.list_component_hotspots([_A], ListRuntimeComponentHotspotsInput())
        assert [row.component_key for row in rows] == ["tIn", "tOut"]

    async def test_component_filters(self, repo):
        rows = await repo.list_component_hotspots(
            [_A], ListRuntimeComponentHotspotsInput(component_key="tOut")
        )
        assert [row.component_key for row in rows] == ["tOut"]

    async def test_pagination_and_total(self, repo):
        params = ListRuntimeComponentHotspotsInput(limit=1)
        assert len(await repo.list_component_hotspots([_A], params)) == 1
        assert await repo.count_component_hotspots([_A], params) == 2

    async def test_scope_excludes_other_project(self, repo):
        rows = await repo.list_component_hotspots([_A], ListRuntimeComponentHotspotsInput())
        assert "tOther" not in {row.component_key for row in rows}

    async def test_empty_scope_denies_all(self, repo):
        assert await repo.list_component_hotspots([], ListRuntimeComponentHotspotsInput()) == []
        assert await repo.count_component_hotspots([], ListRuntimeComponentHotspotsInput()) == 0


class TestJobThroughput:
    async def test_joins_metrics_to_owning_job(self, repo):
        rows = await repo.list_job_throughput([_A], ListRuntimeJobThroughputInput())
        assert len(rows) == 1
        job_a = rows[0]
        assert job_a.job_name == "jobA"
        assert job_a.executions_with_metrics == 3
        assert job_a.component_count == 2
        assert (job_a.rows_total, job_a.rows_rejected) == (35, 6)
        assert job_a.reject_ratio == pytest.approx(6 / 35)

    async def test_job_without_metrics_is_absent(self, repo):
        rows = await repo.list_job_throughput([_A], ListRuntimeJobThroughputInput())
        assert "jobB" not in {row.job_name for row in rows}

    async def test_total_counts_groups(self, repo):
        assert await repo.count_job_throughput([_A], ListRuntimeJobThroughputInput()) == 1

    async def test_scope_excludes_other_project(self, repo):
        rows = await repo.list_job_throughput([_B], ListRuntimeJobThroughputInput())
        assert [row.job_name for row in rows] == ["jobZ"]

    async def test_empty_scope_denies_all(self, repo):
        assert await repo.list_job_throughput([], ListRuntimeJobThroughputInput()) == []
        assert await repo.count_job_throughput([], ListRuntimeJobThroughputInput()) == 0


class TestErrorSignatures:
    async def test_orders_by_recurrence(self, repo):
        rows = await repo.list_error_signatures([_A], ListRuntimeErrorSignaturesInput())
        assert [row.signature_hash for row in rows] == ["sig-truncation", "sig-timeout"]
        assert rows[0].occurrence_count == 16
        assert rows[0].error_code == "8152"
        assert rows[0].summary_text == "String or binary data would be truncated"

    async def test_filters(self, repo):
        rows = await repo.list_error_signatures(
            [_A], ListRuntimeErrorSignaturesInput(exception_class="TimeoutException")
        )
        assert [row.signature_hash for row in rows] == ["sig-timeout"]

    async def test_since_filters_on_last_seen(self, repo):
        rows = await repo.list_error_signatures(
            [_A], ListRuntimeErrorSignaturesInput(since=_DAY_TWO)
        )
        assert [row.signature_hash for row in rows] == ["sig-truncation"]

    async def test_pagination_and_total(self, repo):
        params = ListRuntimeErrorSignaturesInput(limit=1, offset=1)
        rows = await repo.list_error_signatures([_A], params)
        assert [row.signature_hash for row in rows] == ["sig-timeout"]
        assert await repo.count_error_signatures([_A], params) == 2

    async def test_scope_excludes_other_project(self, repo):
        rows = await repo.list_error_signatures([_A], ListRuntimeErrorSignaturesInput())
        assert "sig-other" not in {row.signature_hash for row in rows}
        assert await repo.count_error_signatures([_A], ListRuntimeErrorSignaturesInput()) == 2

    async def test_empty_scope_denies_all(self, repo):
        assert await repo.list_error_signatures([], ListRuntimeErrorSignaturesInput()) == []
        assert await repo.count_error_signatures([], ListRuntimeErrorSignaturesInput()) == 0


class TestExecutionDurationFilters:
    async def test_unfinished_only(self, repo):
        rows = await repo.list_executions([_A], ListExecutionsInput(unfinished=True))
        assert [row.execution_id for row in rows] == ["b2"]

    async def test_finished_only_excludes_open_runs(self, repo):
        rows = await repo.list_executions([_A], ListExecutionsInput(unfinished=False, limit=100))
        assert "b2" not in {row.execution_id for row in rows}

    async def test_min_duration_surfaces_implausible_runs(self, repo):
        rows = await repo.list_executions([_A], ListExecutionsInput(min_duration_ms=1000))
        assert [row.execution_id for row in rows] == ["b1"]

    async def test_count_matches_the_filtered_page(self, repo):
        params = ListExecutionsInput(min_duration_ms=1000)
        assert await repo.count_executions([_A], params) == 1
        params = ListExecutionsInput(unfinished=True)
        assert await repo.count_executions([_A], params) == 1

    async def test_filters_respect_scope(self, repo):
        params = ListExecutionsInput(min_duration_ms=1)
        assert await repo.count_executions([_A], params) == 6
        assert await repo.count_executions([_B], params) == 1
        assert await repo.count_executions([], params) == 0
