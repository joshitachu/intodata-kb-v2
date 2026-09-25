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
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import Float, Integer, and_, case, cast, delete, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import (
    ESTATE_KEY_PREFIX,
    derive_estate_key,
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
    RuntimeSourceReadGrantRow,
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
    ListRuntimeDeploymentsInput,
    ListRuntimeEnginesInput,
    ListRuntimeErrorSignaturesInput,
    ListRuntimeInventoryInput,
    ListRuntimeJobRunsInput,
    ListRuntimeJobReliabilityInput,
    ListRuntimeJobScheduleProfileInput,
    ListRuntimeJobThroughputInput,
    ListRuntimeJobsInput,
    ListRuntimePlanRunsInput,
    ListRuntimePlansInput,
    RuntimeArtifact,
    RuntimeComponentHotspot,
    RuntimeConnection,
    RuntimeDependencySummary,
    RuntimeDeployment,
    RuntimeErrorSignature,
    RuntimeExecution,
    RuntimeEngine,
    RuntimeEngineHealth,
    RuntimeEngineStatus,
    RuntimeEnvironment,
    RuntimeEstateOverview,
    RuntimeExecutionState,
    RuntimeExecutableKind,
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
    RuntimeJobScheduleProfile,
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
    RuntimeSourceReadGrant,
    RuntimeSourceUpdate,
    RuntimeTrendBucket,
    RuntimeTrendCoverage,
    RuntimeTrendInput,
    RuntimeWorkspaceProjectMap,
    RuntimeSyncState,
    RuntimeSyncStatus,
    RuntimeWorkspace,
)
from src.runtime.read_scope import RuntimeReadTerm
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


# --- Run-state semantics -----------------------------------------------------
#
# Two different questions get asked of `state`, and widening the enum made the
# difference load-bearing. Naming them separately is what stops the next state
# from being folded into whichever predicate it was nearest.
#
# 1. "How often did this job FAIL?"  -> failure_rate, failed_runs, the failure
#    streak, and diagnostic-coverage counts. FAILED only, forever. A misfire is
#    a run that never started; counting it as a failure would silently restate
#    every historical failure rate in the product and make yesterday's number
#    incomparable with today's.
#
# 2. "Does a human need to look at this?" -> attention_only, and the
#    unhealthy engines/jobs/plans surfaces. FAILED plus the states that mean
#    work silently did not happen.
#
#: Runs that warrant a human. TERMINATED is deliberately absent: a termination
#: is an operator action, so somebody has already looked.
_ATTENTION_STATES: tuple[RuntimeExecutionState, ...] = (
    RuntimeExecutionState.FAILED,
    RuntimeExecutionState.MISFIRED,
    RuntimeExecutionState.TIMED_OUT,
)
_ATTENTION_STATE_VALUES: tuple[str, ...] = tuple(state.value for state in _ATTENTION_STATES)

#: States whose runs legitimately carry no end timestamp, and therefore must not
#: be reported as "started but never finished". RUNNING has not finished yet;
#: MISFIRED never started, so no source can stamp it with an end.
_OPEN_ENDED_STATES: tuple[RuntimeExecutionState, ...] = (
    RuntimeExecutionState.RUNNING,
    RuntimeExecutionState.MISFIRED,
)
_OPEN_ENDED_STATE_VALUES: tuple[str, ...] = tuple(state.value for state in _OPEN_ENDED_STATES)


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


_SECONDS_PER_DAY = 86_400
# Defensive ceiling on the day-by-day offset scan below. 200 years of daily
# probes is far beyond any runtime window and still costs microseconds; the cap
# only exists so a nonsensical `since`/`until` cannot spin.
_OFFSET_SCAN_MAX_DAYS = 73_000


def _zone_offset_seconds(zone: ZoneInfo, instant: datetime.datetime) -> int:
    """UTC offset of `zone` at one absolute instant, in seconds."""
    offset = instant.astimezone(zone).utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def _offset_transition_instant(
    zone: ZoneInfo, low: datetime.datetime, high: datetime.datetime
) -> datetime.datetime:
    """First minute in (low, high] where `zone`'s UTC offset stops matching low's.

    Every IANA transition since 1970 lands on a whole minute, so a bisection to
    minute resolution finds the exact instant rather than an approximation.
    """
    base = _zone_offset_seconds(zone, low)
    minute = datetime.timedelta(minutes=1)
    while high - low > minute:
        mid = (low + (high - low) / 2).replace(second=0, microsecond=0)
        if mid <= low:
            break
        if _zone_offset_seconds(zone, mid) == base:
            low = mid
        else:
            high = mid
    return high


def _utc_offset_segments(
    zone: ZoneInfo, lower: datetime.datetime, upper: datetime.datetime
) -> list[tuple[datetime.datetime | None, int]]:
    """`zone`'s UTC offset over [lower, upper] as ordered ``(start_utc, seconds)``.

    The first segment's start is None, meaning "everything before the next
    boundary". One segment means the whole window has a single offset.

    Postgres could do this with ``AT TIME ZONE``, but this repository also runs
    on SQLite, which has no IANA database at all. Deriving the offsets in Python
    from the stdlib tz database yields ONE expression that is correct on both
    backends, keeps the tested path identical to the production path, and drops
    the dependency on the database server's tzdata and its session TimeZone.

    DST-awareness is not optional here. A year of Amsterdam runs spans both CET
    and CEST; a single fixed offset would misreport half of them by an hour --
    enough to move a 23:31 job out of a 22:00-07:00 window entirely, or to split
    one nightly schedule into two apparent ones.
    """
    lower = lower - datetime.timedelta(days=1)
    upper = upper + datetime.timedelta(days=1)
    segments: list[tuple[datetime.datetime | None, int]] = [
        (None, _zone_offset_seconds(zone, lower))
    ]
    probe = lower
    day = datetime.timedelta(days=1)
    for _ in range(_OFFSET_SCAN_MAX_DAYS):
        if probe >= upper:
            break
        nxt = min(probe + day, upper)
        if _zone_offset_seconds(zone, nxt) != _zone_offset_seconds(zone, probe):
            boundary = _offset_transition_instant(zone, probe, nxt)
            segments.append((boundary, _zone_offset_seconds(zone, nxt)))
        probe = nxt
    return segments


def _local_epoch_seconds(dialect: str, column, segments: Sequence[tuple[datetime.datetime | None, int]]):
    """`column` as WHOLE epoch seconds shifted onto local wall-clock time.

    Not a real instant any more -- it is "seconds since the local epoch", which
    is exactly what time-of-day arithmetic needs and nothing else should consume.

    Rounded to the second rather than truncated, and that is load-bearing.
    SQLite reaches epoch seconds through ``julianday``, a double whose mantissa
    leaves roughly 20 ms of slack at present-day Julian day numbers; truncating
    it turned an 01:15:00 start into 01:14:59 on every row. Rounding lands on the
    stored second exactly, and gives ONE integer both backends agree on, so the
    day/position arithmetic downstream is exact integer arithmetic rather than
    floating point with a floor at the end of it.
    """
    utc_seconds = _epoch_seconds(dialect, column)
    if len(segments) == 1:
        local = utc_seconds + segments[0][1]
    else:
        # Ordered, first-match-wins branches: everything before boundary i
        # carries the offset of the segment that precedes it.
        whens = [
            (column < boundary, segments[index][1])
            for index, (boundary, _offset) in enumerate(segments[1:])
        ]
        local = utc_seconds + case(*whens, else_=segments[-1][1])
    # floor(x + 0.5) rather than a bare cast: Postgres ROUNDS numeric->integer
    # casts while SQLite TRUNCATES them, so only an explicit round is the same
    # operation on both.
    return cast(func.floor(local + 0.5), Integer)


def _seconds_to_time(seconds: int) -> datetime.time:
    """Seconds-into-a-local-day as a clock time, wrapping at midnight."""
    seconds %= _SECONDS_PER_DAY
    return datetime.time(seconds // 3600, (seconds % 3600) // 60, seconds % 60)


class _GapStats(NamedTuple):
    """Inter-run gap quantiles for ONE job in ONE environment, seconds."""

    p25: int | None
    p50: int | None
    p75: int | None
    sample_size: int


class _ResolvedReadGrant(NamedTuple):
    """One attachment to a FOREIGN estate, with the stamps it actually reaches.

    `exposes` is the attachment's real blast radius: the source's estate key
    plus every project its workspace map re-points rows to. It is computed
    rather than assumed so the API can state it at attach time -- an attachment
    that silently exposed a third project's rows would be the worst kind of
    quiet.
    """

    source_id: int
    source_key: str
    source_label: str
    source_estate_key: str
    environment_ids: list[str] | None
    window_days: int | None
    exposes: list[str]


def _merge_reliability_by_job_name(
    rollups: "Iterable[RuntimeJobReliability]",
) -> dict[str, RuntimeJobReliability]:
    """Collapse reliability rollups to one row per job name.

    `list_job_reliability` groups by (project_id, source_key, job_name), which
    is right for an operations console. A documentation read that spans several
    projects -- a shared tenant -- can therefore return two rows for one name,
    and the dict comprehension this replaces kept whichever arrived last,
    silently discarding the other's runs.

    Counts are summed and the rate recomputed from the sums rather than
    averaged, `first/last_run_at` take the true extremes, and the streak takes
    the maximum: a failure streak spanning two independently scheduled estates
    is not a quantity that adds up, and the longer one is the conservative
    alarm. `source_key` follows the largest contributor, since the field names
    one mirror and cannot describe two.
    """
    merged: dict[str, RuntimeJobReliability] = {}
    for rollup in rollups:
        existing = merged.get(rollup.job_name)
        if existing is None:
            merged[rollup.job_name] = rollup
            continue
        total = existing.total_runs + rollup.total_runs
        failed = existing.failed_runs + rollup.failed_runs
        dominant = existing if existing.total_runs >= rollup.total_runs else rollup
        merged[rollup.job_name] = existing.model_copy(
            update={
                "project_id": dominant.project_id,
                "source_key": dominant.source_key,
                "total_runs": total,
                "success_runs": existing.success_runs + rollup.success_runs,
                "failed_runs": failed,
                "unfinished_runs": existing.unfinished_runs + rollup.unfinished_runs,
                "failure_rate": (failed / total) if total else 0.0,
                "current_failure_streak": max(
                    existing.current_failure_streak, rollup.current_failure_streak
                ),
                "first_run_at": min(
                    (item for item in (existing.first_run_at, rollup.first_run_at) if item),
                    default=None,
                ),
                "last_run_at": max(
                    (item for item in (existing.last_run_at, rollup.last_run_at) if item),
                    default=None,
                ),
            }
        )
    return merged


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


#: The only two things a mirrored runtime row can mean. Anything else -- an
#: undeclared kind, an unknown provider, a provider that has no opinion -- is
#: `"unknown"`, which is an answer, not a gap to be filled with a guess.
_LIVENESS_VALUES = frozenset({"execution", "lifecycle"})


def _provider_for_source_type(source_type: str | None):
    """The registered provider a stored `runtime_sources.source_type` belongs to.

    That column holds whatever key was current when the row was written, and a
    provider keeps its historical keys in `source_type_aliases()` -- TMC mirrors
    predating self-service source configuration still say `talend_tmc`, not
    `tmc`. Resolving on `provider_key()` alone would answer "no provider" for a
    mirror this codebase ships a provider for, which downstream reads as "we
    cannot say what these rows mean". `elastic_aggregates._elastic_source_types`
    resolves aliases the same way for the same reason.

    The registry import is deferred because `src.runtime.store.__init__` imports
    this module, and the registry pulls in provider modules that import
    `src.runtime.providers`, which imports `src.runtime.store`.
    `runtime.sync.service` defers the same import for the same reason.
    """
    if not source_type:
        return None
    from src.runtime.source_registry import all_source_providers, get_source_provider

    provider = get_source_provider(source_type)
    if provider is not None:
        return provider
    for candidate in all_source_providers():
        if source_type in {str(alias) for alias in candidate.source_type_aliases()}:
            return candidate
    return None


def _executable_semantics_for(source_type: str | None) -> Mapping[str, str]:
    """The provider's own declaration of what one of its rows MEANS, per kind.

    Read-only consumption: this reports what the provider declares and derives
    nothing of its own. An unregistered source type contributes an empty
    mapping, so each of its kinds resolves to `"unknown"` rather than being
    assumed to be an execution.
    """
    provider = _provider_for_source_type(source_type)
    if provider is None:
        return {}
    return provider.executable_semantics()


def _liveness_of(
    semantics: Mapping[str, str], executable_kind: str | None
) -> str:
    """Project a provider declaration onto one row's kind."""
    value = semantics.get(executable_kind) if executable_kind else None
    return value if value in _LIVENESS_VALUES else "unknown"


#: A captured log at the provider's row ceiling is a conservative truncation
#: signal. Shared by every diagnostic-coverage aggregate so the two surfaces
#: (operations console, documentation evidence) cannot report different
#: truncation counts for the same execution.
_LOG_CAPTURE_CEILING_LINES = 10_000

#: A diagnosis is only "useful" if it is confident AND says something. The two
#: generic patterns merely tell an operator to go read a log, which is exactly
#: what the diagnosis was supposed to spare them.
_USEFUL_DIAGNOSIS_CONFIDENCES: tuple[str, ...] = ("high", "medium")
_UNINFORMATIVE_PATTERN_IDS: tuple[str, ...] = ("generic.unknown", "exception.generic")


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


class _ProjectFilterParams(Protocol):
    """The two fields `_apply_project_filter` reads off a filter-params model.

    Structural rather than a shared base class: the inputs it serves disagree
    about whether `project_id` is required, and forcing them onto one base to
    satisfy a type hint would change contracts that are reachable from an LLM
    tool call.
    """

    project_id: str | None
    read_terms: list[RuntimeReadTerm]


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

    @staticmethod
    def _apply_read_projects(stmt, column, project_ids: Sequence[str]):
        """Filter to the projects one documentation read may draw from.

        Distinct from `_apply_scope`, which is RBAC. This is the read set a
        project resolved to: itself, plus the projects whose rows a granted
        source stamps (`resolve_runtime_read_projects`). A single-element set
        emits the identical `==` predicate the lane used before read grants
        existed, so the unshared path keeps its query plan and its index use.
        """
        values = list(project_ids)
        if len(values) == 1:
            return stmt.where(column == values[0])
        return stmt.where(column.in_(values))

    def _read_term_environment_predicate(
        self,
        term: RuntimeReadTerm,
        *,
        environment_column: Any,
        execution_id_column: Any,
    ):
        """The environment half of one read term, or None when it restricts nothing.

        Three outcomes, and the difference between the last two is the whole
        point of the grant model:

        * `environment_ids is None` -> None. The grant names no environment, so
          it restricts nothing and no predicate is emitted.
        * the table can express the restriction -> that predicate. Directly when
          the row carries an environment, otherwise through the execution ids
          observed in those environments (`_environment_execution_ids`), which
          is exactly how the documentation lane already narrows
          `runtime_executions` -- a table that carries no environment column.
        * the table can express NEITHER -> `false()`. The term contributes
          nothing, so a narrowed grant exposes no row of that table at all.

        An EMPTY list is the deliberate "nothing" case and also yields
        `false()`; it is never read as "unset". That is the distinction
        `RuntimeSourceReadGrantRow` and `RuntimeDocReadScope` already draw, and
        collapsing the two would turn "expose no environment" into "expose every
        environment" -- the most dangerous possible misreading of a grant.
        """
        if term.environment_ids is None:
            return None
        if not term.environment_ids:
            return false()
        if environment_column is not None:
            return environment_column.in_(term.environment_ids)
        if execution_id_column is not None:
            return execution_id_column.in_(
                self._environment_execution_ids(term.project_id, term.environment_ids)
            )
        return false()

    def _apply_read_terms(
        self,
        stmt,
        project_column,
        terms: Sequence[RuntimeReadTerm],
        *,
        environment_column: Any = None,
        time_column: Any = None,
        execution_id_column: Any = None,
    ):
        """Apply a resolved read set as the row-stamp filter of one statement.

        Three shapes, in order:

        * NO terms -> the statement is returned unchanged. "Nothing was
          resolved" is not "nothing is visible"; the caller falls back to
          whatever predicate it used before read grants existed.
        * ONE unrestricted term -> a bare `project_column == id`. Deliberately
          not a one-element `IN` or a one-branch `OR`: the unshared path is
          almost every read this store serves, and it must keep the query plan
          and the index use it had. Same reason `_apply_read_projects`
          special-cases a single element.
        * otherwise -> a per-project disjunction. A grant's environment and
          window are limits the GRANTOR set on THEIR estate, so they attach to
          that project's branch only. Applied flatly, a `prod` grant would
          either leak the grantor's non-prod rows (union) or hide the reader's
          own non-prod rows (intersection); neither is what anyone agreed to.

        Which columns a call site passes decides what a narrowed grant can
        reach, so the rules are fixed rather than per-caller taste:

        `environment_column` / `execution_id_column`
            Pass the row's own environment column when it has one. When it does
            not but the row carries an execution id, pass that instead and the
            narrowing routes through the job-run table. Pass NEITHER only when
            the table genuinely has neither -- `runtime_artifacts`,
            `runtime_connections`, `runtime_job_dependencies` -- and accept the
            consequence: a grant narrowed to specific environments exposes no
            row of those inventory tables. The rejected alternative was to let
            an unnarrowable table through unfiltered, which would hand a reader
            granted one environment the grantor's COMPLETE artifact and
            connection inventory. A missing row is a visible, reportable gap; a
            leaked one cannot be taken back.

        `time_column`
            Pass it for event-shaped tables -- `runtime_executions`,
            `runtime_job_runs`, `runtime_plan_runs`, `runtime_failures`,
            `runtime_log_events` -- so a windowed grant bounds the history it
            returns. Do NOT pass it for inventory: `runtime_jobs`,
            `runtime_engines`, `runtime_plans`, `runtime_workspaces` describe
            what EXISTS, and a window grant bounds how much history a reader may
            see, not whether an entity exists. A window applied to inventory
            would silently delete jobs from the estate a reader was granted, and
            (since `last_seen_at` and friends are nullable) do it inconsistently.
            When a term carries a window and no `time_column` is passed, the
            window is therefore dropped on purpose, not by oversight.
        """
        if not terms:
            return stmt
        if len(terms) == 1 and terms[0].unrestricted:
            return stmt.where(project_column == terms[0].project_id)
        clauses: list[Any] = []
        for term in terms:
            predicates: list[Any] = [project_column == term.project_id]
            environment = self._read_term_environment_predicate(
                term,
                environment_column=environment_column,
                execution_id_column=execution_id_column,
            )
            if environment is not None:
                predicates.append(environment)
            if term.since is not None and time_column is not None:
                predicates.append(time_column >= term.since)
            clauses.append(and_(*predicates))
        return stmt.where(or_(*clauses))

    def _apply_project_filter(
        self,
        stmt,
        project_column,
        params: _ProjectFilterParams,
        *,
        environment_column: Any = None,
        time_column: Any = None,
        execution_id_column: Any = None,
    ):
        """The row-stamp filter for one request: the resolved read set if there is
        one, else the single requested project, else nothing.

        Every list/count pair in this file must be filtered through this one
        call with the SAME columns. A page and its `total` computed from
        different predicates is the standing trap here, and a read set widened
        on one side of the pair would report a row count nobody can page to.
        """
        if params.read_terms:
            return self._apply_read_terms(
                stmt,
                project_column,
                params.read_terms,
                environment_column=environment_column,
                time_column=time_column,
                execution_id_column=execution_id_column,
            )
        if params.project_id is not None:
            return stmt.where(project_column == params.project_id)
        return stmt

    def _apply_doc_read_filter(
        self,
        stmt,
        project_column,
        project_ids: Sequence[str],
        read_terms: Sequence[RuntimeReadTerm],
        *,
        environment_column: Any = None,
        time_column: Any = None,
        execution_id_column: Any = None,
    ):
        """The row-stamp filter of one documentation sub-query.

        The documentation lane resolves its read set itself (it has no user
        behind it), so it arrives as an explicit pair rather than on a params
        model: `project_ids` is the FLAT read set the lane has always passed, and
        `read_terms` is the same set carrying each grant's own limits.

        The terms REPLACE the flat list rather than joining it -- a term already
        emits its own project predicate, and the two name the same projects
        (`resolve_runtime_read_scope` and `resolve_runtime_read_projects` walk the
        same grants in the same order). Emitting both would only restate one
        predicate twice.

        For an UNGRANTED project the terms are one unrestricted term, and
        `_apply_read_terms` emits exactly the bare `project_id = ...` that
        `_apply_read_projects` emits for a one-element list: the overwhelmingly
        common path keeps its SQL, its plan and its index use. The fallback to
        `_apply_read_projects` is what a caller that resolved no terms at all
        still gets.

        Which columns to pass is not per-caller taste -- see `_apply_read_terms`.
        A sub-query whose table can express neither environment nor execution-id
        narrowing FAILS CLOSED for a narrowed grant, and the coverage record has
        to say so.
        """
        if read_terms:
            return self._apply_read_terms(
                stmt,
                project_column,
                read_terms,
                environment_column=environment_column,
                time_column=time_column,
                execution_id_column=execution_id_column,
            )
        return self._apply_read_projects(stmt, project_column, project_ids)

    @property
    def _dialect(self) -> str:
        return self._session.bind.dialect.name if self._session.bind is not None else ""

    def _apply_execution_filters(self, stmt, params: ListExecutionsInput):
        """Filters shared by the execution list and its count.

        Kept in one place so a page and its `total` can never be computed from
        different predicates -- including the read set, which is why this is no
        longer a staticmethod.

        `ListExecutionsInput` carries no `project_id`: this surface was always
        filtered by the caller's RBAC scope alone. That stops being sufficient
        the moment a grant WIDENS that scope to include a granting project, so
        the terms are applied here as well. `runtime_executions` has no
        environment column, so environment narrowing routes through
        `execution_id`, and `started_at` is the window column.
        """
        stmt = self._apply_read_terms(
            stmt,
            RuntimeExecutionRow.project_id,
            params.read_terms,
            time_column=RuntimeExecutionRow.started_at,
            execution_id_column=RuntimeExecutionRow.execution_id,
        )
        if params.project_name:
            stmt = stmt.where(RuntimeExecutionRow.project_name == params.project_name)
        if params.job_name:
            stmt = stmt.where(RuntimeExecutionRow.job_name == params.job_name)
        if params.state is not None:
            stmt = stmt.where(RuntimeExecutionRow.state == params.state.value)
        stmt = stmt.where(RuntimeExecutionRow.executable_kind == params.executable_kind.value)
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

    def _apply_failure_filters(self, stmt, params: DetectFailuresInput):
        """Filters shared by the failure list and its count.

        `runtime_failures` carries no environment column but does carry an
        execution id, so a narrowed grant reaches it the same way the
        documentation lane reaches `runtime_executions`. `failed_at` is the
        window column: a failure is an event, and a windowed grant bounds it.
        """
        stmt = self._apply_read_terms(
            stmt,
            RuntimeFailureRow.project_id,
            params.read_terms,
            time_column=RuntimeFailureRow.failed_at,
            execution_id_column=RuntimeFailureRow.execution_id,
        )
        if params.since:
            stmt = stmt.where(RuntimeFailureRow.failed_at >= params.since)
        if params.until:
            stmt = stmt.where(RuntimeFailureRow.failed_at <= params.until)
        return stmt

    def _apply_execution_log_filters(self, stmt, params: GetExecutionLogsInput):
        """Filters shared by one execution's log page and its count.

        The execution id is already the narrowest possible filter, but the read
        set is still applied: the caller's scope may have been WIDENED by a
        grant, and an execution id is not secret -- the same id exists in the
        granting project's mirror. Without the terms, a reader granted only PROD
        could name an ACC execution and read its log lines.
        """
        stmt = stmt.where(RuntimeLogEventRow.execution_id == params.execution_id)
        stmt = self._apply_read_terms(
            stmt,
            RuntimeLogEventRow.project_id,
            params.read_terms,
            time_column=RuntimeLogEventRow.timestamp,
            execution_id_column=RuntimeLogEventRow.execution_id,
        )
        if params.min_severity is not None:
            stmt = stmt.where(
                RuntimeLogEventRow.severity.in_(_severities_at_or_above(params.min_severity))
            )
        return stmt

    async def get_execution_details(
        self, scope: ProjectScope, params: GetExecutionDetailsInput
    ) -> RuntimeExecution:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        stmt = select(RuntimeExecutionRow).where(
            RuntimeExecutionRow.execution_id == params.execution_id
        )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        # See `_apply_execution_log_filters`: a widened scope makes the read set
        # the only thing standing between a granted reader and an execution the
        # grant did not cover.
        stmt = self._apply_read_terms(
            stmt,
            RuntimeExecutionRow.project_id,
            params.read_terms,
            time_column=RuntimeExecutionRow.started_at,
            execution_id_column=RuntimeExecutionRow.execution_id,
        )
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
        stmt = self._apply_failure_filters(stmt, params)
        stmt = stmt.order_by(RuntimeFailureRow.failed_at.desc(), RuntimeFailureRow.id.desc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_failure(row) for row in rows]

    async def get_execution_logs(
        self, scope: ProjectScope, params: GetExecutionLogsInput
    ) -> list[RuntimeLogEvent]:
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Unknown execution_id: {params.execution_id}")
        stmt = select(RuntimeLogEventRow)
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        stmt = self._apply_execution_log_filters(stmt, params)
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
        stmt = self._apply_failure_filters(stmt, params)
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_execution_logs(self, scope: ProjectScope, params: GetExecutionLogsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeLogEventRow)
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        stmt = self._apply_execution_log_filters(stmt, params)
        return int((await self._session.execute(stmt)).scalar_one())

    async def prune_expired_log_events(self, *, now: datetime.datetime | None = None, retention_days: int = 90) -> int:
        cutoff = (now or datetime.datetime.now(datetime.UTC)) - datetime.timedelta(days=retention_days)
        result = await self._session.execute(delete(RuntimeLogEventRow).where(RuntimeLogEventRow.timestamp < cutoff))
        await self._session.flush()
        return int(result.rowcount or 0)

    async def clear_engine_scope_log_events(
        self, project_id: str, execution_ids: Sequence[str]
    ) -> int:
        """Drop the previous deployment-log snapshot for these deployments.

        An `execution` scoped row is history: one run happened once, and its
        lines never change, so upserting on the fingerprint is idempotent. An
        `engine` scoped row is the opposite -- a rolling tail of a Route's
        shared OSGi log, re-read in full on every sync.

        `line_ordinal` is the line's position inside that 100-line tail, so a
        route that emitted anything between syncs shifts every retained line
        by one. New ordinal, new fingerprint, new row: the unique index can
        never match and each cycle stored another near-duplicate copy.
        Measured here, one deployment held 200 rows carrying 49 distinct
        messages after two syncs, and the table was growing by ~2.500 rows
        every ten minutes with nothing expiring for ninety days.

        So the snapshot is replaced rather than accumulated: what the panel
        shows is then the current tail, which is what it claims to be.
        """
        ids = [item for item in dict.fromkeys(execution_ids) if item]
        if not ids:
            return 0
        result = await self._session.execute(
            delete(RuntimeLogEventRow).where(
                RuntimeLogEventRow.project_id == project_id,
                RuntimeLogEventRow.scope == "engine",
                RuntimeLogEventRow.execution_id.in_(ids),
            )
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def search_log_events(
        self, scope: ProjectScope, *, query: str, project_id: str | None = None,
        execution_id: str | None = None, since: datetime.datetime | None = None,
        until: datetime.datetime | None = None, limit: int = 20, snippet_chars: int = 300,
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> list[dict[str, object]]:
        """Search redacted raw logs with bounded snippets; scope is always server-derived.

        A blank ``query`` is the RECENCY mode: newest-first within the other
        filters, no text predicate. "Show me the latest logs" / "what happened
        overnight" are the natural questions and were previously unanswerable --
        every path required a keyword, so the request came back empty and
        indistinguishable from "this project has no logs". Verified against a
        173,537-row corpus: ``plainto_tsquery('simple', 'latest logs')`` matched
        zero rows.

        ``read_terms`` is the resolved read set and takes precedence over
        ``project_id``; empty (the default) keeps every existing caller on the
        exact predicate it had. This is the read path behind the agent's
        ``search_logs`` tool and therefore the one that decides whether a shared
        tenant can be QUESTIONED at all rather than merely documented.
        `runtime_log_events` carries no environment column, so a grant narrowed
        to an environment must route through ``execution_id`` -- getting that
        wrong returns the grantor's other environments verbatim, redaction being
        the only thing left in the way. ``timestamp`` bounds a windowed grant.
        """
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeLogEventRow)
        stmt = self._apply_scope(stmt, RuntimeLogEventRow.project_id, scope)
        if read_terms:
            stmt = self._apply_read_terms(
                stmt,
                RuntimeLogEventRow.project_id,
                read_terms,
                time_column=RuntimeLogEventRow.timestamp,
                execution_id_column=RuntimeLogEventRow.execution_id,
            )
        elif project_id is not None:
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
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> list[RuntimeSyncState]:
        """Return scoped source/project sync freshness and health rows.

        ``read_terms`` takes precedence over ``project_id``; empty keeps every
        existing caller unchanged. `runtime_sync_state` is one current-state row
        per (source, project) -- no environment column, no execution id -- so an
        environment-narrowed grant exposes none of the grantor's rows, per the
        fail-closed rule in `_apply_read_terms`. Its timestamps are watermarks
        rather than history, so a grant WINDOW does not apply: "when did this
        mirror last sync" is a fact about now, and hiding it would report a
        healthy shared source as never synced.
        """
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSyncStateRow)
        stmt = self._apply_scope(stmt, RuntimeSyncStateRow.project_id, scope)
        if source_key is not None:
            stmt = stmt.where(RuntimeSyncStateRow.source_key == source_key)
        if read_terms:
            stmt = self._apply_read_terms(
                stmt, RuntimeSyncStateRow.project_id, read_terms
            )
        elif project_id is not None:
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
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> list[RuntimeHealthSummary]:
        """Return DB-derived health rollups grouped by project/source/job.

        Three grouped statements, no whole-row reads. Before this the rollup
        fetched every in-scope execution (55k rows), every component metric (26k)
        and every incident, then reduced them in Python to at most `limit`
        summaries.

        ``read_terms`` takes precedence over ``project_id``; empty keeps every
        existing caller unchanged. Only the execution population is widened --
        the metric and incident rollups join THROUGH it, so they inherit the
        read set rather than re-stating it, which is the same reason they do not
        re-state the scope.
        """
        if self._denies_all(scope):
            return []
        executions = self._health_execution_base(
            scope, project_id, source_key, read_terms=read_terms
        )
        group = [executions.c.project_id, executions.c.source_key, executions.c.job_name]
        rows = (
            await self._session.execute(self._health_execution_rollup(executions, group))
        ).mappings().all()
        if not rows:
            return []

        metrics = await self._health_metric_totals(executions, group, source_key)
        incidents = await self._health_incident_signatures(executions, group)

        summaries: list[RuntimeHealthSummary] = []
        for row in rows:
            key = (row["project_id"], row["source_key"], row["job_name"])
            rows_total, rows_rejected = metrics.get(key, (None, None))
            recurrence_count, signature_hash = incidents.get(key, (None, None))
            summaries.append(
                RuntimeHealthSummary(
                    project_id=row["project_id"],
                    source_key=row["source_key"],
                    job_name=row["job_name"],
                    total_executions=row["total_executions"],
                    failed_executions=row["failed_executions"],
                    failure_rate=_ratio(row["failed_executions"], row["total_executions"]) or 0.0,
                    current_failure_streak=row["current_failure_streak"],
                    last_failure_at=_as_utc(row["last_failure_at"]),
                    last_execution_at=_as_utc(row["last_execution_at"]),
                    p50_duration_ms=row["p50"],
                    p95_duration_ms=row["p95"],
                    rows_total=rows_total,
                    rows_rejected=rows_rejected,
                    reject_ratio=(
                        rows_rejected / rows_total
                        if rows_total is not None and rows_total > 0 and rows_rejected is not None
                        else None
                    ),
                    recurrence_count=recurrence_count,
                    signature_hash=signature_hash,
                )
            )
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

    def _health_execution_base(
        self,
        scope: ProjectScope,
        project_id: str | None,
        source_key: str | None,
        *,
        read_terms: Sequence[RuntimeReadTerm] = (),
    ):
        """Scoped job executions behind the health rollup, as a subquery.

        Written once and reused by all three statements: the metric and incident
        rollups join through it rather than re-stating its filters, so a metric
        can never be attributed to an execution the rollup itself excluded --
        and, with `read_terms`, so a shared read cannot widen one of the three
        and not the others.
        """
        stmt = select(
            RuntimeExecutionRow.id.label("id"),
            RuntimeExecutionRow.project_id.label("project_id"),
            RuntimeExecutionRow.source_key.label("source_key"),
            RuntimeExecutionRow.execution_id.label("execution_id"),
            RuntimeExecutionRow.job_name.label("job_name"),
            RuntimeExecutionRow.state.label("state"),
            RuntimeExecutionRow.started_at.label("started_at"),
            RuntimeExecutionRow.duration_ms.label("duration_ms"),
        ).where(RuntimeExecutionRow.executable_kind == RuntimeExecutableKind.JOB.value)
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        if read_terms:
            stmt = self._apply_read_terms(
                stmt,
                RuntimeExecutionRow.project_id,
                read_terms,
                time_column=RuntimeExecutionRow.started_at,
                execution_id_column=RuntimeExecutionRow.execution_id,
            )
        elif project_id is not None:
            stmt = stmt.where(RuntimeExecutionRow.project_id == project_id)
        if source_key is not None:
            stmt = stmt.where(RuntimeExecutionRow.source_key == source_key)
        return stmt.subquery()

    def _health_execution_rollup(self, executions, group: Sequence):
        """Outcome counts, streak, recency and duration percentiles per job name.

        Ordered by `min(id)` so the caller's stable sort breaks ties in the same
        order the old grouped dict did -- first appearance in the row fetch.
        """
        failed_state = RuntimeExecutionState.FAILED.value
        ranked = select(
            *group,
            executions.c.id.label("id"),
            executions.c.state.label("state"),
            executions.c.started_at.label("started_at"),
            executions.c.duration_ms.label("duration_ms"),
            func.row_number()
            .over(
                partition_by=list(group),
                order_by=[executions.c.started_at.desc(), executions.c.id.desc()],
            )
            .label("rn"),
        ).subquery()
        groups = [ranked.c[column.name] for column in group]
        aggregated = (
            select(
                *groups,
                func.count().label("total_executions"),
                self._state_count(ranked.c.state, RuntimeExecutionState.FAILED).label(
                    "failed_executions"
                ),
                func.coalesce(
                    func.min(case((ranked.c.state != failed_state, ranked.c.rn))) - 1,
                    func.count(),
                ).label("current_failure_streak"),
                func.max(
                    case((ranked.c.state == failed_state, ranked.c.started_at))
                ).label("last_failure_at"),
                func.max(ranked.c.started_at).label("last_execution_at"),
                func.min(ranked.c.id).label("first_seen_id"),
            )
            .group_by(*groups)
            .subquery()
        )
        percentiles = self._duration_percentiles(ranked, groups)
        return (
            select(aggregated, percentiles.c.p50, percentiles.c.p95)
            .join(
                percentiles,
                self._same_group(aggregated, percentiles, group),
                isouter=True,
            )
            .order_by(aggregated.c.first_seen_id.asc())
        )

    async def _health_metric_totals(
        self,
        executions,
        group: Sequence,
        source_key: str | None,
    ) -> dict[tuple[str, str, str], tuple[int | None, int | None]]:
        """Row throughput per job name, summed in SQL.

        `sum()` over an all-NULL group returns NULL, which is exactly the
        "no metric reported a value" answer the Python reduce produced, so the
        None-vs-zero distinction survives the move into the database.
        """
        stmt = (
            select(
                *group,
                func.sum(RuntimeComponentMetricRow.rows_total).label("rows_total"),
                func.sum(RuntimeComponentMetricRow.rows_rejected).label("rows_rejected"),
            )
            .select_from(RuntimeComponentMetricRow)
            .join(
                executions,
                and_(
                    executions.c.project_id == RuntimeComponentMetricRow.project_id,
                    executions.c.execution_id == RuntimeComponentMetricRow.execution_id,
                ),
            )
        )
        # No project predicate here: scope arrives through the join to the
        # already-scoped execution population, and a literal copy beside it is a
        # second statement of one rule, free to drift. The metric-side
        # `source_key` filter is NOT redundant -- it was an explicit predicate
        # that can drop a metric row the execution join would keep.
        if source_key is not None:
            stmt = stmt.where(RuntimeComponentMetricRow.source_key == source_key)
        rows = (await self._session.execute(stmt.group_by(*group))).mappings().all()
        return {
            (row["project_id"], row["source_key"], row["job_name"]): (
                row["rows_total"],
                row["rows_rejected"],
            )
            for row in rows
        }

    async def _health_incident_signatures(
        self, executions, group: Sequence
    ) -> dict[tuple[str, str, str], tuple[int, str | None]]:
        """(recurrence_count, signature_hash) per job name.

        The signature is the newest incident's, and the count is how many of the
        group's incidents share it. Counting stays in Python because
        `signature_hash` is nullable and a NULL-safe equality is spelled
        differently on each backend; only one small row per (job, signature)
        crosses the boundary, so there is nothing to gain from pushing it down.
        """
        stmt = (
            select(
                *group,
                RuntimeIncidentRow.id.label("incident_row_id"),
                RuntimeIncidentRow.signature_hash.label("signature_hash"),
                RuntimeIncidentRow.first_bad_at.label("first_bad_at"),
            )
            .select_from(RuntimeIncidentRow)
            .join(
                executions,
                and_(
                    executions.c.project_id == RuntimeIncidentRow.project_id,
                    executions.c.execution_id == RuntimeIncidentRow.execution_id,
                ),
            )
            # `id` last so the iteration order this reduce depends on -- Python's
            # `max` keeps the FIRST maximal element -- is the row order the
            # unordered fetch happened to give before, made explicit.
            .order_by(RuntimeIncidentRow.id.asc())
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        grouped: dict[tuple[str, str, str], list] = defaultdict(list)
        for row in rows:
            grouped[(row["project_id"], row["source_key"], row["job_name"])].append(row)
        floor = datetime.datetime.min.replace(tzinfo=datetime.UTC)
        result: dict[tuple[str, str, str], tuple[int, str | None]] = {}
        for key, incidents in grouped.items():
            latest = max(incidents, key=lambda row: _as_utc(row["first_bad_at"]) or floor)
            signature_hash = latest["signature_hash"]
            result[key] = (
                sum(1 for row in incidents if row["signature_hash"] == signature_hash),
                signature_hash,
            )
        return result

    async def list_sources(
        self,
        scope: ProjectScope,
        *,
        source_key: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> list[RuntimeSource]:
        """Return scoped runtime source inventory rows.

        ``read_terms`` takes precedence over ``project_id``; empty keeps every
        existing caller unchanged. This is the MIRROR inventory -- "which
        sources produced the rows I can see" -- which is why a granted reader
        belongs in it. `list_source_configs` is the other thing and is
        deliberately not widened.

        `runtime_sources` has no environment column and no execution id, so an
        environment-narrowed grant exposes none of the grantor's source rows
        (`_apply_read_terms`, fail-closed). A source row is not per-environment,
        so there is no honest way to narrow one. No time column either: a source
        either exists in the mirror or it does not, and a window grant bounds
        history rather than existence.

        Filtered on `estate_key`, which IS this table's row stamp -- the same
        value every row this source mirrors carries in its `project_id` column.
        For a source registered against a project that value is the project id,
        so this is the predicate it always was; the resolved read terms name
        estate keys for the same reason.
        """
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSourceRow)
        stmt = self._apply_scope(stmt, RuntimeSourceRow.estate_key, scope)
        if read_terms:
            stmt = self._apply_read_terms(stmt, RuntimeSourceRow.estate_key, read_terms)
        elif project_id is not None:
            stmt = stmt.where(RuntimeSourceRow.estate_key == project_id)
        if source_key is not None:
            stmt = stmt.where(RuntimeSourceRow.source_key == source_key)
        stmt = stmt.order_by(RuntimeSourceRow.last_seen_at.desc().nullslast(), RuntimeSourceRow.id.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_source(row) for row in rows]

    async def list_mirrored_environments(
        self,
        scope: ProjectScope,
        project_id: str,
        *,
        project_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> list[RuntimeMirroredEnvironment]:
        """Environments present in this project's mirror, busiest first.

        Built from the environment inventory AND from observed job runs, then
        merged. Neither alone is sufficient: the inventory can list an
        environment that has never produced a run in the retained window, and a
        run can carry an environment the inventory sweep has not caught up with.
        A picker driven by only one would offer a dead choice or hide a live one.

        `project_ids` widens the mirror to a resolved read set (see
        `resolve_runtime_read_projects`) so a project reading a granted source
        is offered that source's environments. Without it the picker would show
        a reader an empty list and its selection would resolve to no evidence --
        the exact failure this model exists to prevent.

        `read_terms` narrows it back by each grant's own limits. An environment
        a grant excludes is one no read can return a row from, so offering it
        would reintroduce that same failure from the other side -- a selection
        that silently resolves to nothing -- and leaving it in would also tell a
        reader granted PROD which other environments the grantor's estate has.
        The inventory row names its own environment; the run fact names it and
        carries the event time, so a windowed grant bounds what counts as
        observed. Both are the honest reading of "what can this read reach".
        """
        if self._denies_all(scope):
            return []
        read_projects = list(project_ids) if project_ids else [project_id]
        inventory = select(RuntimeEnvironmentRow.environment_id, RuntimeEnvironmentRow.name)
        inventory = self._apply_doc_read_filter(
            inventory,
            RuntimeEnvironmentRow.project_id,
            read_projects,
            read_terms,
            environment_column=RuntimeEnvironmentRow.environment_id,
        )
        inventory = self._apply_scope(inventory, RuntimeEnvironmentRow.project_id, scope)

        observed = (
            select(
                RuntimeJobRunRow.environment_id,
                func.max(RuntimeJobRunRow.environment_name).label("name"),
                func.count().label("run_count"),
                func.max(RuntimeJobRunRow.started_at).label("last_run_at"),
            )
            .where(RuntimeJobRunRow.environment_id.is_not(None))
            .group_by(RuntimeJobRunRow.environment_id)
        )
        observed = self._apply_doc_read_filter(
            observed,
            RuntimeJobRunRow.project_id,
            read_projects,
            read_terms,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
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
        self,
        scope: ProjectScope,
        project_id: str,
        *,
        default_window_days: int,
        read_terms: Sequence[RuntimeReadTerm] = (),
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

        Two scope STORES, one rule -- and the limit that is NOT one of them
        -------------------------------------------------------------------
        An owned source keeps its scope in the credential blob, as above. A
        source this project only READS keeps the scope on the grant row
        (`runtime_source_read_grants`), because the reason two projects share a
        tenant is normally that they care about different environments of it --
        the setting belongs to the (source, project) pair, not to the
        credential. Both are unioned here by the same rule, so a project that
        owns one source and reads another gets one coherent scope.

        The union is what makes a granted environment READABLE at all: without
        the grant's `env-prod` in this list the flat filter would exclude it from
        every sub-query. It is emphatically NOT the grant being applied. A
        grant's environment and window are an access boundary on someone else's
        estate, and this flat scope cannot express them -- unioned they leak the
        grantor's other environments, intersected they suppress the reader's own.
        They are applied per project instead, through `read_terms`
        (`_apply_read_terms`), and reported here as `grant_limits` so a consumer
        can tell which projects were read more narrowly than these flat fields
        say. `read_terms` also narrows the mirror the names below resolve
        against, which is what moves a grant-excluded selection into
        `unavailable_environment_ids` rather than leaving it looking available.
        """
        if self._denies_all(scope):
            return RuntimeDocReadScope(window_days=default_window_days)
        # Only NARROWED terms: the reader's own term restricts nothing, and an
        # unrestricted grant takes nothing out of this read either.
        grant_limits = [term for term in read_terms if not term.unrestricted]
        # Selected by row id, not through `list_sources`: that returns the
        # id-less inventory model, and resolving back to an id by
        # (project, type) would silently collapse two sources of one type.
        source_ids = await self.own_source_ids_for_project(project_id, scope)
        grants = await self.resolve_read_grants_for_project(project_id)
        # Derived from the grants already in hand rather than by calling
        # `resolve_runtime_read_projects`, which would re-run the same queries.
        # Same rule and same order: the reading project first.
        read_projects = [project_id]
        for grant in grants:
            for exposed in grant.exposes:
                if exposed not in read_projects:
                    read_projects.append(exposed)
        shared_from = [grant.source_label for grant in grants]
        if not source_ids and not grants:
            return RuntimeDocReadScope(
                window_days=default_window_days,
                read_project_ids=read_projects,
                grant_limits=grant_limits,
            )

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

        for grant in grants:
            if grant.environment_ids:
                configured = True
                selected.extend(item for item in grant.environment_ids if item not in selected)
            if grant.window_days and grant.window_days > 0:
                configured = True
                window = grant.window_days if window is None else max(window, grant.window_days)

        if not selected:
            return RuntimeDocReadScope(
                window_days=window or default_window_days,
                configured=configured,
                shared_read=bool(grants),
                shared_from=shared_from,
                read_project_ids=read_projects,
                grant_limits=grant_limits,
            )
        mirrored = {item.environment_id: item.name for item in
                    await self.list_mirrored_environments(
                        scope, project_id, project_ids=read_projects, read_terms=read_terms
                    )}
        return RuntimeDocReadScope(
            environment_ids=selected,
            environment_names=[mirrored[item] for item in selected if item in mirrored],
            unavailable_environment_ids=[item for item in selected if item not in mirrored],
            window_days=window or default_window_days,
            configured=configured,
            shared_read=bool(grants),
            shared_from=shared_from,
            read_project_ids=read_projects,
            grant_limits=grant_limits,
        )

    # --- source management (credentials never returned) ---

    async def create_source_config(
        self,
        *,
        project_id: str | None = None,
        source_type: str,
        label: str,
        physical_identity: str | None = None,
        # 30 minutes, matching RuntimeSourceCreate. A short interval pins a CPU
        # core: one cycle fans out a run-history fetch per executable and does
        # not finish before the next is due.
        sync_interval_seconds: int = 1800,
    ) -> RuntimeSourceRead:
        """Register one runtime source, with or without a project.

        `project_id` is now OPTIONAL and means "attach this project", not "own
        this source". Supplied, it does two things: it becomes the source's
        `estate_key` -- so every row this source mirrors is stamped with it,
        exactly as before -- and it gets an attachment row. Omitted, the estate
        key is derived from the source key (`derive_estate_key`) and no project
        is attached yet; projects attach afterwards through `add_read_grant`.

        Keeping the supplied project as the estate key, rather than always
        deriving one, is deliberate and is what makes the common case free: a
        source with exactly one attached project resolves to ONE unrestricted
        read term, which `_apply_read_terms` emits as the same bare
        `project_id = ...` every read used before any of this existed. A derived
        estate key would force a two-branch disjunction onto every read of a
        source that has only ever served one project.

        The only uniqueness rule left is `physical_identity`: one tenant, one
        mirror, one poll. The per-project TMC rule that used to sit beside it is
        gone -- it blocked a project from connecting a SECOND, different tenant,
        which is a different credential and a different estate, and told the
        operator to widen the first source's ingest scope, which reaches none of
        it.
        """
        physical_identity = physical_identity or f"local:{source_type}:{uuid.uuid4().hex}"
        existing = (await self._session.execute(select(RuntimeSourceRow.id).where(
            RuntimeSourceRow.physical_identity == physical_identity,
        ))).scalar_one_or_none()
        if existing is not None:
            raise RuntimeDuplicateSourceError("That physical runtime source is already registered")
        now = datetime.datetime.now(datetime.UTC)
        source_key = f"{source_type}-{uuid.uuid4().hex[:12]}"
        row = RuntimeSourceRow(
            estate_key=project_id or derive_estate_key(source_key),
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
            # `ix_runtime_sources_physical_identity` is the concurrency backstop
            # behind the SELECT above, and is now the only unique rule this
            # insert can trip. A savepoint keeps the caller's transaction usable
            # when two registrations of one tenant race.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
                health = RuntimeSourceHealthRow(source_id=row.id)
                self._session.add(health)
                if project_id is not None:
                    # The attachment is written here, not left implicit, because
                    # the attachment table is now the ONLY record of who may read
                    # this source. A registration that skipped it would produce a
                    # source nobody could see -- the bootstrapping trap, inverted.
                    self._session.add(
                        RuntimeSourceReadGrantRow(
                            source_id=row.id,
                            project_id=project_id,
                            environment_ids=None,
                            window_days=None,
                            created_at=now,
                            created_by=None,
                        )
                    )
                await self._session.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc, "orig", None), "diag", None)
            constraint_name = getattr(constraint, "constraint_name", None)
            message = str(getattr(exc, "orig", exc))
            if constraint_name == "ix_runtime_sources_physical_identity" or (
                "physical_identity" in message
            ):
                raise RuntimeDuplicateSourceError(
                    "That physical runtime source is already registered"
                ) from exc
            raise
        return _to_source_read(row, health, linked_project_ids=[project_id] if project_id else [])

    async def get_source_config(self, source_id: int, scope: ProjectScope) -> RuntimeSourceRead:
        row = await self._get_source_row(source_id, scope)
        health = await self._get_source_health_row(row.id)
        return _to_source_read(row, health, linked_project_ids=await self.list_linked_projects(row.id))

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

    # `find_source_id_by_project_and_type` lived here and is deliberately gone.
    # It answered "which source of this type does this project already have",
    # which was only ever asked to enforce "one TMC per project" -- the rule
    # that stopped a project connecting a SECOND, different tenant and then
    # advised widening the first source's ingest scope, which reaches none of
    # it. The schema constraint went with migration 105 and the route's
    # pre-check went with this change; keeping the lookup alive would leave the
    # question askable, and it has no correct answer now that the count can
    # legitimately exceed one.

    # --- source visibility -------------------------------------------------

    @staticmethod
    def _attached_source_ids(project_ids: Sequence[str]):
        """Sub-select: the ids of every source attached to one of these projects."""
        return select(RuntimeSourceReadGrantRow.source_id).where(
            RuntimeSourceReadGrantRow.project_id.in_(list(project_ids))
        )

    @staticmethod
    def _unattached_source() -> Any:
        """Predicate: this source has no attached project at all."""
        return ~select(RuntimeSourceReadGrantRow.id).where(
            RuntimeSourceReadGrantRow.source_id == RuntimeSourceRow.id
        ).exists()

    @staticmethod
    def _ownerless_estate() -> Any:
        """Predicate: this source's estate key names no project.

        The SQL twin of `is_ownerless_estate_key`. A prefix match rather than a
        flag column so the two can never disagree about one row -- the key is
        derived once, at registration, and read the same way everywhere.
        """
        return RuntimeSourceRow.estate_key.startswith(ESTATE_KEY_PREFIX)

    def _apply_source_visibility(self, stmt, scope: ProjectScope):
        """Restrict a `runtime_sources` query to the CONNECTORS a caller may reach.

        The rule, stated once and applied by every path that reads a source
        config -- the list, the single-source fetch, the update, the delete.
        `scope is None` is an unrestricted operator and adds no predicate. A
        scoped caller reaches a source when EITHER:

        * the source's `estate_key` is a project they hold. Every source that
          existed before the estate key did, and every mirrored source row a
          sync batch writes, carries a project id there, so this clause alone
          reproduces the previous `project_id in scope` rule exactly; or
        * the estate belongs to NO project (`is_ownerless_estate_key` -- it was
          registered before any project) AND the caller holds a project attached
          to it, or nothing is attached to it at all. This is the bootstrapping
          case the whole change exists for: a tenant registered with no project
          has to stay reachable by whoever may manage sources, or it can never
          be attached to one, and once attached its stakeholders are the only
          projects there are.

        What is deliberately NOT in this rule: a read grant over a source whose
        estate IS somebody's project. That grant shares the MIRROR -- and
        `list_sources`, the mirror inventory, is widened by the resolved read
        terms accordingly -- but it does not share the CONNECTOR. Admitting it
        here would hand credential rotation and DELETE over another project's
        tenant to everyone who was granted read of its runs, which is a strictly
        larger permission than the grant was created to give, and one no
        existing behaviour ever offered.

        The permission itself (`runtime_sources:read` / `:write`) is the outer
        gate and is applied by the route, not here.
        """
        if scope is None:
            return stmt
        if self._denies_all(scope):
            # An empty scope means the caller may see nothing, and that has to
            # hold HERE rather than only in each caller's early return: the
            # unattached-and-ownerless branch below is satisfied by a row alone
            # and would otherwise let a deny-all scope through it.
            return stmt.where(false())
        return stmt.where(
            or_(
                RuntimeSourceRow.estate_key.in_(list(scope)),
                and_(
                    self._ownerless_estate(),
                    or_(
                        RuntimeSourceRow.id.in_(self._attached_source_ids(scope)),
                        self._unattached_source(),
                    ),
                ),
            )
        )

    async def list_linked_projects(self, source_id: int) -> list[str]:
        """Every project attached to this source, in a stable order."""
        return (await self._linked_projects_by_source([source_id])).get(source_id, [])

    async def _linked_projects_by_source(
        self, source_ids: Sequence[int]
    ) -> dict[int, list[str]]:
        """Attached projects for several sources at once, sorted per source.

        Batched because the attachment set is part of every source's published
        identity now, and resolving it per row turns a two-statement list into
        an N+1. Every source id asked for gets an entry, so an empty list always
        means "attached to nobody" and never "not looked up" -- the same
        distinction `environment_ids` draws between `[]` and None.
        """
        wanted = list(source_ids)
        linked: dict[int, list[str]] = {source_id: [] for source_id in wanted}
        if not wanted:
            return linked
        for source_id, project_id in (
            await self._session.execute(
                select(
                    RuntimeSourceReadGrantRow.source_id, RuntimeSourceReadGrantRow.project_id
                )
                .where(RuntimeSourceReadGrantRow.source_id.in_(wanted))
                .order_by(RuntimeSourceReadGrantRow.project_id.asc())
            )
        ).all():
            linked[source_id].append(project_id)
        return linked

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
        """The source-management inventory, scoped by `_apply_source_visibility`.

        Distinct from `list_sources`, which is the MIRROR inventory ("which
        sources produced the rows I can see") and filters on the row stamp.
        This one answers "which connectors may I administer", so it is resolved
        from the attachment table and the estate key -- see
        `_apply_source_visibility` for the rule and why an unattached, ownerless
        source is in it.

        `project_id` narrows to the sources ATTACHED to that project. It no
        longer means "owned by", because nothing is owned; for every source
        that predates this change the two sets are identical, since the
        migration attached each source to the project it was registered
        against.
        """
        if self._denies_all(scope):
            return []
        stmt = select(RuntimeSourceRow)
        stmt = self._apply_source_visibility(stmt, scope)
        if project_id is not None:
            stmt = stmt.where(RuntimeSourceRow.id.in_(self._attached_source_ids([project_id])))
        stmt = stmt.order_by(RuntimeSourceRow.ingested_at.desc(), RuntimeSourceRow.id.desc()).limit(limit)
        rows = list((await self._session.execute(stmt)).scalars().all())
        source_ids = [row.id for row in rows] or [-1]
        health_rows = {
            row.source_id: row
            for row in (
                await self._session.execute(
                    select(RuntimeSourceHealthRow).where(
                        RuntimeSourceHealthRow.source_id.in_(source_ids)
                    )
                )
            ).scalars().all()
        }
        linked = await self._linked_projects_by_source([row.id for row in rows])
        return [
            _to_source_read(row, health_rows.get(row.id), linked_project_ids=linked[row.id])
            for row in rows
        ]

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
        linked = await self._linked_projects_by_source([row.id for row in rows])
        return [
            _to_source_read(row, health_rows.get(row.id), linked_project_ids=linked[row.id])
            for row in rows
        ]

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
        return _to_source_read(
            row,
            await self._get_source_health_row(row.id),
            linked_project_ids=await self.list_linked_projects(row.id),
        )

    async def delete_source_config(self, source_id: int, scope: ProjectScope) -> None:
        row = await self._get_source_row(source_id, scope)
        await self._session.execute(delete(RuntimeSourceCredentialsRow).where(RuntimeSourceCredentialsRow.source_id == row.id))
        await self._session.execute(delete(RuntimeSourceHealthRow).where(RuntimeSourceHealthRow.source_id == row.id))
        await self._session.execute(delete(RuntimeWorkspaceProjectMapRow).where(RuntimeWorkspaceProjectMapRow.source_id == row.id))
        # Grants are deleted explicitly rather than left to the FK cascade:
        # SQLite does not enforce it without `PRAGMA foreign_keys`, and a
        # surviving grant would keep widening a reader's scope to a source that
        # no longer exists.
        await self._session.execute(delete(RuntimeSourceReadGrantRow).where(RuntimeSourceReadGrantRow.source_id == row.id))
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

    # --- project attachments (see RuntimeSourceReadGrantRow) ---

    async def _projects_exposed_by_source(self, source_id: int, estate_key: str) -> list[str]:
        """Every row stamp a read of this source can reach.

        The source's ESTATE KEY, plus every project its workspace map re-points
        containers to -- because the filter is on the row stamp, and a mapped
        container's rows carry the mapped project rather than the estate.

        The first element is an estate key, which for every source that predates
        this change IS a project id and for an ownerless source is not. Both are
        row stamps, which is the only thing a downstream predicate needs; the
        field on the API model is still called `exposes_project_ids` because
        renaming it is the next wave's job.
        """
        mapped = (
            await self._session.execute(
                select(RuntimeWorkspaceProjectMapRow.project_id)
                .where(RuntimeWorkspaceProjectMapRow.source_id == source_id)
                .distinct()
            )
        ).scalars().all()
        exposed = [estate_key]
        for project_id in mapped:
            if project_id and project_id not in exposed:
                exposed.append(project_id)
        return exposed

    @staticmethod
    def _clean_environment_ids(raw: object) -> list[str] | None:
        """None means every mirrored environment; [] is a deliberate 'nothing'."""
        if not isinstance(raw, list):
            return None
        return [str(item).strip() for item in raw if str(item).strip()]

    async def add_read_grant(
        self,
        *,
        source_id: int,
        project_id: str,
        environment_ids: Sequence[str] | None,
        window_days: int | None,
        created_by: str | None,
    ) -> RuntimeSourceReadGrant:
        """Attach one project to this source, or re-scope an existing attachment.

        The refusal that remains is NOT "somebody already owns this" -- nothing
        does. It is that a project which equals the source's `estate_key` is
        already stamped on every row this source mirrors, so it reads them
        through its own unrestricted term in `resolve_runtime_read_scope` and
        no `environment_ids` or `window_days` written here could take a single
        row away. Accepting the call would store a limit that silently does
        nothing, and offer a revoke button that revokes nothing. Every source
        registered against a project has exactly one such row, written at
        registration (or by the migration), and it stays as the record of that
        attachment.
        """
        source = await self._get_source_row(source_id, None)
        if project_id == source.estate_key:
            raise RuntimeDuplicateSourceError(
                "That project is the estate this runtime source mirrors into; "
                "it already reads every row of it"
            )
        now = datetime.datetime.now(datetime.UTC)
        stmt = select(RuntimeSourceReadGrantRow).where(
            RuntimeSourceReadGrantRow.source_id == source_id,
            RuntimeSourceReadGrantRow.project_id == project_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSourceReadGrantRow(
                source_id=source_id,
                project_id=project_id,
                created_at=now,
                created_by=created_by,
            )
            self._session.add(row)
        row.environment_ids = (
            None if environment_ids is None else [str(item) for item in environment_ids]
        )
        row.window_days = window_days
        await self._session.flush()
        return await self._to_read_grant(row, source)

    async def list_read_grants(self, source_id: int) -> list[RuntimeSourceReadGrant]:
        """The projects reading this source's mirror that do not stamp it.

        Excludes the attachment whose project EQUALS the source's estate key --
        the row written at registration, and the one the migration created for
        each source's former owner. It is an attachment, not a share: those rows
        already carry that project's stamp, so listing it here would present the
        estate's own project as a revocable reader of itself, and `add_read_grant`
        refuses to create or re-scope it for exactly that reason.

        `list_linked_projects` is the complete attachment list, and
        `RuntimeSourceRead.linked_project_ids` publishes it.
        """
        source = await self._get_source_row(source_id, None)
        rows = (
            await self._session.execute(
                select(RuntimeSourceReadGrantRow)
                .where(
                    RuntimeSourceReadGrantRow.source_id == source_id,
                    RuntimeSourceReadGrantRow.project_id != source.estate_key,
                )
                .order_by(RuntimeSourceReadGrantRow.project_id.asc())
            )
        ).scalars().all()
        return [await self._to_read_grant(row, source) for row in rows]

    async def delete_read_grant(self, source_id: int, project_id: str) -> None:
        """Detach a project from this source.

        A no-op for the attachment whose project equals the estate key, the same
        row `add_read_grant` refuses to write. Removing it would take the source
        out of that project's filtered source list while changing nothing about
        what the project can actually read -- every row is still stamped with it
        -- which is precisely the kind of half-revocation this model exists to
        make impossible.
        """
        estate_key = (await self._session.execute(
            select(RuntimeSourceRow.estate_key).where(RuntimeSourceRow.id == source_id)
        )).scalars().first()
        if estate_key is not None and estate_key == project_id:
            return
        await self._session.execute(
            delete(RuntimeSourceReadGrantRow).where(
                RuntimeSourceReadGrantRow.source_id == source_id,
                RuntimeSourceReadGrantRow.project_id == project_id,
            )
        )
        await self._session.flush()

    async def _to_read_grant(
        self, row: RuntimeSourceReadGrantRow, source: RuntimeSourceRow
    ) -> RuntimeSourceReadGrant:
        return RuntimeSourceReadGrant(
            id=row.id,
            source_id=row.source_id,
            project_id=row.project_id,
            source_estate_key=source.estate_key,
            source_label=source.label,
            environment_ids=self._clean_environment_ids(row.environment_ids),
            window_days=row.window_days,
            exposes_project_ids=await self._projects_exposed_by_source(
                row.source_id, source.estate_key
            ),
            created_at=_as_utc(row.created_at) or row.created_at,
            created_by=row.created_by,
        )

    async def resolve_read_grants_for_project(
        self, project_id: str
    ) -> list[_ResolvedReadGrant]:
        """The attachments this project reads a FOREIGN estate through.

        An attachment whose source stamps rows with this very project
        (`estate_key == project_id` -- every source registered against it has
        exactly one) is skipped, and that filter is what keeps the common case
        identical to the behaviour before estate keys existed. Such a row widens
        nothing: the reader's own unrestricted term already covers every row
        carrying that stamp, so including it would add a duplicate term, report
        the project as reading a "shared" source, and drag its own workspace-map
        targets into its read set -- three changes to a project that is simply
        reading its own mirror.

        Ordered by source id so a resolved read set is deterministic across
        calls -- two documentation runs of one project must not differ because
        the database returned rows in another order.
        """
        stmt = (
            select(RuntimeSourceReadGrantRow, RuntimeSourceRow)
            .join(RuntimeSourceRow, RuntimeSourceRow.id == RuntimeSourceReadGrantRow.source_id)
            .where(
                RuntimeSourceReadGrantRow.project_id == project_id,
                RuntimeSourceRow.estate_key != project_id,
            )
            .order_by(RuntimeSourceReadGrantRow.source_id.asc())
        )
        resolved: list[_ResolvedReadGrant] = []
        for grant, source in (await self._session.execute(stmt)).all():
            resolved.append(
                _ResolvedReadGrant(
                    source_id=source.id,
                    source_key=source.source_key,
                    source_label=source.label,
                    source_estate_key=source.estate_key,
                    environment_ids=self._clean_environment_ids(grant.environment_ids),
                    window_days=grant.window_days,
                    exposes=await self._projects_exposed_by_source(source.id, source.estate_key),
                )
            )
        return resolved

    async def resolve_runtime_read_projects(self, project_id: str) -> list[str]:
        """The row stamps one project's documentation read may draw from.

        Always starts with `project_id` itself, so a project reading only its
        own mirror resolves to a single-element list and every downstream
        predicate stays the `==` it was before any of this existed.

        Widening comes ONLY from stored attachment rows. No caller may pass a
        set in; `GET /runtime/doc-evidence` authenticates with a machine key and
        has no user scope to fall back on, so a request-supplied read set would
        be an authorization bypass with no second gate behind it.
        """
        projects = [project_id]
        for grant in await self.resolve_read_grants_for_project(project_id):
            for exposed in grant.exposes:
                if exposed not in projects:
                    projects.append(exposed)
        return projects

    async def own_source_ids_for_project(self, project_id: str, scope: ProjectScope) -> list[int]:
        """Sources whose non-secret config configures THIS project's documentation.

        Two shapes qualify, and between them they are what "the project's own
        sources" used to mean when a source had an owner:

        * `estate_key == project_id` -- registered against this project, so its
          rows carry this project's stamp. Identical to the old owner test;
        * an OWNERLESS estate this project is attached to. Nobody owns it, so
          its configured documentation scope can only be read by the projects
          attached to it.

        A source whose estate is ANOTHER project is deliberately absent: its
        credential blob holds the scope its own stakeholders configured, and
        letting that narrow a reader here would apply somebody else's setting as
        if it were this project's. What a reader gets from such a source arrives
        through the attachment row instead (`resolve_doc_read_scope`).
        """
        stmt = select(RuntimeSourceRow.id).where(
            or_(
                RuntimeSourceRow.estate_key == project_id,
                and_(
                    self._ownerless_estate(),
                    RuntimeSourceRow.id.in_(self._attached_source_ids([project_id])),
                ),
            )
        )
        stmt = self._apply_source_visibility(stmt, scope)
        return list((await self._session.execute(stmt.order_by(RuntimeSourceRow.id.asc()))).scalars().all())

    async def resolve_project_id(self, source_id: int, container_key: str | None) -> tuple[str, bool]:
        """The stamp one mirrored record is written with, and whether it is the default.

        The workspace map wins when it names the container: that is how a source
        splits its rows between real projects, and it is unchanged. The fallback
        is the source's `estate_key` -- which for a source registered against a
        project is that project's id, exactly as before, and for an ownerless
        source is the derived estate identifier that its attached projects reach
        through their resolved read terms.
        """
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
        return source.estate_key, True

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
        """One source row, or 404, under `_apply_source_visibility`."""
        if self._denies_all(scope):
            raise RuntimeRecordNotFoundError(f"Runtime source not found: {source_id}")
        stmt = select(RuntimeSourceRow).where(RuntimeSourceRow.id == source_id)
        stmt = self._apply_source_visibility(stmt, scope)
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
        return _to_source_read(
            row, health, linked_project_ids=await self.list_linked_projects(row.id)
        )

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
        return _to_source_read(
            row, health, linked_project_ids=await self.list_linked_projects(row.id)
        )

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
        stmt = self._apply_project_filter(
            stmt,
            RuntimeEngineRow.project_id,
            params,
            environment_column=RuntimeEngineRow.environment_id,
        )
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
        stmt = self._apply_project_filter(
            stmt,
            RuntimeEngineRow.project_id,
            params,
            environment_column=RuntimeEngineRow.environment_id,
        )
        if params.source_key is not None:
            stmt = stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            stmt = stmt.where(RuntimeEngineRow.status == params.status.value)
        return int((await self._session.execute(stmt)).scalar_one())

    def _engine_label_select(self, scope: ProjectScope, params: ListRuntimeEnginesInput):
        """The engine inventory rows this surface may label and group by.

        Only the columns the health model reads. The filter chain lives here so
        the label lookup and the run-side group filter can never disagree about
        which engines are in the inventory.
        """
        stmt = select(
            RuntimeEngineRow.project_id.label("project_id"),
            RuntimeEngineRow.source_key.label("source_key"),
            RuntimeEngineRow.engine_id.label("engine_id"),
            RuntimeEngineRow.name.label("name"),
            RuntimeEngineRow.status.label("status"),
            RuntimeEngineRow.environment_name.label("environment_name"),
            RuntimeEngineRow.workspace_name.label("workspace_name"),
        )
        stmt = self._apply_scope(stmt, RuntimeEngineRow.project_id, scope)
        stmt = self._apply_project_filter(
            stmt,
            RuntimeEngineRow.project_id,
            params,
            environment_column=RuntimeEngineRow.environment_id,
        )
        if params.source_key is not None:
            stmt = stmt.where(RuntimeEngineRow.source_key == params.source_key)
        if params.status is not None:
            stmt = stmt.where(RuntimeEngineRow.status == params.status.value)
        return stmt

    def _engine_run_base(
        self,
        scope: ProjectScope,
        params: ListRuntimeEnginesInput,
        *,
        inventory_is_populated: bool,
    ):
        """Scoped job-run rows grouped by engine, as a subquery.

        `inventory_is_populated` reproduces a deliberate quirk of the reduce this
        replaces: a run whose engine is absent from the filtered inventory was
        dropped, but ONLY while that inventory had rows. An empty inventory means
        "no engine filter to apply", not "no engines", so an unconditional join
        would report zero engine health for any tenant whose engine inventory has
        not been mirrored yet.
        """
        stmt = select(
            RuntimeJobRunRow.id.label("id"),
            RuntimeJobRunRow.project_id.label("project_id"),
            RuntimeJobRunRow.source_key.label("source_key"),
            RuntimeJobRunRow.engine_id.label("engine_id"),
            RuntimeJobRunRow.engine_name.label("engine_name"),
            RuntimeJobRunRow.environment_name.label("environment_name"),
            RuntimeJobRunRow.workspace_name.label("workspace_name"),
            RuntimeJobRunRow.state.label("state"),
            RuntimeJobRunRow.started_at.label("started_at"),
            RuntimeJobRunRow.duration_ms.label("duration_ms"),
        ).where(RuntimeJobRunRow.engine_id.is_not(None))
        stmt = self._apply_scope(stmt, RuntimeJobRunRow.project_id, scope)
        stmt = self._apply_project_filter(
            stmt,
            RuntimeJobRunRow.project_id,
            params,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
        )
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRunRow.source_key == params.source_key)
        if inventory_is_populated:
            inventory = self._engine_label_select(scope, params).subquery()
            stmt = stmt.join(
                inventory,
                and_(
                    inventory.c.project_id == RuntimeJobRunRow.project_id,
                    inventory.c.source_key == RuntimeJobRunRow.source_key,
                    inventory.c.engine_id == RuntimeJobRunRow.engine_id,
                ),
            )
        return stmt.subquery()

    async def list_engine_health(
        self,
        scope: ProjectScope,
        params: ListRuntimeEnginesInput,
        *,
        attention_only: bool = False,
    ) -> list[RuntimeEngineHealth]:
        """Return DB-derived health grouped by runtime engine.

        `attention_only` narrows to engines with at least one run a human should
        look at (see `_attention_run_count`). It lives here rather than in the
        caller because only the repository can see run states -- the returned
        health model reports FAILED counts alone, so a caller filtering on
        `failed_runs` silently drops every misfire and timeout.
        """
        if self._denies_all(scope):
            return []
        engines_by_key = {
            (row["project_id"], row["source_key"], row["engine_id"]): row
            for row in (
                await self._session.execute(self._engine_label_select(scope, params))
            ).mappings()
        }
        base = self._engine_run_base(
            scope, params, inventory_is_populated=bool(engines_by_key)
        )
        group = [base.c.project_id, base.c.source_key, base.c.engine_id]
        stmt = self._run_group_health_statement(
            base,
            group,
            latest_columns=("engine_name", "environment_name", "workspace_name"),
            with_percentiles=True,
            attention_only=attention_only,
            limit=params.limit,
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        summaries: list[RuntimeEngineHealth] = []
        for row in rows:
            engine = engines_by_key.get(
                (row["project_id"], row["source_key"], row["engine_id"])
            )
            summaries.append(
                RuntimeEngineHealth(
                    project_id=row["project_id"],
                    source_key=row["source_key"],
                    engine_id=row["engine_id"],
                    engine_name=(
                        engine["name"]
                        if engine is not None
                        else (row["latest_engine_name"] or row["engine_id"])
                    ),
                    engine_status=(
                        RuntimeEngineStatus(engine["status"]) if engine is not None else None
                    ),
                    environment_name=(engine["environment_name"] if engine is not None else None)
                    or row["latest_environment_name"],
                    workspace_name=(engine["workspace_name"] if engine is not None else None)
                    or row["latest_workspace_name"],
                    total_runs=row["total_runs"],
                    failed_runs=row["failed_runs"],
                    failure_rate=_ratio(row["failed_runs"], row["total_runs"]) or 0.0,
                    current_failure_streak=row["current_failure_streak"],
                    latest_run_at=_as_utc(row["latest_run_at"]),
                    latest_state=RuntimeExecutionState(row["latest_state"]),
                    p50_duration_ms=row["p50"],
                    p95_duration_ms=row["p95"],
                )
            )
        return summaries

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
        stmt = self._apply_project_filter(
            stmt,
            RuntimeJobRow.project_id,
            params,
            environment_column=RuntimeJobRow.environment_id,
        )
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
        if params.executable_kind is not None:
            stmt = stmt.where(RuntimeJobRow.executable_kind == params.executable_kind.value)
        stmt = stmt.order_by(RuntimeJobRow.name.asc(), RuntimeJobRow.id.asc()).offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_job(row) for row in rows]

    async def count_jobs(self, scope: ProjectScope, params: ListRuntimeJobsInput) -> int:
        if self._denies_all(scope):
            return 0
        stmt = select(func.count()).select_from(RuntimeJobRow)
        stmt = self._apply_scope(stmt, RuntimeJobRow.project_id, scope)
        stmt = self._apply_project_filter(
            stmt,
            RuntimeJobRow.project_id,
            params,
            environment_column=RuntimeJobRow.environment_id,
        )
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
        if params.executable_kind is not None:
            stmt = stmt.where(RuntimeJobRow.executable_kind == params.executable_kind.value)
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_deployments(
        self,
        scope: ProjectScope,
        params: ListRuntimeDeploymentsInput,
    ) -> list[RuntimeDeployment]:
        """Return Route/Data Service inventory with its latest lifecycle row."""
        deployments = await self._deployment_inventory(scope, params)
        return deployments[params.offset : params.offset + params.limit]

    async def _deployment_inventory(
        self,
        scope: ProjectScope,
        params: ListRuntimeDeploymentsInput,
    ) -> list[RuntimeDeployment]:
        if self._denies_all(scope):
            return []
        kinds = (
            [params.executable_kind.value]
            if params.executable_kind is not None
            else [RuntimeExecutableKind.ROUTE.value, RuntimeExecutableKind.DATA_SERVICE.value]
        )
        job_stmt = select(RuntimeJobRow)
        job_stmt = self._apply_scope(job_stmt, RuntimeJobRow.project_id, scope)
        job_stmt = self._apply_project_filter(
            job_stmt,
            RuntimeJobRow.project_id,
            params,
            environment_column=RuntimeJobRow.environment_id,
        )
        if params.source_key is not None:
            job_stmt = job_stmt.where(RuntimeJobRow.source_key == params.source_key)
        job_stmt = job_stmt.where(RuntimeJobRow.executable_kind.in_(kinds))
        jobs = list((await self._session.execute(job_stmt)).scalars().all())
        if not jobs:
            return []

        run_stmt = select(RuntimeJobRunRow)
        run_stmt = self._apply_scope(run_stmt, RuntimeJobRunRow.project_id, scope)
        run_stmt = self._apply_project_filter(
            run_stmt,
            RuntimeJobRunRow.project_id,
            params,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
        )
        if params.source_key is not None:
            run_stmt = run_stmt.where(RuntimeJobRunRow.source_key == params.source_key)
        run_stmt = run_stmt.where(RuntimeJobRunRow.executable_kind.in_(kinds))
        run_stmt = run_stmt.order_by(RuntimeJobRunRow.started_at.desc(), RuntimeJobRunRow.id.desc())
        rows = list((await self._session.execute(run_stmt)).scalars().all())
        latest: dict[tuple[str, str, str], RuntimeJobRunRow] = {}
        for row in rows:
            latest.setdefault((row.project_id, row.source_key, row.job_id), row)

        deployments = [
            _to_deployment(job, latest.get((job.project_id, job.source_key, job.job_id)))
            for job in jobs
        ]
        if params.state is not None:
            deployments = [item for item in deployments if item.state == params.state]
        deployments.sort(key=lambda item: item.executable_name.casefold())
        deployments.sort(
            key=lambda item: item.started_at
            or datetime.datetime.min.replace(tzinfo=datetime.UTC),
            reverse=True,
        )
        return deployments

    async def count_deployments(
        self,
        scope: ProjectScope,
        params: ListRuntimeDeploymentsInput,
    ) -> int:
        return len(await self._deployment_inventory(scope, params))

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
        # No environment column and no execution id: an environment-narrowed
        # grant exposes nothing here. See `_apply_read_terms` for why that is
        # preferred over returning the grantor's whole artifact inventory.
        stmt = self._apply_project_filter(stmt, RuntimeArtifactRow.project_id, params)
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
        # Fail-closed under an environment-narrowed grant; see `list_artifacts`.
        stmt = self._apply_project_filter(stmt, RuntimeConnectionRow.project_id, params)
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
        # Fail-closed under an environment-narrowed grant; see `list_artifacts`.
        stmt = self._apply_project_filter(stmt, RuntimeJobDependencyRow.project_id, params)
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
        """Return availability and hygiene facts for optional extended metadata.

        The three sub-inputs carry `read_terms` forward. A summary built from a
        narrower read set than the lists it summarises would report "artifacts
        not supported" for a tenant whose artifacts the caller can page through.
        """
        artifacts = await self.list_artifacts(
            scope,
            ListRuntimeArtifactsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
                read_terms=params.read_terms,
            ),
        )
        connections = await self.list_connections(
            scope,
            ListRuntimeConnectionsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
                read_terms=params.read_terms,
            ),
        )
        dependencies = await self.list_job_dependencies(
            scope,
            ListRuntimeDependenciesInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
                read_terms=params.read_terms,
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
        stmt = self._apply_project_filter(
            stmt,
            RuntimeJobRunRow.project_id,
            params,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
        )
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

    async def _executable_semantics_by_source(
        self, source_keys: set[str]
    ) -> dict[str, Mapping[str, str]]:
        """Map each mirrored source key to its provider's kind semantics.

        The `source_key` stamped on a MIRRORED row is the provider's own key,
        not the `runtime_sources.source_key` of the configured source. Verified
        on a live mirror: `runtime_jobs.source_key` holds `tmc` (1118 rows),
        `mock` and `demo-topology`, while the two configured rows are
        `tmc-ddf1ed37af12` and `tmc-a335fb698715`. The two value spaces do not
        intersect at all, so resolving only through `runtime_sources` answered
        "no provider" for every mirrored row and every single one reported
        `liveness="unknown"` -- which silently restores the very behaviour this
        field exists to correct, because a consumer then treats a Route
        deployment as an execution again.

        So the provider key is tried FIRST, and the `runtime_sources` join is
        kept as a fallback for any writer that does stamp a configured source
        key. A value that is neither stays unresolved and its kinds stay
        `"unknown"`.
        """
        if not source_keys:
            return {}
        # One lookup per distinct provider, not per source.
        by_type: dict[str, Mapping[str, str]] = {}

        def semantics_for(source_type: str | None) -> Mapping[str, str]:
            if not source_type:
                return {}
            if source_type not in by_type:
                by_type[source_type] = _executable_semantics_for(source_type)
            return by_type[source_type]

        resolved: dict[str, Mapping[str, str]] = {}
        for source_key in source_keys:
            declared = semantics_for(source_key)
            if declared:
                resolved[source_key] = declared
        unresolved = sorted(source_keys - set(resolved))
        if not unresolved:
            return resolved
        stmt = (
            select(RuntimeSourceRow.source_key, RuntimeSourceRow.source_type)
            .where(RuntimeSourceRow.source_key.in_(unresolved))
            .distinct()
        )
        for source_key, source_type in (await self._session.execute(stmt)).all():
            resolved[source_key] = semantics_for(source_type)
        return resolved

    def _job_label_select(self, scope: ProjectScope, params: ListRuntimeJobsInput):
        """Job inventory rows supplying labels to the job-health surface.

        Labels only -- unlike engines, a run is never dropped for having no
        inventory row, so this is a lookup and not a filter.

        `artifact_name` sits one column away from `name` on the same row and was
        simply not selected, which left every consumer guessing the design a
        runtime row was deployed from instead of joining on it.
        """
        stmt = select(
            RuntimeJobRow.project_id.label("project_id"),
            RuntimeJobRow.source_key.label("source_key"),
            RuntimeJobRow.job_id.label("job_id"),
            RuntimeJobRow.name.label("name"),
            RuntimeJobRow.artifact_name.label("artifact_name"),
            RuntimeJobRow.executable_kind.label("executable_kind"),
            RuntimeJobRow.environment_name.label("environment_name"),
            RuntimeJobRow.workspace_name.label("workspace_name"),
            RuntimeJobRow.engine_name.label("engine_name"),
        )
        stmt = self._apply_scope(stmt, RuntimeJobRow.project_id, scope)
        stmt = self._apply_project_filter(
            stmt,
            RuntimeJobRow.project_id,
            params,
            environment_column=RuntimeJobRow.environment_id,
        )
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimeJobRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeJobRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRow.engine_id == params.engine_id)
        # `None` means EVERY kind, exactly as it does on `list_jobs` /
        # `count_jobs`. Coercing it to JOB here is what structurally excluded
        # every route and data service from job health for every provider.
        if params.executable_kind is not None:
            stmt = stmt.where(RuntimeJobRow.executable_kind == params.executable_kind.value)
        return stmt

    def _job_run_base(self, scope: ProjectScope, params: ListRuntimeJobsInput):
        """Scoped job-run rows grouped by job, as a subquery."""
        stmt = select(
            RuntimeJobRunRow.id.label("id"),
            RuntimeJobRunRow.project_id.label("project_id"),
            RuntimeJobRunRow.source_key.label("source_key"),
            RuntimeJobRunRow.job_id.label("job_id"),
            RuntimeJobRunRow.job_name.label("job_name"),
            RuntimeJobRunRow.environment_name.label("environment_name"),
            RuntimeJobRunRow.workspace_name.label("workspace_name"),
            RuntimeJobRunRow.engine_name.label("engine_name"),
            RuntimeJobRunRow.executable_kind.label("executable_kind"),
            RuntimeJobRunRow.state.label("state"),
            RuntimeJobRunRow.started_at.label("started_at"),
        )
        stmt = self._apply_scope(stmt, RuntimeJobRunRow.project_id, scope)
        stmt = self._apply_project_filter(
            stmt,
            RuntimeJobRunRow.project_id,
            params,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
        )
        if params.source_key is not None:
            stmt = stmt.where(RuntimeJobRunRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.workspace_id == params.workspace_id)
        if params.engine_id is not None:
            stmt = stmt.where(RuntimeJobRunRow.engine_id == params.engine_id)
        if params.executable_kind is not None:
            stmt = stmt.where(
                RuntimeJobRunRow.executable_kind == params.executable_kind.value
            )
        return stmt.subquery()

    async def list_job_health(
        self,
        scope: ProjectScope,
        params: ListRuntimeJobsInput,
        *,
        attention_only: bool = False,
    ) -> list[RuntimeJobHealth]:
        """Return DB-derived health grouped by runtime job.

        See `list_engine_health` for why `attention_only` belongs here.
        """
        if self._denies_all(scope):
            return []
        jobs_by_key = {
            (row["project_id"], row["source_key"], row["job_id"]): row
            for row in (
                await self._session.execute(self._job_label_select(scope, params))
            ).mappings()
        }
        base = self._job_run_base(scope, params)
        group = [base.c.project_id, base.c.source_key, base.c.job_id]
        stmt = self._run_group_health_statement(
            base,
            group,
            latest_columns=(
                "job_name",
                "environment_name",
                "workspace_name",
                "engine_name",
                "executable_kind",
            ),
            attention_only=attention_only,
            limit=params.limit,
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        semantics = await self._executable_semantics_by_source(
            {row["source_key"] for row in rows}
        )
        summaries: list[RuntimeJobHealth] = []
        for row in rows:
            job = jobs_by_key.get((row["project_id"], row["source_key"], row["job_id"]))
            # The inventory row is the current state of the executable; the run
            # row only records what it was when it last ran.
            executable_kind = (
                job["executable_kind"] if job is not None else None
            ) or row["latest_executable_kind"]
            summaries.append(
                RuntimeJobHealth(
                    project_id=row["project_id"],
                    source_key=row["source_key"],
                    job_id=row["job_id"],
                    job_name=job["name"] if job is not None else row["latest_job_name"],
                    artifact_name=job["artifact_name"] if job is not None else None,
                    executable_kind=executable_kind,
                    liveness=_liveness_of(
                        semantics.get(row["source_key"], {}), executable_kind
                    ),
                    environment_name=(job["environment_name"] if job is not None else None)
                    or row["latest_environment_name"],
                    workspace_name=(job["workspace_name"] if job is not None else None)
                    or row["latest_workspace_name"],
                    engine_name=(job["engine_name"] if job is not None else None)
                    or row["latest_engine_name"],
                    total_runs=row["total_runs"],
                    failed_runs=row["failed_runs"],
                    failure_rate=_ratio(row["failed_runs"], row["total_runs"]) or 0.0,
                    current_failure_streak=row["current_failure_streak"],
                    latest_run_at=_as_utc(row["latest_run_at"]),
                    latest_state=RuntimeExecutionState(row["latest_state"]),
                )
            )
        return summaries

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
        stmt = self._apply_project_filter(
            stmt,
            RuntimePlanRow.project_id,
            params,
            environment_column=RuntimePlanRow.environment_id,
        )
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
        stmt = self._apply_project_filter(
            stmt,
            RuntimePlanRow.project_id,
            params,
            environment_column=RuntimePlanRow.environment_id,
        )
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
        stmt = self._apply_project_filter(
            stmt,
            RuntimePlanRunRow.project_id,
            params,
            environment_column=RuntimePlanRunRow.environment_id,
            time_column=RuntimePlanRunRow.started_at,
        )
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

    def _plan_label_select(self, scope: ProjectScope, params: ListRuntimePlansInput):
        """Plan inventory rows supplying labels to the plan-health surface.

        `steps` is deliberately absent: it is a JSON column and no label needs it.
        """
        stmt = select(
            RuntimePlanRow.project_id.label("project_id"),
            RuntimePlanRow.source_key.label("source_key"),
            RuntimePlanRow.plan_id.label("plan_id"),
            RuntimePlanRow.name.label("name"),
            RuntimePlanRow.environment_name.label("environment_name"),
            RuntimePlanRow.workspace_name.label("workspace_name"),
        )
        stmt = self._apply_scope(stmt, RuntimePlanRow.project_id, scope)
        stmt = self._apply_project_filter(
            stmt,
            RuntimePlanRow.project_id,
            params,
            environment_column=RuntimePlanRow.environment_id,
        )
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimePlanRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimePlanRow.workspace_id == params.workspace_id)
        return stmt

    def _plan_run_base(self, scope: ProjectScope, params: ListRuntimePlansInput):
        """Scoped plan-run rows grouped by plan, as a subquery.

        `step_results` is NEVER selected here. It is a JSON column whose TOAST is
        two orders of magnitude larger than the table heap (157 MB against
        2.5 MB on one mirrored tenant, averaging 23 kB a row), and no number on
        this surface is derived from it. Selecting the whole ORM row made a
        request that answers with a handful of integers de-TOAST, decompress,
        ship and deserialize every byte of it.
        """
        stmt = select(
            RuntimePlanRunRow.id.label("id"),
            RuntimePlanRunRow.project_id.label("project_id"),
            RuntimePlanRunRow.source_key.label("source_key"),
            RuntimePlanRunRow.plan_id.label("plan_id"),
            RuntimePlanRunRow.plan_name.label("plan_name"),
            RuntimePlanRunRow.environment_name.label("environment_name"),
            RuntimePlanRunRow.workspace_name.label("workspace_name"),
            RuntimePlanRunRow.state.label("state"),
            RuntimePlanRunRow.started_at.label("started_at"),
            RuntimePlanRunRow.ended_at.label("ended_at"),
        )
        stmt = self._apply_scope(stmt, RuntimePlanRunRow.project_id, scope)
        stmt = self._apply_project_filter(
            stmt,
            RuntimePlanRunRow.project_id,
            params,
            environment_column=RuntimePlanRunRow.environment_id,
            time_column=RuntimePlanRunRow.started_at,
        )
        if params.source_key is not None:
            stmt = stmt.where(RuntimePlanRunRow.source_key == params.source_key)
        if params.environment_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.environment_id == params.environment_id)
        if params.workspace_id is not None:
            stmt = stmt.where(RuntimePlanRunRow.workspace_id == params.workspace_id)
        return stmt.subquery()

    async def list_plan_health(
        self,
        scope: ProjectScope,
        params: ListRuntimePlansInput,
        *,
        attention_only: bool = False,
    ) -> list[RuntimePlanHealth]:
        """Return DB-derived health grouped by runtime plan.

        See `list_engine_health` for why `attention_only` belongs here.
        """
        if self._denies_all(scope):
            return []
        plans_by_key = {
            (row["project_id"], row["source_key"], row["plan_id"]): row
            for row in (
                await self._session.execute(self._plan_label_select(scope, params))
            ).mappings()
        }
        base = self._plan_run_base(scope, params)
        group = [base.c.project_id, base.c.source_key, base.c.plan_id]
        stmt = self._run_group_health_statement(
            base,
            group,
            latest_columns=(
                "plan_name",
                "environment_name",
                "workspace_name",
                "ended_at",
            ),
            attention_only=attention_only,
            limit=params.limit,
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        summaries: list[RuntimePlanHealth] = []
        for row in rows:
            plan = plans_by_key.get(
                (row["project_id"], row["source_key"], row["plan_id"])
            )
            summaries.append(
                RuntimePlanHealth(
                    project_id=row["project_id"],
                    source_key=row["source_key"],
                    plan_id=row["plan_id"],
                    plan_name=plan["name"] if plan is not None else row["latest_plan_name"],
                    environment_name=(plan["environment_name"] if plan is not None else None)
                    or row["latest_environment_name"],
                    workspace_name=(plan["workspace_name"] if plan is not None else None)
                    or row["latest_workspace_name"],
                    total_runs=row["total_runs"],
                    failed_runs=row["failed_runs"],
                    failure_rate=_ratio(row["failed_runs"], row["total_runs"]) or 0.0,
                    current_failure_streak=row["current_failure_streak"],
                    latest_run_at=_as_utc(row["latest_run_at"]),
                    latest_finish_at=_as_utc(row["latest_ended_at"]),
                    latest_state=RuntimeExecutionState(row["latest_state"]),
                )
            )
        return summaries

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

        freshness_cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
        counts = await self._estate_inventory_counts(scope, params, freshness_cutoff)
        # Ask for the attention subsets' SIZE directly. Counting `failed_runs > 0`
        # over full health lists here would have re-derived "unhealthy" from a
        # FAILED-only field and hidden every misfire and timeout from the tiles;
        # building those lists just to call `len()` on them is what made this
        # endpoint the slowest read on the router while answering with 383 bytes.
        engine_params = ListRuntimeEnginesInput(
            project_id=params.project_id,
            source_key=params.source_key,
            limit=params.limit,
            read_terms=params.read_terms,
        )
        engine_inventory_is_populated = bool(
            (
                await self._session.execute(
                    select(func.count()).select_from(
                        self._engine_label_select(scope, engine_params).subquery()
                    )
                )
            ).scalar_one()
        )
        engine_base = self._engine_run_base(
            scope, engine_params, inventory_is_populated=engine_inventory_is_populated
        )
        job_base = self._job_run_base(
            scope,
            ListRuntimeJobsInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
                read_terms=params.read_terms,
            ),
        )
        plan_base = self._plan_run_base(
            scope,
            ListRuntimePlansInput(
                project_id=params.project_id,
                source_key=params.source_key,
                limit=params.limit,
                read_terms=params.read_terms,
            ),
        )
        return RuntimeEstateOverview(
            project_id=params.project_id,
            sources_total=counts["sources_total"],
            sources_failed=counts["sources_failed"],
            sources_partial=counts["sources_partial"],
            sources_demo=counts["sources_demo"],
            engines_total=counts["engines_total"],
            engines_online=counts["engines_online"],
            engines_degraded=counts["engines_degraded"],
            engines_offline=counts["engines_offline"],
            engines_unknown=counts["engines_unknown"],
            engines_unhealthy=await self._count_attention_run_groups(
                engine_base,
                [engine_base.c.project_id, engine_base.c.source_key, engine_base.c.engine_id],
                limit=params.limit,
            ),
            jobs_unhealthy=await self._count_attention_run_groups(
                job_base,
                [job_base.c.project_id, job_base.c.source_key, job_base.c.job_id],
                limit=params.limit,
            ),
            plans_unhealthy=await self._count_attention_run_groups(
                plan_base,
                [plan_base.c.project_id, plan_base.c.source_key, plan_base.c.plan_id],
                limit=params.limit,
            ),
            sources_stale=counts["sources_stale"],
            environments_total=counts["environments_total"],
            workspaces_total=counts["workspaces_total"],
            last_synced_at=_as_utc(counts["last_synced_at"]),
        )

    async def _estate_inventory_counts(
        self,
        scope: ProjectScope,
        params: ListRuntimeInventoryInput,
        freshness_cutoff: datetime.datetime,
    ):
        """The overview's inventory tiles, as one round trip of scalar subqueries.

        Every value here is a count or a max, so none of it justifies reading a
        row: this used to `select(RuntimeSourceRow)` and friends and reduce four
        whole-row lists in Python to produce nine integers and one timestamp.
        Each bucket is its own scoped scalar subquery over the same filter chain,
        so a status bucket and its total can never be computed from different
        predicates.
        """
        # Each table's ROW STAMP column and its environment column, or None
        # where it has neither. Stated explicitly rather than probed with
        # `getattr` so that a table gaining an `environment_id` later cannot
        # silently change what an environment-narrowed grant exposes on this
        # surface -- and so `runtime_sources`, the one table here whose stamp is
        # not called `project_id`, cannot be filtered on the wrong column by an
        # attribute lookup that happens to resolve. Its stamp is `estate_key`:
        # the same value every mirrored row carries, which is exactly what the
        # other three are filtered on.
        #
        # `runtime_sources` has no environment column: a source is not
        # per-environment, so a narrowed grant contributes no source rows here
        # (`_apply_read_terms`, fail-closed). Every table on this surface is
        # inventory, so none takes a time column.
        stamp_columns = {
            RuntimeSourceRow: RuntimeSourceRow.estate_key,
            RuntimeEngineRow: RuntimeEngineRow.project_id,
            RuntimeEnvironmentRow: RuntimeEnvironmentRow.project_id,
            RuntimeWorkspaceRow: RuntimeWorkspaceRow.project_id,
        }
        environment_columns = {
            RuntimeSourceRow: None,
            RuntimeEngineRow: RuntimeEngineRow.environment_id,
            RuntimeEnvironmentRow: RuntimeEnvironmentRow.environment_id,
            RuntimeWorkspaceRow: RuntimeWorkspaceRow.environment_id,
        }

        def aggregate(row_cls, expression, label: str, *conditions):
            """One scoped scalar subquery over `row_cls`, filtered like every other."""
            stmt = select(expression).select_from(row_cls)
            stmt = self._apply_scope(stmt, stamp_columns[row_cls], scope)
            stmt = self._apply_project_filter(
                stmt,
                stamp_columns[row_cls],
                params,
                environment_column=environment_columns[row_cls],
            )
            if params.source_key is not None:
                stmt = stmt.where(row_cls.source_key == params.source_key)
            for condition in conditions:
                stmt = stmt.where(condition)
            return stmt.scalar_subquery().label(label)

        def bucket(row_cls, label: str, *conditions):
            return aggregate(row_cls, func.count(), label, *conditions)

        stmt = select(
            bucket(RuntimeSourceRow, "sources_total"),
            bucket(
                RuntimeSourceRow,
                "sources_failed",
                RuntimeSourceRow.status == RuntimeSyncStatus.FAILED.value,
            ),
            bucket(
                RuntimeSourceRow,
                "sources_partial",
                RuntimeSourceRow.status == RuntimeSyncStatus.PARTIAL.value,
            ),
            # `is_demo` is an integer flag, and the Python it replaces counted
            # truthiness -- so any non-zero value, not just 1.
            bucket(RuntimeSourceRow, "sources_demo", RuntimeSourceRow.is_demo != 0),
            bucket(
                RuntimeSourceRow,
                "sources_stale",
                or_(
                    RuntimeSourceRow.status.in_(
                        (RuntimeSyncStatus.FAILED.value, RuntimeSyncStatus.PARTIAL.value)
                    ),
                    and_(
                        RuntimeSourceRow.last_synced_at.is_not(None),
                        RuntimeSourceRow.last_synced_at < freshness_cutoff,
                    ),
                ),
            ),
            aggregate(
                RuntimeSourceRow, func.max(RuntimeSourceRow.last_synced_at), "last_synced_at"
            ),
            bucket(RuntimeEngineRow, "engines_total"),
            bucket(
                RuntimeEngineRow,
                "engines_online",
                RuntimeEngineRow.status == RuntimeEngineStatus.ONLINE.value,
            ),
            bucket(
                RuntimeEngineRow,
                "engines_degraded",
                RuntimeEngineRow.status == RuntimeEngineStatus.DEGRADED.value,
            ),
            bucket(
                RuntimeEngineRow,
                "engines_offline",
                RuntimeEngineRow.status == RuntimeEngineStatus.OFFLINE.value,
            ),
            bucket(
                RuntimeEngineRow,
                "engines_unknown",
                RuntimeEngineRow.status.in_(
                    (RuntimeEngineStatus.UNKNOWN.value, RuntimeEngineStatus.STALE.value)
                ),
            ),
            bucket(RuntimeEnvironmentRow, "environments_total"),
            bucket(RuntimeWorkspaceRow, "workspaces_total"),
        )
        return (await self._session.execute(stmt)).mappings().one()

    # --- SQL-computed aggregate rollups ---
    #
    # These replace "fetch every row and reduce in Python" for the surfaces that
    # summarise thousands of runs. Grouping, counting, percentiles and ordering all
    # happen in the database; only the already-aggregated rows cross the boundary.

    def _environment_execution_ids(
        self,
        project_id: str | None,
        environment_ids: Sequence[str],
        project_ids: Sequence[str] | None = None,
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

        `project_ids` (a resolved shared read set) takes precedence over
        `project_id`. Both must narrow the same way: this subquery decides which
        executions every downstream aggregate is allowed to see, so leaving it
        on one project while the aggregates span several would filter a shared
        read down to the reader's own -- usually empty -- executions.
        """
        stmt = (
            select(RuntimeJobRunRow.execution_id)
            .where(RuntimeJobRunRow.environment_id.in_(list(environment_ids)))
            .where(RuntimeJobRunRow.execution_id.is_not(None))
        )
        if project_ids:
            stmt = self._apply_read_projects(stmt, RuntimeJobRunRow.project_id, project_ids)
        elif project_id is not None:
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
        project_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
        executable_kind: RuntimeExecutableKind | None = None,
    ):
        """Scoped, filtered execution rows as a subquery for aggregate composition.

        `environment_ids` narrows the population to runs observed in those
        environments. Because every execution-derived aggregate (reliability,
        streak, duration percentiles) composes over this subquery, applying the
        filter here is what keeps a scoped rollup internally consistent -- a
        per-aggregate filter would let one number describe PROD while its
        neighbour described PROD+TST.

        Three row-stamp filters can arrive, and they are tried in this order:
        `read_terms` (the resolved read set, per project), `project_ids` (a flat
        read set resolved by a caller that has no terms), then the bare
        `project_id`.

        `read_terms` used to be tried LAST, on the reasoning that the
        documentation lane resolved its own widening into `project_ids` and a
        second filter must not narrow it further. That was the leak: the
        documentation lane's flat set names the granting project without
        carrying the grant's own environment and window, so a grant scoped to
        `prod` returned the grantor's whole estate. The terms name the same
        projects in the same order (`resolve_runtime_read_scope` and
        `resolve_runtime_read_projects` walk the same grants), so preferring them
        loses no project and adds each grant's boundary back. For an ungranted
        read -- one unrestricted term -- `_apply_read_terms` emits the same bare
        `project_id = ...` that `_apply_read_projects` emits for a one-element
        list, so that path is unchanged.

        `environment_ids` and `read_terms` DO compose, and must: the first is
        the caller's selection, the second the grantor's boundary, and the
        honest answer is their intersection. The caller's selection is resolved
        across every project in the read set (not just `project_id`), for the
        reason `_environment_execution_ids` documents.
        """
        read_project_ids = list(project_ids) if project_ids else [
            term.project_id for term in read_terms
        ]
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
            RuntimeExecutionRow.executable_kind.label("executable_kind"),
        )
        stmt = self._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        if read_terms:
            stmt = self._apply_read_terms(
                stmt,
                RuntimeExecutionRow.project_id,
                read_terms,
                time_column=RuntimeExecutionRow.started_at,
                execution_id_column=RuntimeExecutionRow.execution_id,
            )
        elif project_ids:
            stmt = self._apply_read_projects(stmt, RuntimeExecutionRow.project_id, project_ids)
        elif project_id is not None:
            stmt = stmt.where(RuntimeExecutionRow.project_id == project_id)
        if source_key is not None:
            stmt = stmt.where(RuntimeExecutionRow.source_key == source_key)
        if job_name is not None:
            stmt = stmt.where(RuntimeExecutionRow.job_name == job_name)
        if executable_kind is not None:
            stmt = stmt.where(RuntimeExecutionRow.executable_kind == executable_kind.value)
        if since is not None:
            stmt = stmt.where(RuntimeExecutionRow.started_at >= since)
        if until is not None:
            stmt = stmt.where(RuntimeExecutionRow.started_at <= until)
        if environment_ids is not None:
            stmt = stmt.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(
                        project_id, environment_ids, read_project_ids or None
                    )
                )
            )
        return stmt.subquery()

    @staticmethod
    def _state_count(column, state: RuntimeExecutionState):
        return func.coalesce(func.sum(case((column == state.value, 1), else_=0)), 0)

    def _grouped_percentiles(
        self,
        base,
        group_columns: list,
        value_column,
        percentiles: Mapping[str, int],
    ):
        """Grouped discrete percentiles over one non-null numeric column of `base`.

        `percentiles` maps an output label to the percentile rank it carries, so
        one call can produce p50 and p95 side by side. Discrete (nearest-rank via
        `_percentile_at_rank`) throughout: every reported value is a real observed
        measurement. Kept generic because a second percentile implementation is
        how a p50 duration and a p50 start time end up disagreeing about what
        "median" means.
        """
        ranked = (
            select(
                *group_columns,
                value_column.label("value"),
                func.row_number()
                .over(partition_by=group_columns, order_by=value_column.asc())
                .label("rn"),
                func.count().over(partition_by=group_columns).label("cnt"),
            )
            .where(value_column.is_not(None))
            .subquery()
        )
        ranked_groups = [ranked.c[column.name] for column in group_columns]
        return (
            select(
                *ranked_groups,
                *[
                    _percentile_at_rank(ranked.c.rn, ranked.c.cnt, ranked.c.value, rank).label(label)
                    for label, rank in percentiles.items()
                ],
            )
            .group_by(*ranked_groups)
            .subquery()
        )

    def _duration_percentiles(self, base, group_columns: list):
        """Grouped p50/p95 duration subquery over non-null durations."""
        return self._grouped_percentiles(
            base, group_columns, base.c.duration_ms, {"p50": 50, "p95": 95}
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
            read_terms=params.read_terms,
            executable_kind=RuntimeExecutableKind.JOB,
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
                self._state_count(keyed.c.state, RuntimeExecutionState.MISFIRED).label("misfired_runs"),
                self._state_count(keyed.c.state, RuntimeExecutionState.TERMINATED).label("terminated_runs"),
                self._state_count(keyed.c.state, RuntimeExecutionState.TIMED_OUT).label("timed_out_runs"),
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
                misfired_runs=row["misfired_runs"],
                terminated_runs=row["terminated_runs"],
                timed_out_runs=row["timed_out_runs"],
                # FAILED only -- see the run-state semantics note at module top.
                failure_rate=_ratio(row["failed_runs"], row["total_runs"]) or 0.0,
                p50_duration_ms=row["p50"],
                p95_duration_ms=row["p95"],
                max_duration_ms=row["max_duration_ms"],
            )
            for row in rows
            if (bucket_start := _parse_bucket_key(row["bucket_key"])) is not None
        ]
        return points

    async def get_outcome_trend_coverage(
        self, scope: ProjectScope, params: RuntimeTrendInput
    ) -> RuntimeTrendCoverage:
        """Return captured execution bounds without claiming interval completeness.

        The display window is deliberately excluded: callers need to distinguish
        "before captured history" from a gap inside known history. Entity filters
        and authorization scope still apply.
        """
        if self._denies_all(scope):
            return RuntimeTrendCoverage()
        base = self._scoped_execution_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            job_name=params.job_name,
            since=None,
            until=None,
            read_terms=params.read_terms,
            executable_kind=RuntimeExecutableKind.JOB,
        )
        row = (
            await self._session.execute(
                select(
                    func.min(base.c.started_at).label("observed_from"),
                    func.max(base.c.started_at).label("observed_until"),
                )
            )
        ).mappings().one()
        return RuntimeTrendCoverage(
            observed_from=_as_utc(row["observed_from"]) if row["observed_from"] else None,
            observed_until=_as_utc(row["observed_until"]) if row["observed_until"] else None,
        )

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
            project_ids=params.project_ids,
            read_terms=params.read_terms,
            executable_kind=RuntimeExecutableKind.JOB,
        )
        group = [base.c.project_id, base.c.source_key, base.c.job_name]
        percentiles = self._duration_percentiles(base, group)
        aggregated = (
            select(
                *group,
                func.count().label("total_runs"),
                self._state_count(base.c.state, RuntimeExecutionState.SUCCESS).label("success_runs"),
                self._state_count(base.c.state, RuntimeExecutionState.FAILED).label("failed_runs"),
                self._state_count(base.c.state, RuntimeExecutionState.MISFIRED).label("misfired_runs"),
                self._state_count(base.c.state, RuntimeExecutionState.TERMINATED).label("terminated_runs"),
                self._state_count(base.c.state, RuntimeExecutionState.TIMED_OUT).label("timed_out_runs"),
                # "Started but no end recorded". A MISFIRED run never started,
                # so it has no end by definition and is not an incomplete run --
                # counting it here would report 417 phantom hung runs a month.
                # RUNNING stays in, as it always has: it is genuinely unfinished.
                func.coalesce(
                    func.sum(
                        case(
                            (
                                base.c.ended_at.is_(None)
                                & base.c.state.notin_(
                                    (RuntimeExecutionState.MISFIRED.value,)
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("unfinished_runs"),
                func.min(base.c.started_at).label("first_run_at"),
                func.max(base.c.started_at).label("last_run_at"),
                func.max(base.c.duration_ms).label("max_duration_ms"),
                # Anchor for the failure streak: every run after the most recent
                # non-failed run is, by definition, part of the current streak.
                # FAILED only, matching failure_rate -- a misfire or a timeout
                # breaks a streak of failures the same way any other non-failed
                # state always has.
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
                recent.c.latest_unfinished,
            )
            .join(percentiles, join_keys(percentiles), isouter=True)
            .join(streak, join_keys(streak), isouter=True)
            .join(recent, join_keys(recent), isouter=True)
        )
        if params.attention_only:
            # "Needs attention" is a current-state surface. Historical failures
            # followed by a healthy run are intentionally excluded.
            #
            # A misfired or timed-out latest run also needs a human: the work
            # did not happen. Before the enum widened, both arrived as `unknown`
            # and this filter dropped them, hiding 417 misfires and 251 timeouts
            # a month on one tenant.
            stmt = stmt.where(
                recent.c.latest_state.in_(_ATTENTION_STATE_VALUES)
                | recent.c.latest_unfinished.is_(True)
            ).order_by(
                recent.c.latest_unfinished.desc(),
                aggregated.c.last_run_at.desc(),
                aggregated.c.job_name.asc(),
            )
        else:
            stmt = stmt.order_by(
                aggregated.c.failed_runs.desc(),
                aggregated.c.last_run_at.desc(),
                aggregated.c.job_name.asc(),
            )
        stmt = stmt.offset(params.offset).limit(params.limit)
        rows = (await self._session.execute(stmt)).mappings().all()
        return [
            RuntimeJobReliability(
                project_id=row["project_id"],
                source_key=row["source_key"],
                job_name=row["job_name"],
                total_runs=row["total_runs"],
                success_runs=row["success_runs"],
                failed_runs=row["failed_runs"],
                misfired_runs=row["misfired_runs"],
                terminated_runs=row["terminated_runs"],
                timed_out_runs=row["timed_out_runs"],
                unfinished_runs=row["unfinished_runs"],
                # FAILED only -- see the run-state semantics note at module top.
                failure_rate=_ratio(row["failed_runs"], row["total_runs"]) or 0.0,
                current_failure_streak=row["current_failure_streak"] or 0,
                first_run_at=_as_utc(row["first_run_at"]),
                last_run_at=_as_utc(row["last_run_at"]),
                latest_state=(
                    RuntimeExecutionState(row["latest_state"]) if row["latest_state"] else None
                ),
                latest_unfinished=bool(row["latest_unfinished"]),
                p50_duration_ms=row["p50"],
                p95_duration_ms=row["p95"],
                max_duration_ms=row["max_duration_ms"],
            )
            for row in rows
        ]

    async def _observed_run_bounds(
        self, base, since: datetime.datetime | None, until: datetime.datetime | None
    ) -> tuple[datetime.datetime, datetime.datetime] | None:
        """UTC span the offset table has to cover, or None when nothing is in scope.

        A caller that bounded the window on both sides already told us the span,
        so the extra round trip is skipped. Otherwise min/max over the SAME scoped
        subquery the aggregate uses -- deriving the span from a wider population
        than the one being aggregated would build an offset table for rows the
        caller may not read.
        """
        if since is not None and until is not None:
            return _as_utc(since), _as_utc(until)  # type: ignore[return-value]
        row = (
            await self._session.execute(
                select(
                    func.min(base.c.started_at).label("observed_from"),
                    func.max(base.c.started_at).label("observed_until"),
                )
            )
        ).mappings().one()
        lower, upper = _as_utc(row["observed_from"]), _as_utc(row["observed_until"])
        if lower is None or upper is None:
            return None
        return lower, upper

    async def list_job_schedule_profile(
        self, scope: ProjectScope, params: ListRuntimeJobScheduleProfileInput
    ) -> list[RuntimeJobScheduleProfile]:
        """Per-job clock-time profile: when each job actually starts, per day.

        Answers "which jobs run overnight, and at what time" -- the question
        `get_outcome_trend` cannot answer, because that buckets CHRONOLOGICALLY
        (22:00 on 3 September) while this pools every run onto one 24-hour clock
        across many days. Talend schedules live in the management console, never
        in the scanned design, so these observed starts are the only schedule
        evidence that exists.

        Mechanics, all of it dialect-portable:

        - Timestamps are shifted to `params.timezone` wall-clock seconds with a
          DST-aware offset table derived in Python (`_utc_offset_segments`).
        - A window-relative axis, `shifted = local_seconds - window_start`, makes
          a window that wraps midnight behave exactly like one that does not:
          floor-dividing it by a day gives the NIGHT a run belongs to, and the
          remainder is its position within the window. A naive median over raw
          clock times puts the median of {23:50, 00:10} near midday; on this axis
          it lands at midnight, where it belongs.
        - The median start is discrete (nearest-rank), so it is always a start
          that really happened -- the same convention as every duration
          percentile here.

        Runs of the same job name in different environments are pooled, exactly
        as in `list_job_reliability`. Where one name carries a PROD and a DEV
        deployment that is a blend of two schedules; pass `environment_ids` to
        profile one deployment (see `_dominant_environment_gaps`).
        """
        if self._denies_all(scope):
            return []
        zone = ZoneInfo(params.timezone)
        base = self._scoped_execution_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            job_name=params.job_name,
            since=params.since,
            until=params.until,
            environment_ids=params.environment_ids,
            project_ids=params.project_ids,
            read_terms=params.read_terms,
            executable_kind=RuntimeExecutableKind.JOB,
        )
        bounds = await self._observed_run_bounds(base, params.since, params.until)
        if bounds is None:
            return []
        segments = _utc_offset_segments(zone, *bounds)

        start_hour = params.start_hour if params.start_hour is not None else 0
        end_hour = params.end_hour if params.end_hour is not None else start_hour
        window_start = start_hour * 3600
        # Half-open [start, end) measured forward from the start, so 22->7 is
        # nine hours and 22->22 (or no window at all) is the full day.
        window_length = (end_hour * 3600 - window_start) % _SECONDS_PER_DAY or _SECONDS_PER_DAY

        # The window-relative axis. `shifted` is exact integer seconds, so the
        # division below is a correctly-rounded division of an exact value and
        # its floor is the day index without fencepost risk; `window_pos` is then
        # plain integer arithmetic in 0..86399.
        shifted = _local_epoch_seconds(self._dialect, base.c.started_at, segments) - window_start
        window_day = cast(func.floor(shifted / float(_SECONDS_PER_DAY)), Integer)
        window_pos = shifted - window_day * _SECONDS_PER_DAY

        keyed = (
            select(
                base.c.project_id,
                base.c.source_key,
                base.c.job_name,
                base.c.state.label("state"),
                base.c.duration_ms.label("duration_ms"),
                window_day.label("window_day"),
                window_pos.label("window_pos"),
            )
            .where(window_pos < window_length)
            .subquery()
        )
        group = [keyed.c.project_id, keyed.c.source_key, keyed.c.job_name]
        durations = self._grouped_percentiles(keyed, group, keyed.c.duration_ms, {"p50": 50})
        starts = self._grouped_percentiles(keyed, group, keyed.c.window_pos, {"median_pos": 50})
        aggregated = (
            select(
                *group,
                func.count().label("total_runs"),
                func.count(func.distinct(keyed.c.window_day)).label("distinct_days"),
                func.min(keyed.c.window_pos).label("earliest_pos"),
                func.max(keyed.c.window_pos).label("latest_pos"),
                (
                    func.count()
                    - self._state_count(keyed.c.state, RuntimeExecutionState.SUCCESS)
                ).label("non_success_runs"),
            )
            .group_by(*group)
            .subquery()
        )

        def join_keys(other):
            return and_(
                other.c.project_id == aggregated.c.project_id,
                other.c.source_key == aggregated.c.source_key,
                other.c.job_name == aggregated.c.job_name,
            )

        stmt = (
            select(aggregated, durations.c.p50, starts.c.median_pos)
            .join(durations, join_keys(durations), isouter=True)
            .join(starts, join_keys(starts), isouter=True)
            .order_by(aggregated.c.total_runs.desc(), aggregated.c.job_name.asc())
            .offset(params.offset)
            .limit(params.limit)
        )
        rows = (await self._session.execute(stmt)).mappings().all()
        return [
            RuntimeJobScheduleProfile(
                project_id=row["project_id"],
                source_key=row["source_key"],
                job_name=row["job_name"],
                timezone=params.timezone,
                window_start_hour=start_hour,
                window_end_hour=end_hour,
                total_runs=row["total_runs"],
                distinct_days=row["distinct_days"],
                # Back off the window-relative axis onto the wall clock.
                median_start_time=_seconds_to_time(window_start + int(row["median_pos"])),
                earliest_start_time=_seconds_to_time(window_start + int(row["earliest_pos"])),
                latest_start_time=_seconds_to_time(window_start + int(row["latest_pos"])),
                p50_duration_ms=row["p50"],
                non_success_runs=row["non_success_runs"],
            )
            for row in rows
            if row["median_pos"] is not None
        ]

    @staticmethod
    def _latest_state_subquery(base, group_columns: list):
        """Most recent run's state per group, via a recency window rank."""
        ranked = select(
            *group_columns,
            base.c.state.label("state"),
            # A run with no end time is only "stuck" if its state does not
            # explain the missing end. RUNNING has not finished; MISFIRED never
            # started. Anything else without an end really is unaccounted for.
            case(
                (
                    (base.c.ended_at.is_(None))
                    & base.c.state.notin_(_OPEN_ENDED_STATE_VALUES),
                    True,
                ),
                else_=False,
            ).label("latest_unfinished"),
            func.row_number()
            .over(
                partition_by=group_columns,
                order_by=[base.c.started_at.desc(), base.c.id.desc()],
            )
            .label("rn"),
        ).subquery()
        ranked_groups = [ranked.c[column.name] for column in group_columns]
        return (
            select(
                *ranked_groups,
                ranked.c.state.label("latest_state"),
                ranked.c.latest_unfinished,
            )
            .where(ranked.c.rn == 1)
            .subquery()
        )

    # --- run-group health (engines / jobs / plans) ---
    #
    # The three health surfaces answer the same question over three different run
    # tables: group the runs by owner, count outcomes, measure the current failure
    # streak, and describe the most recent run. They used to do it by fetching
    # EVERY run row in scope and reducing in Python -- 55k `runtime_job_runs` rows
    # per engine/job call, and for plans 5k `runtime_plan_runs` rows whose
    # `step_results` JSON is 157 MB of TOAST, all de-TOASTed, shipped and
    # deserialized to produce a few hundred small numbers. The aggregation now
    # happens in the database and the run tables are never selected whole.
    #
    # There is one implementation because there is one rule. A second copy is how
    # `failed_runs`, `failure_rate` and `current_failure_streak` drift apart.

    @staticmethod
    def _attention_run_count(state_column):
        """Runs in the group that warrant a human, as a SQL aggregate.

        The in-SQL form of the row-level attention test. It must stay a count over
        `_ATTENTION_STATE_VALUES` rather than a `failed_runs > 0` test: the health
        models report FAILED counts alone, so narrowing on those would silently
        drop every misfire and timeout from the surfaces whose whole job is to
        show them.
        """
        return func.coalesce(
            func.sum(case((state_column.in_(_ATTENTION_STATE_VALUES), 1), else_=0)), 0
        )

    @staticmethod
    def _same_group(left, right, group_columns: Sequence):
        """Join predicate equating every grouping column of two grouped subqueries."""
        return and_(
            *[right.c[column.name] == left.c[column.name] for column in group_columns]
        )

    def _run_group_health_statement(
        self,
        base,
        group_columns: Sequence,
        *,
        latest_columns: Sequence[str] = (),
        with_percentiles: bool = False,
        attention_only: bool = False,
        limit: int,
    ):
        """Per-group run health as ONE statement, already ordered and page-limited.

        `base` is a subquery of run rows carrying at least `id`, `state` and
        `started_at` (plus `duration_ms` when `with_percentiles`) and every name in
        `latest_columns`; `group_columns` are its grouping columns.

        Ordering reproduces the Python reduce it replaces: failure rate, then
        streak, then recency, all descending. `first_seen_id` is the final key on
        purpose -- the old code sorted the grouped dict with a stable `sorted()`,
        so equal keys kept the order the group was first seen in an unordered row
        fetch, i.e. physical scan order. `min(id)` states that order explicitly
        instead of inheriting it from the plan, so the same data now always pages
        the same way.
        """
        failed_state = RuntimeExecutionState.FAILED.value
        ranked_columns = [
            base.c.id.label("id"),
            base.c.state.label("state"),
            base.c.started_at.label("started_at"),
            *[base.c[name].label(name) for name in latest_columns],
        ]
        if with_percentiles:
            ranked_columns.append(base.c.duration_ms.label("duration_ms"))
        ranked = select(
            *group_columns,
            *ranked_columns,
            func.row_number()
            .over(
                partition_by=list(group_columns),
                order_by=[base.c.started_at.desc(), base.c.id.desc()],
            )
            .label("rn"),
        ).subquery()
        groups = [ranked.c[column.name] for column in group_columns]

        def latest(column):
            """The value this column holds on the group's most recent run.

            Exactly one row per group has `rn == 1`, so the aggregate collapses to
            that row's value -- and stays NULL when the latest run's own value is
            NULL, which is what the caller wants: an older run's environment name
            is not the latest run's environment name.
            """
            return func.max(case((ranked.c.rn == 1, column)))

        aggregated = select(
            *groups,
            func.count().label("total_runs"),
            self._state_count(ranked.c.state, RuntimeExecutionState.FAILED).label("failed_runs"),
            # Every run more recent than the newest non-FAILED one is part of the
            # current streak by definition, so the streak is that run's recency
            # rank minus one. No non-FAILED run at all means the whole group is
            # one unbroken streak.
            func.coalesce(
                func.min(case((ranked.c.state != failed_state, ranked.c.rn))) - 1,
                func.count(),
            ).label("current_failure_streak"),
            latest(ranked.c.started_at).label("latest_run_at"),
            latest(ranked.c.state).label("latest_state"),
            *[latest(ranked.c[name]).label(f"latest_{name}") for name in latest_columns],
            func.min(ranked.c.id).label("first_seen_id"),
        ).group_by(*groups)
        if attention_only:
            aggregated = aggregated.having(self._attention_run_count(ranked.c.state) > 0)
        aggregated = aggregated.subquery()

        stmt = select(aggregated)
        if with_percentiles:
            percentiles = self._duration_percentiles(ranked, groups)
            stmt = select(aggregated, percentiles.c.p50, percentiles.c.p95).join(
                percentiles,
                self._same_group(aggregated, percentiles, group_columns),
                isouter=True,
            )
        # Cast to float rather than dividing integers: a NUMERIC division would
        # order by a rounded quotient, so two groups whose Python failure rates
        # differ could tie here.
        failure_rate = cast(aggregated.c.failed_runs, Float) / aggregated.c.total_runs
        return stmt.order_by(
            failure_rate.desc(),
            aggregated.c.current_failure_streak.desc(),
            aggregated.c.latest_run_at.desc().nullslast(),
            aggregated.c.first_seen_id.asc(),
        ).limit(limit)

    async def _count_attention_run_groups(
        self, base, group_columns: Sequence, *, limit: int
    ) -> int:
        """How many groups in `base` hold at least one run needing attention.

        The estate overview reports only the SIZE of the three unhealthy lists, so
        it must not build them: no recency window, no percentiles, no ordering,
        and -- for plans -- no `step_results`. The `limit` is applied before the
        count because the list-returning surfaces truncate to the same page size,
        and the overview tile has always shown the truncated figure.
        """
        grouped = (
            select(*group_columns)
            .group_by(*group_columns)
            .having(self._attention_run_count(base.c.state) > 0)
            .limit(limit)
            .subquery()
        )
        return int(
            (await self._session.execute(select(func.count()).select_from(grouped))).scalar_one()
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
            environment_ids=params.environment_ids,
            project_ids=params.project_ids,
            read_terms=params.read_terms,
            executable_kind=RuntimeExecutableKind.JOB,
        )
        group = [base.c.project_id, base.c.source_key, base.c.job_name]
        if params.attention_only:
            recent = self._latest_state_subquery(base, group)
            groups = (
                select(*[recent.c[column.name] for column in group])
                .where(
                    recent.c.latest_state.in_(_ATTENTION_STATE_VALUES)
                    | recent.c.latest_unfinished.is_(True)
                )
                .subquery()
            )
        else:
            groups = select(*group).distinct().subquery()
        return int((await self._session.execute(select(func.count()).select_from(groups))).scalar_one())

    @staticmethod
    def _evidence_coverage_statement(population_stmt, group_names: Sequence[str]):
        """failed/logged/diagnosed/ceiling per group, over a BOUNDED population.

        `population_stmt` must select `project_id`, `execution_id` and every
        name in `group_names` for exactly the executions the caller reports on
        -- already narrowed by project, window, scope and entity filters. The
        population DRIVES the read: both evidence tables are reached only as a
        correlated per-execution probe on `(project_id, execution_id)`.

        That direction is the whole point. Both call sites previously
        pre-aggregated the evidence table on its own and then joined the result
        to the execution population:

            LEFT JOIN (SELECT project_id, execution_id, count(id)
                       FROM runtime_log_events
                       WHERE project_id = :p
                       GROUP BY project_id, execution_id) AS log_counts ...

        so a question about at most a few hundred failed executions built a
        multi-million-row grouped result first and threw nearly all of it away.
        Measured on the live alitalend mirror: 3,763,044 log rows grouped to
        answer for 98 failed executions -- 1,430 ms of a 1,539 ms request, and
        the source of ~7 GB of temp-file spill on that database.
        `_doc_evidence_log_quality` had the same shape with no project
        predicate at all, so it grouped all 4,218,200 rows across every tenant.

        It was also why the caller's `since` did not change the cost: the
        window reached the outer execution filter but could not reach inside a
        subquery that did not mention the population. Bounding the population
        once, here, is what makes the window reach every part of the aggregate
        -- and it is why this is one helper rather than a fix applied twice.

        CORRELATED, not joined, and that part is load-bearing. Bounding the
        subquery by a join to the population was measured and was NOT enough:
        with 80 job keys the planner still chose a hash join and read the whole
        4.2M-row table (`Seq Scan on runtime_log_events rows=4218200`, 790 ms,
        319,339 blocks off disk), and the resulting plan cost of ~724,000 also
        tripped `jit_above_cost` for another 571 ms of JIT compilation. A
        sublink in the target list is executed per outer row, so the access
        path cannot be re-planned into a scan of the fact table as the key
        count grows.

        Row counts, not indexes, were the defect:
        `uq_runtime_log_events_project_exec_ordinal_fp` and
        `uq_runtime_incidents_project_exec` already serve the per-execution
        lookup this shape performs. Nothing was added.
        """
        population = population_stmt.subquery("evidence_population")
        # A CAPPED count, and only two questions are ever asked of it: "any
        # logs at all" and "at the capture ceiling". Both are answered exactly
        # by min(count, ceiling), so the probe stops reading at the ceiling.
        #
        # That matters because log volume per execution is extremely skewed --
        # measured here, 158 distinct executions hold 4,218,200 rows and the
        # largest single execution has 430,800. An uncapped probe over a failed
        # execution with a 400k-line log would read every one of those index
        # entries to learn a fact the ceiling already settles. It also keeps
        # the planner's cost estimate off `jit_above_cost`: with the uncapped
        # form the estimate reached 38M (the table has n_distinct=55 for
        # `execution_id`, so it expects ~19k rows per probe) and Postgres spent
        # 222 ms of a 238 ms statement compiling the plan.
        #
        # Never expose this value: it is a bounded indicator, not a log count.
        capped_lines = (
            select(RuntimeLogEventRow.id)
            .where(RuntimeLogEventRow.project_id == population.c.project_id)
            .where(RuntimeLogEventRow.execution_id == population.c.execution_id)
            .correlate(population)
            .limit(_LOG_CAPTURE_CEILING_LINES)
            .subquery("capped_log_lines")
        )
        log_lines = (
            select(func.count()).select_from(capped_lines).scalar_subquery()
        )
        # `count(*) = 0` reproduces the old LEFT JOIN's outcome exactly: a
        # zero-log execution produced no `log_counts` row under GROUP BY, so it
        # arrived as NULL and was excluded from `logged` the same way.
        #
        # Rendered as 1/0 rather than a bare boolean so the CASE below means
        # the same thing under both dialects.
        diagnosed = case(
            (
                select(RuntimeIncidentRow.id)
                .where(RuntimeIncidentRow.project_id == population.c.project_id)
                .where(RuntimeIncidentRow.execution_id == population.c.execution_id)
                .where(RuntimeIncidentRow.confidence.in_(_USEFUL_DIAGNOSIS_CONFIDENCES))
                .where(RuntimeIncidentRow.pattern_id.notin_(_UNINFORMATIVE_PATTERN_IDS))
                .correlate(population)
                .exists(),
                1,
            ),
            else_=0,
        )
        probed = select(
            *[population.c[name] for name in group_names],
            population.c.execution_id.label("execution_id"),
            log_lines.label("log_lines"),
            diagnosed.label("diagnosed"),
        ).subquery("evidence_probed")
        group = [probed.c[name] for name in group_names]
        return select(
            *group,
            # DISTINCT is defensive: `uq_runtime_executions_project_exec` makes
            # the population unique per (project, execution) already. It is
            # retained because a caller grouping by job name ALONE (the shared
            # documentation read) legitimately folds two projects together, and
            # the previous shape counted that the same way.
            func.count(func.distinct(probed.c.execution_id)).label("failed"),
            func.count(
                func.distinct(case((probed.c.log_lines > 0, probed.c.execution_id)))
            ).label("logged"),
            func.count(
                func.distinct(case((probed.c.diagnosed == 1, probed.c.execution_id)))
            ).label("diagnosed"),
            func.count(
                func.distinct(
                    case(
                        (
                            probed.c.log_lines >= _LOG_CAPTURE_CEILING_LINES,
                            probed.c.execution_id,
                        )
                    )
                )
            ).label("ceiling"),
        ).group_by(*group)

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
        # The population, not the evidence tables, is what gets narrowed: the
        # window, the project, the scope and the page's own job keys all land
        # here, and `_evidence_coverage_statement` reaches log events and
        # incidents only through it.
        population_stmt = (
            select(
                RuntimeExecutionRow.project_id.label("project_id"),
                RuntimeExecutionRow.execution_id.label("execution_id"),
                RuntimeExecutionRow.source_key.label("source_key"),
                RuntimeExecutionRow.job_name.label("job_name"),
            )
            .where(RuntimeExecutionRow.project_id == project_id)
            .where(RuntimeExecutionRow.state == RuntimeExecutionState.FAILED.value)
            .where(RuntimeExecutionRow.executable_kind == RuntimeExecutableKind.JOB.value)
            .where(key_filter)
        )
        if since is not None:
            population_stmt = population_stmt.where(RuntimeExecutionRow.started_at >= since)
        if until is not None:
            population_stmt = population_stmt.where(RuntimeExecutionRow.started_at <= until)
        population_stmt = self._apply_scope(
            population_stmt, RuntimeExecutionRow.project_id, scope
        )
        coverage_stmt = self._evidence_coverage_statement(
            population_stmt, ("source_key", "job_name")
        )
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
            .where(RuntimeExecutionRow.executable_kind == RuntimeExecutableKind.JOB.value)
            .where(key_filter)
            .where(RuntimeIncidentRow.confidence.in_(_USEFUL_DIAGNOSIS_CONFIDENCES))
            .where(RuntimeIncidentRow.pattern_id.notin_(_UNINFORMATIVE_PATTERN_IDS))
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
        project_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
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

        Read set -- `project_ids`
        -------------------------
        Defaults to `[project_id]`, which emits the identical `==` predicate
        this lane used before read grants existed.  A longer list is a resolved
        SHARED read (`resolve_runtime_read_projects`): one vendor tenant backing
        several scanned projects, mirrored once and read by each.  Two
        consequences the CALLER owns rather than this method:

        * the runtime job vocabulary widens to the whole granted tenant, so
          `orphan_runtime_jobs` will count jobs that belong to a sibling
          project.  Under a shared read that is not a mis-binding signal, and
          `RuntimeDocReadScope.shared_read` is what lets the generator say so.
          Narrowing the grant's environment scope is the other half: it holds
          the vocabulary to the environments this project actually documents.
        * one job name can appear under two project ids.  Reliability rollups
          are therefore merged by name (`_merge_reliability_by_job_name`)
          instead of the last-write-wins dict this used to build.

        Grant limits -- `read_terms`
        ----------------------------
        The same read set, carrying what each grant actually allows. It is what
        makes `project_ids` safe: that list names a granting project, and
        `environment_ids` above is the FLAT documentation scope, which by
        construction unions the grant's environments into the reader's own. A
        grant narrowed to `prod` therefore cannot be expressed there at all --
        unioned it returns the grantor's non-prod rows, intersected it deletes
        the reader's own. Each term's limits are applied to that term's project
        alone (`_apply_read_terms`), so the two filters compose: a sub-query
        reads `flat environments AND this project's granted environments`, and
        `caller's window AND this project's granted window`.

        Every sub-query below is bounded this way, because a documentation
        prompt is assembled from all of them and one unbounded aggregate is a
        leak whatever the others do. How each one expresses the narrowing is
        fixed by what its table carries:

        * `runtime_job_runs` (cadence, environments) and `runtime_plans` --
          their own `environment_id`; the run facts also take the window through
          `started_at`, the plan inventory deliberately does not.
        * `runtime_executions` (reliability, durations, log quality, incidents)
          and `runtime_failures` -- no environment column, so the narrowing
          routes through the execution id of the run that produced them, and
          `started_at` / `failed_at` is the window.
        * `runtime_job_scan_maps` -- carries none of the three itself, but the
          name it returns is read off a job run, so the limits attach there.
        * `runtime_log_events`, `runtime_incidents`,
          `runtime_execution_summaries` -- no project predicate at all; they are
          reached only through the execution population, and inherit its bounds
          through the join.

        No table this lane reads is unnarrowable, so nothing here fails closed
        today. A sub-query added over one that is -- `runtime_artifacts`,
        `runtime_connections`, `runtime_job_dependencies` -- WILL fail closed
        (`_apply_read_terms`), and must say so in the coverage record rather
        than return a silent absence. A missing row is a reportable gap; a
        leaked one cannot be taken back.

        Empty `read_terms` means no read set was resolved, and every sub-query
        falls back to `project_ids` exactly as it did before grants carried
        limits.
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
        terms = list(read_terms)
        # The terms name the same projects as `project_ids`, so a caller that
        # resolved only the terms still gets the full read set here -- which
        # matters because this list is what `_environment_execution_ids` scopes
        # the flat environment filter to, and narrowing it to the reader alone
        # would filter a shared read down to the reader's own executions.
        read_projects = (
            list(project_ids)
            if project_ids
            else [term.project_id for term in terms] or [project_id]
        )

        overrides = await self._doc_evidence_scan_map(
            scope, read_projects, requested, read_terms=terms
        )
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
                project_ids=read_projects,
                since=since,
                # Bounded by the runtime vocabulary of one project, not by the
                # scan: a project mirrors tens of jobs, not hundreds. A shared
                # read spans several projects, so the ceiling scales with the
                # read set -- truncating here would turn a mirrored job into
                # "no runtime data", with nothing in the response to show that
                # it was cut rather than absent.
                limit=min(500 * len(read_projects), 5000),
                environment_ids=scoped,
                read_terms=terms,
            ),
        )
        matched = _merge_reliability_by_job_name(
            row for row in reliability if row.job_name in lookup
        )
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
            scope, read_projects, job_names, since, environment_ids=scoped, read_terms=terms
        )
        environments = await self._doc_evidence_environments(
            scope, read_projects, job_names, since, environment_ids=scoped, read_terms=terms
        )
        gaps = await self._doc_evidence_gap_quantiles(
            scope, read_projects, job_names, since, environment_ids=scoped, read_terms=terms
        )
        plans = await self._doc_evidence_plans(
            scope, read_projects, job_names, read_terms=terms
        )
        failures = await self._doc_evidence_failures(
            scope,
            read_projects,
            job_names,
            since,
            max_failures_per_unit,
            environment_ids=scoped,
            read_terms=terms,
        )
        log_quality = await self._doc_evidence_log_quality(
            scope, read_projects, job_names, since, environment_ids=scoped, read_terms=terms
        )
        incident_insights = await self._doc_evidence_incidents(
            scope, read_projects, job_names, since, environment_ids=scoped, read_terms=terms
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
        self,
        scope: ProjectScope,
        project_ids: Sequence[str],
        unit_names: Sequence[str],
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> dict[str, str]:
        """Explicit static-unit -> runtime-job-name overrides, when any exist.

        `runtime_job_scan_maps` carries no environment, no execution id and no
        event time of its own, but the row it RETURNS is a job name read off a
        job run -- and that run has all three. So a grant's limits attach to the
        join partner: an override is honoured only where the run it names is
        itself readable. The project predicate stays on the scan-map column it
        has always been on; the join equates the two project ids anyway, so this
        adds the grant's narrowing without moving the filter that was already
        working.
        """
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
            .where(RuntimeJobScanMapRow.static_job_id.in_(unit_names))
            .distinct()
        )
        stmt = self._apply_doc_read_filter(
            stmt,
            RuntimeJobScanMapRow.project_id,
            project_ids,
            read_terms,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
        )
        stmt = self._apply_scope(stmt, RuntimeJobScanMapRow.project_id, scope)
        rows = (await self._session.execute(stmt)).all()
        return {static_job_id: job_name for static_job_id, job_name in rows}

    async def _doc_evidence_durations(
        self,
        scope: ProjectScope,
        project_ids: Sequence[str],
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
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

        Grouped by job name ALONE, not by (project, job).  The result is keyed
        by name, so under a shared read a project id in the GROUP BY would
        return two rows for one key and the dict below would silently keep
        whichever arrived last.  For a single-project read the project id is
        constant within the group, so dropping it changes nothing.
        """
        base = select(
            RuntimeExecutionRow.job_name.label("job_name"),
            RuntimeExecutionRow.duration_ms.label("duration_ms"),
        ).where(
            RuntimeExecutionRow.job_name.in_(job_names),
            RuntimeExecutionRow.executable_kind == RuntimeExecutableKind.JOB.value,
            RuntimeExecutionRow.started_at >= since,
            RuntimeExecutionRow.state == RuntimeExecutionState.SUCCESS.value,
            RuntimeExecutionRow.ended_at.is_not(None),
            RuntimeExecutionRow.duration_ms.is_not(None),
        )
        base = self._apply_doc_read_filter(
            base,
            RuntimeExecutionRow.project_id,
            project_ids,
            read_terms,
            # `runtime_executions` carries no environment column, so a grant's
            # environment narrowing routes through the job run that produced the
            # execution -- the same route `_environment_execution_ids` documents.
            time_column=RuntimeExecutionRow.started_at,
            execution_id_column=RuntimeExecutionRow.execution_id,
        )
        if environment_ids is not None:
            base = base.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(None, environment_ids, project_ids)
                )
            )
        base = self._apply_scope(base, RuntimeExecutionRow.project_id, scope).subquery()
        group = [base.c.job_name]
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
            percentiles.c.job_name == maxima.c.job_name,
            isouter=True,
        )
        rows = (await self._session.execute(stmt)).all()
        return {job_name: (p50, p95, max_ms) for job_name, p50, p95, max_ms in rows}

    async def _doc_evidence_gap_quantiles(
        self,
        scope: ProjectScope,
        project_ids: Sequence[str],
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
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
            RuntimeJobRunRow.job_name.in_(job_names),
            RuntimeJobRunRow.started_at >= since,
        )
        base = self._apply_doc_read_filter(
            base,
            RuntimeJobRunRow.project_id,
            project_ids,
            read_terms,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
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
        project_ids: Sequence[str],
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
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
            .where(RuntimeJobRunRow.job_name.in_(job_names))
            .where(RuntimeJobRunRow.started_at >= since)
            .where(RuntimeJobRunRow.environment_name.is_not(None))
            .group_by(RuntimeJobRunRow.job_name, RuntimeJobRunRow.environment_name)
            .order_by(func.count().desc(), RuntimeJobRunRow.environment_name.asc())
        )
        stmt = self._apply_doc_read_filter(
            stmt,
            RuntimeJobRunRow.project_id,
            project_ids,
            read_terms,
            # Same reason as the flat filter below, for the other narrowing: an
            # environment a grant excludes must not be able to become this job's
            # DOMINANT one and take the gap quantiles with it.
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
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
        self,
        scope: ProjectScope,
        project_ids: Sequence[str],
        job_names: Sequence[str],
        read_terms: Sequence[RuntimeReadTerm] = (),
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
        stmt = select(
            RuntimePlanRow.name, RuntimePlanRow.display_name, RuntimePlanRow.steps
        ).where(RuntimePlanRow.steps.is_not(None))
        stmt = self._apply_doc_read_filter(
            stmt,
            RuntimePlanRow.project_id,
            project_ids,
            read_terms,
            # A plan is deployed into an environment and the row says which, so a
            # narrowed grant can be honoured here rather than failing closed. A
            # plan the provider left unattributed is excluded, the same reading
            # `_environment_execution_ids` applies to an unattributed run.
            environment_column=RuntimePlanRow.environment_id,
            # No time column, deliberately: `runtime_plans` is inventory. A
            # window grant bounds how much HISTORY a reader may see, not whether
            # an orchestration exists, and applying one here would delete plans
            # from an estate the reader was granted.
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
        project_ids: Sequence[str],
        job_names: Sequence[str],
        since: datetime.datetime,
        max_per_job: int,
        environment_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
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
            .where(RuntimeFailureRow.job_name.in_(job_names))
            .where(RuntimeFailureRow.failed_at >= since)
            .order_by(RuntimeFailureRow.failed_at.desc(), RuntimeFailureRow.id.desc())
        )
        stmt = self._apply_doc_read_filter(
            stmt,
            RuntimeFailureRow.project_id,
            project_ids,
            read_terms,
            # A failure row carries no environment of its own either, so a
            # grant's environment narrowing takes the same route through the job
            # run as the flat filter below, and `failed_at` is its window column.
            time_column=RuntimeFailureRow.failed_at,
            execution_id_column=RuntimeFailureRow.execution_id,
        )
        if environment_ids is not None:
            # A failure row carries no environment either, and joining through
            # the job run is what stops a TST stack trace being published as the
            # production failure mode -- the exact class of error the scope was
            # selected to exclude.
            stmt = stmt.where(
                RuntimeFailureRow.execution_id.in_(
                    self._environment_execution_ids(None, environment_ids, project_ids)
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
        project_ids: Sequence[str],
        job_names: Sequence[str],
        since: datetime.datetime,
        environment_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> dict[str, RuntimeDocLogQuality]:
        """Measure diagnostic coverage without shipping a single raw log line.

        A row count at the current 10k provider ceiling is a conservative
        truncation signal.  It may undercount truncation if a future provider
        lowers its own cap, but it never labels a complete shorter log as cut.

        `runtime_log_events` and `runtime_incidents` carry NO project predicate
        of their own. They are reachable only through the execution population
        below, which is already scoped, and both joins equate `project_id`
        column-to-column -- so scope arrives through the join and a second
        literal filter would be a duplicate rule to keep in step. That matters
        here more than elsewhere: the insights tables carry no `source_key`, so
        the join IS their only scoping mechanism.

        A read grant's own limits arrive the same way. The population carries
        them (`_apply_doc_read_filter`), so a grant narrowed to one environment
        or a short window bounds these coverage counts without either insights
        table needing a predicate it has no column for.

        Reaching them THROUGH the population is also what bounds the read.
        Aggregating them first and joining afterwards -- the previous shape --
        grouped every log row in the database (4.2M across all tenants here,
        with no project predicate at all) to answer for the matched jobs'
        failures in the caller's window. See `_evidence_coverage_statement`.

        Grouped by job name ALONE, deliberately: the result is keyed by name, so
        under a shared read a project id in the GROUP BY would return two rows
        for one key and the dict below would keep whichever arrived last. Same
        rule as `_doc_evidence_durations`.
        """
        population_stmt = (
            select(
                RuntimeExecutionRow.project_id.label("project_id"),
                RuntimeExecutionRow.execution_id.label("execution_id"),
                RuntimeExecutionRow.job_name.label("job_name"),
            )
            .where(RuntimeExecutionRow.job_name.in_(job_names))
            .where(RuntimeExecutionRow.executable_kind == RuntimeExecutableKind.JOB.value)
            .where(RuntimeExecutionRow.started_at >= since)
            .where(RuntimeExecutionRow.state == RuntimeExecutionState.FAILED.value)
        )
        population_stmt = self._apply_doc_read_filter(
            population_stmt,
            RuntimeExecutionRow.project_id,
            project_ids,
            read_terms,
            time_column=RuntimeExecutionRow.started_at,
            execution_id_column=RuntimeExecutionRow.execution_id,
        )
        if environment_ids is not None:
            population_stmt = population_stmt.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(None, environment_ids, project_ids)
                )
            )
        population_stmt = self._apply_scope(
            population_stmt, RuntimeExecutionRow.project_id, scope
        )
        stmt = self._evidence_coverage_statement(population_stmt, ("job_name",))
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
        project_ids: Sequence[str],
        job_names: Sequence[str],
        since: datetime.datetime,
        max_per_job: int = 3,
        environment_ids: Sequence[str] | None = None,
        read_terms: Sequence[RuntimeReadTerm] = (),
    ) -> dict[str, list[RuntimeDocIncidentInsight]]:
        """Return recurring diagnoses backed by useful log evidence.

        The live alitalend source shows the important dividing line: SQL Server
        deadlocks and exhausted Camel delivery carry medium confidence and
        concrete signals; generic exception/unknown rows merely say to review a
        log.  Only the first category is allowed into a documentation prompt.

        The incident and summary rows are scoped by their INNER/OUTER JOIN to
        the execution population -- both join `project_id` column-to-column --
        so the project predicate belongs on the execution table alone. Neither
        insights table carries a `source_key`, which makes that join their only
        scoping mechanism; a literal filter here would be a second copy of the
        same rule, free to drift out of step with it. A read grant's environment
        and window ride the same join: they are applied to the execution, and a
        diagnosis is published only for an execution the grant allows.
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
            .where(RuntimeExecutionRow.job_name.in_(job_names))
            .where(RuntimeExecutionRow.executable_kind == RuntimeExecutableKind.JOB.value)
            .where(RuntimeExecutionRow.started_at >= since)
            .where(RuntimeIncidentRow.confidence.in_(_USEFUL_DIAGNOSIS_CONFIDENCES))
            .where(RuntimeIncidentRow.pattern_id.notin_(_UNINFORMATIVE_PATTERN_IDS))
            .order_by(RuntimeExecutionRow.job_name, RuntimeIncidentRow.first_bad_at.desc())
        )
        stmt = self._apply_doc_read_filter(
            stmt,
            RuntimeExecutionRow.project_id,
            project_ids,
            read_terms,
            time_column=RuntimeExecutionRow.started_at,
            execution_id_column=RuntimeExecutionRow.execution_id,
        )
        if environment_ids is not None:
            stmt = stmt.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._environment_execution_ids(None, environment_ids, project_ids)
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
        read_terms: Sequence[RuntimeReadTerm] = (),
        bound_terms_by_measured_at: bool = True,
    ):
        """Scoped component-metric rows as a subquery.

        `read_terms` takes precedence over `project_id`. The metric table has no
        environment column, so a narrowed grant routes through `execution_id`,
        the same key the write-port and every metric join already use.

        `bound_terms_by_measured_at` exists because the two callers reach these
        rows differently. `list_component_hotspots` reads them DIRECTLY, so a
        windowed grant has to be applied here or it is not applied at all.
        `_job_throughput_base` reaches them through an INNER join to an already
        windowed execution population, and applying the window again on
        `measured_at` would drop a metric recorded moments after an in-window
        run -- the exact failure that function's docstring warns about, since
        the two windows are defined on different columns.
        """
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
        if read_terms:
            stmt = self._apply_read_terms(
                stmt,
                RuntimeComponentMetricRow.project_id,
                read_terms,
                time_column=(
                    RuntimeComponentMetricRow.measured_at
                    if bound_terms_by_measured_at
                    else None
                ),
                execution_id_column=RuntimeComponentMetricRow.execution_id,
            )
        elif project_id is not None:
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
            read_terms=params.read_terms,
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
            read_terms=params.read_terms,
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

        The caller's window is deliberately NOT passed to
        `_scoped_metric_base`, and that is not the same omission as the one
        `_evidence_coverage_statement` documents. The two windows are defined
        on different columns -- the execution's `started_at` and the metric's
        `measured_at` -- so a metric recorded just after an in-window run
        would be dropped by a `measured_at` predicate, and "rows moved by runs
        in this window" would silently stop meaning that. The metric side
        inherits the window through this INNER join, which is the correct
        single definition. The bounding rule generalises as "reach the fact
        table THROUGH the population", not "copy the window into every
        subquery". Volume is a non-issue here regardless:
        `runtime_component_metrics` is ~27k rows against `runtime_log_events`'
        4.2M.
        """
        executions = self._scoped_execution_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            job_name=params.job_name,
            since=params.since,
            until=params.until,
            read_terms=params.read_terms,
            executable_kind=RuntimeExecutableKind.JOB,
        )
        metrics = self._scoped_metric_base(
            scope,
            project_id=params.project_id,
            source_key=params.source_key,
            read_terms=params.read_terms,
            # The grant window reaches these rows through the INNER join below,
            # never through `measured_at`. See `_scoped_metric_base`.
            bound_terms_by_measured_at=False,
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
        # A signature is a rollup OF history, not an entity that exists apart
        # from it, so a windowed grant bounds it on `last_seen_at` -- the same
        # column the caller's own `since` filter uses below. A signature whose
        # last occurrence predates the window (or was never stamped at all, the
        # column being nullable) is not evidence for the window and is dropped,
        # which is the same reading `_environment_execution_ids` applies to a
        # run with no environment. There is no environment column and no
        # execution id here, so an environment-narrowed grant contributes no
        # signatures from the grantor.
        stmt = self._apply_project_filter(
            stmt,
            RuntimeErrorSignatureRow.project_id,
            params,
            time_column=RuntimeErrorSignatureRow.last_seen_at,
        )
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
        # Identical arguments to `list_job_runs`: the page and its total.
        stmt = self._apply_project_filter(
            stmt,
            RuntimeJobRunRow.project_id,
            params,
            environment_column=RuntimeJobRunRow.environment_id,
            time_column=RuntimeJobRunRow.started_at,
        )
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
        # Identical arguments to `list_plan_runs`: the page and its total.
        stmt = self._apply_project_filter(
            stmt,
            RuntimePlanRunRow.project_id,
            params,
            environment_column=RuntimePlanRunRow.environment_id,
            time_column=RuntimePlanRunRow.started_at,
        )
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
        # Identical arguments to `list_artifacts`: the page and its total.
        stmt = self._apply_project_filter(stmt, RuntimeArtifactRow.project_id, params)
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
        # Identical arguments to `list_connections`: the page and its total.
        stmt = self._apply_project_filter(stmt, RuntimeConnectionRow.project_id, params)
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
        # Identical arguments to `list_job_dependencies`: the page and its total.
        stmt = self._apply_project_filter(stmt, RuntimeJobDependencyRow.project_id, params)
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
        estate_key: str,
        source: RuntimeSource,
        ingested_at: datetime.datetime,
    ) -> None:
        """Upsert the MIRRORED source row a sync batch declares.

        `estate_key` is the stamp the rest of the batch is written with, so this
        row is keyed the same way its data is. These rows carry no credentials,
        no health and no schedule -- they are inventory, not connectors, which is
        why `list_pollable_sources` excludes them and why no attachment row is
        written for them.
        """
        stmt = select(RuntimeSourceRow).where(
            RuntimeSourceRow.estate_key == estate_key,
            RuntimeSourceRow.source_key == source.source_key,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            row = RuntimeSourceRow(estate_key=estate_key, source_key=source.source_key)
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
        row.executable_kind = job.executable_kind.value
        row.status = job.status
        row.environment_id = job.environment_id
        row.environment_name = job.environment_name
        row.workspace_id = job.workspace_id
        row.workspace_name = job.workspace_name
        row.engine_id = job.engine_id
        row.engine_name = job.engine_name
        row.owner = job.owner
        row.artifact_name = job.artifact_name
        row.artifact_id = job.artifact_id
        row.artifact_version = job.artifact_version
        row.metric_path = job.metric_path
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
        row.raw_state = run.raw_state
        row.misfire_reason = run.misfire_reason
        row.execution_type = run.execution_type
        row.executable_kind = run.executable_kind.value
        row.environment_id = run.environment_id
        row.environment_name = run.environment_name
        row.workspace_id = run.workspace_id
        row.workspace_name = run.workspace_name
        row.engine_id = run.engine_id
        row.engine_name = run.engine_name
        row.started_at = run.started_at
        row.ended_at = run.ended_at
        row.duration_ms = run.duration_ms
        row.completed_exchanges = run.completed_exchanges
        row.failed_exchanges = run.failed_exchanges
        row.total_exchanges = run.total_exchanges
        row.last_process_time_ms = run.last_process_time_ms
        row.mean_process_time_ms = run.mean_process_time_ms
        row.metrics_updated_at = run.metrics_updated_at
        row.metric_path = run.metric_path
        row.service_call_count = run.service_call_count
        row.one_minute_rate = run.one_minute_rate
        row.five_minute_rate = run.five_minute_rate
        row.fifteen_minute_rate = run.fifteen_minute_rate
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
        row.raw_state = run.raw_state
        row.misfire_reason = run.misfire_reason
        row.execution_type = run.execution_type
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
        row.raw_state = execution.raw_state
        row.misfire_reason = execution.misfire_reason
        row.execution_type = execution.execution_type
        row.executable_kind = execution.executable_kind.value
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
        row.scope = event.scope
        row.attribution_safe = event.attribution_safe
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
        raw_state=row.raw_state,
        misfire_reason=row.misfire_reason,
        execution_type=row.execution_type,
        executable_kind=RuntimeExecutableKind(row.executable_kind),
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
        scope=row.scope,
        attribution_safe=row.attribution_safe,
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
        estate_key=row.estate_key,
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


def _to_source_read(
    row: RuntimeSourceRow,
    health: RuntimeSourceHealthRow | None,
    *,
    linked_project_ids: Sequence[str] = (),
) -> RuntimeSourceRead:
    return RuntimeSourceRead(
        id=row.id,
        estate_key=row.estate_key,
        linked_project_ids=list(linked_project_ids),
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
        executable_kind=RuntimeExecutableKind(row.executable_kind),
        status=row.status,
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        engine_id=row.engine_id,
        engine_name=row.engine_name,
        owner=row.owner,
        artifact_name=row.artifact_name,
        artifact_id=row.artifact_id,
        artifact_version=row.artifact_version,
        metric_path=row.metric_path,
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
        raw_state=row.raw_state,
        misfire_reason=row.misfire_reason,
        execution_type=row.execution_type,
        executable_kind=RuntimeExecutableKind(row.executable_kind),
        environment_id=row.environment_id,
        environment_name=row.environment_name,
        workspace_id=row.workspace_id,
        workspace_name=row.workspace_name,
        engine_id=row.engine_id,
        engine_name=row.engine_name,
        started_at=_as_utc(row.started_at),
        ended_at=_as_utc(row.ended_at),
        duration_ms=row.duration_ms,
        completed_exchanges=row.completed_exchanges,
        failed_exchanges=row.failed_exchanges,
        total_exchanges=row.total_exchanges,
        last_process_time_ms=row.last_process_time_ms,
        mean_process_time_ms=row.mean_process_time_ms,
        metrics_updated_at=_as_utc(row.metrics_updated_at),
        metric_path=row.metric_path,
        service_call_count=row.service_call_count,
        one_minute_rate=row.one_minute_rate,
        five_minute_rate=row.five_minute_rate,
        fifteen_minute_rate=row.fifteen_minute_rate,
    )


def _to_deployment(
    job: RuntimeJobRow,
    row: RuntimeJobRunRow | None,
) -> RuntimeDeployment:
    return RuntimeDeployment(
        project_id=job.project_id,
        source_key=job.source_key,
        deployment_id=row.run_id if row is not None else None,
        executable_id=job.job_id,
        executable_name=job.name,
        executable_kind=RuntimeExecutableKind(job.executable_kind),
        state=RuntimeExecutionState(row.state) if row is not None else None,
        environment_name=(row.environment_name if row is not None else None)
        or job.environment_name,
        workspace_name=(row.workspace_name if row is not None else None)
        or job.workspace_name,
        engine_name=(row.engine_name if row is not None else None) or job.engine_name,
        started_at=_as_utc(row.started_at) if row is not None else None,
        ended_at=_as_utc(row.ended_at) if row is not None else None,
        duration_ms=row.duration_ms if row is not None else None,
        completed_exchanges=row.completed_exchanges if row is not None else None,
        failed_exchanges=row.failed_exchanges if row is not None else None,
        total_exchanges=row.total_exchanges if row is not None else None,
        last_process_time_ms=row.last_process_time_ms if row is not None else None,
        mean_process_time_ms=row.mean_process_time_ms if row is not None else None,
        metrics_updated_at=_as_utc(row.metrics_updated_at) if row is not None else None,
        metric_path=(row.metric_path if row is not None else None) or job.metric_path,
        service_call_count=row.service_call_count if row is not None else None,
        one_minute_rate=row.one_minute_rate if row is not None else None,
        five_minute_rate=row.five_minute_rate if row is not None else None,
        fifteen_minute_rate=row.fifteen_minute_rate if row is not None else None,
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
        raw_state=row.raw_state,
        misfire_reason=row.misfire_reason,
        execution_type=row.execution_type,
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
    """Reference implementation of the discrete percentile this module reports.

    No read path calls it any more -- every percentile is computed in SQL by
    `_percentile_at_rank`. It stays as the executable definition of "the value at
    the ceil-ranked position", and
    `tests/test_runtime/test_run_group_health.py::TestDurationPercentiles`
    asserts the SQL form agrees with it for every group size. Delete one and the
    other loses its specification.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = math.ceil((percentile / 100.0) * len(ordered)) - 1
    return ordered[max(0, min(rank, len(ordered) - 1))]

