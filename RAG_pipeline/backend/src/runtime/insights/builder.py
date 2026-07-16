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
from collections.abc import Callable
from typing import TYPE_CHECKING

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
from src.runtime.insights.catalog import CATALOG
from src.runtime.insights.llm_fallback import maybe_run_llm_fallback
from src.runtime.insights.parser import ParsedRuntimeLog, parse_runtime_log_message, redact_credentials

if TYPE_CHECKING:
    from src.runtime.insights.semantic import RuntimeSemanticIndexer

_SEV_WARNING = "warning"
_SEV_ERROR = "error"
_SEV_INFO = "info"

_SEVERITY_ALIASES = {
    "error": _SEV_ERROR,
    "severe": _SEV_ERROR,
    "fatal": _SEV_ERROR,
    "critical": _SEV_ERROR,
    "warn": _SEV_WARNING,
    "warning": _SEV_WARNING,
}


def normalize_severity(raw: str | None) -> str:
    """Map a raw severity token to a canonical level.

    Case- and alias-insensitive: error/severe/fatal/critical -> "error";
    warn/warning -> "warning"; any other value is lowercased and returned as-is
    (e.g. "INFO" -> "info"). Single source of truth for every severity compare.
    """
    token = (raw or "").strip().lower()
    return _SEVERITY_ALIASES.get(token, token)


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
    semantic_indexer: "RuntimeSemanticIndexer | None" = None,
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

    result = InsightBuildResult(
        enriched_events=len(log_rows),
        had_incident=incident_id is not None,
        incident_id=incident_id,
        signature_hash=sig_hash,
    )
    if semantic_indexer is not None:
        # Optional Qdrant/embedding failures are isolated by the hook; the SQL
        # insight write above remains the authoritative result of this builder.
        from src.runtime.insights.semantic import index_runtime_insights_for_execution

        await index_runtime_insights_for_execution(
            session,
            project_id=project_id,
            execution_id=execution_id,
            signature_hash=sig_hash,
            indexer=semantic_indexer,
        )
    return result


async def rebuild_runtime_insights(
    session: AsyncSession, *, project_id: str | None = None
) -> int:
    """Rebuild all derived insight rows from the raw runtime store.

    The insight tables are derived: the raw runtime store is the source of truth.
    When the deterministic algorithm changes (e.g. the signature formula in this
    slice), stored insight rows become stale. This routine re-runs
    ``build_runtime_insights_for_execution`` for every stored execution (optionally
    scoped to one project) so signatures, confidence, and cause_origin are
    recomputed. Idempotent; returns the number of executions rebuilt.

    Used by Alembic migration 034 to repair signatures after the formula change.
    """
    stmt = select(
        RuntimeExecutionRow.project_id,
        RuntimeExecutionRow.execution_id,
        RuntimeExecutionRow.source_key,
    )
    if project_id is not None:
        stmt = stmt.where(RuntimeExecutionRow.project_id == project_id)
    rows = (await session.execute(stmt)).all()

    rebuilt = 0
    now = datetime.datetime.now(datetime.UTC)
    for row in rows:
        await build_runtime_insights_for_execution(
            session,
            project_id=row.project_id,
            execution_id=row.execution_id,
            source_key=row.source_key,
            ingested_at=now,
        )
        rebuilt += 1
    return rebuilt


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _as_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _select_causal_signal(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
    getter: Callable[[ParsedRuntimeLog], str | None],
) -> str | None:
    """Pick a diagnostic signal from the failure-attributed (terminal causal) event.

    Replaces first-seen selection: a benign value emitted early in the run must
    not shadow the real fault. Preference order, highest first:
      1. The LAST error-severity row that carries the signal (terminal cause).
      2. The LAST warning-severity row that carries the signal.
      3. The LAST row overall that carries the signal.
    Deterministic: within a tier the latest qualifying row wins, which matches the
    chronological-terminal definition of "the failing event".
    """
    by_error: str | None = None
    by_warning: str | None = None
    by_any: str | None = None
    for log_row, parsed in zip(log_rows, parsed_list):
        value = getter(parsed)
        if not value:
            continue
        by_any = value
        sev = normalize_severity(log_row.severity)
        if sev == _SEV_ERROR:
            by_error = value
        elif sev == _SEV_WARNING:
            by_warning = value
    if by_error is not None:
        return by_error
    if by_warning is not None:
        return by_warning
    return by_any


def _select_failed_component(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> str | None:
    """Pick the component of the failure-attributed line.

    The failed component is the originating component, not the parent route that
    propagates the failure. Preference order:
      1. The LAST error-severity row that carries BOTH a component and a
         diagnostic signal (error_code or exception_class) — the line that
         actually reports the fault.
      2. Else the LAST error-severity row that carries a component.
      3. Else the LAST row overall that carries a component.
    """
    diagnostic: str | None = None
    error_component: str | None = None
    any_component: str | None = None
    for log_row, parsed in zip(log_rows, parsed_list):
        if not parsed.component_name:
            continue
        any_component = parsed.component_name
        if normalize_severity(log_row.severity) != _SEV_ERROR:
            continue
        error_component = parsed.component_name
        if parsed.error_code or parsed.exception_class:
            diagnostic = parsed.component_name
    if diagnostic is not None:
        return diagnostic
    if error_component is not None:
        return error_component
    return any_component


def _terminal_error_message(log_rows: list[RuntimeLogEventRow]) -> str | None:
    """Return the message of the last error-severity row (the failure-attributed
    line) for unknown-failure disambiguation. None when no error row exists."""
    for log_row in reversed(log_rows):
        if normalize_severity(log_row.severity) == _SEV_ERROR:
            return log_row.message
    return None


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
    causal_message: str | None
    first_host: str | None
    first_retry_max: int | None
    summary_text: str
    evidence_refs: list[int]


def _compute_summary(
    exec_row: RuntimeExecutionRow,
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> _SummaryData:
    warning_rows = [r for r in log_rows if normalize_severity(r.severity) == _SEV_WARNING]
    error_rows = [r for r in log_rows if normalize_severity(r.severity) == _SEV_ERROR]

    first_warning_at = _as_utc(warning_rows[0].timestamp) if warning_rows else None
    first_error_at = _as_utc(error_rows[0].timestamp) if error_rows else None
    final_error_at = _as_utc(error_rows[-1].timestamp) if error_rows else None

    first_host: str | None = None
    first_retry_max: int | None = None
    evidence_refs: list[int] = []

    for log_row, parsed in zip(log_rows, parsed_list):
        if parsed.host and first_host is None:
            first_host = parsed.host
        if parsed.retry_max is not None and first_retry_max is None:
            first_retry_max = parsed.retry_max
        if parsed.error_code or parsed.exception_class or parsed.retry_attempt is not None:
            evidence_refs.append(log_row.id)

    # Causal selection (NOT first-seen): the primary diagnostic signals must come
    # from the failure-attributed event, i.e. the terminal causal error. A benign
    # code emitted early in the run must not shadow the real fault.
    primary_error_code = _select_causal_signal(log_rows, parsed_list, lambda p: p.error_code)
    primary_exception_class = _select_causal_signal(
        log_rows, parsed_list, lambda p: p.exception_class
    )
    causal_message = _select_causal_signal(log_rows, parsed_list, lambda p: p.causal_message)
    if causal_message is None:
        # No exception/Caused-by line. Fall back to the terminal error line's text
        # so the unknown-signature digest can still distinguish distinct faults.
        causal_message = _terminal_error_message(log_rows)
    # Failed component: the component on the failure-attributed line — the line
    # carrying a diagnostic signal (error_code / exception_class), not the parent
    # route that merely propagates the failure ("Route failed ...").
    failed_component = _select_failed_component(log_rows, parsed_list)

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
        causal_message=causal_message,
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

    symptom_summary = _build_symptom_summary(
        execution_id=execution_id,
        job_name=exec_row.job_name,
        failed_component=summary.failed_component,
        primary_error_code=summary.primary_error_code,
        primary_exception_class=summary.primary_exception_class,
    )
    # Match the catalog against the resolved causal line when available — the
    # deepest "Caused by:" / failure-attributed text — so message_contains
    # constraints fire on the real fault, not the wrapper or a synthetic summary.
    match_message = summary.causal_message or symptom_summary
    pattern = CATALOG.match(
        summary.primary_error_code,
        summary.primary_exception_class,
        match_message,
    )

    signature_hash = _compose_signature(
        job_name=exec_row.job_name,
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
        failed_component=summary.failed_component,
        pattern_id=pattern.pattern_id if pattern else None,
        causal_message=summary.causal_message or symptom_summary,
    )

    first_bad_at = summary.first_warning_at or summary.first_error_at

    last_healthy_at: datetime.datetime | None = None
    if first_bad_at is not None:
        for log_row in log_rows:
            ts = _as_utc(log_row.timestamp)
            if ts is not None and ts < first_bad_at and normalize_severity(log_row.severity) == _SEV_INFO:
                last_healthy_at = ts

    if pattern is None:
        root_cause_candidate = "Execution terminated with errors; review log events for details."
        next_checks = ["Review execution log for error details"]
    else:
        root_cause_candidate = CATALOG.render_root_cause(
            pattern,
            error_code=summary.primary_error_code,
            exception_class=summary.primary_exception_class,
            host=summary.first_host,
            retry_max=summary.first_retry_max,
        )
        next_checks = CATALOG.render_next_checks(
            pattern,
            host=summary.first_host,
            error_code=summary.primary_error_code,
            exception_class=summary.primary_exception_class,
        )
        if not next_checks:
            next_checks = ["Review execution log for error details"]
    confidence = _derive_confidence(
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
        pattern=pattern,
    )
    pattern_id = pattern.pattern_id if pattern else None

    # Bounded, redacted evidence excerpts — the SAME excerpts persisted below are the
    # only payload the LLM fallback may see (never raw log windows).
    evidence_excerpts: list[tuple[int, str, str]] = [
        (log_row.id, role, redact_credentials(log_row.message[:300]))
        for log_row, role in _select_evidence_rows(log_rows, parsed_list)
    ]

    # Decorator over the deterministic result: escalates to a single bounded,
    # redacted LLM call only when the flag is on AND confidence == "low". Off by
    # default => byte-for-byte identical to the deterministic path. Fail-safe: any
    # LLM error keeps the deterministic result. Awaited directly — the whole build
    # path is async, so no event loop is created.
    root_cause_candidate, next_checks, cause_origin = await maybe_run_llm_fallback(
        root_cause_candidate,
        next_checks,
        confidence=confidence,
        symptom_summary=symptom_summary,
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
        failed_component=summary.failed_component,
        excerpts=[(role, excerpt) for _, role, excerpt in evidence_excerpts],
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
    inc_row.cause_origin = cause_origin
    inc_row.pattern_id = pattern_id
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

    for log_event_id, role, excerpt in evidence_excerpts:
        await _upsert_incident_evidence(session, project_id, incident_id, log_event_id, role, excerpt)

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
        if normalize_severity(row.severity) == _SEV_WARNING:
            _add(row, "first_warning")
            break

    for row in log_rows:
        if normalize_severity(row.severity) == _SEV_ERROR:
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
        if normalize_severity(row.severity) == _SEV_ERROR:
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


_SPECIFIC_PATTERN_FLOOR = 25
_FALLBACK_PATTERN_CEILING = 2


def _compose_signature(
    *,
    job_name: str,
    error_code: str | None,
    exception_class: str | None,
    failed_component: str | None,
    pattern_id: str | None,
    causal_message: str | None,
) -> str:
    """Compose a stable signature hash from resolved causal signals.

    Strong path: when any of error_code / exception_class / failed_component is
    present, the signature is their join — genuinely-identical failures hash
    identically (recurrence) and genuinely-distinct failures differ.

    Unknown path: with no strong signals the old behaviour was
    ``unknown|{job_name}`` which collapsed EVERY unknown failure of a job into one
    signature. Instead, anchor on the catalog ``pattern_id`` plus a short digest
    of the redacted causal message so two distinct unknown failures in the same
    job get distinct signatures, while two identical unknown failures still
    collapse (recurrence preserved).
    """
    # A diagnostic signal (error_code or exception_class) is required to call a
    # failure "known". A bare component is NOT enough — two unrelated faults in the
    # same component would otherwise collapse — so it falls through to the unknown
    # path where the causal message digest disambiguates them.
    has_diagnostic = bool(error_code or exception_class)
    if has_diagnostic:
        strong_parts = [p for p in (error_code, exception_class, failed_component) if p]
        sig_input = "|".join(strong_parts)
    else:
        digest_source = redact_credentials(causal_message or "")
        signal_digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
        sig_input = f"unknown|{job_name}|{failed_component or ''}|{pattern_id or ''}|{signal_digest}"
    return hashlib.sha256(sig_input.encode("utf-8")).hexdigest()


def _derive_confidence(
    error_code: str | None,
    exception_class: str | None,
    pattern,  # FailurePattern | None
) -> str:
    """Confidence reflects evidence quality: strong-signal count AND catalog match.

    high   : >=2 strong signals confirmed by a specific catalog pattern.
    medium : exactly one strong signal, OR >=2 signals matched only by a
             low-specificity pattern.
    low    : no strong signals, OR only the fallback pattern matched
             (generic.unknown / exception.generic). Persisted verbatim as "low"
             so Slice 4 can trigger the LLM fallback on it.
    """
    strong = sum(1 for v in (error_code, exception_class) if v is not None)
    specificity = pattern.specificity if pattern is not None else 0
    if strong == 0 or specificity <= _FALLBACK_PATTERN_CEILING:
        return "low"
    if strong >= 2 and specificity >= _SPECIFIC_PATTERN_FLOOR:
        return "high"
    return "medium"
