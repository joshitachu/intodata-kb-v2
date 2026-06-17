"""Integration tests for the runtime insight builder (src/runtime/insights/builder.py).

Uses the in-memory SQLite db_session fixture. Seeds rich runtime data via
seed_rich_runtime_data, then builds insights and asserts the derived rows.
"""

import datetime
from datetime import UTC

import pytest
from sqlalchemy import func, select

from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentEvidenceRow,
    RuntimeIncidentRow,
)
from src.runtime.insights.builder import build_runtime_insights_for_execution
from src.runtime.store.seed import (
    _RICH_FAILED_EXEC,
    _RICH_JOB,
    _RICH_SUCCESS_EXEC,
    seed_rich_runtime_data,
)

_SYNCED_AT = datetime.datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
_PROJECT = "esbmain"
_OTHER_PROJECT = "otherproject"


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def _get_summary(session, project_id: str, execution_id: str) -> RuntimeExecutionSummaryRow | None:
    stmt = select(RuntimeExecutionSummaryRow).where(
        RuntimeExecutionSummaryRow.project_id == project_id,
        RuntimeExecutionSummaryRow.execution_id == execution_id,
    )
    return (await session.execute(stmt)).scalars().first()


async def _get_incident(session, project_id: str, execution_id: str) -> RuntimeIncidentRow | None:
    stmt = select(RuntimeIncidentRow).where(
        RuntimeIncidentRow.project_id == project_id,
        RuntimeIncidentRow.execution_id == execution_id,
    )
    return (await session.execute(stmt)).scalars().first()


class TestSummaryContents:
    async def test_failed_execution_summary(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

        assert result.enriched_events > 0
        assert not result.had_incident is False  # failed exec => had incident

        summary = await _get_summary(db_session, _PROJECT, _RICH_FAILED_EXEC)
        assert summary is not None
        assert summary.job_name == _RICH_JOB
        assert summary.failed_component == "tOracleConnection_1"
        assert summary.primary_error_code == "ORA-12170"
        assert summary.primary_exception_class == "SQLRecoverableException"
        assert summary.warning_count > 0
        assert summary.error_count > 0
        assert summary.evidence_refs is not None
        assert len(summary.evidence_refs) > 0

    async def test_success_execution_summary(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_SUCCESS_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

        assert not result.had_incident
        summary = await _get_summary(db_session, _PROJECT, _RICH_SUCCESS_EXEC)
        assert summary is not None
        assert summary.job_name == _RICH_JOB
        assert summary.error_count == 0

    async def test_missing_execution_returns_zero_result(self, db_session):
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id="nonexistent-exec",
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        assert result.enriched_events == 0
        assert not result.had_incident


class TestIncidentContents:
    async def test_incident_created_for_failed_execution(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

        assert result.had_incident
        assert result.incident_id is not None
        assert result.signature_hash is not None

    async def test_incident_deterministic_id(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        r1 = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        r2 = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        assert r1.incident_id == r2.incident_id
        assert r1.signature_hash == r2.signature_hash

    async def test_incident_root_cause_mentions_database_timeout(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = await _get_incident(db_session, _PROJECT, _RICH_FAILED_EXEC)
        assert incident is not None
        assert incident.root_cause_candidate is not None
        candidate_lower = incident.root_cause_candidate.lower()
        assert "source database" in candidate_lower
        assert "timeout" in candidate_lower

    async def test_incident_has_confidence(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = await _get_incident(db_session, _PROJECT, _RICH_FAILED_EXEC)
        assert incident.confidence in ("high", "medium", "low")

    async def test_incident_has_next_checks(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = await _get_incident(db_session, _PROJECT, _RICH_FAILED_EXEC)
        assert incident.next_checks is not None
        assert len(incident.next_checks) > 0

    async def test_incident_has_first_bad_at(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = await _get_incident(db_session, _PROJECT, _RICH_FAILED_EXEC)
        assert incident.first_bad_at is not None

    async def test_no_incident_for_success_execution(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_SUCCESS_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = await _get_incident(db_session, _PROJECT, _RICH_SUCCESS_EXEC)
        assert incident is None


class TestEnrichedEvents:
    async def test_enriched_events_created_for_each_log(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        count = await _count(db_session, RuntimeEnrichedEventRow)
        assert count == result.enriched_events

    async def test_enriched_events_have_project_id(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        stmt = select(RuntimeEnrichedEventRow.project_id).distinct()
        project_ids = (await db_session.execute(stmt)).scalars().all()
        assert project_ids == [_PROJECT]


class TestIncidentEvidence:
    async def test_evidence_rows_created_for_incident(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        assert result.incident_id is not None
        stmt = select(RuntimeIncidentEvidenceRow).where(
            RuntimeIncidentEvidenceRow.project_id == _PROJECT,
            RuntimeIncidentEvidenceRow.incident_id == result.incident_id,
        )
        evidence_rows = (await db_session.execute(stmt)).scalars().all()
        assert len(evidence_rows) > 0

    async def test_evidence_excerpts_are_bounded(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        stmt = select(RuntimeIncidentEvidenceRow).where(
            RuntimeIncidentEvidenceRow.project_id == _PROJECT,
            RuntimeIncidentEvidenceRow.incident_id == result.incident_id,
        )
        for ev in (await db_session.execute(stmt)).scalars().all():
            if ev.excerpt:
                assert len(ev.excerpt) <= 300


class TestIdempotency:
    async def test_building_twice_does_not_duplicate_rows(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)

        for _ in range(2):
            await build_runtime_insights_for_execution(
                db_session,
                project_id=_PROJECT,
                execution_id=_RICH_FAILED_EXEC,
                source_key="mock",
                ingested_at=_SYNCED_AT,
            )

        for model in (
            RuntimeEnrichedEventRow,
            RuntimeExecutionSummaryRow,
            RuntimeIncidentRow,
            RuntimeIncidentEvidenceRow,
        ):
            assert await _count(db_session, model) > 0

        # Row counts must be stable
        counts_1 = {m: await _count(db_session, m) for m in (
            RuntimeEnrichedEventRow,
            RuntimeExecutionSummaryRow,
            RuntimeIncidentRow,
            RuntimeIncidentEvidenceRow,
            RuntimeErrorSignatureRow,
        )}

        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

        for model, expected in counts_1.items():
            assert await _count(db_session, model) == expected, f"Duplicate rows detected in {model.__tablename__}"


class TestProjectScoping:
    async def test_two_projects_have_separate_insight_rows(self, db_session):
        for pid in (_PROJECT, _OTHER_PROJECT):
            await seed_rich_runtime_data(db_session, project_id=pid, synced_at=_SYNCED_AT)
            await build_runtime_insights_for_execution(
                db_session,
                project_id=pid,
                execution_id=_RICH_FAILED_EXEC,
                source_key="mock",
                ingested_at=_SYNCED_AT,
            )

        for model in (
            RuntimeEnrichedEventRow,
            RuntimeExecutionSummaryRow,
            RuntimeIncidentRow,
        ):
            pids_in_db = (
                await db_session.execute(
                    select(model.project_id).distinct()  # type: ignore[attr-defined]
                )
            ).scalars().all()
            assert set(pids_in_db) == {_PROJECT, _OTHER_PROJECT}, (
                f"{model.__tablename__} contains unexpected project_ids: {pids_in_db}"
            )

    async def test_esbmain_rows_stay_in_esbmain(self, db_session):
        for pid in (_PROJECT, _OTHER_PROJECT):
            await seed_rich_runtime_data(db_session, project_id=pid, synced_at=_SYNCED_AT)
            await build_runtime_insights_for_execution(
                db_session,
                project_id=pid,
                execution_id=_RICH_FAILED_EXEC,
                source_key="mock",
                ingested_at=_SYNCED_AT,
            )

        stmt = select(RuntimeEnrichedEventRow).where(
            RuntimeEnrichedEventRow.project_id == _PROJECT
        )
        esbmain_rows = (await db_session.execute(stmt)).scalars().all()
        assert all(r.project_id == _PROJECT for r in esbmain_rows)
        assert len(esbmain_rows) > 0
