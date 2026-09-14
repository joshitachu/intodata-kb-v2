"""Named Elasticsearch schema presets for the ``elastic`` runtime provider.

This module is DATA, not logic. It answers one question -- "given an index, what
are its field names, its date format, and can a real run be reconstructed from
it?" -- so that adding a third tenant schema means adding a preset here, not
editing the provider.

Two presets ship:

``process_monitoring``
    The fixed Talend ESB process-monitoring schema. Field names are hardcoded
    because that schema is known and not per-tenant configurable. Its
    ``InterfaceRunId`` correlates the steps of ONE real interface run, so this
    preset is ``tier="execution"``: full run reconstruction is legitimate.

``generic``
    ECS-shaped application logs. Field names are per-tenant configurable (see
    :meth:`ElasticSchemaPreset.with_field_overrides`) because not every tenant's
    index uses ECS names. There is no correlation id, therefore
    ``tier="log"``: log rows only, and never a run.

The date format is part of the preset for a reason
--------------------------------------------------
``process_monitoring*``'s ``start``/``end`` are mapped as ``basic_date_time``
(``20260831T020004.123Z``), not the Elasticsearch default
``strict_date_optional_time``. A range query whose bounds are ISO-8601 while the
field expects ``basic_date_time`` does not error -- it silently matches nothing,
which reads downstream as "this estate has no traffic". Every preset therefore
carries the format it must send, and :func:`format_es_datetime` renders bounds
in it.

Status vocabularies are defaults, not truth
-------------------------------------------
``ProcessingStepStatus`` values are tenant-defined and were NOT confirmed
against a live estate. The maps below cover ordinary English status tokens only.
An unmapped token resolves to ``RuntimeExecutionState.UNKNOWN`` (never to
success and never to failure) and the provider warns once per distinct token,
keeping the verbatim string on the run's ``raw_state`` so a mapping gap is
visible and re-mappable rather than silently miscounted.
"""

from __future__ import annotations

import fnmatch
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.runtime.models import RuntimeExecutionState, RuntimeLogSeverity

#: Elasticsearch date-format names, spelled once so a preset cannot typo one.
BASIC_DATE_TIME = "basic_date_time"
STRICT_DATE_OPTIONAL_TIME = "strict_date_optional_time"

#: The index pattern the process-monitoring preset owns.
PROCESS_MONITORING_INDEX_PATTERN = "process_monitoring*"

#: Preset keys, and the sentinel meaning "pick a preset from the index name".
PROCESS_MONITORING_PRESET_KEY = "process_monitoring"
GENERIC_PRESET_KEY = "generic"
AUTO_PRESET_KEY = "auto"

#: ``execution`` presets can reconstruct one real run from a correlation id.
#: ``log`` presets cannot, and must never be allowed to invent one.
ElasticIngestTier = Literal["execution", "log"]

# `%z` accepts a bare "Z" from Python 3.7 on, so both spellings parse here.
_BASIC_DATE_TIME_FORMATS = (
    "%Y%m%dT%H%M%S.%f%z",
    "%Y%m%dT%H%M%S%z",
    "%Y%m%dT%H%M%S.%f",
    "%Y%m%dT%H%M%S",
)

# Epoch values at or above this magnitude are milliseconds, not seconds. Same
# threshold the TMC adapter uses, kept identical so the two providers agree.
_EPOCH_MILLIS_THRESHOLD = 100_000_000_000


class ElasticSchemaPreset(BaseModel):
    """One index schema: its field names, its date format, and its ingest tier."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    tier: ElasticIngestTier

    #: Index expressions this preset claims when the operator asked for `auto`.
    index_patterns: tuple[str, ...] = ()

    #: True when a source may rename this preset's fields. The
    #: process-monitoring schema is fixed, so it is False there: allowing an
    #: override would let a mistyped field name turn real runs into no runs.
    fields_overridable: bool = False

    #: Format sent with (and used to render) every range-query bound.
    date_format: str = STRICT_DATE_OPTIONAL_TIME

    #: The field a time window filters on. Load-bearing; never None.
    timestamp_field: str
    #: Completion timestamp, when the schema has one distinct from the start.
    end_field: str | None = None

    #: The correlation id grouping the documents of one real run. Required for
    #: an `execution` tier preset and meaningless for a `log` tier one.
    correlation_field: str | None = None
    #: Sub-run discriminator within a correlation id. Part of the group key when
    #: present: two sub-runs of one id are two runs, not one.
    correlation_sub_field: str | None = None

    step_field: str | None = None
    #: Step ordinal. Mapped as keyword/text, NOT numeric -- callers must cast.
    step_number_field: str | None = None
    #: Expected step count for the run. Used only to WITHHOLD a success claim
    #: from a run whose steps are not all present, never to assert a failure.
    step_total_field: str | None = None

    #: The token field whose value maps to an outcome and a severity: the step
    #: status for process monitoring, the log level for a generic index.
    status_field: str | None = None

    #: Fields that carry the run's job identifier, in priority order.
    #:
    #: The value is stored VERBATIM as the source logged it -- see
    #: `_register_job` in elastic.py. Declaring more than one field here is
    #: possible but risky: one integration logging `Interface` in some documents
    #: and a fallback field in others would enter the estate's job vocabulary
    #: under two names, so a chain needs a real estate to justify it.
    unit_fields: tuple[str, ...] = ()
    environment_field: str | None = None
    project_field: str | None = None
    service_field: str | None = None
    description_field: str | None = None
    error_field: str | None = None

    #: Fields carrying the human-readable log text, in display priority order.
    message_fields: tuple[str, ...] = ()

    #: Fields carrying request/response payload BODIES. Never fetched unless
    #: payload capture is explicitly enabled on the source, so an unredacted
    #: body does not even cross the network by default.
    payload_fields: tuple[str, ...] = ()

    status_states: dict[str, RuntimeExecutionState] = Field(default_factory=dict)
    status_severities: dict[str, RuntimeLogSeverity] = Field(default_factory=dict)

    def matches_index(self, index_expression: str) -> bool:
        """True when this preset claims the given index name or index pattern."""
        candidate = index_expression.strip().lower()
        return any(fnmatch.fnmatch(candidate, pattern) for pattern in self.index_patterns)

    def source_fields(self, *, include_payloads: bool) -> tuple[str, ...]:
        """Root keys to request in ``_source``.

        Roots only: Elasticsearch source filtering matches on the top-level key
        of a path, so ``log`` covers ``log.level`` while ``log.level`` alone is
        accepted but needlessly version-sensitive.
        """
        names: list[str | None] = [
            self.timestamp_field,
            self.end_field,
            self.correlation_field,
            self.correlation_sub_field,
            self.step_field,
            self.step_number_field,
            self.step_total_field,
            self.status_field,
            self.environment_field,
            self.project_field,
            self.service_field,
            self.description_field,
            self.error_field,
            *self.unit_fields,
            *self.message_fields,
        ]
        if include_payloads:
            names.extend(self.payload_fields)
        roots = {name.split(".")[0] for name in names if name}
        return tuple(sorted(roots))

    def with_field_overrides(self, overrides: dict[str, str]) -> "ElasticSchemaPreset":
        """Return a copy with per-source field names applied.

        Only ``fields_overridable`` presets accept them; on a fixed schema the
        overrides are ignored rather than silently repointing known-good fields
        at names that do not exist. ``message`` accepts a comma-separated list,
        matching the reference deployments' ``ES_FIELD_MESSAGE``.
        """
        if not self.fields_overridable:
            return self
        update: dict[str, Any] = {}
        for key, attribute in (
            ("timestamp", "timestamp_field"),
            ("level", "status_field"),
            ("service", "service_field"),
            ("error", "error_field"),
        ):
            if key not in overrides:
                continue
            value = overrides[key].strip()
            if attribute == "timestamp_field" and not value:
                # A window has to filter on something; an empty timestamp field
                # would produce an unbounded query, which is the one thing this
                # provider must never emit.
                continue
            update[attribute] = value or None
        if "message" in overrides:
            fields = tuple(
                part.strip() for part in overrides["message"].split(",") if part.strip()
            )
            if fields:
                update["message_fields"] = fields
        return self.model_copy(update=update) if update else self


PROCESS_MONITORING_PRESET = ElasticSchemaPreset(
    key=PROCESS_MONITORING_PRESET_KEY,
    label="Talend ESB process monitoring",
    tier="execution",
    index_patterns=(PROCESS_MONITORING_INDEX_PATTERN,),
    fields_overridable=False,
    # NOT strict_date_optional_time. See the module docstring.
    date_format=BASIC_DATE_TIME,
    timestamp_field="start",
    end_field="end",
    correlation_field="InterfaceRunId",
    correlation_sub_field="InterfaceRunId_SubNr",
    step_field="ProcessingStep",
    step_number_field="ProcessingStepNr",
    step_total_field="ProcessingStepTotal",
    status_field="ProcessingStepStatus",
    # `Interface` is the integration's name and the natural runtime job. NO
    # fallback field is configured on purpose: `MonitoringSessionJobName` is a
    # different identifier (the Talend job that carried the interface, not the
    # interface), so using it when `Interface` is absent would file some runs of
    # one integration under a second name. A document with no `Interface` is
    # reported as an explicit attribution gap instead.
    unit_fields=("Interface",),
    environment_field="Environment",
    project_field="MonitoringSessionProjectName",
    service_field="Producer",
    description_field="Description",
    error_field="Error",
    message_fields=("Description",),
    payload_fields=("MessageIn", "MessageOut"),
    status_states={
        "success": RuntimeExecutionState.SUCCESS,
        "successful": RuntimeExecutionState.SUCCESS,
        "succeeded": RuntimeExecutionState.SUCCESS,
        "ok": RuntimeExecutionState.SUCCESS,
        "completed": RuntimeExecutionState.SUCCESS,
        "complete": RuntimeExecutionState.SUCCESS,
        "finished": RuntimeExecutionState.SUCCESS,
        "done": RuntimeExecutionState.SUCCESS,
        "failed": RuntimeExecutionState.FAILED,
        "failure": RuntimeExecutionState.FAILED,
        "error": RuntimeExecutionState.FAILED,
        "fault": RuntimeExecutionState.FAILED,
        "aborted": RuntimeExecutionState.TERMINATED,
        "cancelled": RuntimeExecutionState.TERMINATED,
        "canceled": RuntimeExecutionState.TERMINATED,
        "timeout": RuntimeExecutionState.TIMED_OUT,
        "timedout": RuntimeExecutionState.TIMED_OUT,
        "running": RuntimeExecutionState.RUNNING,
        "started": RuntimeExecutionState.RUNNING,
        "start": RuntimeExecutionState.RUNNING,
        "in_progress": RuntimeExecutionState.RUNNING,
        "processing": RuntimeExecutionState.RUNNING,
    },
    status_severities={
        "failed": RuntimeLogSeverity.ERROR,
        "failure": RuntimeLogSeverity.ERROR,
        "error": RuntimeLogSeverity.ERROR,
        "fault": RuntimeLogSeverity.ERROR,
        "aborted": RuntimeLogSeverity.ERROR,
        "cancelled": RuntimeLogSeverity.WARNING,
        "canceled": RuntimeLogSeverity.WARNING,
        "timeout": RuntimeLogSeverity.ERROR,
        "timedout": RuntimeLogSeverity.ERROR,
        "warn": RuntimeLogSeverity.WARNING,
        "warning": RuntimeLogSeverity.WARNING,
        "retry": RuntimeLogSeverity.WARNING,
        "skipped": RuntimeLogSeverity.WARNING,
    },
)


GENERIC_PRESET = ElasticSchemaPreset(
    key=GENERIC_PRESET_KEY,
    label="Generic ECS-style logs",
    tier="log",
    # Deliberately empty: `generic` is what an index falls back to, never what
    # it is claimed by, so a new PM-like schema cannot be swallowed by it.
    index_patterns=(),
    fields_overridable=True,
    date_format=STRICT_DATE_OPTIONAL_TIME,
    timestamp_field="@timestamp",
    status_field="log.level",
    service_field="service.name",
    error_field="error.stack_trace",
    message_fields=("message",),
    # No correlation field, no steps, no unit: a generic index cannot name the
    # thing that ran, so this preset never produces a run. Removing that
    # constraint is how a message count becomes a fabricated failure rate.
    status_states={},
    status_severities={
        "trace": RuntimeLogSeverity.DEBUG,
        "debug": RuntimeLogSeverity.DEBUG,
        "info": RuntimeLogSeverity.INFO,
        "information": RuntimeLogSeverity.INFO,
        "notice": RuntimeLogSeverity.INFO,
        "warn": RuntimeLogSeverity.WARNING,
        "warning": RuntimeLogSeverity.WARNING,
        "error": RuntimeLogSeverity.ERROR,
        "err": RuntimeLogSeverity.ERROR,
        "fatal": RuntimeLogSeverity.ERROR,
        "critical": RuntimeLogSeverity.ERROR,
        "crit": RuntimeLogSeverity.ERROR,
        "severe": RuntimeLogSeverity.ERROR,
        "alert": RuntimeLogSeverity.ERROR,
        "emergency": RuntimeLogSeverity.ERROR,
    },
)


_PRESETS: dict[str, ElasticSchemaPreset] = {
    preset.key: preset for preset in (PROCESS_MONITORING_PRESET, GENERIC_PRESET)
}

#: Values an operator may put in the source's `schema_preset` field.
SELECTABLE_PRESET_KEYS: tuple[str, ...] = (AUTO_PRESET_KEY, *sorted(_PRESETS))


class UnknownElasticPresetError(ValueError):
    """Raised when a source names a schema preset that is not registered."""


def get_preset(key: str) -> ElasticSchemaPreset:
    preset = _PRESETS.get(key.strip().lower())
    if preset is None:
        raise UnknownElasticPresetError(
            f"Unknown Elasticsearch schema preset '{key}'. "
            f"Choose one of: {', '.join(SELECTABLE_PRESET_KEYS)}"
        )
    return preset


def resolve_preset_for_index(
    index_expression: str, *, override: str | None = None
) -> ElasticSchemaPreset:
    """Pick the preset for one index expression.

    An explicit override wins, so an operator whose PM data lives under a
    renamed index can still get run reconstruction. Otherwise the index name
    decides, and anything unclaimed falls back to the log-only generic preset --
    the safe direction, because a wrongly-claimed generic index would fabricate
    runs while a wrongly-generic PM index only under-reports.
    """
    if override and override.strip().lower() != AUTO_PRESET_KEY:
        return get_preset(override)
    for preset in _PRESETS.values():
        if preset.matches_index(index_expression):
            return preset
    return GENERIC_PRESET


def format_es_datetime(value: datetime, date_format: str) -> str:
    """Render a range-query bound in the format the target field expects."""
    aware = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if date_format == BASIC_DATE_TIME:
        return f"{aware.strftime('%Y%m%dT%H%M%S')}.{aware.microsecond // 1000:03d}Z"
    return aware.isoformat().replace("+00:00", "Z")


def parse_es_datetime(value: Any, date_format: str) -> datetime | None:
    """Parse a stored timestamp, tolerating every shape a dynamic mapping allows.

    Returns ``None`` rather than raising: ``process_monitoring*`` has
    ``dynamic: true`` and ``date_detection: true``, so one malformed document
    must not abort a whole sync cycle. Callers count and report the misses.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if abs(value) >= _EPOCH_MILLIS_THRESHOLD else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, list):
        # A keyword/date field under dynamic mapping can hold an array.
        for item in value:
            parsed = parse_es_datetime(item, date_format)
            if parsed is not None:
                return parsed
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    candidates = (
        _BASIC_DATE_TIME_FORMATS if date_format == BASIC_DATE_TIME else ()
    )
    for pattern in candidates:
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # A basic_date_time value can appear on a generic index too (and the
        # reverse), so try the other family before giving up.
        for pattern in _BASIC_DATE_TIME_FORMATS:
            try:
                parsed = datetime.strptime(text, pattern)
            except ValueError:
                continue
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
