"""Tests for the held runtime-data repository (src/runtime/store/repository.py).

Covers project/RBAC scope enforcement, idempotent write-port upserts, read filters,
and the freshness watermark. Runs against the in-memory SQLite db_session fixture.
"""

import datetime
from datetime import UTC

import pytest
from sqlalchemy import func, select

from src.db.models.runtime_data import (
    RuntimeEngineRow,
    RuntimeExecutionRow,
    RuntimeJobRow,
    RuntimeJobRunRow,
    RuntimeLogEventRow,
    RuntimePlanRow,
    RuntimePlanRunRow,
    RuntimeSourceRow,
)
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    ListRuntimeEnginesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobsInput,
    ListRuntimePlanRunsInput,
    ListRuntimePlansInput,
    RuntimeExecution,
    RuntimeEngine,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJob,
    RuntimeJobRun,
    RuntimePlan,
    RuntimePlanRun,
    RuntimePlanRunStepResult,
    RuntimePlanStep,
    RuntimeComponentMetric,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimeSource,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.store import RuntimeRecordNotFoundError, RuntimeStoreRepository

_T0 = datetime.datetime(2026, 1, 1, tzinfo=UTC)
_SRC = "mock"


def _exec(
    execution_id="e1",
    job="nightly_sync",
    project_name="demo",
    state=RuntimeExecutionState.SUCCESS,
    started=_T0,
):
    return RuntimeExecution(
        execution_id=execution_id,
        job_name=job,
        project_name=project_name,
        state=state,
        started_at=started,
        ended_at=started,
        duration_ms=1000,
    )


def _failure(execution_id="e2", job="nightly_sync", failed_at=_T0, summary="db timeout"):
    return RuntimeFailure(
        execution_id=execution_id, job_name=job, failed_at=failed_at, error_summary=summary
    )


def _log(execution_id="e1", ts=_T0, severity=RuntimeLogSeverity.INFO, message="started"):
    return RuntimeLogEvent(execution_id=execution_id, timestamp=ts, severity=severity, message=message)


def _job(job_id="job-orders", name="orders", engine_id="engine-1", engine_name="remote-engine-1"):
    return RuntimeJob(
        source_key=_SRC,
        job_id=job_id,
        name=name,
        display_name=name.replace("_", " ").title(),
        job_type="task",
        status="active",
        environment_id="env-prod",
        environment_name="Production",
        workspace_id="ws-main",
        workspace_name="Main",
        engine_id=engine_id,
        engine_name=engine_name,
        owner="ops",
        artifact_name=f"{name}-1.0.0",
        last_seen_at=_T0,
        source_updated_at=_T0,
    )


def _job_run(
    run_id="run-1",
    job_id="job-orders",
    job_name="orders",
    state=RuntimeExecutionState.SUCCESS,
    started=_T0,
    execution_id="e1",
):
    return RuntimeJobRun(
        source_key=_SRC,
        run_id=run_id,
        job_id=job_id,
        job_name=job_name,
        execution_id=execution_id,
        state=state,
        environment_id="env-prod",
        environment_name="Production",
        workspace_id="ws-main",
        workspace_name="Main",
        engine_id="engine-1",
        engine_name="remote-engine-1",
        started_at=started,
        ended_at=started + datetime.timedelta(seconds=1),
        duration_ms=1000,
    )


def _plan(plan_id="plan-nightly", name="nightly_flow"):
    return RuntimePlan(
        source_key=_SRC,
        plan_id=plan_id,
        name=name,
        display_name=name.replace("_", " ").title(),
        status="active",
        environment_id="env-prod",
        environment_name="Production",
        workspace_id="ws-main",
        workspace_name="Main",
        owner="ops",
        step_count=1,
        steps=[
            RuntimePlanStep(
                step_id="step-orders",
                name="Run orders",
                job_id="job-orders",
                job_name="orders",
                order=1,
            )
        ],
        last_seen_at=_T0,
        source_updated_at=_T0,
    )


def _plan_run(
    run_id="plan-run-1",
    plan_id="plan-nightly",
    plan_name="nightly_flow",
    state=RuntimeExecutionState.SUCCESS,
    started=_T0,
):
    return RuntimePlanRun(
        source_key=_SRC,
        run_id=run_id,
        plan_id=plan_id,
        plan_name=plan_name,
        state=state,
        environment_id="env-prod",
        environment_name="Production",
        workspace_id="ws-main",
        workspace_name="Main",
        started_at=started,
        ended_at=started + datetime.timedelta(seconds=10),
        duration_ms=10_000,
        step_results=[
            RuntimePlanRunStepResult(
                step_id="step-orders",
                job_id="job-orders",
                job_name="orders",
                state=state,
                execution_id="e1",
            )
        ],
    )


async def _seed_execution(repo, project_id, execution, ingested=_T0):
    await repo.upsert_execution(project_id, execution, _SRC, ingested)


class TestRoundTrips:
    async def test_execution_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec())
        result = await repo.list_executions(None, ListExecutionsInput())
        assert len(result) == 1
        assert result[0].execution_id == "e1"
        assert result[0].state == RuntimeExecutionState.SUCCESS

    async def test_failure_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_failure("project-a", _failure(), _SRC, _T0)
        result = await repo.detect_failures(None, DetectFailuresInput())
        assert len(result) == 1
        assert result[0].error_summary == "db timeout"

    async def test_log_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event("project-a", _log(), _SRC, _T0)
        result = await repo.get_execution_logs(None, GetExecutionLogsInput(execution_id="e1"))
        assert len(result) == 1
        assert result[0].message == "started"

    async def test_freshness_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.update_sync_state(_SRC, "project-a", _T0)
        assert await repo.get_freshness(None, _SRC) == _T0
        assert await repo.get_freshness(None, _SRC, project_id="project-a") == _T0

    async def test_sync_status_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.record_sync_state(
            source_key=_SRC,
            project_id="project-a",
            status=RuntimeSyncStatus.PARTIAL,
            last_synced_at=None,
            failure_reason_code="page_timeout",
            error_summary="Page 2 timed out",
            updated_at=_T0,
        )

        states = await repo.list_sync_states(None, source_key=_SRC)
        assert len(states) == 1
        assert states[0].status == RuntimeSyncStatus.PARTIAL
        assert states[0].last_synced_at is None
        assert states[0].failure_reason_code == "page_timeout"
        assert states[0].error_summary == "Page 2 timed out"

    async def test_runtime_inventory_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_source(
            "project-a",
            RuntimeSource(
                project_id="project-a",
                source_key=_SRC,
                source_type="mock",
                label="Demo source",
                status=RuntimeSyncStatus.SUCCESS,
                is_demo=True,
                last_synced_at=_T0,
                last_seen_at=_T0,
                ingested_at=_T0,
            ),
            _T0,
        )
        await repo.upsert_environment(
            "project-a",
            RuntimeEnvironment(
                source_key=_SRC,
                environment_id="env-prod",
                name="Production",
                last_seen_at=_T0,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_workspace(
            "project-a",
            RuntimeWorkspace(
                source_key=_SRC,
                workspace_id="ws-main",
                name="Main",
                environment_id="env-prod",
                environment_name="Production",
                last_seen_at=_T0,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_engine(
            "project-a",
            RuntimeEngine(
                source_key=_SRC,
                engine_id="engine-1",
                name="remote-engine-1",
                status=RuntimeEngineStatus.ONLINE,
                version="8.0.1",
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-main",
                workspace_name="Main",
                last_seen_at=_T0,
            ),
            _SRC,
            _T0,
        )

        sources = await repo.list_sources(None)
        engines = await repo.list_engines(None, ListRuntimeEnginesInput())
        overview = await repo.get_estate_overview(None, ListRuntimeInventoryInput())

        assert sources[0].label == "Demo source"
        assert sources[0].is_demo is True
        assert engines[0].name == "remote-engine-1"
        assert engines[0].status == RuntimeEngineStatus.ONLINE
        assert engines[0].environment_name == "Production"
        assert overview.sources_total == 1
        assert overview.engines_total == 1
        assert overview.engines_online == 1
        assert overview.environments_total == 1
        assert overview.workspaces_total == 1

    async def test_runtime_job_inventory_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_job("project-a", _job(), _SRC, _T0)
        await repo.upsert_job_run("project-a", _job_run(), _SRC, _T0)

        jobs = await repo.list_jobs(None, ListRuntimeJobsInput())
        runs = await repo.list_job_runs(None, ListRuntimeJobRunsInput())
        health = await repo.list_job_health(None, ListRuntimeJobsInput())

        assert jobs[0].job_id == "job-orders"
        assert jobs[0].engine_name == "remote-engine-1"
        assert runs[0].run_id == "run-1"
        assert runs[0].execution_id == "e1"
        assert health[0].job_id == "job-orders"
        assert health[0].total_runs == 1
        assert health[0].failure_rate == 0.0

    async def test_runtime_plan_inventory_round_trip(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_plan("project-a", _plan(), _SRC, _T0)
        await repo.upsert_plan_run("project-a", _plan_run(), _SRC, _T0)

        plans = await repo.list_plans(None, ListRuntimePlansInput())
        runs = await repo.list_plan_runs(None, ListRuntimePlanRunsInput())
        health = await repo.list_plan_health(None, ListRuntimePlansInput())

        assert plans[0].plan_id == "plan-nightly"
        assert plans[0].steps[0].job_id == "job-orders"
        assert runs[0].run_id == "plan-run-1"
        assert runs[0].step_results[0].execution_id == "e1"
        assert health[0].plan_id == "plan-nightly"
        assert health[0].total_runs == 1
        assert health[0].failure_rate == 0.0


class TestScopeEnforcement:
    async def test_none_scope_reads_all_projects(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="a1"))
        await _seed_execution(repo, "project-b", _exec(execution_id="b1"))
        result = await repo.list_executions(None, ListExecutionsInput())
        assert {e.execution_id for e in result} == {"a1", "b1"}

    async def test_scoped_list_excludes_other_projects(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="a1"))
        await _seed_execution(repo, "project-b", _exec(execution_id="b1"))
        result = await repo.list_executions(["project-a"], ListExecutionsInput())
        assert {e.execution_id for e in result} == {"a1"}

    async def test_scoped_point_lookup_denies_other_project(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-b", _exec(execution_id="b1"))
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_details(["project-a"], GetExecutionDetailsInput(execution_id="b1"))

    async def test_scoped_logs_deny_other_project(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event("project-b", _log(execution_id="b1"), _SRC, _T0)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_logs(["project-a"], GetExecutionLogsInput(execution_id="b1"))

    async def test_empty_scope_returns_nothing(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec())
        await repo.upsert_failure("project-a", _failure(), _SRC, _T0)
        assert await repo.list_executions([], ListExecutionsInput()) == []
        assert await repo.detect_failures([], DetectFailuresInput()) == []
        assert await repo.get_freshness([], _SRC) is None
        assert await repo.list_sync_states([], source_key=_SRC) == []
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_details([], GetExecutionDetailsInput(execution_id="e1"))

    async def test_detect_failures_scoped(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_failure("project-a", _failure(execution_id="a2"), _SRC, _T0)
        await repo.upsert_failure("project-b", _failure(execution_id="b2"), _SRC, _T0)
        result = await repo.detect_failures(["project-b"], DetectFailuresInput())
        assert {f.execution_id for f in result} == {"b2"}

    async def test_sync_status_scoped(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.update_sync_state(_SRC, "project-a", _T0)
        await repo.update_sync_state(_SRC, "project-b", _T0 + datetime.timedelta(minutes=1))

        result = await repo.list_sync_states(["project-a"], source_key=_SRC)
        assert [state.project_id for state in result] == ["project-a"]

    async def test_inventory_scoped(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        for project_id, engine_id in (("project-a", "engine-a"), ("project-b", "engine-b")):
            await repo.upsert_source(
                project_id,
                RuntimeSource(
                    project_id=project_id,
                    source_key=_SRC,
                    source_type="mock",
                    label=f"Source {project_id}",
                    status=RuntimeSyncStatus.SUCCESS,
                    is_demo=True,
                    last_synced_at=_T0,
                    last_seen_at=_T0,
                    ingested_at=_T0,
                ),
                _T0,
            )
            await repo.upsert_engine(
                project_id,
                RuntimeEngine(
                    source_key=_SRC,
                    engine_id=engine_id,
                    name=engine_id,
                    status=RuntimeEngineStatus.ONLINE,
                    last_seen_at=_T0,
                ),
                _SRC,
                _T0,
            )

        sources = await repo.list_sources(["project-a"])
        engines = await repo.list_engines(["project-a"], ListRuntimeEnginesInput())
        denied = await repo.list_engines(["project-a"], ListRuntimeEnginesInput(project_id="project-b"))
        empty = await repo.get_estate_overview([], ListRuntimeInventoryInput())

        assert [source.project_id for source in sources] == ["project-a"]
        assert [engine.engine_id for engine in engines] == ["engine-a"]
        assert denied == []
        assert empty.engines_total == 0

    async def test_job_inventory_scoped(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_job("project-a", _job(job_id="job-a", name="a"), _SRC, _T0)
        await repo.upsert_job("project-b", _job(job_id="job-b", name="b"), _SRC, _T0)
        await repo.upsert_job_run("project-a", _job_run(run_id="run-a", job_id="job-a", job_name="a"), _SRC, _T0)
        await repo.upsert_job_run("project-b", _job_run(run_id="run-b", job_id="job-b", job_name="b"), _SRC, _T0)

        jobs = await repo.list_jobs(["project-a"], ListRuntimeJobsInput())
        runs = await repo.list_job_runs(["project-a"], ListRuntimeJobRunsInput())
        denied = await repo.list_jobs(["project-a"], ListRuntimeJobsInput(project_id="project-b"))
        health = await repo.list_job_health(["project-a"], ListRuntimeJobsInput())

        assert [job.job_id for job in jobs] == ["job-a"]
        assert [run.run_id for run in runs] == ["run-a"]
        assert denied == []
        assert [item.job_id for item in health] == ["job-a"]

    async def test_plan_inventory_scoped(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_plan("project-a", _plan(plan_id="plan-a", name="a"), _SRC, _T0)
        await repo.upsert_plan("project-b", _plan(plan_id="plan-b", name="b"), _SRC, _T0)
        await repo.upsert_plan_run("project-a", _plan_run(run_id="run-a", plan_id="plan-a", plan_name="a"), _SRC, _T0)
        await repo.upsert_plan_run("project-b", _plan_run(run_id="run-b", plan_id="plan-b", plan_name="b"), _SRC, _T0)

        plans = await repo.list_plans(["project-a"], ListRuntimePlansInput())
        runs = await repo.list_plan_runs(["project-a"], ListRuntimePlanRunsInput())
        denied = await repo.list_plans(["project-a"], ListRuntimePlansInput(project_id="project-b"))
        health = await repo.list_plan_health(["project-a"], ListRuntimePlansInput())

        assert [plan.plan_id for plan in plans] == ["plan-a"]
        assert [run.run_id for run in runs] == ["run-a"]
        assert denied == []
        assert [item.plan_id for item in health] == ["plan-a"]


class TestIdempotency:
    async def test_repeated_execution_upsert_does_not_duplicate(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(state=RuntimeExecutionState.RUNNING))
        await _seed_execution(repo, "project-a", _exec(state=RuntimeExecutionState.SUCCESS))
        count = (
            await db_session.execute(select(func.count()).select_from(RuntimeExecutionRow))
        ).scalar()
        assert count == 1
        result = await repo.list_executions(None, ListExecutionsInput())
        assert result[0].state == RuntimeExecutionState.SUCCESS  # updated, not duplicated

    async def test_repeated_log_upsert_is_idempotent(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event("project-a", _log(), _SRC, _T0)
        await repo.upsert_log_event("project-a", _log(), _SRC, _T0)
        count = (
            await db_session.execute(select(func.count()).select_from(RuntimeLogEventRow))
        ).scalar()
        assert count == 1

    async def test_repeated_sync_state_update_is_idempotent(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.update_sync_state(_SRC, "project-a", _T0)
        later = _T0 + datetime.timedelta(hours=1)
        await repo.update_sync_state(_SRC, "project-a", later)
        assert await repo.get_freshness(None, _SRC, project_id="project-a") == later

    async def test_repeated_inventory_upsert_is_idempotent(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        source = RuntimeSource(
            project_id="project-a",
            source_key=_SRC,
            source_type="mock",
            label="Before",
            status=RuntimeSyncStatus.SUCCESS,
            is_demo=True,
            last_synced_at=_T0,
            last_seen_at=_T0,
            ingested_at=_T0,
        )
        await repo.upsert_source("project-a", source, _T0)
        await repo.upsert_source("project-a", source.model_copy(update={"label": "After"}), _T0)
        await repo.upsert_engine(
            "project-a",
            RuntimeEngine(
                source_key=_SRC,
                engine_id="engine-1",
                name="Before",
                status=RuntimeEngineStatus.UNKNOWN,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_engine(
            "project-a",
            RuntimeEngine(
                source_key=_SRC,
                engine_id="engine-1",
                name="After",
                status=RuntimeEngineStatus.DEGRADED,
            ),
            _SRC,
            _T0,
        )

        source_count = (await db_session.execute(select(func.count()).select_from(RuntimeSourceRow))).scalar()
        engine_count = (await db_session.execute(select(func.count()).select_from(RuntimeEngineRow))).scalar()
        assert source_count == 1
        assert engine_count == 1
        assert (await repo.list_sources(None))[0].label == "After"
        assert (await repo.list_engines(None, ListRuntimeEnginesInput()))[0].status == RuntimeEngineStatus.DEGRADED

    async def test_repeated_job_upsert_is_idempotent(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_job("project-a", _job(name="Before"), _SRC, _T0)
        await repo.upsert_job("project-a", _job(name="After"), _SRC, _T0)
        await repo.upsert_job_run("project-a", _job_run(state=RuntimeExecutionState.RUNNING), _SRC, _T0)
        await repo.upsert_job_run("project-a", _job_run(state=RuntimeExecutionState.FAILED), _SRC, _T0)

        job_count = (await db_session.execute(select(func.count()).select_from(RuntimeJobRow))).scalar()
        run_count = (await db_session.execute(select(func.count()).select_from(RuntimeJobRunRow))).scalar()

        assert job_count == 1
        assert run_count == 1
        assert (await repo.list_jobs(None, ListRuntimeJobsInput()))[0].name == "After"
        assert (await repo.list_job_runs(None, ListRuntimeJobRunsInput()))[0].state == RuntimeExecutionState.FAILED

    async def test_repeated_plan_upsert_is_idempotent(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_plan("project-a", _plan(name="Before"), _SRC, _T0)
        await repo.upsert_plan("project-a", _plan(name="After"), _SRC, _T0)
        await repo.upsert_plan_run("project-a", _plan_run(state=RuntimeExecutionState.RUNNING), _SRC, _T0)
        await repo.upsert_plan_run("project-a", _plan_run(state=RuntimeExecutionState.FAILED), _SRC, _T0)

        plan_count = (await db_session.execute(select(func.count()).select_from(RuntimePlanRow))).scalar()
        run_count = (await db_session.execute(select(func.count()).select_from(RuntimePlanRunRow))).scalar()

        assert plan_count == 1
        assert run_count == 1
        assert (await repo.list_plans(None, ListRuntimePlansInput()))[0].name == "After"
        assert (await repo.list_plan_runs(None, ListRuntimePlanRunsInput()))[0].state == RuntimeExecutionState.FAILED


class TestReadFilters:
    async def test_job_name_filter(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="a1", job="nightly_sync"))
        await _seed_execution(repo, "project-a", _exec(execution_id="a2", job="hourly_sync"))
        result = await repo.list_executions(None, ListExecutionsInput(job_name="hourly_sync"))
        assert {e.execution_id for e in result} == {"a2"}

    async def test_project_name_filter(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="a1", project_name="demo"))
        await _seed_execution(repo, "project-a", _exec(execution_id="a2", project_name="other"))
        result = await repo.list_executions(None, ListExecutionsInput(project_name="other"))
        assert {e.execution_id for e in result} == {"a2"}

    async def test_since_until_filter(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        early = _T0
        late = _T0 + datetime.timedelta(days=2)
        await _seed_execution(repo, "project-a", _exec(execution_id="early", started=early))
        await _seed_execution(repo, "project-a", _exec(execution_id="late", started=late))
        result = await repo.list_executions(
            None, ListExecutionsInput(since=_T0 + datetime.timedelta(days=1))
        )
        assert {e.execution_id for e in result} == {"late"}

    async def test_limit(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        for i in range(3):
            await _seed_execution(
                repo,
                "project-a",
                _exec(execution_id=f"e{i}", started=_T0 + datetime.timedelta(hours=i)),
            )
        result = await repo.list_executions(None, ListExecutionsInput(limit=2))
        assert len(result) == 2
        # ordered by started_at desc -> most recent first
        assert result[0].execution_id == "e2"

    async def test_log_min_severity_filter(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event(
            "project-a", _log(severity=RuntimeLogSeverity.INFO, message="info line"), _SRC, _T0
        )
        await repo.upsert_log_event(
            "project-a", _log(severity=RuntimeLogSeverity.ERROR, message="error line"), _SRC, _T0
        )
        result = await repo.get_execution_logs(
            None, GetExecutionLogsInput(execution_id="e1", min_severity=RuntimeLogSeverity.WARNING)
        )
        assert [r.severity for r in result] == [RuntimeLogSeverity.ERROR]


class TestHealthRollups:
    async def test_health_rollup_counts_failures_and_metrics(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="ok", job="orders"))
        await _seed_execution(
            repo,
            "project-a",
            _exec(
                execution_id="bad",
                job="orders",
                state=RuntimeExecutionState.FAILED,
                started=_T0 + datetime.timedelta(hours=1),
            ),
        )
        await repo.upsert_component_metric(
            "project-a",
            RuntimeComponentMetric(
                execution_id="ok",
                component_key="tDBInput_1",
                rows_total=100,
                rows_rejected=5,
                duration_ms=300,
            ),
            _SRC,
            _T0,
        )

        result = await repo.list_health_summaries(["project-a"])

        assert len(result) == 1
        assert result[0].job_name == "orders"
        assert result[0].total_executions == 2
        assert result[0].failed_executions == 1
        assert result[0].failure_rate == 0.5
        assert result[0].current_failure_streak == 1
        assert result[0].rows_total == 100
        assert result[0].rows_rejected == 5
        assert result[0].reject_ratio == 0.05

    async def test_health_rollup_scope_excludes_other_projects(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="a1", job="a"))
        await _seed_execution(repo, "project-b", _exec(execution_id="b1", job="b"))

        result = await repo.list_health_summaries(["project-a"])

        assert [item.project_id for item in result] == ["project-a"]
        assert [item.job_name for item in result] == ["a"]

    async def test_job_health_counts_failures_and_current_streak(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_job("project-a", _job(job_id="job-orders", name="orders"), _SRC, _T0)
        await repo.upsert_job_run(
            "project-a",
            _job_run(run_id="ok", job_id="job-orders", job_name="orders", started=_T0),
            _SRC,
            _T0,
        )
        await repo.upsert_job_run(
            "project-a",
            _job_run(
                run_id="bad",
                job_id="job-orders",
                job_name="orders",
                state=RuntimeExecutionState.FAILED,
                started=_T0 + datetime.timedelta(hours=1),
            ),
            _SRC,
            _T0,
        )

        result = await repo.list_job_health(["project-a"], ListRuntimeJobsInput())

        assert len(result) == 1
        assert result[0].job_id == "job-orders"
        assert result[0].total_runs == 2
        assert result[0].failed_runs == 1
        assert result[0].failure_rate == 0.5
        assert result[0].current_failure_streak == 1
        assert result[0].latest_state == RuntimeExecutionState.FAILED

    async def test_engine_health_counts_failures_and_duration_percentiles(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_engine(
            "project-a",
            RuntimeEngine(
                source_key=_SRC,
                engine_id="engine-1",
                name="remote-engine-1",
                status=RuntimeEngineStatus.ONLINE,
                environment_id="env-prod",
                environment_name="Production",
                workspace_id="ws-main",
                workspace_name="Main",
                last_seen_at=_T0,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_job_run(
            "project-a",
            _job_run(run_id="ok", state=RuntimeExecutionState.SUCCESS, started=_T0),
            _SRC,
            _T0,
        )
        await repo.upsert_job_run(
            "project-a",
            _job_run(
                run_id="bad",
                state=RuntimeExecutionState.FAILED,
                started=_T0 + datetime.timedelta(hours=1),
            ),
            _SRC,
            _T0,
        )

        result = await repo.list_engine_health(["project-a"], ListRuntimeEnginesInput())

        assert len(result) == 1
        assert result[0].engine_id == "engine-1"
        assert result[0].engine_name == "remote-engine-1"
        assert result[0].engine_status == RuntimeEngineStatus.ONLINE
        assert result[0].total_runs == 2
        assert result[0].failed_runs == 1
        assert result[0].failure_rate == 0.5
        assert result[0].current_failure_streak == 1
        assert result[0].latest_state == RuntimeExecutionState.FAILED
        assert result[0].p50_duration_ms == 1000
        assert result[0].p95_duration_ms == 1000

    async def test_plan_health_counts_failures_and_current_streak(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_plan("project-a", _plan(plan_id="plan-nightly", name="nightly"), _SRC, _T0)
        await repo.upsert_plan_run(
            "project-a",
            _plan_run(run_id="ok", plan_id="plan-nightly", plan_name="nightly", started=_T0),
            _SRC,
            _T0,
        )
        await repo.upsert_plan_run(
            "project-a",
            _plan_run(
                run_id="bad",
                plan_id="plan-nightly",
                plan_name="nightly",
                state=RuntimeExecutionState.FAILED,
                started=_T0 + datetime.timedelta(hours=1),
            ),
            _SRC,
            _T0,
        )

        result = await repo.list_plan_health(["project-a"], ListRuntimePlansInput())

        assert len(result) == 1
        assert result[0].plan_id == "plan-nightly"
        assert result[0].total_runs == 2
        assert result[0].failed_runs == 1
        assert result[0].failure_rate == 0.5
        assert result[0].current_failure_streak == 1
        assert result[0].latest_state == RuntimeExecutionState.FAILED

    async def test_estate_overview_counts_unhealthy_and_stale_entities(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_source(
            "project-a",
            RuntimeSource(
                project_id="project-a",
                source_key=_SRC,
                source_type="mock",
                label="Demo source",
                status=RuntimeSyncStatus.SUCCESS,
                is_demo=True,
                last_synced_at=_T0,
                last_seen_at=_T0,
                ingested_at=_T0,
            ),
            _T0,
        )
        await repo.upsert_engine(
            "project-a",
            RuntimeEngine(
                source_key=_SRC,
                engine_id="engine-1",
                name="remote-engine-1",
                status=RuntimeEngineStatus.ONLINE,
                last_seen_at=_T0,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_job("project-a", _job(job_id="job-orders", name="orders"), _SRC, _T0)
        await repo.upsert_plan("project-a", _plan(plan_id="plan-nightly", name="nightly"), _SRC, _T0)
        await repo.upsert_job_run(
            "project-a",
            _job_run(
                run_id="bad",
                job_id="job-orders",
                job_name="orders",
                state=RuntimeExecutionState.FAILED,
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_plan_run(
            "project-a",
            _plan_run(
                run_id="plan-bad",
                plan_id="plan-nightly",
                plan_name="nightly",
                state=RuntimeExecutionState.FAILED,
            ),
            _SRC,
            _T0,
        )

        result = await repo.get_estate_overview(["project-a"], ListRuntimeInventoryInput())

        assert result.engines_unhealthy == 1
        assert result.jobs_unhealthy == 1
        assert result.plans_unhealthy == 1
        assert result.sources_stale == 1


class TestNotFound:
    async def test_get_execution_details_unknown_raises(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_details(None, GetExecutionDetailsInput(execution_id="nope"))

    async def test_get_execution_logs_unknown_raises(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_logs(None, GetExecutionLogsInput(execution_id="nope"))
