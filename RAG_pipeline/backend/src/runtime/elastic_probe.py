"""Bounded, read-only probe primitives for a near-permissionless Elastic key.

Why this module exists
----------------------
A real investigation of a production Elastic estate was carried out through a
connector key with almost no privileges: ``_mapping`` returned 403 and alias
listing was forbidden.  The estate was still fully characterised, by going
through the doors that were open:

* field discovery through ``_field_caps`` rather than ``_mapping``;
* the concrete indices behind an alias recovered by AGGREGATING on the
  ``_index`` metadata field, rather than by asking an index-metadata API;
* the presence and size of business payloads established from percentiles of a
  numeric size field, without ever retrieving a payload.

Those four moves are generic and repeatable, so they belong in the product as a
tool surface rather than in an operator's shell history.  This module is that
surface: :class:`ElasticProbeService`, four primitives, one bounded HTTP client.

Two invariants, both load-bearing
---------------------------------
**A. No document content ever crosses this boundary.**  Every query is issued
with ``size: 0`` and ``_source: false``, and every response is assembled from
hit TOTALS, aggregation buckets and field metadata only -- ``hits.hits`` is
never read.  A cluster (or a proxy in front of one) that returns documents
anyway is a contract violation and raises
:class:`ElasticProbeContractError` rather than being quietly tolerated, because
the destination of this data is an LLM context and the estate that motivated
the module carries patient and order data.  The response models are deliberately
free of any ``dict``/``Any`` passthrough field, so there is no channel through
which a raw payload could travel even by accident.

**B. A zero is never ambiguous.**  A ``terms`` aggregation on an UNMAPPED field
returns zero buckets rather than an error, and an ``exists`` filter on one
returns zero hits rather than an error.  So a bare ``0`` conflates "this field
does not exist" with "this field exists and is empty in 92.8% of messages" --
two findings with opposite consequences.  Before any zero is returned the field
is resolved against the ``describe-fields`` path and the answer is reported as a
structured :class:`ProbeResolution`:

* ``ok`` -- the number is a fact about a field that exists;
* ``field_unmapped`` -- the field does not exist; the count is ``None``, never
  ``0``, so a consumer that ignores ``resolution`` cannot read a fabricated zero;
* ``field_mapping_unknown`` -- ``_field_caps`` was refused AND the fallback
  probe was inconclusive, so the truth is genuinely unknown and says so;
* ``pattern_matched_no_indices`` -- no document is visible under the pattern at
  all, which makes every per-field number moot;
* ``field_not_aggregatable`` -- the field exists but cannot carry the requested
  aggregation.

This mirrors an invariant already written down elsewhere in this codebase --
``mcp_server/providers/custom/section_parser.py``: "a transform that loses
everything must raise, never return empty."  Same rule, different protocol.

What this module is NOT
-----------------------
It is not an ingest path and writes nothing.  It reads the source's stored
credential to talk to the cluster LIVE, which makes it the only runtime read
that does not go through the mirror -- deliberately, because its purpose is to
answer questions about an estate BEFORE deciding what to mirror.

The boundary on what it may look at is the credential's own privileges, not the
source's ``index_names`` ingest allowlist: the probe exists to decide what that
allowlist should be, so constraining it to the allowlist would make it useless
for its one job.  The index-expression SHAPE check is still the ingest one --
imported, not re-implemented, because a second copy of that predicate is free to
drift and it is the guard that keeps ``*``, ``_all``, exclusions, date-math and
path traversal out of a request path.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from enum import Enum
from typing import Any, Literal, Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.runtime.providers import RuntimeProviderError

# `_index_names` and `_tls_verify` are imported rather than re-implemented on
# purpose: the index-expression allow-list and the TLS policy are single-source
# predicates, and a second copy of either is free to drift from the one the
# ingest path enforces. See "What this module is NOT" in the module docstring.
from src.runtime.providers.elastic import (
    PROVIDER_KEY as ELASTIC_PROVIDER_KEY,
)
from src.runtime.providers.elastic import (
    ElasticSourceConfig,
    parse_elastic_config,
)
from src.runtime.providers.elastic import (
    _index_names as _validate_index_expressions,
)
from src.runtime.providers.elastic import _tls_verify as _tls_verify_for
from src.runtime.redaction import sanitize_sync_error
from src.runtime.store import RuntimeRecordNotFoundError, RuntimeStoreRepository

logger = logging.getLogger(__name__)

# --- bounds ------------------------------------------------------------------
# Every one of these exists so a single probe call cannot become an expensive
# query against a customer's production cluster, and so a response cannot grow
# without limit inside an LLM context.

#: Wall-clock ceiling on one HTTP round trip to the cluster.
PROBE_TIMEOUT_SECONDS = 20.0
#: Search-level timeout handed to Elasticsearch itself, so the cluster stops
#: work rather than merely having the connection dropped on it. A search that
#: hits it reports `timed_out`, which is surfaced as `partial=True`: a count
#: that is short because shards ran out of time must never read as a fact.
ES_QUERY_TIMEOUT = "10s"
#: Candidate field names accepted in one describe-fields request.
MAX_CANDIDATE_FIELDS = 64
#: Fields reported back from a wildcard `_field_caps` sweep.
MAX_FIELDS_RETURNED = 500
#: `terms` buckets for the `_index` recovery.
MAX_INDEX_BUCKETS = 500
DEFAULT_INDEX_BUCKETS = 200
#: `terms` buckets for a size-distribution `group_by`.
MAX_GROUP_BUCKETS = 100
DEFAULT_GROUP_BUCKETS = 25
#: Length of a match_phrase needle.
MAX_PHRASE_CHARS = 512
MAX_FIELD_NAME_CHARS = 256
MAX_PERCENTS = 10

DEFAULT_PERCENTS: tuple[float, ...] = (50.0, 75.0, 90.0, 95.0, 99.0)

#: Byte-oriented buckets that make the envelope-versus-payload distinction
#: legible at a glance: an envelope is a few hundred bytes, a real business
#: message is tens of kilobytes. Reported in the field's own units, which are
#: bytes for every size field this was built against but are not enforced.
_SIZE_RANGES: tuple[tuple[str, float | None, float | None], ...] = (
    ("0", None, 1),
    ("1-511", 1, 512),
    ("512-2047", 512, 2048),
    ("2048-16383", 2048, 16384),
    ("16384-131071", 16384, 131072),
    ("131072-1048575", 131072, 1048576),
    ("1048576+", 1048576, None),
)

# Field names travel in the JSON BODY, never the path, so this is a sanity
# allow-list rather than an escape. It admits the metadata fields (`_index`),
# dotted sub-fields (`body.keyword`) and ECS-style `@timestamp`.
_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_@][A-Za-z0-9_.@*+-]*$")

# Statuses that mean "this API is closed to these credentials" as opposed to
# "this request was wrong" or "the cluster is broken". 405 is included because a
# proxy in front of a managed cluster commonly blocks an endpoint by method.
_REFUSAL_STATUSES = frozenset({401, 403, 405})


# --- errors ------------------------------------------------------------------


class ElasticProbeError(RuntimeProviderError):
    """A probe failure whose message is already redacted and safe to surface."""


class ElasticProbeContractError(ElasticProbeError):
    """The cluster returned document content despite `size: 0` / `_source: false`.

    Refusing here rather than filtering is the point. If the query this module
    emitted did not suppress documents, the reason is unknown -- a rewriting
    proxy, a search template, a future Elasticsearch behaviour change -- and the
    only safe response to an unknown reason is to not hand the payload onward.
    """


class ElasticProbeSourceError(ElasticProbeError):
    """No usable Elasticsearch credential could be resolved for the project."""


class _ElasticHttpError(ElasticProbeError):
    """Transport-level failure carrying the status, so a refusal is detectable."""

    def __init__(self, message: str, status_code: int | None, error_type: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type

    @property
    def refused(self) -> bool:
        return self.status_code in _REFUSAL_STATUSES


# --- resolution vocabulary ---------------------------------------------------


class ProbeResolution(str, Enum):
    """What a number in this response actually means. Invariant B lives here."""

    OK = "ok"
    FIELD_UNMAPPED = "field_unmapped"
    FIELD_MAPPING_UNKNOWN = "field_mapping_unknown"
    PATTERN_MATCHED_NO_INDICES = "pattern_matched_no_indices"
    FIELD_NOT_AGGREGATABLE = "field_not_aggregatable"


class FieldEvidence(str, Enum):
    """How a field's existence was established."""

    FIELD_CAPS = "field_caps"
    #: `_field_caps` was refused; the name was probed with an `exists` count and
    #: matched at least one document, which proves it resolves.
    EXISTS_PROBE = "exists_probe"
    #: `_field_caps` was refused and the `exists` probe matched nothing, which
    #: proves nothing: unmapped and mapped-but-empty are indistinguishable.
    EXISTS_PROBE_INCONCLUSIVE = "exists_probe_inconclusive"


# --- requests ----------------------------------------------------------------


def _validated_index_pattern(value: str) -> str:
    """Apply the INGEST index-expression allow-list to a probe pattern."""
    try:
        names = _validate_index_expressions({"index_names": [value]})
    except RuntimeProviderError as exc:
        raise ValueError(str(exc)) from exc
    if len(names) != 1:
        raise ValueError("Give exactly one index name or prefix pattern")
    return names[0]


def _validated_field_name(value: str, *, allow_wildcard: bool) -> str:
    name = value.strip()
    if not name:
        raise ValueError("A field name cannot be empty")
    if len(name) > MAX_FIELD_NAME_CHARS:
        raise ValueError(f"A field name cannot exceed {MAX_FIELD_NAME_CHARS} characters")
    if not _FIELD_NAME_RE.match(name):
        raise ValueError(f"'{value}' is not a usable Elasticsearch field name")
    if "*" in name and not allow_wildcard:
        raise ValueError(
            f"'{value}' contains a wildcard. Aggregating on a wildcard is not "
            "meaningful; name one field."
        )
    return name


class _ProbeRequest(BaseModel):
    """Fields every probe request shares.

    `source_id` is optional and only needed to disambiguate a project holding
    more than one credentialled Elasticsearch source. Picking one silently would
    answer a question about a cluster the caller did not name.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=255)
    source_id: int | None = Field(default=None, ge=1)
    index_pattern: str = Field(min_length=1, max_length=255)

    @field_validator("index_pattern")
    @classmethod
    def _check_index_pattern(cls, value: str) -> str:
        return _validated_index_pattern(value)


class DescribeFieldsRequest(_ProbeRequest):
    candidate_fields: list[str] | None = Field(default=None, max_length=MAX_CANDIDATE_FIELDS)

    @field_validator("candidate_fields")
    @classmethod
    def _check_candidates(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        names = [_validated_field_name(item, allow_wildcard=True) for item in value]
        deduped = list(dict.fromkeys(names))
        if not deduped:
            raise ValueError("candidate_fields was given but empty; omit it to sweep all fields")
        return deduped


class ListIndicesRequest(_ProbeRequest):
    max_indices: int = Field(default=DEFAULT_INDEX_BUCKETS, ge=1, le=MAX_INDEX_BUCKETS)


class CountMatchingRequest(_ProbeRequest):
    field: str = Field(min_length=1, max_length=MAX_FIELD_NAME_CHARS)
    phrase: str | None = Field(default=None, max_length=MAX_PHRASE_CHARS)
    exists_only: bool = False

    @field_validator("field")
    @classmethod
    def _check_field(cls, value: str) -> str:
        return _validated_field_name(value, allow_wildcard=False)

    @model_validator(mode="after")
    def _check_predicate(self) -> CountMatchingRequest:
        if self.phrase is not None and self.exists_only:
            raise ValueError("Give either a phrase or exists_only, not both")
        if self.phrase is None and not self.exists_only:
            raise ValueError("Give a phrase to count, or exists_only=true")
        if self.phrase is not None and not self.phrase.strip():
            raise ValueError("phrase cannot be blank")
        return self


class SizeDistributionRequest(_ProbeRequest):
    size_field: str = Field(min_length=1, max_length=MAX_FIELD_NAME_CHARS)
    group_by: str | None = Field(default=None, max_length=MAX_FIELD_NAME_CHARS)
    max_groups: int = Field(default=DEFAULT_GROUP_BUCKETS, ge=1, le=MAX_GROUP_BUCKETS)
    percents: list[float] | None = Field(default=None, max_length=MAX_PERCENTS)

    @field_validator("size_field", "group_by")
    @classmethod
    def _check_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validated_field_name(value, allow_wildcard=False)

    @field_validator("percents")
    @classmethod
    def _check_percents(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return None
        for item in value:
            if not 0.0 <= item <= 100.0:
                raise ValueError("Each percentile must be between 0 and 100")
        deduped = sorted(dict.fromkeys(value))
        if not deduped:
            raise ValueError("percents was given but empty; omit it for the defaults")
        return deduped


# --- responses ---------------------------------------------------------------
#
# Every field below is a scalar, an enum, a list of scalars, or a model built
# the same way. There is deliberately no `dict[str, Any]`, no `Any`, and no
# `raw`/`response` passthrough: invariant A is enforced by the SHAPE of these
# models as well as by the query bodies, so no future edit can reopen the
# channel without visibly adding an untyped field.


class _ProbeResponse(BaseModel):
    project_id: str
    source_id: int
    index_pattern: str
    resolution: ProbeResolution
    #: True when the search reported a timeout or failed shards, which makes
    #: every count in the response a LOWER BOUND rather than a fact.
    partial: bool = False
    notes: list[str] = Field(default_factory=list)


class ProbedField(BaseModel):
    """One field's discovered metadata.

    `mapped` is a tri-state on purpose: True and False are findings, None means
    the question could not be answered with the privileges available. Collapsing
    None into False is how a permissions gap turns into a false claim that a
    field does not exist.
    """

    name: str
    types: list[str] = Field(default_factory=list)
    searchable: bool | None = None
    aggregatable: bool | None = None
    mapped: bool | None = None
    evidence: FieldEvidence
    #: Only populated by the `exists_probe` fallback.
    matching_documents: int | None = None


class DescribeFieldsResponse(_ProbeResponse):
    method: Literal["field_caps", "candidate_probe"]
    field_caps_refused: bool
    document_count: int
    fields: list[ProbedField] = Field(default_factory=list)
    truncated: bool = False


class IndexBucket(BaseModel):
    index: str
    document_count: int


class ListIndicesResponse(_ProbeResponse):
    document_count: int
    indices: list[IndexBucket] = Field(default_factory=list)
    #: Documents in indices beyond `max_indices` (the terms agg's
    #: `sum_other_doc_count`). Non-zero means the list is not the whole estate.
    documents_outside_listed_indices: int = 0
    truncated: bool = False


class CountMatchingResponse(_ProbeResponse):
    field: str
    predicate: Literal["match_phrase", "exists"]
    phrase: str | None = None
    #: None -- never 0 -- whenever `resolution` is not `ok`. See invariant B.
    matching_documents: int | None = None
    #: Every document under the pattern, so a caller can state a proportion.
    document_count: int
    #: `matching_documents / document_count`, rounded, or None when the
    #: numerator is not a fact.
    matching_fraction: float | None = None
    field_mapped: bool | None = None
    field_evidence: FieldEvidence | None = None


class PercentileValue(BaseModel):
    percentile: float
    #: None when Elasticsearch had no values to compute from.
    value: float | None = None


class SizeRangeBucket(BaseModel):
    key: str
    from_value: float | None = None
    to_value: float | None = None
    document_count: int


class SizeSummary(BaseModel):
    document_count: int
    min_value: float | None = None
    max_value: float | None = None
    avg_value: float | None = None
    sum_value: float | None = None
    percentiles: list[PercentileValue] = Field(default_factory=list)
    buckets: list[SizeRangeBucket] = Field(default_factory=list)


class SizeGroupSummary(SizeSummary):
    key: str


class SizeDistributionResponse(_ProbeResponse):
    size_field: str
    group_by: str | None = None
    #: Every document under the pattern, whether or not it carries the field.
    document_count: int
    #: Documents where `size_field` exists -- the population every statistic
    #: below is computed over.
    measured_documents: int
    overall: SizeSummary
    groups: list[SizeGroupSummary] = Field(default_factory=list)
    groups_truncated: bool = False
    size_field_mapped: bool | None = None
    group_by_mapped: bool | None = None
    field_evidence: FieldEvidence | None = None


# --- HTTP client -------------------------------------------------------------


class _ProbeClient:
    """A read-only Elasticsearch client that cannot ask for documents.

    One client per probe call. `search` is the only query entry point and it
    rewrites `size`/`_source` on every body it is handed, so an invariant-A
    mistake cannot be made at a call site.
    """

    def __init__(
        self,
        config: ElasticSourceConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        headers = {"Accept": "application/json"}
        if config.auth_header:
            headers["Authorization"] = config.auth_header
        self._client = httpx.AsyncClient(
            base_url=config.endpoint_url,
            timeout=PROBE_TIMEOUT_SECONDS,
            transport=transport,
            headers=headers,
            verify=_tls_verify_for(config),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def search(self, index_expression: str, body: dict[str, Any]) -> dict[str, Any]:
        """Issue one aggregate-only search and refuse any document it returns."""
        guarded = dict(body)
        guarded["size"] = 0
        guarded["_source"] = False
        guarded.setdefault("timeout", ES_QUERY_TIMEOUT)
        payload = await self._request(
            "POST",
            f"/{_escape_index_expression(index_expression)}/_search",
            params={"ignore_unavailable": "true", "allow_no_indices": "true"},
            json_body=guarded,
        )
        _assert_no_documents(payload)
        return payload

    async def field_caps(
        self, index_expression: str, fields: Sequence[str] | None
    ) -> dict[str, Any]:
        """`_field_caps`, the door that stays open when `_mapping` is 403."""
        expression = ",".join(fields) if fields else "*"
        return await self._request(
            "GET",
            f"/{_escape_index_expression(index_expression)}/_field_caps",
            params={
                "fields": expression,
                "ignore_unavailable": "true",
                "allow_no_indices": "true",
            },
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, params=params or {}, json=json_body
            )
        except httpx.TimeoutException as exc:
            raise _ElasticHttpError(
                f"Elasticsearch {method} {path} timed out", status_code=None
            ) from exc
        except httpx.TransportError as exc:
            raise _ElasticHttpError(
                f"Elasticsearch {method} {path} transport failure: {type(exc).__name__}",
                status_code=None,
            ) from exc
        except ValueError as exc:
            # httpx encodes header values as ASCII; a homoglyph pasted into a
            # token must not surface as an unhandled 500.
            raise _ElasticHttpError(
                f"Elasticsearch {method} {path} could not be encoded", status_code=None
            ) from exc
        if response.status_code >= 400:
            raise _ElasticHttpError(
                f"Elasticsearch {method} {path} returned HTTP {response.status_code}",
                status_code=response.status_code,
                error_type=_error_type(response),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise _ElasticHttpError(
                f"Elasticsearch {method} {path} returned a non-JSON payload", status_code=None
            ) from exc
        if not isinstance(payload, dict):
            raise _ElasticHttpError(
                f"Elasticsearch {method} {path} returned a non-object payload", status_code=None
            )
        return payload


def _escape_index_expression(expression: str) -> str:
    """Percent-escape an already-allow-listed index expression for the path."""
    return quote(expression, safe="*-_.+")


def _error_type(response: httpx.Response) -> str | None:
    """Extract only Elasticsearch's short error TYPE token from a failure body.

    The `reason` is deliberately NOT read: it can quote the offending document
    or query text, and this module's whole posture is that nothing derived from
    document content leaves the boundary. A type token
    (`illegal_argument_exception`, `security_exception`) is metadata and is what
    distinguishes "wrong field type" from "forbidden".
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, str):
        # Some proxies flatten the error to a string. Only accept it when it
        # already looks like a bare token.
        return error[:64] if re.fullmatch(r"[a-z_]{1,64}", error) else None
    if isinstance(error, dict):
        kind = error.get("type")
        if isinstance(kind, str):
            return kind[:64]
    return None


def _assert_no_documents(payload: dict[str, Any]) -> None:
    """Invariant A, enforced on the wire rather than trusted."""
    hits = payload.get("hits")
    if not isinstance(hits, dict):
        return
    returned = hits.get("hits")
    if isinstance(returned, list) and returned:
        raise ElasticProbeContractError(
            "Elasticsearch returned document bodies for a probe query that set "
            "size=0 and _source=false. The probe refuses the response rather "
            "than filtering it: something between this service and the cluster "
            "is rewriting queries, and no document content may cross this "
            "boundary."
        )


# --- payload readers ---------------------------------------------------------
# Small, total, and deliberately defensive: a missing or oddly-shaped key gives
# a None or a zero, never an exception, because every one of these is optional
# on some Elasticsearch version or some proxy.


def _total_hits(payload: dict[str, Any]) -> int:
    hits = payload.get("hits")
    if not isinstance(hits, dict):
        return 0
    total = hits.get("total")
    if isinstance(total, dict):
        value = total.get("value")
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        return int(total)
    return 0


def _is_partial(payload: dict[str, Any]) -> bool:
    if payload.get("timed_out") is True:
        return True
    shards = payload.get("_shards")
    if isinstance(shards, dict):
        failed = shards.get("failed")
        if isinstance(failed, (int, float)) and not isinstance(failed, bool) and failed > 0:
            return True
    return False


def _aggregation(payload: dict[str, Any], *path: str) -> dict[str, Any]:
    node = payload.get("aggregations")
    if not isinstance(node, dict):
        return {}
    for key in path:
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            return {}
        node = nxt
    return node


def _doc_count(node: dict[str, Any]) -> int:
    value = node.get("doc_count")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# --- the service -------------------------------------------------------------


class ElasticProbeService:
    """The four primitives, over one project's stored Elasticsearch credential."""

    def __init__(
        self,
        db: Any,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._db = db
        self._transport = transport

    # -- credential resolution ------------------------------------------------

    async def _resolve_source(
        self, project_id: str, source_id: int | None
    ) -> tuple[int, ElasticSourceConfig]:
        """Find the project's Elasticsearch credential, or say exactly why not.

        Scope is pinned to the requested project, the narrowest possible read: a
        machine key has no user behind it to derive a scope from. Read GRANTS
        deliberately do NOT widen this. A grant entitles a second project to
        read the MIRROR of a shared tenant; it is not a licence to issue live
        queries with somebody else's credential.
        """
        repo = RuntimeStoreRepository(self._db)
        if source_id is not None:
            try:
                source = await repo.get_source_config(source_id, [project_id])
            except RuntimeRecordNotFoundError as exc:
                raise ElasticProbeSourceError(
                    f"Runtime source {source_id} is not registered to project '{project_id}'"
                ) from exc
            if source.source_type != ELASTIC_PROVIDER_KEY:
                raise ElasticProbeSourceError(
                    f"Runtime source {source_id} is a '{source.source_type}' source, "
                    "not an Elasticsearch source"
                )
            return source.id, await self._load_config(repo, source.id)

        sources = sorted(
            (
                item
                for item in await repo.list_source_configs(
                    [project_id], project_id=project_id, limit=200
                )
                if item.source_type == ELASTIC_PROVIDER_KEY
            ),
            key=lambda item: item.id,
        )
        credentialled: list[int] = []
        for item in sources:
            try:
                await repo.read_credentials(item.id)
            except RuntimeRecordNotFoundError:
                # A mirror row written by a sync batch, not a registered
                # credential. It cannot be probed and is not a candidate.
                continue
            credentialled.append(item.id)
        if not credentialled:
            raise ElasticProbeSourceError(
                f"Project '{project_id}' has no registered Elasticsearch source to probe"
            )
        if len(credentialled) > 1:
            raise ElasticProbeSourceError(
                f"Project '{project_id}' has {len(credentialled)} Elasticsearch sources "
                f"({', '.join(str(item) for item in credentialled)}); name one with source_id"
            )
        return credentialled[0], await self._load_config(repo, credentialled[0])

    async def _load_config(
        self, repo: RuntimeStoreRepository, source_id: int
    ) -> ElasticSourceConfig:
        credentials, _key_id = await repo.read_credentials(source_id)
        try:
            return parse_elastic_config(credentials)
        except RuntimeProviderError as exc:
            raise ElasticProbeSourceError(
                f"Elasticsearch source {source_id} has an unusable configuration: "
                f"{_safe_error(str(exc))}"
            ) from exc

    # -- primitive 1: describe-fields ----------------------------------------

    async def describe_fields(self, request: DescribeFieldsRequest) -> DescribeFieldsResponse:
        source_id, config = await self._resolve_source(request.project_id, request.source_id)
        async with _ProbeClient(config, transport=self._transport) as client:
            total_payload = await client.search(
                request.index_pattern,
                {"track_total_hits": True, "query": {"match_all": {}}},
            )
            document_count = _total_hits(total_payload)
            partial = _is_partial(total_payload)
            discovery = await self._discover_fields(
                client,
                index_pattern=request.index_pattern,
                candidates=request.candidate_fields,
                pattern_has_documents=document_count > 0,
            )

        notes = list(discovery.notes)
        if document_count == 0:
            resolution = ProbeResolution.PATTERN_MATCHED_NO_INDICES
            notes.append(
                "No document is visible under this pattern with these credentials: "
                "either no index matches it, or every match is empty. Field "
                "existence cannot be settled from an empty population."
            )
        elif discovery.refused and not discovery.fields:
            resolution = ProbeResolution.FIELD_MAPPING_UNKNOWN
        else:
            resolution = ProbeResolution.OK
        return DescribeFieldsResponse(
            project_id=request.project_id,
            source_id=source_id,
            index_pattern=request.index_pattern,
            resolution=resolution,
            partial=partial,
            notes=notes,
            method="candidate_probe" if discovery.refused else "field_caps",
            field_caps_refused=discovery.refused,
            document_count=document_count,
            fields=discovery.fields,
            truncated=discovery.truncated,
        )

    # -- primitive 2: list-indices -------------------------------------------

    async def list_indices(self, request: ListIndicesRequest) -> ListIndicesResponse:
        """Recover the concrete indices behind a pattern or alias.

        Through a `terms` aggregation on the `_index` METADATA field, never via
        `_alias`, `_cat/indices`, `_resolve/index` or `_mapping`. On the estate
        this was built for, every one of those was forbidden while `_search` was
        not -- the door was closed and the window was open.
        """
        source_id, config = await self._resolve_source(request.project_id, request.source_id)
        async with _ProbeClient(config, transport=self._transport) as client:
            payload = await client.search(
                request.index_pattern,
                {
                    "track_total_hits": True,
                    "query": {"match_all": {}},
                    "aggs": {
                        "indices": {
                            "terms": {"field": "_index", "size": request.max_indices}
                        }
                    },
                },
            )

        document_count = _total_hits(payload)
        node = _aggregation(payload, "indices")
        raw_buckets = node.get("buckets")
        buckets = [
            IndexBucket(index=str(item.get("key")), document_count=_doc_count(item))
            for item in (raw_buckets if isinstance(raw_buckets, list) else [])
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        ]
        other = node.get("sum_other_doc_count")
        outside = int(other) if isinstance(other, (int, float)) and not isinstance(other, bool) else 0

        notes: list[str] = []
        if not buckets:
            # `_index` is a metadata field present on every document, so zero
            # buckets is never an unmapped-field artefact here. It means the
            # population itself is empty -- which is still reported explicitly
            # rather than as an empty list a caller could read as "no indices
            # exist".
            resolution = ProbeResolution.PATTERN_MATCHED_NO_INDICES
            notes.append(
                "No document is visible under this pattern with these credentials. "
                "`_index` is a metadata field on every document, so this is an "
                "empty population, not an unmapped field."
            )
        else:
            resolution = ProbeResolution.OK
        if outside:
            notes.append(
                f"{outside} documents live in indices beyond the {request.max_indices} "
                "listed; raise max_indices to enumerate them."
            )
        return ListIndicesResponse(
            project_id=request.project_id,
            source_id=source_id,
            index_pattern=request.index_pattern,
            resolution=resolution,
            partial=_is_partial(payload),
            notes=notes,
            document_count=document_count,
            indices=buckets,
            documents_outside_listed_indices=outside,
            truncated=bool(outside),
        )

    # -- primitive 3: count-matching -----------------------------------------

    async def count_matching(self, request: CountMatchingRequest) -> CountMatchingResponse:
        """Count, never retrieve.

        This is the primitive that separates "this field is mapped" from "this
        field is empty in 92.8% of messages", so it returns the denominator as
        well as the numerator: a bare numerator cannot make that statement.
        """
        source_id, config = await self._resolve_source(request.project_id, request.source_id)
        predicate: dict[str, Any]
        if request.exists_only:
            predicate = {"exists": {"field": request.field}}
            predicate_kind: Literal["match_phrase", "exists"] = "exists"
        else:
            predicate = {"match_phrase": {request.field: request.phrase}}
            predicate_kind = "match_phrase"

        async with _ProbeClient(config, transport=self._transport) as client:
            payload = await client.search(
                request.index_pattern,
                {
                    "track_total_hits": True,
                    "query": {"match_all": {}},
                    "aggs": {"matching": {"filter": predicate}},
                },
            )
            document_count = _total_hits(payload)
            matching = _doc_count(_aggregation(payload, "matching"))
            partial = _is_partial(payload)

            mapped: bool | None = None
            evidence: FieldEvidence | None = None
            notes: list[str] = []
            if matching == 0 or document_count == 0:
                # INVARIANT B. A zero here has three possible meanings and the
                # caller must be told which one it got, so the mapping is
                # resolved before any zero is returned.
                discovery = await self._discover_fields(
                    client,
                    index_pattern=request.index_pattern,
                    candidates=[request.field],
                    pattern_has_documents=document_count > 0,
                )
                notes.extend(discovery.notes)
                found = next((item for item in discovery.fields if item.name == request.field), None)
                if found is not None:
                    mapped = found.mapped
                    evidence = found.evidence

        resolution = ProbeResolution.OK
        reported: int | None = matching
        fraction: float | None = None
        if document_count == 0:
            resolution = ProbeResolution.PATTERN_MATCHED_NO_INDICES
            reported = None
            notes.append(
                "No document is visible under this pattern, so the count describes "
                "an empty population rather than the field."
            )
        elif matching == 0 and mapped is False:
            resolution = ProbeResolution.FIELD_UNMAPPED
            reported = None
            notes.append(
                f"'{request.field}' is not mapped in any index matching this pattern. "
                "The count is reported as null, not 0, because 0 would read as "
                "'the field exists and nothing matched'."
            )
        elif matching == 0 and mapped is None:
            resolution = ProbeResolution.FIELD_MAPPING_UNKNOWN
            reported = None
            notes.append(
                f"'{request.field}' matched no document and these credentials could "
                "not establish whether it is mapped, so the zero is genuinely "
                "ambiguous and is reported as null."
            )
        elif document_count:
            fraction = round(matching / document_count, 6)

        return CountMatchingResponse(
            project_id=request.project_id,
            source_id=source_id,
            index_pattern=request.index_pattern,
            resolution=resolution,
            partial=partial,
            notes=notes,
            field=request.field,
            predicate=predicate_kind,
            phrase=request.phrase,
            matching_documents=reported,
            document_count=document_count,
            matching_fraction=fraction,
            field_mapped=mapped if matching == 0 or document_count == 0 else True,
            field_evidence=evidence,
        )

    # -- primitive 4: size-distribution --------------------------------------

    async def size_distribution(
        self, request: SizeDistributionRequest
    ) -> SizeDistributionResponse:
        """Establish that payloads are PRESENT without ever retrieving one.

        An envelope is a few hundred bytes; a real business message is tens of
        kilobytes. Percentiles and byte buckets of a numeric size field settle
        which of the two an estate is carrying, and no document body is read to
        do it.
        """
        source_id, config = await self._resolve_source(request.project_id, request.source_id)
        percents = list(request.percents or DEFAULT_PERCENTS)
        stats_aggs = _distribution_aggs(request.size_field, percents)
        aggs: dict[str, Any] = {
            "measured": {
                "filter": {"exists": {"field": request.size_field}},
                "aggs": dict(stats_aggs),
            }
        }
        if request.group_by:
            aggs["measured"]["aggs"]["groups"] = {
                "terms": {"field": request.group_by, "size": request.max_groups},
                "aggs": dict(stats_aggs),
            }

        notes: list[str] = []
        async with _ProbeClient(config, transport=self._transport) as client:
            try:
                payload = await client.search(
                    request.index_pattern,
                    {"track_total_hits": True, "query": {"match_all": {}}, "aggs": aggs},
                )
            except _ElasticHttpError as exc:
                if exc.status_code == 400:
                    # A numeric aggregation over a non-numeric field. The field
                    # exists; it just cannot carry this question -- which is a
                    # different finding from "no data" and must not read as one.
                    return SizeDistributionResponse(
                        project_id=request.project_id,
                        source_id=source_id,
                        index_pattern=request.index_pattern,
                        resolution=ProbeResolution.FIELD_NOT_AGGREGATABLE,
                        notes=[
                            f"Elasticsearch refused a numeric aggregation over "
                            f"'{request.size_field}'"
                            + (f" ({exc.error_type})" if exc.error_type else "")
                            + ". Check the field's type with describe-fields.",
                        ],
                        size_field=request.size_field,
                        group_by=request.group_by,
                        document_count=0,
                        measured_documents=0,
                        overall=SizeSummary(document_count=0),
                    )
                raise

            document_count = _total_hits(payload)
            partial = _is_partial(payload)
            measured_node = _aggregation(payload, "measured")
            measured = _doc_count(measured_node)

            size_mapped: bool | None = None
            group_mapped: bool | None = None
            evidence: FieldEvidence | None = None
            groups = _parse_groups(measured_node, percents)
            if measured == 0 or (request.group_by and not groups):
                # INVARIANT B again, and this is the exact trap named in the
                # brief: a `terms` agg on an unmapped field returns zero buckets
                # rather than an error, and `exists` on one returns zero hits.
                wanted = [request.size_field] + ([request.group_by] if request.group_by else [])
                discovery = await self._discover_fields(
                    client,
                    index_pattern=request.index_pattern,
                    candidates=wanted,
                    pattern_has_documents=document_count > 0,
                )
                notes.extend(discovery.notes)
                by_name = {item.name: item for item in discovery.fields}
                found = by_name.get(request.size_field)
                if found is not None:
                    size_mapped = found.mapped
                    evidence = found.evidence
                if request.group_by:
                    grouped = by_name.get(request.group_by)
                    if grouped is not None:
                        group_mapped = grouped.mapped
                        evidence = evidence or grouped.evidence

        unmapped = [
            name
            for name, mapped in (
                (request.size_field, size_mapped),
                (request.group_by, group_mapped),
            )
            if name is not None and mapped is False
        ]
        unknown = [
            name
            for name, mapped in (
                (request.size_field, size_mapped),
                (request.group_by, group_mapped),
            )
            if name is not None and mapped is None and measured == 0
        ]
        if document_count == 0:
            resolution = ProbeResolution.PATTERN_MATCHED_NO_INDICES
            notes.append(
                "No document is visible under this pattern, so there is no "
                "population to describe."
            )
        elif unmapped:
            resolution = ProbeResolution.FIELD_UNMAPPED
            notes.append(
                f"Not mapped in any index matching this pattern: {', '.join(unmapped)}. "
                "An aggregation over an unmapped field returns zero buckets rather "
                "than an error, so this is reported as an unmapped field and not as "
                "a distribution of zero."
            )
        elif unknown:
            resolution = ProbeResolution.FIELD_MAPPING_UNKNOWN
            notes.append(
                f"No values found and the mapping could not be established for: "
                f"{', '.join(unknown)}. The empty distribution is ambiguous."
            )
        else:
            resolution = ProbeResolution.OK

        return SizeDistributionResponse(
            project_id=request.project_id,
            source_id=source_id,
            index_pattern=request.index_pattern,
            resolution=resolution,
            partial=partial,
            notes=notes,
            size_field=request.size_field,
            group_by=request.group_by,
            document_count=document_count,
            measured_documents=measured,
            overall=_parse_summary(measured_node, measured, percents),
            groups=groups,
            groups_truncated=_groups_truncated(measured_node),
            size_field_mapped=size_mapped if measured == 0 else True,
            # Buckets exist only for a mapped field, so their presence settles
            # the question without a second lookup.
            group_by_mapped=True if groups else group_mapped,
            field_evidence=evidence,
        )

    # -- shared field discovery ----------------------------------------------

    async def _discover_fields(
        self,
        client: _ProbeClient,
        *,
        index_pattern: str,
        candidates: Sequence[str] | None,
        pattern_has_documents: bool,
    ) -> _FieldDiscovery:
        """`_field_caps` first, candidate probing when it is refused.

        The fallback is the whole reason this is a shared helper rather than
        inline in `describe_fields`: invariant B's mapping resolution and the
        describe-fields primitive must answer the same question the same way, or
        a count could report `field_unmapped` for a field describe-fields calls
        present.
        """
        try:
            payload = await client.field_caps(index_pattern, candidates)
        except _ElasticHttpError as exc:
            if not exc.refused:
                raise
            logger.info(
                "Elasticsearch _field_caps refused (HTTP %s); falling back to candidate probing",
                exc.status_code,
            )
            return await self._probe_candidates(
                client, index_pattern=index_pattern, candidates=candidates, status=exc.status_code
            )

        raw = payload.get("fields")
        fields_payload = raw if isinstance(raw, dict) else {}
        names = sorted(str(name) for name in fields_payload)
        truncated = len(names) > MAX_FIELDS_RETURNED
        fields = [
            _probed_field_from_caps(name, fields_payload.get(name))
            for name in names[:MAX_FIELDS_RETURNED]
        ]
        if candidates:
            present = {item.name for item in fields}
            for name in candidates:
                if name in present or "*" in name:
                    continue
                fields.append(
                    ProbedField(
                        name=name,
                        mapped=False if pattern_has_documents else None,
                        evidence=FieldEvidence.FIELD_CAPS,
                    )
                )
        notes: list[str] = []
        if truncated:
            notes.append(
                f"{len(names)} fields are mapped; the first {MAX_FIELDS_RETURNED} are "
                "listed. Pass candidate_fields to ask about specific names."
            )
        return _FieldDiscovery(fields=fields, refused=False, truncated=truncated, notes=notes)

    async def _probe_candidates(
        self,
        client: _ProbeClient,
        *,
        index_pattern: str,
        candidates: Sequence[str] | None,
        status: int | None,
    ) -> _FieldDiscovery:
        """The door-closed path: ask each candidate name whether it resolves.

        An `exists` count that matches is PROOF the name resolves. An `exists`
        count of zero proves nothing at all -- unmapped and mapped-but-empty are
        indistinguishable without the mapping -- so it is recorded as
        inconclusive rather than as absence.
        """
        notes = [
            (
                f"_field_caps was refused (HTTP {status}); field existence was probed "
                "with bounded exists counts instead."
            )
        ]
        if not candidates:
            notes.append(
                "No candidate_fields were supplied, so there is nothing to probe: "
                "without _field_caps a field name cannot be discovered, only "
                "confirmed. Supply candidate_fields."
            )
            return _FieldDiscovery(fields=[], refused=True, truncated=False, notes=notes)

        fields: list[ProbedField] = []
        for name in list(candidates)[:MAX_CANDIDATE_FIELDS]:
            if "*" in name:
                # A wildcard cannot be confirmed by an exists probe: `exists`
                # takes one concrete field.
                notes.append(f"'{name}' is a wildcard and cannot be probed without _field_caps.")
                continue
            payload = await client.search(
                index_pattern,
                {
                    "track_total_hits": True,
                    "query": {"bool": {"filter": [{"exists": {"field": name}}]}},
                },
            )
            count = _total_hits(payload)
            fields.append(
                ProbedField(
                    name=name,
                    mapped=True if count > 0 else None,
                    evidence=(
                        FieldEvidence.EXISTS_PROBE
                        if count > 0
                        else FieldEvidence.EXISTS_PROBE_INCONCLUSIVE
                    ),
                    matching_documents=count,
                )
            )
        return _FieldDiscovery(fields=fields, refused=True, truncated=False, notes=notes)


class _FieldDiscovery(BaseModel):
    fields: list[ProbedField] = Field(default_factory=list)
    refused: bool = False
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)


# --- aggregation helpers -----------------------------------------------------


def _distribution_aggs(size_field: str, percents: Sequence[float]) -> dict[str, Any]:
    return {
        "percentiles": {
            "percentiles": {"field": size_field, "percents": list(percents)}
        },
        "stats": {"stats": {"field": size_field}},
        "ranges": {
            "range": {
                "field": size_field,
                "ranges": [
                    {
                        "key": key,
                        **({} if low is None else {"from": low}),
                        **({} if high is None else {"to": high}),
                    }
                    for key, low, high in _SIZE_RANGES
                ],
            }
        },
    }


def _probed_field_from_caps(name: str, body: object) -> ProbedField:
    """Fold `_field_caps`' per-type detail into one row.

    A field can be reported under several types across the indices a pattern
    spans (`text` in one, `keyword` in another). `searchable`/`aggregatable` are
    reported as True when ANY type offers the capability, because that is the
    question a caller is asking: can I run this query at all.
    """
    if not isinstance(body, dict):
        return ProbedField(name=name, mapped=True, evidence=FieldEvidence.FIELD_CAPS)
    types: list[str] = []
    searchable = False
    aggregatable = False
    for type_key, detail in body.items():
        if not isinstance(detail, dict):
            continue
        kind = detail.get("type")
        types.append(str(kind) if isinstance(kind, str) else str(type_key))
        searchable = searchable or detail.get("searchable") is True
        aggregatable = aggregatable or detail.get("aggregatable") is True
    return ProbedField(
        name=name,
        types=sorted(dict.fromkeys(types)),
        searchable=searchable,
        aggregatable=aggregatable,
        mapped=True,
        evidence=FieldEvidence.FIELD_CAPS,
    )


def _parse_percentiles(node: dict[str, Any], percents: Sequence[float]) -> list[PercentileValue]:
    values = node.get("values")
    parsed: dict[float, float | None] = {}
    if isinstance(values, dict):
        for key, value in values.items():
            try:
                parsed[float(key)] = _number(value)
            except (TypeError, ValueError):
                continue
    return [PercentileValue(percentile=item, value=parsed.get(item)) for item in percents]


def _parse_ranges(node: dict[str, Any]) -> list[SizeRangeBucket]:
    raw = node.get("buckets")
    buckets: list[SizeRangeBucket] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        buckets.append(
            SizeRangeBucket(
                key=str(item.get("key", "")),
                from_value=_number(item.get("from")),
                to_value=_number(item.get("to")),
                document_count=_doc_count(item),
            )
        )
    return buckets


def _parse_summary(
    node: dict[str, Any], document_count: int, percents: Sequence[float]
) -> SizeSummary:
    stats = node.get("stats") if isinstance(node.get("stats"), dict) else {}
    percentiles_node = node.get("percentiles") if isinstance(node.get("percentiles"), dict) else {}
    ranges_node = node.get("ranges") if isinstance(node.get("ranges"), dict) else {}
    return SizeSummary(
        document_count=document_count,
        min_value=_number(stats.get("min")),
        max_value=_number(stats.get("max")),
        avg_value=_number(stats.get("avg")),
        sum_value=_number(stats.get("sum")),
        percentiles=_parse_percentiles(percentiles_node, percents),
        buckets=_parse_ranges(ranges_node),
    )


def _parse_groups(measured: dict[str, Any], percents: Sequence[float]) -> list[SizeGroupSummary]:
    node = measured.get("groups")
    if not isinstance(node, dict):
        return []
    raw = node.get("buckets")
    groups: list[SizeGroupSummary] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        summary = _parse_summary(item, _doc_count(item), percents)
        groups.append(SizeGroupSummary(key=str(item.get("key", "")), **summary.model_dump()))
    return groups


def _groups_truncated(measured: dict[str, Any]) -> bool:
    node = measured.get("groups")
    if not isinstance(node, dict):
        return False
    other = node.get("sum_other_doc_count")
    return isinstance(other, (int, float)) and not isinstance(other, bool) and other > 0


def _safe_error(message: str) -> str:
    """Redact credentials and entity shapes from any text this module surfaces."""
    return sanitize_sync_error(message) or "Elasticsearch could not be reached"
