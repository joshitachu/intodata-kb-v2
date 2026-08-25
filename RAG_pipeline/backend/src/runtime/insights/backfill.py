"""Re-derive stored runtime insights from raw log rows.

The insight tables (``runtime_enriched_events``, ``runtime_execution_summaries``,
``runtime_error_signatures``, ``runtime_incidents``) are DERIVED: the raw
``runtime_log_events`` / ``runtime_executions`` rows are the source of truth. When
extraction improves — a new log dialect, a new failure pattern, a newly persisted
field — every previously-derived row is stale and still reflects the old
algorithm. Nothing surfaces that staleness, so an extraction fix silently applies
to new syncs only and the historical corpus keeps showing the old, weaker output.

This module re-runs the deterministic build path over stored executions.

A REDACTION fix is one of those improvements, and the only one where staleness is
a confidentiality problem rather than a quality one. When entity-shape redaction
was added to the parser (``redact_endpoint`` / ``redact_host`` /
``redact_log_text``), the stored corpus already held 10,406
``runtime_enriched_events.endpoint`` rows and 10,303 ``.host`` rows carrying
customer object-store buckets, internal FQDNs and single-label internal
hostnames verbatim. Two of those columns have NO reader anywhere in the codebase,
so the read-path redaction in ``repository.py`` cannot reach them: rebuilding is
the only thing that cleans them. Running this backfill is therefore an
operational step of that fix, not an optional tidy-up::

    python -m src.runtime.insights.backfill --dry-run
    python -m src.runtime.insights.backfill

Two things to know before running it for that reason:

  * It rewrites ``signature_hash`` for any incident on the unknown path whose
    digest input changed. Measured on the reference corpus that was 1 incident of
    827, and a full rebuild rehashes every incident consistently, but stale
    ``runtime_error_signatures`` rows under the OLD hashes are not deleted by
    this module.
  * It is the only fix for a host already interpolated into catalog prose
    (``Confirm host 'SRVA0673' resolves ...``): once the ``{host}`` slot is
    filled, a single-label hostname is structurally indistinguishable from any
    other word and no read-path filter can recover it. 3 incidents of 827.

Design constraints:
  * Reuses ``build_runtime_insights_for_execution`` — the algorithm lives in one
    place, so a backfill can never drift from live ingest.
  * Batched commits: one transaction per batch, not one for the whole corpus, so
    a large project neither holds a long write lock nor loses everything on
    failure.
  * Resumable: work is ordered deterministically and already-current rows are
    simply rewritten with identical values (the build path is idempotent), so a
    re-run after interruption converges.
  * ``dry_run`` computes and reports coverage without persisting anything.

Usage::

    python -m src.runtime.insights.backfill --dry-run
    python -m src.runtime.insights.backfill --project werkorder
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeLogEventRow
from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentRow,
)
from src.runtime.insights.builder import (
    DIALECT_CONTEXT,
    associate_context_signals,
    build_runtime_insights_for_execution,
    fetch_execution_log_rows,
    fetch_known_components,
)
from src.runtime.insights.parser import parse_runtime_log_message

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 100


@dataclasses.dataclass
class ExtractionCoverage:
    """Per-LOG-LINE fill rates. Denominator: ``total_rows`` log lines.

    This measures raw material — how much of the corpus the parser can read. It is
    NOT what a user sees; a single execution may contribute thousands of lines. For
    the user-facing figure see :class:`PersistedCausalCoverage`, which counts
    derived rows. The two must never be compared: they have different denominators
    and different units, and doing so previously made an improvement look like a
    total regression.
    """

    total_rows: int = 0
    dialect: int = 0
    component_name: int = 0
    job_name: int = 0
    error_code: int = 0
    exception_class: int = 0
    field_violation_kind: int = 0
    message_family: int = 0
    # Log lines whose parse yields a causal sentence. None means "not measurable
    # from this source": the stored enriched rows have no causal_message column
    # (it is persisted on the derived summary/incident/signature rows instead), so
    # reporting 0 there would assert an absence that was never measured.
    causal_line_rows: int | None = None
    # Lines that carried no signal of their own and inherited a neighbour's
    # diagnosis. A subset of causal_line_rows, surfaced separately because a
    # borrowed sentence is weaker evidence than an extracted one.
    context_adopted_rows: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return dataclasses.asdict(self)

    def render(self, label: str) -> str:
        total = self.total_rows or 1
        fields = [f for f in dataclasses.fields(self) if f.name != "total_rows"]
        lines = [f"{label} — per log line (n={self.total_rows}):"]
        for field in fields:
            value = getattr(self, field.name)
            if value is None:
                lines.append(f"  {field.name:<22} {'n/a':>7}  (not stored per line)")
                continue
            lines.append(f"  {field.name:<22} {value:>7}  {100.0 * value / total:5.1f}%")
        return "\n".join(lines)


@dataclasses.dataclass
class PersistedCausalCoverage:
    """How many DERIVED rows carry the runtime's own diagnostic sentence.

    Denominator: derived rows (executions / incidents / signatures), not log
    lines. This is the number that reflects what a user actually reads, and it is
    bounded by how many executions have any logs at all — not by parser quality.
    """

    summaries_total: int = 0
    summaries_with_causal: int = 0
    incidents_total: int = 0
    incidents_with_causal: int = 0
    signatures_total: int = 0
    signatures_with_causal: int = 0

    def as_dict(self) -> dict[str, int]:
        return dataclasses.asdict(self)

    def render(self, label: str) -> str:
        pairs = (
            ("execution summaries", self.summaries_with_causal, self.summaries_total),
            ("incidents", self.incidents_with_causal, self.incidents_total),
            ("error signatures", self.signatures_with_causal, self.signatures_total),
        )
        lines = [f"{label} — causal sentence on derived rows:"]
        for name, filled, total in pairs:
            pct = 100.0 * filled / total if total else 0.0
            lines.append(f"  {name:<22} {filled:>7} / {total:<7} {pct:5.1f}%")
        return "\n".join(lines)


@dataclasses.dataclass
class BackfillReport:
    project_id: str
    executions: int = 0
    log_rows: int = 0
    incidents_before: int = 0
    dry_run: bool = False
    before: ExtractionCoverage = dataclasses.field(default_factory=ExtractionCoverage)
    projected: ExtractionCoverage = dataclasses.field(default_factory=ExtractionCoverage)
    # Only measurable from persisted state, so it is reported for the current
    # database contents. On a dry run this is the BEFORE picture by definition:
    # nothing was written, so there is no projected value to state.
    persisted_causal: PersistedCausalCoverage = dataclasses.field(
        default_factory=PersistedCausalCoverage
    )


async def measure_stored_coverage(
    session: AsyncSession, project_id: str
) -> ExtractionCoverage:
    """Read current fill rates straight from the stored enriched rows."""
    stmt = select(
        func.count(),
        func.count(RuntimeEnrichedEventRow.dialect),
        func.count(RuntimeEnrichedEventRow.component_name),
        func.count(RuntimeEnrichedEventRow.job_name),
        func.count(RuntimeEnrichedEventRow.error_code),
        func.count(RuntimeEnrichedEventRow.exception_class),
        func.count(RuntimeEnrichedEventRow.field_violation_kind),
        func.count(RuntimeEnrichedEventRow.message_family),
    ).where(RuntimeEnrichedEventRow.project_id == project_id)
    row = (await session.execute(stmt)).one()
    context_rows = (
        await session.execute(
            select(func.count())
            .select_from(RuntimeEnrichedEventRow)
            .where(
                RuntimeEnrichedEventRow.project_id == project_id,
                RuntimeEnrichedEventRow.dialect == DIALECT_CONTEXT,
            )
        )
    ).scalar_one()
    return ExtractionCoverage(
        total_rows=row[0],
        dialect=row[1],
        component_name=row[2],
        job_name=row[3],
        error_code=row[4],
        exception_class=row[5],
        field_violation_kind=row[6],
        message_family=row[7],
        # Left None deliberately: causal sentences are persisted on the derived
        # summary/incident/signature rows, so this source cannot measure the
        # per-line figure. See PersistedCausalCoverage for the real number.
        causal_line_rows=None,
        context_adopted_rows=context_rows,
    )


async def measure_persisted_causal(
    session: AsyncSession, project_id: str
) -> PersistedCausalCoverage:
    """Count derived rows carrying a causal sentence, with derived denominators."""

    async def counts(model, column) -> tuple[int, int]:
        stmt = select(func.count(), func.count(column)).where(model.project_id == project_id)
        row = (await session.execute(stmt)).one()
        return row[0], row[1]

    summaries_total, summaries_filled = await counts(
        RuntimeExecutionSummaryRow, RuntimeExecutionSummaryRow.causal_message
    )
    incidents_total, incidents_filled = await counts(
        RuntimeIncidentRow, RuntimeIncidentRow.causal_message
    )
    signatures_total, signatures_filled = await counts(
        RuntimeErrorSignatureRow, RuntimeErrorSignatureRow.causal_message
    )
    return PersistedCausalCoverage(
        summaries_total=summaries_total,
        summaries_with_causal=summaries_filled,
        incidents_total=incidents_total,
        incidents_with_causal=incidents_filled,
        signatures_total=signatures_total,
        signatures_with_causal=signatures_filled,
    )


async def project_coverage(
    session: AsyncSession, project_id: str, *, batch_size: int = _DEFAULT_BATCH_SIZE
) -> ExtractionCoverage:
    """Parse stored log rows with the CURRENT parser without writing anything.

    This is what ``--dry-run`` reports: the fill rate a backfill would produce,
    measured by running the real parser over the real corpus.

    Rows are streamed grouped by execution, in the same order the live build path
    reads them, because context association is an execution-scoped pass. Scanning
    in flat id order would split executions across batches and under-report
    association, making the dry run predict something the real run would not do.
    """
    known = await fetch_known_components(session, project_id)
    coverage = ExtractionCoverage(causal_line_rows=0, context_adopted_rows=0)

    # Only executions that actually have log rows are worth visiting: in the
    # reference corpus that is 45 of 3,767, so listing them first avoids thousands
    # of empty fetches.
    execution_ids = (
        await session.execute(
            select(RuntimeLogEventRow.execution_id)
            .where(RuntimeLogEventRow.project_id == project_id)
            .distinct()
            .order_by(RuntimeLogEventRow.execution_id.asc())
        )
    ).scalars().all()

    for execution_id in execution_ids:
        # Same fetch the live build path uses, so the projection cannot disagree
        # with the real run about ordering — which context association depends on.
        rows = await fetch_execution_log_rows(session, project_id, execution_id)
        parsed_list = [parse_runtime_log_message(row.message, known) for row in rows]
        coverage.context_adopted_rows += associate_context_signals(rows, parsed_list)
        for parsed in parsed_list:
            coverage.total_rows += 1
            coverage.dialect += parsed.dialect is not None
            coverage.component_name += parsed.component_name is not None
            coverage.job_name += parsed.job_name is not None
            coverage.error_code += parsed.error_code is not None
            coverage.exception_class += parsed.exception_class is not None
            coverage.field_violation_kind += parsed.field_violation_kind is not None
            coverage.message_family += parsed.message_family is not None
            coverage.causal_line_rows += parsed.causal_message is not None
    return coverage


async def list_projects(session: AsyncSession) -> list[str]:
    stmt = select(RuntimeExecutionRow.project_id).distinct().order_by(RuntimeExecutionRow.project_id)
    return list((await session.execute(stmt)).scalars().all())


async def backfill_project(
    session: AsyncSession,
    project_id: str,
    *,
    dry_run: bool = False,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> BackfillReport:
    """Rebuild insight rows for one project. Returns a before/projected report."""
    report = BackfillReport(project_id=project_id, dry_run=dry_run)
    report.before = await measure_stored_coverage(session, project_id)
    report.log_rows = report.before.total_rows
    report.incidents_before = (
        await session.execute(
            select(func.count()).select_from(RuntimeIncidentRow).where(
                RuntimeIncidentRow.project_id == project_id
            )
        )
    ).scalar_one()

    report.persisted_causal = await measure_persisted_causal(session, project_id)

    if dry_run:
        report.projected = await project_coverage(session, project_id, batch_size=batch_size)
        report.executions = (
            await session.execute(
                select(func.count()).select_from(RuntimeExecutionRow).where(
                    RuntimeExecutionRow.project_id == project_id
                )
            )
        ).scalar_one()
        return report

    known = await fetch_known_components(session, project_id)
    now = datetime.datetime.now(datetime.UTC)
    last_key: tuple[str, str] | None = None
    while True:
        stmt = (
            select(
                RuntimeExecutionRow.execution_id,
                RuntimeExecutionRow.source_key,
            )
            .where(RuntimeExecutionRow.project_id == project_id)
            .order_by(RuntimeExecutionRow.execution_id.asc())
            .limit(batch_size)
        )
        if last_key is not None:
            stmt = stmt.where(RuntimeExecutionRow.execution_id > last_key[0])
        rows = (await session.execute(stmt)).all()
        if not rows:
            break
        for execution_id, source_key in rows:
            await build_runtime_insights_for_execution(
                session,
                project_id=project_id,
                execution_id=execution_id,
                source_key=source_key,
                ingested_at=now,
                known_components=known,
            )
            report.executions += 1
            last_key = (execution_id, source_key)
        # One commit per batch: bounded lock duration, and an interrupted run
        # leaves completed batches durable rather than discarding everything.
        await session.commit()
        logger.info(
            "runtime insight backfill: project=%s rebuilt=%d", project_id, report.executions
        )

    report.projected = await measure_stored_coverage(session, project_id)
    report.persisted_causal = await measure_persisted_causal(session, project_id)
    return report


async def run_backfill(
    project_ids: list[str] | None = None,
    *,
    dry_run: bool = False,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> list[BackfillReport]:
    """Entry point used by the CLI: opens its own session per invocation."""
    from src.db.session import async_session_factory

    reports: list[BackfillReport] = []
    async with async_session_factory() as session:
        targets = project_ids or await list_projects(session)
        for project_id in targets:
            reports.append(
                await backfill_project(
                    session, project_id, dry_run=dry_run, batch_size=batch_size
                )
            )
    return reports


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-derive runtime insight rows from stored raw log events.",
    )
    parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        help="Project id to backfill; repeatable. Omit for every stored project.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the coverage a backfill would produce, without writing.",
    )
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH_SIZE)
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_arg_parser().parse_args()
    reports = asyncio.run(
        run_backfill(args.projects, dry_run=args.dry_run, batch_size=args.batch_size)
    )
    for report in reports:
        mode = "DRY RUN" if report.dry_run else "APPLIED"
        print(f"\n=== {report.project_id} [{mode}] ===")
        print(f"executions={report.executions} log_rows={report.log_rows} "
              f"incidents_before={report.incidents_before}")
        print(report.before.render("stored"))
        print(report.projected.render("projected" if report.dry_run else "after"))
        # Printed as its own block with its own denominators. These counts are
        # per derived row; the block above is per log line. Comparing a number
        # across the two blocks is meaningless — that conflation is exactly what
        # made a real improvement read as a regression.
        causal_label = (
            "current (dry run wrote nothing)" if report.dry_run else "after"
        )
        print(report.persisted_causal.render(causal_label))


if __name__ == "__main__":
    main()
