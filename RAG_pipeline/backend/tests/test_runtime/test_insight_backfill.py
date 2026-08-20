"""Tests for insight re-derivation over stored raw log rows.

The insight tables are derived, so an extraction improvement is inert on history
until they are rebuilt. These tests pin the three properties that make the
rebuild trustworthy: it is idempotent, ``--dry-run`` writes nothing, and the
rebuilt rows carry the newly-extracted fields.
"""

import datetime
import hashlib
from datetime import UTC

from sqlalchemy import select

from src.db.models.runtime_data import (
    RuntimeComponentMetricRow,
    RuntimeExecutionRow,
    RuntimeLogEventRow,
)
from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentRow,
)
from src.runtime.insights.backfill import (
    backfill_project,
    list_projects,
    measure_persisted_causal,
    measure_stored_coverage,
    project_coverage,
)
from src.runtime.insights.builder import build_runtime_insights_for_execution

_PROJECT = "backfill-proj"
_NOW = datetime.datetime(2026, 6, 18, 9, 0, tzinfo=UTC)

# Real Talend/TMC shapes: no bracket prefix, so the pre-fix parser extracted
# nothing from any of them.
_LINES = [
    (10, "info", "Task execution 0827 was started"),
    (
        11,
        "error",
        "tMSSqlOutput_5 Data truncation\n"
        "java.sql.BatchUpdateException: Data truncation\n"
        "\tat net.sourceforge.jtds.jdbc.JtdsStatement.executeBatch(JtdsStatement.java:1069)",
    ),
    (
        12,
        "warning",
        "68a5a9da:17.5/0827: Execution 0827 failed with code 1 and error Job stopped "
        "with errors or unable to run. Exception in component tMSSqlOutput_5 "
        "(Maximo2RHM_Workorders) java.sql.BatchUpdateException: Data truncation",
    ),
]


async def _seed(session, execution_id: str = "bf-exec-1", state: str = "failed") -> None:
    session.add(
        RuntimeExecutionRow(
            project_id=_PROJECT,
            execution_id=execution_id,
            job_name="Maximo2RHM_Workorders",
            project_name=_PROJECT,
            state=state,
            source_key="talend_tmc",
            started_at=_NOW,
            ended_at=_NOW + datetime.timedelta(minutes=1),
            duration_ms=60_000,
            ingested_at=_NOW,
        )
    )
    for offset, severity, message in _LINES:
        ts = _NOW + datetime.timedelta(seconds=offset)
        session.add(
            RuntimeLogEventRow(
                project_id=_PROJECT,
                execution_id=execution_id,
                source_key="talend_tmc",
                timestamp=ts,
                severity=severity,
                message=message,
                line_ordinal=offset,
                event_fingerprint=hashlib.sha256(
                    f"{ts.isoformat()}|{severity}|{message}".encode()
                ).hexdigest()[:64],
                ingested_at=_NOW,
            )
        )
    session.add(
        RuntimeComponentMetricRow(
            project_id=_PROJECT,
            execution_id=execution_id,
            component_key="tMSSqlOutput_5",
            component_type="tMSSqlOutput",
            source_key="talend_tmc",
            rows_total=10,
            ingested_at=_NOW,
        )
    )
    await session.flush()


class TestDryRun:
    async def test_dry_run_writes_nothing(self, db_session):
        await _seed(db_session)
        report = await backfill_project(db_session, _PROJECT, dry_run=True)

        assert report.dry_run is True
        enriched = (
            await db_session.execute(
                select(RuntimeEnrichedEventRow).where(
                    RuntimeEnrichedEventRow.project_id == _PROJECT
                )
            )
        ).scalars().all()
        assert list(enriched) == []

    async def test_dry_run_projects_the_coverage_a_rebuild_would_produce(self, db_session):
        await _seed(db_session)
        projected = await project_coverage(db_session, _PROJECT)

        assert projected.total_rows == len(_LINES)
        # Two of the three lines name a component under the real dialects.
        assert projected.component_name == 2
        assert projected.dialect == 2
        # Per-LINE raw material, not the user-facing figure — the persisted count
        # is measured separately against derived rows.
        assert projected.causal_line_rows == 2
        assert projected.job_name == 1


class TestRebuild:
    async def test_rebuild_populates_the_newly_extracted_fields(self, db_session):
        await _seed(db_session)
        report = await backfill_project(db_session, _PROJECT)

        assert report.executions == 1
        rows = (
            await db_session.execute(
                select(RuntimeEnrichedEventRow)
                .where(RuntimeEnrichedEventRow.project_id == _PROJECT)
                .order_by(RuntimeEnrichedEventRow.log_event_id)
            )
        ).scalars().all()
        assert len(rows) == len(_LINES)
        assert {r.component_name for r in rows} == {None, "tMSSqlOutput_5"}
        assert "tmc_status" in {r.dialect for r in rows}
        assert "Maximo2RHM_Workorders" in {r.job_name for r in rows}

    async def test_rebuild_persists_the_runtimes_own_sentence(self, db_session):
        await _seed(db_session)
        await backfill_project(db_session, _PROJECT)

        summary = (
            await db_session.execute(
                select(RuntimeExecutionSummaryRow).where(
                    RuntimeExecutionSummaryRow.project_id == _PROJECT
                )
            )
        ).scalars().one()
        assert summary.causal_message is not None
        assert "Data truncation" in summary.causal_message

        incident = (
            await db_session.execute(
                select(RuntimeIncidentRow).where(RuntimeIncidentRow.project_id == _PROJECT)
            )
        ).scalars().one()
        assert incident.causal_message is not None
        assert "Data truncation" in incident.causal_message
        # The catalog now recognises the platform, so this is no longer a
        # placeholder cause.
        assert incident.pattern_id == "sqlserver.truncation"
        assert "review log events for details" not in (incident.root_cause_candidate or "")

        signature = (
            await db_session.execute(
                select(RuntimeErrorSignatureRow).where(
                    RuntimeErrorSignatureRow.project_id == _PROJECT
                )
            )
        ).scalars().one()
        assert signature.causal_message is not None

    async def test_rebuild_is_idempotent(self, db_session):
        await _seed(db_session)
        first = await backfill_project(db_session, _PROJECT)
        second = await backfill_project(db_session, _PROJECT)

        assert first.executions == second.executions == 1
        enriched = (
            await db_session.execute(
                select(RuntimeEnrichedEventRow).where(
                    RuntimeEnrichedEventRow.project_id == _PROJECT
                )
            )
        ).scalars().all()
        assert len(enriched) == len(_LINES)
        incidents = (
            await db_session.execute(
                select(RuntimeIncidentRow).where(RuntimeIncidentRow.project_id == _PROJECT)
            )
        ).scalars().all()
        assert len(incidents) == 1

    async def test_rebuild_overwrites_stale_rows_from_a_weaker_extraction(self, db_session):
        """The reason a backfill exists: stored rows reflect the OLD algorithm."""
        await _seed(db_session)
        await build_runtime_insights_for_execution(
            db_session,
            project_id=_PROJECT,
            execution_id="bf-exec-1",
            source_key="talend_tmc",
            ingested_at=_NOW,
        )
        # Simulate a row derived before the dialect work landed.
        stale = (
            await db_session.execute(
                select(RuntimeEnrichedEventRow).where(
                    RuntimeEnrichedEventRow.project_id == _PROJECT
                )
            )
        ).scalars().all()
        for row in stale:
            row.component_name = None
            row.dialect = None
        await db_session.flush()

        await backfill_project(db_session, _PROJECT)
        refreshed = (
            await db_session.execute(
                select(RuntimeEnrichedEventRow).where(
                    RuntimeEnrichedEventRow.project_id == _PROJECT
                )
            )
        ).scalars().all()
        assert any(r.component_name == "tMSSqlOutput_5" for r in refreshed)
        assert any(r.dialect is not None for r in refreshed)


class TestScopingAndReporting:
    async def test_backfill_is_project_scoped(self, db_session):
        await _seed(db_session, execution_id="bf-exec-1")
        other = "other-project"
        db_session.add(
            RuntimeExecutionRow(
                project_id=other,
                execution_id="other-exec",
                job_name="other_job",
                project_name=other,
                state="failed",
                source_key="talend_tmc",
                started_at=_NOW,
                ingested_at=_NOW,
            )
        )
        await db_session.flush()

        report = await backfill_project(db_session, _PROJECT)
        assert report.executions == 1
        assert (
            await db_session.execute(
                select(RuntimeEnrichedEventRow).where(
                    RuntimeEnrichedEventRow.project_id == other
                )
            )
        ).scalars().all() == []

    async def test_list_projects_reports_stored_projects(self, db_session):
        await _seed(db_session)
        assert _PROJECT in await list_projects(db_session)

    async def test_stored_coverage_reflects_persisted_rows(self, db_session):
        await _seed(db_session)
        before = await measure_stored_coverage(db_session, _PROJECT)
        assert before.total_rows == 0

        await backfill_project(db_session, _PROJECT)
        after = await measure_stored_coverage(db_session, _PROJECT)
        assert after.total_rows == len(_LINES)
        assert after.component_name == 2


class TestRedactionOnNewColumns:
    async def test_credentials_never_reach_the_new_causal_columns(self, db_session):
        """causal_message is persisted, so redaction is a storage guarantee here."""
        exec_id = "bf-secret"
        db_session.add(
            RuntimeExecutionRow(
                project_id=_PROJECT,
                execution_id=exec_id,
                job_name="secret_job",
                project_name=_PROJECT,
                state="failed",
                source_key="talend_tmc",
                started_at=_NOW,
                ingested_at=_NOW,
            )
        )
        message = (
            "tMSSqlConnection_1 java.sql.SQLException: login failed for "
            "jdbc:jtds:sqlserver://scott:tiger@dbhost:1433/MAXIMO password=hunter2"
        )
        ts = _NOW + datetime.timedelta(seconds=5)
        db_session.add(
            RuntimeLogEventRow(
                project_id=_PROJECT,
                execution_id=exec_id,
                source_key="talend_tmc",
                timestamp=ts,
                severity="error",
                message=message,
                line_ordinal=1,
                event_fingerprint=hashlib.sha256(message.encode()).hexdigest()[:64],
                ingested_at=_NOW,
            )
        )
        await db_session.flush()

        await backfill_project(db_session, _PROJECT)

        summary = (
            await db_session.execute(
                select(RuntimeExecutionSummaryRow).where(
                    RuntimeExecutionSummaryRow.execution_id == exec_id
                )
            )
        ).scalars().one()
        incident = (
            await db_session.execute(
                select(RuntimeIncidentRow).where(RuntimeIncidentRow.execution_id == exec_id)
            )
        ).scalars().one()
        for value in (
            summary.causal_message,
            incident.causal_message,
            incident.root_cause_candidate,
        ):
            assert value is not None
            assert "tiger" not in value
            assert "hunter2" not in value
            assert "scott" not in value


class TestCoverageMetricsAreNotConflated:
    """The report previously printed one label for two incompatible measurements.

    ``project_coverage`` counted log LINES yielding a causal sentence (werkorder:
    4,661) while ``measure_stored_coverage`` read enriched rows, which have no
    causal_message column, and reported 0. Side by side under one name that read as
    a total regression when the truth was "4,661 lines of raw material; 44
    executions persisted a sentence". The two now have distinct names, distinct
    denominators, and separate report blocks.
    """

    async def test_the_per_line_figure_is_unmeasurable_from_stored_rows(self, db_session):
        """None, not 0: reporting 0 asserts an absence that was never measured."""
        await _seed(db_session)
        await backfill_project(db_session, _PROJECT)

        stored = await measure_stored_coverage(db_session, _PROJECT)

        assert stored.causal_line_rows is None
        assert stored.total_rows > 0

    async def test_the_projection_measures_the_per_line_figure(self, db_session):
        await _seed(db_session)

        projected = await project_coverage(db_session, _PROJECT)

        assert projected.causal_line_rows is not None
        assert projected.causal_line_rows > 0

    async def test_the_persisted_figure_counts_derived_rows_not_log_lines(self, db_session):
        await _seed(db_session)
        await backfill_project(db_session, _PROJECT)

        persisted = await measure_persisted_causal(db_session, _PROJECT)
        stored = await measure_stored_coverage(db_session, _PROJECT)

        # One execution, several log lines: the denominators genuinely differ, which
        # is the whole reason the two must not be compared.
        assert persisted.summaries_total == 1
        assert persisted.summaries_with_causal == 1
        assert stored.total_rows > persisted.summaries_total

    async def test_the_render_blocks_state_their_own_units(self, db_session):
        """A future reader must not be able to mistake one series for the other."""
        await _seed(db_session)
        report = await backfill_project(db_session, _PROJECT)

        per_line = report.projected.render("after")
        derived = report.persisted_causal.render("after")

        assert "per log line" in per_line
        assert "derived rows" in derived
        assert "n/a" in report.before.render("stored")

    async def test_a_dry_run_reports_persisted_state_without_claiming_a_projection(
        self, db_session
    ):
        await _seed(db_session)

        report = await backfill_project(db_session, _PROJECT, dry_run=True)

        # Nothing was written, so the persisted block describes current state only.
        assert report.persisted_causal.summaries_with_causal == 0
        assert report.projected.causal_line_rows is not None


class TestContextAssociationIsProjectedFaithfully:
    """The dry run must predict what the real run does.

    Association is execution-scoped, so a projection that scanned log rows in flat
    id order would split executions across batches and under-report it.
    """

    async def test_projection_and_rebuild_agree_on_adopted_rows(self, db_session):
        await _seed(db_session)

        projected = await project_coverage(db_session, _PROJECT)
        await backfill_project(db_session, _PROJECT)
        stored = await measure_stored_coverage(db_session, _PROJECT)

        assert projected.context_adopted_rows == stored.context_adopted_rows
