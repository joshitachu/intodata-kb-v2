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
    ListRuntimeDeploymentsInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobsInput,
    ListRuntimePlanRunsInput,
    ListRuntimePlansInput,
    RuntimeExecution,
    RuntimeEngine,
    RuntimeDeploymentTarget,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeExecutionState,
    RuntimeExecutableKind,
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
                estate_key="project-a",
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
                    estate_key=project_id,
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

        # The mirror inventory is filtered on the ROW STAMP, which this model
        # now names honestly: `estate_key`, the value written into every
        # mirrored row's `project_id` column.
        assert [source.estate_key for source in sources] == ["project-a"]
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

    async def test_repeated_log_lines_are_preserved_by_ordinal(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event("project-a", _log(message="retry", ts=_T0), _SRC, _T0)
        await repo.upsert_log_event(
            "project-a", _log(message="retry", ts=_T0).model_copy(update={"line_ordinal": 1}), _SRC, _T0
        )
        events = await repo.get_execution_logs(None, GetExecutionLogsInput(execution_id="e1"))
        assert [(event.message, event.line_ordinal) for event in events] == [("retry", 0), ("retry", 1)]


class TestLogRetentionAndSearch:
    async def test_prune_removes_only_expired_logs(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        now = _T0 + datetime.timedelta(days=100)
        await repo.upsert_log_event("project-a", _log(message="old", ts=_T0), _SRC, now)
        await repo.upsert_log_event(
            "project-a", _log(message="fresh", ts=now).model_copy(update={"line_ordinal": 1}), _SRC, now
        )
        assert await repo.prune_expired_log_events(now=now) == 1
        assert [event.message for event in await repo.get_execution_logs(None, GetExecutionLogsInput(execution_id="e1"))] == ["fresh"]

    async def test_search_scopes_and_redacts_bounded_snippets(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event(
            "project-a", _log(message="ORA-12170 password=secret-value connection timeout"), _SRC, _T0
        )
        await repo.upsert_log_event(
            "project-a",
            _log(message="ORA-12170 password=secret-value connection timeout").model_copy(
                update={"line_ordinal": 1}
            ),
            _SRC,
            _T0,
        )
        await repo.upsert_log_event("project-b", _log(message="ORA-12170 private"), _SRC, _T0)
        results = await repo.search_log_events(["project-a"], query="ORA-12170", snippet_chars=40)
        assert len(results) == 1
        assert results[0]["project_id"] == "project-a"
        assert "secret-value" not in str(results[0]["snippet"])
        assert len(str(results[0]["snippet"])) <= 40
        assert results[0]["occurrence_count"] == 2

    async def test_blank_query_returns_newest_first_instead_of_nothing(self, db_session):
        """Recency mode. Every path required a keyword, so "show me the latest
        logs" matched nothing and was indistinguishable from an empty corpus:
        `plainto_tsquery('simple','latest logs')` hit 0 of 173,537 real rows."""
        repo = RuntimeStoreRepository(db_session)
        older = _T0
        newer = _T0 + datetime.timedelta(hours=5)
        await repo.upsert_log_event(
            "project-a", _log(message="older entry", ts=older), _SRC, _T0
        )
        await repo.upsert_log_event(
            "project-a",
            _log(message="newest entry", ts=newer).model_copy(update={"line_ordinal": 1}),
            _SRC,
            _T0,
        )

        results = await repo.search_log_events(["project-a"], query="")

        assert [str(row["snippet"]) for row in results] == ["newest entry", "older entry"]

    async def test_blank_query_still_honours_scope_and_window(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event("project-a", _log(message="mine"), _SRC, _T0)
        await repo.upsert_log_event("project-b", _log(message="theirs"), _SRC, _T0)

        results = await repo.search_log_events(["project-a"], query="")

        assert [row["project_id"] for row in results] == ["project-a"]

    async def test_exception_class_inside_a_dotted_fqn_is_findable(self, db_session):
        """to_tsvector('simple') keeps `java.lang.NullPointerException` as ONE
        lexeme, so the bare class name matched nothing: 0 tsquery hits against 30
        rows that contained it. Trigram similarity could not rescue it either --
        a short query against a long log line falls under the 0.3 threshold."""
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event(
            "project-a",
            _log(
                message=(
                    "Exception in component tSetGlobalVar_3 "
                    "java.lang.NullPointerException: null at foo.Bar(Bar.java:42)"
                )
            ),
            _SRC,
            _T0,
        )

        results = await repo.search_log_events(["project-a"], query="NullPointerException")

        assert len(results) == 1

    async def test_component_name_with_an_underscore_is_matched_literally(self, db_session):
        """`_` is a LIKE single-character wildcard and it appears in nearly every
        Talend component name, so an unescaped token would silently widen."""
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_log_event(
            "project-a", _log(message="failure in tSetGlobalVar_3 during load"), _SRC, _T0
        )
        await repo.upsert_log_event(
            "project-a",
            _log(message="tSetGlobalVarX3 must not match").model_copy(
                update={"line_ordinal": 1}
            ),
            _SRC,
            _T0,
        )

        results = await repo.search_log_events(["project-a"], query="tSetGlobalVar_3")

        assert len(results) == 1
        assert "tSetGlobalVar_3" in str(results[0]["snippet"])


class TestPagination:
    async def test_execution_pagination_has_stable_tiebreaker_and_honest_total(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="a", started=_T0))
        await _seed_execution(repo, "project-a", _exec(execution_id="b", started=_T0))
        params = ListExecutionsInput(limit=1, offset=1)
        assert await repo.count_executions(["project-a"], params) == 2
        page = await repo.list_executions(["project-a"], params)
        assert len(page) == 1
        assert page[0].execution_id == "a"

    async def test_read_side_dedupe_counts_a_mirrored_execution_once(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await _seed_execution(repo, "project-a", _exec(execution_id="mirror"))
        await _seed_execution(repo, "project-b", _exec(execution_id="mirror"))
        params = ListExecutionsInput(limit=10)
        assert await repo.count_executions(None, params) == 1
        assert [item.execution_id for item in await repo.list_executions(None, params)] == ["mirror"]

    async def test_repeated_sync_state_update_is_idempotent(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.update_sync_state(_SRC, "project-a", _T0)
        later = _T0 + datetime.timedelta(hours=1)
        await repo.update_sync_state(_SRC, "project-a", later)
        assert await repo.get_freshness(None, _SRC, project_id="project-a") == later

    async def test_repeated_inventory_upsert_is_idempotent(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        source = RuntimeSource(
            estate_key="project-a",
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
    async def _seed_cluster_engines(self, repo):
        for engine_id, name in (("engine-re01", "PRD_RE_01"), ("engine-re02", "PRD_RE_02")):
            await repo.upsert_engine(
                "project-a",
                RuntimeEngine(
                    source_key=_SRC,
                    engine_id=engine_id,
                    name=name,
                    status=RuntimeEngineStatus.ONLINE,
                    version="8.0.1",
                    cluster_id="cluster-prod",
                    cluster_name="RE_Cluster_01",
                    environment_id="env-prod",
                    environment_name="Production",
                ),
                _SRC,
                _T0,
            )

    async def test_cluster_members_collapse_into_one_row_that_states_its_coverage(
        self, db_session
    ):
        """A clustered route is ONE deployment, and its coverage is not implied.

        Verified on a live two-engine Talend cluster: one member reported 7,210
        completed exchanges while the other, on an engine that ships no logs,
        reported nothing at all. Summing the pair publishes a floor, so the row
        has to say how many engines it was actually measured on.
        """
        repo = RuntimeStoreRepository(db_session)
        await self._seed_cluster_engines(repo)
        route = _job(job_id="route-cluster", name="sales_orders_route").model_copy(
            update={"executable_kind": RuntimeExecutableKind.ROUTE}
        )
        measured = _job_run(
            run_id="re01-v2",
            job_id="route-cluster",
            job_name="sales_orders_route",
            state=RuntimeExecutionState.RUNNING,
            execution_id="re01-v2",
        ).model_copy(
            update={
                "executable_kind": RuntimeExecutableKind.ROUTE,
                "deployment_target": RuntimeDeploymentTarget.CLUSTER,
                "artifact_version": "2.0",
                "engine_id": "engine-re01",
                "engine_name": "PRD_RE_01",
                "ended_at": None,
                "duration_ms": None,
                "completed_exchanges": 7210,
                "failed_exchanges": 167,
                "total_exchanges": 7377,
            }
        )
        silent = measured.model_copy(
            update={
                "run_id": "re02-v2",
                "execution_id": "re02-v2",
                "engine_id": "engine-re02",
                "engine_name": "PRD_RE_02",
                "started_at": _T0 + datetime.timedelta(seconds=36),
                "completed_exchanges": None,
                "failed_exchanges": None,
                "total_exchanges": None,
            }
        )
        await repo.upsert_job("project-a", route, _SRC, _T0)
        await repo.upsert_job_run("project-a", measured, _SRC, _T0)
        await repo.upsert_job_run("project-a", silent, _SRC, _T0)

        deployments = await repo.list_deployments(
            ["project-a"],
            ListRuntimeDeploymentsInput(executable_kind=RuntimeExecutableKind.ROUTE),
        )

        assert len(deployments) == 1, "cluster members are one deployment, not two"
        row = deployments[0]
        assert row.deployment_target == RuntimeDeploymentTarget.CLUSTER
        assert row.engine_name == "RE_Cluster_01", "the row names the cluster, not a member"
        assert row.cluster_id == "cluster-prod"
        assert row.artifact_version == "2.0"
        # Summed over the member that reported; the silent one is not counted as 0.
        assert row.completed_exchanges == 7210
        assert row.failed_exchanges == 167
        assert row.engines_total == 2
        assert row.engines_reporting == 1
        assert row.silent_engines == ["PRD_RE_02"]
        assert {member.engine_name for member in row.members} == {"PRD_RE_01", "PRD_RE_02"}
        assert [m.reported_telemetry for m in row.members if m.engine_name == "PRD_RE_02"] == [
            False
        ]

    async def test_a_superseded_cluster_version_leaves_the_inventory(self, db_session):
        """The exact production case a client raised, from TMC's own drill-down.

        `processor_inther_product_jde_rest_p001`: V1 was deployed to the CLUSTER
        in 2024 and has since stopped on both members (Failed on one, Undeployed
        on the other); V2 was then deployed BY HAND to each engine and is the
        only thing running. TMC's list view still shows all four as Running with
        a 654-day duration, which is what made this confusing -- its own detail
        view disagrees, and the detail view is right.

        So the answer to "what does the API return here" is two rows, both V2,
        one per engine. Not four, and not one.
        """
        repo = RuntimeStoreRepository(db_session)
        await self._seed_cluster_engines(repo)
        route = _job(job_id="route-inther", name="inther_product_route").model_copy(
            update={"executable_kind": RuntimeExecutableKind.ROUTE}
        )
        v1 = _job_run(
            run_id="v1-re01",
            job_id="route-inther",
            job_name="inther_product_route",
            state=RuntimeExecutionState.SUCCESS,
            execution_id="v1-re01",
        ).model_copy(
            update={
                "executable_kind": RuntimeExecutableKind.ROUTE,
                "deployment_target": RuntimeDeploymentTarget.CLUSTER,
                "artifact_version": "1.0",
                "engine_id": "engine-re01",
                "engine_name": "PRD_RE_01",
                "ended_at": _T0 + datetime.timedelta(days=2),
            }
        )
        runs = [
            v1,
            v1.model_copy(
                update={
                    "run_id": "v1-re02",
                    "execution_id": "v1-re02",
                    "state": RuntimeExecutionState.FAILED,
                    "engine_id": "engine-re02",
                    "engine_name": "PRD_RE_02",
                }
            ),
            # V2, deployed by hand per engine and still open.
            v1.model_copy(
                update={
                    "run_id": "v2-re01",
                    "execution_id": "v2-re01",
                    "state": RuntimeExecutionState.RUNNING,
                    "deployment_target": RuntimeDeploymentTarget.ENGINE,
                    "artifact_version": "2.0",
                    "started_at": _T0 + datetime.timedelta(days=3),
                    "ended_at": None,
                }
            ),
            v1.model_copy(
                update={
                    "run_id": "v2-re02",
                    "execution_id": "v2-re02",
                    "state": RuntimeExecutionState.RUNNING,
                    "deployment_target": RuntimeDeploymentTarget.ENGINE,
                    "artifact_version": "2.0",
                    "engine_id": "engine-re02",
                    "engine_name": "PRD_RE_02",
                    "started_at": _T0 + datetime.timedelta(days=3, seconds=30),
                    "ended_at": None,
                }
            ),
        ]
        await repo.upsert_job("project-a", route, _SRC, _T0)
        for run in runs:
            await repo.upsert_job_run("project-a", run, _SRC, _T0)

        deployments = await repo.list_deployments(
            ["project-a"],
            ListRuntimeDeploymentsInput(executable_kind=RuntimeExecutableKind.ROUTE),
        )

        assert len(deployments) == 2, "only the version that is actually running"
        assert {row.artifact_version for row in deployments} == {"2.0"}
        assert {row.engine_name for row in deployments} == {"PRD_RE_01", "PRD_RE_02"}
        assert all(
            row.deployment_target == RuntimeDeploymentTarget.ENGINE for row in deployments
        ), "V2 was deployed per engine, so it is not a cluster deployment"

    async def test_a_second_version_deployed_per_engine_stays_a_separate_deployment(
        self, db_session
    ):
        """V1 on the cluster and V2 by hand on each engine are three deployments.

        Verified on a live estate (`processor_inther_product_jde_rest_p001`).
        Without the version and target, all of them collapse to whichever started
        last, so a superseded deployment can present itself as the running one.
        """
        repo = RuntimeStoreRepository(db_session)
        await self._seed_cluster_engines(repo)
        route = _job(job_id="route-two-versions", name="inther_product_route").model_copy(
            update={"executable_kind": RuntimeExecutableKind.ROUTE}
        )
        base = _job_run(
            run_id="v1-re01",
            job_id="route-two-versions",
            job_name="inther_product_route",
            state=RuntimeExecutionState.RUNNING,
            execution_id="v1-re01",
        ).model_copy(
            update={
                "executable_kind": RuntimeExecutableKind.ROUTE,
                "deployment_target": RuntimeDeploymentTarget.CLUSTER,
                "artifact_version": "1.0",
                "engine_id": "engine-re01",
                "engine_name": "PRD_RE_01",
                "ended_at": None,
                "duration_ms": None,
            }
        )
        runs = [
            base,
            base.model_copy(
                update={
                    "run_id": "v1-re02",
                    "execution_id": "v1-re02",
                    "engine_id": "engine-re02",
                    "engine_name": "PRD_RE_02",
                }
            ),
            # Deployed by hand to each engine, so NOT cluster-targeted.
            base.model_copy(
                update={
                    "run_id": "v2-re01",
                    "execution_id": "v2-re01",
                    "artifact_version": "2.0",
                    "deployment_target": RuntimeDeploymentTarget.ENGINE,
                    "started_at": _T0 + datetime.timedelta(days=1),
                }
            ),
            base.model_copy(
                update={
                    "run_id": "v2-re02",
                    "execution_id": "v2-re02",
                    "artifact_version": "2.0",
                    "deployment_target": RuntimeDeploymentTarget.ENGINE,
                    "engine_id": "engine-re02",
                    "engine_name": "PRD_RE_02",
                    "started_at": _T0 + datetime.timedelta(days=1, seconds=30),
                }
            ),
        ]
        await repo.upsert_job("project-a", route, _SRC, _T0)
        for run in runs:
            await repo.upsert_job_run("project-a", run, _SRC, _T0)

        deployments = await repo.list_deployments(
            ["project-a"],
            ListRuntimeDeploymentsInput(executable_kind=RuntimeExecutableKind.ROUTE),
        )

        assert len(deployments) == 3
        cluster_rows = [
            item
            for item in deployments
            if item.deployment_target == RuntimeDeploymentTarget.CLUSTER
        ]
        engine_rows = [
            item
            for item in deployments
            if item.deployment_target == RuntimeDeploymentTarget.ENGINE
        ]
        assert len(cluster_rows) == 1
        assert cluster_rows[0].artifact_version == "1.0"
        assert cluster_rows[0].engines_total == 2
        assert len(engine_rows) == 2
        assert {row.artifact_version for row in engine_rows} == {"2.0"}
        assert {row.engine_name for row in engine_rows} == {"PRD_RE_01", "PRD_RE_02"}

    async def test_every_cluster_member_is_its_own_deployment_row(self, db_session):
        """A clustered Route must not lose an engine to a rolling-redeploy race.

        Verified on a live two-engine Talend cluster: the member that reports no
        telemetry started 36 seconds AFTER the member holding the counters, so a
        key without `engine_id` published "Not supplied" while 7,210 completed
        and 167 failed exchanges sat in the row it had just discarded.
        """
        repo = RuntimeStoreRepository(db_session)
        route = _job(job_id="route-cluster", name="sales_orders_route").model_copy(
            update={"executable_kind": RuntimeExecutableKind.ROUTE}
        )
        # The engine that HAS the telemetry started first.
        measured = _job_run(
            run_id="deployment-re01",
            job_id="route-cluster",
            job_name="sales_orders_route",
            state=RuntimeExecutionState.RUNNING,
            execution_id="deployment-re01",
        ).model_copy(
            update={
                "executable_kind": RuntimeExecutableKind.ROUTE,
                "engine_id": "engine-re01",
                "engine_name": "PRD_RE_01",
                "ended_at": None,
                "duration_ms": None,
                "completed_exchanges": 7210,
                "failed_exchanges": 167,
                "total_exchanges": 7377,
            }
        )
        # The silent engine started 36 seconds later, so it wins a newest-only key.
        silent = measured.model_copy(
            update={
                "run_id": "deployment-re02",
                "execution_id": "deployment-re02",
                "engine_id": "engine-re02",
                "engine_name": "PRD_RE_02",
                "started_at": _T0 + datetime.timedelta(seconds=36),
                "completed_exchanges": None,
                "failed_exchanges": None,
                "total_exchanges": None,
            }
        )
        await repo.upsert_job("project-a", route, _SRC, _T0)
        await repo.upsert_job_run("project-a", measured, _SRC, _T0)
        await repo.upsert_job_run("project-a", silent, _SRC, _T0)

        deployments = await repo.list_deployments(
            ["project-a"],
            ListRuntimeDeploymentsInput(executable_kind=RuntimeExecutableKind.ROUTE),
        )

        by_engine = {item.engine_name: item for item in deployments}
        assert set(by_engine) == {"PRD_RE_01", "PRD_RE_02"}, (
            "both cluster members must survive; the later-starting one must not "
            "displace the one carrying the counters"
        )
        assert by_engine["PRD_RE_01"].completed_exchanges == 7210
        assert by_engine["PRD_RE_01"].failed_exchanges == 167
        assert by_engine["PRD_RE_02"].total_exchanges is None
        # Newest first, so the silent member leads -- it is still reported, not hidden.
        assert deployments[0].engine_name == "PRD_RE_02"
        assert (
            await repo.count_deployments(
                ["project-a"],
                ListRuntimeDeploymentsInput(executable_kind=RuntimeExecutableKind.ROUTE),
            )
            == 2
        )

    async def test_routes_are_separate_deployments_not_job_inventory(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        route = _job(job_id="route-1", name="orders_route").model_copy(
            update={"executable_kind": RuntimeExecutableKind.ROUTE}
        )
        never_deployed = _job(job_id="route-2", name="customer_route").model_copy(
            update={
                "executable_kind": RuntimeExecutableKind.ROUTE,
                "metric_path": "customer_route",
            }
        )
        deployment = _job_run(
            run_id="deployment-1",
            job_id="route-1",
            job_name="orders_route",
            state=RuntimeExecutionState.RUNNING,
        ).model_copy(
            update={
                "executable_kind": RuntimeExecutableKind.ROUTE,
                "ended_at": None,
                "duration_ms": None,
                "total_exchanges": 42,
                "failed_exchanges": 1,
            }
        )
        previous_deployment = deployment.model_copy(
            update={
                "run_id": "deployment-0",
                "execution_id": "deployment-0",
                "state": RuntimeExecutionState.FAILED,
                "started_at": _T0 - datetime.timedelta(days=1),
                "ended_at": _T0 - datetime.timedelta(days=1) + datetime.timedelta(seconds=1),
            }
        )
        await repo.upsert_job("project-a", route, _SRC, _T0)
        await repo.upsert_job("project-a", never_deployed, _SRC, _T0)
        await repo.upsert_job_run("project-a", previous_deployment, _SRC, _T0)
        await repo.upsert_job_run("project-a", deployment, _SRC, _T0)

        jobs = await repo.list_jobs(
            ["project-a"],
            ListRuntimeJobsInput(executable_kind=RuntimeExecutableKind.JOB),
        )
        routes = await repo.list_deployments(
            ["project-a"],
            ListRuntimeDeploymentsInput(executable_kind=RuntimeExecutableKind.ROUTE),
        )
        failed_routes = await repo.list_deployments(
            ["project-a"],
            ListRuntimeDeploymentsInput(
                executable_kind=RuntimeExecutableKind.ROUTE,
                state=RuntimeExecutionState.FAILED,
            ),
        )

        assert jobs == []
        assert len(routes) == 2
        assert routes[0].deployment_id == "deployment-1"
        assert routes[0].ended_at is None
        assert routes[0].total_exchanges == 42
        assert routes[0].failed_exchanges == 1
        assert routes[1].executable_name == "customer_route"
        assert routes[1].deployment_id is None
        assert routes[1].state is None
        assert routes[1].started_at is None
        assert routes[1].metric_path == "customer_route"
        assert failed_routes == []
        assert (
            await repo.count_deployments(
                ["project-a"],
                ListRuntimeDeploymentsInput(
                    executable_kind=RuntimeExecutableKind.ROUTE,
                    state=RuntimeExecutionState.RUNNING,
                ),
            )
            == 1
        )

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
                estate_key="project-a",
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
