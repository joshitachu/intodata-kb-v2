"""Deterministic insight builder for the runtime store.

Reads raw runtime store rows for one execution, parses log signals via the
parser module, and upserts derived insight rows. All logic is deterministic:
no LLM calls, no business-prose synthesis. Text fields are assembled from
extracted structured values only.

Upserts are dialect-portable (select-then-update/insert) to keep SQLite tests
working alongside Postgres in production.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import RuntimeExecutionRow, RuntimeLogEventRow
from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentEvidenceRow,
    RuntimeIncidentRow,
)
from src.runtime.insights.parser import ParsedRuntimeLog, parse_runtime_log_message, redact_credentials

_SEV_WARNING = "warning"
_SEV_ERROR = "error"
_SEV_INFO = "info"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class InsightBuildResult:
    enriched_events: int
    had_incident: bool
    incident_id: str | None = None
    signature_hash: str | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def build_runtime_insights_for_execution(
    session: AsyncSession,
    *,
    project_id: str,
    execution_id: str,
    source_key: str,
    ingested_at: datetime.datetime,
) -> InsightBuildResult:
    """Build and persist insight rows for one (project_id, execution_id) pair.

    Idempotent: calling twice with the same inputs updates rows rather than
    duplicating them. Returns a result summary for test assertions.
    """
    exec_row = await _fetch_execution(session, project_id, execution_id)
    if exec_row is None:
        return InsightBuildResult(enriched_events=0, had_incident=False)

    log_rows = await _fetch_log_rows(session, project_id, execution_id)

    parsed_list: list[ParsedRuntimeLog] = []
    for log_row in log_rows:
        parsed = parse_runtime_log_message(log_row.message)
        parsed_list.append(parsed)
        await _upsert_enriched_event(session, project_id, log_row, parsed)

    summary = _compute_summary(exec_row, log_rows, parsed_list)
    await _upsert_execution_summary(session, project_id, execution_id, exec_row.job_name, summary)

    incident_id: str | None = None
    sig_hash: str | None = None

    if exec_row.state == "failed":
        incident_id, sig_hash = await _build_incident(
            session,
            project_id=project_id,
            execution_id=execution_id,
            exec_row=exec_row,
            log_rows=log_rows,
            parsed_list=parsed_list,
            summary=summary,
        )

    return InsightBuildResult(
        enriched_events=len(log_rows),
        had_incident=incident_id is not None,
        incident_id=incident_id,
        signature_hash=sig_hash,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _as_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


async def _fetch_execution(
    session: AsyncSession, project_id: str, execution_id: str
) -> RuntimeExecutionRow | None:
    stmt = select(RuntimeExecutionRow).where(
        RuntimeExecutionRow.project_id == project_id,
        RuntimeExecutionRow.execution_id == execution_id,
    )
    return (await session.execute(stmt)).scalars().first()


async def _fetch_log_rows(
    session: AsyncSession, project_id: str, execution_id: str
) -> list[RuntimeLogEventRow]:
    stmt = (
        select(RuntimeLogEventRow)
        .where(
            RuntimeLogEventRow.project_id == project_id,
            RuntimeLogEventRow.execution_id == execution_id,
        )
        .order_by(RuntimeLogEventRow.timestamp.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _upsert_enriched_event(
    session: AsyncSession,
    project_id: str,
    log_row: RuntimeLogEventRow,
    parsed: ParsedRuntimeLog,
) -> None:
    stmt = select(RuntimeEnrichedEventRow).where(
        RuntimeEnrichedEventRow.project_id == project_id,
        RuntimeEnrichedEventRow.log_event_id == log_row.id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        row = RuntimeEnrichedEventRow(
            project_id=project_id,
            log_event_id=log_row.id,
            execution_id=log_row.execution_id,
        )
        session.add(row)
    row.component_name = parsed.component_name
    row.error_code = parsed.error_code
    row.exception_class = parsed.exception_class
    row.retry_attempt = parsed.retry_attempt
    row.retry_max = parsed.retry_max
    row.duration_ms = parsed.duration_ms
    row.timeout_ms = parsed.timeout_ms
    row.endpoint = parsed.endpoint
    row.host = parsed.host
    row.database_name = parsed.database_name
    row.table_name = parsed.table_name
    row.queue_name = parsed.queue_name
    row.message_family = parsed.message_family
    row.confidence = parsed.confidence
    await session.flush()


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _SummaryData:
    warning_count: int
    error_count: int
    first_warning_at: datetime.datetime | None
    first_error_at: datetime.datetime | None
    final_error_at: datetime.datetime | None
    failed_component: str | None
    primary_error_code: str | None
    primary_exception_class: str | None
    first_host: str | None
    first_retry_max: int | None
    summary_text: str
    evidence_refs: list[int]


def _compute_summary(
    exec_row: RuntimeExecutionRow,
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> _SummaryData:
    warning_rows = [r for r in log_rows if r.severity == _SEV_WARNING]
    error_rows = [r for r in log_rows if r.severity == _SEV_ERROR]

    first_warning_at = _as_utc(warning_rows[0].timestamp) if warning_rows else None
    first_error_at = _as_utc(error_rows[0].timestamp) if error_rows else None
    final_error_at = _as_utc(error_rows[-1].timestamp) if error_rows else None

    primary_error_code: str | None = None
    primary_exception_class: str | None = None
    first_host: str | None = None
    first_retry_max: int | None = None
    evidence_refs: list[int] = []

    for log_row, parsed in zip(log_rows, parsed_list):
        if parsed.error_code and primary_error_code is None:
            primary_error_code = parsed.error_code
        if parsed.exception_class and primary_exception_class is None:
            primary_exception_class = parsed.exception_class
        if parsed.host and first_host is None:
            first_host = parsed.host
        if parsed.retry_max is not None and first_retry_max is None:
            first_retry_max = parsed.retry_max
        if parsed.error_code or parsed.exception_class or parsed.retry_attempt is not None:
            evidence_refs.append(log_row.id)

    # Failed component: component from first error-severity row that has one
    # (the originating component, not the parent route that propagates the failure)
    failed_component: str | None = None
    for log_row, parsed in zip(log_rows, parsed_list):
        if log_row.severity == _SEV_ERROR and parsed.component_name:
            failed_component = parsed.component_name
            break

    summary_text = _build_summary_text(
        job_name=exec_row.job_name,
        warning_count=len(warning_rows),
        error_count=len(error_rows),
        failed_component=failed_component,
        primary_error_code=primary_error_code,
        primary_exception_class=primary_exception_class,
    )

    return _SummaryData(
        warning_count=len(warning_rows),
        error_count=len(error_rows),
        first_warning_at=first_warning_at,
        first_error_at=first_error_at,
        final_error_at=final_error_at,
        failed_component=failed_component,
        primary_error_code=primary_error_code,
        primary_exception_class=primary_exception_class,
        first_host=first_host,
        first_retry_max=first_retry_max,
        summary_text=summary_text,
        evidence_refs=evidence_refs[:10],
    )


def _build_summary_text(
    job_name: str,
    warning_count: int,
    error_count: int,
    failed_component: str | None,
    primary_error_code: str | None,
    primary_exception_class: str | None,
) -> str:
    if error_count:
        base = f"Job '{job_name}' failed with {warning_count} warning(s) and {error_count} error(s)"
    elif warning_count:
        base = f"Job '{job_name}' completed with {warning_count} warning(s)"
    else:
        return f"Job '{job_name}' completed cleanly."

    details: list[str] = []
    if failed_component:
        details.append(f"failed component: {failed_component}")
    if primary_error_code:
        details.append(f"error code: {primary_error_code}")
    if primary_exception_class:
        details.append(f"exception: {primary_exception_class}")

    return base + (". " + "; ".join(details) if details else "") + "."


async def _upsert_execution_summary(
    session: AsyncSession,
    project_id: str,
    execution_id: str,
    job_name: str,
    s: _SummaryData,
) -> None:
    stmt = select(RuntimeExecutionSummaryRow).where(
        RuntimeExecutionSummaryRow.project_id == project_id,
        RuntimeExecutionSummaryRow.execution_id == execution_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        row = RuntimeExecutionSummaryRow(project_id=project_id, execution_id=execution_id)
        session.add(row)
    row.job_name = job_name
    row.warning_count = s.warning_count
    row.error_count = s.error_count
    row.first_warning_at = s.first_warning_at
    row.first_error_at = s.first_error_at
    row.final_error_at = s.final_error_at
    row.failed_component = s.failed_component
    row.primary_error_code = s.primary_error_code
    row.primary_exception_class = s.primary_exception_class
    row.summary_text = s.summary_text
    row.evidence_refs = s.evidence_refs
    await session.flush()


# ---------------------------------------------------------------------------
# Incident, signature, and evidence
# ---------------------------------------------------------------------------


async def _build_incident(
    session: AsyncSession,
    *,
    project_id: str,
    execution_id: str,
    exec_row: RuntimeExecutionRow,
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
    summary: _SummaryData,
) -> tuple[str, str]:
    incident_id = hashlib.sha256(f"{project_id}:{execution_id}".encode()).hexdigest()[:32]

    sig_parts = "|".join(
        p
        for p in (
            summary.primary_error_code or "",
            summary.primary_exception_class or "",
            summary.failed_component or "",
        )
        if p
    )
    if not sig_parts:
        # No strong diagnostic signal — include job_name so distinct unknown
        # failures from different jobs do not all collapse into one signature group.
        sig_parts = f"unknown|{exec_row.job_name}"
    signature_hash = hashlib.sha256(sig_parts.encode("utf-8")).hexdigest()

    first_bad_at = summary.first_warning_at or summary.first_error_at

    last_healthy_at: datetime.datetime | None = None
    if first_bad_at is not None:
        for log_row in log_rows:
            ts = _as_utc(log_row.timestamp)
            if ts is not None and ts < first_bad_at and log_row.severity == _SEV_INFO:
                last_healthy_at = ts

    symptom_summary = _build_symptom_summary(
        execution_id=execution_id,
        job_name=exec_row.job_name,
        failed_component=summary.failed_component,
        primary_error_code=summary.primary_error_code,
        primary_exception_class=summary.primary_exception_class,
    )
    root_cause_candidate = _derive_root_cause(
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
        retry_max=summary.first_retry_max,
    )
    confidence = _derive_confidence(
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
    )
    next_checks = _build_next_checks(
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
        host=summary.first_host,
    )

    # Upsert incident row first so _upsert_error_signature can aggregate from it.
    stmt = select(RuntimeIncidentRow).where(
        RuntimeIncidentRow.project_id == project_id,
        RuntimeIncidentRow.execution_id == execution_id,
    )
    inc_row = (await session.execute(stmt)).scalars().first()
    if inc_row is None:
        inc_row = RuntimeIncidentRow(project_id=project_id, execution_id=execution_id)
        session.add(inc_row)
    inc_row.incident_id = incident_id
    inc_row.signature_hash = signature_hash
    inc_row.symptom_summary = symptom_summary
    inc_row.root_cause_candidate = root_cause_candidate
    inc_row.confidence = confidence
    inc_row.first_bad_at = first_bad_at
    inc_row.last_healthy_at = last_healthy_at
    inc_row.next_checks = next_checks
    await session.flush()

    await _upsert_error_signature(
        session,
        project_id=project_id,
        signature_hash=signature_hash,
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
        failed_component=summary.failed_component,
    )

    for log_row, role in _select_evidence_rows(log_rows, parsed_list):
        excerpt = redact_credentials(log_row.message[:300])
        await _upsert_incident_evidence(session, project_id, incident_id, log_row.id, role, excerpt)

    return incident_id, signature_hash


async def _upsert_error_signature(
    session: AsyncSession,
    *,
    project_id: str,
    signature_hash: str,
    error_code: str | None,
    exception_class: str | None,
    failed_component: str | None,
) -> None:
    # Derive occurrence_count, first_seen_at, last_seen_at from distinct incident rows
    # so repeated builds of the same execution do not inflate counts.
    agg_stmt = select(
        func.count(RuntimeIncidentRow.incident_id).label("cnt"),
        func.min(RuntimeIncidentRow.first_bad_at).label("first_seen"),
        func.max(RuntimeIncidentRow.first_bad_at).label("last_seen"),
    ).where(
        RuntimeIncidentRow.project_id == project_id,
        RuntimeIncidentRow.signature_hash == signature_hash,
    )
    agg = (await session.execute(agg_stmt)).first()
    occurrence_count = max(agg.cnt, 1) if agg and agg.cnt else 1
    first_seen_at = _as_utc(agg.first_seen) if agg else None
    last_seen_at = _as_utc(agg.last_seen) if agg else None

    sig_stmt = select(RuntimeErrorSignatureRow).where(
        RuntimeErrorSignatureRow.project_id == project_id,
        RuntimeErrorSignatureRow.signature_hash == signature_hash,
    )
    row = (await session.execute(sig_stmt)).scalars().first()
    if row is None:
        row = RuntimeErrorSignatureRow(project_id=project_id, signature_hash=signature_hash)
        session.add(row)
    row.occurrence_count = occurrence_count
    row.first_seen_at = first_seen_at
    row.last_seen_at = last_seen_at
    row.error_code = error_code
    row.exception_class = exception_class
    row.component_name = failed_component
    summary_parts = [p for p in (error_code, exception_class, failed_component) if p]
    row.summary_text = " / ".join(summary_parts) if summary_parts else None
    await session.flush()


async def _upsert_incident_evidence(
    session: AsyncSession,
    project_id: str,
    incident_id: str,
    log_event_id: int,
    evidence_role: str,
    excerpt: str,
) -> None:
    stmt = select(RuntimeIncidentEvidenceRow).where(
        RuntimeIncidentEvidenceRow.project_id == project_id,
        RuntimeIncidentEvidenceRow.incident_id == incident_id,
        RuntimeIncidentEvidenceRow.log_event_id == log_event_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        row = RuntimeIncidentEvidenceRow(
            project_id=project_id,
            incident_id=incident_id,
            log_event_id=log_event_id,
        )
        session.add(row)
    row.evidence_role = evidence_role
    row.excerpt = excerpt
    await session.flush()


# ---------------------------------------------------------------------------
# Evidence selection
# ---------------------------------------------------------------------------


def _select_evidence_rows(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> list[tuple[RuntimeLogEventRow, str]]:
    """Return at most 5 (row, role) pairs covering the strongest diagnostic signals."""
    seen: set[int] = set()
    result: list[tuple[RuntimeLogEventRow, str]] = []

    def _add(row: RuntimeLogEventRow, role: str) -> None:
        if row.id not in seen:
            seen.add(row.id)
            result.append((row, role))

    for row in log_rows:
        if row.severity == _SEV_WARNING:
            _add(row, "first_warning")
            break

    for row in log_rows:
        if row.severity == _SEV_ERROR:
            _add(row, "first_error")
            break

    for row, parsed in zip(log_rows, parsed_list):
        if parsed.exception_class:
            _add(row, "exception_line")
            break

    for row, parsed in zip(log_rows, parsed_list):
        if parsed.error_code:
            _add(row, "error_code_line")
            break

    for row in log_rows:
        msg_lower = row.message.lower()
        if "rollback" in msg_lower or "dlq" in msg_lower:
            _add(row, "rollback_line")
            break

    for row in reversed(log_rows):
        if row.severity == _SEV_ERROR:
            _add(row, "final_failure")
            break

    return result[:5]


# ---------------------------------------------------------------------------
# Deterministic text builders
# ---------------------------------------------------------------------------


def _build_symptom_summary(
    execution_id: str,
    job_name: str,
    failed_component: str | None,
    primary_error_code: str | None,
    primary_exception_class: str | None,
) -> str:
    parts = [f"Execution '{execution_id}' of job '{job_name}' failed"]
    if failed_component:
        parts[0] += f" in component {failed_component}"
    if primary_error_code:
        parts.append(primary_error_code)
    if primary_exception_class:
        parts.append(f"({primary_exception_class})")
    return " ".join(parts) + "."


def _derive_root_cause(
    error_code: str | None,
    exception_class: str | None,
    retry_max: int | None,
) -> str:
    if error_code and error_code.startswith("ORA-12"):
        attempts_text = f" after {retry_max} attempts" if retry_max else ""
        return (
            f"Source database connection timeout{attempts_text} ({error_code}): "
            "network path to database host may be unreachable or Oracle listener unavailable."
        )
    if error_code:
        return f"Execution terminated with database error {error_code}."
    if exception_class:
        return f"Execution terminated with exception {exception_class}."
    return "Execution terminated with errors; review log events for details."


def _derive_confidence(error_code: str | None, exception_class: str | None) -> str:
    strong = sum(1 for v in (error_code, exception_class) if v is not None)
    if strong >= 2:
        return "high"
    if strong == 1:
        return "medium"
    return "low"


def _build_next_checks(
    error_code: str | None,
    exception_class: str | None,
    host: str | None,
) -> list[str]:
    checks: list[str] = []
    if error_code and error_code.startswith("ORA-12"):
        checks.extend([
            "Check network connectivity to database host",
            "Verify Oracle listener status and port availability",
            "Review connection timeout configuration",
        ])
        if host:
            checks.append(f"Confirm database host '{host}' is reachable from application host")
    elif error_code:
        checks.append(f"Investigate error code {error_code}")
    if exception_class and "Recoverable" in exception_class:
        checks.append("Retry execution to determine if failure is transient")
    if not checks:
        checks.append("Review execution log for error details")
    return checks
