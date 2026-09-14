"""Tests for the held runtime-store seed (src/runtime/store/seed.py).

Verifies the seed loads mock fixtures through the repository write-port, is
idempotent for a fixed synced_at, anchors freshness, and scopes rows to the
requested project.
"""

import datetime
from datetime import UTC

from sqlalchemy import func, select

from src.db.models.runtime_data import (
    RuntimeExecutionRow,
    RuntimeFailureRow,
    RuntimeLogEventRow,
)
from src.runtime.models import DetectFailuresInput, GetExecutionLogsInput, RuntimeLogSeverity
from src.runtime.store import RuntimeStoreRepository
from src.runtime.store.seed import seed_mock_runtime_data, seed_rich_runtime_data

_SYNCED_AT = datetime.datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
_PROJECT = "demo-runtime"


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar()


class TestSeedWrites:
    async def test_seed_writes_executions_failures_logs_and_watermark(self, db_session):
        summary = await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)

        assert summary.executions == await _count(db_session, RuntimeExecutionRow)
        assert summary.failures == await _count(db_session, RuntimeFailureRow)
        assert summary.log_events == await _count(db_session, RuntimeLogEventRow)
        assert summary.executions > 0
        assert summary.failures > 0
        assert summary.log_events > 0

        repo = RuntimeStoreRepository(db_session)
        assert await repo.get_freshness(None, summary.source_key, project_id=_PROJECT) == _SYNCED_AT

    async def test_all_rows_use_requested_project_id(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        for model in (RuntimeExecutionRow, RuntimeFailureRow, RuntimeLogEventRow):
            project_ids = (await db_session.execute(select(model.project_id).distinct())).scalars().all()
            assert project_ids == [_PROJECT]


class TestSeedIdempotency:
    async def test_seeding_twice_does_not_duplicate(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        counts_after_first = {
            model: await _count(db_session, model)
            for model in (RuntimeExecutionRow, RuntimeFailureRow, RuntimeLogEventRow)
        }
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        for model, first in counts_after_first.items():
            assert await _count(db_session, model) == first


class TestRichSeed:
    async def test_rich_seed_writes_deep_failed_run(self, db_session):
        summary = await seed_rich_runtime_data(db_session, project_id="esbmain", synced_at=_SYNCED_AT)
        # failed order sync, prior success, success billing export, misfire, timeout
        assert summary.executions == 5
        assert summary.executions == await _count(db_session, RuntimeExecutionRow)
        # Only the failed run produces a failure row: a misfire never ran and a
        # timeout was killed rather than raising.
        assert summary.failures == 1
        assert summary.log_events >= 15  # in-depth log sequence

        repo = RuntimeStoreRepository(db_session)
        failures = await repo.detect_failures(["esbmain"], DetectFailuresInput())
        assert len(failures) == 1
        failed_id = failures[0].execution_id

        logs = await repo.get_execution_logs(["esbmain"], GetExecutionLogsInput(execution_id=failed_id, limit=100))
        assert len(logs) >= 15
        # logs are time-ordered and include the escalation to ERROR
        assert any(log.severity == RuntimeLogSeverity.WARNING for log in logs)
        assert any(log.severity == RuntimeLogSeverity.ERROR for log in logs)
        timestamps = [log.timestamp for log in logs]
        assert timestamps == sorted(timestamps)

    async def test_rich_seed_min_severity_filter(self, db_session):
        await seed_rich_runtime_data(db_session, project_id="esbmain", synced_at=_SYNCED_AT)
        repo = RuntimeStoreRepository(db_session)
        failed_id = (await repo.detect_failures(["esbmain"], DetectFailuresInput()))[0].execution_id
        errors = await repo.get_execution_logs(
            ["esbmain"], GetExecutionLogsInput(execution_id=failed_id, min_severity=RuntimeLogSeverity.ERROR)
        )
        assert len(errors) >= 1
        assert all(log.severity == RuntimeLogSeverity.ERROR for log in errors)

    async def test_rich_seed_is_idempotent(self, db_session):
        await seed_rich_runtime_data(db_session, project_id="esbmain", synced_at=_SYNCED_AT)
        repo = RuntimeStoreRepository(db_session)
        failed_id = (await repo.detect_failures(["esbmain"], DetectFailuresInput()))[0].execution_id
        first = len(await repo.get_execution_logs(["esbmain"], GetExecutionLogsInput(execution_id=failed_id, limit=100)))
        await seed_rich_runtime_data(db_session, project_id="esbmain", synced_at=_SYNCED_AT)
        second = len(await repo.get_execution_logs(["esbmain"], GetExecutionLogsInput(execution_id=failed_id, limit=100)))
        assert first == second


class TestSeedFreshness:
    async def test_failures_fall_within_freshness_window(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        repo = RuntimeStoreRepository(db_session)
        # Failures detected within the 24h before the watermark.
        since = _SYNCED_AT - datetime.timedelta(hours=24)
        failures = await repo.detect_failures([_PROJECT], DetectFailuresInput(since=since))
        assert len(failures) > 0
        for failure in failures:
            assert since <= failure.failed_at <= _SYNCED_AT

    async def test_logs_are_shifted_to_window(self, db_session):
        await seed_mock_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        repo = RuntimeStoreRepository(db_session)
        failures = await repo.detect_failures([_PROJECT], DetectFailuresInput())
        logs = await repo.get_execution_logs(
            [_PROJECT], GetExecutionLogsInput(execution_id=failures[0].execution_id)
        )
        assert len(logs) > 0
        for log in logs:
            assert log.timestamp <= _SYNCED_AT
