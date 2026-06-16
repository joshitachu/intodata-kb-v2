"""Tests for the held runtime-data repository (src/runtime/store/repository.py).

Covers project/RBAC scope enforcement, idempotent write-port upserts, read filters,
and the freshness watermark. Runs against the in-memory SQLite db_session fixture.
"""

import datetime
from datetime import UTC

import pytest
from sqlalchemy import func, select

from src.db.models.runtime_data import (
    RuntimeExecutionRow,
    RuntimeLogEventRow,
)
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeLogEvent,
    RuntimeLogSeverity,
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
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_details([], GetExecutionDetailsInput(execution_id="e1"))

    async def test_detect_failures_scoped(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        await repo.upsert_failure("project-a", _failure(execution_id="a2"), _SRC, _T0)
        await repo.upsert_failure("project-b", _failure(execution_id="b2"), _SRC, _T0)
        result = await repo.detect_failures(["project-b"], DetectFailuresInput())
        assert {f.execution_id for f in result} == {"b2"}


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


class TestNotFound:
    async def test_get_execution_details_unknown_raises(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_details(None, GetExecutionDetailsInput(execution_id="nope"))

    async def test_get_execution_logs_unknown_raises(self, db_session):
        repo = RuntimeStoreRepository(db_session)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_execution_logs(None, GetExecutionLogsInput(execution_id="nope"))
