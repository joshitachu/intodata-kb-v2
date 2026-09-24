"""Repository tests for the per-job schedule profile (time-of-day pooling).

The gap this closes: the store computed cadence and duration percentiles but never
time of day, so "which jobs run between 22:00 and 07:00, and at what time" had no
read path at all and was answered as "the run times live in the management console
and were not supplied" -- with 10k PROD runs sitting in the table.

Fixtures are written as LOCAL Amsterdam wall-clock times and converted to the UTC
the store holds, because every assertion here is about what an operator's clock
says. The cases that matter are the ones a naive implementation gets wrong:

* a window that wraps midnight, with runs on both sides of it;
* two runs of one nightly job either side of midnight, which are ONE night;
* the same local time on both sides of a DST transition;
* project scope, which is an access boundary and not a filter.
"""

import datetime
from datetime import UTC
from zoneinfo import ZoneInfo

import pytest

from src.runtime.models import (
    ListRuntimeJobScheduleProfileInput,
    RuntimeExecution,
    RuntimeExecutionState,
)
from src.runtime.store import RuntimeStoreRepository

_A = "proj-a"
_B = "proj-b"
_SRC = "src-1"
_AMS = ZoneInfo("Europe/Amsterdam")
_INGESTED = datetime.datetime(2026, 1, 1, tzinfo=UTC)


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime.datetime:
    """An Amsterdam wall-clock moment, as the UTC instant the store holds."""
    return datetime.datetime(year, month, day, hour, minute, tzinfo=_AMS).astimezone(UTC)


async def _add_run(
    repo: RuntimeStoreRepository,
    *,
    project_id: str,
    execution_id: str,
    job_name: str,
    started_at: datetime.datetime,
    state: str = "success",
    duration_ms: int | None = 60_000,
) -> None:
    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState(state),
            started_at=started_at,
            ended_at=started_at + datetime.timedelta(milliseconds=duration_ms or 0),
            duration_ms=duration_ms,
        ),
        _SRC,
        _INGESTED,
    )


@pytest.fixture
async def repo(db_session):
    """Seed a deterministic set of jobs whose local start times are known exactly.

    proj-a / night_batch  : 3 runs, local 23:50 / 00:10 / 00:30 on 3 separate nights.
    proj-a / one_night    : 2 runs, local 23:55 and 00:05 -- the SAME night.
    proj-a / day_job      : 3 runs, local 09:00 / 10:00 / 11:00 on one day.
    proj-a / dst_job      : 2 runs, local 02:00 in CET and local 02:00 in CEST.
    proj-a / single_run   : 1 run, local 01:15.
    proj-a / failing_night: 2 runs in the night window, one failed.
    proj-b / other_night  : 1 run at local 23:45 -- must never appear under proj-a.
    """
    store = RuntimeStoreRepository(db_session)

    for index, started in enumerate(
        [
            _local(2026, 1, 5, 23, 50),
            _local(2026, 1, 7, 0, 10),
            _local(2026, 1, 8, 0, 30),
        ]
    ):
        await _add_run(
            store,
            project_id=_A,
            execution_id=f"night-{index}",
            job_name="night_batch",
            started_at=started,
            duration_ms=(index + 1) * 100_000,
        )

    for index, started in enumerate([_local(2026, 1, 5, 23, 55), _local(2026, 1, 6, 0, 5)]):
        await _add_run(
            store,
            project_id=_A,
            execution_id=f"one-night-{index}",
            job_name="one_night",
            started_at=started,
        )

    for index, hour in enumerate([9, 10, 11]):
        await _add_run(
            store,
            project_id=_A,
            execution_id=f"day-{index}",
            job_name="day_job",
            started_at=_local(2026, 1, 12, hour),
            duration_ms=(index + 1) * 1_000,
        )

    # 2026-02-10 is CET (UTC+1); 2026-07-10 is CEST (UTC+2). Both are 02:00 local.
    for index, started in enumerate([_local(2026, 2, 10, 2), _local(2026, 7, 10, 2)]):
        await _add_run(
            store,
            project_id=_A,
            execution_id=f"dst-{index}",
            job_name="dst_job",
            started_at=started,
        )

    await _add_run(
        store,
        project_id=_A,
        execution_id="single-0",
        job_name="single_run",
        started_at=_local(2026, 1, 9, 1, 15),
        duration_ms=7_000,
    )

    await _add_run(
        store,
        project_id=_A,
        execution_id="fail-0",
        job_name="failing_night",
        started_at=_local(2026, 1, 9, 2, 0),
        state="failed",
    )
    await _add_run(
        store,
        project_id=_A,
        execution_id="fail-1",
        job_name="failing_night",
        started_at=_local(2026, 1, 10, 2, 0),
        state="success",
    )

    await _add_run(
        store,
        project_id=_B,
        execution_id="other-0",
        job_name="other_night",
        started_at=_local(2026, 1, 5, 23, 45),
    )
    await db_session.flush()
    return store


def _night(**overrides) -> ListRuntimeJobScheduleProfileInput:
    """The 22:00-07:00 Amsterdam window from the question that prompted this."""
    return ListRuntimeJobScheduleProfileInput(
        start_hour=22, end_hour=7, timezone="Europe/Amsterdam", **overrides
    )


def _by_job(rows) -> dict:
    return {row.job_name: row for row in rows}


class TestDaytimeWindow:
    async def test_median_and_bounds_are_observed_local_times(self, repo):
        rows = await repo.list_job_schedule_profile(
            [_A],
            ListRuntimeJobScheduleProfileInput(
                start_hour=8, end_hour=17, job_name="day_job", timezone="Europe/Amsterdam"
            ),
        )
        assert len(rows) == 1
        profile = rows[0]
        assert profile.total_runs == 3
        assert profile.distinct_days == 1
        # Discrete median of 09:00/10:00/11:00 -> ceil(3*0.5)=2 -> the 2nd start.
        assert profile.median_start_time == datetime.time(10, 0)
        assert profile.earliest_start_time == datetime.time(9, 0)
        assert profile.latest_start_time == datetime.time(11, 0)
        # Durations 1000/2000/3000 -> discrete p50 at rank 2 -> 2000.
        assert profile.p50_duration_ms == 2_000
        assert profile.non_success_runs == 0
        assert (profile.window_start_hour, profile.window_end_hour) == (8, 17)
        assert profile.timezone == "Europe/Amsterdam"

    async def test_daytime_window_excludes_the_night_jobs(self, repo):
        rows = await repo.list_job_schedule_profile(
            [_A],
            ListRuntimeJobScheduleProfileInput(
                start_hour=8, end_hour=17, timezone="Europe/Amsterdam"
            ),
        )
        assert set(_by_job(rows)) == {"day_job"}

    async def test_no_window_pools_the_whole_day(self, repo):
        rows = await repo.list_job_schedule_profile(
            [_A], ListRuntimeJobScheduleProfileInput(timezone="Europe/Amsterdam")
        )
        by_job = _by_job(rows)
        assert set(by_job) == {
            "night_batch",
            "one_night",
            "day_job",
            "dst_job",
            "single_run",
            "failing_night",
        }
        assert (by_job["day_job"].window_start_hour, by_job["day_job"].window_end_hour) == (0, 0)


class TestWindowAcrossMidnight:
    async def test_median_is_measured_on_the_window_axis_not_the_raw_clock(self, repo):
        """Starts 23:50 / 00:10 / 00:30 in a 22:00-07:00 window.

        Sorted by raw clock time the middle value is 00:30 -- the wrong answer,
        because 23:50 sorts last. Sorted by position within the window (1h50m,
        2h10m, 2h30m from 22:00) the middle value is 00:10.
        """
        rows = await repo.list_job_schedule_profile([_A], _night(job_name="night_batch"))
        profile = rows[0]
        assert profile.total_runs == 3
        assert profile.median_start_time == datetime.time(0, 10)
        # Same axis for the bounds: a raw min/max would report 00:10 as the
        # earliest start of a window that begins at 22:00.
        assert profile.earliest_start_time == datetime.time(23, 50)
        assert profile.latest_start_time == datetime.time(0, 30)

    async def test_two_runs_either_side_of_midnight_are_one_night(self, repo):
        """23:55 and 00:05 are two calendar dates but one occurrence of the window."""
        rows = await repo.list_job_schedule_profile([_A], _night(job_name="one_night"))
        profile = rows[0]
        assert profile.total_runs == 2
        assert profile.distinct_days == 1
        assert profile.earliest_start_time == datetime.time(23, 55)
        assert profile.latest_start_time == datetime.time(0, 5)
        # n=2 -> discrete median at rank ceil(2*0.5)=1 -> the earlier position.
        assert profile.median_start_time == datetime.time(23, 55)

    async def test_each_night_is_counted_once(self, repo):
        rows = await repo.list_job_schedule_profile([_A], _night(job_name="night_batch"))
        assert rows[0].distinct_days == 3

    async def test_window_end_is_exclusive(self, repo):
        """day_job starts at 09:00 exactly; a window ending at 09:00 must drop it."""
        included = await repo.list_job_schedule_profile(
            [_A],
            ListRuntimeJobScheduleProfileInput(
                start_hour=8, end_hour=10, job_name="day_job", timezone="Europe/Amsterdam"
            ),
        )
        excluded = await repo.list_job_schedule_profile(
            [_A],
            ListRuntimeJobScheduleProfileInput(
                start_hour=5, end_hour=9, job_name="day_job", timezone="Europe/Amsterdam"
            ),
        )
        assert included[0].total_runs == 1
        assert excluded == []


class TestTimezone:
    async def test_utc_and_amsterdam_report_different_clock_times(self, repo):
        """single_run starts 01:15 Amsterdam == 00:15 UTC on a CET date."""
        ams = await repo.list_job_schedule_profile(
            [_A],
            ListRuntimeJobScheduleProfileInput(
                job_name="single_run", timezone="Europe/Amsterdam"
            ),
        )
        utc = await repo.list_job_schedule_profile(
            [_A], ListRuntimeJobScheduleProfileInput(job_name="single_run", timezone="UTC")
        )
        assert ams[0].median_start_time == datetime.time(1, 15)
        assert utc[0].median_start_time == datetime.time(0, 15)
        assert (ams[0].timezone, utc[0].timezone) == ("Europe/Amsterdam", "UTC")

    async def test_dst_transition_does_not_smear_one_schedule_into_two(self, repo):
        """02:00 CET and 02:00 CEST are 01:00 and 00:00 UTC. Both are 02:00 local.

        A single fixed offset -- the shortcut a portable implementation is tempted
        into -- would report one of them as 01:00 or 03:00.
        """
        rows = await repo.list_job_schedule_profile(
            [_A],
            ListRuntimeJobScheduleProfileInput(job_name="dst_job", timezone="Europe/Amsterdam"),
        )
        profile = rows[0]
        assert profile.total_runs == 2
        assert profile.earliest_start_time == datetime.time(2, 0)
        assert profile.latest_start_time == datetime.time(2, 0)
        assert profile.median_start_time == datetime.time(2, 0)

    async def test_dst_run_is_inside_the_night_window_in_both_offsets(self, repo):
        rows = await repo.list_job_schedule_profile([_A], _night(job_name="dst_job"))
        assert rows[0].total_runs == 2


class TestScope:
    async def test_other_project_runs_never_appear(self, repo):
        scoped = await repo.list_job_schedule_profile([_A], _night())
        assert "other_night" not in _by_job(scoped)
        assert {row.project_id for row in scoped} == {_A}

    async def test_unrestricted_scope_sees_both_projects(self, repo):
        unrestricted = await repo.list_job_schedule_profile(None, _night())
        assert {row.project_id for row in unrestricted} == {_A, _B}

    async def test_project_id_filter_cannot_widen_scope(self, repo):
        """project_id is an in-scope filter, never an authorization source."""
        rows = await repo.list_job_schedule_profile([_A], _night(project_id=_B))
        assert rows == []

    async def test_empty_scope_denies_everything(self, repo):
        assert await repo.list_job_schedule_profile([], _night()) == []

    async def test_scope_applies_to_the_bounds_probe_too(self, repo, statements):
        """The offset table is derived from the SAME scoped population.

        An unscoped min/max would build the timezone segments from another
        project's date range, which is both a wrong answer and a leak of when
        that project runs.
        """
        with statements() as recorded:
            await repo.list_job_schedule_profile([_A], _night())
        assert recorded.count == 2
        probe, aggregate = recorded.statements
        assert "min(anon_1.started_at)" in probe
        # Both statements read through the scoped base, and every reference to
        # the executions table in either of them carries the scope predicate.
        for statement in (probe, aggregate):
            assert statement.count("FROM runtime_executions") == statement.count(
                "runtime_executions.project_id IN"
            )
        # Nothing streams whole rows: the aggregate returns one row per job.
        assert "GROUP BY" in aggregate


class TestEdgeCases:
    async def test_single_run_reports_itself_as_median_and_bounds(self, repo):
        rows = await repo.list_job_schedule_profile([_A], _night(job_name="single_run"))
        profile = rows[0]
        assert profile.total_runs == 1
        assert profile.distinct_days == 1
        assert (
            profile.median_start_time,
            profile.earliest_start_time,
            profile.latest_start_time,
        ) == (datetime.time(1, 15),) * 3
        assert profile.p50_duration_ms == 7_000

    async def test_window_with_no_runs_returns_empty_not_an_error(self, repo):
        rows = await repo.list_job_schedule_profile(
            [_A],
            ListRuntimeJobScheduleProfileInput(
                start_hour=14, end_hour=15, timezone="Europe/Amsterdam"
            ),
        )
        assert rows == []

    async def test_unknown_project_returns_empty(self, repo):
        rows = await repo.list_job_schedule_profile([_A], _night(project_id="nope"))
        assert rows == []

    async def test_since_until_window_excludes_runs_outside_it(self, repo):
        rows = await repo.list_job_schedule_profile(
            [_A],
            _night(
                job_name="night_batch",
                since=_local(2026, 1, 6, 12),
                until=_local(2026, 1, 9, 12),
            ),
        )
        assert rows[0].total_runs == 2
        assert rows[0].earliest_start_time == datetime.time(0, 10)

    async def test_bounded_window_skips_the_extra_bounds_query(self, repo, statements):
        with statements() as recorded:
            await repo.list_job_schedule_profile(
                [_A], _night(since=_local(2026, 1, 1, 0), until=_local(2026, 12, 31, 0))
            )
        assert recorded.count == 1

    async def test_non_success_runs_are_counted(self, repo):
        rows = await repo.list_job_schedule_profile([_A], _night(job_name="failing_night"))
        assert rows[0].total_runs == 2
        assert rows[0].non_success_runs == 1

    async def test_rows_are_ordered_by_run_count(self, repo):
        rows = await repo.list_job_schedule_profile([_A], _night())
        counts = [row.total_runs for row in rows]
        assert counts == sorted(counts, reverse=True)

    async def test_limit_and_offset_page_the_result(self, repo):
        first = await repo.list_job_schedule_profile([_A], _night(limit=1))
        second = await repo.list_job_schedule_profile([_A], _night(limit=1, offset=1))
        assert len(first) == 1 and len(second) == 1
        assert first[0].job_name != second[0].job_name


class TestInputValidation:
    def test_unknown_timezone_is_rejected_at_the_boundary(self):
        with pytest.raises(ValueError, match="Unknown IANA timezone"):
            ListRuntimeJobScheduleProfileInput(timezone="Mars/Olympus_Mons")

    def test_half_a_window_is_rejected(self):
        with pytest.raises(ValueError, match="must be given together"):
            ListRuntimeJobScheduleProfileInput(start_hour=22)

    def test_hours_outside_a_day_are_rejected(self):
        with pytest.raises(ValueError):
            ListRuntimeJobScheduleProfileInput(start_hour=22, end_hour=24)
