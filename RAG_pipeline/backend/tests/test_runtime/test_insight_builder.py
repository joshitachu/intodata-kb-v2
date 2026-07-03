"""Integration tests for the runtime insight builder (src/runtime/insights/builder.py).

Uses the in-memory SQLite db_session fixture. Seeds rich runtime data via
seed_rich_runtime_data, then builds insights and asserts the derived rows.
"""

import datetime
import hashlib
from datetime import UTC

import pytest
from sqlalchemy import func, select

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeLogEventRow
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


async def _seed_custom_failed_execution(
    session,
    *,
    project_id: str,
    execution_id: str,
    job_name: str,
    lines: list[tuple[int, str, str]],
) -> None:
    """Seed one failed execution with explicit (offset, severity, message) lines.

    Used to exercise severity-alias normalization and credential redaction with
    severities/messages not present in the canonical rich seed.
    """
    exec_row = RuntimeExecutionRow(
        project_id=project_id,
        execution_id=execution_id,
        job_name=job_name,
        project_name=project_id,
        state="failed",
        source_key="mock",
        started_at=datetime.datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
        ended_at=datetime.datetime(2026, 6, 18, 9, 1, tzinfo=UTC),
        duration_ms=60000,
        ingested_at=_SYNCED_AT,
    )
    session.add(exec_row)
    for offset, severity, message in lines:
        ts = datetime.datetime(2026, 6, 18, 9, 0, offset, tzinfo=UTC)
        fp = hashlib.sha256(f"{ts.isoformat()}|{severity}|{message}".encode()).hexdigest()[:64]
        session.add(RuntimeLogEventRow(
            project_id=project_id,
            execution_id=execution_id,
            source_key="mock",
            severity=severity,
            message=message,
            event_fingerprint=fp,
            timestamp=ts,
            ingested_at=_SYNCED_AT,
        ))
    await session.flush()


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


class TestSignatureIdempotency:
    """Signature occurrence_count must be derived from distinct incidents, not build count."""

    async def test_rebuild_same_execution_does_not_increase_occurrence_count(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        for _ in range(2):
            await build_runtime_insights_for_execution(
                db_session,
                project_id=_PROJECT,
                execution_id=_RICH_FAILED_EXEC,
                source_key="mock",
                ingested_at=_SYNCED_AT,
            )

        stmt = select(RuntimeErrorSignatureRow).where(
            RuntimeErrorSignatureRow.project_id == _PROJECT
        )
        rows = list((await db_session.execute(stmt)).scalars().all())
        assert len(rows) == 1
        assert rows[0].occurrence_count == 1

    async def test_two_distinct_executions_produce_occurrence_count_two(self, db_session):
        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=_RICH_FAILED_EXEC,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

        second_exec_id = "esbmain-route-second"
        exec2 = RuntimeExecutionRow(
            project_id=_PROJECT,
            execution_id=second_exec_id,
            job_name=_RICH_JOB,
            project_name=_PROJECT,
            state="failed",
            source_key="mock",
            started_at=datetime.datetime(2026, 6, 18, 10, 0, tzinfo=UTC),
            ended_at=datetime.datetime(2026, 6, 18, 10, 1, tzinfo=UTC),
            duration_ms=60000,
            ingested_at=_SYNCED_AT,
        )
        db_session.add(exec2)
        for offset, msg in [
            (30, "[tOracleConnection_1] Connection attempt 3/3 failed: ORA-12170: TNS:Connect timeout"),
            (31, "[tOracleConnection_1] java.sql.SQLRecoverableException: IO Error: Connection timed out"),
        ]:
            _ts = datetime.datetime(2026, 6, 18, 10, 0, offset, tzinfo=UTC)
            _fp = hashlib.sha256(f"{_ts.isoformat()}|error|{msg}".encode()).hexdigest()[:64]
            db_session.add(RuntimeLogEventRow(
                project_id=_PROJECT,
                execution_id=second_exec_id,
                source_key="mock",
                severity="error",
                message=msg,
                event_fingerprint=_fp,
                timestamp=_ts,
                ingested_at=_SYNCED_AT,
            ))
        await db_session.flush()

        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=second_exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

        stmt = select(RuntimeErrorSignatureRow).where(
            RuntimeErrorSignatureRow.project_id == _PROJECT,
            RuntimeErrorSignatureRow.error_code == "ORA-12170",
        )
        sig = (await db_session.execute(stmt)).scalars().first()
        assert sig is not None
        assert sig.occurrence_count == 2

    async def test_same_signature_other_project_counted_separately(self, db_session):
        for pid in (_PROJECT, _OTHER_PROJECT):
            await seed_rich_runtime_data(db_session, project_id=pid, synced_at=_SYNCED_AT)
            await build_runtime_insights_for_execution(
                db_session,
                project_id=pid,
                execution_id=_RICH_FAILED_EXEC,
                source_key="mock",
                ingested_at=_SYNCED_AT,
            )

        for pid in (_PROJECT, _OTHER_PROJECT):
            stmt = select(RuntimeErrorSignatureRow).where(
                RuntimeErrorSignatureRow.project_id == pid
            )
            rows = list((await db_session.execute(stmt)).scalars().all())
            assert len(rows) >= 1
            assert all(r.occurrence_count == 1 for r in rows), (
                f"{pid}: expected occurrence_count==1, got {[r.occurrence_count for r in rows]}"
            )


class TestSeverityNormalization:
    """Severity matching must be case- and alias-insensitive (FR 9)."""

    async def test_uppercase_and_alias_severities_are_counted(self, db_session):
        exec_id = "sev-variant-exec"
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name="severity_variant_job",
            lines=[
                (10, "INFO", "[route_1] Route starting (context PROD)"),
                (11, "WARNING", "[tOracleConnection_1] Connection attempt 1/3 failed"),
                (12, "WARN", "[tOracleConnection_1] Connection attempt 2/3 failed"),
                (13, "SEVERE", "[tOracleConnection_1] Connection attempt 3/3 failed: ORA-12170: TNS:Connect timeout"),
                (14, "FATAL", "[tOracleConnection_1] java.sql.SQLRecoverableException: IO Error"),
                (15, "ERROR", "[route_1] Route aborted"),
            ],
        )
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

        assert result.had_incident
        summary = await _get_summary(db_session, _PROJECT, exec_id)
        assert summary is not None
        # WARNING + WARN -> 2 warnings; SEVERE + FATAL + ERROR -> 3 errors.
        assert summary.warning_count == 2
        assert summary.error_count == 3
        # Causal signals must still be extracted from the alias-severity rows.
        assert summary.primary_error_code == "ORA-12170"
        assert summary.primary_exception_class == "SQLRecoverableException"

    async def test_failed_component_selected_from_alias_severity_row(self, db_session):
        exec_id = "sev-component-exec"
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name="severity_component_job",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (11, "SEVERE", "[tOracleConnection_1] ORA-12170: TNS:Connect timeout"),
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        summary = await _get_summary(db_session, _PROJECT, exec_id)
        assert summary is not None
        assert summary.failed_component == "tOracleConnection_1"


class TestOracleNoRegression:
    """The Oracle knowledge moved into the catalog as data; behavior must not drift."""

    async def test_oracle_root_cause_and_checks_preserved(self, db_session):
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
        cause = incident.root_cause_candidate.lower()
        assert "source database" in cause
        assert "timeout" in cause
        assert "ora-12170" in cause
        joined = " ".join(incident.next_checks).lower()
        assert "network connectivity" in joined
        assert "listener" in joined


class TestMultiPlatformRootCause:
    """Non-Oracle failures must yield specific (non-generic) causes + checks."""

    _GENERIC = "review log events for details"

    async def _build_and_get(self, db_session, exec_id, lines):
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name=f"{exec_id}_job",
            lines=lines,
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        return await _get_incident(db_session, _PROJECT, exec_id)

    async def test_mysql_access_denied_specific(self, db_session):
        incident = await self._build_and_get(
            db_session,
            "mysql-denied-exec",
            [
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "[tMysqlConnection_1] java.sql.SQLException: ER_ACCESS_DENIED_ERROR "
                    "access denied for user 'app'@'10.0.0.5'",
                ),
            ],
        )
        cause = incident.root_cause_candidate.lower()
        assert self._GENERIC not in cause
        assert "access denied" in cause
        joined = " ".join(incident.next_checks).lower()
        assert "password" in joined or "grants" in joined

    async def test_postgres_connection_specific(self, db_session):
        incident = await self._build_and_get(
            db_session,
            "pg-conn-exec",
            [
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "[tPostgresInput_1] org.postgresql.util.PSQLException: "
                    "connection reset; SQLSTATE 08006",
                ),
            ],
        )
        cause = incident.root_cause_candidate.lower()
        assert self._GENERIC not in cause
        assert "postgresql" in cause
        joined = " ".join(incident.next_checks).lower()
        assert "postgresql" in joined

    async def test_http_server_error_specific(self, db_session):
        incident = await self._build_and_get(
            db_session,
            "http-503-exec",
            [
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "[cHttpClient_1] Downstream call to /orders returned HTTP 503 "
                    "Service Unavailable",
                ),
            ],
        )
        cause = incident.root_cause_candidate.lower()
        assert self._GENERIC not in cause
        assert "server error" in cause
        assert "503" in cause

    async def test_mule_connectivity_specific(self, db_session):
        incident = await self._build_and_get(
            db_session,
            "mule-conn-exec",
            [
                (10, "INFO", "[flow_1] Flow starting"),
                (
                    11,
                    "ERROR",
                    "[db-connector] org.mule.runtime.api.connection.ConnectException: "
                    "could not reach target system",
                ),
            ],
        )
        cause = incident.root_cause_candidate.lower()
        assert self._GENERIC not in cause
        assert "mulesoft" in cause
        joined = " ".join(incident.next_checks).lower()
        assert "connector" in joined or "connectivity" in joined


class TestDiagnosticGoldenSet:
    """Representative failures must produce diagnostic incidents, not shallow status."""

    async def _build_and_get(self, db_session, exec_id, lines):
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name=f"{exec_id}_job",
            lines=lines,
        )
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = await _get_incident(db_session, _PROJECT, exec_id)
        assert result.had_incident
        assert incident is not None
        return incident

    async def _evidence_excerpts(self, db_session, incident_id: str) -> list[str]:
        stmt = select(RuntimeIncidentEvidenceRow.excerpt).where(
            RuntimeIncidentEvidenceRow.project_id == _PROJECT,
            RuntimeIncidentEvidenceRow.incident_id == incident_id,
        )
        return list((await db_session.execute(stmt)).scalars().all())

    @pytest.mark.parametrize(
        ("exec_id", "lines", "cause_tokens", "check_tokens"),
        [
            (
                "golden-source-offline",
                [
                    (10, "INFO", "[engine_1] Engine accepted execution"),
                    (
                        11,
                        "ERROR",
                        "[engine_1] com.runtime.EngineOfflineException: source engine is offline",
                    ),
                ],
                ("offline", "unavailable"),
                ("heartbeat", "online"),
            ),
            (
                "golden-db-timeout",
                [
                    (10, "INFO", "[route_1] Route starting"),
                    (
                        11,
                        "ERROR",
                        "[tOracleConnection_1] java.sql.SQLRecoverableException: "
                        "ORA-12170: TNS:Connect timeout",
                    ),
                ],
                ("database", "timeout"),
                ("listener", "network"),
            ),
            (
                "golden-auth-http",
                [
                    (10, "INFO", "[rest_1] Calling partner API"),
                    (11, "ERROR", "[rest_1] Downstream API returned HTTP 401 unauthorized"),
                ],
                ("authentication", "authorization"),
                ("credential", "authorized"),
            ),
            (
                "golden-jms",
                [
                    (10, "INFO", "[jms_1] Reading jms:queue:orders.in"),
                    (
                        11,
                        "ERROR",
                        "[jms_1] jakarta.jms.JMSException: broker connection refused for "
                        "jms:queue:orders.in",
                    ),
                ],
                ("broker", "queue"),
                ("queue", "dead-letter"),
            ),
            (
                "golden-resource-memory",
                [
                    (10, "INFO", "[transform_1] Processing batch"),
                    (
                        11,
                        "ERROR",
                        "[transform_1] java.lang.OutOfMemoryError: Java heap space",
                    ),
                ],
                ("memory", "resource"),
                ("heap", "scale"),
            ),
        ],
    )
    async def test_known_failure_patterns_are_diagnostic(
        self,
        db_session,
        exec_id,
        lines,
        cause_tokens,
        check_tokens,
    ):
        incident = await self._build_and_get(db_session, exec_id, lines)

        cause = (incident.root_cause_candidate or "").lower()
        checks = " ".join(incident.next_checks or []).lower()
        assert all(token in cause for token in cause_tokens)
        assert any(token in checks for token in check_tokens)
        assert incident.confidence in ("high", "medium", "low")
        assert incident.pattern_id not in (None, "generic.unknown", "exception.generic")

        excerpts = await self._evidence_excerpts(db_session, incident.incident_id)
        assert excerpts
        assert all(len(excerpt) <= 300 for excerpt in excerpts if excerpt)

    async def test_unknown_failure_is_low_confidence_and_distinct_by_causal_message(self, db_session):
        incident_a = await self._build_and_get(
            db_session,
            "golden-unknown-a",
            [
                (10, "INFO", "[route_1] Route starting"),
                (11, "ERROR", "[engine_1] engine failed while finalizing partition alpha"),
            ],
        )
        incident_b = await self._build_and_get(
            db_session,
            "golden-unknown-b",
            [
                (10, "INFO", "[route_1] Route starting"),
                (11, "ERROR", "[engine_1] engine failed while finalizing partition beta"),
            ],
        )

        assert incident_a.confidence == "low"
        assert incident_b.confidence == "low"
        assert incident_a.pattern_id == "generic.unknown"
        assert incident_b.pattern_id == "generic.unknown"
        assert incident_a.root_cause_candidate
        assert incident_b.root_cause_candidate
        assert incident_a.signature_hash != incident_b.signature_hash

        stmt = select(RuntimeErrorSignatureRow).where(
            RuntimeErrorSignatureRow.project_id == _PROJECT,
            RuntimeErrorSignatureRow.signature_hash.in_(
                [incident_a.signature_hash, incident_b.signature_hash]
            ),
        )
        signatures = list((await db_session.execute(stmt)).scalars().all())
        assert {sig.occurrence_count for sig in signatures} == {1}

    async def test_shallow_engine_failed_does_not_become_high_confidence(self, db_session):
        incident = await self._build_and_get(
            db_session,
            "golden-shallow-engine-failed",
            [
                (10, "INFO", "[route_1] Route starting"),
                (11, "ERROR", "[engine_1] engine failed"),
            ],
        )

        assert incident.confidence == "low"
        assert incident.pattern_id == "generic.unknown"
        assert "engine failed" not in (incident.root_cause_candidate or "").lower()


class TestCausalExtraction:
    """Slice 3: root cause must come from the deepest causal event, not first-seen."""

    async def test_caused_by_chain_resolves_to_root_exception(self, db_session):
        # The outermost exception is a wrapper; the deepest "Caused by:" is the fault.
        exec_id = "caused-by-exec"
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name="caused_by_job",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "[tRESTClient_1] org.apache.camel.CamelExecutionException: failed to deliver\n"
                    "\tat org.apache.camel.Pipeline.process(Pipeline.java:1)\n"
                    "Caused by: java.net.SocketTimeoutException: Read timed out\n"
                    "\tat java.base/java.net.SocketInputStream.read(SocketInputStream.java:1)",
                ),
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        summary = await _get_summary(db_session, _PROJECT, exec_id)
        assert summary is not None
        # Root (deepest) exception wins, not the outer CamelExecutionException wrapper.
        assert summary.primary_exception_class == "SocketTimeoutException"

    async def test_primary_signals_from_terminal_causal_not_first_seen(self, db_session):
        # A benign early error code must not shadow the real terminal fault.
        exec_id = "misattribution-exec"
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name="misattribution_job",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "[tWarmup_1] non-fatal error code APP-001 during warmup; continuing",
                ),
                (
                    12,
                    "ERROR",
                    "[tOracleConnection_1] Connection attempt 3/3 failed: "
                    "ORA-12170: TNS:Connect timeout",
                ),
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        summary = await _get_summary(db_session, _PROJECT, exec_id)
        assert summary is not None
        # Terminal causal code, not the first-seen APP-001.
        assert summary.primary_error_code == "ORA-12170"
        assert summary.failed_component == "tOracleConnection_1"


class TestUnknownSignatureDisambiguation:
    """Slice 3: distinct unknown failures in one job must NOT collapse together."""

    _UNKNOWN_A = [
        (10, "INFO", "[route_1] Route starting"),
        (11, "ERROR", "[route_1] Route aborted: disk volume /data is full"),
    ]
    _UNKNOWN_B = [
        (10, "INFO", "[route_1] Route starting"),
        (11, "ERROR", "[route_1] Route aborted: upstream feed produced malformed payload"),
    ]

    async def _build(self, db_session, exec_id, lines):
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name="shared_unknown_job",
            lines=lines,
        )
        return await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )

    async def test_two_distinct_unknowns_get_distinct_signatures(self, db_session):
        r1 = await self._build(db_session, "unknown-a", self._UNKNOWN_A)
        r2 = await self._build(db_session, "unknown-b", self._UNKNOWN_B)
        assert r1.signature_hash is not None
        assert r2.signature_hash is not None
        assert r1.signature_hash != r2.signature_hash

        # Each distinct unknown failure is its own signature group: occurrence 1.
        for sig in (r1.signature_hash, r2.signature_hash):
            stmt = select(RuntimeErrorSignatureRow).where(
                RuntimeErrorSignatureRow.project_id == _PROJECT,
                RuntimeErrorSignatureRow.signature_hash == sig,
            )
            row = (await db_session.execute(stmt)).scalars().first()
            assert row is not None
            assert row.occurrence_count == 1

    async def test_identical_unknowns_recur_under_one_signature(self, db_session):
        r1 = await self._build(db_session, "unknown-same-1", self._UNKNOWN_A)
        r2 = await self._build(db_session, "unknown-same-2", self._UNKNOWN_A)
        assert r1.signature_hash == r2.signature_hash

        stmt = select(RuntimeErrorSignatureRow).where(
            RuntimeErrorSignatureRow.project_id == _PROJECT,
            RuntimeErrorSignatureRow.signature_hash == r1.signature_hash,
        )
        row = (await db_session.execute(stmt)).scalars().first()
        assert row is not None
        assert row.occurrence_count == 2


class TestConfidenceAndCauseOrigin:
    """Slice 3: confidence reflects evidence quality; cause_origin defaults deterministic."""

    async def test_low_confidence_persisted_for_unknown_failure(self, db_session):
        exec_id = "low-conf-exec"
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name="low_conf_job",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (11, "ERROR", "[route_1] Route aborted: disk volume /data is full"),
            ],
        )
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        incident = await _get_incident(db_session, _PROJECT, exec_id)
        assert incident is not None
        assert incident.confidence == "low"

    async def test_high_confidence_for_specific_two_signal_failure(self, db_session):
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
        # ORA-12170 + SQLRecoverableException + specific oracle.net.timeout pattern.
        assert incident.confidence == "high"

    async def test_cause_origin_defaults_deterministic(self, db_session):
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
        assert incident.cause_origin == "deterministic"
        assert incident.pattern_id == "oracle.net.timeout"


class TestRebuildRuntimeInsights:
    """Slice 3: insight tables must be fully rebuildable from the raw runtime store."""

    async def test_rebuild_recomputes_incident_signatures(self, db_session):
        from src.runtime.insights.builder import rebuild_runtime_insights

        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        # No insights built yet — rebuild from raw store.
        rebuilt = await rebuild_runtime_insights(db_session, project_id=_PROJECT)
        assert rebuilt == 3  # failed order sync + success order sync + success billing export

        incident = await _get_incident(db_session, _PROJECT, _RICH_FAILED_EXEC)
        assert incident is not None
        assert incident.signature_hash is not None
        assert incident.cause_origin == "deterministic"

    async def test_rebuild_is_idempotent(self, db_session):
        from src.runtime.insights.builder import rebuild_runtime_insights

        await seed_rich_runtime_data(db_session, project_id=_PROJECT, synced_at=_SYNCED_AT)
        await rebuild_runtime_insights(db_session, project_id=_PROJECT)
        first = await _get_incident(db_session, _PROJECT, _RICH_FAILED_EXEC)
        first_sig = first.signature_hash

        await rebuild_runtime_insights(db_session, project_id=_PROJECT)
        again = await _get_incident(db_session, _PROJECT, _RICH_FAILED_EXEC)
        assert again.signature_hash == first_sig
        # Signature occurrence_count stays at 1 (single failed execution).
        stmt = select(RuntimeErrorSignatureRow).where(
            RuntimeErrorSignatureRow.project_id == _PROJECT,
            RuntimeErrorSignatureRow.signature_hash == first_sig,
        )
        row = (await db_session.execute(stmt)).scalars().first()
        assert row is not None
        assert row.occurrence_count == 1


class TestCredentialRedactionInPersistedFields:
    """No credential pattern may survive into endpoint, excerpt, or summary_text."""

    async def test_inline_jdbc_credentials_not_persisted(self, db_session):
        exec_id = "cred-leak-exec"
        secret_user = "scott"
        secret_pass = "tiger"
        await _seed_custom_failed_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            job_name="credential_job",
            lines=[
                (10, "INFO", "[route_1] Route starting"),
                (
                    11,
                    "ERROR",
                    "[tOracleConnection_1] Opening connection "
                    "jdbc:oracle:thin:scott/tiger@//db-prod:1521/ORCLPDB1 failed: "
                    "ORA-12170: TNS:Connect timeout",
                ),
                (
                    12,
                    "ERROR",
                    "[tMysqlConnection_1] jdbc:mysql://scott:tiger@db-prod:3306/sales "
                    "java.sql.SQLException: access denied",
                ),
            ],
        )
        result = await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id=exec_id,
            source_key="mock",
            ingested_at=_SYNCED_AT,
        )
        assert result.had_incident

        # Enriched event endpoints
        enriched = (await db_session.execute(
            select(RuntimeEnrichedEventRow).where(
                RuntimeEnrichedEventRow.project_id == _PROJECT,
                RuntimeEnrichedEventRow.execution_id == exec_id,
            )
        )).scalars().all()
        for ev in enriched:
            if ev.endpoint:
                assert secret_user not in ev.endpoint
                assert secret_pass not in ev.endpoint

        # Incident evidence excerpts
        evidence = (await db_session.execute(
            select(RuntimeIncidentEvidenceRow).where(
                RuntimeIncidentEvidenceRow.project_id == _PROJECT,
                RuntimeIncidentEvidenceRow.incident_id == result.incident_id,
            )
        )).scalars().all()
        for ev in evidence:
            if ev.excerpt:
                assert secret_user not in ev.excerpt
                assert secret_pass not in ev.excerpt

        # Execution summary text
        summary = await _get_summary(db_session, _PROJECT, exec_id)
        assert summary is not None
        assert secret_user not in summary.summary_text
        assert secret_pass not in summary.summary_text
