"""Produce the `elastic-aggregates-1` payload out of the runtime mirror.

The documentation harness defines `ElasticAggregatePayload` as an INPUT contract
and deliberately not a client (see
`knowledge_base/mcp-server/src/mcp_server/providers/custom/runtime_authority.py`):
"Whoever can reach the Elastic estate ... normalizes into this shape and hands it
in". Until now nobody did, so `enterprise/phase_runner.py` passed `None` and the
Elastic authority was permanently reported as `unavailable`. This module is that
producer.

It queries the DATABASE, never Elasticsearch. The Elastic source connector
mirrors the log estate into the same generic runtime tables every provider writes
(`runtime_executions`, `runtime_log_events`, `runtime_job_runs`,
`runtime_job_dependencies`); this module only reduces what is already mirrored.
Keeping the cluster out of the read path is what stops a documentation run from
acquiring a second live dependency, and it is why every property below is
testable without a cluster.

Five rules shape everything here.

1. **One scoping rule, reused, never re-invented.** Scope is resolved exactly as
   `GET /runtime/doc-evidence` resolves it -- `resolve_runtime_read_projects`
   then `resolve_doc_read_scope` (`doc_environment_ids` / `doc_window_days`) --
   and widening comes only from stored read grants. No caller may pass a project
   set or an environment in: this lane authenticates with a machine key and has
   no user scope behind it, so a request-supplied read set would be an
   authorization bypass with nothing behind it.

2. **The environment filter is applied ONCE, to the whole population.** It
   narrows the execution population that every aggregate below composes over
   (`_population`), never each aggregate separately. Measured on the live
   werkorder mirror, an unscoped read reported 3392 runs / 785 failures = 23.1%
   -- a figure describing no environment that exists (PROD alone 0.8%, ACC
   60.7%). One number describing a different population than its neighbour is
   the defect this shape prevents.

   For the same reason a per_job row is emitted per (job, environment) pair and
   never pooled: `runtime_authority._environment_key` cannot tell a pooled
   `"ACC+PROD"` string from a real single environment, so pooling here would
   print one environment's name over two environments' numbers.

3. **Absence is stated, never implied.** A project with no mirrored Elastic data
   gets a well-formed payload whose `limitations` say which of the several
   possible absences it is (no source registered, source with an empty mirror,
   messages that no mirrored execution accounts for, an environment selection
   nothing was mirrored for). Downstream code distinguishes "no source" from
   "source with nothing in it", and `_fuse_elastic` reports an empty `per_job`
   as `unavailable` -- so an empty payload with no limitation would publish an
   unexplained absence.

4. **Percentiles are real percentiles.** Latency p50/p95 come from
   `RuntimeStoreRepository._duration_percentiles`, the discrete nearest-rank
   pick shared with every other percentile in the store, so the value is always
   one that was actually observed. A mean is never substituted: verified in this
   codebase, `Maximo2RHM_Workorders` has a mean gap of 2202 s against a true
   median of 1801 s, and a mean-fed classifier downgraded a 30-minute schedule
   to "ad hoc". `runtime_job_runs.mean_process_time_ms` is mirrored and is
   deliberately NOT read here for the same reason. When a group has no mirrored
   duration at all, both percentiles are None -- never an approximation.

5. **Bounded, and indexed.** `runtime_log_events` is ~868 MB / 424k rows and its
   only indexes are `ix_runtime_log_events_project_id`,
   `ix_runtime_log_events_execution_id` and
   `uq_runtime_log_events_project_exec_ordinal_fp`; `source_key`, `severity` and
   `timestamp` are NOT indexed. So the log table is reached exclusively as a
   CORRELATED per-execution probe on `(project_id, execution_id)` -- the shape
   `RuntimeStoreRepository._evidence_coverage_statement` documents, where a
   population-bounded JOIN was measured to still lose to a hash join over the
   whole 4.2M-row table. The population itself is capped at
   `MAX_PROBED_EXECUTIONS` (newest first) and the output at `MAX_PER_JOB_ROWS`;
   both caps state themselves in `limitations` when they bind.

Job names are the names the ESTATE LOGS UNDER
---------------------------------------------
`per_job.job_name` is `runtime_executions.job_name` exactly as the Elastic
source mirrored it. It is never normalized, never fuzzy-matched to a scanned
unit, and -- unlike the doc-evidence lane -- never rewritten through
`runtime_job_scan_maps`, which exists to answer the opposite question ("which
runtime job backs this scanned unit").

That is deliberate on both sides. `_fuse_elastic` sets `unit_name=""` and does
no matching, because `AssetCheck_GetLocation` and `AssetCheck_GetLocations` are
both real, separately-scheduled integrations and a normaliser would silently
merge two of them. The measured asymmetry is real: `FactsManifest.unit_names()`
returns `job_id`, not the display name a scheduler files a job under, and 79 of
one project's 429 job rows have `job_id != name`. So a manifest-unit join is not
resolvable here without inventing a match, and inventing one would put a
limitation about unit X next to a message-volume row for job Y with nothing
linking them. The payload states this rather than guessing; if a join is ever
wanted it must be resolved server-side and returned as an explicit matched name.

Authority selection, and why `source_key` appears at all
--------------------------------------------------------
`knowledge_base/CLAUDE.md` records the hard-won rule "filter on `project_id`,
never `source_key`", because a mixed predicate is two scoping rules in one lane.
That rule is about SCOPE and is honoured: every predicate that decides what this
read may see is on `project_id` (the resolved read set, plus the RBAC scope).
`source_key` is used here for a different question -- WHICH AUTHORITY wrote a
row. TMC and Elastic mirror into the same tables, so counting TMC's engine log
lines as Elastic messages would fuse two authorities into one number, which is
exactly what `runtime_authority` exists to prevent. The selector is applied
uniformly to every aggregate in this payload, and it is never applied to the
four insights tables (which carry no `source_key`); those are not read here.

Prompt safety
-------------
The payload reaches an LLM. It carries counts, timestamps, percentiles, job
names, environment names and one shape-validated index pattern. No credentials,
no log bodies, no error text, no operator-authored labels (a source label such as
"<customer> PROD Elastic" is exactly the customer-identifying free text this
boundary must not carry), and no execution ids.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Sequence
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.runtime_data import (
    RuntimeExecutionRow,
    RuntimeJobDependencyRow,
    RuntimeJobRunRow,
    RuntimeLogEventRow,
    RuntimeSourceRow,
)
from src.runtime.models import RuntimeDocReadScope, RuntimeLogSeverity
from src.runtime.secrets import RuntimeCredentialSecretError
from src.runtime.source_registry import get_source_provider
from src.runtime.store import (
    ProjectScope,
    RuntimeRecordNotFoundError,
    RuntimeStoreRepository,
)

# Imported rather than copied. `_as_utc` is the store's one normalization of a
# stored timestamp (Postgres returns tz-aware values, SQLite naive ones), and a
# second copy of it here would be a second answer to the same question.
from src.runtime.store.repository import _as_utc

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "elastic-aggregates-1"

#: `runtime_sources.source_type` values that mirror a log estate. The column
#: holds `RuntimeSourceProvider.provider_key()`, so this is the one place that
#: names the Elastic connector; a provider registering under another key is
#: added HERE and nowhere else. Historical keys are picked up automatically from
#: the provider's own `source_type_aliases()` when it is registered, so an
#: alias never has to be duplicated into this module.
ELASTIC_SOURCE_TYPE_SEEDS: frozenset[str] = frozenset({"elastic", "elasticsearch"})

#: `runtime_job_dependencies.dependency_type` values that declare a correlated
#: hand-off from the row's job to another job. `dependency_id` carries the
#: correlation key, `dependency_name` the target job. That table is the only
#: generic mirror relation able to express a job -> job edge, and the edge has to
#: be DECLARED by whoever read the correlation id out of the log estate:
#: inferring one from name similarity is the join this codebase forbids
#: (`AssetCheck_GetLocation` and `AssetCheck_GetLocations` are both real).
CORRELATION_DEPENDENCY_TYPES: frozenset[str] = frozenset({"correlation"})

#: Output cap. One documentation run reads a catalogue, not an estate dump, and
#: the payload is prompt-bound. Rows are selected by message volume (the biggest
#: contributors survive a truncation) and the cap states itself in
#: `limitations` when it binds.
MAX_PER_JOB_ROWS = 200

#: Population cap. Every message figure is a correlated per-execution probe, so
#: this is also the ceiling on probe count: at most 3 index lookups per
#: execution. Newest executions win, and the cap states itself when it binds.
MAX_PROBED_EXECUTIONS = 20_000

#: Correlation edges are current-state inventory rows (tens per project), read
#: whole and reduced in Python. Bounded anyway so a mis-writing provider cannot
#: stream an unbounded relation into a prompt-bound payload.
MAX_CORRELATION_EDGES = 2_000

#: Diagnostic probe ceiling for the empty-payload branch. Reported as "at
#: least N", because it is a bounded probe and not a count.
UNATTRIBUTED_MESSAGE_PROBE_LIMIT = 500

#: Severity vocabulary of the store is exactly debug/info/warning/error, and only
#: `error` denotes a failed message. WARNING is deliberately excluded: the fusion
#: derives `failure_rate = error_count / message_count`, so counting warnings
#: would restate every log-side failure rate upward for no observed failure.
_ERROR_SEVERITIES: tuple[str, ...] = (RuntimeLogSeverity.ERROR.value,)

#: Non-secret config keys an Elastic source may declare its index pattern under.
#: A strict allowlist: this value is the only operator-supplied string that
#: crosses into the prompt, and it crosses as `provenance.source_key`.
#:
#: `index_names` is FIRST because it is the only key a real registered source
#: actually uses: it is `ElasticRuntimeProvider.scope_selection().field`, so the
#: connect dialog and `editable_config_fields` both bind the operator's index
#: selection to that name. Without it every live source is counted `undeclared`
#: and the payload permanently reports an index pattern nobody failed to supply.
_INDEX_PATTERN_KEYS: tuple[str, ...] = ("index_names", "index_pattern", "index", "indices")

#: Shape of an acceptable index pattern (`talend-esb-monitoring-*`,
#: `cluster:logs-*`). Neither `@`, `/` nor whitespace is admissible, so the value
#: cannot carry a URL, userinfo or a credential blob. Anything else is dropped
#: and reported rather than sanitized into something plausible.
_INDEX_PATTERN_RE = re.compile(r"^[A-Za-z0-9_.,:*+\-]{1,200}$")

_ISO_SECONDS = "%Y-%m-%dT%H:%M:%SZ"


# --- contract -----------------------------------------------------------------
# Field-for-field `ElasticPerJobAggregate` / `ElasticAggregatePayload` from
# `providers/custom/runtime_authority.py`, which is authoritative. `extra="forbid"`
# matches its `_Strict` base: the consumer rejects an unknown key, so this side
# must not grow an envelope field. `tests/test_runtime/test_elastic_aggregates.py`
# pins parity against the real definition.


class ElasticPerJobAggregate(BaseModel):
    """One job's log-side aggregates. Counts of MESSAGES, never of executions."""

    model_config = ConfigDict(extra="forbid")

    job_name: str
    environment: str = ""
    message_count: int = 0
    error_count: int = 0
    last_seen: str | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    correlation_key: str = ""
    correlated_target_job: str = ""


class ElasticAggregatePayload(BaseModel):
    """Normalized `elastic-aggregates-1` body, ready to hand to the fusion."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["elastic-aggregates-1"] = SCHEMA_VERSION
    index_pattern: str = ""
    window_days: int = 0
    window_end: str | None = None
    environments: list[str] = Field(default_factory=list)
    per_job: list[ElasticPerJobAggregate] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


# --- internal records ---------------------------------------------------------


class _ElasticSource(NamedTuple):
    """One registered Elastic source inside the resolved read set."""

    source_id: int
    source_key: str


class _CorrelationEdge(NamedTuple):
    correlation_key: str
    target_job: str


def _iso(moment: datetime.datetime | None) -> str | None:
    """Second-precision UTC ISO string, matching the harness's own `_iso`."""
    normalized = _as_utc(moment)
    if normalized is None:
        return None
    return normalized.strftime(_ISO_SECONDS)


def _elastic_source_types() -> frozenset[str]:
    """Seed types plus whatever the registered provider calls its own history."""
    types = set(ELASTIC_SOURCE_TYPE_SEEDS)
    for key in sorted(ELASTIC_SOURCE_TYPE_SEEDS):
        provider = get_source_provider(key)
        if provider is None:
            continue
        aliases = provider.source_type_aliases()
        types.update(str(alias) for alias in aliases if str(alias).strip())
    return frozenset(types)


def _clean_index_pattern(value: object) -> tuple[list[str], bool]:
    """Accepted patterns from one config value, plus whether anything was dropped."""
    candidates: list[str] = []
    if isinstance(value, str):
        candidates = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        candidates = [str(part).strip() for part in value]
    else:
        return [], value is not None
    accepted = [item for item in candidates if item and _INDEX_PATTERN_RE.match(item)]
    dropped = len([item for item in candidates if item]) != len(accepted)
    return accepted, dropped


class ElasticAggregateService:
    """Reduce the mirrored Elastic estate of one project to `elastic-aggregates-1`.

    Bound to one session and owning its own queries, in the shape of
    `RuntimeCorrelationService`: a reducer over the runtime store rather than a
    second store. Scope resolution is delegated to `RuntimeStoreRepository` so
    this lane cannot drift from the doc-evidence lane it shares a contract with.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = RuntimeStoreRepository(session)

    # --- public API ---

    async def build_payload(
        self,
        *,
        project_id: str,
        default_window_days: int = 90,
        now: datetime.datetime | None = None,
    ) -> ElasticAggregatePayload:
        """The whole payload for one project. Never raises on absence.

        `default_window_days` is a DEFAULT and not a ceiling: a configured
        `doc_window_days` is the operator's deliberate statement about how much
        history describes this estate, and this producer has no basis to
        overrule it -- the same rule `GET /runtime/doc-evidence` applies.

        There is deliberately no `environment_ids`, `project_ids` or `source_key`
        parameter. Scope is resolved from stored configuration only.
        """
        window_end = _as_utc(now) or datetime.datetime.now(datetime.UTC)
        read_projects = await self._store.resolve_runtime_read_projects(project_id)
        read_scope = await self._store.resolve_doc_read_scope(
            read_projects, project_id, default_window_days=default_window_days
        )
        window_days = read_scope.window_days or default_window_days
        since = window_end - datetime.timedelta(days=window_days)
        # This lane has no user behind it, so the resolved read set IS the RBAC
        # scope -- identical to the doc-evidence route.
        scope: ProjectScope = list(read_projects)

        limitations: list[str] = []
        sources = await self._elastic_sources(scope, read_projects)
        index_pattern, pattern_notes = await self._index_pattern(sources)
        limitations.extend(pattern_notes)
        limitations.extend(_scope_limitations(read_scope, window_days))

        if not sources:
            limitations.insert(
                0,
                "No Elastic runtime source is registered for this project "
                f"(considered source types: {', '.join(sorted(_elastic_source_types()))}). "
                "No message, error or latency figure exists to report, and this is a "
                "statement about the mirror, not about the estate.",
            )
            return ElasticAggregatePayload(
                index_pattern=index_pattern,
                window_days=window_days,
                window_end=_iso(window_end),
                environments=_scope_environments(read_scope, []),
                per_job=[],
                limitations=limitations,
            )

        source_keys = sorted(source.source_key for source in sources)
        environment_ids = read_scope.environment_ids
        if environment_ids is not None and not environment_ids:
            # An explicitly empty selection is a deliberate "nothing", not
            # "everything" -- the distinction `RuntimeDocReadScope` draws.
            limitations.append(
                "The documentation read scope for this project selects no environment, "
                "so no mirrored Elastic data is in scope. Nothing below is a statement "
                "about the estate."
            )
            return ElasticAggregatePayload(
                index_pattern=index_pattern,
                window_days=window_days,
                window_end=_iso(window_end),
                environments=[],
                per_job=[],
                limitations=limitations,
            )

        population_total = await self._population_size(
            scope,
            read_projects=read_projects,
            source_keys=source_keys,
            since=since,
            until=window_end,
            environment_ids=environment_ids,
        )
        rows = (
            await self._aggregate(
                scope,
                read_projects=read_projects,
                source_keys=source_keys,
                since=since,
                until=window_end,
                environment_ids=environment_ids,
            )
            if population_total
            else []
        )
        correlations, correlation_notes = await self._correlations(
            scope, read_projects=read_projects, source_keys=source_keys
        )

        per_job, output_notes = _rows_to_per_job(rows, correlations)
        limitations.extend(
            _population_limitations(
                population_total=population_total,
                rows=rows,
                window_days=window_days,
            )
        )
        limitations.extend(output_notes)
        if per_job:
            limitations.extend(correlation_notes)
            # Standing statements about how to READ the rows. Emitted only when
            # there are rows: a statement about counts that do not exist reads
            # as a claim that they do.
            limitations.append(
                "Job names below are the names the log estate logs under, not scanned "
                "unit names, and no join between the two is asserted here. Sibling "
                "designs differing by one character (AssetCheck_GetLocation and "
                "AssetCheck_GetLocations) are both real, so a name here must not be "
                "matched to a manifest unit by resemblance."
            )
            limitations.append(
                "Message and error counts include only attribution-safe log lines. "
                "Engine-scoped lines, which a shared runtime can interleave between "
                "deployments, are excluded rather than attributed to a job."
            )
        else:
            limitations.extend(
                await self._empty_population_notes(
                    scope,
                    read_projects=read_projects,
                    source_keys=source_keys,
                    since=since,
                    until=window_end,
                    window_days=window_days,
                    environment_ids=environment_ids,
                )
            )

        return ElasticAggregatePayload(
            index_pattern=index_pattern,
            window_days=window_days,
            window_end=_iso(window_end),
            environments=_scope_environments(read_scope, per_job),
            per_job=per_job,
            limitations=limitations,
        )

    # --- sources ---

    async def _elastic_sources(
        self, scope: ProjectScope, read_projects: Sequence[str]
    ) -> list[_ElasticSource]:
        """Registered Elastic sources this read may draw from, ordered by id.

        Selected across the whole read set, not just the requesting project: a
        granted source belongs to the GRANTING project, and a reader that looked
        only at its own rows would find no source and report "no Elastic source"
        for an estate it was explicitly granted.
        """
        types = _elastic_source_types()
        stmt = select(RuntimeSourceRow.id, RuntimeSourceRow.source_key).where(
            RuntimeSourceRow.source_type.in_(sorted(types))
        )
        stmt = RuntimeStoreRepository._apply_read_projects(
            stmt, RuntimeSourceRow.project_id, read_projects
        )
        stmt = RuntimeStoreRepository._apply_scope(stmt, RuntimeSourceRow.project_id, scope)
        stmt = stmt.order_by(RuntimeSourceRow.id.asc())
        return [
            _ElasticSource(source_id=row[0], source_key=row[1])
            for row in (await self._session.execute(stmt)).all()
        ]

    async def _index_pattern(self, sources: Sequence[_ElasticSource]) -> tuple[str, list[str]]:
        """The estate's index pattern, from each source's non-secret config.

        Read scope already pays a decrypt per source
        (`resolve_doc_read_scope`), and this is the same store: a connector's
        non-secret config lives in the credential blob. Only the allowlisted
        keys are looked at and only shape-valid values are kept, because this is
        the single operator-supplied string that reaches the prompt. A missing,
        unreadable or malformed value yields an empty pattern and a stated
        limitation -- never a guess, and never the source label.
        """
        patterns: list[str] = []
        notes: list[str] = []
        malformed = 0
        unreadable = 0
        undeclared = 0
        for source in sources:
            try:
                credentials, _key_id = await self._store.read_credentials(source.source_id)
            except RuntimeRecordNotFoundError:
                unreadable += 1
                continue
            except RuntimeCredentialSecretError:
                # Never log the payload or the key: only that this source's
                # config could not be opened.
                logger.warning(
                    "Elastic aggregates: config for runtime source %s could not be decrypted",
                    source.source_id,
                )
                unreadable += 1
                continue
            found = False
            source_malformed = False
            for key in _INDEX_PATTERN_KEYS:
                if key not in credentials:
                    continue
                accepted, dropped = _clean_index_pattern(credentials.get(key))
                source_malformed = source_malformed or dropped
                for item in accepted:
                    found = True
                    if item not in patterns:
                        patterns.append(item)
            if source_malformed:
                malformed += 1
            elif not found:
                undeclared += 1
        if undeclared:
            notes.append(
                f"{undeclared} mirrored Elastic source(s) declare no index pattern in "
                "their configuration, so the figures below cannot name every index they "
                "came from."
            )
        if malformed:
            notes.append(
                f"{malformed} declared Elastic index pattern(s) did not match the accepted "
                "shape and were dropped rather than published, so the index pattern below "
                "may be incomplete."
            )
        if unreadable:
            notes.append(
                f"The configuration of {unreadable} mirrored Elastic source(s) could not be "
                "read, so the index pattern below may be incomplete. The mirrored data "
                "itself is unaffected."
            )
        return ",".join(patterns), notes

    # --- population ---

    def _population(
        self,
        scope: ProjectScope,
        *,
        read_projects: Sequence[str],
        source_keys: Sequence[str],
        since: datetime.datetime,
        until: datetime.datetime,
        environment_ids: Sequence[str] | None,
        limit: int | None,
    ) -> Select:
        """Scoped Elastic executions, one row each, with their environment.

        The execution is the ONLY join from a log line to a job name:
        `runtime_log_events` carries `execution_id` and no `job_name`. So the
        Elastic-sourced execution population is what every aggregate below
        composes over, and it is where project scope, the window and the
        environment filter are applied -- once, together.

        `executable_kind` is deliberately NOT filtered. TMC's lane restricts to
        `job` because a long-running route's run count does not mean what a
        batch job's does; the log estate is precisely the authority that CAN
        describe a route, so filtering here would discard what this payload
        exists to supply.

        The environment name is a CORRELATED probe rather than a join, because a
        join to `runtime_job_runs` can fan out (its key is `run_id`, not
        `execution_id`) and a duplicated execution row would double-count that
        execution's messages. `max()` collapses it to one deterministic value,
        and `coalesce(..., '')` keeps the group key non-NULL so the percentile
        join below cannot silently drop the unnamed-environment group.
        """
        environment_probe = (
            select(func.max(RuntimeJobRunRow.environment_name))
            .where(RuntimeJobRunRow.project_id == RuntimeExecutionRow.project_id)
            .where(RuntimeJobRunRow.execution_id == RuntimeExecutionRow.execution_id)
        )
        if environment_ids is not None:
            environment_probe = environment_probe.where(
                RuntimeJobRunRow.environment_id.in_(list(environment_ids))
            )
        environment_probe = environment_probe.correlate(RuntimeExecutionRow).scalar_subquery()

        stmt = select(
            RuntimeExecutionRow.project_id.label("project_id"),
            RuntimeExecutionRow.execution_id.label("execution_id"),
            RuntimeExecutionRow.job_name.label("job_name"),
            func.coalesce(environment_probe, "").label("environment"),
            RuntimeExecutionRow.duration_ms.label("duration_ms"),
        ).where(
            RuntimeExecutionRow.source_key.in_(list(source_keys)),
            RuntimeExecutionRow.started_at >= since,
            RuntimeExecutionRow.started_at <= until,
        )
        stmt = RuntimeStoreRepository._apply_read_projects(
            stmt, RuntimeExecutionRow.project_id, read_projects
        )
        if environment_ids is not None:
            # The store's own environment-scoping subquery, reused rather than
            # restated: `runtime_executions` carries no environment column,
            # `runtime_job_runs` does, and a second copy of that rule here would
            # be free to drift from the doc-evidence lane's copy.
            stmt = stmt.where(
                RuntimeExecutionRow.execution_id.in_(
                    self._store._environment_execution_ids(None, environment_ids, read_projects)
                )
            )
        stmt = RuntimeStoreRepository._apply_scope(stmt, RuntimeExecutionRow.project_id, scope)
        if limit is not None:
            stmt = stmt.order_by(
                RuntimeExecutionRow.started_at.desc(), RuntimeExecutionRow.id.desc()
            ).limit(limit)
        return stmt

    async def _population_size(
        self,
        scope: ProjectScope,
        *,
        read_projects: Sequence[str],
        source_keys: Sequence[str],
        since: datetime.datetime,
        until: datetime.datetime,
        environment_ids: Sequence[str] | None,
    ) -> int:
        """How many Elastic executions the window really holds.

        One indexed aggregate, so the payload can say whether
        `MAX_PROBED_EXECUTIONS` bound instead of leaving a truncated count
        looking like a total.
        """
        counted = self._population(
            scope,
            read_projects=read_projects,
            source_keys=source_keys,
            since=since,
            until=until,
            environment_ids=environment_ids,
            limit=None,
        ).subquery("elastic_population_count")
        stmt = select(func.count()).select_from(counted)
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def _aggregate(
        self,
        scope: ProjectScope,
        *,
        read_projects: Sequence[str],
        source_keys: Sequence[str],
        since: datetime.datetime,
        until: datetime.datetime,
        environment_ids: Sequence[str] | None,
    ) -> list[dict[str, object]]:
        """Per (job, environment) message, error, recency and latency aggregates.

        Every log figure is a CORRELATED aggregate subquery keyed on
        `(project_id, execution_id)`, which
        `uq_runtime_log_events_project_exec_ordinal_fp` and
        `ix_runtime_log_events_execution_id` serve directly. Nothing may scan
        `runtime_log_events`: `source_key`, `severity` and `timestamp` are
        unindexed, and `_evidence_coverage_statement` records that even a
        population-bounded JOIN was re-planned into a full scan of the 4.2M-row
        table once the key count grew. A sublink in the target list cannot be.
        """
        # A CTE, not a subquery: the population is referenced twice below (once
        # by the message probes, once by the percentile ranking), and a plain
        # subquery would be evaluated -- and its environment probe re-run --
        # once per reference. Postgres materializes a multiply-referenced CTE,
        # so the execution scan happens once.
        population = self._population(
            scope,
            read_projects=read_projects,
            source_keys=source_keys,
            since=since,
            until=until,
            environment_ids=environment_ids,
            limit=MAX_PROBED_EXECUTIONS,
        ).cte("elastic_population")

        def probe(aggregate, *conditions):
            stmt = (
                select(aggregate)
                .where(RuntimeLogEventRow.project_id == population.c.project_id)
                .where(RuntimeLogEventRow.execution_id == population.c.execution_id)
                .where(RuntimeLogEventRow.source_key.in_(list(source_keys)))
                # Attribution safety is part of the count's meaning, not a
                # filter: an engine-scoped line may belong to a sibling
                # deployment, so counting it would attribute another job's
                # traffic to this one.
                .where(RuntimeLogEventRow.attribution_safe.is_(True))
            )
            for condition in conditions:
                stmt = stmt.where(condition)
            return stmt.correlate(population).scalar_subquery()

        probed = select(
            population.c.job_name.label("job_name"),
            population.c.environment.label("environment"),
            population.c.duration_ms.label("duration_ms"),
            probe(func.count(RuntimeLogEventRow.id)).label("messages"),
            probe(
                func.count(RuntimeLogEventRow.id),
                RuntimeLogEventRow.severity.in_(_ERROR_SEVERITIES),
            ).label("errors"),
            probe(func.max(RuntimeLogEventRow.timestamp)).label("last_seen"),
        ).subquery("elastic_probed")

        # The store's own nearest-rank percentile subquery. Reused, not copied:
        # a p95 computed by two different expressions is how two surfaces start
        # disagreeing about the same estate. Ranked over the POPULATION rather
        # than over `probed`, because latency needs only the duration -- ranking
        # the probed rows would re-evaluate every message probe a second time.
        percentiles = self._store._duration_percentiles(
            population, [population.c.job_name, population.c.environment]
        )
        aggregated = (
            select(
                probed.c.job_name.label("job_name"),
                probed.c.environment.label("environment"),
                func.coalesce(func.sum(probed.c.messages), 0).label("message_count"),
                func.coalesce(func.sum(probed.c.errors), 0).label("error_count"),
                func.max(probed.c.last_seen).label("last_seen"),
                # Non-null durations only: the sample size a percentile was
                # computed from, and the reason a null percentile can state
                # itself instead of reading as zero latency.
                func.count(probed.c.duration_ms).label("duration_samples"),
            )
            .group_by(probed.c.job_name, probed.c.environment)
            .subquery("elastic_aggregated")
        )
        stmt = (
            select(
                aggregated.c.job_name,
                aggregated.c.environment,
                aggregated.c.message_count,
                aggregated.c.error_count,
                aggregated.c.last_seen,
                aggregated.c.duration_samples,
                percentiles.c.p50,
                percentiles.c.p95,
            )
            .join(
                percentiles,
                and_(
                    percentiles.c.job_name == aggregated.c.job_name,
                    percentiles.c.environment == aggregated.c.environment,
                ),
                isouter=True,
            )
            # Volume first so a truncation keeps the rows that carry the estate,
            # then name/environment so the selection is deterministic.
            .order_by(
                aggregated.c.message_count.desc(),
                aggregated.c.job_name.asc(),
                aggregated.c.environment.asc(),
            )
            # One over the cap: enough to know the cap bound without a count.
            .limit(MAX_PER_JOB_ROWS + 1)
        )
        return [dict(row) for row in (await self._session.execute(stmt)).mappings().all()]

    # --- correlation ---

    async def _correlations(
        self,
        scope: ProjectScope,
        *,
        read_projects: Sequence[str],
        source_keys: Sequence[str],
    ) -> tuple[dict[str, _CorrelationEdge], list[str]]:
        """One declared correlation edge per job, plus what that dropped.

        At most ONE edge per job reaches the payload, because
        `ElasticPerJobAggregate` is single-valued and emitting a second row for
        the same (job, environment) would republish that job's message counts
        under a second key -- double counting for any reader that sums them. The
        survivor is the most recently observed edge, then alphabetical, so the
        choice is deterministic; the rest are reported.
        """
        stmt = select(
            RuntimeJobDependencyRow.job_name,
            RuntimeJobDependencyRow.dependency_id,
            RuntimeJobDependencyRow.dependency_name,
        ).where(
            RuntimeJobDependencyRow.source_key.in_(list(source_keys)),
            RuntimeJobDependencyRow.dependency_type.in_(sorted(CORRELATION_DEPENDENCY_TYPES)),
        )
        stmt = RuntimeStoreRepository._apply_read_projects(
            stmt, RuntimeJobDependencyRow.project_id, read_projects
        )
        stmt = RuntimeStoreRepository._apply_scope(
            stmt, RuntimeJobDependencyRow.project_id, scope
        )
        stmt = stmt.order_by(
            RuntimeJobDependencyRow.job_name.asc(),
            RuntimeJobDependencyRow.last_seen_at.desc().nullslast(),
            RuntimeJobDependencyRow.dependency_name.asc(),
            RuntimeJobDependencyRow.dependency_id.asc(),
        ).limit(MAX_CORRELATION_EDGES)

        edges: dict[str, _CorrelationEdge] = {}
        extra: dict[str, int] = {}
        unusable = 0
        for job_name, dependency_id, dependency_name in (
            await self._session.execute(stmt)
        ).all():
            target = str(dependency_name or "").strip()
            key = str(dependency_id or "").strip()
            if not target or not key:
                # An edge naming no target job is not a correlation; publishing
                # it would put a correlation key on a hop that goes nowhere.
                unusable += 1
                continue
            name = str(job_name)
            if name in edges:
                extra[name] = extra.get(name, 0) + 1
                continue
            edges[name] = _CorrelationEdge(correlation_key=key, target_job=target)

        notes: list[str] = []
        if not edges:
            notes.append(
                "No message correlation is mirrored for this estate, so no cross-hop "
                "latency is published. Absence of a correlated hand-off here means the "
                "correlation id was not mirrored, not that the hand-off does not exist."
            )
        if extra:
            named = ", ".join(f"{job} ({count + 1} edges)" for job, count in sorted(extra.items())[:10])
            notes.append(
                f"{len(extra)} job(s) have more than one mirrored correlation edge; each "
                f"row below carries the most recently observed one only: {named}."
            )
        if unusable:
            notes.append(
                f"{unusable} mirrored correlation edge(s) name no target job and were "
                "dropped rather than published as a hand-off to nowhere."
            )
        return edges, notes

    # --- stated absence ---

    async def _empty_population_notes(
        self,
        scope: ProjectScope,
        *,
        read_projects: Sequence[str],
        source_keys: Sequence[str],
        since: datetime.datetime,
        until: datetime.datetime,
        window_days: int,
        environment_ids: Sequence[str] | None,
    ) -> list[str]:
        """Why an Elastic source produced no row -- the three cases, distinguished.

        Only reached when the payload would otherwise be a silent empty, and the
        bounded message probe is CHEAP exactly here: nothing was attributed, so
        qualifying rows are plentiful and the `LIMIT` bites immediately.
        """
        notes = [
            f"An Elastic runtime source is registered, but its mirror holds no "
            f"attributable message for the last {window_days} day(s) within this read "
            f"scope. The estate itself is not described by that absence."
        ]

        job_runs = select(RuntimeJobRunRow.id).where(
            RuntimeJobRunRow.source_key.in_(list(source_keys)),
            RuntimeJobRunRow.started_at >= since,
            RuntimeJobRunRow.started_at <= until,
        )
        job_runs = RuntimeStoreRepository._apply_read_projects(
            job_runs, RuntimeJobRunRow.project_id, read_projects
        )
        job_runs = RuntimeStoreRepository._apply_scope(
            job_runs, RuntimeJobRunRow.project_id, scope
        )
        job_run_count = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(job_runs.subquery("elastic_job_run_probe"))
                )
            ).scalar_one()
            or 0
        )
        if job_run_count:
            notes.append(
                f"The mirror holds {job_run_count} Elastic job run(s) in this window but no "
                "execution to attribute a message to. A log line is joined to a job "
                "through its execution, so nothing can be counted per job until "
                "executions are mirrored."
            )

        messages = select(RuntimeLogEventRow.id).where(
            RuntimeLogEventRow.source_key.in_(list(source_keys)),
            RuntimeLogEventRow.timestamp >= since,
            RuntimeLogEventRow.timestamp <= until,
        )
        messages = RuntimeStoreRepository._apply_read_projects(
            messages, RuntimeLogEventRow.project_id, read_projects
        )
        messages = RuntimeStoreRepository._apply_scope(
            messages, RuntimeLogEventRow.project_id, scope
        )
        # LIMIT before the count: this is a bounded probe reported as "at
        # least N", never a total. It is affordable only in this branch --
        # nothing was attributed, so qualifying rows are plentiful and the
        # limit stops the read almost immediately.
        messages = messages.limit(UNATTRIBUTED_MESSAGE_PROBE_LIMIT + 1)
        message_count = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(messages.subquery("elastic_message_probe"))
                )
            ).scalar_one()
            or 0
        )
        if message_count:
            floor = (
                f"at least {UNATTRIBUTED_MESSAGE_PROBE_LIMIT}"
                if message_count > UNATTRIBUTED_MESSAGE_PROBE_LIMIT
                else str(message_count)
            )
            notes.append(
                f"The mirror holds {floor} Elastic log message(s) in this window that no "
                "in-scope execution accounts for, so they are counted nowhere below."
            )
        if environment_ids is not None:
            notes.append(
                "The read scope narrows this project to specific environments. A mirrored "
                "run that carries no environment cannot be shown to belong to one and is "
                "therefore not evidence for it."
            )
        return notes


# --- reduction (pure) ---------------------------------------------------------


def _rows_to_per_job(
    rows: Sequence[dict[str, object]],
    correlations: dict[str, _CorrelationEdge],
) -> tuple[list[ElasticPerJobAggregate], list[str]]:
    """Aggregated rows to contract records, capped, with the cap stated."""
    kept = list(rows[:MAX_PER_JOB_ROWS])
    notes: list[str] = []
    if len(rows) > MAX_PER_JOB_ROWS:
        notes.append(
            f"More than {MAX_PER_JOB_ROWS} (job, environment) pairs reported messages in "
            f"this window. The {MAX_PER_JOB_ROWS} highest-volume pairs are published; the "
            "remainder are omitted from this payload and their absence here is not an "
            "absence of traffic."
        )
    missing_latency = 0
    per_job: list[ElasticPerJobAggregate] = []
    for row in kept:
        job_name = str(row["job_name"])
        if not int(row["duration_samples"] or 0):
            missing_latency += 1
        edge = correlations.get(job_name)
        per_job.append(
            ElasticPerJobAggregate(
                job_name=job_name,
                environment=str(row["environment"] or ""),
                message_count=int(row["message_count"] or 0),
                error_count=int(row["error_count"] or 0),
                last_seen=_iso(row["last_seen"]) if row["last_seen"] is not None else None,
                # None, never an approximation: a group whose mirrored
                # executions carry no duration has no observed percentile, and a
                # mean of the ones that do would describe a different population.
                latency_p50_ms=_as_int(row["p50"]),
                latency_p95_ms=_as_int(row["p95"]),
                correlation_key=edge.correlation_key if edge else "",
                correlated_target_job=edge.target_job if edge else "",
            )
        )
    if missing_latency:
        notes.append(
            f"{missing_latency} of {len(kept)} published pair(s) report no latency "
            "percentile: the mirror holds no duration for their executions. The fields "
            "are null rather than estimated."
        )
    # Stable, contract-facing order. Volume order drove the CAP above; the
    # payload itself is sorted the way the fusion sorts it.
    per_job.sort(key=lambda item: (item.job_name, item.environment))
    return per_job, notes


def _as_int(value: object) -> int | None:
    """A SQL percentile as an int, or None. Never rounded into existence."""
    if value is None:
        return None
    return int(round(float(value)))  # type: ignore[arg-type]


def _scope_environments(
    read_scope: RuntimeDocReadScope, per_job: Sequence[ElasticPerJobAggregate]
) -> list[str]:
    """The environments this payload describes.

    When a read scope is configured these are its RESOLVED NAMES, because the
    fusion carries them onto every record's `provenance.read_scope` and a
    consumer has to be able to tell a narrowed read from a shrinking estate. An
    id that resolves to no mirrored environment is reported as a limitation and
    never mixed into this list: one list holding both ids and names would be two
    vocabularies under one key.

    With no scope configured, the observed environments ARE the scope.
    """
    if read_scope.environment_ids is not None:
        return list(read_scope.environment_names)
    return sorted({item.environment for item in per_job if item.environment})


def _scope_limitations(read_scope: RuntimeDocReadScope, window_days: int) -> list[str]:
    """What the reader must know about the scope these figures were read under."""
    notes: list[str] = []
    if read_scope.environment_ids is None:
        notes.append(
            "No documentation read scope is configured for this project, so every "
            "mirrored environment is included. Each row below still names its own "
            "environment; no figure pools two."
        )
    if read_scope.unavailable_environment_ids:
        notes.append(
            f"{len(read_scope.unavailable_environment_ids)} selected environment(s) have "
            "nothing in the mirror, so the read is narrower than the selection. Reading "
            "less than requested silently would be the worse failure."
        )
    if read_scope.shared_read:
        notes.append(
            f"This estate is read through {len(read_scope.shared_from)} read grant(s) "
            f"covering {len(read_scope.read_project_ids)} project(s). Job names below can "
            "belong to a sibling project that shares the same mirrored tenant, which is "
            "by design and not a mis-binding."
        )
    if not read_scope.configured:
        notes.append(
            f"Window: the default {window_days} day(s). No per-project window was "
            "configured, so this is not a statement that the estate's history is "
            f"{window_days} day(s) long."
        )
    return notes


def _population_limitations(
    *,
    population_total: int,
    rows: Sequence[dict[str, object]],
    window_days: int,
) -> list[str]:
    """Caps and coverage of the execution population the counts were read from."""
    notes: list[str] = []
    if population_total > MAX_PROBED_EXECUTIONS:
        notes.append(
            f"The window holds {population_total} mirrored Elastic executions; counts "
            f"below cover the most recent {MAX_PROBED_EXECUTIONS}. Every message figure "
            "is therefore a floor, not a total."
        )
    if population_total and not rows:
        notes.append(
            f"{population_total} mirrored Elastic execution(s) are in scope but none of "
            "them grouped into a reportable row. This is a mirror-side gap, not an idle "
            f"estate over the last {window_days} day(s)."
        )
    return notes
