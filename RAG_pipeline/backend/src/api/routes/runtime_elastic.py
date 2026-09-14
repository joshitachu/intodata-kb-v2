"""Elastic log-estate aggregates for a documentation run.

`GET /runtime/elastic-aggregates` serves the `elastic-aggregates-1` body the
enterprise Talend documentation method already accepts as an input contract
(`providers/custom/runtime_authority.py::ElasticAggregatePayload`). Until this
route existed, `enterprise/phase_runner.py` passed `None` for it and the Elastic
authority was permanently `unavailable` on every run.

It is a sibling of `GET /runtime/doc-evidence` and deliberately identical in
posture -- one machine endpoint per authority, one scoping rule for both:

* **Same auth.** The MCP ingest key (`verify_machine_topology_ingest_api_key`),
  not a user session. There is no new credential.
* **Same scope resolution.** `resolve_runtime_read_projects` then
  `resolve_doc_read_scope`, so `doc_environment_ids` / `doc_window_days` decide
  what a documentation run reads out of the mirror, and widening comes only from
  stored read grants. Having no user behind the key, scope is pinned to the
  requested project -- the narrowest possible -- rather than left unrestricted.
* **No caller-supplied scope.** There is no `units`, `environment_ids`,
  `project_ids` or `source_key` parameter, and adding one would be an
  authorization bypass with no second gate behind it. The environment filter is
  applied server-side, once, to the whole population.

The response body is the contract object at the TOP LEVEL, with no envelope: the
consumer hands the JSON straight to `fuse_runtime_authorities`, whose models set
`extra="forbid"`, so an added envelope field would be rejected wholesale.

A project with no mirrored Elastic data returns 200 with a WELL-FORMED payload
whose `limitations` state which absence it is -- not 404, and not an empty body.
`_fuse_elastic` reads an empty `per_job` as `unavailable`, which is correct; a
404 would be indistinguishable from an unreachable backend, and a bare empty
payload would read as a healthy estate that produced nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import DBSession
from src.api.routes.ingest import verify_machine_topology_ingest_api_key
from src.runtime.elastic_aggregates import (
    ElasticAggregatePayload,
    ElasticAggregateService,
)

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get(
    "/elastic-aggregates",
    response_model=ElasticAggregatePayload,
    dependencies=[Depends(verify_machine_topology_ingest_api_key)],
    summary="Normalized elastic-aggregates-1 payload for a project's mirrored log estate",
)
async def get_runtime_elastic_aggregates(
    db: DBSession,
    project_id: str,
    window_days: int = Query(default=90, ge=1, le=365),
) -> ElasticAggregatePayload:
    """Reduce this project's mirrored Elastic estate to `elastic-aggregates-1`.

    Reads the runtime mirror, never Elasticsearch: the log estate is polled by
    the Elastic source connector on its own schedule, and keeping the cluster
    out of this path is what stops a documentation run from acquiring a second
    live dependency.

    `window_days` is a DEFAULT, not a ceiling. A project with a configured
    `doc_window_days` gets that window, because it is the operator's deliberate
    statement about how much history describes this estate; the response's
    `window_days` always reports the window actually applied.

    Aggregates only. Counts are of MESSAGES, never of executions; percentiles are
    real nearest-rank percentiles over mirrored durations and are null rather
    than approximated when none exist; and no log body, error text, execution id
    or credential crosses this boundary, because the response is destined for an
    LLM prompt.
    """
    return await ElasticAggregateService(db).build_payload(
        project_id=project_id, default_window_days=window_days
    )
