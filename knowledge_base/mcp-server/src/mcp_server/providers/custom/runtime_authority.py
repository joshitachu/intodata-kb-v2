"""Fusion of the runtime authorities into one typed, provenanced bundle.

A documentation run can learn about runtime behaviour from more than one place.
TMC knows what the control plane *scheduled and executed*: run counts, failure
rates, durations, plans, environments. An Elastic/ESB log estate knows what
actually *flowed*: message volumes, error volumes, and end-to-end latency
between two correlated hops. They answer different questions over different
populations and they disagree routinely, because a TMC task execution is not a
message and a Camel route's uptime is not a batch job's run duration.

So this module fuses them by **placing them side by side with their provenance
attached**, never by averaging, summing or preferring one. Three rules follow
from that and every function here is written to keep them:

1. **Every record names its authority, its environment and its window.** A
   figure without those three is not comparable with anything, including the
   same figure from the previous run of the same project. ``RuntimeProvenance``
   is a required field on every record type, not an optional annotation.

2. **Absence is always ``unavailable``, never non-execution.** A unit with no
   observation row has no *figure*; that is a statement about the mirror, not
   about the job. The deployed-set gap list distinguishes the two cases that
   look identical from the outside -- ``not_observed_in_window`` (the lookup
   completed and found nothing) from ``lookup_failed`` (the lookup did not
   complete) -- and when the TMC lane is opted out or unavailable, EVERY listed
   unit is ``lookup_failed``, because ``RuntimeEvidenceStore`` fills
   ``unmatched_units`` with the whole request list in exactly that case and
   reading it as "no runtime record" would publish an inference nobody made.

3. **Numbers are never merged across authorities.** Two observation records for
   one unit -- one ``tmc``, one ``elastic`` -- is the correct output. One
   blended record is a number that describes no population that exists. The
   same defect class as the pooled failure rate that made an unscoped werkorder
   read report 23.1% for an estate whose PROD was 0.8% and whose ACC was 60.7%.

Pure and deterministic: no network, no clock unless the caller withholds one, no
filesystem except the two explicit write helpers and the one explicit read
helper. Everything the fusion needs arrives as a mapping.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mcp_server.config import config
from mcp_server.providers.custom.knowledge_contracts import write_json
from mcp_server.providers.custom.runtime_evidence import (
    RUNTIME_CACHE_JSON,
    RuntimeEvidenceStore,
    RuntimeMode,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "runtime-authority-1"
ELASTIC_SCHEMA_VERSION = "elastic-aggregates-1"

#: Staging-relative outputs. `_analysis/` is not in
#: `phase4_cleanup.PUBLISHED_STAGING_DIRS`, so both files are prompt input for
#: this run and are destroyed before publication. The durable operator-facing
#: record is `_assessment/runtime_evidence_coverage.json`, owned by the existing
#: runtime-evidence wiring; this module deliberately does not write there.
RUNTIME_AUTHORITY_JSON = "_analysis/_runtime_authority.json"
RUNTIME_AUTHORITY_MD = "_analysis/_runtime_authority.md"

AuthorityName = Literal["tmc", "elastic"]
AuthorityStatus = Literal["data", "opted_out", "unavailable", "partial"]
LimitationScope = Literal["project", "unit", "authority"]
DeployedSetStatus = Literal["not_observed_in_window", "lookup_failed"]

TMC = "tmc"
ELASTIC = "elastic"

# --- limitation codes ---------------------------------------------------------
# Constants rather than a Literal: a new authority will bring new codes, and a
# closed enum here would force a schema bump for a purely additive fact. The
# renderer prints an unknown code verbatim rather than dropping the row.

#: The TMC lane produced nothing. No figures, and no inference from that.
CODE_RUNTIME_UNAVAILABLE = "runtime_unavailable"
#: Runtime evidence was switched off for this run or this project.
CODE_RUNTIME_OPTED_OUT = "runtime_opted_out"
#: Some of the fetch completed. Coverage is a floor, not a total.
CODE_PARTIAL_FETCH = "partial_fetch"
#: The orphan ratio suggests the runtime mirror may describe a different estate.
CODE_SCOPE_MISMATCH = "scope_mismatch"
#: Same signal, but the mirror is legitimately shared, so the verdict is void.
CODE_SCOPE_MISMATCH_SUPPRESSED = "scope_mismatch_suppressed"
#: No Elastic-style aggregate was supplied, so no message or latency figures.
CODE_NO_ELASTIC_SOURCE = "no_elastic_source"
#: An Elastic payload was supplied but did not satisfy `elastic-aggregates-1`.
CODE_ELASTIC_PAYLOAD_INVALID = "elastic_payload_invalid"
#: TMC's execution model describes tasks, so route figures do not mean what the
#: same column means for a DI job.
CODE_TMC_OBSERVABILITY_DI_ONLY = "tmc_observability_di_only"
#: A failure headline reached this side still carrying an identifier shape.
CODE_REDACTION_FLAG = "redaction_flag"

#: `unit_kinds` values that denote a Camel/ESB route rather than a DI job.
_ROUTE_KINDS = frozenset({"route", "routes", "esb_route", "esb-route", "routelet"})

#: Rendered in place of an environment nobody reported.
_UNREPORTED_ENVIRONMENT = "unreported"

_STATUS_MEANING: dict[str, str] = {
    "data": "This authority answered. Figures below carry its provenance.",
    "partial": (
        "This authority answered incompletely. Every count below is a FLOOR, "
        "not a total, and a unit missing from it may simply not have been read."
    ),
    "opted_out": (
        "This authority was not consulted for this run. It holds no opinion "
        "about any unit below."
    ),
    "unavailable": (
        "This authority produced nothing usable. Absence of a figure is "
        "unavailability of evidence and nothing more."
    ),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- provenance ---------------------------------------------------------------


class RuntimeProvenance(_Strict):
    """Which authority said this, about which environment, over which window.

    Required on every record. Two runs of one project are not comparable
    without it, and a narrowed read scope is indistinguishable from a shrinking
    estate.
    """

    authority: AuthorityName
    environment: str = ""
    window_days: int = 0
    window_end: str | None = None
    source_key: str = ""
    #: The scope the authority was READ at. Empty means "everything the mirror
    #: holds", which is a different state from an explicit list naming all of
    #: it: a later ingest-scope widening extends the former and not the latter.
    read_scope: list[str] = Field(default_factory=list)


# --- deployed set -------------------------------------------------------------


class DeployedSetRecord(_Strict):
    """A scanned unit that HAS a runtime counterpart, and where.

    Deployed-set resolution is a separate question from behaviour: "is this job
    deployed and scheduled at all" is answerable even when every aggregate for
    it is empty, and it is the question a reader asks first.
    """

    unit_name: str
    job_name: str = ""
    environments: list[str] = Field(default_factory=list)
    plans: list[str] = Field(default_factory=list)
    provenance: RuntimeProvenance


class DeployedSetGap(_Strict):
    """A scanned unit with no runtime counterpart, and WHY it has none."""

    unit_name: str
    status: DeployedSetStatus


# --- observations -------------------------------------------------------------


class RuntimeFailureMode(_Strict):
    """One distinct failure headline and how often it recurred."""

    failed_at: str = ""
    error_summary: str = ""
    occurrences: int = 0


class ObservationRecord(_Strict):
    """Volume, error and last-seen aggregates for one unit under one authority.

    One record per unit per authority. When an authority was read across several
    environments and cannot separate them, ``environment`` is the joined key and
    ``pooled_environments`` is true -- pooling is stated, never silent, because a
    pooled reliability figure describes no environment that exists.
    """

    unit_name: str = ""
    job_name: str = ""
    environment: str = ""
    pooled_environments: bool = False
    total_runs: int = 0
    success_runs: int = 0
    failed_runs: int = 0
    unfinished_runs: int = 0
    failure_rate: float = 0.0
    current_failure_streak: int = 0
    first_run_at: str | None = None
    last_run_at: str | None = None
    runs_per_day: float | None = None
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    max_duration_ms: int | None = None
    recent_failure_modes: list[RuntimeFailureMode] = Field(default_factory=list)
    #: Deterministic cadence classification, as produced by
    #: `runtime_evidence.classify_cadence`. `None` when the authority supplies
    #: no inter-run gaps -- never a guessed interval.
    cadence: dict[str, Any] | None = None
    provenance: RuntimeProvenance


class CorrelationLatencyRecord(_Strict):
    """End-to-end latency between two correlated hops.

    Only a log estate can produce this: it needs a correlation id present on
    both sides. TMC reports per-execution duration and has no cross-unit
    correlation, so this list is empty on a TMC-only run and that emptiness is
    reported as unavailability, not as zero latency.
    """

    correlation_key: str
    source_unit: str = ""
    target_unit: str = ""
    p50_ms: int | None = None
    p95_ms: int | None = None
    sample_size: int = 0
    environment: str = ""
    provenance: RuntimeProvenance


# --- limitations --------------------------------------------------------------


class RuntimeLimitation(_Strict):
    """Something a reader must know before trusting a figure above."""

    code: str
    message: str
    scope: LimitationScope
    unit_name: str = ""
    authority: str = ""


# --- bundle -------------------------------------------------------------------


class RuntimeAuthorityBundle(_Strict):
    """The fused runtime picture for one run."""

    schema_version: Literal["runtime-authority-1"] = SCHEMA_VERSION
    project_id: str = ""
    generated_at: str = ""
    #: Per-authority status. A key is always present for every authority the
    #: fusion considered, including the ones that produced nothing -- a missing
    #: key would be indistinguishable from an authority nobody thought about.
    authorities: dict[str, AuthorityStatus] = Field(default_factory=dict)
    window_days: int = 0
    #: The TMC read scope only. Empty means every mirrored environment. An
    #: Elastic payload's own scope travels on its records' `provenance.read_scope`
    #: rather than here, so one field never describes two populations.
    read_environments: list[str] = Field(default_factory=list)
    deployed_set: list[DeployedSetRecord] = Field(default_factory=list)
    deployed_set_gaps: list[DeployedSetGap] = Field(default_factory=list)
    observations: list[ObservationRecord] = Field(default_factory=list)
    correlation_latency: list[CorrelationLatencyRecord] = Field(default_factory=list)
    limitations: list[RuntimeLimitation] = Field(default_factory=list)
    authority_notes: list[str] = Field(default_factory=list)

    def status_of(self, authority: str) -> str:
        return self.authorities.get(authority, "unavailable")

    def observations_for(self, authority: str) -> list[ObservationRecord]:
        return [item for item in self.observations if item.provenance.authority == authority]


# --- Elastic input contract ---------------------------------------------------


class ElasticPerJobAggregate(_Strict):
    """One job's log-side aggregates. Counts of MESSAGES, never of executions."""

    job_name: str
    environment: str = ""
    message_count: int = 0
    error_count: int = 0
    last_seen: str | None = None
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    correlation_key: str = ""
    correlated_target_job: str = ""


class ElasticAggregatePayload(_Strict):
    """Normalized INPUT contract for a log-estate aggregate.

    Deliberately a contract and not a client. Whoever can reach the Elastic
    estate -- a backend route, an operator export, a fixture -- normalizes into
    this shape and hands it in. Keeping the query out of the harness is what
    stops a documentation run from acquiring a second live dependency, and it
    makes every fusion property testable without a cluster.
    """

    schema_version: Literal["elastic-aggregates-1"] = ELASTIC_SCHEMA_VERSION
    index_pattern: str = ""
    window_days: int = 0
    window_end: str | None = None
    environments: list[str] = Field(default_factory=list)
    per_job: list[ElasticPerJobAggregate] = Field(default_factory=list)
    #: Free text from the producer, e.g. "index rolled over mid-window".
    limitations: list[str] = Field(default_factory=list)


# --- fusion -------------------------------------------------------------------


def _utc(now: datetime | None) -> datetime:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _environment_key(environments: Sequence[str]) -> tuple[str, bool]:
    """One environment string, plus whether it pools several.

    Pooling is reported rather than resolved. The alternative -- picking one
    environment and printing its name over combined numbers -- is how a figure
    ends up describing a population that does not exist.
    """
    cleaned = sorted({str(env).strip() for env in environments if str(env).strip()})
    if not cleaned:
        return "", False
    if len(cleaned) == 1:
        return cleaned[0], False
    return "+".join(cleaned), True


def fuse_runtime_authorities(
    tmc_store_payload: Mapping[str, Any] | None,
    elastic_payload: Mapping[str, Any] | None,
    *,
    project_id: str,
    unit_kinds: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> RuntimeAuthorityBundle:
    """Place every authority's runtime facts side by side, with provenance.

    ``tmc_store_payload`` is a ``RuntimeEvidenceStore.to_json()`` body (see
    ``load_tmc_store_payload``). ``elastic_payload`` is an
    ``ElasticAggregatePayload`` body. Either may be ``None``; a bundle is always
    returned, because a run that cannot state its runtime scope is worse than a
    run that states it has none.
    """
    generated = _utc(now)
    generated_at = _iso(generated)

    authorities: dict[str, AuthorityStatus] = {}
    limitations: list[RuntimeLimitation] = []
    notes: list[str] = []

    store = _load_store(tmc_store_payload, limitations)
    elastic = _load_elastic(elastic_payload, limitations)

    window_days = _resolve_window_days(store, elastic)
    read_environments = sorted(store.read_environments) if store is not None else []

    tmc_provenance_defaults = RuntimeProvenance(
        authority=TMC,
        window_days=store.window_days if store is not None else window_days,
        # TMC's read is anchored on this run: the mirror is read at generation
        # time and the window extends backwards from there.
        window_end=generated_at,
        source_key=store.source_key if store is not None else "",
        read_scope=list(read_environments),
    )

    deployed_set: list[DeployedSetRecord] = []
    deployed_set_gaps: list[DeployedSetGap] = []
    observations: list[ObservationRecord] = []
    correlation_latency: list[CorrelationLatencyRecord] = []

    authorities[TMC] = _tmc_status(store)
    if store is None:
        # `_load_store` already recorded the reason when a payload WAS supplied
        # and could not be read. Adding "no payload was available" beside it
        # would be both a duplicate code and a false statement.
        if tmc_store_payload is None:
            limitations.append(
                RuntimeLimitation(
                    code=CODE_RUNTIME_UNAVAILABLE,
                    message=(
                        "No TMC runtime payload was available to this run, so no unit "
                        "has a TMC figure. This is unavailability of evidence; it says "
                        "nothing about whether any unit runs."
                    ),
                    scope="authority",
                    authority=TMC,
                )
            )
    else:
        _fuse_tmc(
            store,
            tmc_provenance_defaults,
            deployed_set=deployed_set,
            deployed_set_gaps=deployed_set_gaps,
            observations=observations,
            limitations=limitations,
            notes=notes,
        )

    if elastic is None:
        authorities[ELASTIC] = "unavailable"
        # Same rule as the TMC branch: an invalid payload already has its own,
        # more specific code from `_load_elastic`.
        if elastic_payload is None:
            limitations.append(
                RuntimeLimitation(
                    code=CODE_NO_ELASTIC_SOURCE,
                    message=(
                        "No log-estate aggregate was supplied, so message volumes, "
                        "error volumes and end-to-end correlation latency are "
                        "unavailable for every unit. An empty correlation-latency list "
                        "below is that absence, not a measurement of zero."
                    ),
                    scope="authority",
                    authority=ELASTIC,
                )
            )
    else:
        authorities[ELASTIC] = _fuse_elastic(
            elastic,
            observations=observations,
            correlation_latency=correlation_latency,
            limitations=limitations,
        )

    _apply_unit_kind_limitations(unit_kinds, limitations)

    notes.extend(_standing_notes(authorities, elastic))

    return RuntimeAuthorityBundle(
        project_id=project_id,
        generated_at=generated_at,
        authorities=dict(sorted(authorities.items())),
        window_days=window_days,
        read_environments=list(read_environments),
        deployed_set=sorted(deployed_set, key=lambda item: (item.unit_name, item.job_name)),
        deployed_set_gaps=sorted(
            deployed_set_gaps, key=lambda item: (item.unit_name, item.status)
        ),
        observations=sorted(
            observations,
            key=lambda item: (
                item.provenance.authority,
                item.unit_name or item.job_name,
                item.job_name,
                item.environment,
            ),
        ),
        correlation_latency=sorted(
            correlation_latency,
            key=lambda item: (
                item.correlation_key,
                item.source_unit,
                item.target_unit,
                item.environment,
            ),
        ),
        limitations=sorted(
            limitations,
            key=lambda item: (
                _SCOPE_RANK.get(item.scope, 9),
                item.code,
                item.authority,
                item.unit_name,
            ),
        ),
        authority_notes=notes,
    )


_SCOPE_RANK = {"project": 0, "authority": 1, "unit": 2}


def _load_store(
    payload: Mapping[str, Any] | None, limitations: list[RuntimeLimitation]
) -> RuntimeEvidenceStore | None:
    if payload is None:
        return None
    try:
        return RuntimeEvidenceStore.from_json(payload)
    except Exception as exc:  # from_json is total, but a non-mapping still lands here
        logger.warning("Runtime authority: unreadable TMC payload (%s)", exc)
        limitations.append(
            RuntimeLimitation(
                code=CODE_RUNTIME_UNAVAILABLE,
                message=(
                    "A TMC runtime payload was supplied but could not be read, so it "
                    "contributes no figures. Unavailability of evidence, not a finding "
                    "about any unit."
                ),
                scope="authority",
                authority=TMC,
            )
        )
        return None


def _load_elastic(
    payload: Mapping[str, Any] | None, limitations: list[RuntimeLimitation]
) -> ElasticAggregatePayload | None:
    if payload is None:
        return None
    try:
        return ElasticAggregatePayload.model_validate(payload)
    except ValidationError as exc:
        logger.warning("Runtime authority: invalid Elastic aggregate payload (%s)", exc)
        limitations.append(
            RuntimeLimitation(
                code=CODE_ELASTIC_PAYLOAD_INVALID,
                message=(
                    "A log-estate aggregate was supplied but did not satisfy "
                    f"{ELASTIC_SCHEMA_VERSION}, so it contributes no figures: "
                    f"{_first_line(str(exc))}"
                ),
                scope="authority",
                authority=ELASTIC,
            )
        )
        return None


def _first_line(text: str, *, limit: int = 200) -> str:
    collapsed = " ".join(str(text).split())
    return collapsed[:limit]


def _resolve_window_days(
    store: RuntimeEvidenceStore | None, elastic: ElasticAggregatePayload | None
) -> int:
    if store is not None and store.window_days:
        return int(store.window_days)
    if elastic is not None and elastic.window_days:
        return int(elastic.window_days)
    return int(config.runtime_evidence_window_days)


def _tmc_status(store: RuntimeEvidenceStore | None) -> AuthorityStatus:
    if store is None:
        return "unavailable"
    if store.mode is RuntimeMode.OPTED_OUT:
        return "opted_out"
    if store.mode is RuntimeMode.UNAVAILABLE:
        # `partial` can be true here (every batch failed sets both), but the
        # lane still produced nothing, and "partial" would imply it produced
        # some. The partial_fetch limitation records the attempt.
        return "unavailable"
    return "partial" if store.partial else "data"


def _fuse_tmc(
    store: RuntimeEvidenceStore,
    defaults: RuntimeProvenance,
    *,
    deployed_set: list[DeployedSetRecord],
    deployed_set_gaps: list[DeployedSetGap],
    observations: list[ObservationRecord],
    limitations: list[RuntimeLimitation],
    notes: list[str],
) -> None:
    usable = store.mode is RuntimeMode.DATA

    if store.mode is RuntimeMode.OPTED_OUT:
        limitations.append(
            RuntimeLimitation(
                code=CODE_RUNTIME_OPTED_OUT,
                message=(
                    "Runtime evidence was not collected for this run, so the TMC lane "
                    "holds no figures. Every unit below is listed as lookup_failed "
                    "because nothing was looked up."
                ),
                scope="authority",
                authority=TMC,
            )
        )
    elif store.mode is RuntimeMode.UNAVAILABLE:
        limitations.append(
            RuntimeLimitation(
                code=CODE_RUNTIME_UNAVAILABLE,
                message=(
                    "The TMC runtime mirror produced nothing usable for this run "
                    f"({store.failed_batches} of {store.batches} fetch batches failed). "
                    "No unit has a TMC figure, and no absence below is evidence about "
                    "a unit's behaviour."
                ),
                scope="authority",
                authority=TMC,
            )
        )

    if store.partial:
        limitations.append(
            RuntimeLimitation(
                code=CODE_PARTIAL_FETCH,
                message=(
                    "The TMC fetch completed only in part "
                    f"({store.failed_batches} of {store.batches} batches failed). Every "
                    "TMC count is a floor, and a unit missing from the observation "
                    "table may simply not have been read."
                ),
                scope="authority",
                authority=TMC,
            )
        )

    # A shared read suppresses the mis-binding VERDICT, never the numbers: under
    # a granted read a sibling project's jobs are unmatched by design, so leaving
    # the verdict armed publishes an accusation against a correct configuration.
    if store.scope_mismatch and store.shared_read:
        granted_by = (
            " granted by " + ", ".join(sorted(store.shared_from)) if store.shared_from else ""
        )
        limitations.append(
            RuntimeLimitation(
                code=CODE_SCOPE_MISMATCH_SUPPRESSED,
                message=(
                    "The runtime mirror holds many jobs with no counterpart in this "
                    "scan, which would normally suggest a mis-binding. This project "
                    f"reads a SHARED mirror{granted_by}, so a sibling project's jobs "
                    "are unmatched by design and the mis-binding verdict is void. The "
                    "ratio is reported as an observation only."
                ),
                scope="project",
                authority=TMC,
            )
        )
    elif store.scope_mismatch:
        limitations.append(
            RuntimeLimitation(
                code=CODE_SCOPE_MISMATCH,
                message=(
                    "Most jobs in the runtime mirror have no counterpart in this scan. "
                    "Either the mirror is bound to a different project or this scan "
                    "covers a fraction of the estate. Treat the coverage denominator as "
                    "unverified until the binding is confirmed."
                ),
                scope="project",
                authority=TMC,
            )
        )

    if store.redaction_flags:
        limitations.append(
            RuntimeLimitation(
                code=CODE_REDACTION_FLAG,
                message=(
                    f"{len(store.redaction_flags)} runtime failure headline(s) reached "
                    "this run still carrying an identifier shape and were dropped from "
                    "the evidence. The failure population below is therefore missing "
                    "those modes."
                ),
                scope="project",
                authority=TMC,
            )
        )

    if usable:
        for unit_name in sorted(store.by_unit):
            evidence = store.by_unit[unit_name]
            environment, pooled = _environment_key(evidence.environments)
            provenance = defaults.model_copy(
                update={
                    "environment": environment,
                    "source_key": evidence.source_key or defaults.source_key,
                }
            )
            deployed_set.append(
                DeployedSetRecord(
                    unit_name=unit_name,
                    job_name=evidence.job_name,
                    environments=sorted(evidence.environments),
                    plans=sorted(evidence.plans),
                    provenance=provenance,
                )
            )
            observations.append(
                ObservationRecord(
                    unit_name=unit_name,
                    job_name=evidence.job_name,
                    environment=environment,
                    pooled_environments=pooled,
                    total_runs=evidence.total_runs,
                    success_runs=evidence.success_runs,
                    failed_runs=evidence.failed_runs,
                    unfinished_runs=evidence.unfinished_runs,
                    failure_rate=evidence.failure_rate,
                    current_failure_streak=evidence.current_failure_streak,
                    first_run_at=evidence.first_run_at,
                    last_run_at=evidence.last_run_at,
                    runs_per_day=evidence.runs_per_day,
                    p50_duration_ms=evidence.p50_duration_ms,
                    p95_duration_ms=evidence.p95_duration_ms,
                    max_duration_ms=evidence.max_duration_ms,
                    recent_failure_modes=[
                        RuntimeFailureMode(
                            failed_at=failed_at,
                            error_summary=summary,
                            occurrences=occurrences,
                        )
                        for failed_at, summary, occurrences in evidence.recent_failures
                    ],
                    cadence=evidence.cadence().to_json(),
                    provenance=provenance,
                )
            )
            if pooled:
                notes.append(
                    f"TMC figures for {unit_name} pool environments "
                    f"{', '.join(sorted(evidence.environments))}: the authority did not "
                    "separate them, so the reliability figure describes the pool and "
                    "not any single environment."
                )

    # Gap classification. Under a non-DATA mode the store fills `unmatched_units`
    # with the WHOLE request list (see `fetch_runtime_evidence`), which means "we
    # did not look", so reading it as "no runtime record" would publish an
    # inference nobody made.
    for unit_name in sorted(store.unretrieved_units):
        deployed_set_gaps.append(
            DeployedSetGap(unit_name=unit_name, status="lookup_failed")
        )
    unretrieved = set(store.unretrieved_units)
    for unit_name in sorted(store.unmatched_units):
        if unit_name in unretrieved:
            continue
        deployed_set_gaps.append(
            DeployedSetGap(
                unit_name=unit_name,
                status="not_observed_in_window" if usable else "lookup_failed",
            )
        )


def _fuse_elastic(
    elastic: ElasticAggregatePayload,
    *,
    observations: list[ObservationRecord],
    correlation_latency: list[CorrelationLatencyRecord],
    limitations: list[RuntimeLimitation],
) -> AuthorityStatus:
    for text in elastic.limitations:
        limitations.append(
            RuntimeLimitation(
                code=CODE_PARTIAL_FETCH,
                message=f"Log estate: {_first_line(text)}",
                scope="authority",
                authority=ELASTIC,
            )
        )

    read_scope = sorted({str(env).strip() for env in elastic.environments if str(env).strip()})
    for entry in sorted(
        elastic.per_job, key=lambda item: (item.job_name, item.environment)
    ):
        environment, pooled = _environment_key([entry.environment])
        provenance = RuntimeProvenance(
            authority=ELASTIC,
            environment=environment,
            window_days=elastic.window_days,
            window_end=elastic.window_end,
            source_key=elastic.index_pattern,
            read_scope=list(read_scope),
        )
        failed = max(0, int(entry.error_count))
        total = max(0, int(entry.message_count))
        observations.append(
            ObservationRecord(
                # A log estate keys on the job NAME it logged under. It carries
                # no scanner identity, and inventing one by fuzzy-matching a
                # manifest unit is exactly the join this codebase forbids
                # (`AssetCheck_GetLocation` / `AssetCheck_GetLocations`).
                unit_name="",
                job_name=entry.job_name,
                environment=environment,
                pooled_environments=pooled,
                total_runs=total,
                success_runs=max(0, total - failed),
                failed_runs=failed,
                failure_rate=round(failed / total, 4) if total else 0.0,
                last_run_at=entry.last_seen,
                p50_duration_ms=entry.latency_p50_ms,
                p95_duration_ms=entry.latency_p95_ms,
                # A log estate reports no inter-message gap quantiles here, so
                # cadence stays unclassified rather than estimated.
                cadence=None,
                provenance=provenance,
            )
        )
        if entry.correlation_key and entry.correlated_target_job:
            correlation_latency.append(
                CorrelationLatencyRecord(
                    correlation_key=entry.correlation_key,
                    source_unit=entry.job_name,
                    target_unit=entry.correlated_target_job,
                    p50_ms=entry.latency_p50_ms,
                    p95_ms=entry.latency_p95_ms,
                    sample_size=total,
                    environment=environment,
                    provenance=provenance,
                )
            )

    if not elastic.per_job:
        return "unavailable"
    return "partial" if elastic.limitations else "data"


def _apply_unit_kind_limitations(
    unit_kinds: Mapping[str, str] | None, limitations: list[RuntimeLimitation]
) -> None:
    """Flag every ESB route: TMC's execution model does not describe it.

    TMC schedules and reports TASKS. A long-running Camel route reports uptime
    per start, so its run count, duration percentiles and failure rate do not
    mean what the same columns mean for a DI job. Emitted per unit, because the
    reader of a route's row is the one who needs to be told.
    """
    if not unit_kinds:
        return
    for unit_name in sorted(unit_kinds):
        kind = str(unit_kinds[unit_name] or "").strip().lower()
        if kind not in _ROUTE_KINDS:
            continue
        limitations.append(
            RuntimeLimitation(
                code=CODE_TMC_OBSERVABILITY_DI_ONLY,
                message=(
                    f"{unit_name} is an ESB route. TMC's execution model describes "
                    "scheduled tasks, so a route's run count and duration percentiles "
                    "measure uptime per start, not per-message work. Do not read them "
                    "as batch-job reliability, and do not read a low count as low use."
                ),
                scope="unit",
                unit_name=unit_name,
                authority=TMC,
            )
        )


def _standing_notes(
    authorities: Mapping[str, str], elastic: ElasticAggregatePayload | None
) -> list[str]:
    notes = [
        "Every figure in this bundle names its authority, environment and window in "
        "its provenance. Figures from different authorities are never combined: a TMC "
        "task execution is not a log message, and one blended number would describe "
        "no population that exists.",
        "A unit with no row in a table has no figure from that authority. That is "
        "unavailability of evidence and must be written as such.",
    ]
    if authorities.get(TMC) in {"data", "partial"}:
        notes.append(
            "The TMC window ends at this bundle's generation time and extends "
            "backwards by the stated number of days."
        )
    if elastic is not None:
        notes.append(
            "Log-estate counts are MESSAGES, not executions. Its 'runs' column counts "
            "messages seen and its failure rate is the error-message share."
            + (
                f" Index pattern: {elastic.index_pattern}."
                if elastic.index_pattern
                else ""
            )
        )
    else:
        notes.append(
            "Correlation latency needs a correlation id present on both hops, which "
            "only a log estate carries. With no log estate supplied, end-to-end "
            "latency is unavailable for every path."
        )
    return notes


# --- persistence --------------------------------------------------------------


def load_tmc_store_payload(staging_dir: Path) -> dict[str, Any] | None:
    """Read the runtime-evidence cache the existing producer wrote.

    Returns ``None`` when the file is absent or unreadable, which the fusion
    turns into a stated ``runtime_unavailable``. That is deliberate: the runtime
    lane never gates a run, and an unreadable cache must degrade the documents
    with an explicit statement rather than fail them.
    """
    path = Path(staging_dir) / RUNTIME_CACHE_JSON
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Runtime authority: cannot read %s (%s)", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("Runtime authority: %s is not a JSON object", path)
        return None
    return payload


def write_runtime_authority(
    staging_dir: Path, bundle: RuntimeAuthorityBundle
) -> tuple[Path, Path]:
    """Persist the bundle as JSON plus a deterministic ASCII markdown rollup."""
    root = Path(staging_dir)
    json_path = root / RUNTIME_AUTHORITY_JSON
    md_path = root / RUNTIME_AUTHORITY_MD
    write_json(json_path, bundle)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_runtime_authority(bundle), encoding="utf-8")
    return json_path, md_path


# --- rendering ----------------------------------------------------------------


def _md(value: object) -> str:
    """Keep bounded text inside one Markdown cell, and keep it ASCII."""
    text = " ".join(str(value).replace("|", "\\|").split())
    return text.encode("ascii", errors="replace").decode("ascii")


def _cell(value: object) -> str:
    if value is None or value == "":
        return "unavailable"
    return _md(value)


def _ms(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{int(value):,} ms".replace(",", " ")


def _rate(value: float) -> str:
    return f"{value * 100:.1f}%"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _environment_cell(record: ObservationRecord) -> str:
    label = _md(record.environment or _UNREPORTED_ENVIRONMENT)
    return label + (" (pooled)" if record.pooled_environments else "")


def _execution_observation_table(records: Sequence[ObservationRecord]) -> list[str]:
    """Control-plane shape: executions, unfinished runs, streaks, durations."""
    return _table(
        [
            "Unit",
            "Job",
            "Environment",
            "Runs",
            "OK",
            "Failed",
            "Unfinished",
            "Failure rate",
            "Streak",
            "First seen",
            "Last seen",
            "p50",
            "p95",
            "max",
            "Cadence",
        ],
        [
            [
                _cell(record.unit_name),
                _cell(record.job_name),
                _environment_cell(record),
                str(record.total_runs),
                str(record.success_runs),
                str(record.failed_runs),
                str(record.unfinished_runs),
                _rate(record.failure_rate),
                str(record.current_failure_streak),
                _cell(record.first_run_at),
                _cell(record.last_run_at),
                _ms(record.p50_duration_ms),
                _ms(record.p95_duration_ms),
                _ms(record.max_duration_ms),
                _cell((record.cadence or {}).get("label")),
            ]
            for record in records
        ],
    )


def _elastic_observation_table(records: Sequence[ObservationRecord]) -> list[str]:
    """Log-estate shape: messages and end-to-end latency, no execution columns.

    A narrower table on purpose. Printing `Unfinished 0` and `Streak 0` for an
    authority that measures neither is the same defect as printing a zero for a
    missing figure: the reader cannot tell "measured as none" from "not measured".
    The job name is the only identity a log estate carries, so there is no unit
    column to fill with a resolution nobody made.
    """
    return _table(
        ["Job (as logged)", "Environment", "Messages", "OK", "Errors", "Error rate",
         "Last seen", "Latency p50", "Latency p95"],
        [
            [
                _cell(record.job_name),
                _environment_cell(record),
                str(record.total_runs),
                str(record.success_runs),
                str(record.failed_runs),
                _rate(record.failure_rate),
                _cell(record.last_run_at),
                _ms(record.p50_duration_ms),
                _ms(record.p95_duration_ms),
            ]
            for record in records
        ],
    )


def render_runtime_authority(bundle: RuntimeAuthorityBundle) -> str:
    """Deterministic ASCII markdown. Status first, then facts, then limitations.

    Order is the point. A reader who sees a reliability table before the status
    table reads an empty row as "this job does not run"; a reader who sees the
    status table first reads it as "this authority had nothing to say".
    """
    lines: list[str] = [
        "# RUNTIME AUTHORITY -- FUSED RUNTIME EVIDENCE",
        "",
        f"Schema: {bundle.schema_version}",
        f"Project: {_cell(bundle.project_id)}",
        f"Generated at: {_cell(bundle.generated_at)}",
        f"Read window: {bundle.window_days} days",
        "TMC read scope: "
        + (
            ", ".join(_md(env) for env in bundle.read_environments)
            if bundle.read_environments
            else "every mirrored environment (no narrowing configured)"
        ),
        "",
        "READ THE STATUS TABLE FIRST. A unit or a figure that does not appear below "
        "is UNAVAILABLE from that authority. Absence here is a statement about the "
        "runtime mirror, and it is never evidence about how a unit behaves.",
        "",
        "## 1. Authorities and status",
        "",
    ]

    lines.extend(
        _table(
            ["Authority", "Status", "What that means here"],
            [
                [_md(name), _md(status), _md(_STATUS_MEANING.get(status, "Unclassified."))]
                for name, status in sorted(bundle.authorities.items())
            ]
            or [["none", "unavailable", "No runtime authority was considered."]],
        )
    )

    lines.extend(["", "## 2. Deployed set", ""])
    if bundle.deployed_set:
        lines.append(
            f"{len(bundle.deployed_set)} scanned unit(s) resolved to a runtime "
            "counterpart. This answers only whether a unit is deployed and known to "
            "the control plane, not how it behaves."
        )
        lines.append("")
        lines.extend(
            _table(
                ["Unit", "Runtime job", "Environments", "Plans", "Authority"],
                [
                    [
                        _md(record.unit_name),
                        _cell(record.job_name),
                        _cell(", ".join(record.environments)),
                        _cell(", ".join(record.plans)),
                        _md(record.provenance.authority),
                    ]
                    for record in bundle.deployed_set
                ],
            )
        )
    else:
        lines.append(
            "No scanned unit resolved to a runtime counterpart. Deployment state is "
            "unavailable for every unit in this run; it is not a finding that any "
            "unit is undeployed."
        )

    lines.extend(["", "### Deployed-set gaps", ""])
    if bundle.deployed_set_gaps:
        lines.extend(
            _table(
                ["Unit", "Status"],
                [
                    [_md(gap.unit_name), _md(gap.status)]
                    for gap in bundle.deployed_set_gaps
                ],
            )
        )
        lines.extend(
            [
                "",
                "- not_observed_in_window: the lookup completed and returned no "
                "execution inside the read window. The unit may run on a longer "
                "cycle, be triggered by a parent job, or be idle.",
                "- lookup_failed: the lookup did not complete. This unit's runtime "
                "behaviour is unavailable; nothing at all was learned about it.",
            ]
        )
    else:
        lines.append("None recorded.")

    lines.extend(["", "## 3. Observations", ""])
    if bundle.observations:
        for authority in sorted({item.provenance.authority for item in bundle.observations}):
            records = [
                item for item in bundle.observations if item.provenance.authority == authority
            ]
            lines.extend([f"### Authority: {_md(authority)}", ""])
            if authority == ELASTIC:
                lines.extend(
                    [
                        "Counts are MESSAGES seen in the log estate, not control-plane "
                        "executions, and they are not comparable with the TMC rows. The "
                        "columns differ for the same reason: unfinished runs, failure "
                        "streaks and run durations are execution concepts a log estate "
                        "does not measure, so they are absent here rather than zero.",
                        "",
                    ]
                )
                lines.extend(_elastic_observation_table(records))
            else:
                lines.extend(_execution_observation_table(records))
            lines.append("")
        failure_rows = [
            [
                _cell(record.unit_name or record.job_name),
                _md(record.provenance.authority),
                _cell(mode.failed_at),
                str(mode.occurrences),
                _md(mode.error_summary),
            ]
            for record in bundle.observations
            for mode in record.recent_failure_modes
        ]
        if failure_rows:
            lines.extend(["### Recent failure modes", ""])
            lines.extend(
                _table(
                    ["Unit", "Authority", "Most recent", "Occurrences", "Headline"],
                    failure_rows,
                )
            )
            lines.append("")
        lines.append(
            "A cell reading 'unavailable' means the authority did not report that "
            "field. It is not a zero and not an absence of activity."
        )
    else:
        lines.append(
            "No authority produced an observation for this run. Volume, error and "
            "duration figures are unavailable for every unit."
        )

    lines.extend(["", "## 4. Correlation latency", ""])
    if bundle.correlation_latency:
        lines.extend(
            _table(
                [
                    "Correlation key",
                    "Source",
                    "Target",
                    "p50",
                    "p95",
                    "Samples",
                    "Environment",
                    "Authority",
                ],
                [
                    [
                        _md(record.correlation_key),
                        _cell(record.source_unit),
                        _cell(record.target_unit),
                        _ms(record.p50_ms),
                        _ms(record.p95_ms),
                        str(record.sample_size),
                        _md(record.environment or _UNREPORTED_ENVIRONMENT),
                        _md(record.provenance.authority),
                    ]
                    for record in bundle.correlation_latency
                ],
            )
        )
    else:
        lines.append(
            "Not available. End-to-end latency between two hops needs a correlation "
            "id present on both sides, which only a log estate carries. No such "
            "aggregate contributed to this run, so every path's latency is "
            "unavailable."
        )

    lines.extend(["", "## 5. Limitations", ""])
    if bundle.limitations:
        lines.extend(
            _table(
                ["Code", "Scope", "Subject", "What it means"],
                [
                    [
                        _md(item.code),
                        _md(item.scope),
                        _cell(item.unit_name or item.authority),
                        _md(item.message),
                    ]
                    for item in bundle.limitations
                ],
            )
        )
    else:
        lines.append("None recorded.")

    lines.extend(["", "## 6. Notes", ""])
    if bundle.authority_notes:
        lines.extend(f"- {_md(note)}" for note in bundle.authority_notes)
    else:
        lines.append("None recorded.")

    text = "\n".join(lines).rstrip() + "\n"
    return text.encode("ascii", errors="replace").decode("ascii")
