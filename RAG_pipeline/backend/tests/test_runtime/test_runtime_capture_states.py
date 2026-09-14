"""Storage and rollup behaviour for the widened runtime execution states.

TMC reports six execution statuses; the mirror understood two. Misfires,
timeouts, terminations and running Routes were all flattened to `unknown`, and
the `misfiredExecutionReason` / `executionType` fields that explain them were
discarded outright.

Two invariants are load-bearing here and are asserted separately in almost every
test below:

* `failure_rate` and `failed_runs` count FAILED and nothing else, so historical
  numbers stay comparable across the change.
* "needs a human to look at it" counts FAILED, MISFIRED and TIMED_OUT, so the
  work that silently did not happen stops being invisible.

Values used are the ones observed on the live API: `PARALLEL_EXECUTION_FORBIDDEN`
as a misfire reason, `SCHEDULED` as an execution type.
"""

import datetime
from datetime import UTC

import pytest

from src.runtime.models import (
    ListExecutionsInput,
    ListRuntimeEnginesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobReliabilityInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobsInput,
    ListRuntimePlanRunsInput,
    ListRuntimePlansInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeJobRun,
    RuntimePlanRun,
    RuntimeTrendBucket,
    RuntimeTrendInput,
)
from src.runtime.store import RuntimeStoreRepository

_PROJECT = "proj-capture"
_SRC = "src-capture"
_T0 = datetime.datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _execution(
    execution_id: str,
    *,
    job_name: str = "nightly_load",
    state: RuntimeExecutionState,
    raw_state: str | None = None,
    misfire_reason: str | None = None,
    execution_type: str | None = None,
    started_at: datetime.datetime = _T0,
    ended: bool = True,
) -> RuntimeExecution:
    return RuntimeExecution(
        execution_id=execution_id,
        job_name=job_name,
        state=state,
        raw_state=raw_state,
        misfire_reason=misfire_reason,
        execution_type=execution_type,
        started_at=started_at,
        ended_at=started_at + datetime.timedelta(seconds=30) if ended else None,
        duration_ms=30_000 if ended else None,
    )


def _job_run(
    run_id: str,
    *,
    job_id: str = "job-1",
    job_name: str = "nightly_load",
    state: RuntimeExecutionState,
    raw_state: str | None = None,
    misfire_reason: str | None = None,
    execution_type: str | None = None,
    engine_id: str = "engine-1",
    started_at: datetime.datetime = _T0,
    ended: bool = True,
) -> RuntimeJobRun:
    return RuntimeJobRun(
        source_key=_SRC,
        run_id=run_id,
        job_id=job_id,
        job_name=job_name,
        execution_id=run_id,
        state=state,
        raw_state=raw_state,
        misfire_reason=misfire_reason,
        execution_type=execution_type,
        engine_id=engine_id,
        engine_name="remote-engine-1",
        started_at=started_at,
        ended_at=started_at + datetime.timedelta(seconds=30) if ended else None,
        duration_ms=30_000 if ended else None,
    )


def _plan_run(
    run_id: str,
    *,
    state: RuntimeExecutionState,
    raw_state: str | None = None,
    misfire_reason: str | None = None,
    execution_type: str | None = None,
) -> RuntimePlanRun:
    return RuntimePlanRun(
        source_key=_SRC,
        run_id=run_id,
        plan_id="plan-1",
        plan_name="nightly_flow",
        state=state,
        raw_state=raw_state,
        misfire_reason=misfire_reason,
        execution_type=execution_type,
        started_at=_T0,
        ended_at=_T0 + datetime.timedelta(minutes=5),
        duration_ms=300_000,
    )


@pytest.fixture
async def repo(db_session) -> RuntimeStoreRepository:
    return RuntimeStoreRepository(db_session)


class TestEvidencePersistence:
    """The three captured fields must survive a write/read round trip."""

    async def test_execution_round_trips_raw_state_reason_and_type(self, repo):
        await repo.upsert_execution(
            _PROJECT,
            _execution(
                "exec-misfire",
                state=RuntimeExecutionState.MISFIRED,
                raw_state="EXECUTION_MISFIRED",
                misfire_reason="PARALLEL_EXECUTION_FORBIDDEN",
                execution_type="SCHEDULED",
                ended=False,
            ),
            _SRC,
            _T0,
        )

        [read_back] = await repo.list_executions([_PROJECT], ListExecutionsInput())
        assert read_back.state is RuntimeExecutionState.MISFIRED
        assert read_back.raw_state == "EXECUTION_MISFIRED"
        assert read_back.misfire_reason == "PARALLEL_EXECUTION_FORBIDDEN"
        assert read_back.execution_type == "SCHEDULED"

    async def test_job_run_round_trips_raw_state_reason_and_type(self, repo):
        await repo.upsert_job_run(
            _PROJECT,
            _job_run(
                "run-misfire",
                state=RuntimeExecutionState.MISFIRED,
                raw_state="EXECUTION_MISFIRED",
                misfire_reason="MAX_DEPLOYMENT_ATTEMPTS_REACHED",
                execution_type="PLAN",
                ended=False,
            ),
            _SRC,
            _T0,
        )

        [read_back] = await repo.list_job_runs([_PROJECT], ListRuntimeJobRunsInput())
        assert read_back.state is RuntimeExecutionState.MISFIRED
        assert read_back.raw_state == "EXECUTION_MISFIRED"
        assert read_back.misfire_reason == "MAX_DEPLOYMENT_ATTEMPTS_REACHED"
        assert read_back.execution_type == "PLAN"

    async def test_plan_run_round_trips_raw_state_reason_and_type(self, repo):
        await repo.upsert_plan_run(
            _PROJECT,
            _plan_run(
                "plan-run-timeout",
                state=RuntimeExecutionState.TIMED_OUT,
                raw_state="EXECUTION_TIMEOUT",
                execution_type="SCHEDULED",
            ),
            _SRC,
            _T0,
        )

        [read_back] = await repo.list_plan_runs([_PROJECT], ListRuntimePlanRunsInput())
        assert read_back.state is RuntimeExecutionState.TIMED_OUT
        assert read_back.raw_state == "EXECUTION_TIMEOUT"
        assert read_back.misfire_reason is None
        assert read_back.execution_type == "SCHEDULED"

    async def test_absent_evidence_stays_null_rather_than_defaulting(self, repo):
        """A provider that supplies nothing must not have a value invented for it."""
        await repo.upsert_job_run(
            _PROJECT,
            _job_run("run-plain", state=RuntimeExecutionState.SUCCESS),
            _SRC,
            _T0,
        )

        [read_back] = await repo.list_job_runs([_PROJECT], ListRuntimeJobRunsInput())
        assert read_back.raw_state is None
        assert read_back.misfire_reason is None
        assert read_back.execution_type is None

    async def test_restating_a_run_replaces_stale_evidence(self, repo):
        """A re-sync that resolves a misfire must not leave the reason behind.

        The upsert updates in place, so an evidence field left out of the write
        path would keep its first value forever and describe a run that no
        longer exists.
        """
        await repo.upsert_job_run(
            _PROJECT,
            _job_run(
                "run-1",
                state=RuntimeExecutionState.MISFIRED,
                raw_state="EXECUTION_MISFIRED",
                misfire_reason="TASK_IS_PAUSED",
                execution_type="SCHEDULED",
                ended=False,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_job_run(
            _PROJECT,
            _job_run(
                "run-1",
                state=RuntimeExecutionState.SUCCESS,
                raw_state="EXECUTION_SUCCESSFUL",
                misfire_reason=None,
                execution_type="SCHEDULED",
            ),
            _SRC,
            _T0,
        )

        runs = await repo.list_job_runs([_PROJECT], ListRuntimeJobRunsInput())
        assert len(runs) == 1
        assert runs[0].state is RuntimeExecutionState.SUCCESS
        assert runs[0].raw_state == "EXECUTION_SUCCESSFUL"
        assert runs[0].misfire_reason is None

    async def test_state_filter_selects_a_widened_state(self, repo):
        """`ix_runtime_job_runs_state` and the state predicate cover new values."""
        await repo.upsert_job_run(
            _PROJECT, _job_run("run-ok", state=RuntimeExecutionState.SUCCESS), _SRC, _T0
        )
        await repo.upsert_job_run(
            _PROJECT,
            _job_run("run-killed", state=RuntimeExecutionState.TERMINATED),
            _SRC,
            _T0,
        )

        runs = await repo.list_job_runs(
            [_PROJECT],
            ListRuntimeJobRunsInput(state=RuntimeExecutionState.TERMINATED),
        )
        assert [run.run_id for run in runs] == ["run-killed"]


class TestRollupCounters:
    """The three new counters, and the failure numbers that must not move."""

    @pytest.fixture
    async def seeded(self, repo) -> RuntimeStoreRepository:
        # One of each outcome inside a single day bucket.
        states = [
            ("e-success", RuntimeExecutionState.SUCCESS),
            ("e-failed", RuntimeExecutionState.FAILED),
            ("e-misfired", RuntimeExecutionState.MISFIRED),
            ("e-timeout", RuntimeExecutionState.TIMED_OUT),
            ("e-terminated", RuntimeExecutionState.TERMINATED),
        ]
        for index, (execution_id, state) in enumerate(states):
            await repo.upsert_execution(
                _PROJECT,
                _execution(
                    execution_id,
                    state=state,
                    started_at=_T0 + datetime.timedelta(minutes=index),
                    execution_type="SCHEDULED",
                    ended=state is not RuntimeExecutionState.MISFIRED,
                ),
                _SRC,
                _T0,
            )
        return repo

    async def test_outcome_trend_counts_each_new_state(self, seeded):
        [point] = await seeded.get_outcome_trend(
            [_PROJECT], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY)
        )
        assert point.total_runs == 5
        assert point.success_runs == 1
        assert point.failed_runs == 1
        assert point.misfired_runs == 1
        assert point.timed_out_runs == 1
        assert point.terminated_runs == 1
        assert point.unknown_runs == 0

    async def test_outcome_trend_failure_rate_still_counts_only_failed(self, seeded):
        [point] = await seeded.get_outcome_trend(
            [_PROJECT], RuntimeTrendInput(bucket=RuntimeTrendBucket.DAY)
        )
        # 1 failed of 5 runs. Folding in the misfire, timeout and termination
        # would report 0.8 and silently restate every historical figure.
        assert point.failure_rate == pytest.approx(0.2)

    async def test_job_reliability_counts_each_new_state(self, seeded):
        [row] = await seeded.list_job_reliability(
            [_PROJECT], ListRuntimeJobReliabilityInput()
        )
        assert row.total_runs == 5
        assert row.failed_runs == 1
        assert row.misfired_runs == 1
        assert row.timed_out_runs == 1
        assert row.terminated_runs == 1
        assert row.failure_rate == pytest.approx(0.2)

    async def test_misfired_run_is_not_counted_as_unfinished(self, seeded):
        """A misfire never started, so it has no end time and is not hung."""
        [row] = await seeded.list_job_reliability(
            [_PROJECT], ListRuntimeJobReliabilityInput()
        )
        assert row.unfinished_runs == 0

    async def test_latest_misfire_does_not_read_as_a_stuck_run(self, repo):
        await repo.upsert_execution(
            _PROJECT,
            _execution("e-1", state=RuntimeExecutionState.SUCCESS),
            _SRC,
            _T0,
        )
        await repo.upsert_execution(
            _PROJECT,
            _execution(
                "e-2",
                state=RuntimeExecutionState.MISFIRED,
                misfire_reason="PARALLEL_EXECUTION_FORBIDDEN",
                started_at=_T0 + datetime.timedelta(hours=1),
                ended=False,
            ),
            _SRC,
            _T0,
        )

        [row] = await repo.list_job_reliability([_PROJECT], ListRuntimeJobReliabilityInput())
        assert row.latest_state is RuntimeExecutionState.MISFIRED
        assert row.latest_unfinished is False


class TestAttentionOnly:
    """`attention_only` is the "does a human need to look at this" surface."""

    async def _seed_latest(self, repo, job_name: str, state: RuntimeExecutionState) -> None:
        await repo.upsert_execution(
            _PROJECT,
            _execution(f"{job_name}-old", job_name=job_name, state=RuntimeExecutionState.SUCCESS),
            _SRC,
            _T0,
        )
        await repo.upsert_execution(
            _PROJECT,
            _execution(
                f"{job_name}-latest",
                job_name=job_name,
                state=state,
                started_at=_T0 + datetime.timedelta(hours=1),
                ended=state is not RuntimeExecutionState.MISFIRED,
            ),
            _SRC,
            _T0,
        )

    async def test_includes_misfired_and_timed_out_alongside_failed(self, repo):
        await self._seed_latest(repo, "job_failed", RuntimeExecutionState.FAILED)
        await self._seed_latest(repo, "job_misfired", RuntimeExecutionState.MISFIRED)
        await self._seed_latest(repo, "job_timeout", RuntimeExecutionState.TIMED_OUT)
        await self._seed_latest(repo, "job_ok", RuntimeExecutionState.SUCCESS)

        rows = await repo.list_job_reliability(
            [_PROJECT], ListRuntimeJobReliabilityInput(attention_only=True)
        )

        assert {row.job_name for row in rows} == {"job_failed", "job_misfired", "job_timeout"}

    async def test_excludes_terminated_because_an_operator_already_acted(self, repo):
        await self._seed_latest(repo, "job_killed", RuntimeExecutionState.TERMINATED)

        rows = await repo.list_job_reliability(
            [_PROJECT], ListRuntimeJobReliabilityInput(attention_only=True)
        )

        assert rows == []

    async def test_count_matches_the_listed_rows(self, repo):
        await self._seed_latest(repo, "job_failed", RuntimeExecutionState.FAILED)
        await self._seed_latest(repo, "job_misfired", RuntimeExecutionState.MISFIRED)
        await self._seed_latest(repo, "job_ok", RuntimeExecutionState.SUCCESS)

        params = ListRuntimeJobReliabilityInput(attention_only=True)
        rows = await repo.list_job_reliability([_PROJECT], params)
        assert await repo.count_job_reliability([_PROJECT], params) == len(rows) == 2


class TestUnhealthyEntitySurfaces:
    """Unhealthy engines/jobs/plans must see the states that produce no logs."""

    @pytest.fixture
    async def seeded(self, repo) -> RuntimeStoreRepository:
        await repo.upsert_job_run(
            _PROJECT,
            _job_run(
                "run-misfire",
                job_id="job-misfire",
                job_name="inventory_sync",
                state=RuntimeExecutionState.MISFIRED,
                misfire_reason="PARALLEL_EXECUTION_FORBIDDEN",
                execution_type="SCHEDULED",
                engine_id="engine-misfire",
                ended=False,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_job_run(
            _PROJECT,
            _job_run(
                "run-ok",
                job_id="job-ok",
                job_name="healthy_load",
                state=RuntimeExecutionState.SUCCESS,
                engine_id="engine-ok",
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_plan_run(
            _PROJECT,
            _plan_run("plan-run-timeout", state=RuntimeExecutionState.TIMED_OUT),
            _SRC,
            _T0,
        )
        return repo

    async def test_job_health_attention_only_keeps_the_misfired_job(self, seeded):
        rows = await seeded.list_job_health(
            [_PROJECT], ListRuntimeJobsInput(), attention_only=True
        )
        assert [row.job_name for row in rows] == ["inventory_sync"]
        # ...and still reports it with a zero failure rate.
        assert rows[0].failed_runs == 0
        assert rows[0].failure_rate == 0.0

    async def test_engine_health_attention_only_keeps_the_hosting_engine(self, seeded):
        rows = await seeded.list_engine_health(
            [_PROJECT], ListRuntimeEnginesInput(), attention_only=True
        )
        assert [row.engine_id for row in rows] == ["engine-misfire"]

    async def test_plan_health_attention_only_keeps_the_timed_out_plan(self, seeded):
        rows = await seeded.list_plan_health(
            [_PROJECT], ListRuntimePlansInput(), attention_only=True
        )
        assert [row.plan_id for row in rows] == ["plan-1"]

    async def test_estate_overview_counts_them_as_unhealthy(self, seeded):
        overview = await seeded.get_estate_overview(
            [_PROJECT], ListRuntimeInventoryInput(project_id=_PROJECT)
        )
        assert overview.jobs_unhealthy == 1
        assert overview.engines_unhealthy == 1
        assert overview.plans_unhealthy == 1

    async def test_unfiltered_health_lists_are_unchanged(self, seeded):
        """The default path still returns every entity, healthy or not."""
        rows = await seeded.list_job_health([_PROJECT], ListRuntimeJobsInput())
        assert {row.job_name for row in rows} == {"inventory_sync", "healthy_load"}
