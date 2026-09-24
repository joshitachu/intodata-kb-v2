"""Pins the SQL-aggregated runtime health surfaces.

`list_engine_health`, `list_job_health`, `list_plan_health`,
`list_health_summaries` and `get_estate_overview` used to fetch every run row in
scope and reduce it in Python. They now aggregate in SQL. These tests pin the
numbers, the tie-break order, and the two things that made the old shape slow, so
a future refactor cannot quietly change a count or reintroduce a whole-row read:

* the plan-run read must never select `step_results` -- a JSON column whose TOAST
  measured 157 MB against a 2.5 MB heap on a live mirror;
* `_percentile_at_rank` (SQL) must agree with `_percentile` (Python) exactly.
"""

import datetime
import random
from datetime import UTC

import pytest
from sqlalchemy import select

from src.db.models.runtime_data import RuntimeJobRunRow
from src.db.models.runtime_insights import RuntimeIncidentRow
from src.runtime.models import (
    ListRuntimeEnginesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    RuntimeComponentMetric,
    RuntimeEngine,
    RuntimeEngineStatus,
    RuntimeExecutableKind,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJob,
    RuntimeJobRun,
    RuntimePlan,
    RuntimePlanRun,
    RuntimeSource,
    RuntimeSyncStatus,
)
from src.runtime.store import RuntimeStoreRepository
from src.runtime.store.repository import _percentile

_P = "proj-a"
_SRC = "src-1"
_T0 = datetime.datetime(2026, 3, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime.datetime:
    return _T0 + datetime.timedelta(minutes=minutes)


async def _add_engine(
    repo,
    engine_id="engine-1",
    name="engine one",
    status=RuntimeEngineStatus.ONLINE,
    project_id=_P,
    environment_name="Production",
    workspace_name="Main",
):
    await repo.upsert_engine(
        project_id,
        RuntimeEngine(
            source_key=_SRC,
            engine_id=engine_id,
            name=name,
            status=status,
            environment_id="env-prod",
            environment_name=environment_name,
            workspace_id="ws-main",
            workspace_name=workspace_name,
            last_seen_at=_T0,
        ),
        _SRC,
        _T0,
    )


async def _add_job_run(
    repo,
    *,
    run_id,
    state,
    minute,
    job_id="job-1",
    job_name="orders",
    engine_id="engine-1",
    engine_name="engine one",
    duration_ms=1000,
    project_id=_P,
    environment_name="Production",
    workspace_name="Main",
    executable_kind=RuntimeExecutableKind.JOB,
):
    await repo.upsert_job_run(
        project_id,
        RuntimeJobRun(
            source_key=_SRC,
            run_id=run_id,
            job_id=job_id,
            job_name=job_name,
            execution_id=run_id,
            state=state,
            executable_kind=executable_kind,
            environment_id="env-prod",
            environment_name=environment_name,
            workspace_id="ws-main",
            workspace_name=workspace_name,
            engine_id=engine_id,
            engine_name=engine_name,
            started_at=_at(minute),
            ended_at=_at(minute) + datetime.timedelta(seconds=1),
            duration_ms=duration_ms,
        ),
        _SRC,
        _T0,
    )


async def _add_plan_run(
    repo,
    *,
    run_id,
    state,
    minute,
    plan_id="plan-1",
    plan_name="nightly",
    project_id=_P,
):
    await repo.upsert_plan_run(
        project_id,
        RuntimePlanRun(
            source_key=_SRC,
            run_id=run_id,
            plan_id=plan_id,
            plan_name=plan_name,
            state=state,
            environment_id="env-prod",
            environment_name="Production",
            workspace_id="ws-main",
            workspace_name="Main",
            started_at=_at(minute),
            ended_at=_at(minute) + datetime.timedelta(seconds=10),
            duration_ms=10_000,
            step_results=[],
        ),
        _SRC,
        _T0,
    )


async def _add_execution(
    repo, *, execution_id, job_name, state, minute, duration_ms=1000, project_id=_P
):
    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=state,
            started_at=_at(minute),
            ended_at=_at(minute) + datetime.timedelta(seconds=1),
            duration_ms=duration_ms,
        ),
        _SRC,
        _T0,
    )


class TestFailureCountsAndStreak:
    """`failed_runs`/`failure_rate` are FAILED-only; the streak is consecutive-from-newest."""

    @pytest.mark.parametrize(
        "states,expected_streak",
        [
            # newest last; the streak counts backwards from the newest run
            ([RuntimeExecutionState.FAILED], 1),
            ([RuntimeExecutionState.SUCCESS], 0),
            ([RuntimeExecutionState.FAILED, RuntimeExecutionState.SUCCESS], 0),
            ([RuntimeExecutionState.SUCCESS, RuntimeExecutionState.FAILED], 1),
            (
                [
                    RuntimeExecutionState.FAILED,
                    RuntimeExecutionState.SUCCESS,
                    RuntimeExecutionState.FAILED,
                    RuntimeExecutionState.FAILED,
                ],
                2,
            ),
            # Every run failed: the whole group is one unbroken streak.
            (
                [RuntimeExecutionState.FAILED] * 5,
                5,
            ),
            # A misfire is not a failure, but it does break a streak of them.
            (
                [
                    RuntimeExecutionState.FAILED,
                    RuntimeExecutionState.FAILED,
                    RuntimeExecutionState.MISFIRED,
                ],
                0,
            ),
        ],
    )
    async def test_job_health_streak(self, db_session, states, expected_streak):
        repo = RuntimeStoreRepository(db_session)
        for index, state in enumerate(states):
            await _add_job_run(repo, run_id=f"r{index}", state=state, minute=index)

        health = await repo.list_job_health([_P], ListRuntimeJobsInput())

        assert len(health) == 1
        row = health[0]
        failed = sum(1 for state in states if state == RuntimeExecutionState.FAILED)
        assert row.total_runs == len(states)
        assert row.failed_runs == failed
        assert row.failure_rate == failed / len(states)
        assert row.current_failure_streak == expected_streak
        assert row.latest_state == states[-1]
        assert row.latest_run_at == _at(len(states) - 1)

    async def test_misfire_is_not_counted_as_a_failure(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(repo, run_id="a", state=RuntimeExecutionState.MISFIRED, minute=0)
        await _add_job_run(repo, run_id="b", state=RuntimeExecutionState.TIMED_OUT, minute=1)

        row = (await repo.list_job_health([_P], ListRuntimeJobsInput()))[0]

        assert row.total_runs == 2
        assert row.failed_runs == 0
        assert row.failure_rate == 0.0
        assert row.current_failure_streak == 0

    async def test_plan_and_engine_health_agree_with_job_health(self, db_session):
        """One rule, three surfaces: the same run sequence must yield the same numbers."""
        repo = RuntimeStoreRepository(db_session)
        states = [
            RuntimeExecutionState.SUCCESS,
            RuntimeExecutionState.FAILED,
            RuntimeExecutionState.FAILED,
        ]
        for index, state in enumerate(states):
            await _add_job_run(repo, run_id=f"j{index}", state=state, minute=index)
            await _add_plan_run(repo, run_id=f"p{index}", state=state, minute=index)

        job = (await repo.list_job_health([_P], ListRuntimeJobsInput()))[0]
        engine = (await repo.list_engine_health([_P], ListRuntimeEnginesInput()))[0]
        plan = (await repo.list_plan_health([_P], ListRuntimePlansInput()))[0]

        for row in (job, engine, plan):
            assert (row.total_runs, row.failed_runs, row.current_failure_streak) == (3, 2, 2)
            assert row.failure_rate == pytest.approx(2 / 3)
            assert row.latest_state == RuntimeExecutionState.FAILED

    async def test_plan_health_reports_the_latest_finish(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_plan_run(repo, run_id="p0", state=RuntimeExecutionState.SUCCESS, minute=0)
        await _add_plan_run(repo, run_id="p1", state=RuntimeExecutionState.FAILED, minute=30)

        row = (await repo.list_plan_health([_P], ListRuntimePlansInput()))[0]

        assert row.latest_run_at == _at(30)
        assert row.latest_finish_at == _at(30) + datetime.timedelta(seconds=10)


class TestAttentionFilter:
    @pytest.mark.parametrize(
        "state,expected",
        [
            (RuntimeExecutionState.FAILED, 1),
            (RuntimeExecutionState.MISFIRED, 1),
            (RuntimeExecutionState.TIMED_OUT, 1),
            # An operator already looked at a termination, so it is not attention.
            (RuntimeExecutionState.TERMINATED, 0),
            (RuntimeExecutionState.SUCCESS, 0),
        ],
    )
    async def test_attention_only_covers_every_state_a_human_must_see(
        self, db_session, state, expected
    ):
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(repo, run_id="r0", state=state, minute=0)
        await _add_plan_run(repo, run_id="p0", state=state, minute=0)

        jobs = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(), attention_only=True
        )
        plans = await repo.list_plan_health(
            [_P], ListRuntimePlansInput(), attention_only=True
        )
        overview = await repo.get_estate_overview([_P], ListRuntimeInventoryInput())

        assert len(jobs) == expected
        assert len(plans) == expected
        assert overview.jobs_unhealthy == expected
        assert overview.plans_unhealthy == expected

    async def test_a_group_qualifies_on_any_run_not_just_the_newest(self, db_session):
        """Distinguishes this filter from the latest-state one on `job-reliability`."""
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(repo, run_id="r0", state=RuntimeExecutionState.FAILED, minute=0)
        await _add_job_run(repo, run_id="r1", state=RuntimeExecutionState.SUCCESS, minute=1)

        health = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(), attention_only=True
        )

        assert [row.job_id for row in health] == ["job-1"]


class TestOrdering:
    async def test_orders_by_failure_rate_then_streak_then_recency(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        # worst: 100% failure. middle: 50%. best: healthy but most recent.
        await _add_job_run(
            repo, run_id="w0", job_id="worst", job_name="worst",
            state=RuntimeExecutionState.FAILED, minute=0,
        )
        await _add_job_run(
            repo, run_id="m0", job_id="mid", job_name="mid",
            state=RuntimeExecutionState.FAILED, minute=1,
        )
        await _add_job_run(
            repo, run_id="m1", job_id="mid", job_name="mid",
            state=RuntimeExecutionState.SUCCESS, minute=2,
        )
        await _add_job_run(
            repo, run_id="b0", job_id="best", job_name="best",
            state=RuntimeExecutionState.SUCCESS, minute=99,
        )

        health = await repo.list_job_health([_P], ListRuntimeJobsInput())

        assert [row.job_id for row in health] == ["worst", "mid", "best"]

    async def test_fully_tied_groups_come_back_in_a_stable_defined_order(self, db_session):
        """The old stable sort left ties in heap-fetch order, which SQL does not define.

        Twenty healthy jobs share one sort key (rate 0, streak 0) and one
        timestamp, so nothing but the tie-break separates them. The order must be
        insertion order and it must not change between identical calls.
        """
        repo = RuntimeStoreRepository(db_session)
        for index in range(20):
            await _add_job_run(
                repo,
                run_id=f"r{index}",
                job_id=f"job-{index:02d}",
                job_name=f"job-{index:02d}",
                state=RuntimeExecutionState.SUCCESS,
                minute=0,
            )

        first = await repo.list_job_health([_P], ListRuntimeJobsInput(limit=500))
        second = await repo.list_job_health([_P], ListRuntimeJobsInput(limit=500))

        assert [row.job_id for row in first] == [f"job-{i:02d}" for i in range(20)]
        assert [row.job_id for row in first] == [row.job_id for row in second]

    async def test_limit_truncates_after_ordering(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        for index, state in enumerate(
            [RuntimeExecutionState.SUCCESS, RuntimeExecutionState.FAILED]
        ):
            await _add_job_run(
                repo,
                run_id=f"r{index}",
                job_id=f"job-{index}",
                job_name=f"job-{index}",
                state=state,
                minute=index,
            )

        health = await repo.list_job_health([_P], ListRuntimeJobsInput(limit=1))

        assert [row.job_id for row in health] == ["job-1"]


class TestDurationPercentiles:
    @pytest.mark.parametrize("size", list(range(1, 26)))
    async def test_sql_percentiles_match_the_python_reference(self, db_session, size):
        """`_percentile_at_rank` must reproduce `_percentile` for every group size.

        Durations are inserted in shuffled order so a percentile that happened to
        agree only on pre-sorted input would fail here.
        """
        repo = RuntimeStoreRepository(db_session)
        durations = [(index + 1) * 137 for index in range(size)]
        shuffled = durations[:]
        random.Random(size).shuffle(shuffled)
        for index, duration in enumerate(shuffled):
            await _add_job_run(
                repo,
                run_id=f"r{index}",
                state=RuntimeExecutionState.SUCCESS,
                minute=index,
                duration_ms=duration,
            )
        await _add_engine(repo)

        row = (await repo.list_engine_health([_P], ListRuntimeEnginesInput()))[0]

        assert row.p50_duration_ms == _percentile(durations, 50)
        assert row.p95_duration_ms == _percentile(durations, 95)

    async def test_percentiles_ignore_runs_with_no_duration(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_engine(repo)
        await _add_job_run(
            repo, run_id="a", state=RuntimeExecutionState.SUCCESS, minute=0, duration_ms=500
        )
        await _add_job_run(
            repo, run_id="b", state=RuntimeExecutionState.RUNNING, minute=1, duration_ms=None
        )

        row = (await repo.list_engine_health([_P], ListRuntimeEnginesInput()))[0]

        assert row.total_runs == 2
        assert row.p50_duration_ms == 500
        assert row.p95_duration_ms == 500

    async def test_percentiles_are_null_when_no_run_reported_a_duration(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_engine(repo)
        await _add_job_run(
            repo, run_id="a", state=RuntimeExecutionState.RUNNING, minute=0, duration_ms=None
        )

        row = (await repo.list_engine_health([_P], ListRuntimeEnginesInput()))[0]

        assert row.p50_duration_ms is None
        assert row.p95_duration_ms is None


class TestLabelResolution:
    async def test_inventory_label_wins_over_the_latest_run(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_job(
            _P,
            RuntimeJob(
                source_key=_SRC,
                job_id="job-1",
                name="inventory name",
                executable_kind=RuntimeExecutableKind.JOB,
                environment_name="Inventory Env",
                workspace_name="Inventory WS",
                engine_name="Inventory Engine",
            ),
            _SRC,
            _T0,
        )
        await _add_job_run(
            repo,
            run_id="r0",
            state=RuntimeExecutionState.SUCCESS,
            minute=0,
            job_name="run name",
            environment_name="Run Env",
            workspace_name="Run WS",
            engine_name="Run Engine",
        )

        row = (await repo.list_job_health([_P], ListRuntimeJobsInput()))[0]

        assert row.job_name == "inventory name"
        assert row.environment_name == "Inventory Env"
        assert row.workspace_name == "Inventory WS"
        assert row.engine_name == "Inventory Engine"

    async def test_falls_back_to_the_latest_run_with_no_inventory_row(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(
            repo, run_id="r0", state=RuntimeExecutionState.SUCCESS, minute=0,
            job_name="older name", environment_name="Older Env",
        )
        await _add_job_run(
            repo, run_id="r1", state=RuntimeExecutionState.SUCCESS, minute=1,
            job_name="newest name", environment_name="Newest Env",
        )

        row = (await repo.list_job_health([_P], ListRuntimeJobsInput()))[0]

        assert row.job_name == "newest name"
        assert row.environment_name == "Newest Env"

    async def test_a_null_on_the_newest_run_does_not_inherit_an_older_value(self, db_session):
        """The label describes the latest run, so an older run's value is not a fallback."""
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(
            repo, run_id="r0", state=RuntimeExecutionState.SUCCESS, minute=0,
            environment_name="Older Env",
        )
        await _add_job_run(
            repo, run_id="r1", state=RuntimeExecutionState.SUCCESS, minute=1,
            environment_name=None,
        )

        row = (await repo.list_job_health([_P], ListRuntimeJobsInput()))[0]

        assert row.environment_name is None


class TestEngineInventoryGate:
    async def test_runs_are_kept_when_the_engine_inventory_is_empty(self, db_session):
        """An unmirrored engine table means "no filter", not "no engines"."""
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(repo, run_id="r0", state=RuntimeExecutionState.FAILED, minute=0)

        health = await repo.list_engine_health([_P], ListRuntimeEnginesInput())

        assert [row.engine_id for row in health] == ["engine-1"]
        assert health[0].engine_name == "engine one"
        assert health[0].engine_status is None

    async def test_runs_for_an_unknown_engine_are_dropped_once_inventory_exists(
        self, db_session
    ):
        repo = RuntimeStoreRepository(db_session)
        await _add_engine(repo, engine_id="engine-1")
        await _add_job_run(repo, run_id="r0", state=RuntimeExecutionState.FAILED, minute=0)
        await _add_job_run(
            repo,
            run_id="r1",
            state=RuntimeExecutionState.FAILED,
            minute=1,
            engine_id="engine-ghost",
            engine_name="ghost",
        )

        health = await repo.list_engine_health([_P], ListRuntimeEnginesInput())

        assert [row.engine_id for row in health] == ["engine-1"]

    async def test_runs_with_no_engine_are_never_grouped(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(
            repo, run_id="r0", state=RuntimeExecutionState.FAILED, minute=0,
            engine_id=None, engine_name=None,
        )

        assert await repo.list_engine_health([_P], ListRuntimeEnginesInput()) == []

    async def test_status_filter_narrows_both_the_labels_and_the_groups(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_engine(repo, engine_id="on", name="on", status=RuntimeEngineStatus.ONLINE)
        await _add_engine(repo, engine_id="off", name="off", status=RuntimeEngineStatus.OFFLINE)
        await _add_job_run(
            repo, run_id="r0", state=RuntimeExecutionState.FAILED, minute=0,
            engine_id="on", engine_name="on",
        )
        await _add_job_run(
            repo, run_id="r1", state=RuntimeExecutionState.FAILED, minute=1,
            engine_id="off", engine_name="off",
        )

        health = await repo.list_engine_health(
            [_P], ListRuntimeEnginesInput(status=RuntimeEngineStatus.OFFLINE)
        )

        assert [row.engine_id for row in health] == ["off"]


class TestScopeIsPreserved:
    async def test_rbac_scope_and_the_project_filter_both_narrow(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(repo, run_id="a0", state=RuntimeExecutionState.FAILED, minute=0)
        await _add_job_run(
            repo, run_id="b0", state=RuntimeExecutionState.FAILED, minute=0,
            project_id="proj-b",
        )

        assert len(await repo.list_job_health(None, ListRuntimeJobsInput())) == 2
        assert len(await repo.list_job_health([_P], ListRuntimeJobsInput())) == 1
        assert (
            len(await repo.list_job_health(None, ListRuntimeJobsInput(project_id="proj-b"))) == 1
        )
        # An out-of-scope project filter cannot widen the scope.
        assert (
            await repo.list_job_health([_P], ListRuntimeJobsInput(project_id="proj-b")) == []
        )
        assert await repo.list_job_health([], ListRuntimeJobsInput()) == []

    async def test_executable_kind_separates_jobs_from_other_deployables(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(repo, run_id="j0", state=RuntimeExecutionState.FAILED, minute=0)
        await _add_job_run(
            repo,
            run_id="r0",
            state=RuntimeExecutionState.FAILED,
            minute=1,
            job_id="route-1",
            job_name="route",
            executable_kind=RuntimeExecutableKind.ROUTE,
        )

        jobs = await repo.list_job_health([_P], ListRuntimeJobsInput())
        routes = await repo.list_job_health(
            [_P], ListRuntimeJobsInput(executable_kind=RuntimeExecutableKind.ROUTE)
        )

        assert [row.job_id for row in jobs] == ["job-1"]
        assert [row.job_id for row in routes] == ["route-1"]


class TestEstateOverview:
    async def _seed_sources(self, repo, now):
        for source_key, status, is_demo, synced in (
            ("s-ok", RuntimeSyncStatus.SUCCESS, False, now),
            ("s-failed", RuntimeSyncStatus.FAILED, False, now),
            ("s-partial", RuntimeSyncStatus.PARTIAL, False, now),
            ("s-demo", RuntimeSyncStatus.SUCCESS, True, now),
            ("s-stale", RuntimeSyncStatus.SUCCESS, False, now - datetime.timedelta(hours=2)),
        ):
            await repo.upsert_source(
                _P,
                RuntimeSource(
                    estate_key=_P,
                    source_key=source_key,
                    source_type="mock",
                    label=source_key,
                    status=status,
                    is_demo=is_demo,
                    last_synced_at=synced,
                    last_seen_at=synced,
                    ingested_at=now,
                ),
                now,
            )

    async def test_inventory_tiles_are_counted_in_sql(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        now = datetime.datetime.now(UTC)
        await self._seed_sources(repo, now)
        for engine_id, status in (
            ("e-on", RuntimeEngineStatus.ONLINE),
            ("e-deg", RuntimeEngineStatus.DEGRADED),
            ("e-off", RuntimeEngineStatus.OFFLINE),
            ("e-unk", RuntimeEngineStatus.UNKNOWN),
            ("e-stale", RuntimeEngineStatus.STALE),
        ):
            await _add_engine(repo, engine_id=engine_id, name=engine_id, status=status)

        overview = await repo.get_estate_overview([_P], ListRuntimeInventoryInput())

        assert overview.sources_total == 5
        assert overview.sources_failed == 1
        assert overview.sources_partial == 1
        assert overview.sources_demo == 1
        # FAILED + PARTIAL + the one whose watermark is older than the freshness cutoff.
        assert overview.sources_stale == 3
        assert overview.last_synced_at == now
        assert overview.engines_total == 5
        assert overview.engines_online == 1
        assert overview.engines_degraded == 1
        assert overview.engines_offline == 1
        # UNKNOWN and STALE share one tile: neither reported a usable state.
        assert overview.engines_unknown == 2

    async def test_unhealthy_tiles_are_capped_by_the_page_size(self, db_session):
        """The tile has always shown the truncated figure; the SQL count must too."""
        repo = RuntimeStoreRepository(db_session)
        for index in range(5):
            await _add_job_run(
                repo,
                run_id=f"r{index}",
                job_id=f"job-{index}",
                job_name=f"job-{index}",
                state=RuntimeExecutionState.FAILED,
                minute=index,
            )

        capped = await repo.get_estate_overview([_P], ListRuntimeInventoryInput(limit=2))
        full = await repo.get_estate_overview([_P], ListRuntimeInventoryInput(limit=100))

        assert capped.jobs_unhealthy == 2
        assert full.jobs_unhealthy == 5
        assert len(await repo.list_job_health(
            [_P], ListRuntimeJobsInput(limit=2), attention_only=True
        )) == capped.jobs_unhealthy

    async def test_unhealthy_tiles_agree_with_the_lists_they_summarise(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_engine(repo)
        await _add_job_run(repo, run_id="r0", state=RuntimeExecutionState.TIMED_OUT, minute=0)
        await _add_plan_run(repo, run_id="p0", state=RuntimeExecutionState.MISFIRED, minute=0)

        overview = await repo.get_estate_overview([_P], ListRuntimeInventoryInput())

        assert overview.engines_unhealthy == len(
            await repo.list_engine_health(
                [_P], ListRuntimeEnginesInput(), attention_only=True
            )
        )
        assert overview.jobs_unhealthy == len(
            await repo.list_job_health([_P], ListRuntimeJobsInput(), attention_only=True)
        )
        assert overview.plans_unhealthy == len(
            await repo.list_plan_health([_P], ListRuntimePlansInput(), attention_only=True)
        )
        assert (overview.engines_unhealthy, overview.jobs_unhealthy, overview.plans_unhealthy) == (
            1,
            1,
            1,
        )

    async def test_denied_scope_returns_zeroes_without_touching_the_tables(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_job_run(repo, run_id="r0", state=RuntimeExecutionState.FAILED, minute=0)

        overview = await repo.get_estate_overview([], ListRuntimeInventoryInput())

        assert overview.sources_total == 0
        assert overview.jobs_unhealthy == 0
        assert overview.last_synced_at is None

    async def test_source_key_filter_narrows_every_tile(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        now = datetime.datetime.now(UTC)
        await self._seed_sources(repo, now)

        overview = await repo.get_estate_overview(
            [_P], ListRuntimeInventoryInput(source_key="s-failed")
        )

        assert overview.sources_total == 1
        assert overview.sources_failed == 1
        assert overview.sources_demo == 0


class TestHealthSummaries:
    async def test_counts_streak_and_percentiles_per_job_name(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        for index, (state, duration) in enumerate(
            [
                (RuntimeExecutionState.SUCCESS, 100),
                (RuntimeExecutionState.FAILED, 200),
                (RuntimeExecutionState.SUCCESS, 300),
                (RuntimeExecutionState.FAILED, 400),
            ]
        ):
            await _add_execution(
                repo,
                execution_id=f"e{index}",
                job_name="orders",
                state=state,
                minute=index,
                duration_ms=duration,
            )

        row = (await repo.list_health_summaries([_P]))[0]

        assert row.job_name == "orders"
        assert row.total_executions == 4
        assert row.failed_executions == 2
        assert row.failure_rate == 0.5
        assert row.current_failure_streak == 1
        assert row.last_execution_at == _at(3)
        assert row.last_failure_at == _at(3)
        assert row.p50_duration_ms == _percentile([100, 200, 300, 400], 50)
        assert row.p95_duration_ms == _percentile([100, 200, 300, 400], 95)

    async def test_last_failure_is_the_newest_failure_not_the_newest_run(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_execution(
            repo, execution_id="e0", job_name="orders",
            state=RuntimeExecutionState.FAILED, minute=0,
        )
        await _add_execution(
            repo, execution_id="e1", job_name="orders",
            state=RuntimeExecutionState.SUCCESS, minute=5,
        )

        row = (await repo.list_health_summaries([_P]))[0]

        assert row.last_execution_at == _at(5)
        assert row.last_failure_at == _at(0)
        assert row.current_failure_streak == 0

    async def test_row_totals_stay_none_when_no_metric_reported_one(self, db_session):
        """`None` and `0` are different answers, and `sum()` over NULLs preserves that."""
        repo = RuntimeStoreRepository(db_session)
        await _add_execution(
            repo, execution_id="e0", job_name="orders",
            state=RuntimeExecutionState.SUCCESS, minute=0,
        )
        await repo.upsert_component_metric(
            _P,
            RuntimeComponentMetric(
                execution_id="e0",
                component_key="tMap_1",
                component_type="tMap",
                rows_total=None,
                rows_rejected=None,
                duration_ms=5,
            ),
            _SRC,
            _T0,
        )

        row = (await repo.list_health_summaries([_P]))[0]

        assert row.rows_total is None
        assert row.rows_rejected is None
        assert row.reject_ratio is None

    async def test_row_totals_sum_across_the_jobs_components_and_executions(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        for index in range(2):
            await _add_execution(
                repo, execution_id=f"e{index}", job_name="orders",
                state=RuntimeExecutionState.SUCCESS, minute=index,
            )
            for component, total, rejected in (("tMap_1", 100, 5), ("tMap_2", 50, 0)):
                await repo.upsert_component_metric(
                    _P,
                    RuntimeComponentMetric(
                        execution_id=f"e{index}",
                        component_key=component,
                        component_type="tMap",
                        rows_total=total,
                        rows_rejected=rejected,
                        duration_ms=5,
                    ),
                    _SRC,
                    _T0,
                )

        row = (await repo.list_health_summaries([_P]))[0]

        assert row.rows_total == 300
        assert row.rows_rejected == 10
        assert row.reject_ratio == pytest.approx(10 / 300)

    async def test_a_metric_whose_execution_is_out_of_scope_is_ignored(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_execution(
            repo, execution_id="e0", job_name="orders",
            state=RuntimeExecutionState.SUCCESS, minute=0,
        )
        await repo.upsert_component_metric(
            _P,
            RuntimeComponentMetric(
                execution_id="e0", component_key="tMap_1", rows_total=7, rows_rejected=0,
            ),
            _SRC,
            _T0,
        )
        await _add_execution(
            repo, execution_id="b0", job_name="orders",
            state=RuntimeExecutionState.SUCCESS, minute=0, project_id="proj-b",
        )
        await repo.upsert_component_metric(
            "proj-b",
            RuntimeComponentMetric(
                execution_id="b0", component_key="tMap_1", rows_total=999, rows_rejected=0,
            ),
            _SRC,
            _T0,
        )

        row = (await repo.list_health_summaries([_P]))[0]

        assert row.rows_total == 7

    async def test_recurrence_comes_from_the_newest_incidents_signature(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        for index in range(3):
            await _add_execution(
                repo, execution_id=f"e{index}", job_name="orders",
                state=RuntimeExecutionState.FAILED, minute=index,
            )
        # Two incidents share the newest signature; one carries an older, different one.
        for execution_id, signature, minute in (
            ("e0", "sig-old", 0),
            ("e1", "sig-new", 1),
            ("e2", "sig-new", 2),
        ):
            db_session.add(
                RuntimeIncidentRow(
                    project_id=_P,
                    incident_id=f"inc-{execution_id}",
                    execution_id=execution_id,
                    signature_hash=signature,
                    first_bad_at=_at(minute),
                )
            )
        await db_session.flush()

        row = (await repo.list_health_summaries([_P]))[0]

        assert row.signature_hash == "sig-new"
        assert row.recurrence_count == 2

    async def test_only_job_executions_are_rolled_up(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_execution(
            repo, execution_id="e0", job_name="orders",
            state=RuntimeExecutionState.SUCCESS, minute=0,
        )
        await repo.upsert_execution(
            _P,
            RuntimeExecution(
                execution_id="r0",
                job_name="route",
                state=RuntimeExecutionState.SUCCESS,
                executable_kind=RuntimeExecutableKind.ROUTE,
                started_at=_at(1),
                ended_at=_at(1),
                duration_ms=1,
            ),
            _SRC,
            _T0,
        )

        rows = await repo.list_health_summaries([_P])

        assert [row.job_name for row in rows] == ["orders"]

    async def test_tied_groups_come_back_in_a_stable_defined_order(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        for index in range(15):
            await _add_execution(
                repo,
                execution_id=f"e{index}",
                job_name=f"job-{index:02d}",
                state=RuntimeExecutionState.SUCCESS,
                minute=0,
            )

        first = await repo.list_health_summaries([_P], limit=100)
        second = await repo.list_health_summaries([_P], limit=100)

        assert [row.job_name for row in first] == [f"job-{i:02d}" for i in range(15)]
        assert [row.job_name for row in first] == [row.job_name for row in second]

    async def test_scope_is_enforced(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _add_execution(
            repo, execution_id="e0", job_name="orders",
            state=RuntimeExecutionState.SUCCESS, minute=0,
        )
        await _add_execution(
            repo, execution_id="b0", job_name="other",
            state=RuntimeExecutionState.SUCCESS, minute=0, project_id="proj-b",
        )

        assert len(await repo.list_health_summaries(None)) == 2
        assert [r.job_name for r in await repo.list_health_summaries([_P])] == ["orders"]
        assert await repo.list_health_summaries([]) == []


class TestNoWholeRowReads:
    """Guards the two shapes that made these endpoints slow, at the SQL level."""

    def test_the_plan_run_rollup_never_selects_step_results(self, db_session):
        """`step_results` is 23 kB of TOASTed JSON a row and no number here uses it."""
        repo = RuntimeStoreRepository(db_session)
        base = repo._plan_run_base(None, ListRuntimePlansInput())
        statement = repo._run_group_health_statement(
            base,
            [base.c.project_id, base.c.source_key, base.c.plan_id],
            latest_columns=("plan_name", "environment_name", "workspace_name", "ended_at"),
            attention_only=False,
            limit=100,
        )

        assert "step_results" not in str(statement.compile()).lower()

    def test_the_plan_inventory_lookup_never_selects_steps(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        statement = repo._plan_label_select(None, ListRuntimePlansInput())

        rendered = str(statement.compile()).lower()
        assert "runtime_plans.steps" not in rendered

    async def test_the_overview_answers_entirely_with_aggregates(self, db_session, statements):
        """It returns nine integers and a timestamp, so no statement may stream rows."""
        repo = RuntimeStoreRepository(db_session)
        await _add_engine(repo)
        for index in range(30):
            await _add_job_run(
                repo,
                run_id=f"r{index}",
                job_id=f"job-{index}",
                job_name=f"job-{index}",
                state=RuntimeExecutionState.FAILED,
                minute=index,
            )
            await _add_plan_run(
                repo, run_id=f"p{index}", state=RuntimeExecutionState.FAILED, minute=index
            )

        with statements() as recorded:
            await repo.get_estate_overview([_P], ListRuntimeInventoryInput())

        assert recorded.mentioning("step_results") == []
        for statement in recorded.statements:
            assert "count(" in statement.lower() or "max(" in statement.lower(), statement
        # Four round trips: the inventory tiles as one row of scalar subqueries,
        # the engine-inventory emptiness probe, then one grouped count per
        # unhealthy tile.
        assert recorded.count <= 6

    async def test_health_surfaces_aggregate_in_sql_not_in_python(self, db_session):
        """Aggregation happens in the database, so a big run table yields a small result."""
        repo = RuntimeStoreRepository(db_session)
        for index in range(40):
            await _add_job_run(
                repo,
                run_id=f"r{index}",
                job_id=f"job-{index % 4}",
                job_name=f"job-{index % 4}",
                state=RuntimeExecutionState.FAILED,
                minute=index,
            )

        rows = (await db_session.execute(select(RuntimeJobRunRow.id))).scalars().all()
        health = await repo.list_job_health([_P], ListRuntimeJobsInput(limit=100))

        assert len(rows) == 40
        assert len(health) == 4
        assert sum(row.total_runs for row in health) == 40

    async def test_the_plan_health_surface_never_issues_a_step_results_read(
        self, db_session, statements
    ):
        repo = RuntimeStoreRepository(db_session)
        await _add_plan_run(repo, run_id="p0", state=RuntimeExecutionState.FAILED, minute=0)

        with statements() as recorded:
            await repo.list_plan_health([_P], ListRuntimePlansInput())

        assert recorded.mentioning("step_results") == []
