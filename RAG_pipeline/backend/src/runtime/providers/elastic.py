"""Elasticsearch runtime source provider.

Registers an Elasticsearch cluster as a pollable runtime source, the same way
``tmc`` registers a Talend Management Console tenant. It speaks plain REST over
``httpx`` rather than using the ``elasticsearch`` Python client:

- that client pins to a cluster major version (a 7.x client is not a supported
  way to talk to an 8.x cluster, and vice versa), which is the entire reason the
  reference MCP servers exist as a 7.x/8.x fork pair;
- the three endpoints this provider needs -- ``GET /``,
  ``POST /{index}/_search`` and ``GET /{index}/_stats/docs`` -- are
  wire-compatible across 7.x, 8.x and 9.x.

The cluster version is READ from ``GET /`` and reported, but never used to fail
closed. Where an endpoint is genuinely not universal
(``GET /_resolve/index/*`` needs 7.9+) the provider degrades to an older
equivalent instead of refusing.

Two ingest tiers, and the split is the point
--------------------------------------------
``process_monitoring*`` (the ``process_monitoring`` schema preset) carries
``InterfaceRunId``, a correlation id that groups the steps of ONE REAL interface
run. That, and only that, licenses full run reconstruction: the provider emits
``RuntimeJob`` / ``RuntimeJobRun`` / ``RuntimeExecution`` records plus
execution-scoped, attribution-safe ``RuntimeLogEvent`` rows keyed on the
correlation id.

Every other index is log-only. No runs, no jobs, no executions: log rows with
``scope="engine"`` and ``attribution_safe=False``, whose ``execution_id`` is an
openly synthetic time bucket that no execution row will ever match. A generic
log index cannot name the thing that ran, so presenting a grouping key as a run
would be a fabrication.

Message counts are never execution counts
-----------------------------------------
No run, and no field of any run, is derived from a number of log documents. A
run's outcome comes from the provider's own step statuses; an unmapped status
becomes ``UNKNOWN`` (never success, never failure) with the verbatim string kept
on ``raw_state``. Counting messages as executions is what previously made an
estate report a 23.1% failure rate describing no environment that existed.

Bounded ingest is a requirement, not an optimisation
----------------------------------------------------
``runtime_log_events`` is already ~868 MB / 424k rows with a 90-day prune and no
other retention policy, and an ESB estate is far larger -- one real tenant holds
eight months of traffic. So: the time window is mandatory and capped, the row
count per cycle is capped, every query is paged deterministically inside
hour-aligned slices, and re-ingesting an overlapping window is idempotent by
construction rather than by luck (see ``_OrdinalAllocator``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import ssl
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urlsplit

import httpx

from src.runtime.models import (
    RuntimeEnvironment,
    RuntimeExecutableKind,
    RuntimeExecution,
    RuntimeExecutionState,
    RuntimeFailure,
    RuntimeJob,
    RuntimeJobRun,
    RuntimeLogEvent,
    RuntimeLogSeverity,
    RuntimeScopeOption,
    RuntimeScopeSelection,
    RuntimeWorkspace,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.providers.base import CredentialField, compose_tenant_identity
from src.runtime.providers.elastic_schemas import (
    AUTO_PRESET_KEY,
    PROCESS_MONITORING_PRESET,
    SELECTABLE_PRESET_KEYS,
    ElasticSchemaPreset,
    UnknownElasticPresetError,
    format_es_datetime,
    parse_es_datetime,
    resolve_preset_for_index,
)
from src.runtime.redaction import redact_credentials, redact_entities, sanitize_sync_error
from src.runtime.sync import RuntimeSyncBatch

logger = logging.getLogger(__name__)

PROVIDER_KEY = "elastic"

# --- bounded-ingest defaults -------------------------------------------------
# Conservative on purpose: an operator who accepts every default must not be
# able to start a multi-million-row import by clicking Save.
_DEFAULT_WINDOW_DAYS = 7
_MIN_WINDOW_DAYS = 1
_MAX_WINDOW_DAYS = 90
_DEFAULT_MAX_DOCUMENTS = 20_000
_MIN_MAX_DOCUMENTS = 100
_MAX_MAX_DOCUMENTS = 200_000
_DEFAULT_PAYLOAD_MAX_CHARS = 2_000
_MAX_PAYLOAD_MAX_CHARS = 20_000

# Documents per `_search` request. Well under the 10,000 `index.max_result_window`
# default so a page never trips it on its own.
_PAGE_SIZE = 1_000
# Elasticsearch refuses `from + size` beyond this without a scroll/PIT, on every
# supported major version. Slices are subdivided rather than paged past it.
_MAX_RESULT_WINDOW = 10_000

# Windows are sliced into whole hours, and the newest partial hour is excluded.
# Both properties exist for idempotency: a bucket that is always fetched WHOLE
# assigns the same ordinals every cycle (see `_OrdinalAllocator`).
_SLICE_HOURS = 1
_SUBSLICE_MINUTES = 10

_MAX_MESSAGE_CHARS = 4_000
_MAX_ERROR_DETAIL_CHARS = 4_000
_MAX_IDENTIFIER_CHARS = 240

_REQUEST_TIMEOUT_SECONDS = 30.0
_VALIDATE_TIMEOUT_SECONDS = 10.0
_TRANSPORT_MAX_ATTEMPTS = 3
_TRANSPORT_RETRY_DELAY_SECONDS = 0.25

_SCOPE_DISCOVERY_LIMIT = 500

# The `execution_id` prefix for log-only rows. Deliberately unlike any real run
# id: it names the mechanism, so a synthetic bucket can never be mistaken for an
# execution in a query, a log line, or a screenshot.
_SYNTHETIC_EXECUTION_PREFIX = "elastic-log"

# PM step ordinals come from `ProcessingStepNr`. Steps whose ordinal is missing
# or unparseable are placed after every numbered step rather than interleaved.
_FALLBACK_ORDINAL_BASE = 1_000_000
_MAX_STEP_ORDINAL = _FALLBACK_ORDINAL_BASE - 1

# Index expressions are interpolated into the request PATH, so the accepted
# charset is a conservative allow-list rather than an escape pass. Multi-index
# commas, exclusions (`-foo`), date-math (`<foo-{now/d}>`) and traversal are all
# rejected: each selected expression must stand alone.
_INDEX_EXPRESSION_RE = re.compile(r"^[a-z0-9][a-z0-9_.*+-]*$")
_MIN_INDEX_LITERAL_CHARS = 3

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})

# Outcomes that owe an operator an explanation and therefore get a failure row.
_DIAGNOSABLE_STATES: frozenset[RuntimeExecutionState] = frozenset(
    {
        RuntimeExecutionState.FAILED,
        RuntimeExecutionState.TIMED_OUT,
        RuntimeExecutionState.TERMINATED,
    }
)

# Failure-family states, worst first. A run reports its worst step outcome.
_FAILURE_PRECEDENCE: tuple[RuntimeExecutionState, ...] = (
    RuntimeExecutionState.FAILED,
    RuntimeExecutionState.TIMED_OUT,
    RuntimeExecutionState.TERMINATED,
)

# One warning per distinct unmapped status token per process. A sync walks tens
# of thousands of documents and a novel status is systemic, not incidental: an
# un-deduplicated warning emits thousands of identical lines and gets muted,
# which is exactly how a silent UNKNOWN mapping survives unnoticed.
_UNKNOWN_STATUS_WARN_LIMIT = 50
_unknown_statuses_warned: set[str] = set()


# --- configuration -----------------------------------------------------------


@dataclass(frozen=True)
class ElasticSourceConfig:
    """One source's parsed, validated, bounded configuration."""

    endpoint_url: str
    auth_header: str | None
    verify_tls: bool
    ca_cert_pem: str | None
    index_names: tuple[str, ...]
    preset_override: str | None
    field_overrides: dict[str, str]
    window_days: int
    max_documents: int
    capture_payloads: bool
    payload_max_chars: int


@dataclass
class _FetchStats:
    """What a cycle actually managed to read, so it can be reported honestly."""

    documents: int = 0
    budget_exhausted: bool = False
    overflowed_slices: int = 0
    unparseable_timestamps: int = 0
    failed_index_groups: list[str] = field(default_factory=list)
    succeeded_index_groups: int = 0


@dataclass(frozen=True)
class _ElasticDocument:
    """One search hit, reduced to the three things the mapper needs."""

    index: str
    doc_id: str
    source: dict[str, Any]


class _OrdinalAllocator:
    """Assigns ``line_ordinal`` values that survive an overlapping re-ingest.

    ``runtime_log_events`` is unique on
    ``(project_id, execution_id, line_ordinal, event_fingerprint)`` and the
    fingerprint is ``timestamp|severity|line_ordinal|message``. So an ordinal
    that depends on a document's POSITION in a fetch would shift whenever a
    window boundary or a page boundary moved, and every shift writes a second
    copy of a line already stored -- on a 30-minute cadence, forever.

    The ordinal here is therefore intrinsic to content: it counts how many
    documents with the SAME ``(execution_id, timestamp, severity, message)``
    have already been seen in this cycle. Byte-identical repeated lines are kept
    (they are valid log data), the value is invariant to fetch order, and a
    partially fetched bucket produces a PREFIX of the same ordinals rather than
    a different set.
    """

    def __init__(self) -> None:
        self._seen: Counter[tuple[str, str, str, str]] = Counter()

    def next_ordinal(
        self, execution_id: str, timestamp: datetime, severity: RuntimeLogSeverity, message: str
    ) -> int:
        key = (execution_id, timestamp.isoformat(), severity.value, message)
        ordinal = self._seen[key]
        self._seen[key] += 1
        return ordinal


def _flag(credentials: Mapping[str, Any], name: str, *, default: bool) -> bool:
    raw = credentials.get(name)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise RuntimeProviderError(f"'{name}' must be true or false")


def _bounded_int(
    credentials: Mapping[str, Any], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = credentials.get(name)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        raise RuntimeProviderError(f"'{name}' must be a whole number from {minimum} to {maximum}")
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise RuntimeProviderError(
            f"'{name}' must be a whole number from {minimum} to {maximum}"
        ) from exc
    if not minimum <= value <= maximum:
        raise RuntimeProviderError(f"'{name}' must be from {minimum} to {maximum}")
    return value


def _endpoint_url(credentials: Mapping[str, Any]) -> str:
    raw = str(credentials.get("endpoint_url") or "").strip().rstrip("/")
    if not raw:
        raise RuntimeProviderError("Missing the Elasticsearch endpoint URL")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"}:
        raise RuntimeProviderError("The Elasticsearch endpoint URL must start with http:// or https://")
    if not parts.hostname:
        raise RuntimeProviderError("The Elasticsearch endpoint URL has no host")
    if parts.username or parts.password:
        # Credentials in the URL would end up inside the stored physical
        # identity, which is required to be secret-free.
        raise RuntimeProviderError(
            "Put the username and password in their own fields, not in the endpoint URL"
        )
    if parts.query or parts.fragment:
        raise RuntimeProviderError("The Elasticsearch endpoint URL must not carry a query or fragment")
    return raw


def _auth_header(credentials: Mapping[str, Any]) -> str | None:
    """Build the Authorization header, preferring an API key over basic auth.

    Returns None for a cluster with security disabled (a local 7.x dev cluster,
    typically). That is allowed but never assumed: the caller still has to reach
    the cluster for validation to pass.
    """
    api_key = str(credentials.get("api_key") or "").strip()
    if api_key:
        return f"ApiKey {api_key}"
    username = str(credentials.get("username") or "").strip()
    password = str(credentials.get("password") or "")
    if username and password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"
    if username or password:
        raise RuntimeProviderError("Basic auth needs both a username and a password")
    return None


def _index_names(credentials: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate and de-duplicate the selected index expressions.

    Empty is tolerated here because the connection test runs BEFORE an operator
    can pick anything; the source-management route enforces a non-empty ingest
    scope at save time, and `sync` refuses to run without one.
    """
    configured = credentials.get("index_names")
    if configured is None:
        return ()
    if isinstance(configured, str):
        parts = [part.strip() for part in configured.split(",")]
    elif isinstance(configured, (list, tuple)):
        parts = [str(part).strip() for part in configured]
    else:
        raise RuntimeProviderError("The index selection must be a list of index names")
    names: list[str] = []
    for part in parts:
        if not part:
            continue
        candidate = part.lower()
        if candidate in {"*", "_all", "-*"} or not _INDEX_EXPRESSION_RE.match(candidate):
            raise RuntimeProviderError(
                f"'{part}' is not a usable index selection. Use one lowercase index name or "
                "prefix pattern per entry, for example 'process_monitoring*'."
            )
        if len(candidate.replace("*", "")) < _MIN_INDEX_LITERAL_CHARS:
            # `a*` would silently widen the source to most of the cluster.
            raise RuntimeProviderError(
                f"'{part}' is too broad. An index pattern needs at least "
                f"{_MIN_INDEX_LITERAL_CHARS} literal characters."
            )
        names.append(candidate)
    return tuple(dict.fromkeys(names))


def _field_overrides(credentials: Mapping[str, Any]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for key, credential_name in (
        ("timestamp", "field_timestamp"),
        ("level", "field_level"),
        ("service", "field_service"),
        ("message", "field_message"),
        ("error", "field_error"),
    ):
        if credential_name in credentials and credentials[credential_name] is not None:
            overrides[key] = str(credentials[credential_name])
    return overrides


def _preset_override(credentials: Mapping[str, Any]) -> str | None:
    raw = str(credentials.get("schema_preset") or "").strip().lower()
    if not raw or raw == AUTO_PRESET_KEY:
        return None
    if raw not in SELECTABLE_PRESET_KEYS:
        raise RuntimeProviderError(
            f"Unknown schema preset '{raw}'. Choose one of: {', '.join(SELECTABLE_PRESET_KEYS)}"
        )
    return raw


def parse_elastic_config(credentials: Mapping[str, Any]) -> ElasticSourceConfig:
    """Parse and bound one source's configuration, or raise a clear error."""
    return ElasticSourceConfig(
        endpoint_url=_endpoint_url(credentials),
        auth_header=_auth_header(credentials),
        verify_tls=_flag(credentials, "verify_tls", default=True),
        ca_cert_pem=(str(credentials.get("ca_cert_pem") or "").strip() or None),
        index_names=_index_names(credentials),
        preset_override=_preset_override(credentials),
        field_overrides=_field_overrides(credentials),
        window_days=_bounded_int(
            credentials,
            "window_days",
            default=_DEFAULT_WINDOW_DAYS,
            minimum=_MIN_WINDOW_DAYS,
            maximum=_MAX_WINDOW_DAYS,
        ),
        max_documents=_bounded_int(
            credentials,
            "max_documents_per_sync",
            default=_DEFAULT_MAX_DOCUMENTS,
            minimum=_MIN_MAX_DOCUMENTS,
            maximum=_MAX_MAX_DOCUMENTS,
        ),
        capture_payloads=_flag(credentials, "capture_payloads", default=False),
        payload_max_chars=_bounded_int(
            credentials,
            "payload_max_chars",
            default=_DEFAULT_PAYLOAD_MAX_CHARS,
            minimum=0,
            maximum=_MAX_PAYLOAD_MAX_CHARS,
        ),
    )


# --- provider ----------------------------------------------------------------


class ElasticRuntimeProvider:
    """REST adapter to an Elasticsearch cluster, version-agnostic by design."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._config: ElasticSourceConfig | None = None
        # One client (and one TLS context) per sync. Building an AsyncClient per
        # request is what drove the TMC adapter's RSS into its container limit.
        self._http_client: httpx.AsyncClient | None = None
        self._cluster: dict[str, Any] = {}

    # -- identity and metadata ------------------------------------------------

    def provider_key(self) -> str:
        return PROVIDER_KEY

    def provider_label(self) -> str:
        return "Elasticsearch (logs and process monitoring)"

    def credential_schema(self) -> list[CredentialField]:
        """Fields the connect dialog renders.

        Deliberately absent:

        - ``index_names`` -- that is the ingest scope, declared by
          `scope_selection` and populated by `discover_scopes`, so the operator
          picks from what the credentials can actually reach rather than typing
          index names blind.
        - ``doc_environment_ids`` / ``doc_window_days`` -- the documentation
          READ scope, which `editable_config_fields` appends for every provider.
          Duplicating them here would ask an operator to decide what documents
          may read while they are holding an API key.
        """
        return [
            {"name": "endpoint_url", "type": "url", "label": "Elasticsearch URL", "required": True},
            {
                "name": "api_key",
                "type": "secret",
                "label": "API key (or use username and password)",
                "required": False,
            },
            {"name": "username", "type": "text", "label": "Username", "required": False},
            {"name": "password", "type": "secret", "label": "Password", "required": False},
            {
                "name": "ca_cert_pem",
                "type": "text",
                "label": "CA certificate (PEM) for a self-signed cluster",
                "required": False,
            },
            {
                "name": "verify_tls",
                "type": "text",
                "label": "Verify the TLS certificate (true/false)",
                "required": False,
            },
            {
                "name": "window_days",
                "type": "text",
                "label": f"Ingest window in days ({_MIN_WINDOW_DAYS}-{_MAX_WINDOW_DAYS})",
                "required": False,
            },
            {
                "name": "max_documents_per_sync",
                "type": "text",
                "label": (
                    f"Maximum documents per sync cycle "
                    f"({_MIN_MAX_DOCUMENTS}-{_MAX_MAX_DOCUMENTS})"
                ),
                "required": False,
            },
            {
                "name": "capture_payloads",
                "type": "text",
                "label": "Capture MessageIn/MessageOut payload bodies (true/false)",
                "required": False,
            },
            {
                "name": "payload_max_chars",
                "type": "text",
                "label": f"Characters kept per captured payload (0-{_MAX_PAYLOAD_MAX_CHARS})",
                "required": False,
            },
            {
                "name": "schema_preset",
                "type": "text",
                "label": f"Schema preset ({', '.join(SELECTABLE_PRESET_KEYS)})",
                "required": False,
            },
            {
                "name": "field_timestamp",
                "type": "text",
                "label": "Generic indices: timestamp field",
                "required": False,
            },
            {
                "name": "field_level",
                "type": "text",
                "label": "Generic indices: log level field",
                "required": False,
            },
            {
                "name": "field_service",
                "type": "text",
                "label": "Generic indices: service name field",
                "required": False,
            },
            {
                "name": "field_message",
                "type": "text",
                "label": "Generic indices: message field(s), comma-separated",
                "required": False,
            },
            {
                "name": "field_error",
                "type": "text",
                "label": "Generic indices: error/stack field",
                "required": False,
            },
        ]

    def container_key_field(self) -> str:
        """The INDEX is this provider's container.

        `resolve_project_id` needs a key that exists on every container-bearing
        record, which leaves `workspace_id` or `environment_id`. The index is the
        only partition an Elasticsearch source actually has across BOTH tiers
        (`Environment` is a process-monitoring field and absent from a generic
        index), so index names are carried in `workspace_id` and a
        `RuntimeWorkspace` row is emitted per ingested index for the workspace
        project map to bind to.
        """
        return "workspace_id"

    def static_ref_strategy(self) -> None:
        return None

    def scope_selection(self) -> RuntimeScopeSelection:
        return RuntimeScopeSelection(
            field="index_names",
            label="Indices",
            description=(
                "Only the selected indices and index patterns are polled. "
                "process_monitoring* is reconstructed into real interface runs; "
                "everything else is imported as logs only."
            ),
            required=True,
            multiple=True,
        )

    def identity_credential_fields(self) -> set[str]:
        """Only the endpoint is frozen.

        Changing it would repoint the source at a DIFFERENT cluster while
        keeping the previous cluster's ingested history, which is exactly what
        this freeze exists to prevent.

        The index allowlist is not frozen even though it takes part in the
        identity: it is the declared ingest scope, which this framework treats
        as operator-editable by construction (`editable_config_fields` adds
        `scope_selection().field` for every provider), and
        `physical_identity_from_batch` re-derives the stored identity after an
        edit so the duplicate guard keeps working. Same arrangement as `tmc`,
        whose environment ids are both identity markers and editable scope.
        """
        return {"endpoint_url"}

    def physical_source_identity(self, credentials: Mapping[str, Any]) -> str:
        """The endpoint identity: stable, secret-free, derived offline.

        One Elasticsearch URL is one cluster, so unlike Talend Cloud this is
        already customer-specific rather than a shared regional prefix.
        """
        return f"{PROVIDER_KEY}:{_endpoint_url(credentials).lower()}"

    async def resolve_physical_identity(self, credentials: Mapping[str, Any]) -> str:
        """Narrow the cluster identity by the index allowlist.

        `SingleTenantIdentityMixin` was considered and rejected. It fits the
        first half -- an Elasticsearch endpoint serves exactly one cluster, so
        no live call is needed to tell two customers apart -- but it also
        asserts that nothing about the source can change the identity, and here
        one thing can: the index allowlist. A cluster legitimately backs more
        than one Nexus source (a process-monitoring source and an application-log
        source are different data with different attribution rules), and an
        endpoint-only identity would reject the second registration as a
        duplicate of the first.

        `compose_tenant_identity` does the narrowing so the stored value keeps
        the same shape as every other provider's and stays free of anything
        secret. No network call is made, so this is cheap and offline despite
        being the async half of the contract.
        """
        return self._composed_identity(credentials)

    def physical_identity_from_batch(
        self, credentials: Mapping[str, Any], batch: RuntimeSyncBatch
    ) -> str | None:
        """Keep the stored identity aligned with the CURRENT index allowlist.

        Derived from the credentials rather than the batch: an index in the
        allowlist that happens to hold nothing this cycle is still part of what
        this source is, and letting an empty index silently drop out of the
        identity would let a re-registration of the same scope slip past the
        duplicate guard.
        """
        if not _index_names(credentials):
            return None
        return self._composed_identity(credentials)

    def _composed_identity(self, credentials: Mapping[str, Any]) -> str:
        endpoint_identity = self.physical_source_identity(credentials)
        index_names = _index_names(credentials)
        if not index_names:
            return endpoint_identity
        return compose_tenant_identity(endpoint_identity, index_names)

    def source_type_aliases(self) -> set[str]:
        return {self.provider_key()}

    # -- connection test and scope discovery ----------------------------------

    async def validate_credentials(self, credentials: Mapping[str, Any]) -> tuple[bool, str | None]:
        try:
            config = parse_elastic_config(credentials)
        except (RuntimeProviderError, UnknownElasticPresetError) as error:
            return False, str(error)
        probe = ElasticRuntimeProvider(transport=self._transport)
        probe._config = config
        try:
            await probe._cluster_info(timeout=_VALIDATE_TIMEOUT_SECONDS)
        except RuntimeProviderError as error:
            return False, _sanitize_provider_error(str(error))
        finally:
            await probe.aclose()
        return True, None

    async def discover_scopes(self, credentials: Mapping[str, Any]) -> list[RuntimeScopeOption]:
        """List the indices these credentials can reach, with document counts.

        Reaching the cluster is what proves the credentials work, so a
        successful discovery is also a successful connection test.

        Indices matching a registered preset pattern are collapsed into the
        PATTERN (``process_monitoring*``, not 240 dated members): a rolling
        index set is what an operator means to select, and offering the members
        individually guarantees the selection goes stale.
        """
        config = parse_elastic_config(credentials)
        probe = ElasticRuntimeProvider(transport=self._transport)
        probe._config = config
        try:
            await probe._cluster_info()
            names = await probe._resolve_index_names()
            counts = await probe._document_counts()
        finally:
            await probe.aclose()

        options: list[RuntimeScopeOption] = []
        claimed: set[str] = set()
        for pattern, preset in _preset_patterns():
            members = [name for name in names if preset.matches_index(name)]
            if not members:
                continue
            claimed.update(members)
            options.append(
                RuntimeScopeOption(
                    id=pattern,
                    label=pattern,
                    detail=_scope_detail(len(members), _sum_counts(members, counts), preset.label),
                    # A pattern that matched something is almost always what the
                    # operator came for, so it starts selected.
                    default_selected=True,
                )
            )
        for name in sorted(names):
            if name in claimed:
                continue
            if len(options) >= _SCOPE_DISCOVERY_LIMIT:
                logger.warning(
                    "Elasticsearch scope discovery truncated at %d indices; narrow the "
                    "cluster's index set or select a pattern instead",
                    _SCOPE_DISCOVERY_LIMIT,
                )
                break
            count = counts.get(name)
            options.append(
                RuntimeScopeOption(
                    id=name,
                    label=name,
                    detail=None if count is None else f"{count} documents",
                    default_selected=False,
                )
            )
        return options

    # -- sync -----------------------------------------------------------------

    async def sync(self, *, credentials: Mapping[str, Any], project_id: str) -> RuntimeSyncBatch:
        config = parse_elastic_config(credentials)
        if not config.index_names:
            raise RuntimeProviderError(
                "This Elasticsearch source has no indices selected, so there is nothing to poll. "
                "Edit its ingest scope and select at least one index."
            )
        provider = ElasticRuntimeProvider(transport=self._transport)
        provider._config = config
        try:
            # `since` is the incremental-sync hook. The connector contract hands
            # a provider only credentials and a project id -- no cursor and no
            # way to persist one -- so the scheduler cannot supply a watermark
            # yet and every cycle re-reads the bounded rolling window, which the
            # ordinal scheme makes idempotent. Wiring it up needs
            # `run_runtime_source_sync_cycle` to pass the source's stored
            # `last_synced_at` through `provider.sync(...)`.
            return await provider.fetch_runtime_batch(project_id=project_id, since=None)
        finally:
            await provider.aclose()

    async def fetch_runtime_batch(
        self, *, project_id: str, since: datetime | None = None
    ) -> RuntimeSyncBatch:
        config = self._require_config()
        synced_at = datetime.now(UTC)
        source_key = self.provider_key()
        self._cluster = await self._cluster_info()

        window_start, window_end = _ingest_window(
            now=synced_at, window_days=config.window_days, since=since
        )
        if window_start >= window_end:
            # A watermark newer than the newest whole hour: nothing to do, and
            # saying so beats emitting an empty batch that looks like data loss.
            logger.info("Elasticsearch ingest window is empty; nothing new since the watermark")
            return RuntimeSyncBatch(synced_at=synced_at)

        stats = _FetchStats()
        documents_by_preset: list[tuple[ElasticSchemaPreset, list[_ElasticDocument]]] = []
        for preset, indices in self._index_groups():
            remaining = config.max_documents - stats.documents
            if remaining <= 0:
                stats.budget_exhausted = True
                break
            try:
                documents = await self._fetch_documents(
                    indices=indices,
                    preset=preset,
                    start=window_start,
                    end=window_end,
                    budget=remaining,
                    stats=stats,
                )
            except RuntimeProviderError as error:
                # One unreadable index group must not discard the groups that
                # worked; reporting PARTIAL is what tells an operator to look.
                logger.warning(
                    "Elasticsearch index group %s could not be read: %s",
                    ",".join(indices),
                    _sanitize_provider_error(str(error)),
                )
                stats.failed_index_groups.append(",".join(indices))
                continue
            stats.succeeded_index_groups += 1
            documents_by_preset.append((preset, documents))

        if stats.failed_index_groups and not stats.succeeded_index_groups:
            raise RuntimeProviderError(
                f"None of this source's {len(stats.failed_index_groups)} index selections "
                "could be read from Elasticsearch"
            )

        builder = _BatchBuilder(
            source_key=source_key,
            project_id=project_id,
            synced_at=synced_at,
            config=config,
        )
        for preset, documents in documents_by_preset:
            if preset.tier == "execution":
                builder.add_execution_tier(preset, documents)
            else:
                builder.add_log_tier(preset, documents)
        stats.unparseable_timestamps += builder.unparseable_timestamps

        _log_bounding(stats, config)
        builder.log_attribution_gaps()
        return builder.build(
            partial=bool(stats.failed_index_groups),
            failure_reason_code="index_group_unreadable" if stats.failed_index_groups else None,
            error_summary=(
                f"{len(stats.failed_index_groups)} of "
                f"{len(stats.failed_index_groups) + stats.succeeded_index_groups} "
                "index selections could not be read"
                if stats.failed_index_groups
                else None
            ),
        )

    def _index_groups(self) -> list[tuple[ElasticSchemaPreset, tuple[str, ...]]]:
        """Group the allowlist by preset so one request covers several indices.

        Each hit carries its own ``_index``, so grouping loses nothing: the
        container key is still per-document.
        """
        config = self._require_config()
        grouped: dict[str, tuple[ElasticSchemaPreset, list[str]]] = {}
        for name in config.index_names:
            preset = resolve_preset_for_index(name, override=config.preset_override)
            preset = preset.with_field_overrides(config.field_overrides)
            entry = grouped.setdefault(preset.key, (preset, []))
            entry[1].append(name)
        return [(preset, tuple(names)) for preset, names in grouped.values()]

    async def _fetch_documents(
        self,
        *,
        indices: Sequence[str],
        preset: ElasticSchemaPreset,
        start: datetime,
        end: datetime,
        budget: int,
        stats: _FetchStats,
    ) -> list[_ElasticDocument]:
        """Read one preset group's documents, newest slice first, within budget.

        Newest-first because a capped cycle should keep the most recent evidence,
        which is what an incident question asks about.
        """
        include_payloads = self._require_config().capture_payloads
        index_path = _index_path(indices)
        collected: list[_ElasticDocument] = []
        seen: set[tuple[str, str]] = set()
        pending: list[tuple[datetime, datetime]] = _time_slices(start, end)
        while pending:
            if len(collected) >= budget:
                stats.budget_exhausted = True
                break
            slice_start, slice_end = pending.pop(0)
            page, total, capped_total = await self._search(
                index_path=index_path,
                preset=preset,
                start=slice_start,
                end=slice_end,
                offset=0,
                size=min(_PAGE_SIZE, budget - len(collected)),
                include_payloads=include_payloads,
            )
            overflows = total is None or total > _MAX_RESULT_WINDOW or capped_total
            subslices = _subdivide(slice_start, slice_end)
            if overflows and subslices:
                # Discard the probe page: refetching it inside the subslices
                # would hand the same document two different ordinals.
                pending = subslices + pending
                continue
            reachable = min(total if total is not None else len(page), _MAX_RESULT_WINDOW)
            if overflows:
                stats.overflowed_slices += 1
            _extend_unique(collected, seen, page, budget)
            offset = len(page)
            while offset < reachable and len(collected) < budget:
                size = min(_PAGE_SIZE, budget - len(collected), _MAX_RESULT_WINDOW - offset)
                if size <= 0:
                    break
                page, _total, _capped = await self._search(
                    index_path=index_path,
                    preset=preset,
                    start=slice_start,
                    end=slice_end,
                    offset=offset,
                    size=size,
                    include_payloads=include_payloads,
                )
                if not page:
                    break
                _extend_unique(collected, seen, page, budget)
                offset += len(page)
        stats.documents += len(collected)
        return collected

    async def _search(
        self,
        *,
        index_path: str,
        preset: ElasticSchemaPreset,
        start: datetime,
        end: datetime,
        offset: int,
        size: int,
        include_payloads: bool,
    ) -> tuple[list[_ElasticDocument], int | None, bool]:
        """One bounded `_search`. Returns (documents, total, total_was_capped)."""
        body: dict[str, Any] = {
            "size": max(size, 0),
            "from": offset,
            # Without this, ES 7+ caps the reported total at 10,000 and reports
            # `relation: gte`, so a slice that needs subdividing looks like one
            # that does not.
            "track_total_hits": True,
            "sort": _sort_clause(preset),
            "_source": list(preset.source_fields(include_payloads=include_payloads)),
            "query": {"bool": {"filter": [_range_filter(preset, start, end)]}},
        }
        payload = await self._post(
            f"/{index_path}/_search",
            body,
            # A pattern that currently matches no index is a normal state for a
            # rolling index set, not an error.
            params={"ignore_unavailable": "true", "allow_no_indices": "true"},
        )
        envelope = payload.get("hits")
        if not isinstance(envelope, dict):
            raise RuntimeProviderError("Elasticsearch search response has no hits envelope")
        total, capped = _total_hits(envelope)
        raw_hits = envelope.get("hits")
        hits = [item for item in raw_hits if isinstance(item, dict)] if isinstance(raw_hits, list) else []
        documents = [
            _ElasticDocument(
                index=str(hit.get("_index") or "unknown"),
                doc_id=str(hit.get("_id") or ""),
                source=source,
            )
            for hit in hits
            if isinstance(source := hit.get("_source"), dict)
        ]
        return documents, total, capped

    async def _cluster_info(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Read `GET /`: cluster name, uuid and version.

        The version is recorded for reporting and capability decisions. It is
        NEVER a gate: this provider is one code path across 7.x, 8.x and 9.x, so
        refusing a major version would only reintroduce the fork it avoids.
        """
        payload = await self._get("/", timeout=timeout)
        if not isinstance(payload, dict):
            raise RuntimeProviderError("Elasticsearch root endpoint returned an unexpected payload")
        version = payload.get("version") if isinstance(payload.get("version"), dict) else {}
        number = str(version.get("number") or "") if isinstance(version, dict) else ""
        return {
            "cluster_name": str(payload.get("cluster_name") or ""),
            "cluster_uuid": str(payload.get("cluster_uuid") or ""),
            "version": number,
            "major_version": _major_version(number),
        }

    async def _resolve_index_names(self) -> list[str]:
        """List concrete indices, aliases and data streams, degrading by version.

        `GET /_resolve/index/*` needs 7.9+. Older clusters fall back to
        `GET /_alias`, then to `GET /_cat/indices`. Degrade, never refuse.
        """
        try:
            payload = await self._get("/_resolve/index/*")
        except RuntimeProviderError:
            logger.info("Elasticsearch _resolve/index unavailable; falling back to _alias")
        else:
            if isinstance(payload, dict):
                names = [
                    name
                    for key in ("indices", "aliases", "data_streams")
                    for entry in (payload.get(key) or [])
                    if isinstance(entry, dict) and (name := entry.get("name"))
                    if isinstance(name, str)
                ]
                if names:
                    return _visible_index_names(names)

        try:
            payload = await self._get("/_alias")
        except RuntimeProviderError:
            logger.info("Elasticsearch _alias unavailable; falling back to _cat/indices")
        else:
            if isinstance(payload, dict):
                names = list(payload)
                for body in payload.values():
                    aliases = body.get("aliases") if isinstance(body, dict) else None
                    if isinstance(aliases, dict):
                        names.extend(aliases)
                if names:
                    return _visible_index_names(name for name in names if isinstance(name, str))

        payload = await self._get("/_cat/indices", params={"format": "json", "h": "index"})
        if isinstance(payload, list):
            return _visible_index_names(
                str(entry["index"])
                for entry in payload
                if isinstance(entry, dict) and isinstance(entry.get("index"), str)
            )
        raise RuntimeProviderError("Elasticsearch would not list its indices")

    async def _document_counts(self) -> dict[str, int]:
        """Per-index document counts, or {} when stats are not readable.

        A count is a nicety for the scope picker; a token without cluster
        monitor privileges must still be able to choose indices.
        """
        try:
            payload = await self._get("/_stats/docs", params={"level": "indices"})
        except RuntimeProviderError:
            logger.info("Elasticsearch index stats are not readable with these credentials")
            return {}
        indices = payload.get("indices") if isinstance(payload, dict) else None
        if not isinstance(indices, dict):
            return {}
        counts: dict[str, int] = {}
        for name, body in indices.items():
            if not isinstance(name, str) or not isinstance(body, dict):
                continue
            primaries = body.get("primaries")
            docs = primaries.get("docs") if isinstance(primaries, dict) else None
            count = docs.get("count") if isinstance(docs, dict) else None
            if isinstance(count, int) and not isinstance(count, bool):
                counts[name] = count
        return counts

    # -- HTTP -----------------------------------------------------------------

    def _require_config(self) -> ElasticSourceConfig:
        if self._config is None:
            raise RuntimeProviderError("This Elasticsearch source has no configuration loaded")
        return self._config

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            config = self._require_config()
            headers = {"Accept": "application/json"}
            if config.auth_header:
                headers["Authorization"] = config.auth_header
            self._http_client = httpx.AsyncClient(
                base_url=config.endpoint_url,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                transport=self._transport,
                headers=headers,
                verify=_tls_verify(config),
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | list[Any]:
        return await self._request("GET", path, params=params, timeout=timeout)

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = await self._request("POST", path, params=params, json_body=body)
        if not isinstance(payload, dict):
            raise RuntimeProviderError(f"Elasticsearch POST {path} returned a non-object payload")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | list[Any]:
        data: Any = None
        for attempt in range(_TRANSPORT_MAX_ATTEMPTS):
            try:
                response = await self._client().request(
                    method,
                    path,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=json_body,
                    timeout=timeout if timeout is not None else _REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                raise RuntimeProviderError(
                    f"Elasticsearch {method} {path} returned HTTP {exc.response.status_code}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise RuntimeProviderError(
                    f"Elasticsearch {method} {path} timed out"
                ) from exc
            except httpx.TransportError as exc:
                if attempt + 1 < _TRANSPORT_MAX_ATTEMPTS:
                    await asyncio.sleep(_TRANSPORT_RETRY_DELAY_SECONDS * (attempt + 1))
                    continue
                raise RuntimeProviderError(
                    f"Elasticsearch {method} {path} transport failure after "
                    f"{_TRANSPORT_MAX_ATTEMPTS} attempts: {type(exc).__name__}"
                ) from exc
            except ValueError as exc:
                # httpx encodes header values as ASCII and json() raises
                # ValueError; a homoglyph pasted into a token must not surface
                # as an unhandled 500.
                raise RuntimeProviderError(
                    f"Elasticsearch {method} {path} could not be encoded or decoded"
                ) from exc
        if isinstance(data, (dict, list)):
            return data
        raise RuntimeProviderError(f"Elasticsearch {method} {path} returned a non-JSON payload")


# --- batch construction ------------------------------------------------------


class _BatchBuilder:
    """Turns fetched documents into runtime records, one tier at a time."""

    def __init__(
        self,
        *,
        source_key: str,
        project_id: str,
        synced_at: datetime,
        config: ElasticSourceConfig,
    ) -> None:
        self._source_key = source_key
        self._project_id = project_id
        self._synced_at = synced_at
        self._config = config
        self._ordinals = _OrdinalAllocator()
        self._jobs: dict[str, RuntimeJob] = {}
        self._job_environments: dict[str, set[str]] = {}
        self._environments: dict[str, RuntimeEnvironment] = {}
        self._workspaces: dict[str, RuntimeWorkspace] = {}
        self._job_runs: list[RuntimeJobRun] = []
        self._executions: list[RuntimeExecution] = []
        self._failures: list[RuntimeFailure] = []
        self._log_events: list[RuntimeLogEvent] = []
        self.unparseable_timestamps = 0
        # Attribution gaps, counted rather than logged per occurrence: a sync
        # walks tens of thousands of documents, and a per-run warning would emit
        # thousands of identical lines and get muted.
        self.runs_without_identifier = 0
        self.runs_without_timestamp = 0
        self.documents_without_correlation = 0
        # Correlation ids observed to cover more than one job identifier. This
        # is the evidence that would justify mirroring job-to-job correlation
        # edges; until it is non-zero on a real estate, inferring a hand-off
        # topology would be inventing one.
        self.correlation_groups_spanning_units = 0

    # -- tier 1: real run reconstruction --------------------------------------

    def add_execution_tier(
        self, preset: ElasticSchemaPreset, documents: Iterable[_ElasticDocument]
    ) -> None:
        correlation_field = preset.correlation_field
        if correlation_field is None:
            raise RuntimeProviderError(
                f"Schema preset '{preset.key}' claims to reconstruct runs but declares no "
                "correlation field"
            )
        groups: dict[tuple[str, str], list[_ElasticDocument]] = {}
        for document in documents:
            self._note_index(document.index)
            correlation_id = _text(_resolve_path(document.source, correlation_field))
            if not correlation_id:
                # No correlation id means no run this document belongs to. It is
                # still real evidence, so it is kept as an unattributable log
                # row rather than folded into somebody else's run.
                self.documents_without_correlation += 1
                self._add_log_event(preset, document, attributable=False)
                continue
            sub_number = (
                _text(_resolve_path(document.source, preset.correlation_sub_field))
                if preset.correlation_sub_field
                else None
            ) or ""
            groups.setdefault((correlation_id, sub_number), []).append(document)

        for (correlation_id, sub_number), steps in groups.items():
            # A correlation id is documented as identifying ONE interface run,
            # but nothing in the index enforces it, and an ESB that propagates
            # its correlation id across a hand-off would put two interfaces
            # under one id. Taking the first interface found would then file the
            # whole chain under whichever one happened to sort first -- a
            # plausible-looking wrong attribution. Partition by the logged
            # identifier instead, so each run belongs to the interface that
            # actually logged it.
            by_unit = self._partition_by_unit(preset, steps)
            spans_units = len(by_unit) > 1
            if spans_units:
                self.correlation_groups_spanning_units += 1
            for unit_name, unit_steps in by_unit.items():
                self._add_run(
                    preset,
                    correlation_id,
                    sub_number,
                    unit_steps,
                    unit_suffix=unit_name if spans_units else None,
                )

    def _partition_by_unit(
        self, preset: ElasticSchemaPreset, steps: list[_ElasticDocument]
    ) -> dict[str | None, list[_ElasticDocument]]:
        """Split one correlation group by the job identifier its steps logged."""
        partitioned: dict[str | None, list[_ElasticDocument]] = {}
        for document in steps:
            unit = None
            for name in preset.unit_fields:
                unit = _text(_resolve_path(document.source, name))
                if unit:
                    break
            partitioned.setdefault(unit or None, []).append(document)
        return partitioned

    def _add_run(
        self,
        preset: ElasticSchemaPreset,
        correlation_id: str,
        sub_number: str,
        steps: list[_ElasticDocument],
        *,
        unit_suffix: str | None = None,
    ) -> None:
        # `execution_id` is the raw correlation id in the normal case -- one id,
        # one interface -- and only gains a discriminator when the id genuinely
        # covers more than one interface, which would otherwise collide on
        # `runtime_executions`' (project_id, execution_id) uniqueness.
        base_id = f"{correlation_id}#{sub_number}" if sub_number else correlation_id
        execution_id = _bounded_identifier(
            f"{base_id}#{unit_suffix}" if unit_suffix else base_id
        )
        ordered = sorted(steps, key=lambda item: _step_sort_key(preset, item))
        starts = [
            parsed
            for item in ordered
            if (parsed := parse_es_datetime(_resolve_path(item.source, preset.timestamp_field), preset.date_format))
        ]
        job_name = _first_text(ordered, preset.unit_fields)

        # Decide attributability BEFORE writing the log lines. A line is only
        # marked attribution-safe when a run record will actually exist for it;
        # otherwise the evidence is kept but flagged as unattributable, so a
        # consumer cannot read "safe" from a row that joins to nothing.
        attributable = bool(starts) and bool(job_name)
        for document in ordered:
            self._add_log_event(
                preset, document, attributable=attributable, execution_id=execution_id
            )

        if not starts:
            self.runs_without_timestamp += 1
            return
        if not job_name:
            # A run whose documents name no job identifier cannot be attributed,
            # and RuntimeJobRun/RuntimeExecution both require a job name. A
            # guessed or reconciled name would put a unit in the estate's job
            # vocabulary that no scanned integration corresponds to, so the log
            # rows stand alone and the gap is counted and reported instead.
            self.runs_without_identifier += 1
            return

        # Bounded once, here, so the run, the job and the environment row all
        # carry the identical value rather than diverging at a column edge.
        environment = _bounded_text(
            _first_text(ordered, (preset.environment_field,)) if preset.environment_field else None
        )
        project_name = _bounded_text(
            _first_text(ordered, (preset.project_field,)) if preset.project_field else None
        )
        index_name = ordered[0].index
        started_at = min(starts)
        ends = [
            parsed
            for item in ordered
            if preset.end_field
            and (parsed := parse_es_datetime(_resolve_path(item.source, preset.end_field), preset.date_format))
        ]
        complete = bool(preset.end_field) and len(ends) == len(ordered)
        ended_at = max(ends) if ends and complete else None
        state, raw_state = _run_state(preset, ordered, complete=complete)

        job = self._register_job(preset, job_name, environment, index_name)
        self._job_runs.append(
            RuntimeJobRun(
                source_key=self._source_key,
                run_id=execution_id,
                job_id=job.job_id,
                job_name=job.name,
                # The join that makes a log row attributable: `runtime_log_events`
                # carries no job name, so a log line reaches its job only through
                # a run/execution sharing this execution_id.
                execution_id=execution_id,
                state=state,
                raw_state=raw_state,
                executable_kind=RuntimeExecutableKind.JOB,
                environment_id=environment,
                environment_name=environment,
                workspace_id=index_name,
                workspace_name=index_name,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=_duration_ms(started_at, ended_at),
                # Exchange/row counters are deliberately absent: process
                # monitoring reports no row counts, and deriving one from the
                # number of step documents would be a message count posing as
                # a throughput fact.
            )
        )
        self._executions.append(
            RuntimeExecution(
                execution_id=execution_id,
                job_name=job.name,
                # `runtime_executions` carries no environment column, so the
                # environment lives on the job run above; this field is the
                # source-side project name where the schema has one.
                project_name=project_name or self._project_id,
                state=state,
                raw_state=raw_state,
                executable_kind=RuntimeExecutableKind.JOB,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=_duration_ms(started_at, ended_at),
            )
        )
        if state in _DIAGNOSABLE_STATES:
            self._add_failure(preset, execution_id, job.name, ordered, ended_at or started_at, state)

    def _add_failure(
        self,
        preset: ElasticSchemaPreset,
        execution_id: str,
        job_name: str,
        steps: list[_ElasticDocument],
        failed_at: datetime,
        state: RuntimeExecutionState,
    ) -> None:
        detail: str | None = None
        summary: str | None = None
        for document in steps:
            error_text = _text(_resolve_path(document.source, preset.error_field)) if preset.error_field else None
            if not error_text:
                continue
            detail = _safe_text(error_text, _MAX_ERROR_DETAIL_CHARS)
            summary = sanitize_sync_error(error_text)
            break
        if not summary:
            step_name = _first_text(
                [item for item in steps if _step_state(preset, item) in _FAILURE_PRECEDENCE],
                (preset.step_field,),
            )
            summary = (
                f"{job_name} reported {state.value} at step '{step_name}'"
                if step_name
                else f"{job_name} reported {state.value} in Elasticsearch process monitoring"
            )
        self._failures.append(
            RuntimeFailure(
                execution_id=execution_id,
                job_name=job_name,
                failed_at=failed_at,
                error_summary=summary,
                error_detail=detail,
            )
        )

    def _register_job(
        self,
        preset: ElasticSchemaPreset,
        job_name: str,
        environment: str | None,
        index_name: str,
    ) -> RuntimeJob:
        """Register the job under the identifier the source logged, verbatim.

        `job_id` IS the logged name, not a derived key. Consumers read `job_id`
        as the unit name (`FactsManifest.unit_names()` returns `job_id`) and
        deliberately do no fuzzy matching against scanned manifest units,
        because `AssetCheck_GetLocation` and `AssetCheck_GetLocations` are two
        real, separately-scheduled integrations that any normaliser would merge.
        So: no environment prefix, no case folding, no affix stripping, no
        reconciliation. Anything else here corrupts attribution rather than
        merely looking untidy.

        The cost of a verbatim id is that one interface running in TEST and PROD
        is ONE job row -- `runtime_jobs` is unique on
        (project_id, source_key, job_id), not on (job_id, environment). Rather
        than assert whichever environment happened to be upserted last, the
        job's environment is resolved in `build()` and left NULL when the name
        was observed in more than one. The per-run environment is authoritative
        and is carried on `runtime_job_runs`, which is what the environment-
        scoped rollups and doc-evidence reads filter on.
        """
        # `job_id`, `name` and `display_name` are all the same bounded value, so
        # the "job_id IS the logged name" contract holds even for a name longer
        # than the 255-char columns it is written to. Only the length guard can
        # ever change the string, and it appends a hash rather than truncating,
        # so two long names sharing a prefix cannot collapse into one job.
        job_id = _bounded_identifier(job_name)
        job_name = job_id
        if environment:
            self._job_environments.setdefault(job_id, set()).add(environment)
            self._environments.setdefault(
                environment,
                RuntimeEnvironment(
                    source_key=self._source_key,
                    environment_id=environment,
                    name=environment,
                    last_seen_at=self._synced_at,
                ),
            )
        existing = self._jobs.get(job_id)
        if existing is not None:
            return existing
        job = RuntimeJob(
            source_key=self._source_key,
            job_id=job_id,
            name=job_name,
            display_name=job_name,
            job_type=preset.key,
            # JOB, not ROUTE: these are discrete runs with a start and an end,
            # which is batch semantics. Marking them ROUTE would move them onto
            # the deployment-lifecycle surface and hide them from the default
            # Runs and Jobs views, whose inputs default to executable_kind=job.
            executable_kind=RuntimeExecutableKind.JOB,
            status="observed",
            workspace_id=index_name,
            workspace_name=index_name,
            last_seen_at=self._synced_at,
            source_updated_at=self._synced_at,
        )
        self._jobs[job_id] = job
        return job

    # -- tier 2: log-only -----------------------------------------------------

    def add_log_tier(
        self, preset: ElasticSchemaPreset, documents: Iterable[_ElasticDocument]
    ) -> None:
        for document in documents:
            self._note_index(document.index)
            self._add_log_event(preset, document, attributable=False)

    def _add_log_event(
        self,
        preset: ElasticSchemaPreset,
        document: _ElasticDocument,
        *,
        attributable: bool,
        execution_id: str | None = None,
    ) -> None:
        timestamp = parse_es_datetime(
            _resolve_path(document.source, preset.timestamp_field), preset.date_format
        )
        if timestamp is None:
            self.unparseable_timestamps += 1
            return
        status_token = (
            _token(_resolve_path(document.source, preset.status_field)) if preset.status_field else None
        )
        error_text = (
            _text(_resolve_path(document.source, preset.error_field)) if preset.error_field else None
        )
        severity = _severity(preset, status_token, has_error=bool(error_text))
        message = self._compose_message(preset, document, status_token, error_text)
        if execution_id is None:
            execution_id = _synthetic_execution_id(
                index=document.index,
                service=_text(_resolve_path(document.source, preset.service_field))
                if preset.service_field
                else None,
                timestamp=timestamp,
            )
        ordinal = self._log_ordinal(preset, document, execution_id, timestamp, severity, message)
        self._log_events.append(
            RuntimeLogEvent(
                execution_id=execution_id,
                timestamp=timestamp,
                severity=severity,
                message=message,
                line_ordinal=ordinal,
                # `execution` + attribution_safe only when this line belongs to
                # a correlation id that identifies ONE real run. Everything else
                # is engine-scoped evidence that must not be attributed or used
                # to synthesize an incident.
                scope="execution" if attributable else "engine",
                attribution_safe=attributable,
            )
        )

    def _log_ordinal(
        self,
        preset: ElasticSchemaPreset,
        document: _ElasticDocument,
        execution_id: str,
        timestamp: datetime,
        severity: RuntimeLogSeverity,
        message: str,
    ) -> int:
        """Prefer the schema's own step ordinal; fall back to content counting.

        A declared step number is intrinsic to the document, so it survives any
        change in fetch order or window boundary. Where none parses, the
        content-counting allocator gives the same guarantee without inventing an
        order.
        """
        if preset.step_number_field:
            declared = _step_number(_resolve_path(document.source, preset.step_number_field))
            if declared is not None:
                return declared
        return _FALLBACK_ORDINAL_BASE + self._ordinals.next_ordinal(
            execution_id, timestamp, severity, message
        )

    def _compose_message(
        self,
        preset: ElasticSchemaPreset,
        document: _ElasticDocument,
        status_token: str | None,
        error_text: str | None,
    ) -> str:
        parts: list[str] = []
        if preset.step_field or preset.step_number_field:
            step_name = _text(_resolve_path(document.source, preset.step_field)) if preset.step_field else None
            step_number = (
                _text(_resolve_path(document.source, preset.step_number_field))
                if preset.step_number_field
                else None
            )
            step_total = (
                _text(_resolve_path(document.source, preset.step_total_field))
                if preset.step_total_field
                else None
            )
            if step_number and step_total:
                parts.append(f"step {step_number}/{step_total}")
            elif step_number:
                parts.append(f"step {step_number}")
            if step_name:
                parts.append(step_name)
        if status_token:
            parts.append(f"status={status_token}")
        for name in preset.message_fields:
            value = _text(_resolve_path(document.source, name))
            if value:
                parts.append(value)
                break
        if error_text:
            parts.append(f"error={error_text}")
        if self._config.capture_payloads and self._config.payload_max_chars > 0:
            for name in preset.payload_fields:
                payload = _text(_resolve_path(document.source, name))
                if not payload:
                    continue
                # Bound each body BEFORE joining so one large payload cannot
                # consume the whole message budget and evict the diagnosis.
                parts.append(f"{name}={payload[: self._config.payload_max_chars]}")
        if not parts:
            # `message` is NOT NULL and a blank line is not a fact. Say what the
            # document was instead of writing an empty row.
            parts.append(f"{preset.key} document in {document.index} with no readable text")
        return _safe_text(" | ".join(parts), _MAX_MESSAGE_CHARS)

    # -- shared ---------------------------------------------------------------

    def _note_index(self, index_name: str) -> None:
        self._workspaces.setdefault(
            index_name,
            RuntimeWorkspace(
                source_key=self._source_key,
                workspace_id=index_name,
                name=index_name,
                # An index holds documents from every environment, so it maps to
                # none of them; the environment lives on the run.
                environment_id=None,
                workspace_type="elasticsearch_index",
                last_seen_at=self._synced_at,
            ),
        )

    def _resolved_jobs(self) -> list[RuntimeJob]:
        """Stamp each job's environment only when it is unambiguous.

        A verbatim job id means one interface seen in TEST and PROD is one job
        row. Asserting either environment on it would be a coin flip that
        changes between syncs, so an ambiguous name keeps a NULL environment and
        the truth stays on the runs.
        """
        resolved: list[RuntimeJob] = []
        for job_id, job in self._jobs.items():
            environments = self._job_environments.get(job_id, set())
            if len(environments) == 1:
                only = next(iter(environments))
                resolved.append(
                    job.model_copy(update={"environment_id": only, "environment_name": only})
                )
                continue
            if len(environments) > 1:
                logger.info(
                    "Elasticsearch job %r was observed in %d environments (%s); its job row "
                    "keeps no environment and the per-run environments stand",
                    job.name,
                    len(environments),
                    ", ".join(sorted(environments)),
                )
            resolved.append(job)
        return resolved

    def log_attribution_gaps(self) -> None:
        """Report, once per cycle, every run this provider refused to attribute."""
        if self.runs_without_identifier:
            logger.warning(
                "%d Elasticsearch correlation groups named no job identifier; their log lines "
                "were imported as unattributable evidence and no run records were created. "
                "This is a source-side gap, not a mapping one: a reconciled name would "
                "attribute runs to an integration that may not be the one that ran.",
                self.runs_without_identifier,
            )
        if self.runs_without_timestamp:
            logger.warning(
                "%d Elasticsearch correlation groups had no parseable start timestamp on any "
                "step; their log lines were kept but no run records were created",
                self.runs_without_timestamp,
            )
        if self.documents_without_correlation:
            logger.warning(
                "%d Elasticsearch process-monitoring documents carried no correlation id and "
                "were imported as engine-scoped, unattributable log lines",
                self.documents_without_correlation,
            )
        if self.correlation_groups_spanning_units:
            # Reported, not modelled. `runtime_job_dependencies` is keyed
            # per (job, dependency_id), so one edge row per correlation id would
            # grow the table with TRAFFIC volume rather than with topology --
            # millions of rows for an eight-month ESB estate. Mirroring a
            # hand-off relation needs a stable pair key and a real estate to
            # confirm the relation exists; this counter is how that gets
            # confirmed instead of assumed.
            logger.warning(
                "%d Elasticsearch correlation ids covered more than one job identifier. Each "
                "interface got its own run rather than the whole chain being filed under one, "
                "but no job-to-job correlation edges were mirrored.",
                self.correlation_groups_spanning_units,
            )

    def build(
        self,
        *,
        partial: bool,
        failure_reason_code: str | None,
        error_summary: str | None,
    ) -> RuntimeSyncBatch:
        return RuntimeSyncBatch(
            # `sources` is deliberately empty. The self-service sync path does
            # not persist a provider-declared source row (the operator's
            # registered row already holds the credentials, health and
            # schedule), and declaring one produced a second, credential-less
            # row that the admin UI rendered as a real connection.
            sources=[],
            environments=list(self._environments.values()),
            workspaces=list(self._workspaces.values()),
            jobs=self._resolved_jobs(),
            job_runs=self._job_runs,
            executions=self._executions,
            failures=self._failures,
            log_events=self._log_events,
            partial=partial,
            failure_reason_code=failure_reason_code,
            error_summary=error_summary,
            synced_at=self._synced_at,
        )


# --- helpers -----------------------------------------------------------------


def _tls_verify(config: ElasticSourceConfig) -> Any:
    if not config.verify_tls:
        logger.warning(
            "Elasticsearch source %s has TLS verification disabled; this is for local "
            "clusters only",
            config.endpoint_url,
        )
        return False
    if config.ca_cert_pem:
        # PEM text rather than a server-side path: a path from a web form is a
        # local-file-read surface, and the certificate itself is not a secret.
        context = ssl.create_default_context(cadata=config.ca_cert_pem)
        return context
    return True


def _major_version(number: str) -> int | None:
    head = number.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _index_path(indices: Sequence[str]) -> str:
    """Join validated index expressions into one request path segment.

    The expressions are already restricted to a conservative charset, so `quote`
    here is belt and braces rather than the guard.
    """
    if not indices:
        raise RuntimeProviderError("No Elasticsearch indices were selected")
    return ",".join(quote(index, safe="*-_.+") for index in indices)


def _sort_clause(preset: ElasticSchemaPreset) -> list[dict[str, Any]]:
    """A stable sort with an explicit unmapped type.

    `unmapped_type` matters because an index expression can span indices that do
    not all map the field; without it Elasticsearch refuses the whole search
    with "No mapping found ... in order to sort on".
    """
    clause: list[dict[str, Any]] = [
        {preset.timestamp_field: {"order": "asc", "unmapped_type": "date"}}
    ]
    if preset.correlation_field:
        clause.append({preset.correlation_field: {"order": "asc", "unmapped_type": "keyword"}})
    return clause


def _range_filter(preset: ElasticSchemaPreset, start: datetime, end: datetime) -> dict[str, Any]:
    """A half-open range in the field's OWN date format.

    Half-open (`gte`/`lt`) so two adjacent slices never both claim a document
    on the boundary. `format` and the rendered bounds both come from the preset:
    sending ISO-8601 bounds against `process_monitoring*`'s `basic_date_time`
    field does not error, it silently matches nothing.
    """
    return {
        "range": {
            preset.timestamp_field: {
                "gte": format_es_datetime(start, preset.date_format),
                "lt": format_es_datetime(end, preset.date_format),
                "format": preset.date_format,
            }
        }
    }


def _total_hits(envelope: Mapping[str, Any]) -> tuple[int | None, bool]:
    """Read `hits.total` in either shape, and say whether it is a lower bound.

    Pre-7.0 clusters send a bare number; 7.0+ send `{value, relation}`. A
    `relation` of `gte` means the count was capped, so the slice needs
    subdividing rather than trusting the number.
    """
    raw = envelope.get("total")
    if isinstance(raw, Mapping):
        value = raw.get("value")
        capped = str(raw.get("relation") or "eq").lower() != "eq"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value), capped
        return None, capped
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw), False
    return None, False


def _hour_floor(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _ingest_window(
    *, now: datetime, window_days: int, since: datetime | None
) -> tuple[datetime, datetime]:
    """The bounded, hour-aligned window one cycle may read.

    The newest partial hour is excluded on purpose. A bucket that is always
    fetched whole assigns stable ordinals, so the cost is at most one hour of
    freshness and the gain is that an overlapping re-ingest updates rows instead
    of multiplying them.

    `since` is the incremental watermark. It can only narrow the window, never
    widen it past the configured cap: a stale watermark must not turn one cycle
    into an unbounded backfill.
    """
    end = _hour_floor(now.astimezone(UTC))
    start = end - timedelta(days=window_days)
    if since is not None:
        start = max(start, _hour_floor(since.astimezone(UTC)))
    return start, end


def _time_slices(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Hour-aligned half-open slices, newest first."""
    slices: list[tuple[datetime, datetime]] = []
    cursor = end
    while cursor > start:
        slice_start = max(start, cursor - timedelta(hours=_SLICE_HOURS))
        slices.append((slice_start, cursor))
        cursor = slice_start
    return slices


def _subdivide(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split an overflowing slice, or return [] once it cannot usefully shrink."""
    span = end - start
    if span <= timedelta(minutes=_SUBSLICE_MINUTES):
        return []
    step = timedelta(minutes=_SUBSLICE_MINUTES)
    slices: list[tuple[datetime, datetime]] = []
    cursor = end
    while cursor > start:
        slice_start = max(start, cursor - step)
        slices.append((slice_start, cursor))
        cursor = slice_start
    return slices


def _extend_unique(
    collected: list[_ElasticDocument],
    seen: set[tuple[str, str]],
    page: Iterable[_ElasticDocument],
    budget: int,
) -> None:
    """Append within budget, dropping any document already collected.

    Subdivision refetches a range, so the same hit can arrive twice. Two copies
    would be counted as two occurrences of the same line and written as two rows.
    """
    for document in page:
        if len(collected) >= budget:
            return
        key = (document.index, document.doc_id)
        if document.doc_id and key in seen:
            continue
        if document.doc_id:
            seen.add(key)
        collected.append(document)


def _resolve_path(source: Mapping[str, Any], path: str | None) -> Any:
    """Read a field that may be a flat key or a dotted ECS-style path."""
    if not path:
        return None
    if path in source:
        return source[path]
    value: Any = source
    for part in path.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            return None
    return value


def _text(value: Any) -> str | None:
    """Coerce a source value to display text.

    A keyword field under a dynamic mapping legitimately holds a list, or an
    empty list when the document never set it -- `str()` would turn that into
    the literal "[]" and make "no value" indistinguishable from a value.

    The only transformation applied is surrounding-whitespace removal, and that
    boundary is deliberate: identifiers read through this function (job names,
    environments) are otherwise stored exactly as logged. Case is never folded,
    no prefix or suffix is ever stripped, and nothing is reconciled against
    another vocabulary -- `AssetCheck_GetLocation` and `AssetCheck_GetLocations`
    are two real integrations, so any normaliser merges two different things.
    """
    if value is None or isinstance(value, bool):
        return None if value is None else str(value)
    if isinstance(value, list):
        items = [text for item in value if (text := _text(item))]
        return " ".join(items) if items else None
    if isinstance(value, Mapping):
        return None
    text = str(value).strip()
    return text or None


def _token(value: Any) -> str | None:
    text = _text(value)
    return text.strip().lower() if text else None


def _first_text(
    documents: Sequence[_ElasticDocument], field_names: Sequence[str | None]
) -> str | None:
    for document in documents:
        for name in field_names:
            value = _text(_resolve_path(document.source, name))
            if value:
                return value
    return None


def _step_number(value: Any) -> int | None:
    """Cast a keyword-mapped step ordinal to an int.

    `ProcessingStepNr` is keyword/text (`numeric_detection` is off), so a
    lexicographic order puts "10" before "2".
    """
    text = _text(value)
    if text is None:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if 0 <= number <= _MAX_STEP_ORDINAL else None


def _step_sort_key(preset: ElasticSchemaPreset, document: _ElasticDocument) -> tuple[Any, ...]:
    number = (
        _step_number(_resolve_path(document.source, preset.step_number_field))
        if preset.step_number_field
        else None
    )
    started = parse_es_datetime(
        _resolve_path(document.source, preset.timestamp_field), preset.date_format
    )
    return (
        0 if number is not None else 1,
        number if number is not None else 0,
        started.timestamp() if started is not None else 0.0,
        document.doc_id,
    )


def _step_state(
    preset: ElasticSchemaPreset, document: _ElasticDocument
) -> RuntimeExecutionState | None:
    token = _token(_resolve_path(document.source, preset.status_field)) if preset.status_field else None
    if token is None:
        return None
    return preset.status_states.get(token)


def _run_state(
    preset: ElasticSchemaPreset, steps: Sequence[_ElasticDocument], *, complete: bool
) -> tuple[RuntimeExecutionState, str | None]:
    """Derive one run's outcome from its steps' own statuses.

    Never from a count of documents. The order of the checks is the whole
    safety argument:

    1. any failure-family step status makes the run that outcome -- a run that
       failed at step 3 of 9 did not "partially succeed";
    2. otherwise, a declared `ProcessingStepTotal` that exceeds the steps
       actually seen WITHHOLDS the success claim (the evidence is incomplete,
       which is not the same as a failure and must not be reported as one);
    3. otherwise, a step with no end timestamp means still running;
    4. success only when every step's status maps to success;
    5. anything else is UNKNOWN, and the verbatim status is preserved so an
       unmapped vocabulary shows up as a mapping gap rather than a wrong count.
    """
    raw_state = _first_text(list(reversed(list(steps))), (preset.status_field,))
    tokens = [
        token
        for document in steps
        if (token := _token(_resolve_path(document.source, preset.status_field)))
    ]
    states = [preset.status_states.get(token) for token in tokens]
    for candidate in _FAILURE_PRECEDENCE:
        if candidate in states:
            return candidate, raw_state

    unmapped = sorted({token for token, state in zip(tokens, states, strict=True) if state is None})
    if unmapped:
        _warn_unknown_statuses(preset, unmapped)

    expected_total = _step_number(
        _resolve_path(steps[0].source, preset.step_total_field) if preset.step_total_field else None
    )
    observed = len({_step_number(_resolve_path(item.source, preset.step_number_field)) for item in steps})
    if expected_total is not None and expected_total > 0 and observed < expected_total:
        return RuntimeExecutionState.UNKNOWN, raw_state
    if not complete:
        return RuntimeExecutionState.RUNNING, raw_state
    if RuntimeExecutionState.RUNNING in states:
        return RuntimeExecutionState.RUNNING, raw_state
    if states and all(state == RuntimeExecutionState.SUCCESS for state in states):
        return RuntimeExecutionState.SUCCESS, raw_state
    return RuntimeExecutionState.UNKNOWN, raw_state


def _warn_unknown_statuses(preset: ElasticSchemaPreset, tokens: Sequence[str]) -> None:
    for token in tokens:
        key = f"{preset.key}|{token}"
        if key in _unknown_statuses_warned or len(_unknown_statuses_warned) >= _UNKNOWN_STATUS_WARN_LIMIT:
            return
        _unknown_statuses_warned.add(key)
        logger.warning(
            "Elasticsearch preset '%s' has no mapping for %s status %r. Runs carrying it are "
            "stored with state=unknown (never success, never failure); add the token to "
            "status_states in elastic_schemas.py to count them.",
            preset.key,
            preset.status_field,
            token,
        )


def _severity(
    preset: ElasticSchemaPreset, status_token: str | None, *, has_error: bool
) -> RuntimeLogSeverity:
    mapped = preset.status_severities.get(status_token or "")
    if mapped is not None:
        return mapped
    # An error field with content is at least an error-level line even when the
    # status token says nothing this preset recognises.
    return RuntimeLogSeverity.ERROR if has_error else RuntimeLogSeverity.INFO


def _duration_ms(started_at: datetime, ended_at: datetime | None) -> int | None:
    if ended_at is None:
        return None
    return int((ended_at - started_at).total_seconds() * 1000)


def _safe_text(value: str, limit: int) -> str:
    """Redact, then bound.

    Redaction runs FIRST so truncation cannot leave half a credential behind,
    and `redact_entities` normalises the identifying entity shapes (buckets,
    hosts, URLs) that a keyword filter alone never catches. The store applies
    `redact_credentials` again on write; both functions are idempotent.
    """
    collapsed = " ".join(value.replace("\n", " ").replace("\r", " ").split())
    redacted = redact_entities(redact_credentials(collapsed))
    if len(redacted) <= limit:
        return redacted
    return f"{redacted[: max(limit - 3, 0)]}..."


def _bounded_text(value: str | None) -> str | None:
    """Bound an optional String(255) attribute, preserving None."""
    return None if value is None else _bounded_identifier(value)


def _bounded_identifier(value: str) -> str:
    """Keep an id inside the 255-char columns it is written to.

    Hashing the whole value rather than truncating it: two long ids sharing a
    prefix would otherwise collapse into one execution.
    """
    if len(value) <= _MAX_IDENTIFIER_CHARS:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"{value[: _MAX_IDENTIFIER_CHARS - 33]}#{digest}"


def _synthetic_execution_id(*, index: str, service: str | None, timestamp: datetime) -> str:
    """A grouping key for log-only rows, and openly not a run id.

    `runtime_log_events.execution_id` is NOT NULL, so a log-only row needs some
    value. It is deliberately prefixed and hour-bucketed so it cannot be
    mistaken for a real execution: no `runtime_executions` row will ever carry
    it, and `attribution_safe=False` says the same thing in the data.

    Hour-bucketed because the ingest window is hour-aligned, which is what keeps
    a re-ingest idempotent.
    """
    bucket = timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H")
    return _bounded_identifier(
        f"{_SYNTHETIC_EXECUTION_PREFIX}:{index}:{service or 'unattributed'}:{bucket}"
    )


def _preset_patterns() -> list[tuple[str, ElasticSchemaPreset]]:
    """Every preset that claims an index pattern, for the scope picker."""
    return [
        (pattern, preset)
        for preset in (PROCESS_MONITORING_PRESET,)
        for pattern in preset.index_patterns
    ]


def _visible_index_names(names: Iterable[str]) -> list[str]:
    """Drop system/hidden indices; they are noise in a scope picker."""
    return sorted({name for name in names if name and not name.startswith(".")})


def _sum_counts(names: Sequence[str], counts: Mapping[str, int]) -> int | None:
    known = [counts[name] for name in names if name in counts]
    return sum(known) if known else None


def _scope_detail(index_count: int, document_count: int | None, preset_label: str) -> str:
    indices = f"{index_count} index" if index_count == 1 else f"{index_count} indices"
    if document_count is None:
        return f"{indices} -- {preset_label}"
    return f"{indices}, {document_count} documents -- {preset_label}"


def _sanitize_provider_error(message: str) -> str:
    return sanitize_sync_error(message) or "Elasticsearch could not be reached"


def _log_bounding(stats: _FetchStats, config: ElasticSourceConfig) -> None:
    """Report what the caps cost, loudly, without failing the cycle.

    Hitting a configured cap is the DESIGNED behaviour of a bounded ingest, not
    a fault, and marking the batch partial for it would record a sync failure
    every cycle and auto-disable a healthy source after three of them. So this
    warns with the exact numbers instead -- silent truncation is the thing that
    reads downstream as "this estate has no traffic".
    """
    if stats.budget_exhausted:
        logger.warning(
            "Elasticsearch document cap reached: %d of a maximum %d documents imported this "
            "cycle (max_documents_per_sync). Older documents in the window were not read; "
            "narrow the index selection or shorten window_days to cover the window fully.",
            stats.documents,
            config.max_documents,
        )
    if stats.overflowed_slices:
        logger.warning(
            "%d Elasticsearch time slices held more than %d documents even at %d-minute "
            "granularity, so only the first %d of each were read.",
            stats.overflowed_slices,
            _MAX_RESULT_WINDOW,
            _SUBSLICE_MINUTES,
            _MAX_RESULT_WINDOW,
        )
    if stats.unparseable_timestamps:
        logger.warning(
            "%d Elasticsearch documents had no parseable timestamp and were skipped",
            stats.unparseable_timestamps,
        )


provider = ElasticRuntimeProvider()
