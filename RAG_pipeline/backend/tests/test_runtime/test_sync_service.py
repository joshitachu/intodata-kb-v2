"""Tests for the source-agnostic runtime sync service."""

import datetime
from datetime import UTC

from sqlalchemy import select

from src.db.models.runtime_insights import RuntimeIncidentRow
from src.runtime.models import (
    ListRuntimeArtifactsInput,
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeJobsInput,
    ListRuntimePlansInput,
    RuntimeEngine,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeArtifact,
    RuntimeConnection,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeJobRun,
    RuntimePlan,
    RuntimePlanRun,
    RuntimeComponentMetric,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimeSource,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.store import RuntimeStoreRepository
from src.runtime.sync import RuntimeSyncBatch, run_runtime_sync_cycle

_NOW = datetime.datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
_PROJECT = "project-a"
_SOURCE = "fixture"


class _BatchAdapter:
    def __init__(self, batch: RuntimeSyncBatch) -> None:
        self._batch = batch

    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        assert project_id == _PROJECT
        return self._batch


class _FailingAdapter:
    async def fetch_runtime_batch(self, *, project_id: str) -> RuntimeSyncBatch:
        raise RuntimeError(
            "token=abc123 password=hunter2 raw payload {'authorization': 'Bearer secret'}"
        )


def _failed_execution(execution_id: str = "exec-1") -> RuntimeExecution:
    return RuntimeExecution(
        execution_id=execution_id,
        job_name="orders_sync",
        project_name=_PROJECT,
        state=RuntimeExecutionState.FAILED,
        started_at=_NOW,
        ended_at=_NOW + datetime.timedelta(seconds=30),
        duration_ms=30000,
    )


def _failure(execution_id: str = "exec-1") -> RuntimeFailure:
    return RuntimeFailure(
        execution_id=execution_id,
        job_name="orders_sync",
        failed_at=_NOW + datetime.timedelta(seconds=30),
        error_summary="ORA-12170 timeout",
    )


def _log(message: str, execution_id: str = "exec-1") -> RuntimeLogEvent:
    return RuntimeLogEvent(
        execution_id=execution_id,
        timestamp=_NOW + datetime.timedelta(seconds=30),
        severity=RuntimeLogSeverity.ERROR,
        message=message,
    )


def _engine_log(message: str, line_ordinal: int, execution_id: str = "deploy-1") -> RuntimeLogEvent:
    """A Route deployment-log line: engine scoped, not attribution safe."""
    return RuntimeLogEvent(
        execution_id=execution_id,
        timestamp=_NOW + datetime.timedelta(seconds=line_ordinal),
        severity=RuntimeLogSeverity.INFO,
        message=message,
        line_ordinal=line_ordinal,
        scope="engine",
        attribution_safe=False,
    )


async def test_engine_scope_logs_are_replaced_not_accumulated(db_session):
    """A deployment tail is a snapshot, so re-reading it must not pile up.

    `line_ordinal` is the position inside the fetched tail window, so a route
    that emits anything between syncs shifts every retained line by one. New
    ordinal, new fingerprint, new row -- the unique index never matches and
    each cycle used to store another near-duplicate copy of the same log.
    """
    from src.db.models.runtime_data import RuntimeLogEventRow

    async def engine_rows() -> list[str]:
        rows = (
            await db_session.execute(
                select(RuntimeLogEventRow.message).where(
                    RuntimeLogEventRow.project_id == _PROJECT,
                    RuntimeLogEventRow.scope == "engine",
                )
            )
        ).scalars().all()
        return list(rows)

    first = RuntimeSyncBatch(
        log_events=[_engine_log("line A", 0), _engine_log("line B", 1)],
        synced_at=_NOW,
    )
    await run_runtime_sync_cycle(
        db_session, adapter=_BatchAdapter(first), source_key=_SOURCE,
        project_id=_PROJECT, now=_NOW,
    )
    assert sorted(await engine_rows()) == ["line A", "line B"]

    # The tail moved on: "line A" aged out, "line C" arrived, and the survivor
    # shifted ordinal -- which is exactly what defeated the fingerprint.
    later = _NOW + datetime.timedelta(minutes=10)
    second = RuntimeSyncBatch(
        log_events=[_engine_log("line B", 0), _engine_log("line C", 1)],
        synced_at=later,
    )
    await run_runtime_sync_cycle(
        db_session, adapter=_BatchAdapter(second), source_key=_SOURCE,
        project_id=_PROJECT, now=later,
    )

    surviving = sorted(await engine_rows())
    assert surviving == ["line B", "line C"], (
        f"deployment tail should be replaced, got {surviving}"
    )


async def test_execution_scope_logs_still_accumulate(db_session):
    """Per-run job logs are history, not a snapshot: they must be kept."""
    from src.db.models.runtime_data import RuntimeLogEventRow

    await run_runtime_sync_cycle(
        db_session,
        adapter=_BatchAdapter(RuntimeSyncBatch(log_events=[_log("run one")], synced_at=_NOW)),
        source_key=_SOURCE, project_id=_PROJECT, now=_NOW,
    )
    later = _NOW + datetime.timedelta(minutes=10)
    await run_runtime_sync_cycle(
        db_session,
        adapter=_BatchAdapter(
            RuntimeSyncBatch(log_events=[_log("run two", execution_id="exec-2")], synced_at=later)
        ),
        source_key=_SOURCE, project_id=_PROJECT, now=later,
    )

    kept = (
        await db_session.execute(
            select(RuntimeLogEventRow.message).where(
                RuntimeLogEventRow.project_id == _PROJECT,
                RuntimeLogEventRow.scope != "engine",
            )
        )
    ).scalars().all()
    assert sorted(kept) == ["run one", "run two"]


async def test_sync_cycle_writes_rows_and_builds_insights(db_session):
    batch = RuntimeSyncBatch(
        executions=[_failed_execution()],
        failures=[_failure()],
        log_events=[
            _log(
                "[tOracleConnection_1] java.sql.SQLRecoverableException: "
                "ORA-12170: TNS:Connect timeout"
            )
        ],
        component_metrics=[
            RuntimeComponentMetric(
                execution_id="exec-1",
                component_key="tDBInput_1",
                rows_total=100,
                rows_rejected=2,
                duration_ms=123,
            )
        ],
        synced_at=_NOW,
    )

    result = await run_runtime_sync_cycle(
        db_session,
        adapter=_BatchAdapter(batch),
        source_key=_SOURCE,
        project_id=_PROJECT,
        now=_NOW,
    )

    assert result.status == RuntimeSyncStatus.SUCCESS
    assert result.executions_written == 1
    assert result.failures_written == 1
    assert result.log_events_written == 1
    assert result.component_metrics_written == 1
    assert result.insights_built == 1

    repo = RuntimeStoreRepository(db_session)
    states = await repo.list_sync_states(None, source_key=_SOURCE, project_id=_PROJECT)
    assert len(states) == 1
    assert states[0].status == RuntimeSyncStatus.SUCCESS
    assert states[0].last_synced_at == _NOW

    incident = (
        await db_session.execute(
            select(RuntimeIncidentRow).where(
                RuntimeIncidentRow.project_id == _PROJECT,
                RuntimeIncidentRow.execution_id == "exec-1",
            )
        )
    ).scalars().first()
    assert incident is not None
    assert "timeout" in incident.root_cause_candidate.lower()


async def test_sync_cycle_writes_inventory_and_run_facts(db_session):
    batch = RuntimeSyncBatch(
        sources=[
            RuntimeSource(
                estate_key=_PROJECT,
                source_key=_SOURCE,
                source_type="fixture",
                label="Fixture Runtime",
                status=RuntimeSyncStatus.SUCCESS,
                is_demo=True,
                last_synced_at=_NOW,
                last_seen_at=_NOW,
                ingested_at=_NOW,
            )
        ],
        environments=[
            RuntimeEnvironment(
                source_key=_SOURCE,
                environment_id="env-prod",
                name="PROD",
                last_seen_at=_NOW,
            )
        ],
        workspaces=[
            RuntimeWorkspace(
                source_key=_SOURCE,
                workspace_id="ws-1",
                name="Analytics",
                environment_id="env-prod",
                environment_name="PROD",
                last_seen_at=_NOW,
            )
        ],
        engines=[
            RuntimeEngine(
                source_key=_SOURCE,
                engine_id="eng-1",
                name="prod-engine-01",
                status=RuntimeEngineStatus.ONLINE,
                environment_id="env-prod",
                environment_name="PROD",
                last_seen_at=_NOW,
            )
        ],
        jobs=[
            RuntimeJob(
                source_key=_SOURCE,
                job_id="task-1",
                name="nightly-load",
                environment_id="env-prod",
                environment_name="PROD",
                workspace_id="ws-1",
                workspace_name="Analytics",
                engine_id="eng-1",
                engine_name="prod-engine-01",
                last_seen_at=_NOW,
            )
        ],
        artifacts=[
            RuntimeArtifact(
                source_key=_SOURCE,
                artifact_id="artifact-1",
                name="orders_route",
                artifact_type="route",
                version="1.0.0",
                workspace_id="ws-1",
                workspace_name="Analytics",
                owner="platform",
                last_seen_at=_NOW,
                source_updated_at=_NOW,
            )
        ],
        connections=[
            RuntimeConnection(
                source_key=_SOURCE,
                connection_id="conn-1",
                name="Oracle Orders",
                connection_type="oracle",
                workspace_id="ws-1",
                workspace_name="Analytics",
                owner="dba",
                last_seen_at=_NOW,
                source_updated_at=_NOW,
            )
        ],
        job_dependencies=[
            RuntimeJobDependency(
                source_key=_SOURCE,
                job_id="task-1",
                job_name="nightly-load",
                dependency_type="connection",
                dependency_id="conn-1",
                dependency_name="Oracle Orders",
                dependency_kind="oracle",
                workspace_id="ws-1",
                workspace_name="Analytics",
                last_seen_at=_NOW,
            )
        ],
        job_runs=[
            RuntimeJobRun(
                source_key=_SOURCE,
                run_id="exec-1",
                job_id="task-1",
                job_name="nightly-load",
                execution_id="exec-1",
                state=RuntimeExecutionState.SUCCESS,
                engine_id="eng-1",
                engine_name="prod-engine-01",
                started_at=_NOW,
                ended_at=_NOW + datetime.timedelta(seconds=10),
                duration_ms=10000,
            )
        ],
        plans=[
            RuntimePlan(
                source_key=_SOURCE,
                plan_id="plan-1",
                name="nightly-orchestration",
                workspace_id="ws-1",
                workspace_name="Analytics",
                steps=[],
                last_seen_at=_NOW,
            )
        ],
        plan_runs=[
            RuntimePlanRun(
                source_key=_SOURCE,
                run_id="pexec-1",
                plan_id="plan-1",
                plan_name="nightly-orchestration",
                state=RuntimeExecutionState.SUCCESS,
                started_at=_NOW,
                ended_at=_NOW + datetime.timedelta(minutes=5),
                duration_ms=300000,
            )
        ],
        synced_at=_NOW,
    )

    result = await run_runtime_sync_cycle(
        db_session,
        adapter=_BatchAdapter(batch),
        source_key=_SOURCE,
        project_id=_PROJECT,
        now=_NOW,
    )

    assert result.sources_written == 1
    assert result.environments_written == 1
    assert result.workspaces_written == 1
    assert result.engines_written == 1
    assert result.jobs_written == 1
    assert result.artifacts_written == 1
    assert result.connections_written == 1
    assert result.job_dependencies_written == 1
    assert result.job_runs_written == 1
    assert result.plans_written == 1
    assert result.plan_runs_written == 1

    repo = RuntimeStoreRepository(db_session)
    assert [engine.name for engine in await repo.list_engines(None, ListRuntimeEnginesInput())] == ["prod-engine-01"]
    assert [job.name for job in await repo.list_jobs(None, ListRuntimeJobsInput())] == ["nightly-load"]
    assert [artifact.name for artifact in await repo.list_artifacts(None, ListRuntimeArtifactsInput())] == ["orders_route"]
    assert [connection.name for connection in await repo.list_connections(None, ListRuntimeConnectionsInput())] == ["Oracle Orders"]
    assert [
        dependency.dependency_id
        for dependency in await repo.list_job_dependencies(None, ListRuntimeDependenciesInput())
    ] == ["conn-1"]
    assert [plan.name for plan in await repo.list_plans(None, ListRuntimePlansInput())] == ["nightly-orchestration"]


async def test_sync_cycle_failure_records_sanitized_status(db_session):
    result = await run_runtime_sync_cycle(
        db_session,
        adapter=_FailingAdapter(),
        source_key=_SOURCE,
        project_id=_PROJECT,
        now=_NOW,
    )

    assert result.status == RuntimeSyncStatus.FAILED
    assert result.last_synced_at is None
    assert result.error_summary is not None
    lowered = result.error_summary.lower()
    assert "abc123" not in lowered
    assert "hunter2" not in lowered
    assert "secret" not in lowered

    repo = RuntimeStoreRepository(db_session)
    states = await repo.list_sync_states(None, source_key=_SOURCE, project_id=_PROJECT)
    assert states[0].status == RuntimeSyncStatus.FAILED
    assert states[0].error_summary == result.error_summary


async def test_partial_sync_records_partial_without_fresh_watermark(db_session):
    batch = RuntimeSyncBatch(
        executions=[_failed_execution("partial-exec")],
        log_events=[_log("[engine_1] engine failed", "partial-exec")],
        partial=True,
        failure_reason_code="page timeout / source 1",
        error_summary="Page token=abc123 timed out",
        synced_at=_NOW,
    )

    result = await run_runtime_sync_cycle(
        db_session,
        adapter=_BatchAdapter(batch),
        source_key=_SOURCE,
        project_id=_PROJECT,
        now=_NOW,
    )

    assert result.status == RuntimeSyncStatus.PARTIAL
    assert result.last_synced_at is None
    assert result.failure_reason_code == "page_timeout_source_1"
    assert result.error_summary == "Page <redacted> timed out"

    repo = RuntimeStoreRepository(db_session)
    states = await repo.list_sync_states(None, source_key=_SOURCE, project_id=_PROJECT)
    assert states[0].status == RuntimeSyncStatus.PARTIAL
    assert states[0].last_synced_at is None
    assert await repo.get_freshness(None, _SOURCE, project_id=_PROJECT) is None


async def test_sync_status_read_is_project_scoped(db_session):
    repo = RuntimeStoreRepository(db_session)
    await repo.record_sync_state(
        source_key=_SOURCE,
        project_id="project-a",
        status=RuntimeSyncStatus.SUCCESS,
        last_synced_at=_NOW,
        failure_reason_code=None,
        error_summary=None,
        updated_at=_NOW,
    )
    await repo.record_sync_state(
        source_key=_SOURCE,
        project_id="project-b",
        status=RuntimeSyncStatus.FAILED,
        last_synced_at=None,
        failure_reason_code="denied",
        error_summary="Unavailable",
        updated_at=_NOW,
    )

    states = await repo.list_sync_states(["project-a"], source_key=_SOURCE)
    assert [state.project_id for state in states] == ["project-a"]
