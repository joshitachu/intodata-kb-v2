"""Pins the bounding of the diagnostic-coverage aggregate.

`GET /runtime/job-insights` and the documentation evidence read both answer
"of this job's failures, how many were logged, diagnosed, or truncated". Both
used to answer it by pre-aggregating the whole log-event table and joining the
result to the -- already tiny -- execution population:

    LEFT JOIN (SELECT project_id, execution_id, count(id)
               FROM runtime_log_events
               WHERE project_id = :p           -- and NOT even that in the
               GROUP BY project_id, execution_id) AS log_counts   -- doc read

Measured on the live alitalend mirror: 3,763,044 log rows grouped to answer
for 98 failed executions, 1,430 ms of a 1,539 ms request, and the source of
several GB of temp-file spill. `limit=1` cost the same as `limit=500`, and a
24-hour window cost the same as all time -- the window reached the outer
execution filter but could not reach inside a subquery that never mentioned
the population.

The tests below pin the four properties that make that shape unreachable:

* the statement count does not grow with the page;
* the evidence tables are reached ONLY through the bounded population, and by
  index, never by a scan;
* narrowing the window really does shrink the work, measured against the
  defect's own witness rather than a clock;
* the numbers are unchanged, including every edge the old outer joins
  encoded (no logs at all, at the capture ceiling, diagnosed-but-unhelpful).
"""

import datetime
import re
from datetime import UTC

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import sqlite as sqlite_dialect

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeLogEventRow
from src.db.models.runtime_insights import RuntimeIncidentRow
from src.runtime.models import (
    ListRuntimeJobReliabilityInput,
    RuntimeExecutableKind,
    RuntimeExecution,
    RuntimeExecutionState,
)
from src.runtime.store import RuntimeStoreRepository
from src.runtime.store.repository import _LOG_CAPTURE_CEILING_LINES

_P = "proj-coverage"
_OTHER = "proj-neighbour"
_SRC = "tmc"
_NOW = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
_DAY_AGO = _NOW - datetime.timedelta(hours=12)
_LONG_AGO = _NOW - datetime.timedelta(days=40)
_WINDOW_24H = _NOW - datetime.timedelta(days=1)
_ALL_TIME = _NOW - datetime.timedelta(days=3650)


async def _execution(
    repo: RuntimeStoreRepository,
    *,
    project_id: str = _P,
    execution_id: str,
    job_name: str,
    state: str = "failed",
    started_at: datetime.datetime,
    executable_kind: RuntimeExecutableKind = RuntimeExecutableKind.JOB,
) -> None:
    await repo.upsert_execution(
        project_id,
        RuntimeExecution(
            execution_id=execution_id,
            job_name=job_name,
            state=RuntimeExecutionState(state),
            started_at=started_at,
            ended_at=started_at + datetime.timedelta(seconds=1),
            duration_ms=1000,
            executable_kind=executable_kind,
        ),
        _SRC,
        _NOW,
    )


def _log_rows(
    *, project_id: str, execution_id: str, count: int, at: datetime.datetime
) -> list[RuntimeLogEventRow]:
    return [
        RuntimeLogEventRow(
            project_id=project_id,
            execution_id=execution_id,
            timestamp=at,
            severity="error",
            message=f"line {ordinal}",
            line_ordinal=ordinal,
            event_fingerprint=f"{ordinal:064d}",
            source_key=_SRC,
            ingested_at=_NOW,
        )
        for ordinal in range(count)
    ]


def _incident(
    *,
    project_id: str = _P,
    execution_id: str,
    pattern_id: str,
    confidence: str,
) -> RuntimeIncidentRow:
    return RuntimeIncidentRow(
        project_id=project_id,
        incident_id=f"inc-{project_id}-{execution_id}",
        execution_id=execution_id,
        signature_hash="d" * 64,
        symptom_summary="job failed",
        confidence=confidence,
        pattern_id=pattern_id,
        cause_origin="deterministic",
        first_bad_at=_DAY_AGO,
    )


@pytest.fixture
async def seeded(db_session):
    """A population deliberately smaller than the evidence around it.

    In-window failures (12 h ago):
      recent_logged      3 log lines, diagnosed usefully
      recent_ceiling     at the capture ceiling, no incident
      recent_silent      no log lines at all, no incident
      recent_unhelpful   3 log lines, incident present but generic
    Out-of-window failure (40 days ago):
      old_logged         5 log lines -- must vanish from a 24 h answer
    Never in the population, but carrying most of the log volume:
      success_noisy      a SUCCESS execution with 40 log lines
      other_project      another tenant's failure with 30 log lines
    """
    store = RuntimeStoreRepository(db_session)
    await _execution(store, execution_id="recent_logged", job_name="job-a", started_at=_DAY_AGO)
    await _execution(store, execution_id="recent_ceiling", job_name="job-a", started_at=_DAY_AGO)
    await _execution(store, execution_id="recent_silent", job_name="job-a", started_at=_DAY_AGO)
    await _execution(store, execution_id="recent_unhelpful", job_name="job-b", started_at=_DAY_AGO)
    await _execution(store, execution_id="old_logged", job_name="job-a", started_at=_LONG_AGO)
    await _execution(
        store, execution_id="success_noisy", job_name="job-a", state="success", started_at=_DAY_AGO
    )
    await _execution(
        store,
        project_id=_OTHER,
        execution_id="other_project",
        job_name="job-a",
        started_at=_DAY_AGO,
    )

    db_session.add_all(
        [
            *_log_rows(project_id=_P, execution_id="recent_logged", count=3, at=_DAY_AGO),
            *_log_rows(
                project_id=_P,
                execution_id="recent_ceiling",
                count=_LOG_CAPTURE_CEILING_LINES,
                at=_DAY_AGO,
            ),
            *_log_rows(project_id=_P, execution_id="recent_unhelpful", count=3, at=_DAY_AGO),
            *_log_rows(project_id=_P, execution_id="old_logged", count=5, at=_LONG_AGO),
            *_log_rows(project_id=_P, execution_id="success_noisy", count=40, at=_DAY_AGO),
            *_log_rows(project_id=_OTHER, execution_id="other_project", count=30, at=_DAY_AGO),
            _incident(
                execution_id="recent_logged",
                pattern_id="sqlserver.deadlock",
                confidence="medium",
            ),
            _incident(
                execution_id="recent_unhelpful",
                pattern_id="generic.unknown",
                confidence="high",
            ),
            _incident(
                project_id=_OTHER,
                execution_id="other_project",
                pattern_id="sqlserver.deadlock",
                confidence="high",
            ),
        ]
    )
    await db_session.flush()
    return store


def _failed_job_population(since: datetime.datetime | None):
    """The population the bounded aggregate is allowed to touch."""
    stmt = (
        select(
            RuntimeExecutionRow.project_id.label("project_id"),
            RuntimeExecutionRow.execution_id.label("execution_id"),
            RuntimeExecutionRow.job_name.label("job_name"),
        )
        .where(RuntimeExecutionRow.project_id == _P)
        .where(RuntimeExecutionRow.state == RuntimeExecutionState.FAILED.value)
        .where(RuntimeExecutionRow.executable_kind == RuntimeExecutableKind.JOB.value)
    )
    if since is not None:
        stmt = stmt.where(RuntimeExecutionRow.started_at >= since)
    return stmt


def _unbounded_log_pre_aggregation():
    """The defect's own witness: the subquery both reads used to build first.

    Kept verbatim so the volume test measures the real regression rather than a
    paraphrase of it. Its row count is a function of the log table alone, which
    is exactly why the caller's window never changed the cost.
    """
    return (
        select(
            RuntimeLogEventRow.project_id.label("project_id"),
            RuntimeLogEventRow.execution_id.label("execution_id"),
            func.count(RuntimeLogEventRow.id).label("log_lines"),
        )
        .group_by(RuntimeLogEventRow.project_id, RuntimeLogEventRow.execution_id)
        .subquery()
    )


def _rendered(stmt) -> str:
    return str(
        stmt.compile(
            dialect=sqlite_dialect.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class TestStatementCountDoesNotScaleWithThePage:
    async def test_a_full_page_costs_the_same_statements_as_a_single_row(
        self, db_session, seeded, statements
    ):
        with statements() as one:
            single = await seeded.list_job_operational_insights(
                [_P], project_id=_P, since=None, until=None, limit=1
            )
        with statements() as many:
            page = await seeded.list_job_operational_insights(
                [_P], project_id=_P, since=None, until=None, limit=500
            )

        assert len(single) == 1
        assert len(page) == 2
        assert one.count == many.count, (
            "coverage must be resolved for the whole page at once: "
            f"1 row took {one.count} statements, the full page took {many.count}"
        )
        # Reliability, coverage, incidents. An inequality so a fourth relation
        # is a deliberate edit rather than a silent slide back to per-job
        # follow-up queries.
        assert many.count <= 4

    async def test_the_page_reads_the_log_event_table_in_one_statement(
        self, db_session, seeded, statements
    ):
        with statements() as recorded:
            await seeded.list_job_operational_insights(
                [_P], project_id=_P, since=None, until=None, limit=500
            )

        assert len(recorded.mentioning("runtime_log_events")) == 1


class TestTheEvidenceTablesAreReachedOnlyThroughThePopulation:
    """The structural property that makes the old shape unreachable."""

    @pytest.mark.parametrize("group_names", [("source_key", "job_name"), ("job_name",)])
    def test_the_log_event_table_is_never_pre_aggregated_on_its_own(self, group_names):
        population = _failed_job_population(_WINDOW_24H)
        if "source_key" in group_names:
            population = population.add_columns(
                RuntimeExecutionRow.source_key.label("source_key")
            )
        sql = _rendered(
            RuntimeStoreRepository._evidence_coverage_statement(population, group_names)
        )

        # The defect's fingerprint: a GROUP BY whose grouping columns come from
        # the log table rather than from the population.
        assert not re.search(r"GROUP BY\s+runtime_log_events\.", sql), sql
        # Every reference to the fact tables must sit next to a predicate that
        # ties it to one population row.
        for table in ("runtime_log_events", "runtime_incidents"):
            assert f"{table}.execution_id = evidence_population.execution_id" in sql, sql
            assert f"{table}.project_id = evidence_population.project_id" in sql, sql

    async def test_the_log_probe_searches_the_index_and_never_scans_the_table(
        self, db_session, seeded
    ):
        """An access-path assertion, so it cannot go stale with row counts.

        `uq_runtime_log_events_project_exec_ordinal_fp` already serves this
        lookup -- the defect was volume, not a missing index, and no index was
        added for it. Verified against Postgres on the live mirror too, where
        the plan changed from `Seq Scan on runtime_log_events rows=4218200` to
        `Index Scan using uq_runtime_log_events_project_exec_ordinal_fp`.
        """
        stmt = RuntimeStoreRepository._evidence_coverage_statement(
            _failed_job_population(_WINDOW_24H), ("job_name",)
        )
        plan = "\n".join(
            str(row[3])
            for row in (
                await db_session.execute(text("EXPLAIN QUERY PLAN " + _rendered(stmt)))
            ).all()
        )

        log_steps = [line for line in plan.splitlines() if "runtime_log_events" in line]
        assert log_steps, plan
        for line in log_steps:
            assert "SEARCH" in line and "USING" in line and "INDEX" in line, plan
            assert "SCAN runtime_log_events" not in line, plan


class TestNarrowingTheWindowShrinksTheWork:
    async def test_the_old_pre_aggregation_was_window_independent(self, db_session, seeded):
        """Characterization, not a guard -- it measures the tables, not the code.

        It exists because "the window does not reach the expensive part" is a
        claim about cardinality, and this is where that claim is written down
        and checked. The GUARDS that would fail on a relapse are the structural
        and access-path pair above (both verified to fail against the previous
        implementation) plus the statement-count pair; a row count on its own
        cannot detect a code regression.
        """
        narrow = (
            await db_session.execute(
                select(func.count()).select_from(_failed_job_population(_WINDOW_24H).subquery())
            )
        ).scalar_one()
        everything = (
            await db_session.execute(
                select(func.count()).select_from(_failed_job_population(None).subquery())
            )
        ).scalar_one()

        assert narrow == 4
        assert everything == 5
        assert narrow < everything, "the window must narrow the population it probes"

        # And the witness: the subquery the reads used to build first is a
        # function of the log table alone. Same row count either way -- which
        # is why 24 h, 90 d and no window all cost 1.5 s before this change.
        pre_aggregated = (
            await db_session.execute(
                select(func.count()).select_from(_unbounded_log_pre_aggregation())
            )
        ).scalar_one()
        assert pre_aggregated == 6, (
            "the old shape grouped every execution that has ANY log line, in "
            "every project, regardless of state, job or window"
        )
        assert pre_aggregated > everything

    async def test_a_narrow_window_reports_only_in_window_evidence(self, db_session, seeded):
        narrow = {
            (row.job_name): (row.failed, row.logged, row.diagnosed, row.ceiling)
            for row in (
                await db_session.execute(
                    RuntimeStoreRepository._evidence_coverage_statement(
                        _failed_job_population(_WINDOW_24H), ("job_name",)
                    )
                )
            ).all()
        }
        everything = {
            (row.job_name): (row.failed, row.logged, row.diagnosed, row.ceiling)
            for row in (
                await db_session.execute(
                    RuntimeStoreRepository._evidence_coverage_statement(
                        _failed_job_population(None), ("job_name",)
                    )
                )
            ).all()
        }

        # job-a in 24 h: recent_logged, recent_ceiling, recent_silent.
        # Two carry logs, one of those is at the ceiling, one is diagnosed.
        assert narrow["job-a"] == (3, 2, 1, 1)
        # All time adds `old_logged`, which has logs and no diagnosis.
        assert everything["job-a"] == (4, 3, 1, 1)
        # job-b's only incident is generic, so it is logged but not diagnosed.
        assert narrow["job-b"] == (1, 1, 0, 0)
        assert everything["job-b"] == narrow["job-b"]


class TestTheNumbersAreUnchanged:
    """Every edge the old outer joins encoded, pinned on the bounded form."""

    async def test_coverage_matches_a_python_reference(self, db_session, seeded):
        rows = (
            await db_session.execute(
                RuntimeStoreRepository._evidence_coverage_statement(
                    _failed_job_population(None), ("job_name",)
                )
            )
        ).all()

        # Independent reference: per failed job execution, count its own log
        # rows and ask whether it has a non-generic, confident incident.
        expected: dict[str, list[int]] = {}
        population = (
            await db_session.execute(_failed_job_population(None))
        ).all()
        for execution in population:
            lines = (
                await db_session.execute(
                    select(func.count())
                    .select_from(RuntimeLogEventRow)
                    .where(RuntimeLogEventRow.project_id == execution.project_id)
                    .where(RuntimeLogEventRow.execution_id == execution.execution_id)
                )
            ).scalar_one()
            diagnosed = (
                await db_session.execute(
                    select(func.count())
                    .select_from(RuntimeIncidentRow)
                    .where(RuntimeIncidentRow.project_id == execution.project_id)
                    .where(RuntimeIncidentRow.execution_id == execution.execution_id)
                    .where(RuntimeIncidentRow.confidence.in_(("high", "medium")))
                    .where(
                        RuntimeIncidentRow.pattern_id.notin_(
                            ("generic.unknown", "exception.generic")
                        )
                    )
                )
            ).scalar_one()
            bucket = expected.setdefault(execution.job_name, [0, 0, 0, 0])
            bucket[0] += 1
            bucket[1] += 1 if lines > 0 else 0
            bucket[2] += 1 if diagnosed > 0 else 0
            bucket[3] += 1 if lines >= _LOG_CAPTURE_CEILING_LINES else 0

        assert {
            row.job_name: [row.failed, row.logged, row.diagnosed, row.ceiling] for row in rows
        } == expected

    async def test_an_execution_with_no_log_lines_is_counted_as_a_failure_only(
        self, db_session, seeded
    ):
        """`recent_silent` is why the old form used a LEFT JOIN.

        Its absence from the log table must reduce `logged`, never `failed`.
        """
        row = (
            await db_session.execute(
                RuntimeStoreRepository._evidence_coverage_statement(
                    _failed_job_population(_WINDOW_24H).where(
                        RuntimeExecutionRow.execution_id == "recent_silent"
                    ),
                    ("job_name",),
                )
            )
        ).one()
        assert (row.failed, row.logged, row.diagnosed, row.ceiling) == (1, 0, 0, 0)

    async def test_the_capture_ceiling_is_reported_from_a_capped_probe(
        self, db_session, seeded
    ):
        """The probe stops reading at the ceiling, so the flag must still be exact.

        Two executions bracket the boundary: one at the ceiling exactly, one
        three lines short.
        """
        rows = {
            row.job_name: row
            for row in (
                await db_session.execute(
                    RuntimeStoreRepository._evidence_coverage_statement(
                        _failed_job_population(_WINDOW_24H).where(
                            RuntimeExecutionRow.execution_id.in_(
                                ("recent_ceiling", "recent_logged")
                            )
                        ),
                        ("job_name",),
                    )
                )
            ).all()
        }
        assert (rows["job-a"].logged, rows["job-a"].ceiling) == (2, 1)

    async def test_a_generic_diagnosis_does_not_count_as_diagnosed(self, db_session, seeded):
        row = (
            await db_session.execute(
                RuntimeStoreRepository._evidence_coverage_statement(
                    _failed_job_population(_WINDOW_24H).where(
                        RuntimeExecutionRow.execution_id == "recent_unhelpful"
                    ),
                    ("job_name",),
                )
            )
        ).one()
        assert (row.failed, row.logged, row.diagnosed) == (1, 1, 0)

    async def test_a_neighbouring_tenants_evidence_never_leaks_in(self, db_session, seeded):
        """`other_project` has the same job name, logs and a useful incident."""
        rows = (
            await db_session.execute(
                RuntimeStoreRepository._evidence_coverage_statement(
                    _failed_job_population(_WINDOW_24H), ("job_name",)
                )
            )
        ).all()
        assert {row.job_name for row in rows} == {"job-a", "job-b"}
        by_job = {row.job_name: row for row in rows}
        # 3 in-window failures, not 4: the neighbour's is not in the population.
        assert by_job["job-a"].failed == 3

    async def test_the_endpoint_level_answer_is_unchanged(self, db_session, seeded):
        insights = await seeded.list_job_operational_insights(
            [_P], project_id=_P, since=_WINDOW_24H, until=None, limit=500
        )
        by_job = {row.job_name: row for row in insights}

        assert by_job["job-a"].failed_runs == 3
        assert by_job["job-a"].failures_with_logs == 2
        assert by_job["job-a"].diagnosed_failures == 1
        assert by_job["job-a"].capture_ceiling_reached == 1
        assert by_job["job-a"].evidence_completeness == "partial"
        # job-b's incident is generic, so no diagnosis is published for it.
        assert by_job["job-b"].diagnosed_failures == 0
        assert by_job["job-b"].evidence_completeness == "logs_only"
        assert by_job["job-b"].pattern_id is None

    async def test_reliability_and_insights_agree_on_the_job_population(
        self, db_session, seeded
    ):
        """The two reads share one population, so their pages cannot drift."""
        reliability = await seeded.list_job_reliability(
            [_P],
            ListRuntimeJobReliabilityInput(project_id=_P, since=_WINDOW_24H, limit=500),
        )
        insights = await seeded.list_job_operational_insights(
            [_P], project_id=_P, since=_WINDOW_24H, until=None, limit=500
        )
        assert [(row.source_key, row.job_name) for row in reliability] == [
            (row.source_key, row.job_name) for row in insights
        ]


class TestTheDocumentationReadIsBoundedTheSameWay:
    async def test_the_doc_read_reaches_the_log_table_once_and_by_index(
        self, db_session, seeded, statements
    ):
        with statements() as recorded:
            quality = await seeded._doc_evidence_log_quality(
                [_P], [_P], ["job-a", "job-b"], _WINDOW_24H
            )

        assert len(recorded.mentioning("runtime_log_events")) == 1
        assert not re.search(
            r"GROUP BY\s+runtime_log_events\.", recorded.mentioning("runtime_log_events")[0]
        )
        assert quality["job-a"].failed_executions == 3
        assert quality["job-a"].executions_with_logs == 2
        assert quality["job-a"].diagnosed_executions == 1
        assert quality["job-a"].capture_ceiling_reached == 1

    async def test_the_doc_read_window_reaches_the_aggregate(self, db_session, seeded):
        narrow = await seeded._doc_evidence_log_quality(
            [_P], [_P], ["job-a", "job-b"], _WINDOW_24H
        )
        everything = await seeded._doc_evidence_log_quality(
            [_P], [_P], ["job-a", "job-b"], _ALL_TIME
        )

        assert narrow["job-a"].failed_executions == 3
        assert everything["job-a"].failed_executions == 4
        assert narrow["job-a"].executions_with_logs == 2
        assert everything["job-a"].executions_with_logs == 3
