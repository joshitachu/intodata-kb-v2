"""Tests for the similar-failure repository method (EP-006 slice 5)."""

import datetime
import hashlib
from datetime import UTC

import pytest
from sqlalchemy import select

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeLogEventRow
from src.db.models.runtime_insights import RuntimeIncidentRow
from src.runtime.insights.builder import build_runtime_insights_for_execution
from src.runtime.insights.repository import RuntimeInsightRepository
from src.runtime.store import RuntimeRecordNotFoundError
from src.runtime.store.seed import _RICH_FAILED_EXEC, _RICH_JOB, seed_rich_runtime_data

_SYNCED_AT = datetime.datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
_PROJECT = "esbmain"
_OTHER = "other-proj"


async def _build(session, project_id: str, execution_id: str = _RICH_FAILED_EXEC) -> None:
    await seed_rich_runtime_data(session, project_id=project_id, synced_at=_SYNCED_AT)
    await build_runtime_insights_for_execution(
        session,
        project_id=project_id,
        execution_id=execution_id,
        source_key="mock",
        ingested_at=_SYNCED_AT,
    )


async def _get_incident_id(session, project_id: str, execution_id: str) -> str:
    stmt = select(RuntimeIncidentRow).where(
        RuntimeIncidentRow.project_id == project_id,
        RuntimeIncidentRow.execution_id == execution_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    assert row is not None, f"no incident for {project_id}/{execution_id}"
    return row.incident_id


async def _insert_second_execution(session, project_id: str, execution_id: str) -> None:
    exec2 = RuntimeExecutionRow(
        project_id=project_id,
        execution_id=execution_id,
        job_name=_RICH_JOB,
        project_name=project_id,
        state="failed",
        source_key="mock",
        started_at=datetime.datetime(2026, 6, 18, 10, 0, tzinfo=UTC),
        ended_at=datetime.datetime(2026, 6, 18, 10, 1, tzinfo=UTC),
        duration_ms=60000,
        ingested_at=_SYNCED_AT,
    )
    session.add(exec2)
    for offset, msg in [
        (30, "[tOracleConnection_1] Connection attempt 3/3 failed: ORA-12170: TNS:Connect timeout"),
        (31, "[tOracleConnection_1] java.sql.SQLRecoverableException: IO Error: Connection timed out"),
    ]:
        _ts = datetime.datetime(2026, 6, 18, 10, 0, offset, tzinfo=UTC)
        _fp = hashlib.sha256(f"{_ts.isoformat()}|error|{msg}".encode()).hexdigest()[:64]
        session.add(RuntimeLogEventRow(
            project_id=project_id,
            execution_id=execution_id,
            source_key="mock",
            severity="error",
            message=msg,
            event_fingerprint=_fp,
            timestamp=_ts,
            ingested_at=_SYNCED_AT,
        ))
    await session.flush()
    await build_runtime_insights_for_execution(
        session,
        project_id=project_id,
        execution_id=execution_id,
        source_key="mock",
        ingested_at=_SYNCED_AT,
    )


class TestGetSimilarFailuresForIncident:
    async def test_first_incident_is_not_recurring(self, db_session):
        await _build(db_session, _PROJECT)
        incident_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)

        repo = RuntimeInsightRepository(db_session)
        result = await repo.get_similar_failures_for_incident([_PROJECT], incident_id)

        assert result.incident_id == incident_id
        assert result.recurrence.occurrence_count >= 1
        assert result.recurrence.is_recurring is False
        assert result.similar_incidents == []

    async def test_deny_all_scope_raises_not_found(self, db_session):
        await _build(db_session, _PROJECT)
        incident_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)

        repo = RuntimeInsightRepository(db_session)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_similar_failures_for_incident([], incident_id)

    async def test_unknown_incident_raises_not_found(self, db_session):
        await _build(db_session, _PROJECT)
        repo = RuntimeInsightRepository(db_session)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_similar_failures_for_incident([_PROJECT], "deadbeef" * 4)

    async def test_cross_project_incident_raises_not_found(self, db_session):
        await _build(db_session, _PROJECT)
        await _build(db_session, _OTHER)
        esbmain_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)

        repo = RuntimeInsightRepository(db_session)
        with pytest.raises(RuntimeRecordNotFoundError):
            await repo.get_similar_failures_for_incident([_OTHER], esbmain_id)

    async def test_second_incident_shows_as_similar(self, db_session):
        await _build(db_session, _PROJECT)
        first_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)

        await _insert_second_execution(db_session, _PROJECT, "esbmain-route-second")
        second_id = await _get_incident_id(db_session, _PROJECT, "esbmain-route-second")

        repo = RuntimeInsightRepository(db_session)
        result = await repo.get_similar_failures_for_incident([_PROJECT], second_id)

        assert result.recurrence.occurrence_count == 2
        assert result.recurrence.is_recurring is True
        assert result.recurrence.similar_count == 1
        assert len(result.similar_incidents) == 1
        assert result.similar_incidents[0].incident_id == first_id

    async def test_current_incident_excluded_from_similar_list(self, db_session):
        await _build(db_session, _PROJECT)
        incident_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)

        repo = RuntimeInsightRepository(db_session)
        result = await repo.get_similar_failures_for_incident([_PROJECT], incident_id)

        assert incident_id not in [s.incident_id for s in result.similar_incidents]

    async def test_same_signature_other_project_not_visible(self, db_session):
        await _build(db_session, _PROJECT)
        await _build(db_session, _OTHER)
        esbmain_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)
        other_id = await _get_incident_id(db_session, _OTHER, _RICH_FAILED_EXEC)

        repo = RuntimeInsightRepository(db_session)
        result = await repo.get_similar_failures_for_incident([_PROJECT], esbmain_id)

        similar_ids = [s.incident_id for s in result.similar_incidents]
        assert other_id not in similar_ids

    async def test_limit_is_respected(self, db_session):
        await _build(db_session, _PROJECT)
        incident_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)

        repo = RuntimeInsightRepository(db_session)
        result = await repo.get_similar_failures_for_incident([_PROJECT], incident_id, limit=2)

        assert len(result.similar_incidents) <= 2

    async def test_alert_metadata_fields_present(self, db_session):
        await _build(db_session, _PROJECT)
        incident_id = await _get_incident_id(db_session, _PROJECT, _RICH_FAILED_EXEC)

        repo = RuntimeInsightRepository(db_session)
        result = await repo.get_similar_failures_for_incident([_PROJECT], incident_id)

        r = result.recurrence
        assert len(r.alert_fingerprint) > 0
        assert len(r.dedupe_key) > 0
        assert r.alert_title
        assert r.alert_reason
