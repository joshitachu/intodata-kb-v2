"""Repository for the held runtime-data store.

Reads return the normalized runtime models from src/runtime/models.py and are always
project-scoped. The scope is an explicit argument supplied by backend code — never by
LLM/tool arguments — so authorization cannot be widened from chat input.

The write-port performs dialect-portable, idempotent upserts (select-then-update/insert
rather than Postgres-only ON CONFLICT) so the same repository runs under SQLite in
unit tests and Postgres in production.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import (
    RuntimeArtifactRow,
    RuntimeComponentMetricRow,
    RuntimeConnectionRow,
    RuntimeEngineRow,
    RuntimeEnvironmentRow,
    RuntimeExecutionRow,
    RuntimeFailureRow,
    RuntimeJobRow,
    RuntimeJobDependencyRow,
    RuntimeJobRunRow,
    RuntimeLogEventRow,
    RuntimePlanRow,
    RuntimePlanRunRow,
    RuntimeSourceRow,
    RuntimeSourceCredentialsRow,
    RuntimeSourceHealthRow,
    RuntimeWorkspaceProjectMapRow,
    RuntimeSyncStateRow,
    RuntimeWorkspaceRow,
)
from src.db.models.runtime_insights import (
    RuntimeErrorSignatureRow,
    RuntimeExecutionSummaryRow,
    RuntimeIncidentRow,
)
from src.db.models.runtime_topology import RuntimeJobScanMapRow
from src.runtime.models import (
    DetectFailuresInput,
    GetExecutionDetailsInput,
    GetExecutionLogsInput,
    ListExecutionsInput,
    ListRuntimeArtifactsInput,
    ListRuntimeComponentHotspotsInput,
    ListRuntimeConnectionsInput,
    ListRuntimeDependenciesInput,
    ListRuntimeEnginesInput,
    ListRuntimeErrorSignaturesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobReliabilityInput,
    ListRuntimeJobThroughputInput,
    ListRuntimeJobsInput,
    ListRuntimePlanRunsInput,
    ListRuntimePlansInput,
    RuntimeArtifact,
    RuntimeComponentHotspot,
    RuntimeConnection,
    RuntimeDependencySummary,
    RuntimeErrorSignature,
    RuntimeExecution,
    RuntimeEngine,
    RuntimeEngineHealth,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeEstateOverview,
    RuntimeExecutionState,
    RuntimeExtendedMetadataAvailability,
    RuntimeFailure,
    RuntimeHealthSummary,
    RuntimeJob,
    RuntimeJobDependency,
    RuntimeJobHealth,
    RuntimeDocEvidence,
    RuntimeDocReadScope,
    RuntimeMirroredEnvironment,
    RuntimeDocEvidenceFailure,
    RuntimeDocIncidentInsight,
    RuntimeDocLogQuality,
    RuntimeJobReliability,
    RuntimeJobOperationalInsight,
    RuntimeJobRun,
    RuntimeJobThroughput,
    RuntimeComponentMetric,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimeOutcomeTrendPoint,
    RuntimePlan,
    RuntimePlanHealth,
    RuntimePlanRun,
    RuntimePlanRunStepResult,
    RuntimePlanStep,
    RuntimeSource,
    RuntimeSourceHealth,
    RuntimeSourceRead,
    RuntimeSourceUpdate,
    RuntimeTrendBucket,
    RuntimeTrendInput,
    RuntimeWorkspaceProjectMap,
    RuntimeSyncState,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.secrets import (
    current_key_id,
    decrypt_and_rotate_if_needed,
    encrypt,
)
from src.runtime.insights.parser import redact_credentials
from src.runtime.redaction import sanitize_doc_evidence_error
from src.runtime.store import ProjectScope, RuntimeDuplicateSourceError, RuntimeRecordNotFoundError

# Severity ordering for min_severity filtering (low -> high).
_SEVERITY_ORDER: list[RuntimeLogSeverity] = list(RuntimeLogSeverity)


def _severities_at_or_above(minimum: RuntimeLogSeverity) -> list[str]:
    min_rank = _SEVERITY_ORDER.index(minimum)
    return [s.value for s in _SEVERITY_ORDER[min_rank:]]


#: Substring matching is only selective enough to be worth a scan above this
#: length. "in", "the", "a" would each match most of the corpus.
_LOG_TOKEN_MIN_CHARS = 4
#: Bounded so a long question cannot assemble an unbounded OR chain.
_LOG_TOKEN_MAX_COUNT = 6
_LOG_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9_.$]+")


def _like_escape(value: str) -> str:
    """Escape LIKE metacharacters so a literal stays literal.

    ``_`` matches any single character and appears in nearly every Talend
    component name (``tSetGlobalVar_3``), so an unescaped needle silently
    widened the match -- ``tSetGlobalVarX3`` came back as a hit for it.
    """

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _log_search_tokens(query: str) -> list[str]:
    """Selective substring tokens for a log search, longest first.

    Splits on separators but KEEPS ``. _ $``, because the identifiers worth
    finding contain them: ``java.lang.NullPointerException``,
    ``tSetGlobalVar_3``, ``Foo$Bar``. Deduplicated, length-filtered and capped.

    LIKE metacharacters are escaped: ``_`` is a single-character wildcard and it
    appears in nearly every Talend component name, so an unescaped token would
    silently widen the match.
    """

    seen: list[str] = []
    for raw in _LOG_TOKEN_SPLIT_RE.split(query):
        token = raw.strip(".$_")
        if len(token) < _LOG_TOKEN_MIN_CHARS:
            continue
        escaped = _like_escape(token)
        if escaped not in seen:
            seen.append(escaped)
    seen.sort(key=len, reverse=True)
    return seen[:_LOG_TOKEN_MAX_COUNT]


def log_event_fingerprint(event: RuntimeLogEvent) -> str:
    """Deterministic fingerprint for log idempotency (no natural unique id upstream)."""
    raw = f"{event.timestamp.isoformat()}|{event.severity.value}|{event.line_ordinal}|{event.message}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


_SQLITE_BUCKET_FORMATS: dict[RuntimeTrendBucket, str] = {
    RuntimeTrendBucket.HOUR: "%Y-%m-%dT%H:00:00",
    RuntimeTrendBucket.DAY: "%Y-%m-%dT00:00:00",
    RuntimeTrendBucket.WEEK: "%Y-%m-%dT00:00:00",
}


def _bucket_key_expr(dialect: str, column, bucket: RuntimeTrendBucket):
    """UTC-anchored time-bucket key rendered as an ISO 'YYYY-MM-DDTHH:MM:SS' string.

    Returning a string rather than a native timestamp keeps one expression working on
    both backends. On Postgres the column is pinned to UTC first: ``date_trunc`` on a
    ``timestamptz`` otherwise honours the session TimeZone, so the same rows would
    bucket differently depending on connection settings. SQLite stores these columns
    as naive UTC strings already, so ``strftime`` is directly equivalent. Week buckets
    start Monday on both backends (verified against real data).
    """
    if dialect == "postgresql":
        truncated = func.date_trunc(bucket.value, func.timezone("UTC", column))
        return func.to_char(truncated, 'YYYY-MM-DD"T"HH24:MI:SS')
    fmt = _SQLITE_BUCKET_FORMATS[bucket]
    if bucket is RuntimeTrendBucket.WEEK:
        return func.strftime(fmt, column, "weekday 0", "-6 days")
    return func.strftime(fmt, column)


def _parse_bucket_key(value: object) -> datetime.datetime | None:
    if not isinstance(value, str):
        return _as_utc(value) if isinstance(value, datetime.datetime) else None
    try:
        return datetime.datetime.fromisoformat(value).replace(tzinfo=datetime.UTC)
    except ValueError:
        return None


def _percentile_at_rank(rank_col, count_col, value_col, percentile: int):
    """Pick the value at the ceil-ranked percentile position, division-free.

    ``rank`` is the smallest integer n with ``n >= count * percentile / 100``. Encoding
    that as two integer multiplications avoids depending on how each backend (or
    SQLAlchemy's ``/`` operator) treats integer division, and reproduces exactly the
    discrete semantics of the in-process ``_percentile`` helper below — so a percentile
    computed in SQL and one computed in Python always agree.
    """
    threshold = count_col * percentile
    return func.max(
        case(
            (
                and_(rank_col * 100 >= threshold, (rank_col - 1) * 100 < threshold),
                value_col,
            ),
        )
    )


def _epoch_seconds(dialect: str, column):
    """A stored UTC timestamp as absolute epoch seconds, on either backend.

    Needed because an inter-run GAP is arithmetic on timestamps and the two
    backends disagree on how to do it: Postgres subtracts to an ``interval``,
    SQLite stores these columns as naive UTC strings and will not subtract at
    all. Reducing each timestamp to a number FIRST leaves one gap expression for
    both. ``extract(epoch)`` is absolute, so unlike ``date_trunc`` it does not
    depend on the session TimeZone; ``julianday`` keeps the sub-second part that
    ``strftime('%s')`` would truncate, so the two backends agree to the
    millisecond on the same data.
    """
    if dialect == "postgresql":
        return func.extract("epoch", column)
    return (func.julianday(column) - 2_440_587.5) * 86_400.0


class _GapStats(NamedTuple):
    """Inter-run gap quantiles for ONE job in ONE environment, seconds."""

    p25: int | None
    p50: int | None
    p75: int | None
    sample_size: int


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _evidence_completeness(failed: int, logged: int, diagnosed: int) -> str:
    """Compact quality label for dashboard and documentation consumers."""
    if failed <= 0:
        return "no_failures"
    if logged <= 0:
        return "missing"
    if diagnosed >= failed and logged >= failed:
        return "complete"
    if diagnosed > 0:
        return "partial"
    return "logs_only"


_FAILURE_VOLATILE_RE = re.compile(
    r"""
    [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}  # uuid
    | \b[0-9a-f]{16,}\b                                            # long hex id
    | \b\d{3,}\b                                                   # counters, ports, ids
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Orphan runtime jobs are a tripwire, not an inventory: the name list exists so
# a document can say "N runtime jobs have no scanned code", and the count is
# what carries the finding once the list is long.  Bounded so a mis-bound
# project with a large runtime vocabulary cannot inflate an LLM-bound payload.
_DOC_EVIDENCE_MAX_ORPHAN_JOBS = 200


def _failure_signature(summary: str) -> str:
    """Group key for "the same failure, different incident".

    Deliberately NOT the incident `signature_hash` from
    `runtime/insights/builder.py`: that one composes parsed log fields (error
    code, exception class, failed component) and needs ingested log events.
    This works on the provider's headline string alone, which is all the
    documentation path has, so it only strips the parts that vary per
    occurrence -- execution ids and numeric counters.
    """
    return _FAILURE_VOLATILE_RE.sub("#", summary.casefold()).strip()


def _observed_runs_per_day(rollup: RuntimeJobReliability) -> float | None:
    """Observed cadence, derived from run timestamps rather than a schedule.

    TMC exposes no schedule expression on a run, so frequency is what actually
    happened over the observed span.  A single run, or runs inside one day, give
    no span to divide by and yield None -- reporting "1 run/day" from one sample
    would read as a cadence the evidence does not support.
    """
    first, last = rollup.first_run_at, rollup.last_run_at
    if first is None or last is None or rollup.total_runs < 2:
        return None
    span_days = (last - first).total_seconds() / 86_400
    if span_days < 1:
        return None
    return round(rollup.total_runs / span_days, 2)


def _dominant_environment_gaps(
    gaps: dict[tuple[str, str | None], _GapStats],
    job_name: str,
    environments: Sequence[str],
) -> _GapStats | None:
    """Gap statistics for the ONE environment the payload already names.

    A runtime job NAME is not a schedule: `runtime_jobs` is unique on
    `(project_id, source_key, job_id)`, so one name can carry a DEV, a TEST and
    a PROD deployment (verified in werkorder -- `maximoFourpsWorkOrder` runs in
    both ACC and TST, `Maximo2Quintiq_Workorder_Route` likewise; alitalend has
    124 job rows under 92 distinct names). Pooling their starts interleaves an
    hourly PROD schedule with ad-hoc DEV runs, which does not merely add noise:
    it produces a median and an IQR that describe no deployment that exists.
    Pooled, `maximoFourpsWorkOrder` reports a median gap of 75_475 s; its ACC
    deployment's real median is 2 s.

    So the reported quantiles are always one environment's, and the environment
    is the highest-volume one -- which is exactly `environments[0]`, since
    `_doc_evidence_environments` already returns them most-frequent-first from
    the same table and the same window. Selecting via that list rather than
    recomputing "dominant" here is deliberate: it makes it impossible for the
    named environment and the measured one to drift apart, including under a
    volume tie.

    `environments` is empty only when the provider reported no environment at
    all, in which case the unnamed group is the whole population and pooling it
    blends nothing.
    """
    if environments:
        return gaps.get((job_name, environments[0]))
    return gaps.get((job_name, None))


def _as_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    """Return a timezone-aware UTC datetime. Postgres returns tz-aware values; SQLite
    (used in tests) returns naive ones. Runtime timestamps are stored as UTC, so the
    repository normalizes both backends to the same tz-aware UTC contract."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


class RuntimeStoreRepository:
    """Async repository over the runtime-data tables, bound to one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- scope helpers ---

    @staticmethod
    def _denies_all(scope: ProjectScope) -> bool:
        """An empty (non-None) scope means the caller may see nothing."""
        return scope is not None and len(scope) == 0

    @staticmethod
    def _apply_scope(stmt, column, scope: ProjectScope):
        if scope is None:
            return stmt
        return stmt.where(column.in_(scope))

    @property
    def _dialect(self) -> str:
        return self._session.bind.dialect.name if self._session.bind is not None else ""

    @staticmethod
    def _apply_execution_filters(stmt, params: ListExecutionsInput):
        """Filters shared by the execution list and its count.

        Kept in one place so a page and its `total` can never be computed from
        different predicates.
        """
        if params.project_name:
            stmt = stmt.where(RuntimeExecutionRow.project_name == params.project_name)
        if params.job_name:
            stmt = stmt.where(RuntimeExecutionRow.job_name == params.job_name)
        if params.state is not None:
            stmt = stmt.where(RuntimeExecutionRow.state == params.state.value)
        if params.since:
            stmt = stmt.where(RuntimeExecutionRow.started_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeExecutionRow.started_at <= params.until)
        if params.min_duration_ms is not None:
            stmt = stmt.where(RuntimeExecutionRow.duration_ms >= params.min_duration_ms)
        if params.unfinished is not None:
            stmt = (
                stmt.where(RuntimeExecutionRow.ended_at.is_(None))
                if params.unfinished
                else stmt.where(RuntimeExecutionRow.ended_at.is_not(None))
            )
        return stmt

    # --- reads (project-scoped) ---

    async def list_executions(
        self, scope: ProjectScope, params: ListExecutionsInput
    ) -> list[RuntimeExecution]:
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeExecutionRow)
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        stmt = self._apply_execution_filters(stmt, params)
        stmt = stmt.order_by(RuntimeExecutionRow.started_at.desc(), RuntimeExecutionRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        # Defensive read-side protection for historical mirrored writes. A source
        # owns its execution ids, so an identical (source_key, execution_id) is
        # one physical event even if an old mirror still exists temporarily.
        seen: set[tuple[str, str]] = set()
        return [
            _to_execution(row)
            for row in rows
            if not ((key := (row.source_key, row.execution_id)) in seen or seen.add(key))
        ]

    async def get_execution_details(
        self, scope: ProjectScope, params: GetExecutionDetailsInput
    ) -> RuntimeExecution:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.execution_id == params.execution_id
        )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        return _to_execution(row)

    async def detect_failures(
        self, scope: ProjectScope, params: DetectFailuresInput
    ) -> list[RuntimeFailure]:
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeFailureRow)
        stmt = self._apply_scope(stmt, RuntimeFailureRow.project_id, scope)
        if params.since:
            stmt = stmt.where(RuntimeFailureRow.failed_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeFailureRow.failed_at <= params.until)
        stmt = stmt.order_by(RuntimeFailureRow.failed_at.desc(), RuntimeFailureRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_failure(row) for row in rows]

    async def get_execution_logs(
        self, scope: ProjectScope, params: GetExecutionLogsInput
    ) -> list[RuntimeLogEvent]:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        stmt = select(RuntimeLogEventRow).where(
            RuntimeLogEventRow.execution_id == params.execution_id
        )
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        if params.min_severity is not None:
            stmt = stmt.where(
                RuntimeLogEventRow.severity.in_(_severities_at_or_above(params.min_severity))
            )
        stmt = stmt.order_by(RuntimeLogEventRow.timestamp.asc(), RuntimeLogEventRow.line_ordinal.asc(), RuntimeLogEventRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        if not rows:
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        return [_to_log_event(row) for row in rows]

    async def get_freshness(
        self, scope: ProjectScope, source_key: str, project_id: str | None = None
    ) -> datetime.datetime | None:
        """Latest in-scope watermark for a source (and optional project) — the "as of"."""
        if self._denies_all(scope):
            return None
        stmt = select(RuntimeSyncStateRow.last_synced_at).where(
            RuntimeSyncStateRow.source_key == source_key
        )
        stmt = self._apply_scope(stmt, RuntimeSyncStateRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeSyncStateRow.project_id == project_id)
        values = [value for value in (await self._session.execute(stmt)).scalars().all() if value]
        if not values:
            return None
        return _as_utc(max(values))

    async def count_executions(self, scope: ProjectScope, params: ListExecutionsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(RuntimeExecutionRow.source_key, RuntimeExecutionRow.execution_id)
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        stmt = self._apply_execution_filters(stmt, params)
        deduped = stmt.distinct().subquery()
        return int((await self._session.execute(select(func.count()).select_from(deduped))).scalar_one())

    async def count_failures(self, scope: ProjectScope, params: DetectFailuresInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeFailureRow)
        stmt = self._apply_scope(stmt, RuntimeFailureRow.project_id, scope)
        if params.since:
            stmt = stmt.where(RuntimeFailureRow.failed_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeFailureRow.failed_at <= params.until)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_execution_logs(self, scope: ProjectScope, params: GetExecutionLogsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeLogEventRow).where(RuntimeLogEventRow.execution_id == params.execution_id)
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        if params.min_severity is not None:
            stmt = stmt.where(RuntimeLogEventRow.severity.in_(_severities_at_or_above(params.min_severity)))
        return int((await self._session.execute(stmt)).scalar_one())

    async def prune_expired_log_events(self, *, now: datetime.datetime | None = None, retention_days: int = 90) -> int:
        cutoff = (now or datetime.datetime.now(datetime.UTC)) - datetime.timedelta(days=retention_days)
        result = await self._session.execute(delete(RuntimeLogEventRow).where(RuntimeLogEventRow.timestamp < cutoff))
        await self._session.flush()
        return int(result.rowcount or 0)

    async def search_log_events(
        self, scope: ProjectScope, *, query: str, project_id: str | None = None,
        execution_id: str | None = None, since: datetime.datetime | None = None,
        until: datetime.datetime | None = None, limit: int = 20, snippet_chars: int = 300,
    ) -> list[dict[str, object]]:
        """Search redacted raw logs with bounded snippets; scope is always server-derived.

        A blank ``query`` is the RECENCY mode: newest-first within the other
        filters, no text predicate. "Show me the latest logs" / "what happened
        overnight" are the natural questions and were previously unanswerable --
        every path required a keyword, so the request came back empty and
        indistinguishable from "this project has no logs". Verified against a
        173,537-row corpus: ``plainto_tsquery('simple', 'latest logs')`` matched
        zero rows.
        """
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeLogEventRow)
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeLogEventRow.project_id == project_id)
        if execution_id is not None:
            stmt = stmt.where(RuntimeLogEventRow.execution_id == execution_id)
        if since is not None:
            stmt = stmt.where(RuntimeLogEventRow.timestamp >= since)
        if until is not None:
            stmt = stmt.where(RuntimeLogEventRow.timestamp <= until)
        dialect = self._session.bind.dialect.name if self._session.bind is not None else ""
        if not query.strip():
            stmt = stmt.order_by(
                RuntimeLogEventRow.timestamp.desc(), RuntimeLogEventRow.id.desc()
            )
        elif dialect == "postgresql":
            vector = func.to_tsvector("simple", RuntimeLogEventRow.message)
            tsquery = func.plainto_tsquery("simple", query)
            # Token-wise substring match alongside the full-text predicate.
            # to_tsvector('simple') keeps a dotted Java FQN as ONE token, so
            # 'java.lang.NullPointerException' is a single lexeme and searching
            # for the bare class name matches nothing: measured 0 tsquery hits
            # against 30 rows containing it. The trigram fallback cannot rescue
            # that either -- similarity() of a short query against a long log
            # line falls under the default 0.3 threshold. Substring matching per
            # token finds the class name wherever it is embedded, which is the
            # main thing anyone searches logs for.
            predicate = vector.op("@@")(tsquery) | RuntimeLogEventRow.message.op("%")(query)
            for token in _log_search_tokens(query):
                predicate = predicate | RuntimeLogEventRow.message.ilike(
                    f"%{token}%", escape="\\"
                )
            stmt = stmt.where(predicate).order_by(
                func.ts_rank_cd(vector, tsquery).desc(),
                func.similarity(RuntimeLogEventRow.message, query).desc(),
                RuntimeLogEventRow.timestamp.desc(),
                RuntimeLogEventRow.id.desc(),
            )
        else:
            predicate = RuntimeLogEventRow.message.ilike(
                f"%{_like_escape(query)}%", escape="\\"
            )
            for token in _log_search_tokens(query):
                predicate = predicate | RuntimeLogEventRow.message.ilike(
                    f"%{token}%", escape="\\"
                )
            stmt = stmt.where(predicate).order_by(
                RuntimeLogEventRow.timestamp.desc(), RuntimeLogEventRow.id.desc()
            )
        rows = (
            await self._session.execute(stmt.limit(min(max(limit * 5, limit), 500)))
        ).scalars().all()
        results: dict[tuple[str, str, str, str], dict[str, object]] = {}
        for row in rows:
            safe_message = redact_credentials(row.message)
            key = (row.project_id, row.execution_id, row.severity, safe_message)
            existing = results.get(key)
            if existing is not None:
                existing["occurrence_count"] = int(existing["occurrence_count"]) + 1
                continue
            results[key] = {
                "project_id": row.project_id,
                "execution_id": row.execution_id,
                "timestamp": _as_utc(row.timestamp),
                "severity": row.severity,
                "line_ordinal": row.line_ordinal,
                "snippet": safe_message[:snippet_chars],
                "occurrence_count": 1,
            }
        return list(results.values())[:limit]

    async def list_sync_states(
        self,
        scope: ProjectScope,
        *,
        source_key: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeSyncState]:
        """Return scoped source/project sync freshness and health rows."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSyncStateRow)
        stmt = self._apply_scope(stmt, RuntimeSyncStateRow.project_id, scope)
        if source_key is not None:
            stmt = stmt.where(RuntimeSyncStateRow.source_key == source_key)
        if project_id is not None:
            stmt = stmt.where(RuntimeSyncStateRow.project_id == project_id)
        stmt = stmt.order_by(RuntimeSyncStateRow.updated_at.desc(), RuntimeSyncStateRow.id.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_sync_state(row) for row in rows]

    async def list_health_summaries(
        self,
        scope: ProjectScope,
        *,
        project_id: str | None = None,
        source_key: str | None = None,
        limit: int = 50,
    ) -> list[RuntimeHealthSummary]:
        """Return DB-derived health rollups grouped by project/source/job."""
        if self._denies_all(scope):
            return []
        exec_stmt = select(RuntimeExecutionRow)
        exec_stmt = self._apply_scope(exec_stmt, RuntimeExecutionRow.project_id, scope)
        if project_id is not None:
            exec_stmt = exec_stmt.where(RuntimeExecutionRow.project_id == project_id)
        if source_key is not None:
            exec_stmt = exec_stmt.where(RuntimeExecutionRow.source_key == source_key)
        exec_rows = list((await self._session.execute(exec_stmt)).scalars().all())
        if not exec_rows:
            return []

        metric_stmt = select(RuntimeComponentMetricRow)
        metric_stmt = self._apply_scope(metric_stmt, RuntimeComponentMetricRow.project_id, scope)
        if project_id is not None:
            metric_stmt = metric_stmt.where(RuntimeComponentMetricRow.project_id == project_id)
        if source_key is not None:
            metric_stmt = metric_stmt.where(RuntimeComponentMetricRow.source_key == source_key)
        metric_rows = list((await self._session.execute(metric_stmt)).scalars().all())

        incident_stmt = select(RuntimeIncidentRow)
        incident_stmt = self._apply_scope(incident_stmt, RuntimeIncidentRow.project_id, scope)
        if project_id is not None:
            incident_stmt = incident_stmt.where(RuntimeIncidentRow.project_id == project_id)
        incident_rows = list((await self._session.execute(incident_stmt)).scalars().all())

        exec_by_id = {(r.project_id, r.execution_id): r for r in exec_rows}
        grouped: dict[tuple[str, str, str], list[RuntimeExecutionRow]] = defaultdict(list)
        for row in exec_rows:
            grouped[(row.project_id, row.source_key, row.job_name)].append(row)

        metrics_by_group: dict[tuple[str, str, str], list[RuntimeComponentMetricRow]] = defaultdict(list)
        for metric in metric_rows:
            exec_row = exec_by_id.get((metric.project_id, metric.execution_id))
            if exec_row is None:
                continue
            metrics_by_group[(exec_row.project_id, exec_row.source_key, exec_row.job_name)].append(metric)

        incidents_by_group: dict[tuple[str, str, str], list[RuntimeIncidentRow]] = defaultdict(list)
        for incident in incident_rows:
            exec_row = exec_by_id.get((incident.project_id, incident.execution_id))
            if exec_row is None:
                continue
            incidents_by_group[(exec_row.project_id, exec_row.source_key, exec_row.job_name)].append(incident)

        summaries = [
            _to_health_summary(
                project_id=pid,
                source_key=src,
                job_name=job,
                executions=rows,
                metrics=metrics_by_group.get((pid, src, job), []),
                incidents=incidents_by_group.get((pid, src, job), []),
            )
            for (pid, src, job), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.recurrence_count or 0,
                item.last_failure_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[:limit]

    async def list_sources(
        self,
        scope: ProjectScope,
        *,
        source_key: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeSource]:
        """Return scoped runtime source inventory rows."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSourceRow)
        stmt = self._apply_scope(stmt, RuntimeSourceRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeSourceRow.project_id == project_id)
        if source_key is not None:
            stmt = stmt.where(RuntimeSourceRow.source_key == source_key)
        stmt = stmt.order_by(RuntimeSourceRow.last_seen_at.desc().nullslast(), RuntimeSourceRow.id.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_source(row) for row in rows]

    async def list_mirrored_environments(
        self, scope: ProjectScope, project_id: str
    ) -> list[RuntimeMirroredEnvironment]:
        """Environments present in this project's mirror, busiest first.

        Built from the environment inventory AND from observed job runs, then
        merged. Neither alone is sufficient: the inventory can list an
        environment that has never produced a run in the retained window, and a
        run can carry an environment the inventory sweep has not caught up with.
        A picker driven by only one would offer a dead choice or hide a live one.
        """
        if self._denies_all(scope):
            return []
        inventory = select(
            RuntimeEnvironmentRow.environment_id, RuntimeEnvironmentRow.name
        ).where(RuntimeEnvironmentRow.project_id == project_id)
        inventory = self._apply_scope(inventory, RuntimeEnvironmentRow.project_id, scope)

        observed = (
            select(
                RuntimeJobRunRow.environment_id,
                func.max(RuntimeJobRunRow.environment_name).label("name"),
                func.count().label("run_count"),
                func.max(RuntimeJobRunRow.started_at).label("last_run_at"),
            )
            .where(RuntimeJobRunRow.project_id == project_id)
            .where(RuntimeJobRunRow.environment_id.is_not(None))
            .group_by(RuntimeJobRunRow.environment_id)
        )
        observed = self._apply_scope(observed, RuntimeJobRunRow.project_id, scope)

        merged: dict[str, RuntimeMirroredEnvironment] = {}
        for environment_id, name in (await self._session.execute(inventory)).all():
            if environment_id:
                merged[environment_id] = RuntimeMirroredEnvironment(
                    environment_id=environment_id, name=name or environment_id
                )
        for environment_id, name, run_count, last_run_at in (
            await self._session.execute(observed)
        ).all():
            entry = merged.get(environment_id)
            if entry is None:
                merged[environment_id] = RuntimeMirroredEnvironment(
                    environment_id=environment_id,
                    name=name or environment_id,
                    run_count=run_count,
                    last_run_at=_as_utc(last_run_at),
                )
            else:
                entry.run_count = run_count
                entry.last_run_at = _as_utc(last_run_at)
        return sorted(
            merged.values(), key=lambda item: (-item.run_count, item.name.lower())
        )

    async def resolve_doc_read_scope(
        self, scope: ProjectScope, project_id: str, *, default_window_days: int
    ) -> RuntimeDocReadScope:
        """The documentation read scope configured for a project, if any.

        Read scope is stored in each source's non-secret config, which lives in
        the credential blob -- this connector framework's per-source config
        store, and where ingest scope, filters and history windows already sit.
        Reading it therefore costs a decrypt; that is why it is resolved ONCE
        per doc-evidence request rather than per sub-query.

        A project with several sources (the two-spelling TMC case) unions their
        environment selections and takes the widest window: a configured source
        can only ever narrow ITS OWN contribution, so combining by intersection
        would let one source's narrow scope silently suppress another's data.
        """
        if self._denies_all(scope):
            return RuntimeDocReadScope(window_days=default_window_days)
        # Selected by row id, not through `list_sources`: that returns the
        # id-less inventory model, and resolving back to an id by
        # (project, type) would silently collapse two sources of one type.
        id_stmt = select(RuntimeSourceRow.id).where(RuntimeSourceRow.project_id == project_id)
        id_stmt = self._apply_scope(id_stmt, RuntimeSourceRow.project_id, scope)
        source_ids = list((await self._session.execute(id_stmt)).scalars().all())
        if not source_ids:
            return RuntimeDocReadScope(window_days=default_window_days)

        selected: list[str] = []
        window: int | None = None
        configured = False
        for source_id in source_ids:
            try:
                credentials, _key_id = await self.read_credentials(source_id)
            except RuntimeRecordNotFoundError:
                continue
            raw_environments = credentials.get("doc_environment_ids")
            if isinstance(raw_environments, list):
                cleaned = [str(item).strip() for item in raw_environments if str(item).strip()]
                if cleaned:
                    configured = True
                    selected.extend(item for item in cleaned if item not in selected)
            raw_window = credentials.get("doc_window_days")
            if isinstance(raw_window, (int, str)):
                try:
                    parsed = int(raw_window)
                except (TypeError, ValueError):
                    parsed = 0
                if parsed > 0:
                    configured = True
                    window = parsed if window is None else max(window, parsed)

        if not selected:
            return RuntimeDocReadScope(
                window_days=window or default_window_days, configured=configured
            )
        mirrored = {item.environment_id: item.name for item in
                    await self.list_mirrored_environments(scope, project_id)}
        return RuntimeDocReadScope(
            environment_ids=selected,
            environment_names=[mirrored[item] for item in selected if item in mirrored],
            unavailable_environment_ids=[item for item in selected if item not in mirrored],
            window_days=window or default_window_days,
            configured=configured,
        )

    # --- source management (credentials never returned) ---

    async def create_source_config(
        self,
        *,
        project_id: str,
        source_type: str,
        label: str,
        physical_identity: str | None = None,
        sync_interval_seconds: int = 60,
    ) -> RuntimeSourceRead:
        if source_type == "tmc":
            existing_tmc = await self.find_source_id_by_project_and_type(project_id, source_type)
            if existing_tmc is not None:
                raise RuntimeDuplicateSourceError(
                    "This project already has a TMC source. Edit its ingest scope to add environments."
                )
        physical_identity = physical_identity or f"local:{source_type}:{uuid.uuid4().hex}"
        existing = (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.physical_identity == physical_identity,
        ))).scalar_one_or_none()
        if existing is not None:
            raise RuntimeDuplicateSourceError("That physical runtime source is already registered")
        now = datetime.datetime.now(datetime.UTC)
        source_key = f"{source_type}-{uuid.uuid4().hex[:12]}"
        row = RuntimeSourceRow(
            project_id=project_id,
            source_key=source_key,
            source_type=source_type,
            physical_identity=physical_identity,
            label=label,
            status=RuntimeSyncStatus.SUCCESS.value,
            enabled=True,
            sync_interval_seconds=max(sync_interval_seconds, 30),
            auto_disable_threshold=3,
            is_demo=1 if source_type == "mock" else 0,
            ingested_at=now,
            last_seen_at=now,
        )
        try:
            # The partial unique index is the concurrency backstop. A savepoint
            # keeps the caller's transaction usable when two registrations race.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
                health = RuntimeSourceHealthRow(source_id=row.id)
                self._session.add(health)
                await self._session.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc, "orig", None), "diag", None)
            constraint_name = getattr(constraint, "constraint_name", None)
            sqlite_message = str(getattr(exc, "orig", exc))
            is_project_tmc_collision = constraint_name == "uq_runtime_sources_project_tmc" or (
                source_type == "tmc"
                and "UNIQUE constraint failed: runtime_sources.project_id" in sqlite_message
            )
            if is_project_tmc_collision:
                raise RuntimeDuplicateSourceError(
                    "This project already has a TMC source. Edit its ingest scope to add environments."
                ) from exc
            raise
        return _to_source_read(row, health)

    async def get_source_config(self, source_id: int, scope: ProjectScope) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, scope)
        health = await self._get_source_health_row(row.id)
        return _to_source_read(row, health)

    async def promote_source_identity(self, source_id: int, physical_identity: str) -> None:
        """Replace a migration-era legacy identity after credentials are verified."""
        row = await self._get_source_row(source_id, None)
        collision = (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.physical_identity == physical_identity,
            RuntimeSourceRow.id != source_id,
        ))).scalar_one_or_none()
        if collision is not None:
            raise RuntimeDuplicateSourceError("That physical runtime source is already registered")
        row.physical_identity = physical_identity
        await self._session.flush()

    async def get_source_physical_identity(self, source_id: int) -> str:
        return (await self._get_source_row(source_id, None)).physical_identity

    async def find_source_id_by_physical_identity(self, physical_identity: str) -> int | None:
        return (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.physical_identity == physical_identity,
        ))).scalar_one_or_none()

    async def find_source_id_by_project_and_type(
        self, project_id: str, source_type: str
    ) -> int | None:
        return (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.project_id == project_id,
            RuntimeSourceRow.source_type == source_type,
        ))).scalar_one_or_none()

    async def list_unresolved_identity_source_ids(
        self, source_types: set[str], endpoint_identity: str | None = None
    ) -> list[int]:
        """Return sources whose stored identity predates the current identity rule.

        Two generations qualify: migration-era rows the migration could not
        decrypt (``legacy:``), and rows written when the identity was the bare
        endpoint, before it was narrowed to a tenant. Both must be re-resolved
        from their own credentials before a new registration can be judged a
        duplicate, otherwise a second tenant on the same endpoint is rejected
        for colliding with the first one registered.
        """
        if not source_types:
            return []
        conditions = [RuntimeSourceRow.physical_identity.startswith("legacy:")]
        if endpoint_identity:
            conditions.append(RuntimeSourceRow.physical_identity == endpoint_identity)
        return list((await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.source_type.in_(source_types),
            or_(*conditions),
        ))).scalars().all())

    async def list_source_configs(
        self,
        scope: ProjectScope,
        *,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeSourceRead]:
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSourceRow)
        stmt = self._apply_scope(stmt, RuntimeSourceRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeSourceRow.project_id == project_id)
        stmt = stmt.order_by(RuntimeSourceRow.ingested_at.desc(), RuntimeSourceRow.id.desc()).limit(limit)
        rows = list((await self._session.execute(stmt)).scalars().all())
        health_rows = {
            row.source_id: row
            for row in (
                await self._session.execute(
                    select(RuntimeSourceHealthRow).where(
                        RuntimeSourceHealthRow.source_id.in_([row.id for row in rows] or [-1])
                    )
                )
            ).scalars().all()
        }
        return [_to_source_read(row, health_rows.get(row.id)) for row in rows]

    async def list_pollable_sources(self) -> list[RuntimeSourceRead]:
        """Return enabled source configs that have stored credentials (system scope).

        This is the set the automatic polling scheduler is allowed to sync. It
        deliberately excludes discovered/mirror source rows written by sync
        batches (via ``upsert_source``): those live in the same table but carry
        no credentials row, so polling them would only produce FAILED cycles and
        eventually auto-disable them.
        """
        credentialed = select(RuntimeSourceCredentialsRow.source_id)
        stmt = (
            select(RuntimeSourceRow)
            .where(RuntimeSourceRow.enabled.is_(True))
            .where(RuntimeSourceRow.id.in_(credentialed))
            .order_by(RuntimeSourceRow.id)
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        health_rows = {
            row.source_id: row
            for row in (
                await self._session.execute(
                    select(RuntimeSourceHealthRow).where(
                        RuntimeSourceHealthRow.source_id.in_([row.id for row in rows] or [-1])
                    )
                )
            ).scalars().all()
        }
        return [_to_source_read(row, health_rows.get(row.id)) for row in rows]

    async def update_source_config(
        self,
        source_id: int,
        scope: ProjectScope,
        update: RuntimeSourceUpdate,
    ) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, scope)
        if update.label is not None:
            row.label = update.label
        if update.enabled is not None:
            row.enabled = update.enabled
            row.disabled_reason = None if update.enabled else "manual"
            if update.enabled:
                health = await self._get_source_health_row(row.id)
                if health is not None:
                    health.consecutive_failures = 0
        if update.sync_interval_seconds is not None:
            row.sync_interval_seconds = max(update.sync_interval_seconds, 30)
        await self._session.flush()
        return _to_source_read(row, await self._get_source_health_row(row.id))

    async def delete_source_config(self, source_id: int, scope: ProjectScope) -> None:
        row = await self._get_source_row(source_id, scope)
        await self._session.execute(delete(RuntimeSourceCredentialsRow).where(RuntimeSourceCredentialsRow.source_id == row.id))
        await self._session.execute(delete(RuntimeSourceHealthRow).where(RuntimeSourceHealthRow.source_id == row.id))
        await self._session.execute(delete(RuntimeWorkspaceProjectMapRow).where(RuntimeWorkspaceProjectMapRow.source_id == row.id))
        await self._session.delete(row)
        await self._session.flush()

    async def add_container_mapping(
        self,
        *,
        source_id: int,
        container_key: str,
        container_name: str | None,
        project_id: str,
        created_by: str | None,
    ) -> RuntimeWorkspaceProjectMap:
        now = datetime.datetime.now(datetime.UTC)
        stmt = select(RuntimeWorkspaceProjectMapRow).where(
            RuntimeWorkspaceProjectMapRow.source_id == source_id,
            RuntimeWorkspaceProjectMapRow.container_key == container_key,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeWorkspaceProjectMapRow(
                source_id=source_id,
                container_key=container_key,
                created_at=now,
                created_by=created_by,
            )
            self._session.add(row)
        row.container_name = container_name
        row.project_id = project_id
        await self._session.flush()
        return _to_workspace_project_map(row)

    async def list_mappings(self, source_id: int) -> list[RuntimeWorkspaceProjectMap]:
        rows = (
            await self._session.execute(
                select(RuntimeWorkspaceProjectMapRow)
                .where(RuntimeWorkspaceProjectMapRow.source_id == source_id)
                .order_by(RuntimeWorkspaceProjectMapRow.container_key.asc())
            )
        ).scalars().all()
        return [_to_workspace_project_map(row) for row in rows]

    async def delete_mapping(self, source_id: int, container_key: str) -> None:
        await self._session.execute(
            delete(RuntimeWorkspaceProjectMapRow).where(
                RuntimeWorkspaceProjectMapRow.source_id == source_id,
                RuntimeWorkspaceProjectMapRow.container_key == container_key,
            )
        )
        await self._session.flush()

    async def resolve_project_id(self, source_id: int, container_key: str | None) -> tuple[str, bool]:
        source = await self._get_source_row(source_id, None)
        if container_key:
            row = (
                await self._session.execute(
                    select(RuntimeWorkspaceProjectMapRow).where(
                        RuntimeWorkspaceProjectMapRow.source_id == source_id,
                        RuntimeWorkspaceProjectMapRow.container_key == container_key,
                    )
                )
            ).scalars().first()
            if row is not None:
                return row.project_id, False
        return source.project_id, True

    async def write_credentials(
        self,
        *,
        source_id: int,
        plaintext_payload: dict,
        credential_type: str,
        rotated_by: str | None,
    ) -> None:
        now = datetime.datetime.now(datetime.UTC)
        payload = json.dumps(plaintext_payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        key_id = current_key_id()
        encrypted_payload = encrypt(payload, key_id)
        stmt = select(RuntimeSourceCredentialsRow).where(RuntimeSourceCredentialsRow.source_id == source_id)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSourceCredentialsRow(
                source_id=source_id,
                credential_type=credential_type,
                created_at=now,
            )
            self._session.add(row)
        row.encrypted_payload = encrypted_payload
        row.encryption_key_id = key_id
        row.rotated_at = now
        row.rotated_by = rotated_by
        await self._session.flush()

    async def read_credentials(self, source_id: int) -> tuple[dict, str]:
        stmt = select(RuntimeSourceCredentialsRow).where(RuntimeSourceCredentialsRow.source_id == source_id)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise RuntimeRecordNotFoundError(f"Credentials not found for runtime source {source_id}")
        result = decrypt_and_rotate_if_needed(row.encrypted_payload, row.encryption_key_id)
        if result.rotated:
            row.encrypted_payload = result.ciphertext
            row.encryption_key_id = result.encryption_key_id
            row.rotated_at = datetime.datetime.now(datetime.UTC)
            await self._session.flush()
        return json.loads(result.plaintext.decode("utf-8")), row.encryption_key_id

    async def _get_source_row(self, source_id: int, scope: ProjectScope) -> RuntimeSourceRow:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Runtime source not found: {source_id}")
        stmt = select(RuntimeSourceRow).where(RuntimeSourceRow.id == source_id)
        stmt = self._apply_scope(stmt, RuntimeSourceRow.project_id, scope)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise RuntimeRecordNotFoundError(f"Runtime source not found: {source_id}")
        return row

    async def _get_source_health_row(self, source_id: int) -> RuntimeSourceHealthRow | None:
        return (
            await self._session.execute(
                select(RuntimeSourceHealthRow).where(RuntimeSourceHealthRow.source_id == source_id)
            )
        ).scalars().first()

    async def record_source_sync_attempt(self, source_id: int, attempted_at: datetime.datetime) -> None:
        health = await self._ensure_source_health_row(source_id)
        health.last_attempt_at = attempted_at
        await self._session.flush()

    async def record_source_sync_success(
        self,
        source_id: int,
        *,
        succeeded_at: datetime.datetime,
        records_ingested: int,
    ) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, None)
        health = await self._ensure_source_health_row(source_id)
        health.last_success_at = succeeded_at
        health.consecutive_failures = 0
        health.last_failure_sanitized = None
        health.total_cycles += 1
        health.total_records_ingested += records_ingested
        row.last_synced_at = succeeded_at
        row.last_seen_at = succeeded_at
        row.status = RuntimeSyncStatus.SUCCESS.value
        row.enabled = True
        row.disabled_reason = None
        await self._session.flush()
        return _to_source_read(row, health)

    async def record_source_sync_failure(
        self,
        source_id: int,
        *,
        failed_at: datetime.datetime,
        sanitized_error: str | None,
    ) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, None)
        health = await self._ensure_source_health_row(source_id)
        health.last_failure_at = failed_at
        health.consecutive_failures += 1
        health.last_failure_sanitized = sanitized_error
        health.total_cycles += 1
        row.status = RuntimeSyncStatus.FAILED.value
        if health.consecutive_failures >= row.auto_disable_threshold:
            row.enabled = False
            row.disabled_reason = "auto-disabled"
        await self._session.flush()
        return _to_source_read(row, health)

    async def _ensure_source_health_row(self, source_id: int) -> RuntimeSourceHealthRow:
        row = await self._get_source_health_row(source_id)
        if row is None:
            row = RuntimeSourceHealthRow(source_id=source_id)
            self._session.add(row)
            await self._session.flush()
        return row

    async def list_engines(
        self,
        scope: ProjectScope,
        params: ListRuntimeEnginesInput,
    ) -> list[RuntimeEngine]:
        """Return scoped runtime engines with source/environment/workspace labels."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeEngineRow)
        stmt = self._apply_scope(stmt, RuntimeEngineRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeEngineRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            stmt = stmt.where(RuntimeEngineRow.status == params.status.value)
        stmt = stmt.order_by(RuntimeEngineRow.name.asc(), RuntimeEngineRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_engine(row) for row in rows]

    async def count_engines(
        self, scope: ProjectScope, params: ListRuntimeEnginesInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeEngineRow)
        stmt = self._apply_scope(stmt, RuntimeEngineRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeEngineRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            stmt = stmt.where(RuntimeEngineRow.status == params.status.value)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_engine_health(
        self,
        scope: ProjectScope,
        params: ListRuntimeEnginesInput,
    ) -> list[RuntimeEngineHealth]:
        """Return DB-derived health grouped by runtime engine."""
        if self._denies_all(scope):
            return []
        run_stmt = select(RuntimeJobRunRow)
        run_stmt = self._apply_scope(run_stmt, RuntimeJobRunRow.project_id, scope)
        engine_stmt = select(RuntimeEngineRow)
        engine_stmt = self._apply_scope(engine_stmt, RuntimeEngineRow.project_id, scope)

        if params.project_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.project_id == params.project_id)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.project_id == params.project_id)
        if params.source_key is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.source_key == params.source_key)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            engine_stmt = engine_stmt.where(RuntimeEngineRow.status == params.status.value)

        run_rows = list((await self._session.execute(run_stmt)).scalars().all())
        engine_rows = list((await self._session.execute(engine_stmt)).scalars().all())
        engines_by_key = {
            (row.project_id, row.source_key, row.engine_id): row
            for row in engine_rows
        }
        allowed_engine_keys = set(engines_by_key)

        grouped: dict[tuple[str, str, str], list[RuntimeJobRunRow]] = defaultdict(list)
        for row in run_rows:
            if row.engine_id is None:
                continue
            key = (row.project_id, row.source_key, row.engine_id)
            if allowed_engine_keys and key not in allowed_engine_keys:
                continue
            grouped[key].append(row)

        summaries = [
            _to_engine_health(
                project_id=pid,
                source_key=source_key,
                engine_id=engine_id,
                runs=rows,
                engine=engines_by_key.get((pid, source_key, engine_id)),
            )
            for (pid, source_key, engine_id), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.latest_run_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[: params.limit]

    async def list_jobs(
        self,
        scope: ProjectScope,
        params: ListRuntimeJobsInput,
    ) -> list[RuntimeJob]:
        """Return scoped current-state runtime jobs/tasks."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeJobRow)
        stmt = self._apply_scope(stmt, RuntimeJobRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimeJobRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeJobRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRow.engine_id == params.engine_id)
        if params.status is not None:
            stmt = stmt.where(RuntimeJobRow.status == params.status)
        stmt = stmt.order_by(RuntimeJobRow.name.asc(), RuntimeJobRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_job(row) for row in rows]

    async def count_jobs(self, scope: ProjectScope, params: ListRuntimeJobsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeJobRow)
        stmt = self._apply_scope(stmt, RuntimeJobRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimeJobRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeJobRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRow.engine_id == params.engine_id)
        if params.status is not None:
            stmt = stmt.where(RuntimeJobRow.status == params.status)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_artifacts(
        self,
        scope: ProjectScope,
        params: ListRuntimeArtifactsInput,
    ) -> list[RuntimeArtifact]:
        """Return optional artifact/package metadata from the runtime mirror."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeArtifactRow)
        stmt = self._apply_scope(stmt, RuntimeArtifactRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeArtifactRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeArtifactRow.source_key == params.source_key)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeArtifactRow.workspace_id == params.workspace_id)
        if params.artifact_type is not None:
            stmt = stmt.where(RuntimeArtifactRow.artifact_type == params.artifact_type)
        stmt = stmt.order_by(RuntimeArtifactRow.name.asc(), RuntimeArtifactRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_artifact(row) for row in rows]

    async def list_connections(
        self,
        scope: ProjectScope,
        params: ListRuntimeConnectionsInput,
    ) -> list[RuntimeConnection]:
        """Return optional connection metadata without parameters or credentials."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeConnectionRow)
        stmt = self._apply_scope(stmt, RuntimeConnectionRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeConnectionRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeConnectionRow.source_key == params.source_key)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeConnectionRow.workspace_id == params.workspace_id)
        if params.connection_type is not None:
            stmt = stmt.where(RuntimeConnectionRow.connection_type == params.connection_type)
        stmt = stmt.order_by(RuntimeConnectionRow.name.asc(), RuntimeConnectionRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_connection(row) for row in rows]

    async def list_job_dependencies(
        self,
        scope: ProjectScope,
        params: ListRuntimeDependenciesInput,
    ) -> list[RuntimeJobDependency]:
        """Return job-to-artifact/connection dependency edges."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeJobDependencyRow)
        stmt = self._apply_scope(stmt, RuntimeJobDependencyRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.source_key == params.source_key)
        if params.dependency_type is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.dependency_type == params.dependency_type)
        if params.dependency_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.dependency_id == params.dependency_id)
        if params.job_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.job_id == params.job_id)
        stmt = stmt.order_by(RuntimeJobDependencyRow.job_name.asc(), RuntimeJobDependencyRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_job_dependency(row) for row in rows]

    async def get_dependency_summary(
        self,
        scope: ProjectScope,
        params: ListRuntimeInventoryInput,
    ) -> RuntimeDependencySummary:
        """Return availability and hygiene facts for optional extended metadata."""
        artifacts = await self.list_artifacts(
            scope,
            ListRuntimeArtifactsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        connections = await self.list_connections(
            scope,
            ListRuntimeConnectionsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        dependencies = await self.list_job_dependencies(
            scope,
            ListRuntimeDependenciesInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        return RuntimeDependencySummary(
            availability=RuntimeExtendedMetadataAvailability(
                artifacts_supported=bool(artifacts),
                connections_supported=bool(connections),
                schedules_supported=False,
                promotions_supported=False,
                clusters_supported=False,
                unavailable=["schedules", "promotions", "clusters"],
            ),
            artifacts_total=len(artifacts),
            connections_total=len(connections),
            dependencies_total=len(dependencies),
            stale_artifacts=[item for item in artifacts if item.source_updated_at is None][: params.limit],
            unowned_artifacts=[item for item in artifacts if not item.owner][: params.limit],
            unowned_connections=[item for item in connections if not item.owner][: params.limit],
        )

    async def list_job_runs(
        self,
        scope: ProjectScope,
        params: ListRuntimeJobRunsInput,
    ) -> list[RuntimeJobRun]:
        """Return scoped runtime job/task run facts."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeJobRunRow)
        stmt = self._apply_scope(stmt, RuntimeJobRunRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRunRow.source_key == params.source_key)
        if params.job_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.job_id == params.job_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.engine_id == params.engine_id)
        if params.state is not None:
            stmt = stmt.where(RuntimeJobRunRow.state == params.state.value)
        if params.since is not None:
            stmt = stmt.where(RuntimeJobRunRow.started_at >= params.since)
        if params.until is not None:
            stmt = stmt.where(RuntimeJobRunRow.started_at <= params.until)
        stmt = stmt.order_by(RuntimeJobRunRow.started_at.desc(), RuntimeJobRunRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_job_run(row) for row in rows]

    async def list_job_health(
        self,
        scope: ProjectScope,
        params: ListRuntimeJobsInput,
    ) -> list[RuntimeJobHealth]:
        """Return DB-derived health grouped by runtime job."""
        if self._denies_all(scope):
            return []
        run_stmt = select(RuntimeJobRunRow)
        run_stmt = self._apply_scope(run_stmt, RuntimeJobRunRow.project_id, scope)
        job_stmt = select(RuntimeJobRow)
        job_stmt = self._apply_scope(job_stmt, RuntimeJobRow.project_id, scope)

        if params.project_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.project_id == params.project_id)
            job_stmt = job_stmt.where(RuntimeJobRow.project_id == params.project_id)
        if params.source_key is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.source_key == params.source_key)
            job_stmt = job_stmt.where(RuntimeJobRow.source_key == params.source_key)
        if params.environment_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.environment_id == params.environment_id)
            job_stmt = job_stmt.where(RuntimeJobRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.workspace_id == params.workspace_id)
            job_stmt = job_stmt.where(RuntimeJobRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.engine_id == params.engine_id)
            job_stmt = job_stmt.where(RuntimeJobRow.engine_id == params.engine_id)

        run_rows = list((await self._session.execute(run_stmt)).scalars().all())
        job_rows = list((await self._session.execute(job_stmt)).scalars().all())
        jobs_by_key = {(row.project_id, row.source_key, row.job_id): row for row in job_rows}

        grouped: dict[tuple[str, str, str], list[RuntimeJobRunRow]] = defaultdict(list)
        for row in run_rows:
            grouped[(row.project_id, row.source_key, row.job_id)].append(row)

        summaries = [
            _to_job_health(
                project_id=pid,
                source_key=source_key,
                job_id=job_id,
                runs=rows,
                job=jobs_by_key.get((pid, source_key, job_id)),
            )
            for (pid, source_key, job_id), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.latest_run_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[: params.limit]

    async def list_plans(
        self,
        scope: ProjectScope,
        params: ListRuntimePlansInput,
    ) -> list[RuntimePlan]:
        """Return scoped current-state runtime plans."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimePlanRow)
        stmt = self._apply_scope(stmt, RuntimePlanRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimePlanRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimePlanRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimePlanRow.workspace_id == params.workspace_id)
        if params.status is not None:
            stmt = stmt.where(RuntimePlanRow.status == params.status)
        stmt = stmt.order_by(RuntimePlanRow.name.asc(), RuntimePlanRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_plan(row) for row in rows]

    async def count_plans(self, scope: ProjectScope, params: ListRuntimePlansInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimePlanRow)
        stmt = self._apply_scope(stmt, RuntimePlanRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimePlanRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimePlanRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimePlanRow.workspace_id == params.workspace_id)
        if params.status is not None:
            stmt = stmt.where(RuntimePlanRow.status == params.status)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_plan_runs(
        self,
        scope: ProjectScope,
        params: ListRuntimePlanRunsInput,
    ) -> list[RuntimePlanRun]:
        """Return scoped runtime plan-run facts."""
        if self._denies_all(scope):
            return []
        stmt = select(RuntimePlanRunRow)
        stmt = self._apply_scope(stmt, RuntimePlanRunRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRunRow.source_key == params.source_key)
        if params.plan_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.plan_id == params.plan_id)
        if params.state is not None:
            stmt = stmt.where(RuntimePlanRunRow.state == params.state.value)
        if params.since is not None:
            stmt = stmt.where(RuntimePlanRunRow.started_at >= params.since)
        if params.until is not None:
            stmt = stmt.where(RuntimePlanRunRow.started_at <= params.until)
        stmt = stmt.order_by(RuntimePlanRunRow.started_at.desc(), RuntimePlanRunRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_plan_run(row) for row in rows]

    async def list_plan_health(
        self,
        scope: ProjectScope,
        params: ListRuntimePlansInput,
    ) -> list[RuntimePlanHealth]:
        """Return DB-derived health grouped by runtime plan."""
        if self._denies_all(scope):
            return []
        run_stmt = select(RuntimePlanRunRow)
        run_stmt = self._apply_scope(run_stmt, RuntimePlanRunRow.project_id, scope)
        plan_stmt = select(RuntimePlanRow)
        plan_stmt = self._apply_scope(plan_stmt, RuntimePlanRow.project_id, scope)

        if params.project_id is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.project_id == params.project_id)
            plan_stmt = plan_stmt.where(RuntimePlanRow.project_id == params.project_id)
        if params.source_key is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.source_key == params.source_key)
            plan_stmt = plan_stmt.where(RuntimePlanRow.source_key == params.source_key)
        if params.environment_id is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.environment_id == params.environment_id)
            plan_stmt = plan_stmt.where(RuntimePlanRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            run_stmt = run_stmt.where(RuntimePlanRunRow.workspace_id == params.workspace_id)
            plan_stmt = plan_stmt.where(RuntimePlanRow.workspace_id == params.workspace_id)

        run_rows = list((await self._session.execute(run_stmt)).scalars().all())
        plan_rows = list((await self._session.execute(plan_stmt)).scalars().all())
        plans_by_key = {(row.project_id, row.source_key, row.plan_id): row for row in plan_rows}

        grouped: dict[tuple[str, str, str], list[RuntimePlanRunRow]] = defaultdict(list)
        for row in run_rows:
            grouped[(row.project_id, row.source_key, row.plan_id)].append(row)

        summaries = [
            _to_plan_health(
                project_id=pid,
                source_key=source_key,
                plan_id=plan_id,
                runs=rows,
                plan=plans_by_key.get((pid, source_key, plan_id)),
            )
            for (pid, source_key, plan_id), rows in grouped.items()
        ]
        summaries.sort(
            key=lambda item: (
                item.failure_rate,
                item.current_failure_streak,
                item.latest_run_at or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            ),
            reverse=True,
        )
        return summaries[: params.limit]

    async def get_estate_overview(
        self,
        scope: ProjectScope,
        params: ListRuntimeInventoryInput,
    ) -> RuntimeEstateOverview:
        """Return compact scoped counts for the runtime operations overview."""
        if self._denies_all(scope):
            return RuntimeEstateOverview(
                project_id=params.project_id,
                sources_total=0,
                sources_failed=0,
                sources_partial=0,
                sources_demo=0,
                engines_total=0,
                engines_online=0,
                engines_degraded=0,
                engines_offline=0,
                engines_unknown=0,
                environments_total=0,
                workspaces_total=0,
            )

        source_stmt = select(RuntimeSourceRow)
        source_stmt = self._apply_scope(source_stmt, RuntimeSourceRow.project_id, scope)
        engine_stmt = select(RuntimeEngineRow)
        engine_stmt = self._apply_scope(engine_stmt, RuntimeEngineRow.project_id, scope)
        env_stmt = select(RuntimeEnvironmentRow)
        env_stmt = self._apply_scope(env_stmt, RuntimeEnvironmentRow.project_id, scope)
        workspace_stmt = select(RuntimeWorkspaceRow)
        workspace_stmt = self._apply_scope(workspace_stmt, RuntimeWorkspaceRow.project_id, scope)

        if params.project_id is not None:
            source_stmt = source_stmt.where(RuntimeSourceRow.project_id == params.project_id)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.project_id == params.project_id)
            env_stmt = env_stmt.where(RuntimeEnvironmentRow.project_id == params.project_id)
            workspace_stmt = workspace_stmt.where(RuntimeWorkspaceRow.project_id == params.project_id)
        if params.source_key is not None:
            source_stmt = source_stmt.where(RuntimeSourceRow.source_key == params.source_key)
            engine_stmt = engine_stmt.where(RuntimeEngineRow.source_key == params.source_key)
            env_stmt = env_stmt.where(RuntimeEnvironmentRow.source_key == params.source_key)
            workspace_stmt = workspace_stmt.where(RuntimeWorkspaceRow.source_key == params.source_key)

        sources = list((await self._session.execute(source_stmt)).scalars().all())
        engines = list((await self._session.execute(engine_stmt)).scalars().all())
        environments = list((await self._session.execute(env_stmt)).scalars().all())
        workspaces = list((await self._session.execute(workspace_stmt)).scalars().all())
        synced_values = [_as_utc(row.last_synced_at) for row in sources if row.last_synced_at]
        engine_health = await self.list_engine_health(
            scope,
            ListRuntimeEnginesInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        job_health = await self.list_job_health(
            scope,
            ListRuntimeJobsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        plan_health = await self.list_plan_health(
            scope,
            ListRuntimePlansInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
            ),
        )
        freshness_cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)

        return RuntimeEstateOverview(
            project_id=params.project_id,
            sources_total=len(sources),
            sources_failed=sum(1 for row in sources if row.status == RuntimeSyncStatus.FAILED.value),
            sources_partial=sum(1 for row in sources if row.status == RuntimeSyncStatus.PARTIAL.value),
            sources_demo=sum(1 for row in sources if row.is_demo),
            engines_total=len(engines),
            engines_online=sum(1 for row in engines if row.status == RuntimeEngineStatus.ONLINE.value),
            engines_degraded=sum(1 for row in engines if row.status == RuntimeEngineStatus.DEGRADED.value),
            engines_offline=sum(1 for row in engines if row.status == RuntimeEngineStatus.OFFLINE.value),
            engines_unknown=sum(
                1
                for row in engines
                if row.status in {RuntimeEngineStatus.UNKNOWN.value, RuntimeEngineStatus.STALE.value}
            ),
            engines_unhealthy=sum(
                1
                for row in engine_health
                if row.failed_runs > 0 or row.current_failure_streak > 0
            ),
            jobs_unhealthy=sum(
                1
                for row in job_health
                if row.failed_runs > 0 or row.current_failure_streak > 0
            ),
            plans_unhealthy=sum(
                1
                for row in plan_health
                if row.failed_runs > 0 or row.current_failure_streak > 0
            ),
            sources_stale=sum(
                1
                for row in sources
                if row.status in {RuntimeSyncStatus.FAILED.value, RuntimeSyncStatus.PARTIAL.value}
                or (row.last_synced_at is not None and _as_utc(row.last_synced_at) < freshness_cutoff)
            ),
            environments_total=len(environments),
            workspaces_total=len(workspaces),
            last_synced_at=max(synced_values) if synced_values else None,
        )

    # --- SQL-computed aggregate rollups ---
    #
    # These replace "fetch every row and reduce in Python" for the surfaces that
    # summarise thousands of runs. Grouping, counting, percentiles and ordering all
    # happen in the database; only the already-aggregated rows cross the boundary.

    def _environment_execution_ids(
        self, project_id: str | None, environment_ids: Sequence[str]
    ):
        """Execution ids whose run was observed in one of these environments.

        `runtime_executions` carries no environment column; `runtime_job_runs`
        does. For a TMC mirror the two are one-to-one -- the provider derives
        each execution FROM a job run (`_execution_from_job_run`) and copies
        `run_id` into `execution_id` -- so the job-run table is the environment
        authority for an execution, joined on that id.

        A run whose provider reported no environment is excluded. That is the
        honest reading of "only PROD": a run that cannot be shown to be PROD is
        not evidence for it.
        """
        stmt = (
            select(RuntimeJobRunRow.execution_id)
            .where(RuntimeJobRunRow.environment_id.in_(list(environment_ids)))
            .where(RuntimeJobRunRow.execution_id.is_not(None))
        )
        if project_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.project_id == project_id)
        return stmt

    def _scoped_execution_base(
        self,
        scope: ProjectScope,
        *,
        project_id: str | None,
        source_key: str | None,
        job_name: str | None,
        since: datetime.datetime | None,
        until: datetime.datetime | None,
        environment_ids: Sequence[str] | None = None,
    ):
        """Scoped, filtered execution rows as a subquery for aggregate composition.

        `environment_ids` narrows the population to runs observed in those
        environments. Because every execution-derived aggregate (reliability,
        streak, duration percentiles) composes over this subquery, applying the
        filter here is what keeps a scoped rollup internally consistent -- a
        per-aggregate filter would let one number describe PROD while its
        neighbour described PROD+TST.
        """
        stmt = select(
            RuntimeExecutionRow.id.label("id"),
            RuntimeExecutionRow.project_id.label("project_id"),
            RuntimeExecutionRow.source_key.label("source_key"),
            RuntimeExecutionRow.execution_id.label("execution_id"),
            RuntimeExecutionRow.job_name.label("job_name"),
            RuntimeExecutionRow.state.label("state"),
            RuntimeExecutionRow.started_at.label("started_at"),
            RuntimeExecutionRow.ended_at.label("ended_at"),
            RuntimeExecutionRow.duration_ms.label("duration_ms"),
        )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeExecutionRow.project_id == project_id)
        if source_key is not None:
            stmt = stmt.where(RuntimeExecutionRow.source_key == source_key)
        if job_name is not None:
            stmt = stmt.where(RuntimeExecutionRow.job_name == job_name)
        if since is not None:
            stmt = stmt.where(RuntimeExecutionRow.started_at >= since)
        if until is not None:
            stmt = stmt.where(RuntimeExecutionRow.started_at <= until)
        if environment_ids is not None:
            stmt = stmt.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(project_id, environment_ids)
                )
            )
        return stmt.subquery()

    @staticmethod
    def _state_count(column, state: RuntimeExecutionState):
        return func.coalesce(func.sum(case((column == state.value, 1), else_=0)), 0)

    def _duration_percentiles(self, base, group_columns: list):
        """Grouped p50/p95 duration subquery over non-null durations."""
        ranked = (
            select(
                *group_columns,
                base.c.duration_ms.label("duration_ms"),
                func.row_number()
                .over(partition_by=group_columns, order_by=base.c.duration_ms.asc())
                .label("rn"),
                func.count().over(partition_by=group_columns).label("cnt"),
            )
            .where(base.c.duration_ms.is_not(None))
            .subquery()
        )
        ranked_groups = [ranked.c[column.name] for column in group_columns]
        return (
            select(
                *ranked_groups,
                _percentile_at_rank(ranked.c.rn, ranked.c.cnt, ranked.c.duration_ms, 50).label("p50"),
                _percentile_at_rank(ranked.c.rn, ranked.c.cnt, ranked.c.duration_ms, 95).label("p95"),
            )
            .group_by(*ranked_groups)
            .subquery()
        )

    async def get_outcome_trend(
        self, scope: ProjectScope, params: RuntimeTrendInput
    ) -> list[RuntimeOutcomeTrendPoint]:
        """Return time-bucketed run outcomes with duration percentiles, newest first."""
        if self._denies_all(scope):
            return []
        base = self._scoped_execution_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            job_name=params.job_name,
            since=params.since,
            until=params.until,
        )
        bucket = _bucket_key_expr(self._dialect, base.c.started_at, params.bucket)
        keyed = select(
            bucket.label("bucket_key"),
            base.c.state.label("state"),
            base.c.duration_ms.label("duration_ms"),
        ).subquery()
        group = [keyed.c.bucket_key]
        percentiles = self._duration_percentiles(keyed, group)
        aggregated = (
            select(
                keyed.c.bucket_key,
                func.count().label("total_runs"),
                self._state_count(keyed.c.state, RuntimeExecutionState.SUCCESS).label("success_runs"),
                self._state_count(keyed.c.state, RuntimeExecutionState.FAILED).label("failed_runs"),
                self._state_count(keyed.c.state, RuntimeExecutionState.RUNNING).label("running_runs"),
                self._state_count(keyed.c.state, RuntimeExecutionState.UNKNOWN).label("unknown_runs"),
                func.max(keyed.c.duration_ms).label("max_duration_ms"),
            )
            .group_by(keyed.c.bucket_key)
            .subquery()
        )
        stmt = (
            select(aggregated, percentiles.c.p50, percentiles.c.p95)
            .join(
                percentiles,
                percentiles.c.bucket_key == aggregated.c.bucket_key,
                isouter=True,
            )
            .order_by(aggregated.c.bucket_key.desc())
            .limit(params.limit)
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        points = [
            RuntimeOutcomeTrendPoint(
                bucket_start=bucket_start,
                total_runs=row["total_runs"],
                success_runs=row["success_runs"],
                failed_runs=row["failed_runs"],
                running_runs=row["running_runs"],
                unknown_runs=row["unknown_runs"],
                failure_rate=_ratio(row["failed_runs"], row["total_runs"]) or 0.0,
                p50_duration_ms=row["p50"],
                p95_duration_ms=row["p95"],
                max_duration_ms=row["max_duration_ms"],
            )
            for row in rows
            if (bucket_start := _parse_bucket_key(row["bucket_key"])) is not None
        ]
        return points

    async def list_job_reliability(
        self, scope: ProjectScope, params: ListRuntimeJobReliabilityInput
    ) -> list[RuntimeJobReliability]:
        """Per-job reliability rollup: outcomes, streak, latency percentiles."""
        if self._denies_all(scope):
            return []
        base = self._scoped_execution_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            job_name=params.job_name,
            since=params.since,
            until=params.until,
            environment_ids=params.environment_ids,
        )
        group = [base.c.project_id, base.c.source_key, base.c.job_name]
        percentiles = self._duration_percentiles(base, group)
        aggregated = (
            select(
                *group,
                func.count().label("total_runs"),
                self._state_count(base.c.state, RuntimeExecutionState.SUCCESS).label("success_runs"),
                self._state_count(base.c.state, RuntimeExecutionState.FAILED).label("failed_runs"),
                func.coalesce(
                    func.sum(case((base.c.ended_at.is_(None), 1), else_=0)), 0
                ).label("unfinished_runs"),
                func.min(base.c.started_at).label("first_run_at"),
                func.max(base.c.started_at).label("last_run_at"),
                func.max(base.c.duration_ms).label("max_duration_ms"),
                # Anchor for the failure streak: every run after the most recent
                # non-failed run is, by definition, part of the current streak.
                func.max(
                    case((base.c.state != RuntimeExecutionState.FAILED.value, base.c.started_at))
                ).label("last_ok_at"),
            )
            .group_by(*group)
            .subquery()
        )
        streak = (
            select(
                base.c.project_id,
                base.c.source_key,
                base.c.job_name,
                func.count().label("current_failure_streak"),
            )
            .join(
                aggregated,
                and_(
                    aggregated.c.project_id == base.c.project_id,
                    aggregated.c.source_key == base.c.source_key,
                    aggregated.c.job_name == base.c.job_name,
                ),
            )
            .where(base.c.state == RuntimeExecutionState.FAILED.value)
            .where(
                (aggregated.c.last_ok_at.is_(None))
                | (base.c.started_at > aggregated.c.last_ok_at)
            )
            .group_by(base.c.project_id, base.c.source_key, base.c.job_name)
            .subquery()
        )
        recent = self._latest_state_subquery(base, group)

        def join_keys(other):
            return and_(
                other.c.project_id == aggregated.c.project_id,
                other.c.source_key == aggregated.c.source_key,
                other.c.job_name == aggregated.c.job_name,
            )

        stmt = (
            select(
                aggregated,
                percentiles.c.p50,
                percentiles.c.p95,
                streak.c.current_failure_streak,
                recent.c.latest_state,
            )
            .join(percentiles, join_keys(percentiles), isouter=True)
            .join(streak, join_keys(streak), isouter=True)
            .join(recent, join_keys(recent), isouter=True)
            .order_by(
                aggregated.c.failed_runs.desc(),
                aggregated.c.last_run_at.desc(),
                aggregated.c.job_name.asc(),
            )
            .offset(params.offset)
            .limit(params.limit)
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        return [
            RuntimeJobReliability(
                project_id=row["project_id"],
                source_key=row["source_key"],
                job_name=row["job_name"],
                total_runs=row["total_runs"],
                success_runs=row["success_runs"],
                failed_runs=row["failed_runs"],
                unfinished_runs=row["unfinished_runs"],
                failure_rate=_ratio(row["failed_runs"], row["total_runs"]) or 0.0,
                current_failure_streak=row["current_failure_streak"] or 0,
                first_run_at=_as_utc(row["first_run_at"]),
                last_run_at=_as_utc(row["last_run_at"]),
                latest_state=(
                    RuntimeExecutionState(row["latest_state"]) if row["latest_state"] else None
                ),
                p50_duration_ms=row["p50"],
                p95_duration_ms=row["p95"],
                max_duration_ms=row["max_duration_ms"],
            )
            for row in rows
        ]

    @staticmethod
    def _latest_state_subquery(base, group_columns: list):
        """Most recent run's state per group, via a recency window rank."""
        ranked = select(
            *group_columns,
            base.c.state.label("state"),
            func.row_number()
            .over(
                partition_by=group_columns,
                order_by=[base.c.started_at.desc(), base.c.id.desc()],
            )
            .label("rn"),
        ).subquery()
        ranked_groups = [ranked.c[column.name] for column in group_columns]
        return (
            select(*ranked_groups, ranked.c.state.label("latest_state"))
            .where(ranked.c.rn == 1)
            .subquery()
        )

    async def count_job_reliability(
        self, scope: ProjectScope, params: ListRuntimeJobReliabilityInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        base = self._scoped_execution_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            job_name=params.job_name,
            since=params.since,
            until=params.until,
        )
        groups = (
            select(base.c.project_id, base.c.source_key, base.c.job_name).distinct().subquery()
        )
        return int((await self._session.execute(select(func.count()).select_from(groups))).scalar_one())

    async def list_job_operational_insights(
        self,
        scope: ProjectScope,
        *,
        project_id: str,
        since: datetime.datetime | None,
        until: datetime.datetime | None,
        limit: int,
    ) -> list[RuntimeJobOperationalInsight]:
        """Return one bounded operational story per job, never raw logs.

        Reliability defines the job population. Diagnostic coverage and one
        dominant useful incident are then joined only for those bounded jobs.
        Grouping includes `source_key`, so identically named jobs from different
        providers cannot contaminate each other's evidence.
        """
        reliability = await self.list_job_reliability(
            scope,
            ListRuntimeJobReliabilityInput(
                project_id=project_id,
                since=since,
                until=until,
                limit=limit,
            ),
        )
        if not reliability:
            return []

        keys = {(row.source_key, row.job_name) for row in reliability}
        key_filter = or_(
            *(
                and_(
                    RuntimeExecutionRow.source_key == source_key,
                    RuntimeExecutionRow.job_name == job_name,
                )
                for source_key, job_name in keys
            )
        )
        log_counts = (
            select(
                RuntimeLogEventRow.project_id.label("project_id"),
                RuntimeLogEventRow.execution_id.label("execution_id"),
                func.count(RuntimeLogEventRow.id).label("log_lines"),
            )
            .where(RuntimeLogEventRow.project_id == project_id)
            .group_by(RuntimeLogEventRow.project_id, RuntimeLogEventRow.execution_id)
            .subquery()
        )
        useful_diagnoses = (
            select(
                RuntimeIncidentRow.project_id.label("project_id"),
                RuntimeIncidentRow.execution_id.label("execution_id"),
            )
            .where(RuntimeIncidentRow.project_id == project_id)
            .where(RuntimeIncidentRow.confidence.in_(("high", "medium")))
            .where(RuntimeIncidentRow.pattern_id.notin_(("generic.unknown", "exception.generic")))
            .distinct()
            .subquery()
        )
        coverage_stmt = (
            select(
                RuntimeExecutionRow.source_key,
                RuntimeExecutionRow.job_name,
                func.count(func.distinct(RuntimeExecutionRow.execution_id)).label("failed"),
                func.count(
                    func.distinct(
                        case(
                            (log_counts.c.execution_id.is_not(None), RuntimeExecutionRow.execution_id)
                        )
                    )
                ).label("logged"),
                func.count(
                    func.distinct(
                        case(
                            (
                                useful_diagnoses.c.execution_id.is_not(None),
                                RuntimeExecutionRow.execution_id,
                            )
                        )
                    )
                ).label("diagnosed"),
                func.count(
                    func.distinct(
                        case(
                            (log_counts.c.log_lines >= 10_000, RuntimeExecutionRow.execution_id)
                        )
                    )
                ).label("ceiling"),
            )
            .outerjoin(
                log_counts,
                and_(
                    log_counts.c.project_id == RuntimeExecutionRow.project_id,
                    log_counts.c.execution_id == RuntimeExecutionRow.execution_id,
                ),
            )
            .outerjoin(
                useful_diagnoses,
                and_(
                    useful_diagnoses.c.project_id == RuntimeExecutionRow.project_id,
                    useful_diagnoses.c.execution_id == RuntimeExecutionRow.execution_id,
                ),
            )
            .where(RuntimeExecutionRow.project_id == project_id)
            .where(RuntimeExecutionRow.state == RuntimeExecutionState.FAILED.value)
            .where(key_filter)
            .group_by(RuntimeExecutionRow.source_key, RuntimeExecutionRow.job_name)
        )
        if since is not None:
            coverage_stmt = coverage_stmt.where(RuntimeExecutionRow.started_at >= since)
        if until is not None:
            coverage_stmt = coverage_stmt.where(RuntimeExecutionRow.started_at <= until)
        coverage_stmt = self._apply_scope(coverage_stmt, RuntimeExecutionRow.project_id, scope)
        coverage = {
            (row.source_key, row.job_name): (
                int(row.failed or 0),
                int(row.logged or 0),
                int(row.diagnosed or 0),
                int(row.ceiling or 0),
            )
            for row in (await self._session.execute(coverage_stmt)).all()
        }

        incident_stmt = (
            select(
                RuntimeExecutionRow.source_key,
                RuntimeExecutionRow.job_name,
                RuntimeIncidentRow.signature_hash,
                RuntimeIncidentRow.pattern_id,
                RuntimeIncidentRow.confidence,
                RuntimeIncidentRow.first_bad_at,
                RuntimeExecutionRow.started_at,
                RuntimeIncidentRow.causal_message,
                RuntimeIncidentRow.root_cause_candidate,
                RuntimeIncidentRow.cause_origin,
                RuntimeIncidentRow.termination_component,
                RuntimeIncidentRow.next_checks,
                RuntimeExecutionSummaryRow.failed_component,
            )
            .join(
                RuntimeIncidentRow,
                and_(
                    RuntimeIncidentRow.project_id == RuntimeExecutionRow.project_id,
                    RuntimeIncidentRow.execution_id == RuntimeExecutionRow.execution_id,
                ),
            )
            .outerjoin(
                RuntimeExecutionSummaryRow,
                and_(
                    RuntimeExecutionSummaryRow.project_id == RuntimeExecutionRow.project_id,
                    RuntimeExecutionSummaryRow.execution_id == RuntimeExecutionRow.execution_id,
                ),
            )
            .where(RuntimeExecutionRow.project_id == project_id)
            .where(key_filter)
            .where(RuntimeIncidentRow.confidence.in_(("high", "medium")))
            .where(RuntimeIncidentRow.pattern_id.notin_(("generic.unknown", "exception.generic")))
        )
        if since is not None:
            incident_stmt = incident_stmt.where(RuntimeExecutionRow.started_at >= since)
        if until is not None:
            incident_stmt = incident_stmt.where(RuntimeExecutionRow.started_at <= until)
        incident_stmt = self._apply_scope(incident_stmt, RuntimeExecutionRow.project_id, scope)

        grouped: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
        for row in (await self._session.execute(incident_stmt)).all():
            key = (str(row.source_key), str(row.job_name))
            signature = str(
                row.signature_hash
                or f"{row.pattern_id}|{row.failed_component}|{row.termination_component}"
            )
            bucket = grouped.setdefault(key, {})
            current = bucket.get(signature)
            if current is None:
                current = {
                    "pattern_id": str(row.pattern_id),
                    "confidence": str(row.confidence),
                    "occurrences": 0,
                    "most_recent_at": _as_utc(row.first_bad_at or row.started_at),
                    "failed_component": row.failed_component,
                    "termination_component": row.termination_component,
                    "causal_message": sanitize_doc_evidence_error(row.causal_message),
                    "root_cause_candidate": sanitize_doc_evidence_error(
                        row.root_cause_candidate
                    ),
                    "cause_origin": row.cause_origin,
                    "next_checks": [
                        check
                        for check in (
                            sanitize_doc_evidence_error(value)
                            for value in (row.next_checks or [])[:3]
                        )
                        if check
                    ],
                }
                bucket[signature] = current
            current["occurrences"] = int(current["occurrences"]) + 1

        dominant: dict[tuple[str, str], dict[str, object]] = {}
        for key, signatures in grouped.items():
            dominant[key] = sorted(
                signatures.values(),
                key=lambda item: (
                    0 if item["confidence"] == "high" else 1,
                    -int(item["occurrences"]),
                    -(_as_utc(item["most_recent_at"]) or datetime.datetime.min.replace(tzinfo=datetime.UTC)).timestamp(),
                ),
            )[0]

        insights: list[RuntimeJobOperationalInsight] = []
        for rollup in reliability:
            key = (rollup.source_key, rollup.job_name)
            failed, logged, diagnosed, ceiling = coverage.get(key, (0, 0, 0, 0))
            diagnosis = dominant.get(key, {})
            insights.append(
                RuntimeJobOperationalInsight(
                    project_id=rollup.project_id,
                    source_key=rollup.source_key,
                    job_name=rollup.job_name,
                    total_runs=rollup.total_runs,
                    success_runs=rollup.success_runs,
                    failed_runs=rollup.failed_runs,
                    unfinished_runs=rollup.unfinished_runs,
                    failure_rate=rollup.failure_rate,
                    first_run_at=rollup.first_run_at,
                    last_run_at=rollup.last_run_at,
                    failures_with_logs=logged,
                    diagnosed_failures=diagnosed,
                    capture_ceiling_reached=ceiling,
                    evidence_completeness=_evidence_completeness(
                        failed, logged, diagnosed
                    ),
                    **diagnosis,
                )
            )
        return insights

    async def list_doc_evidence(
        self,
        scope: ProjectScope,
        *,
        project_id: str,
        unit_names: Sequence[str],
        since: datetime.datetime,
        max_failures_per_unit: int = 5,
        environment_ids: Sequence[str] | None = None,
    ) -> tuple[list[RuntimeDocEvidence], list[str], list[str]]:
        """Runtime profiles for scanned units, the units with no data, and the
        runtime jobs with no unit.

        Unit-to-runtime resolution is EXACT, never substring: a scan commonly
        holds sibling units whose names differ by one character (verified in
        werkorder: `AssetCheck_GetLocation` alongside `AssetCheck_GetLocations`),
        and a prefix match would silently attribute one job's failures to the
        other.  An explicit `runtime_job_scan_maps` row wins over name equality
        so a deployment whose runtime task was renamed can still be mapped by
        hand; the table is empty in practice today, which is why name equality
        is the working path rather than the fallback.

        Returns aggregates only.  Callers get no log lines, no per-run
        timestamps, and the single free text field (`error_summary`) is redacted
        before it leaves.  Cadence is carried by gap QUANTILES
        (`gap_p25/p50/p75_seconds`, `gap_sample_size`) for the same reason --
        they answer "how often does this run" without shipping the run log that
        would answer it by enumeration.

        Third element -- `orphan_runtime_jobs`
        --------------------------------------
        Runtime job names observed in this project and this window that matched
        NONE of the units in THIS call.  It is the mis-binding tripwire: scan
        directory `alitalend` holds the AssetCheck estate whose units match, set
        for set, the runtime jobs filed under `reverse_engineer_VW`, while
        runtime `alitalend` holds a different 28-job workspace.  Without this
        field a document publishes 1-of-10 coverage in silence; with it the
        generator can state "27 runtime jobs have no scanned code" and flag the
        binding as suspect.

        **A batched caller must merge by INTERSECTION, never by union.**  The
        field is relative to the units in one request, so a unit sent in batch 2
        makes its job look orphaned in batch 1.  Writing
        `A` for the project's runtime job vocabulary (identical for every batch:
        the reliability query depends only on `project_id` and `since`) and
        `M_i` for the jobs matched by batch `i`, this method returns
        `O_i = A - M_i`, and the true orphan set is
        `A - union(M_i) = intersection(O_i)`.  Union would report every runtime
        job as an orphan as soon as the unit list is split.

        The list is capped at `_DOC_EVIDENCE_MAX_ORPHAN_JOBS`, sorted by name so
        the cap selects a stable prefix across batches.  The cap is fail-safe
        under intersection: dropping names can only shrink the merged result, so
        a truncated response can under-report orphans but can never invent one.
        An empty `unit_names` yields an empty orphan list rather than the whole
        vocabulary -- with nothing requested, "orphaned" is not a claim the
        evidence supports.

        Read scope -- `environment_ids`
        -------------------------------
        None reads every mirrored environment (the historical behaviour). A list
        narrows EVERY aggregate here to runs observed in those environments, so
        a project documenting only production gets production's failure rate,
        production's latency and production's cadence, rather than figures that
        blend a nightly TST loop into them. The narrowing also moves the orphan
        line: a job that only ever runs in an excluded environment becomes
        unmatched, which is correct -- within this scope there is no evidence
        for it -- and is why the applied scope must be reported alongside the
        result rather than left implicit.
        """
        requested = list(dict.fromkeys(name for name in unit_names if name))
        if self._denies_all(scope) or not requested:
            return [], requested, []
        # An explicitly empty selection is not "everything". Callers resolve a
        # configured-but-unsatisfiable scope before reaching here; this guard
        # exists so a future caller cannot turn one into a silent full read.
        scoped = list(environment_ids) if environment_ids is not None else None
        if scoped is not None and not scoped:
            return [], requested, []

        overrides = await self._doc_evidence_scan_map(scope, project_id, requested)
        # A mapped unit is looked up by its runtime name; everything else by its
        # own name.  Both directions are needed because one runtime job may back
        # several units and the response is keyed by unit.
        lookup: dict[str, list[tuple[str, str]]] = {}
        for unit in requested:
            runtime_name = overrides.get(unit)
            resolved_by = "scan_map" if runtime_name else "exact_name"
            lookup.setdefault(runtime_name or unit, []).append((unit, resolved_by))

        reliability = await self.list_job_reliability(
            scope,
            ListRuntimeJobReliabilityInput(
                project_id=project_id,
                since=since,
                # Bounded by the runtime vocabulary of one project, not by the
                # scan: a project mirrors tens of jobs, not hundreds.
                limit=500,
                environment_ids=scoped,
            ),
        )
        matched = {row.job_name: row for row in reliability if row.job_name in lookup}
        orphans = sorted({row.job_name for row in reliability if row.job_name not in lookup})[
            :_DOC_EVIDENCE_MAX_ORPHAN_JOBS
        ]
        if not matched:
            # Zero matches with a non-empty orphan list is the binding-suspect
            # signal, so the orphans have to survive this exit -- it is the one
            # case where they carry the whole finding.
            return [], requested, orphans

        job_names = list(matched)
        durations = await self._doc_evidence_durations(
            scope, project_id, job_names, since, environment_ids=scoped
        )
        environments = await self._doc_evidence_environments(
            scope, project_id, job_names, since, environment_ids=scoped
        )
        gaps = await self._doc_evidence_gap_quantiles(
            scope, project_id, job_names, since, environment_ids=scoped
        )
        plans = await self._doc_evidence_plans(scope, project_id, job_names)
        failures = await self._doc_evidence_failures(
            scope, project_id, job_names, since, max_failures_per_unit, environment_ids=scoped
        )
        log_quality = await self._doc_evidence_log_quality(
            scope, project_id, job_names, since, environment_ids=scoped
        )
        incident_insights = await self._doc_evidence_incidents(
            scope, project_id, job_names, since, environment_ids=scoped
        )

        evidence: list[RuntimeDocEvidence] = []
        for job_name, rollup in matched.items():
            p50, p95, max_duration_ms = durations.get(job_name, (None, None, None))
            job_environments = environments.get(job_name, [])
            gap = _dominant_environment_gaps(gaps, job_name, job_environments)
            for unit_name, resolved_by in lookup[job_name]:
                evidence.append(
                    RuntimeDocEvidence(
                        unit_name=unit_name,
                        job_name=job_name,
                        source_key=rollup.source_key,
                        resolved_by=resolved_by,
                        environments=job_environments,
                        total_runs=rollup.total_runs,
                        success_runs=rollup.success_runs,
                        failed_runs=rollup.failed_runs,
                        unfinished_runs=rollup.unfinished_runs,
                        failure_rate=rollup.failure_rate,
                        current_failure_streak=rollup.current_failure_streak,
                        first_run_at=rollup.first_run_at,
                        last_run_at=rollup.last_run_at,
                        runs_per_day=_observed_runs_per_day(rollup),
                        p50_duration_ms=p50,
                        p95_duration_ms=p95,
                        max_duration_ms=max_duration_ms,
                        # Cadence evidence for the dominant environment only,
                        # which `environments[0]` names. All four stay None when
                        # that environment has fewer than two runs in the
                        # window -- one run is not a gap.
                        gap_p25_seconds=gap.p25 if gap else None,
                        gap_p50_seconds=gap.p50 if gap else None,
                        gap_p75_seconds=gap.p75 if gap else None,
                        gap_sample_size=gap.sample_size if gap else None,
                        plans=plans.get(job_name, []),
                        recent_failures=failures.get(job_name, []),
                        log_quality=log_quality.get(job_name, RuntimeDocLogQuality()),
                        incident_insights=incident_insights.get(job_name, []),
                    )
                )
        evidence.sort(key=lambda item: item.unit_name)
        covered = {item.unit_name for item in evidence}
        return evidence, [unit for unit in requested if unit not in covered], orphans

    async def _doc_evidence_scan_map(
        self, scope: ProjectScope, project_id: str, unit_names: Sequence[str]
    ) -> dict[str, str]:
        """Explicit static-unit -> runtime-job-name overrides, when any exist."""
        stmt = (
            select(RuntimeJobScanMapRow.static_job_id, RuntimeJobRunRow.job_name)
            .join(
                RuntimeJobRunRow,
                and_(
                    RuntimeJobRunRow.project_id == RuntimeJobScanMapRow.project_id,
                    RuntimeJobRunRow.source_key == RuntimeJobScanMapRow.source_key,
                    RuntimeJobRunRow.job_id == RuntimeJobScanMapRow.runtime_job_id,
                ),
            )
            .where(RuntimeJobScanMapRow.project_id == project_id)
            .where(RuntimeJobScanMapRow.static_job_id.in_(unit_names))
            .distinct()
        )
        stmt = self._apply_scope(stmt, RuntimeJobScanMapRow.project_id, scope)
        rows = (await self._session.execute(stmt)).all()
        return {static_job_id: job_name for static_job_id, job_name in rows}

    async def _doc_evidence_durations(
        self,
        scope: ProjectScope,
        project_id: str,
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
    ) -> dict[str, tuple[int | None, int | None, int | None]]:
        """Duration percentiles over SUCCESSFUL runs only, as p50/p95/max.

        The reliability rollup measures every run, which is right for an
        operations console but wrong for a document: a hung or abandoned
        execution still carries a `duration_ms`, and it dominates the
        percentiles.  Verified in werkorder -- `Maximo2Quintiq_Workorder_Route`
        reports a p95 of 1_141_724_496 ms (13 days) and `maximoFourpsWorkOrder`
        a p50 of 1.7 days, neither of which describes how long the job takes.
        Restricting to completed successes gives the figure a reader expects
        from "how long does this job run".
        """
        base = select(
            RuntimeExecutionRow.project_id.label("project_id"),
            RuntimeExecutionRow.job_name.label("job_name"),
            RuntimeExecutionRow.duration_ms.label("duration_ms"),
        ).where(
            RuntimeExecutionRow.project_id == project_id,
            RuntimeExecutionRow.job_name.in_(job_names),
            RuntimeExecutionRow.started_at >= since,
            RuntimeExecutionRow.state == RuntimeExecutionState.SUCCESS.value,
            RuntimeExecutionRow.ended_at.is_not(None),
            RuntimeExecutionRow.duration_ms.is_not(None),
        )
        if environment_ids is not None:
            base = base.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(project_id, environment_ids)
                )
            )
        base = self._apply_scope(base, RuntimeExecutionRow.project_id, scope).subquery()
        group = [base.c.project_id, base.c.job_name]
        percentiles = self._duration_percentiles(base, group)
        maxima = (
            select(*group, func.max(base.c.duration_ms).label("max_duration_ms"))
            .group_by(*group)
            .subquery()
        )
        stmt = select(
            maxima.c.job_name, percentiles.c.p50, percentiles.c.p95, maxima.c.max_duration_ms
        ).join(
            percentiles,
            and_(
                percentiles.c.project_id == maxima.c.project_id,
                percentiles.c.job_name == maxima.c.job_name,
            ),
            isouter=True,
        )
        rows = (await self._session.execute(stmt)).all()
        return {job_name: (p50, p95, max_ms) for job_name, p50, p95, max_ms in rows}

    async def _doc_evidence_gap_quantiles(
        self,
        scope: ProjectScope,
        project_id: str,
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
    ) -> dict[tuple[str, str | None], _GapStats]:
        """Inter-run gap quantiles per (job, environment), in seconds.

        The evidence a cadence claim needs. `runs_per_day` is a mean over the
        window and cannot support one: verified in werkorder,
        `Maximo2RHM_Workorders` has a mean gap of 2204 s and a median of 1801 s,
        so a document derived from the mean announces a 37-minute schedule that
        nobody configured, while the median lands on the half-hour it actually
        runs on. Publishing p25/p50/p75 lets the consumer test the SPREAD as
        well as the centre, which is the difference between a schedule and a
        coincidence.

        Grouped BY ENVIRONMENT, never pooled across them -- see
        `_dominant_environment_gaps` for why that is the load-bearing part.

        Read from `runtime_job_runs` rather than `runtime_executions` because
        only the job-run fact carries `environment_name`; the two tables mirror
        the same stream (3394/3394 rows for werkorder, 914/914 for alitalend on
        the live mirror). A project whose job-run mirror is empty therefore
        reports no cadence rather than a pooled one, which is the safe
        direction.

        Everything -- gap, rank, quantile -- is computed in the database, in
        the sibling style of `_doc_evidence_durations`: only one already
        aggregated row per (job, environment) crosses the boundary, so a job
        with thousands of runs costs the same as one with ten. Bounded by the
        matched job set and the caller's window.

        The quantile is the discrete nearest-rank pick shared with every other
        percentile in this file (`_percentile_at_rank`), so it is always a gap
        that was actually observed, never an interpolation between two.
        """
        started = _epoch_seconds(self._dialect, RuntimeJobRunRow.started_at)
        base = select(
            RuntimeJobRunRow.job_name.label("job_name"),
            RuntimeJobRunRow.environment_name.label("environment_name"),
            started.label("started_epoch"),
        ).where(
            RuntimeJobRunRow.project_id == project_id,
            RuntimeJobRunRow.job_name.in_(job_names),
            RuntimeJobRunRow.started_at >= since,
        )
        if environment_ids is not None:
            base = base.where(RuntimeJobRunRow.environment_id.in_(list(environment_ids)))
        base = self._apply_scope(base, RuntimeJobRunRow.project_id, scope).subquery()

        # NULL environments partition together: SQL PARTITION BY treats NULLs as
        # equal, which is the wanted grouping -- "the deployment the provider
        # did not name" is one population, not one per row.
        partition = [base.c.job_name, base.c.environment_name]
        previous_start = func.lag(base.c.started_epoch).over(
            partition_by=partition, order_by=base.c.started_epoch.asc()
        )
        gaps = select(
            base.c.job_name,
            base.c.environment_name,
            (base.c.started_epoch - previous_start).label("gap_seconds"),
        ).subquery()
        # The first run of every group has no predecessor and yields NULL. A
        # single-run group therefore contributes no rows at all and simply never
        # appears in the result -- the caller reads that as "no cadence
        # evidence", which is what one run means.
        gap_partition = [gaps.c.job_name, gaps.c.environment_name]
        ranked = (
            select(
                gaps.c.job_name,
                gaps.c.environment_name,
                gaps.c.gap_seconds,
                func.row_number()
                .over(partition_by=gap_partition, order_by=gaps.c.gap_seconds.asc())
                .label("rn"),
                func.count().over(partition_by=gap_partition).label("cnt"),
            )
            .where(gaps.c.gap_seconds.is_not(None))
            .subquery()
        )
        stmt = select(
            ranked.c.job_name,
            ranked.c.environment_name,
            func.max(ranked.c.cnt).label("sample_size"),
            _percentile_at_rank(ranked.c.rn, ranked.c.cnt, ranked.c.gap_seconds, 25).label("p25"),
            _percentile_at_rank(ranked.c.rn, ranked.c.cnt, ranked.c.gap_seconds, 50).label("p50"),
            _percentile_at_rank(ranked.c.rn, ranked.c.cnt, ranked.c.gap_seconds, 75).label("p75"),
        ).group_by(ranked.c.job_name, ranked.c.environment_name)

        result: dict[tuple[str, str | None], _GapStats] = {}
        for job_name, environment_name, sample_size, p25, p50, p75 in (
            await self._session.execute(stmt)
        ).all():
            result[(job_name, environment_name)] = _GapStats(
                # Seconds, rounded: the underlying value is a float on both
                # backends (Postgres `extract` is numeric, SQLite julianday is a
                # double) and sub-second precision says nothing about a
                # schedule.
                p25=None if p25 is None else int(round(float(p25))),
                p50=None if p50 is None else int(round(float(p50))),
                p75=None if p75 is None else int(round(float(p75))),
                sample_size=int(sample_size),
            )
        return result

    async def _doc_evidence_environments(
        self,
        scope: ProjectScope,
        project_id: str,
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
    ) -> dict[str, list[str]]:
        """Environments each job was observed running in, most frequent first.

        The order is a contract, not a presentation choice: `environments[0]` is
        the environment the gap quantiles describe (see
        `_dominant_environment_gaps`), so the tie-break is pinned to the name
        rather than left to whatever order the backend returns equal counts in.
        """
        stmt = (
            select(
                RuntimeJobRunRow.job_name,
                RuntimeJobRunRow.environment_name,
                func.count().label("runs"),
            )
            .where(RuntimeJobRunRow.project_id == project_id)
            .where(RuntimeJobRunRow.job_name.in_(job_names))
            .where(RuntimeJobRunRow.started_at >= since)
            .where(RuntimeJobRunRow.environment_name.is_not(None))
            .group_by(RuntimeJobRunRow.job_name, RuntimeJobRunRow.environment_name)
            .order_by(func.count().desc(), RuntimeJobRunRow.environment_name.asc())
        )
        if environment_ids is not None:
            # Keeps the `environments[0]` contract true under a read scope: the
            # dominant environment must be the dominant SELECTED one, or the
            # gap quantiles would be attributed to an environment the reader was
            # told is out of scope.
            stmt = stmt.where(RuntimeJobRunRow.environment_id.in_(list(environment_ids)))
        stmt = self._apply_scope(stmt, RuntimeJobRunRow.project_id, scope)
        result: dict[str, list[str]] = {}
        for job_name, environment_name, _runs in (await self._session.execute(stmt)).all():
            result.setdefault(job_name, []).append(environment_name)
        return result

    async def _doc_evidence_plans(
        self, scope: ProjectScope, project_id: str, job_names: Sequence[str]
    ) -> dict[str, list[str]]:
        """Orchestration plans that reference each job as one of their steps.

        `steps` is provider JSON, so membership is resolved in Python against
        the step's own `job_name` field rather than through a JSON containment
        query -- the column is portable JSON on SQLite in tests and JSONB in
        production, and the plan inventory per project is tens of rows.

        Resolving instead (or additionally) through `steps[].job_id ->
        runtime_jobs.job_id -> name` was proposed on the observation that TMC
        leaves most step `job_name` values null.  Measured over the whole live
        mirror it recovers NOTHING: of 343 plan steps, 219 have a null
        `job_name`, and every one of those carries a `job_id` that is absent
        from `runtime_jobs` (the provider also copies the id into the step's
        `name` field, which is the tell).  Counting distinct jobs that gain a
        plan attribution, name resolution gives 16/64/1/2 for
        alitalend/cdc/werkorder/reingest-drill and id resolution gives
        16/60/1/2 -- strictly not better, and a union adds zero.  Null step
        names are a mirror-coverage gap (plan steps pointing at tasks in
        environments the sync does not hold), not a lookup-key mistake, so the
        `job_name` path stays until the job inventory covers them.
        """
        stmt = (
            select(RuntimePlanRow.name, RuntimePlanRow.display_name, RuntimePlanRow.steps)
            .where(RuntimePlanRow.project_id == project_id)
            .where(RuntimePlanRow.steps.is_not(None))
        )
        stmt = self._apply_scope(stmt, RuntimePlanRow.project_id, scope)
        wanted = set(job_names)
        result: dict[str, list[str]] = {}
        for name, display_name, steps in (await self._session.execute(stmt)).all():
            if not isinstance(steps, list):
                continue
            label = display_name or name
            for step in steps:
                if not isinstance(step, dict):
                    continue
                step_job = step.get("job_name")
                if step_job in wanted and label not in result.setdefault(step_job, []):
                    result[step_job].append(label)
        return result

    async def _doc_evidence_failures(
        self,
        scope: ProjectScope,
        project_id: str,
        job_names: Sequence[str],
        since: datetime.datetime,
        max_per_job: int,
        environment_ids: Sequence[str] | None = None,
    ) -> dict[str, list[RuntimeDocEvidenceFailure]]:
        """Distinct failure MODES per job, most recent first, redacted and capped.

        Grouped rather than listed: verified in werkorder, the five most recent
        failures of `RHM2Smartflow_Workorders` are the same
        `tMSSqlConnection_2` network error under five execution ids.  Listing
        them fills the section with one story and hides every rarer failure.
        Each group reports its occurrence count and its most recent wording.
        """
        stmt = (
            select(
                RuntimeFailureRow.job_name,
                RuntimeFailureRow.failed_at,
                RuntimeFailureRow.error_summary,
            )
            .where(RuntimeFailureRow.project_id == project_id)
            .where(RuntimeFailureRow.job_name.in_(job_names))
            .where(RuntimeFailureRow.failed_at >= since)
            .order_by(RuntimeFailureRow.failed_at.desc(), RuntimeFailureRow.id.desc())
        )
        if environment_ids is not None:
            # A failure row carries no environment either, and joining through
            # the job run is what stops a TST stack trace being published as the
            # production failure mode -- the exact class of error the scope was
            # selected to exclude.
            stmt = stmt.where(
                RuntimeFailureRow.execution_id.in_(
                    self._environment_execution_ids(project_id, environment_ids)
                )
            )
        stmt = self._apply_scope(stmt, RuntimeFailureRow.project_id, scope)
        # job -> signature -> accumulating entry, insertion-ordered so the first
        # sighting of each signature (the most recent, given the ORDER BY) keeps
        # its position and its wording.
        grouped: dict[str, dict[str, RuntimeDocEvidenceFailure]] = {}
        for job_name, failed_at, error_summary in (await self._session.execute(stmt)).all():
            # The prompt-bound variant of the shared redactor: same keyword,
            # credential and entity-shape filters as the persistence path, plus
            # execution-id stripping, which is safe here and only here (the
            # stored row keeps the id an operator needs to pull the run).
            summary = sanitize_doc_evidence_error(error_summary)
            if not summary:
                continue
            buckets = grouped.setdefault(job_name, {})
            signature = _failure_signature(summary)
            existing = buckets.get(signature)
            if existing is not None:
                existing.occurrences += 1
                continue
            if len(buckets) >= max_per_job:
                continue
            buckets[signature] = RuntimeDocEvidenceFailure(
                failed_at=_as_utc(failed_at), error_summary=summary
            )
        return {job_name: list(buckets.values()) for job_name, buckets in grouped.items()}

    async def _doc_evidence_log_quality(
        self,
        scope: ProjectScope,
        project_id: str,
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
    ) -> dict[str, RuntimeDocLogQuality]:
        """Measure diagnostic coverage without shipping a single raw log line.

        A row count at the current 10k provider ceiling is a conservative
        truncation signal.  It may undercount truncation if a future provider
        lowers its own cap, but it never labels a complete shorter log as cut.
        """
        log_counts = (
            select(
                RuntimeLogEventRow.project_id.label("project_id"),
                RuntimeLogEventRow.execution_id.label("execution_id"),
                func.count(RuntimeLogEventRow.id).label("log_lines"),
            )
            .where(RuntimeLogEventRow.project_id == project_id)
            .group_by(RuntimeLogEventRow.project_id, RuntimeLogEventRow.execution_id)
            .subquery()
        )
        diagnosed = (
            select(
                RuntimeIncidentRow.project_id.label("project_id"),
                RuntimeIncidentRow.execution_id.label("execution_id"),
            )
            .where(RuntimeIncidentRow.project_id == project_id)
            .where(RuntimeIncidentRow.confidence.in_(("high", "medium")))
            .where(RuntimeIncidentRow.pattern_id.notin_(("generic.unknown", "exception.generic")))
            .distinct()
            .subquery()
        )
        stmt = (
            select(
                RuntimeExecutionRow.job_name,
                func.count(func.distinct(RuntimeExecutionRow.execution_id)).label("failed"),
                func.count(
                    func.distinct(
                        case(
                            (log_counts.c.execution_id.is_not(None), RuntimeExecutionRow.execution_id)
                        )
                    )
                ).label("logged"),
                func.count(
                    func.distinct(
                        case(
                            (diagnosed.c.execution_id.is_not(None), RuntimeExecutionRow.execution_id)
                        )
                    )
                ).label("diagnosed"),
                func.count(
                    func.distinct(
                        case(
                            (log_counts.c.log_lines >= 10_000, RuntimeExecutionRow.execution_id)
                        )
                    )
                ).label("ceiling"),
            )
            .outerjoin(
                log_counts,
                and_(
                    log_counts.c.project_id == RuntimeExecutionRow.project_id,
                    log_counts.c.execution_id == RuntimeExecutionRow.execution_id,
                ),
            )
            .outerjoin(
                diagnosed,
                and_(
                    diagnosed.c.project_id == RuntimeExecutionRow.project_id,
                    diagnosed.c.execution_id == RuntimeExecutionRow.execution_id,
                ),
            )
            .where(RuntimeExecutionRow.project_id == project_id)
            .where(RuntimeExecutionRow.job_name.in_(job_names))
            .where(RuntimeExecutionRow.started_at >= since)
            .where(RuntimeExecutionRow.state == RuntimeExecutionState.FAILED.value)
            .group_by(RuntimeExecutionRow.job_name)
        )
        if environment_ids is not None:
            stmt = stmt.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(project_id, environment_ids)
                )
            )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        return {
            job_name: RuntimeDocLogQuality(
                failed_executions=int(failed or 0),
                executions_with_logs=int(logged or 0),
                diagnosed_executions=int(diagnosed_count or 0),
                capture_ceiling_reached=int(ceiling or 0),
            )
            for job_name, failed, logged, diagnosed_count, ceiling in (
                await self._session.execute(stmt)
            ).all()
        }

    async def _doc_evidence_incidents(
        self,
        scope: ProjectScope,
        project_id: str,
        job_names: Sequence[str],
        since: datetime.datetime,
        max_per_job: int = 3,
        environment_ids: Sequence[str] | None = None,
    ) -> dict[str, list[RuntimeDocIncidentInsight]]:
        """Return recurring diagnoses backed by useful log evidence.

        The live alitalend source shows the important dividing line: SQL Server
        deadlocks and exhausted Camel delivery carry medium confidence and
        concrete signals; generic exception/unknown rows merely say to review a
        log.  Only the first category is allowed into a documentation prompt.
        """
        stmt = (
            select(
                RuntimeExecutionRow.job_name,
                RuntimeIncidentRow.signature_hash,
                RuntimeIncidentRow.pattern_id,
                RuntimeIncidentRow.confidence,
                RuntimeIncidentRow.first_bad_at,
                RuntimeExecutionRow.started_at,
                RuntimeIncidentRow.causal_message,
                RuntimeIncidentRow.root_cause_candidate,
                RuntimeIncidentRow.cause_origin,
                RuntimeIncidentRow.termination_component,
                RuntimeIncidentRow.next_checks,
                RuntimeExecutionSummaryRow.failed_component,
                RuntimeExecutionSummaryRow.primary_error_code,
                RuntimeExecutionSummaryRow.primary_exception_class,
            )
            .join(
                RuntimeIncidentRow,
                and_(
                    RuntimeIncidentRow.project_id == RuntimeExecutionRow.project_id,
                    RuntimeIncidentRow.execution_id == RuntimeExecutionRow.execution_id,
                ),
            )
            .join(
                RuntimeExecutionSummaryRow,
                and_(
                    RuntimeExecutionSummaryRow.project_id == RuntimeExecutionRow.project_id,
                    RuntimeExecutionSummaryRow.execution_id == RuntimeExecutionRow.execution_id,
                ),
                isouter=True,
            )
            .where(RuntimeExecutionRow.project_id == project_id)
            .where(RuntimeExecutionRow.job_name.in_(job_names))
            .where(RuntimeExecutionRow.started_at >= since)
            .where(RuntimeIncidentRow.confidence.in_(("high", "medium")))
            .where(RuntimeIncidentRow.pattern_id.notin_(("generic.unknown", "exception.generic")))
            .order_by(RuntimeExecutionRow.job_name, RuntimeIncidentRow.first_bad_at.desc())
        )
        if environment_ids is not None:
            stmt = stmt.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(project_id, environment_ids)
                )
            )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        grouped: dict[str, dict[str, dict[str, object]]] = {}
        for row in (await self._session.execute(stmt)).all():
            job_name = str(row.job_name)
            signature = str(
                row.signature_hash
                or f"{row.pattern_id}|{row.failed_component}|{row.primary_exception_class}"
            )
            by_signature = grouped.setdefault(job_name, {})
            current = by_signature.get(signature)
            if current is None:
                current = {
                    "pattern_id": str(row.pattern_id),
                    "confidence": str(row.confidence),
                    "occurrences": 0,
                    "most_recent_at": _as_utc(row.first_bad_at or row.started_at),
                    "failed_component": row.failed_component,
                    "termination_component": row.termination_component,
                    "error_code": row.primary_error_code,
                    "exception_class": row.primary_exception_class,
                    "causal_message": sanitize_doc_evidence_error(row.causal_message),
                    "root_cause_candidate": sanitize_doc_evidence_error(
                        row.root_cause_candidate
                    ),
                    "cause_origin": row.cause_origin,
                    "next_checks": [
                        check
                        for check in (
                            sanitize_doc_evidence_error(value)
                            for value in (row.next_checks or [])[:3]
                        )
                        if check
                    ],
                }
                by_signature[signature] = current
            current["occurrences"] = int(current["occurrences"]) + 1

        result: dict[str, list[RuntimeDocIncidentInsight]] = {}
        for job_name, by_signature in grouped.items():
            ordered = sorted(
                by_signature.values(),
                key=lambda item: (
                    0 if item["confidence"] == "high" else 1,
                    -int(item["occurrences"]),
                    str(item["pattern_id"]),
                ),
            )[:max_per_job]
            result[job_name] = [RuntimeDocIncidentInsight(**item) for item in ordered]
        return result

    def _scoped_metric_base(
        self,
        scope: ProjectScope,
        *,
        project_id: str | None,
        source_key: str | None,
        component_key: str | None = None,
        component_type: str | None = None,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
    ):
        stmt = select(
            RuntimeComponentMetricRow.project_id.label("project_id"),
            RuntimeComponentMetricRow.source_key.label("source_key"),
            RuntimeComponentMetricRow.execution_id.label("execution_id"),
            RuntimeComponentMetricRow.component_key.label("component_key"),
            RuntimeComponentMetricRow.component_type.label("component_type"),
            RuntimeComponentMetricRow.source_connector_type.label("source_connector_type"),
            RuntimeComponentMetricRow.target_connector_type.label("target_connector_type"),
            RuntimeComponentMetricRow.rows_total.label("rows_total"),
            RuntimeComponentMetricRow.rows_rejected.label("rows_rejected"),
            RuntimeComponentMetricRow.duration_ms.label("duration_ms"),
            RuntimeComponentMetricRow.measured_at.label("measured_at"),
        )
        stmt = self._apply_scope(stmt, RuntimeComponentMetricRow.project_id, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeComponentMetricRow.project_id == project_id)
        if source_key is not None:
            stmt = stmt.where(RuntimeComponentMetricRow.source_key == source_key)
        if component_key is not None:
            stmt = stmt.where(RuntimeComponentMetricRow.component_key == component_key)
        if component_type is not None:
            stmt = stmt.where(RuntimeComponentMetricRow.component_type == component_type)
        if since is not None:
            stmt = stmt.where(RuntimeComponentMetricRow.measured_at >= since)
        if until is not None:
            stmt = stmt.where(RuntimeComponentMetricRow.measured_at <= until)
        return stmt.subquery()

    async def list_component_hotspots(
        self, scope: ProjectScope, params: ListRuntimeComponentHotspotsInput
    ) -> list[RuntimeComponentHotspot]:
        """Per-component throughput/latency rollup, highest row volume first."""
        if self._denies_all(scope):
            return []
        base = self._scoped_metric_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            component_key=params.component_key,
            component_type=params.component_type,
            since=params.since,
            until=params.until,
        )
        group = [base.c.project_id, base.c.source_key, base.c.component_key]
        percentiles = self._duration_percentiles(base, group)
        aggregated = (
            select(
                *group,
                func.max(base.c.component_type).label("component_type"),
                func.max(base.c.source_connector_type).label("source_connector_type"),
                func.max(base.c.target_connector_type).label("target_connector_type"),
                func.count(func.distinct(base.c.execution_id)).label("executions"),
                func.sum(base.c.rows_total).label("rows_total"),
                func.sum(base.c.rows_rejected).label("rows_rejected"),
                func.avg(base.c.duration_ms).label("avg_duration_ms"),
                func.max(base.c.duration_ms).label("max_duration_ms"),
                func.max(base.c.measured_at).label("last_measured_at"),
            )
            .group_by(*group)
            .subquery()
        )
        stmt = (
            select(aggregated, percentiles.c.p95)
            .join(
                percentiles,
                and_(
                    percentiles.c.project_id == aggregated.c.project_id,
                    percentiles.c.source_key == aggregated.c.source_key,
                    percentiles.c.component_key == aggregated.c.component_key,
                ),
                isouter=True,
            )
            .order_by(
                aggregated.c.rows_total.desc().nullslast(),
                aggregated.c.executions.desc(),
                aggregated.c.component_key.asc(),
            )
            .offset(params.offset)
            .limit(params.limit)
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        return [
            RuntimeComponentHotspot(
                project_id=row["project_id"],
                source_key=row["source_key"],
                component_key=row["component_key"],
                component_type=row["component_type"],
                source_connector_type=row["source_connector_type"],
                target_connector_type=row["target_connector_type"],
                executions=row["executions"],
                rows_total=row["rows_total"],
                rows_rejected=row["rows_rejected"],
                reject_ratio=_ratio(row["rows_rejected"], row["rows_total"]),
                avg_duration_ms=(
                    int(row["avg_duration_ms"]) if row["avg_duration_ms"] is not None else None
                ),
                p95_duration_ms=row["p95"],
                max_duration_ms=row["max_duration_ms"],
                last_measured_at=_as_utc(row["last_measured_at"]),
            )
            for row in rows
        ]

    async def count_component_hotspots(
        self, scope: ProjectScope, params: ListRuntimeComponentHotspotsInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        base = self._scoped_metric_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            component_key=params.component_key,
            component_type=params.component_type,
            since=params.since,
            until=params.until,
        )
        groups = (
            select(base.c.project_id, base.c.source_key, base.c.component_key)
            .distinct()
            .subquery()
        )
        return int((await self._session.execute(select(func.count()).select_from(groups))).scalar_one())

    async def list_job_throughput(
        self, scope: ProjectScope, params: ListRuntimeJobThroughputInput
    ) -> list[RuntimeJobThroughput]:
        """Rows moved per job, joining run facts to their component metrics."""
        if self._denies_all(scope):
            return []
        joined = self._job_throughput_base(scope, params)
        aggregated = (
            select(
                joined.c.project_id,
                joined.c.source_key,
                joined.c.job_name,
                func.count(func.distinct(joined.c.execution_id)).label("executions_with_metrics"),
                func.count(func.distinct(joined.c.component_key)).label("component_count"),
                func.sum(joined.c.rows_total).label("rows_total"),
                func.sum(joined.c.rows_rejected).label("rows_rejected"),
                func.max(joined.c.measured_at).label("last_measured_at"),
            )
            .group_by(joined.c.project_id, joined.c.source_key, joined.c.job_name)
            .subquery()
        )
        stmt = (
            select(aggregated)
            .order_by(
                aggregated.c.rows_total.desc().nullslast(),
                aggregated.c.job_name.asc(),
            )
            .offset(params.offset)
            .limit(params.limit)
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        return [
            RuntimeJobThroughput(
                project_id=row["project_id"],
                source_key=row["source_key"],
                job_name=row["job_name"],
                executions_with_metrics=row["executions_with_metrics"],
                component_count=row["component_count"],
                rows_total=row["rows_total"],
                rows_rejected=row["rows_rejected"],
                reject_ratio=_ratio(row["rows_rejected"], row["rows_total"]),
                last_measured_at=_as_utc(row["last_measured_at"]),
            )
            for row in rows
        ]

    def _job_throughput_base(
        self, scope: ProjectScope, params: ListRuntimeJobThroughputInput
    ):
        """Component metrics joined to their owning execution's job name.

        The join is on (project_id, execution_id): a source owns its execution ids, so
        this is the same key the metric write-port uses.
        """
        executions = self._scoped_execution_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            job_name=params.job_name,
            since=params.since,
            until=params.until,
        )
        metrics = self._scoped_metric_base(
            scope, project_id=params.project_id, source_key=params.source_key
        )
        return (
            select(
                executions.c.project_id.label("project_id"),
                executions.c.source_key.label("source_key"),
                executions.c.job_name.label("job_name"),
                metrics.c.execution_id.label("execution_id"),
                metrics.c.component_key.label("component_key"),
                metrics.c.rows_total.label("rows_total"),
                metrics.c.rows_rejected.label("rows_rejected"),
                metrics.c.measured_at.label("measured_at"),
            )
            .join(
                metrics,
                and_(
                    metrics.c.project_id == executions.c.project_id,
                    metrics.c.execution_id == executions.c.execution_id,
                ),
            )
            .subquery()
        )

    async def count_job_throughput(
        self, scope: ProjectScope, params: ListRuntimeJobThroughputInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        joined = self._job_throughput_base(scope, params)
        groups = (
            select(joined.c.project_id, joined.c.source_key, joined.c.job_name)
            .distinct()
            .subquery()
        )
        return int((await self._session.execute(select(func.count()).select_from(groups))).scalar_one())

    def _error_signature_stmt(self, scope: ProjectScope, params: ListRuntimeErrorSignaturesInput):
        stmt = select(RuntimeErrorSignatureRow)
        stmt = self._apply_scope(stmt, RuntimeErrorSignatureRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeErrorSignatureRow.project_id == params.project_id)
        if params.error_code is not None:
            stmt = stmt.where(RuntimeErrorSignatureRow.error_code == params.error_code)
        if params.exception_class is not None:
            stmt = stmt.where(RuntimeErrorSignatureRow.exception_class == params.exception_class)
        if params.component_name is not None:
            stmt = stmt.where(RuntimeErrorSignatureRow.component_name == params.component_name)
        if params.since is not None:
            stmt = stmt.where(RuntimeErrorSignatureRow.last_seen_at >= params.since)
        if params.until is not None:
            stmt = stmt.where(RuntimeErrorSignatureRow.last_seen_at <= params.until)
        return stmt

    async def list_error_signatures(
        self, scope: ProjectScope, params: ListRuntimeErrorSignaturesInput
    ) -> list[RuntimeErrorSignature]:
        """Recurring fault fingerprints, most-recurrent first."""
        if self._denies_all(scope):
            return []
        stmt = (
            self._error_signature_stmt(scope, params)
            .order_by(
                RuntimeErrorSignatureRow.occurrence_count.desc(),
                RuntimeErrorSignatureRow.last_seen_at.desc().nullslast(),
                RuntimeErrorSignatureRow.id.desc(),
            )
            .offset(params.offset)
            .limit(params.limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_error_signature(row) for row in rows]

    async def count_error_signatures(
        self, scope: ProjectScope, params: ListRuntimeErrorSignaturesInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        inner = self._error_signature_stmt(scope, params).subquery()
        return int((await self._session.execute(select(func.count()).select_from(inner))).scalar_one())

    # --- honest counts for previously len()-reported list endpoints ---

    async def count_job_runs(self, scope: ProjectScope, params: ListRuntimeJobRunsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeJobRunRow)
        stmt = self._apply_scope(stmt, RuntimeJobRunRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRunRow.source_key == params.source_key)
        if params.job_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.job_id == params.job_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.engine_id == params.engine_id)
        if params.state is not None:
            stmt = stmt.where(RuntimeJobRunRow.state == params.state.value)
        if params.since is not None:
            stmt = stmt.where(RuntimeJobRunRow.started_at >= params.since)
        if params.until is not None:
            stmt = stmt.where(RuntimeJobRunRow.started_at <= params.until)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_plan_runs(self, scope: ProjectScope, params: ListRuntimePlanRunsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimePlanRunRow)
        stmt = self._apply_scope(stmt, RuntimePlanRunRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRunRow.source_key == params.source_key)
        if params.plan_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.plan_id == params.plan_id)
        if params.state is not None:
            stmt = stmt.where(RuntimePlanRunRow.state == params.state.value)
        if params.since is not None:
            stmt = stmt.where(RuntimePlanRunRow.started_at >= params.since)
        if params.until is not None:
            stmt = stmt.where(RuntimePlanRunRow.started_at <= params.until)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_artifacts(self, scope: ProjectScope, params: ListRuntimeArtifactsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeArtifactRow)
        stmt = self._apply_scope(stmt, RuntimeArtifactRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeArtifactRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeArtifactRow.source_key == params.source_key)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeArtifactRow.workspace_id == params.workspace_id)
        if params.artifact_type is not None:
            stmt = stmt.where(RuntimeArtifactRow.artifact_type == params.artifact_type)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_connections(
        self, scope: ProjectScope, params: ListRuntimeConnectionsInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeConnectionRow)
        stmt = self._apply_scope(stmt, RuntimeConnectionRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeConnectionRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeConnectionRow.source_key == params.source_key)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeConnectionRow.workspace_id == params.workspace_id)
        if params.connection_type is not None:
            stmt = stmt.where(RuntimeConnectionRow.connection_type == params.connection_type)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_job_dependencies(
        self, scope: ProjectScope, params: ListRuntimeDependenciesInput
    ) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeJobDependencyRow)
        stmt = self._apply_scope(stmt, RuntimeJobDependencyRow.project_id, scope)
        if params.project_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.project_id == params.project_id)
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.source_key == params.source_key)
        if params.dependency_type is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.dependency_type == params.dependency_type)
        if params.dependency_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.dependency_id == params.dependency_id)
        if params.job_id is not None:
            stmt = stmt.where(RuntimeJobDependencyRow.job_id == params.job_id)
        return int((await self._session.execute(stmt)).scalar_one())

    # --- write-port (idempotent) ---

    async def upsert_source(
        self,
        project_id: str,
        source: RuntimeSource,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeSourceRow).where(
            RuntimeSourceRow.project_id == project_id,
            RuntimeSourceRow.source_key == source.source_key,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSourceRow(project_id=project_id, source_key=source.source_key)
            self._session.add(row)
        row.source_type = source.source_type
        row.physical_identity = f"observed:{source.source_key}"
        row.label = source.label
        row.status = source.status.value
        row.is_demo = 1 if source.is_demo else 0
        row.last_synced_at = source.last_synced_at
        row.last_seen_at = source.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_environment(
        self,
        project_id: str,
        environment: RuntimeEnvironment,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeEnvironmentRow).where(
            RuntimeEnvironmentRow.project_id == project_id,
            RuntimeEnvironmentRow.source_key == source_key,
            RuntimeEnvironmentRow.environment_id == environment.environment_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeEnvironmentRow(
                project_id=project_id,
                source_key=source_key,
                environment_id=environment.environment_id,
            )
            self._session.add(row)
        row.name = environment.name
        row.description = environment.description
        row.last_seen_at = environment.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_workspace(
        self,
        project_id: str,
        workspace: RuntimeWorkspace,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeWorkspaceRow).where(
            RuntimeWorkspaceRow.project_id == project_id,
            RuntimeWorkspaceRow.source_key == source_key,
            RuntimeWorkspaceRow.workspace_id == workspace.workspace_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeWorkspaceRow(
                project_id=project_id,
                source_key=source_key,
                workspace_id=workspace.workspace_id,
            )
            self._session.add(row)
        row.name = workspace.name
        row.environment_id = workspace.environment_id
        row.environment_name = workspace.environment_name
        row.owner = workspace.owner
        row.workspace_type = workspace.workspace_type
        row.last_seen_at = workspace.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_engine(
        self,
        project_id: str,
        engine: RuntimeEngine,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeEngineRow).where(
            RuntimeEngineRow.project_id == project_id,
            RuntimeEngineRow.source_key == source_key,
            RuntimeEngineRow.engine_id == engine.engine_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeEngineRow(
                project_id=project_id,
                source_key=source_key,
                engine_id=engine.engine_id,
            )
            self._session.add(row)
        row.name = engine.name
        row.status = engine.status.value
        row.raw_status = engine.raw_status
        row.availability = engine.availability
        row.version = engine.version
        row.environment_id = engine.environment_id
        row.environment_name = engine.environment_name
        row.workspace_id = engine.workspace_id
        row.workspace_name = engine.workspace_name
        row.last_seen_at = engine.last_seen_at
        row.source_updated_at = engine.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_job(
        self,
        project_id: str,
        job: RuntimeJob,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeJobRow).where(
            RuntimeJobRow.project_id == project_id,
            RuntimeJobRow.source_key == source_key,
            RuntimeJobRow.job_id == job.job_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeJobRow(
                project_id=project_id,
                source_key=source_key,
                job_id=job.job_id,
            )
            self._session.add(row)
        row.name = job.name
        row.display_name = job.display_name
        row.job_type = job.job_type
        row.status = job.status
        row.environment_id = job.environment_id
        row.environment_name = job.environment_name
        row.workspace_id = job.workspace_id
        row.workspace_name = job.workspace_name
        row.engine_id = job.engine_id
        row.engine_name = job.engine_name
        row.owner = job.owner
        row.artifact_name = job.artifact_name
        row.last_seen_at = job.last_seen_at
        row.source_updated_at = job.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_artifact(
        self,
        project_id: str,
        artifact: RuntimeArtifact,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeArtifactRow).where(
            RuntimeArtifactRow.project_id == project_id,
            RuntimeArtifactRow.source_key == source_key,
            RuntimeArtifactRow.artifact_id == artifact.artifact_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeArtifactRow(
                project_id=project_id,
                source_key=source_key,
                artifact_id=artifact.artifact_id,
            )
            self._session.add(row)
        row.name = artifact.name
        row.artifact_type = artifact.artifact_type
        row.version = artifact.version
        row.workspace_id = artifact.workspace_id
        row.workspace_name = artifact.workspace_name
        row.owner = artifact.owner
        row.last_seen_at = artifact.last_seen_at
        row.source_updated_at = artifact.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_connection(
        self,
        project_id: str,
        connection: RuntimeConnection,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeConnectionRow).where(
            RuntimeConnectionRow.project_id == project_id,
            RuntimeConnectionRow.source_key == source_key,
            RuntimeConnectionRow.connection_id == connection.connection_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeConnectionRow(
                project_id=project_id,
                source_key=source_key,
                connection_id=connection.connection_id,
            )
            self._session.add(row)
        row.name = connection.name
        row.connection_type = connection.connection_type
        row.workspace_id = connection.workspace_id
        row.workspace_name = connection.workspace_name
        row.owner = connection.owner
        row.last_seen_at = connection.last_seen_at
        row.source_updated_at = connection.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_job_dependency(
        self,
        project_id: str,
        dependency: RuntimeJobDependency,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeJobDependencyRow).where(
            RuntimeJobDependencyRow.project_id == project_id,
            RuntimeJobDependencyRow.source_key == source_key,
            RuntimeJobDependencyRow.job_id == dependency.job_id,
            RuntimeJobDependencyRow.dependency_type == dependency.dependency_type,
            RuntimeJobDependencyRow.dependency_id == dependency.dependency_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeJobDependencyRow(
                project_id=project_id,
                source_key=source_key,
                job_id=dependency.job_id,
                dependency_type=dependency.dependency_type,
                dependency_id=dependency.dependency_id,
            )
            self._session.add(row)
        row.job_name = dependency.job_name
        row.dependency_name = dependency.dependency_name
        row.dependency_kind = dependency.dependency_kind
        row.workspace_id = dependency.workspace_id
        row.workspace_name = dependency.workspace_name
        row.last_seen_at = dependency.last_seen_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_job_run(
        self,
        project_id: str,
        run: RuntimeJobRun,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeJobRunRow).where(
            RuntimeJobRunRow.project_id == project_id,
            RuntimeJobRunRow.source_key == source_key,
            RuntimeJobRunRow.run_id == run.run_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeJobRunRow(
                project_id=project_id,
                source_key=source_key,
                run_id=run.run_id,
            )
            self._session.add(row)
        row.job_id = run.job_id
        row.job_name = run.job_name
        row.execution_id = run.execution_id
        row.state = run.state.value
        row.environment_id = run.environment_id
        row.environment_name = run.environment_name
        row.workspace_id = run.workspace_id
        row.workspace_name = run.workspace_name
        row.engine_id = run.engine_id
        row.engine_name = run.engine_name
        row.started_at = run.started_at
        row.ended_at = run.ended_at
        row.duration_ms = run.duration_ms
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_plan(
        self,
        project_id: str,
        plan: RuntimePlan,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimePlanRow).where(
            RuntimePlanRow.project_id == project_id,
            RuntimePlanRow.source_key == source_key,
            RuntimePlanRow.plan_id == plan.plan_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimePlanRow(
                project_id=project_id,
                source_key=source_key,
                plan_id=plan.plan_id,
            )
            self._session.add(row)
        row.name = plan.name
        row.display_name = plan.display_name
        row.status = plan.status
        row.environment_id = plan.environment_id
        row.environment_name = plan.environment_name
        row.workspace_id = plan.workspace_id
        row.workspace_name = plan.workspace_name
        row.owner = plan.owner
        row.step_count = plan.step_count
        row.steps = [step.model_dump(mode="json") for step in plan.steps]
        row.last_seen_at = plan.last_seen_at
        row.source_updated_at = plan.source_updated_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_plan_run(
        self,
        project_id: str,
        run: RuntimePlanRun,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimePlanRunRow).where(
            RuntimePlanRunRow.project_id == project_id,
            RuntimePlanRunRow.source_key == source_key,
            RuntimePlanRunRow.run_id == run.run_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimePlanRunRow(
                project_id=project_id,
                source_key=source_key,
                run_id=run.run_id,
            )
            self._session.add(row)
        row.plan_id = run.plan_id
        row.plan_name = run.plan_name
        row.state = run.state.value
        row.environment_id = run.environment_id
        row.environment_name = run.environment_name
        row.workspace_id = run.workspace_id
        row.workspace_name = run.workspace_name
        row.started_at = run.started_at
        row.ended_at = run.ended_at
        row.duration_ms = run.duration_ms
        row.step_results = [step.model_dump(mode="json") for step in run.step_results]
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_execution(
        self,
        project_id: str,
        execution: RuntimeExecution,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.project_id == project_id,
            RuntimeExecutionRow.execution_id == execution.execution_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeExecutionRow(project_id=project_id, execution_id=execution.execution_id)
            self._session.add(row)
        row.job_name = execution.job_name
        row.project_name = execution.project_name
        row.state = execution.state.value
        row.started_at = execution.started_at
        row.ended_at = execution.ended_at
        row.duration_ms = execution.duration_ms
        row.source_key = source_key
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_failure(
        self,
        project_id: str,
        failure: RuntimeFailure,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeFailureRow).where(
            RuntimeFailureRow.project_id == project_id,
            RuntimeFailureRow.execution_id == failure.execution_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeFailureRow(project_id=project_id, execution_id=failure.execution_id)
            self._session.add(row)
        row.job_name = failure.job_name
        row.failed_at = failure.failed_at
        row.error_summary = redact_credentials(failure.error_summary)[:300]
        row.error_detail = redact_credentials(failure.error_detail) if failure.error_detail else None
        row.source_key = source_key
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_log_event(
        self,
        project_id: str,
        event: RuntimeLogEvent,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        fingerprint = log_event_fingerprint(event)
        stmt = select(RuntimeLogEventRow).where(
            RuntimeLogEventRow.project_id == project_id,
            RuntimeLogEventRow.execution_id == event.execution_id,
            RuntimeLogEventRow.line_ordinal == event.line_ordinal,
            RuntimeLogEventRow.event_fingerprint == fingerprint,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeLogEventRow(
                project_id=project_id,
                execution_id=event.execution_id,
                event_fingerprint=fingerprint,
                line_ordinal=event.line_ordinal,
            )
            self._session.add(row)
        row.timestamp = event.timestamp
        row.severity = event.severity.value
        row.message = redact_credentials(event.message)
        row.line_ordinal = event.line_ordinal
        row.source_key = source_key
        row.ingested_at = ingested_at
        await self._session.flush()

    async def upsert_component_metric(
        self,
        project_id: str,
        metric: RuntimeComponentMetric,
        source_key: str,
        ingested_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeComponentMetricRow).where(
            RuntimeComponentMetricRow.project_id == project_id,
            RuntimeComponentMetricRow.execution_id == metric.execution_id,
            RuntimeComponentMetricRow.component_key == metric.component_key,
            RuntimeComponentMetricRow.source_key == source_key,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeComponentMetricRow(
                project_id=project_id,
                execution_id=metric.execution_id,
                component_key=metric.component_key,
                source_key=source_key,
            )
            self._session.add(row)
        row.component_type = metric.component_type
        row.source_connector_type = metric.source_connector_type
        row.target_connector_type = metric.target_connector_type
        row.rows_total = metric.rows_total
        row.rows_rejected = metric.rows_rejected
        row.duration_ms = metric.duration_ms
        row.measured_at = metric.measured_at
        row.ingested_at = ingested_at
        await self._session.flush()

    async def update_sync_state(
        self, source_key: str, project_id: str, last_synced_at: datetime.datetime
    ) -> None:
        await self.record_sync_state(
            source_key=source_key,
            project_id=project_id,
            status=RuntimeSyncStatus.SUCCESS,
            last_synced_at=last_synced_at,
            failure_reason_code=None,
            error_summary=None,
            updated_at=last_synced_at,
        )

    async def record_sync_state(
        self,
        *,
        source_key: str,
        project_id: str,
        status: RuntimeSyncStatus,
        last_synced_at: datetime.datetime | None,
        failure_reason_code: str | None,
        error_summary: str | None,
        updated_at: datetime.datetime,
    ) -> None:
        stmt = select(RuntimeSyncStateRow).where(
            RuntimeSyncStateRow.source_key == source_key,
            RuntimeSyncStateRow.project_id == project_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSyncStateRow(source_key=source_key, project_id=project_id)
            self._session.add(row)
        row.last_synced_at = last_synced_at
        row.status = status.value
        row.failure_reason_code = failure_reason_code
        row.error_summary = error_summary
        row.updated_at = updated_at
        await self._session.flush()


def _to_execution(row: RuntimeExecutionRow) -> RuntimeExecution:
    return RuntimeExecution(
        execution_id=row.execution_id,
        job_name=row.job_name,
        project_name=row.project_name,
        state=RuntimeExecutionState(row.state),
        started_at=_as_utc(row.started_at),
        ended_at=_as_utc(row.ended_at),
        duration_ms=row.duration_ms,
    )


def _to_failure(row: RuntimeFailureRow) -> RuntimeFailure:
    return RuntimeFailure(
        execution_id=row.execution_id,
        job_name=row.job_name,
        failed_at=_as_utc(row.failed_at),
        error_summary=row.error_summary,
        error_detail=row.error_detail,
    )


def _to_log_event(row: RuntimeLogEventRow) -> RuntimeLogEvent:
    return RuntimeLogEvent(
        execution_id=row.execution_id,
        timestamp=_as_utc(row.timestamp),
        severity=RuntimeLogSeverity(row.severity),
        message=row.message,
        line_ordinal=row.line_ordinal,
    )


def _to_error_signature(row: RuntimeErrorSignatureRow) -> RuntimeErrorSignature:
    return RuntimeErrorSignature(
        project_id=row.project_id,
        signature_hash=row.signature_hash,
        error_code=row.error_code,
        exception_class=row.exception_class,
        component_name=row.component_name,
        summary_text=row.summary_text,
        causal_message=row.causal_message,
        occurrence_count=row.occurrence_count,
        first_seen_at=_as_utc(row.first_seen_at),
        last_seen_at=_as_utc(row.last_seen_at),
    )


def _to_sync_state(row: RuntimeSyncStateRow) -> RuntimeSyncState:
    return RuntimeSyncState(
        source_key=row.source_key,
        project_id=row.project_id,
        status=RuntimeSyncStatus(row.status),
        last_synced_at=_as_utc(row.last_synced_at),
        failure_reason_code=row.failure_reason_code,
        error_summary=row.error_summary,
        updated_at=_as_utc(row.updated_at),
    )


def _to_source(row: RuntimeSourceRow) -> RuntimeSource:
    return RuntimeSource(
        project_id=row.project_id,
        source_key=row.source_key,
        source_type=row.source_type,
        label=row.label,
        status=RuntimeSyncStatus(row.status),
        is_demo=bool(row.is_demo),
        last_synced_at=_as_utc(row.last_synced_at),
        last_seen_at=_as_utc(row.last_seen_at),
        ingested_at=_as_utc(row.ingested_at),
    )


def _to_source_health(row: RuntimeSourceHealthRow | None) -> RuntimeSourceHealth | None:
    if row is None:
        return None
    return RuntimeSourceHealth(
        last_attempt_at=_as_utc(row.last_attempt_at),
        last_success_at=_as_utc(row.last_success_at),
        last_failure_at=_as_utc(row.last_failure_at),
        consecutive_failures=row.consecutive_failures,
        last_failure_sanitized=row.last_failure_sanitized,
        total_cycles=row.total_cycles,
        total_records_ingested=row.total_records_ingested,
    )


def _to_source_read(row: RuntimeSourceRow, health: RuntimeSourceHealthRow | None) -> RuntimeSourceRead:
    return RuntimeSourceRead(
        id=row.id,
        project_id=row.project_id,
        source_key=row.source_key,
        source_type=row.source_type,
        label=row.label,
        status=row.status,
        enabled=row.enabled,
        sync_interval_seconds=row.sync_interval_seconds,
        auto_disable_threshold=row.auto_disable_threshold,
        disabled_reason=row.disabled_reason,
        is_demo=bool(row.is_demo),
        last_synced_at=_as_utc(row.last_synced_at),
        last_seen_at=_as_utc(row.last_seen_at),
        ingested_at=_as_utc(row.ingested_at),
        health=_to_source_health(health),
    )


def _to_workspace_project_map(row: RuntimeWorkspaceProjectMapRow) -> RuntimeWorkspaceProjectMap:
    return RuntimeWorkspaceProjectMap(
        id=row.id,
        source_id=row.source_id,
        container_key=row.container_key,
        container_name=row.container_name,
        project_id=row.project_id,
        created_at=_as_utc(row.created_at),
        created_by=row.created_by,
    )


def _to_engine(row: RuntimeEngineRow) -> RuntimeEngine:
    return RuntimeEngine(
        engine_id=row.engine_id,
        source_key=row.source_key,
        name=row.name,
        status=RuntimeEngineStatus(row.status),
        raw_status=row.raw_status,
        availability=row.availability,
        version=row.version,
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_job(row: RuntimeJobRow) -> RuntimeJob:
    return RuntimeJob(
        job_id=row.job_id,
        source_key=row.source_key,
        name=row.name,
        display_name=row.display_name,
        job_type=row.job_type,
        status=row.status,
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        engine_id=row.engine_id,
        engine_name=row.engine_name,
        owner=row.owner,
        artifact_name=row.artifact_name,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_artifact(row: RuntimeArtifactRow) -> RuntimeArtifact:
    return RuntimeArtifact(
        artifact_id=row.artifact_id,
        source_key=row.source_key,
        name=row.name,
        artifact_type=row.artifact_type,
        version=row.version,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        owner=row.owner,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_connection(row: RuntimeConnectionRow) -> RuntimeConnection:
    return RuntimeConnection(
        connection_id=row.connection_id,
        source_key=row.source_key,
        name=row.name,
        connection_type=row.connection_type,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        owner=row.owner,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_job_dependency(row: RuntimeJobDependencyRow) -> RuntimeJobDependency:
    return RuntimeJobDependency(
        job_id=row.job_id,
        job_name=row.job_name,
        source_key=row.source_key,
        dependency_type=row.dependency_type,
        dependency_id=row.dependency_id,
        dependency_name=row.dependency_name,
        dependency_kind=row.dependency_kind,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        last_seen_at=_as_utc(row.last_seen_at),
    )


def _to_job_run(row: RuntimeJobRunRow) -> RuntimeJobRun:
    return RuntimeJobRun(
        run_id=row.run_id,
        job_id=row.job_id,
        job_name=row.job_name,
        source_key=row.source_key,
        execution_id=row.execution_id,
        state=RuntimeExecutionState(row.state),
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        engine_id=row.engine_id,
        engine_name=row.engine_name,
        started_at=_as_utc(row.started_at),
        ended_at=_as_utc(row.ended_at),
        duration_ms=row.duration_ms,
    )


def _to_plan(row: RuntimePlanRow) -> RuntimePlan:
    steps = []
    for raw in row.steps or []:
        if isinstance(raw, dict):
            steps.append(RuntimePlanStep.model_validate(raw))
    return RuntimePlan(
        plan_id=row.plan_id,
        source_key=row.source_key,
        name=row.name,
        display_name=row.display_name,
        status=row.status,
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        owner=row.owner,
        step_count=row.step_count,
        steps=steps,
        last_seen_at=_as_utc(row.last_seen_at),
        source_updated_at=_as_utc(row.source_updated_at),
    )


def _to_plan_run(row: RuntimePlanRunRow) -> RuntimePlanRun:
    step_results = []
    for raw in row.step_results or []:
        if isinstance(raw, dict):
            step_results.append(RuntimePlanRunStepResult.model_validate(raw))
    return RuntimePlanRun(
        run_id=row.run_id,
        plan_id=row.plan_id,
        plan_name=row.plan_name,
        source_key=row.source_key,
        state=RuntimeExecutionState(row.state),
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        started_at=_as_utc(row.started_at),
        ended_at=_as_utc(row.ended_at),
        duration_ms=row.duration_ms,
        step_results=step_results,
    )


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = math.ceil((percentile / 100.0) * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]


def _to_health_summary(
    *,
    project_id: str,
    source_key: str,
    job_name: str,
    executions: list[RuntimeExecutionRow],
    metrics: list[RuntimeComponentMetricRow],
    incidents: list[RuntimeIncidentRow],
) -> RuntimeHealthSummary:
    ordered = sorted(executions, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    durations = [row.duration_ms for row in ordered if row.duration_ms is not None]
    rows_total_values = [row.rows_total or 0 for row in metrics if row.rows_total is not None]
    rows_rejected_values = [
        row.rows_rejected or 0 for row in metrics if row.rows_rejected is not None
    ]
    rows_total = sum(rows_total_values) if rows_total_values else None
    rows_rejected = sum(rows_rejected_values) if rows_rejected_values else None
    reject_ratio = (
        rows_rejected / rows_total
        if rows_total is not None and rows_total > 0 and rows_rejected is not None
        else None
    )
    recurrence_count = None
    signature_hash = None
    if incidents:
        latest = max(incidents, key=lambda row: _as_utc(row.first_bad_at) or datetime.datetime.min.replace(tzinfo=datetime.UTC))
        signature_hash = latest.signature_hash
        recurrence_count = sum(1 for row in incidents if row.signature_hash == signature_hash)
    return RuntimeHealthSummary(
        project_id=project_id,
        source_key=source_key,
        job_name=job_name,
        total_executions=len(ordered),
        failed_executions=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        last_failure_at=_as_utc(failed[0].started_at) if failed else None,
        last_execution_at=_as_utc(ordered[0].started_at) if ordered else None,
        p50_duration_ms=_percentile(durations, 50),
        p95_duration_ms=_percentile(durations, 95),
        rows_total=rows_total,
        rows_rejected=rows_rejected,
        reject_ratio=reject_ratio,
        recurrence_count=recurrence_count,
        signature_hash=signature_hash,
    )


def _to_job_health(
    *,
    project_id: str,
    source_key: str,
    job_id: str,
    runs: list[RuntimeJobRunRow],
    job: RuntimeJobRow | None,
) -> RuntimeJobHealth:
    ordered = sorted(runs, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    latest = ordered[0] if ordered else None
    fallback_name = latest.job_name if latest is not None else job_id
    return RuntimeJobHealth(
        project_id=project_id,
        source_key=source_key,
        job_id=job_id,
        job_name=job.name if job is not None else fallback_name,
        environment_name=(job.environment_name if job is not None else None)
        or (latest.environment_name if latest is not None else None),
        workspace_name=(job.workspace_name if job is not None else None)
        or (latest.workspace_name if latest is not None else None),
        engine_name=(job.engine_name if job is not None else None)
        or (latest.engine_name if latest is not None else None),
        total_runs=len(ordered),
        failed_runs=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        latest_run_at=_as_utc(latest.started_at) if latest is not None else None,
        latest_state=RuntimeExecutionState(latest.state) if latest is not None else None,
    )


def _to_engine_health(
    *,
    project_id: str,
    source_key: str,
    engine_id: str,
    runs: list[RuntimeJobRunRow],
    engine: RuntimeEngineRow | None,
) -> RuntimeEngineHealth:
    ordered = sorted(runs, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    latest = ordered[0] if ordered else None
    durations = [row.duration_ms for row in ordered if row.duration_ms is not None]
    fallback_name = latest.engine_name if latest is not None and latest.engine_name else engine_id
    return RuntimeEngineHealth(
        project_id=project_id,
        source_key=source_key,
        engine_id=engine_id,
        engine_name=engine.name if engine is not None else fallback_name,
        engine_status=RuntimeEngineStatus(engine.status) if engine is not None else None,
        environment_name=(engine.environment_name if engine is not None else None)
        or (latest.environment_name if latest is not None else None),
        workspace_name=(engine.workspace_name if engine is not None else None)
        or (latest.workspace_name if latest is not None else None),
        total_runs=len(ordered),
        failed_runs=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        latest_run_at=_as_utc(latest.started_at) if latest is not None else None,
        latest_state=RuntimeExecutionState(latest.state) if latest is not None else None,
        p50_duration_ms=_percentile(durations, 50),
        p95_duration_ms=_percentile(durations, 95),
    )


def _to_plan_health(
    *,
    project_id: str,
    source_key: str,
    plan_id: str,
    runs: list[RuntimePlanRunRow],
    plan: RuntimePlanRow | None,
) -> RuntimePlanHealth:
    ordered = sorted(runs, key=lambda row: _as_utc(row.started_at), reverse=True)
    failed = [row for row in ordered if row.state == RuntimeExecutionState.FAILED.value]
    streak = 0
    for row in ordered:
        if row.state == RuntimeExecutionState.FAILED.value:
            streak += 1
        else:
            break
    latest = ordered[0] if ordered else None
    fallback_name = latest.plan_name if latest is not None else plan_id
    return RuntimePlanHealth(
        project_id=project_id,
        source_key=source_key,
        plan_id=plan_id,
        plan_name=plan.name if plan is not None else fallback_name,
        environment_name=(plan.environment_name if plan is not None else None)
        or (latest.environment_name if latest is not None else None),
        workspace_name=(plan.workspace_name if plan is not None else None)
        or (latest.workspace_name if latest is not None else None),
        total_runs=len(ordered),
        failed_runs=len(failed),
        failure_rate=(len(failed) / len(ordered)) if ordered else 0.0,
        current_failure_streak=streak,
        latest_run_at=_as_utc(latest.started_at) if latest is not None else None,
        latest_finish_at=_as_utc(latest.ended_at) if latest is not None else None,
        latest_state=RuntimeExecutionState(latest.state) if latest is not None else None,
    )
