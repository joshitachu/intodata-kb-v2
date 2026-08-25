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
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import (
    RuntimeComponentMetricRow,
    RuntimeExecutionRow,
    RuntimeLogEventRow,
)
from src.db.models.runtime_insights import (
    RuntimeEnrichedEventRow,
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentEvidenceRow,
    RuntimeIncidentRow,
)
from src.runtime.insights.catalog import CATALOG
from src.runtime.insights.llm_fallback import maybe_run_llm_fallback
from src.runtime.insights.parser import (
    ParsedRuntimeLog,
    parse_runtime_log_message,
    redact_credentials,
    redact_log_text,
)

if TYPE_CHECKING:
    from src.runtime.insights.semantic import RuntimeSemanticIndexer

_SEV_WARNING = "warning"
_SEV_ERROR = "error"
_SEV_INFO = "info"

# Persisted incident-evidence excerpt length. Bounded so an evidence list cannot
# flood an LLM prompt; applied AFTER redaction (see `_build_incident`).
_EVIDENCE_EXCERPT_CHARS = 300

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
    known_components: frozenset[str] | None = None,
) -> InsightBuildResult:
    """Build and persist insight rows for one (project_id, execution_id) pair.

    Idempotent: calling twice with the same inputs updates rows rather than
    duplicating them. Returns a result summary for test assertions.

    ``known_components`` is the project's authoritative component inventory, used
    by the parser to resolve compound component keys that no naming convention can
    safely claim. Fetched here when not supplied; bulk callers should hoist it per
    project to avoid one lookup per execution.
    """
    exec_row = await _fetch_execution(session, project_id, execution_id)
    if exec_row is None:
        return InsightBuildResult(enriched_events=0, had_incident=False)

    log_rows = await fetch_execution_log_rows(session, project_id, execution_id)
    if known_components is None:
        known_components = await fetch_known_components(session, project_id)

    parsed_list = [
        parse_runtime_log_message(log_row.message, known_components) for log_row in log_rows
    ]
    # Association needs the whole execution in view, so it runs after parsing and
    # before persistence — the enriched rows then carry the borrowed sentence
    # along with the provenance marker that says it was borrowed.
    associate_context_signals(log_rows, parsed_list)
    for log_row, parsed in zip(log_rows, parsed_list):
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
    # One inventory lookup per project, not per execution.
    inventories: dict[str, frozenset[str]] = {}
    for row in rows:
        if row.project_id not in inventories:
            inventories[row.project_id] = await fetch_known_components(session, row.project_id)
        await build_runtime_insights_for_execution(
            session,
            project_id=row.project_id,
            execution_id=row.execution_id,
            source_key=row.source_key,
            ingested_at=now,
            known_components=inventories[row.project_id],
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


# ---------------------------------------------------------------------------
# Context association
# ---------------------------------------------------------------------------
#
# The parser is a pure per-line function, so a fault line that carries no text of
# its own extracts nothing even when the line beside it states the cause. Real
# runtimes emit exactly that pairing — a CXF/JAX-RS client logs the diagnosis as a
# warning and then a bare "Failed request" as the error:
#
#   57  warning  Interceptor for {https://host:9200/alias}WebClient has thrown exception
#   58  error    Failed request              <- no signal at all
#
# Association runs where an execution's lines are visible together (here, not in
# the parser) and lends the neighbouring diagnosis to the empty line. It is
# deliberately narrow: a bounded lookback, fault severities only, and provenance
# recorded so a borrowed sentence is never mistaken for the line's own words.

DIALECT_CONTEXT = "context"
# Lines, not seconds: a diagnosis more than a couple of lines away is not
# reliably about this error. Small by intent — widening it would start linking
# unrelated faults that merely happened nearby.
_CONTEXT_LOOKBACK = 3
_FAULT_SEVERITIES = (_SEV_ERROR, _SEV_WARNING)


def _carries_no_signal(parsed: ParsedRuntimeLog) -> bool:
    """True when the parser found nothing at all on this line.

    Deliberately strict: any single extracted signal — even just a component name
    — means the line spoke for itself and must not be overwritten by a neighbour.
    """
    return not any(
        (
            parsed.causal_message,
            parsed.exception_class,
            parsed.error_code,
            parsed.component_name,
            parsed.field_violation_kind,
            parsed.host,
            parsed.queue_name,
            parsed.endpoint,
        )
    )


def associate_context_signals(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> int:
    """Lend an adjacent line's diagnosis to signal-less fault lines, in place.

    Only ``causal_message`` is borrowed — never ``exception_class`` or
    ``error_code``. Those drive the signature hash and the confidence tier, and a
    line that did not itself report an exception must not inflate either.

    Walks back at most ``_CONTEXT_LOOKBACK`` rows, skipping info/debug chatter.
    The first fault-severity row found is the only donor considered: if it is
    itself signal-less, the search stops rather than reaching past it into an
    older, unrelated fault. Donors that were themselves context-derived are
    refused, so a borrowed sentence cannot propagate down a run of empty lines.

    Returns the number of rows that adopted context.
    """
    adopted = 0
    for index, (log_row, parsed) in enumerate(zip(log_rows, parsed_list)):
        if normalize_severity(log_row.severity) not in _FAULT_SEVERITIES:
            continue
        if not _carries_no_signal(parsed):
            continue
        donor = _nearest_fault_donor(log_rows, parsed_list, index)
        if donor is None:
            continue
        parsed.causal_message = donor.causal_message
        # Provenance: this row's sentence is inherited, not extracted. Reuses the
        # existing dialect column rather than adding a parallel mechanism — the
        # column already answers "what claimed this line".
        parsed.dialect = DIALECT_CONTEXT
        adopted += 1
    return adopted


def _without_context_rows(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> tuple[list[RuntimeLogEventRow], list[ParsedRuntimeLog]]:
    """Drop context-derived rows, keeping the two lists index-aligned."""
    kept = [
        (log_row, parsed)
        for log_row, parsed in zip(log_rows, parsed_list)
        if parsed.dialect != DIALECT_CONTEXT
    ]
    if not kept:
        return [], []
    rows, parses = zip(*kept)
    return list(rows), list(parses)


def _nearest_fault_donor(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
    index: int,
) -> ParsedRuntimeLog | None:
    """Return the nearest preceding fault line able to explain ``index``."""
    start = max(0, index - _CONTEXT_LOOKBACK)
    for back in range(index - 1, start - 1, -1):
        if normalize_severity(log_rows[back].severity) not in _FAULT_SEVERITIES:
            continue  # info/debug chatter between the pair is not a boundary
        candidate = parsed_list[back]
        # First fault row wins or loses outright; never look past it.
        if candidate.dialect == DIALECT_CONTEXT or not candidate.causal_message:
            return None
        return candidate
    return None


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


def _select_causal_code(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> tuple[str | None, str | None]:
    """Pick ``(error_code, error_family)`` as a consistent pair from ONE row.

    Selecting the code and its family independently could pair a code from one
    log line with a family from another, and the catalog now enforces family
    agreement — a mismatched pair would silently suppress every platform pattern.
    Tiering matches :func:`_select_causal_signal`: last error row, then last
    warning row, then last row overall.
    """
    by_error: tuple[str, str | None] | None = None
    by_warning: tuple[str, str | None] | None = None
    by_any: tuple[str, str | None] | None = None
    for log_row, parsed in zip(log_rows, parsed_list):
        if not parsed.error_code:
            continue
        pair = (parsed.error_code, parsed.error_family)
        by_any = pair
        sev = normalize_severity(log_row.severity)
        if sev == _SEV_ERROR:
            by_error = pair
        elif sev == _SEV_WARNING:
            by_warning = pair
    chosen = by_error or by_warning or by_any
    return chosen if chosen is not None else (None, None)


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


_TERMINAL_WRAPPER_COMPONENT_RE = re.compile(
    r"^(?:tdie|abort|terminate|raise|throw|fail)(?:_|$)", re.IGNORECASE
)
_GENERIC_TERMINAL_MESSAGES = frozenset(
    {
        "",
        "null",
        "none",
        "error",
        "failed",
        "unknown",
        "job failed",
        "execution failed",
    }
)


def _is_terminal_wrapper_component(component_name: str | None) -> bool:
    """Whether a component's role is explicitly to abort/raise a failure.

    This is a provider-neutral role test with Talend's ``tDie`` included as one
    spelling. A wrapper is still retained as evidence; it simply cannot displace
    an earlier, more substantive fault as the originating component.
    """
    return bool(component_name and _TERMINAL_WRAPPER_COMPONENT_RE.search(component_name))


def _primary_fault_score(
    log_row: RuntimeLogEventRow,
    parsed: ParsedRuntimeLog,
) -> int:
    """Rank one fault line by diagnostic value, not merely by recency."""
    message = parsed.causal_message or log_row.message or ""
    pattern = CATALOG.match(
        parsed.error_code,
        parsed.exception_class,
        f"{message}\n{log_row.message}",
        parsed.error_family,
    )
    specificity = pattern.specificity if pattern is not None else 0
    score = specificity
    score += 12 * sum(bool(value) for value in (parsed.error_code, parsed.exception_class))
    if parsed.component_name:
        score += 3
    if normalize_severity(log_row.severity) == _SEV_ERROR:
        score += 4
    if _is_substantive_diagnostic(message):
        score += 8
    if message.strip().lower() in _GENERIC_TERMINAL_MESSAGES:
        score -= 15
    if _is_terminal_wrapper_component(parsed.component_name):
        score -= 30
    return score


def _select_primary_fault_index(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
) -> int | None:
    """Choose the substantive originating fault across one execution.

    Latest-wins remains the tie-breaker, but specific catalog evidence, codes,
    exceptions and useful diagnostic language outrank explicit terminal wrappers.
    """
    best: tuple[int, int] | None = None
    for index, (log_row, parsed) in enumerate(zip(log_rows, parsed_list)):
        if normalize_severity(log_row.severity) not in _FAULT_SEVERITIES:
            continue
        if not any(
            (
                parsed.causal_message,
                parsed.error_code,
                parsed.exception_class,
                parsed.component_name,
            )
        ):
            continue
        candidate = (_primary_fault_score(log_row, parsed), index)
        if best is None or candidate > best:
            best = candidate
    return best[1] if best is not None else None


def _nearest_component_signal(
    parsed_list: list[ParsedRuntimeLog],
    primary_index: int | None,
    getter: Callable[[ParsedRuntimeLog], str | None],
) -> str | None:
    """Complete a split multi-line diagnostic from the same component.

    Runtimes commonly emit an error code and exception on adjacent lines. They
    are one causal event when the component agrees; crossing component boundaries
    would instead create the signal-mixing bug this builder avoids.
    """
    if primary_index is None:
        return None
    component = parsed_list[primary_index].component_name
    if not component:
        return None
    candidates = [
        (abs(index - primary_index), -index, getter(parsed))
        for index, parsed in enumerate(parsed_list)
        if parsed.component_name == component and getter(parsed) is not None
    ]
    if not candidates:
        return None
    return min(candidates)[2]


def _select_termination_component(
    log_rows: list[RuntimeLogEventRow],
    parsed_list: list[ParsedRuntimeLog],
    primary_index: int | None,
    failed_component: str | None,
) -> str | None:
    """Return a distinct terminal wrapper observed after the origin."""
    start = (primary_index + 1) if primary_index is not None else 0
    for log_row, parsed in reversed(list(zip(log_rows[start:], parsed_list[start:]))):
        if normalize_severity(log_row.severity) not in _FAULT_SEVERITIES:
            continue
        component = parsed.component_name
        if (
            component
            and component != failed_component
            and _is_terminal_wrapper_component(component)
        ):
            return component
    return None


def _terminal_error_message(log_rows: list[RuntimeLogEventRow]) -> str | None:
    """Return the message of the last error-severity row (the failure-attributed
    line) for unknown-failure disambiguation. None when no error row exists."""
    for log_row in reversed(log_rows):
        if normalize_severity(log_row.severity) == _SEV_ERROR:
            return log_row.message
    return None


async def fetch_known_components(session: AsyncSession, project_id: str) -> frozenset[str]:
    """Return the project's observed component keys.

    ``runtime_component_metrics`` is the authoritative record of which components
    the runtime actually reported, so it is the safe way to accept a component
    name whose shape no convention regex can validate (subjob-prefixed compound
    keys). Scoped to one project: a component key from another tenant must never
    influence attribution here.
    """
    stmt = select(RuntimeComponentMetricRow.component_key).where(
        RuntimeComponentMetricRow.project_id == project_id
    ).distinct()
    return frozenset((await session.execute(stmt)).scalars().all())


async def _fetch_execution(
    session: AsyncSession, project_id: str, execution_id: str
) -> RuntimeExecutionRow | None:
    stmt = select(RuntimeExecutionRow).where(
        RuntimeExecutionRow.project_id == project_id,
        RuntimeExecutionRow.execution_id == execution_id,
    )
    return (await session.execute(stmt)).scalars().first()


async def fetch_execution_log_rows(
    session: AsyncSession, project_id: str, execution_id: str
) -> list[RuntimeLogEventRow]:
    stmt = (
        select(RuntimeLogEventRow)
        .where(
            RuntimeLogEventRow.project_id == project_id,
            RuntimeLogEventRow.execution_id == execution_id,
        )
        # line_ordinal is the authoritative position within an execution, and it is
        # the tiebreaker rather than a nicety: runtimes stamp a whole burst of lines
        # with one timestamp (7,754 such groups in the reference corpus), so
        # timestamp alone leaves the order of a fault and its explanation
        # undefined. Context association and every "last error row" selection
        # depend on this being stable.
        .order_by(
            RuntimeLogEventRow.timestamp.asc(),
            RuntimeLogEventRow.line_ordinal.asc(),
            RuntimeLogEventRow.id.asc(),
        )
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
    row.field_name = parsed.field_name
    row.field_violation_kind = parsed.field_violation_kind
    row.expected_type = parsed.expected_type
    row.message_family = parsed.message_family
    row.confidence = parsed.confidence
    row.dialect = parsed.dialect
    row.job_name = parsed.job_name
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
    termination_component: str | None
    primary_error_code: str | None
    primary_error_family: str | None
    primary_exception_class: str | None
    causal_message: str | None
    # Full text of the failure-attributed log row, redacted, used ONLY for catalog
    # matching. Not persisted: causal_message is the display field. Kept separate
    # because trimming a transport preamble out of the readable sentence also
    # removes tokens a dialect pattern keys on ("Exhausted after delivery
    # attempt"), and matching must see all available evidence.
    match_text: str | None
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

    # Choose one coherent originating event. This avoids combining the code from
    # one row with the component/message from another and, critically, prevents a
    # generic terminal wrapper such as `tDie_1 null` from hiding an earlier Oracle
    # connection-reset line.
    primary_index = _select_primary_fault_index(log_rows, parsed_list)
    primary_parsed = parsed_list[primary_index] if primary_index is not None else None
    primary_row = log_rows[primary_index] if primary_index is not None else None
    primary_error_code = primary_parsed.error_code if primary_parsed else None
    primary_error_family = primary_parsed.error_family if primary_parsed else None
    if primary_error_code is None:
        primary_error_code = _nearest_component_signal(
            parsed_list, primary_index, lambda parsed: parsed.error_code
        )
        primary_error_family = _nearest_component_signal(
            parsed_list, primary_index, lambda parsed: parsed.error_family
        )
    primary_exception_class = primary_parsed.exception_class if primary_parsed else None
    if primary_exception_class is None:
        primary_exception_class = _nearest_component_signal(
            parsed_list, primary_index, lambda parsed: parsed.exception_class
        )
    causal_message = primary_parsed.causal_message if primary_parsed else None
    if causal_message is None:
        # No exception/Caused-by line. Fall back to the terminal error line's text
        # so the unknown-signature digest can still distinguish distinct faults.
        causal_message = _terminal_error_message(log_rows)
    if causal_message is not None:
        # Persisted from here on AND read straight into an LLM tool result, so
        # redaction is mandatory rather than advisory — and it is the FULL
        # redaction, not the credential keyword filter. Sentences reaching this
        # line via `_terminal_error_message` never passed through the parser's
        # own redaction at all.
        causal_message = redact_log_text(causal_message)
    terminal_text = primary_row.message if primary_row is not None else _terminal_error_message(log_rows)
    # `match_text` is catalog-matching input only: never persisted, never shown,
    # never prompted (see the field docstring). It stays on the credential filter
    # so pattern matching keeps seeing the text the runtime actually emitted.
    match_text = redact_credentials(terminal_text) if terminal_text else None
    # Failed component: the component on the failure-attributed line — the line
    # carrying a diagnostic signal (error_code / exception_class), not the parent
    # route that merely propagates the failure ("Route failed ...").
    failed_component = primary_parsed.component_name if primary_parsed else None
    if failed_component is None:
        failed_component = _select_failed_component(log_rows, parsed_list)
    termination_component = _select_termination_component(
        log_rows, parsed_list, primary_index, failed_component
    )

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
        termination_component=termination_component,
        primary_error_code=primary_error_code,
        primary_error_family=primary_error_family,
        primary_exception_class=primary_exception_class,
        causal_message=causal_message,
        match_text=match_text,
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
    row.causal_message = s.causal_message
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
    # All available evidence, most-specific first: the trimmed diagnosis plus the
    # untrimmed failing line. Matching on the trimmed sentence alone silently loses
    # dialect tokens that the preamble carried.
    match_message = "\n".join(
        part for part in (summary.causal_message, summary.match_text) if part
    ) or symptom_summary
    pattern = CATALOG.match(
        summary.primary_error_code,
        summary.primary_exception_class,
        match_message,
        # Family agreement: without it a platform pattern can claim a same-shaped
        # code from another platform (e.g. a bare numeric read as a MySQL errno).
        summary.primary_error_family,
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
        # A fallback-tier template says nothing the reader did not already know
        # ("Execution terminated with errors; review log events for details").
        # When the runtime stated an actual diagnosis, quote it instead: the log's
        # own sentence is strictly more informative than a placeholder. Specific
        # patterns keep their template, which explains the fault class and is not
        # merely a restatement.
        if (
            pattern.specificity <= _FALLBACK_PATTERN_CEILING
            and summary.causal_message
            and _is_substantive_diagnostic(summary.causal_message)
        ):
            root_cause_candidate = f"Reported by the runtime: {summary.causal_message.strip()}"
    confidence = _derive_confidence(
        error_code=summary.primary_error_code,
        exception_class=summary.primary_exception_class,
        pattern=pattern,
    )
    pattern_id = pattern.pattern_id if pattern else None

    # Bounded, redacted evidence excerpts — the SAME excerpts persisted below are the
    # only payload the LLM fallback may see (never raw log windows).
    #
    # Redact FIRST, then truncate. Truncating first was the previous order and it
    # leaks: a URL cut at 300 chars loses the suffix that makes the FQDN rule fire
    # (`volkerinfra.relaticsonline.com` -> `volkerinfra.relatics`, two labels, no
    # recognised suffix), so the partial host survives the entity pass. Redacting
    # the whole line first also buys back characters of real diagnosis.
    evidence_excerpts: list[tuple[int, str, str]] = [
        (log_row.id, role, (redact_log_text(log_row.message) or "")[:_EVIDENCE_EXCERPT_CHARS])
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
    inc_row.causal_message = summary.causal_message
    inc_row.termination_component = summary.termination_component
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
        causal_message=summary.causal_message,
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
    causal_message: str | None = None,
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
    row.causal_message = causal_message
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

# A quotable diagnostic must carry more than the bare fact of failure. These are
# the surface features that distinguish an actual diagnosis ("Invalid dataset ID
# \"test_ zaf_del_clean\"", "Not found: Files gs://...") from a status stub
# ("engine failed"), which must never be dressed up as a root cause.
_MIN_DIAGNOSTIC_WORDS = 5
_DIAGNOSTIC_SUBSTANCE_RES = (
    re.compile(r"['\"`][^'\"`]{2,}['\"`]"),  # a quoted identifier or value
    re.compile(r"\w+://"),  # a URI (gs://, https://, jdbc:...)
    re.compile(r"\b[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.[A-Z]\w+"),  # qualified class
    re.compile(r"\S+:\s+\S+"),  # "detail: value" structure
)


def _is_substantive_diagnostic(text: str) -> bool:
    """True when the runtime's sentence says more than "it failed".

    Quoting the log is only an improvement when the log actually explained
    something. A short, featureless status line quoted as a root cause would look
    like a diagnosis while carrying no more information than the fallback
    template it replaced.
    """
    stripped = text.strip()
    if len(stripped.split()) >= _MIN_DIAGNOSTIC_WORDS:
        return True
    return any(pattern.search(stripped) for pattern in _DIAGNOSTIC_SUBSTANCE_RES)


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
        # The digest is one-way and stores no text, so redaction here is about
        # HASH STABILITY, not confidentiality. The caller now supplies an
        # entity-redacted sentence, which changes the digest input wherever an
        # address was present and therefore changes that incident's signature on
        # the next rebuild. Measured against the stored corpus before making the
        # change: 784 incidents take this unknown path, 783 of them have no
        # causal sentence at all and digest their (address-free) symptom summary,
        # and exactly ONE sees a different input. Carrying an un-redacted second
        # copy of the sentence through the pipeline to protect one hash is a
        # worse trade than the churn, and a full backfill rehashes every incident
        # consistently in any case.
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
    medium : exactly one strong signal, OR a highly specific deterministic
             message pattern, OR >=2 signals matched only by a low-specificity
             pattern.
    low    : no strong signals and no specific message pattern, OR only the fallback pattern
             (generic.unknown / exception.generic). Persisted verbatim as "low"
             so Slice 4 can trigger the LLM fallback on it.
    """
    strong = sum(1 for v in (error_code, exception_class) if v is not None)
    specificity = pattern.specificity if pattern is not None else 0
    if specificity <= _FALLBACK_PATTERN_CEILING:
        return "low"
    if strong >= 2 and specificity >= _SPECIFIC_PATTERN_FLOOR:
        return "high"
    if strong == 0 and specificity < _SPECIFIC_PATTERN_FLOOR:
        return "low"
    return "medium"
