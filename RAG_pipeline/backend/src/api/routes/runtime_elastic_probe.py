"""Read-only Elasticsearch probe primitives, `POST /runtime/elastic/probe/*`.

Four endpoints, one posture. They exist because a real production estate was
characterised through a connector key that could not call `_mapping` and could
not list aliases, and the moves that worked -- `_field_caps` for field
discovery, a `_index` terms aggregation for the concrete indices behind an
alias, counts for emptiness, size percentiles for payload presence -- are
generic enough to belong in the product.

Same auth as `GET /runtime/doc-evidence` and `GET /runtime/elastic-aggregates`:
the MCP machine ingest key, not a user session, because the caller is a
documentation run or an agent tool rather than a person. Scope is pinned to the
`project_id` in the body, which is the narrowest possible read for a key with no
user behind it. There is deliberately no parameter that widens scope.

Unlike its two siblings this lane reads the CLUSTER, not the mirror. That is the
point: the probe answers questions about an estate before deciding what to
mirror, so routing it through the mirror would make it unable to do its job. It
is bounded accordingly (see `src/runtime/elastic_probe.py`) and it never returns
document content -- counts, percentiles, field metadata and bucket keys only.

One caveat a caller must know about
-----------------------------------
These are POST routes, so `CSRFMiddleware` applies and a request without the
double-submit pair (`X-CSRF-Token` matching the `csrf_token` cookie) is refused
with 403 before it reaches machine auth. The two sibling machine endpoints are
GETs and never met this. A non-browser client can satisfy it by sending both
values itself, and the tests do, but the correct fix is to add
``/api/v1/runtime/elastic/probe`` to `_EXEMPT_PREFIXES` in
`src/api/middleware/csrf.py` -- which is exactly why `/api/v1/ingest/` and
`/api/v1/labs/mcp` are already listed there: "server-to-server calls, no browser
cookie", so there is no cross-site request to forge. That edit belongs to the
owner of the middleware file and is deliberately not made here.

Status mapping:

* 404 -- the project has no registered Elasticsearch source, or the named
  `source_id` is not one of its Elasticsearch sources;
* 409 -- the project has more than one, so `source_id` must name which;
* 422 -- a malformed request (Pydantic), including an index expression that
  fails the ingest allow-list;
* 502 -- the cluster refused, timed out, or returned something this service
  will not pass on (notably document bodies for a `size: 0` query).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import DBSession
from src.api.routes.ingest import verify_machine_topology_ingest_api_key
from src.runtime.elastic_probe import (
    CountMatchingRequest,
    CountMatchingResponse,
    DescribeFieldsRequest,
    DescribeFieldsResponse,
    ElasticProbeError,
    ElasticProbeService,
    ElasticProbeSourceError,
    ListIndicesRequest,
    ListIndicesResponse,
    SizeDistributionRequest,
    SizeDistributionResponse,
)
from src.runtime.providers import RuntimeProviderError
from src.runtime.redaction import sanitize_sync_error

router = APIRouter(
    prefix="/runtime/elastic/probe",
    tags=["runtime"],
    dependencies=[Depends(verify_machine_topology_ingest_api_key)],
)


def _http_error(error: Exception) -> HTTPException:
    """Turn a probe failure into a status, with the text already redacted.

    `sanitize_sync_error` is applied at this single boundary rather than at each
    raise site, so no future error path can reach a client unredacted.
    """
    detail = sanitize_sync_error(str(error)) or "Elasticsearch could not be reached"
    if isinstance(error, ElasticProbeSourceError):
        # "has N Elasticsearch sources" is a conflict the caller resolves by
        # naming one; everything else in this class is a missing source.
        status = 409 if "source_id" in str(error) else 404
        return HTTPException(status_code=status, detail=detail)
    # Everything else -- a refusal, a timeout, and notably an
    # `ElasticProbeContractError` where the cluster returned document bodies for
    # a `size: 0` query -- is an upstream fault, not a bad request.
    return HTTPException(status_code=502, detail=detail)


@router.post(
    "/describe-fields",
    response_model=DescribeFieldsResponse,
    summary="Discover field names, types and capabilities without `_mapping`",
)
async def probe_describe_fields(
    body: DescribeFieldsRequest, db: DBSession
) -> DescribeFieldsResponse:
    """Field discovery through `_field_caps`, with a candidate-probe fallback.

    `_mapping` is commonly forbidden to a connector key while `_field_caps` is
    not. When `_field_caps` is refused too, each supplied candidate name is
    probed with a bounded `exists` count: a match PROVES the name resolves, and
    a non-match proves nothing and is reported as inconclusive rather than as
    absence.
    """
    try:
        return await ElasticProbeService(db).describe_fields(body)
    except (ElasticProbeError, RuntimeProviderError) as error:
        raise _http_error(error) from error


@router.post(
    "/list-indices",
    response_model=ListIndicesResponse,
    summary="Recover the concrete indices behind a pattern or alias",
)
async def probe_list_indices(body: ListIndicesRequest, db: DBSession) -> ListIndicesResponse:
    """Aggregate on the `_index` metadata field, not on an index-metadata API.

    `_alias`, `_cat/indices`, `_resolve/index` and `_mapping` are all commonly
    forbidden to a connector key; `_search` is what such a key is issued for.
    Every document carries `_index`, so a terms aggregation over it recovers the
    concrete index set through the door that is open.
    """
    try:
        return await ElasticProbeService(db).list_indices(body)
    except (ElasticProbeError, RuntimeProviderError) as error:
        raise _http_error(error) from error


@router.post(
    "/count-matching",
    response_model=CountMatchingResponse,
    summary="Count documents matching a phrase, or carrying a field at all",
)
async def probe_count_matching(body: CountMatchingRequest, db: DBSession) -> CountMatchingResponse:
    """A count and its denominator -- never a document.

    This is what separates "this field is mapped" from "this field is empty in
    92.8% of messages", so the response carries both the matching count and the
    population it was counted against. A zero is never returned bare: the field
    is resolved first and the answer arrives as `resolution`.
    """
    try:
        return await ElasticProbeService(db).count_matching(body)
    except (ElasticProbeError, RuntimeProviderError) as error:
        raise _http_error(error) from error


@router.post(
    "/size-distribution",
    response_model=SizeDistributionResponse,
    summary="Percentiles and byte buckets of a numeric size field",
)
async def probe_size_distribution(
    body: SizeDistributionRequest, db: DBSession
) -> SizeDistributionResponse:
    """Establish that payloads are PRESENT without ever retrieving one.

    An envelope is a few hundred bytes; a real business message is tens of
    kilobytes. Percentiles plus fixed byte buckets settle which of the two an
    estate is carrying, optionally split by a grouping field, and no document
    body is read to do it.
    """
    try:
        return await ElasticProbeService(db).size_distribution(body)
    except (ElasticProbeError, RuntimeProviderError) as error:
        raise _http_error(error) from error
