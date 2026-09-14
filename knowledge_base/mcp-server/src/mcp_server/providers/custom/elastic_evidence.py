"""Log-estate (Elastic) runtime aggregates for the runtime authority fusion.

TMC knows what the control plane *scheduled and executed*. A log estate knows
what actually *flowed*: how many messages a route carried, how many of them
errored, and how long a correlated hop-to-hop path took. ``runtime_authority``
already fuses the two side by side and already owns both the input contract
(``ElasticAggregatePayload`` / ``elastic-aggregates-1``) and the rendering. What
was missing was a PRODUCER: the fusion was called with a hardcoded ``None``, so
the Elastic authority was recorded ``unavailable`` with ``no_elastic_source`` on
every run and every enterprise dossier stated that message volumes and latency
figures were unavailable -- including for estates whose ESB traffic is fully
indexed.

This module is that producer, and it is deliberately the thinnest one that can
exist:

* **One bounded read from the RAG backend.** Whoever can reach the cluster
  normalises into ``elastic-aggregates-1``; the harness never queries Elastic
  and never learns an index pattern, a field name or a credential. Keeping the
  query on the backend side is what stops a documentation run from acquiring a
  second live dependency, and it is the same split the TMC lane uses
  (``runtime_evidence.fetch_runtime_evidence`` -> ``GET
  /api/v1/runtime/doc-evidence``).
* **No parsing here.** The raw JSON body travels to
  ``fuse_runtime_authorities``, which validates it against
  ``ElasticAggregatePayload`` and records ``elastic_payload_invalid`` when it
  does not conform. A second validator on this side would be a second, drifting
  definition of one contract.
* **Absence is ``None``, always, and never anything else.** Not configured,
  disabled, switched off for this run, unreachable, timed out, 404, null body:
  every one returns ``None``, and the fusion turns that into a stated
  ``no_elastic_source``. Nothing here synthesises a payload -- a zero-filled or
  empty-but-valid body would publish "nothing flowed" over "we could not look",
  and those are different statements.
* **Never a gate.** A log estate that is down degrades the documents; it does
  not fail the run, and bounded by one timeout it does not stall it either.

The read scope (which environments, which window) is resolved SERVER-side, the
same way ``GET /runtime/doc-evidence`` resolves ``doc_environment_ids``. Nothing
in the harness passes an environment, so both engines and both authorities
inherit one rule instead of two copies of it.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mcp_server.config import config

logger = logging.getLogger(__name__)

#: HTTP statuses that mean "this project has no log-estate aggregate to give
#: you". That is a configuration fact, not a failure, so it is logged at info
#: and never as a warning an operator has to triage every run.
_ABSENT_STATUSES = frozenset({204, 404})


def per_job_count(payload: Any) -> int:
    """How many per-job aggregates a raw body carries. Total: never raises.

    For logging only, on both sides of the boundary. Deliberately NOT a
    validation step: a body whose ``per_job`` has the wrong type still travels
    to the fusion, which is the one place that judges the contract.
    """
    if not isinstance(payload, dict):
        return 0
    entries = payload.get("per_job")
    if isinstance(entries, (list, tuple)):
        return len(entries)
    return 0


def resolve_window_days() -> int:
    """The window to ask the log estate for.

    ``elastic_evidence_window_days`` of 0 inherits the TMC window so both
    authorities describe the same period by default. An operator may widen it
    deliberately -- a log estate commonly retains months more than the TMC
    mirror -- and that stays honest because every fused record carries its own
    ``provenance.window_days`` rather than inheriting one project-wide number.
    """
    configured = int(config.elastic_evidence_window_days or 0)
    if configured > 0:
        return configured
    return int(config.runtime_evidence_window_days)


def endpoint_url() -> str:
    """The absolute read URL, or ``""`` when the lane is not configured.

    Empty rather than raising: an unset backend URL or a cleared endpoint path
    is an operator saying "no log estate here", and that must be a no-op. It is
    also the switch that costs nothing -- clearing the path stops the request
    without touching the boolean.
    """
    base = str(config.rag_base_url or "").strip().rstrip("/")
    path = str(config.elastic_evidence_path or "").strip()
    if not base or not path:
        return ""
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


async def fetch_elastic_aggregates(
    project_id: str,
    *,
    live_evidence: bool = True,
    window_days: int | None = None,
) -> dict[str, Any] | None:
    """Fetch one project's ``elastic-aggregates-1`` body. Never raises.

    Returns the RAW body for ``fuse_runtime_authorities`` to validate, or
    ``None`` for every form of absence. ``None`` is one return value with one
    meaning -- "no log-estate aggregate reached this run" -- and the reason
    behind it is in this module's log, where an operator looks. What must never
    happen is the alternative: a fabricated payload, an empty ``per_job`` list
    minted on this side, or a default of zeros. Each of those turns an
    unreachable source into a published finding that nothing flowed.
    """
    if not live_evidence:
        logger.debug(
            "elastic_evidence: live evidence is off for this run; no log-estate read"
        )
        return None
    if not config.elastic_evidence_enabled:
        logger.debug("elastic_evidence: disabled by configuration; no log-estate read")
        return None
    url = endpoint_url()
    if not url:
        logger.debug(
            "elastic_evidence: no backend base URL or endpoint path configured; "
            "no log-estate read"
        )
        return None

    window = int(window_days) if window_days is not None else resolve_window_days()
    params = [("project_id", project_id), ("window_days", str(window))]
    headers: dict[str, str] = {}
    if config.ingest_api_key:
        headers["Authorization"] = f"Bearer {config.ingest_api_key}"

    try:
        async with httpx.AsyncClient(timeout=config.elastic_evidence_timeout_s) as client:
            response = await client.get(url, params=params, headers=headers)
            status = int(response.status_code)
            if status in _ABSENT_STATUSES:
                logger.info(
                    "elastic_evidence: project %s has no log-estate aggregate "
                    "(HTTP %d); message volumes and end-to-end latency stay "
                    "stated as unavailable",
                    project_id,
                    status,
                )
                return None
            response.raise_for_status()
            payload = response.json()
    except Exception:
        # Deliberately broad, exactly as the doc-evidence fetch is: a runtime
        # source that is down, slow, misconfigured or answering with something
        # unexpected must degrade the documents and never end the run. A
        # narrower tuple would let one unanticipated client-side error kill a
        # paid documentation run over an optional evidence tier.
        logger.warning(
            "elastic_evidence: log-estate read failed for project %s; the Elastic "
            "authority stays unavailable and the run continues",
            project_id,
            exc_info=True,
        )
        return None

    if payload is None:
        logger.info(
            "elastic_evidence: project %s returned an empty log-estate body; read as "
            "unavailable, never as an idle estate",
            project_id,
        )
        return None
    if not isinstance(payload, dict):
        # Same rule as `runtime_authority.load_tmc_store_payload`: a non-object
        # body is not the contract, and guessing at its shape here would be the
        # second parser this module exists to avoid.
        logger.warning(
            "elastic_evidence: project %s returned a %s rather than a JSON object; "
            "the Elastic authority stays unavailable",
            project_id,
            type(payload).__name__,
        )
        return None

    logger.info(
        "elastic_evidence: project %s returned %d job aggregate(s) over %d day(s) "
        "(schema %s); the fusion validates the body",
        project_id,
        per_job_count(payload),
        window,
        payload.get("schema_version") or "unstated",
    )
    return payload
