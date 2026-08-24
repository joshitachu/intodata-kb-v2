"""Read-only HTTP client for the MCP landscape and graph-manifest artifacts.

Follows the adapter shape established by `src.harness.providers.mcp_docs`: a
module-level httpx client per call, typed results, and one safe error surface.
The access token is forwarded as MCP's `access_token` cookie so MCP performs the
authoritative project-access check (`require_project_access`) — this service's
own `project_scope` check is enforced separately in `src.topology.client`.

Five artifact families, one fetch/cache path. The landscape and the project
graph manifest are project-level documents (the canvas loads both to draw its
top level); the job graph, the component graph and the field lineage are DEEP
reads, fetched only when a question is actually about that job or that component.
Lazy fetch is part of the contract, not an optimisation: a landscape-level
question must not pull a per-job document, both because it is wasted latency and
because it would let the model answer a structural question from a document it
had no reason to open.

Field lineage is the one family paged upstream rather than fetched whole: a real
project publishes 2 222 field mappings, so `offset`/`limit` are part of its
request and therefore part of its cache key.

Error messages here are static or derived from a validated project id, job name
or component name only. The upstream response body is never interpolated into
them: it is logged instead, so an operator keeps the diagnostic and the model
never sees it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, ValidationError

from src.config import settings
from src.topology.models import (
    LINEAGE_MAX_JOBS,
    LINEAGE_PAGE_LIMIT_MAX,
    PROJECT_ID_PATTERN,
    ComponentGraph,
    JobFieldLineage,
    JobGraph,
    ProjectGraph,
    ProjectLandscape,
    validated_artifact_name,
    validated_query_value,
)

logger = logging.getLogger(__name__)

_LANDSCAPE_PATH_TEMPLATE = "/projects/{project_id}/landscape"
_PROJECT_GRAPH_PATH_TEMPLATE = "/projects/{project_id}/graph"
_JOB_GRAPH_PATH_TEMPLATE = "/projects/{project_id}/jobs/{job_name}/graph"
_COMPONENT_GRAPH_PATH_TEMPLATE = (
    "/projects/{project_id}/jobs/{job_name}/components/{component_name}/graph"
)
_JOB_LINEAGE_PATH_TEMPLATE = "/projects/{project_id}/lineage/job"

#: Markers that identify upstream's 404 as "this project publishes no lineage
#: artifact at all" rather than "the requested job or group has no lineage
#: entries". The two lead to opposite answers — "rescan the project" versus
#: "that name is not in the scan" — and the endpoint signals both with a 404,
#: distinguishable only by its detail message.
#:
#: Anything that does not match a marker defaults to the name-not-found reading.
#: That default is chosen, not incidental: telling a user to rescan over a typo
#: is exactly the harm `TopologyDesignUnavailableError` exists to prevent, so
#: the rescan answer is only given on positive evidence for it.
_LINEAGE_ARTIFACT_ABSENT_MARKERS = (
    "scan/lineage.json",
    "no lineage data for project",
    "cannot read lineage data",
    "run a scan",
)

#: Structured form of the same signal, preferred over the text markers when
#: upstream emits it, mirroring how `_is_mapping_fallback` reads a 404 body.
_LINEAGE_ABSENT_REASON = "lineage_artifact_absent"

ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)


class TopologyError(Exception):
    """Base for every topology read outcome that is not a landscape artifact.

    Subclasses are siblings on purpose: `except TopologyProviderError` must not
    be able to swallow "this project has no artifact", because the two lead to
    completely different answers ("try again" vs "rescan the project").
    """

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class TopologyArtifactUnavailableError(TopologyError):
    """The project has no landscape artifact (upstream 404). Needs a rescan."""

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"No topology artifact has been published for project '{project_id}'. "
            "The project needs a scan before its landscape can be answered from."
        )
        self.project_id = project_id


class TopologyProviderTimeoutError(TopologyError):
    """The topology provider did not answer within the configured timeout."""


class TopologyProviderError(TopologyError):
    """The topology provider was unreachable or refused the read."""


class TopologyPayloadError(TopologyError):
    """The artifact was served but is not a usable landscape document."""


class TopologyDesignUnavailableError(TopologyError):
    """A named job or component has no design artifact in this project's scan.

    Distinct from `TopologyArtifactUnavailableError`: the project HAS been
    scanned, so the answer is "that name is not in the scan", not "rescan the
    project". Collapsing the two would tell a user to rescan over a typo.
    """


class TopologyMappingUnavailableError(TopologyError):
    """The component exists but publishes no deeper mapping graph.

    A documented outcome, not a failure: only mapping components (tMap today)
    carry intra-component structure. Upstream signals it with a 404 whose body
    is `{"fallback": "inspector"}`, which is the canvas's cue to show the
    structured inspector instead of a fabricated graph. The tool layer answers
    from job-level facts and says the deeper graph does not exist.
    """


@dataclass(frozen=True)
class LandscapeFingerprint:
    """Identity of one published artifact.

    `scan` is the scanner fingerprint the overlay declares, which is what an
    operator recognises. It is not sufficient on its own as a cache key: a
    documentation run can republish a different overlay against an unchanged
    scan, so the content digest is part of the identity as well.
    """

    scan: str
    content: str


@dataclass(frozen=True)
class LandscapeArtifact:
    project_id: str
    fingerprint: LandscapeFingerprint
    landscape: ProjectLandscape


@dataclass(frozen=True)
class ProjectGraphArtifact:
    project_id: str
    fingerprint: LandscapeFingerprint
    graph: ProjectGraph


@dataclass(frozen=True)
class JobGraphArtifact:
    project_id: str
    job_name: str
    fingerprint: LandscapeFingerprint
    graph: JobGraph


@dataclass(frozen=True)
class ComponentGraphArtifact:
    project_id: str
    job_name: str
    component_name: str
    fingerprint: LandscapeFingerprint
    graph: ComponentGraph


@dataclass(frozen=True)
class JobLineageArtifact:
    project_id: str
    #: The filter that was requested, kept alongside the response's own `jobs`
    #: list: a `group` request expands to many designs upstream, and an answer
    #: has to be able to say which scope produced these numbers.
    requested_jobs: tuple[str, ...]
    requested_group: str
    fingerprint: LandscapeFingerprint
    lineage: JobFieldLineage


@dataclass
class _CacheEntry:
    fingerprint: LandscapeFingerprint
    model: BaseModel
    expires_at: float


class TopologyProvider:
    """Fetches and caches the MCP topology artifacts for one deployment."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        cache_ttl_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        configured_base_url = base_url if base_url is not None else settings.mcp_docs_base_url
        self._base_url = configured_base_url.strip().rstrip("/")
        self._timeout = timeout if timeout is not None else settings.mcp_docs_request_timeout
        self._cache_ttl = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else settings.topology_landscape_cache_ttl_seconds
        )
        self._transport = transport
        self._cache: dict[str, _CacheEntry] = {}

    async def get_landscape(
        self,
        *,
        project_id: str,
        access_token: str | None = None,
    ) -> LandscapeArtifact:
        """Return the current landscape artifact for one project.

        Caching is entirely internal: callers never reason about freshness, and
        a fingerprint change always wins over the TTL.
        """
        project_id = self._ready(project_id)
        fingerprint, landscape = await self._artifact(
            cache_key=f"landscape:{project_id}",
            path=_LANDSCAPE_PATH_TEMPLATE.format(project_id=project_id),
            model=ProjectLandscape,
            project_id=project_id,
            access_token=access_token,
            unreadable=(
                f"The topology artifact for project '{project_id}' is not readable. "
                "It needs to be regenerated by a rescan."
            ),
        )
        return LandscapeArtifact(
            project_id=project_id, fingerprint=fingerprint, landscape=landscape
        )

    async def get_project_graph(
        self,
        *,
        project_id: str,
        access_token: str | None = None,
    ) -> ProjectGraphArtifact:
        """Return the project design manifest: groups, jobs, source roots, wiring."""
        project_id = self._ready(project_id)
        fingerprint, graph = await self._artifact(
            cache_key=f"project-graph:{project_id}",
            path=_PROJECT_GRAPH_PATH_TEMPLATE.format(project_id=project_id),
            model=ProjectGraph,
            project_id=project_id,
            access_token=access_token,
            unreadable=(
                f"The design manifest for project '{project_id}' is not readable. "
                "It needs to be regenerated by a rescan."
            ),
        )
        return ProjectGraphArtifact(project_id=project_id, fingerprint=fingerprint, graph=graph)

    async def get_job_graph(
        self,
        *,
        project_id: str,
        job_name: str,
        access_token: str | None = None,
    ) -> JobGraphArtifact:
        """DEEP read: one job's component graph. Only for job-level questions."""
        project_id = self._ready(project_id)
        job_name = _validated_name(job_name, subject="job name")
        fingerprint, graph = await self._artifact(
            cache_key=f"job-graph:{project_id}:{job_name}",
            path=_JOB_GRAPH_PATH_TEMPLATE.format(
                project_id=project_id, job_name=_segment(job_name)
            ),
            model=JobGraph,
            project_id=project_id,
            access_token=access_token,
            unreadable=f"The design for job '{job_name}' is not readable.",
            missing=TopologyDesignUnavailableError(
                f"No design named '{job_name}' is in this project's scan."
            ),
        )
        return JobGraphArtifact(
            project_id=project_id, job_name=job_name, fingerprint=fingerprint, graph=graph
        )

    async def get_component_graph(
        self,
        *,
        project_id: str,
        job_name: str,
        component_name: str,
        access_token: str | None = None,
    ) -> ComponentGraphArtifact:
        """DEEP read: one component's mapping graph.

        Raises `TopologyMappingUnavailableError` for the common, documented case
        of a component that simply has no deeper graph.
        """
        project_id = self._ready(project_id)
        job_name = _validated_name(job_name, subject="job name")
        component_name = _validated_name(component_name, subject="component name")
        fingerprint, graph = await self._artifact(
            cache_key=f"component-graph:{project_id}:{job_name}:{component_name}",
            path=_COMPONENT_GRAPH_PATH_TEMPLATE.format(
                project_id=project_id,
                job_name=_segment(job_name),
                component_name=_segment(component_name),
            ),
            model=ComponentGraph,
            project_id=project_id,
            access_token=access_token,
            unreadable=f"The mapping for component '{component_name}' is not readable.",
            missing=TopologyDesignUnavailableError(
                f"No component named '{component_name}' is in the design for '{job_name}'."
            ),
            mapping_fallback=TopologyMappingUnavailableError(
                f"Component '{component_name}' publishes no deeper mapping graph."
            ),
        )
        return ComponentGraphArtifact(
            project_id=project_id,
            job_name=job_name,
            component_name=component_name,
            fingerprint=fingerprint,
            graph=graph,
        )

    async def get_job_lineage(
        self,
        *,
        project_id: str,
        jobs: Sequence[str] = (),
        group: str | None = None,
        offset: int = 0,
        limit: int = LINEAGE_PAGE_LIMIT_MAX,
        access_token: str | None = None,
    ) -> JobLineageArtifact:
        """DEEP read: field-level mappings for named designs or a whole group.

        The deepest artifact in the set, and the only one paged upstream. At
        least one of `jobs` or `group` is required — an unscoped read of 2 222
        field mappings is a document, not an answer.

        The two 404 outcomes are mapped to different errors on purpose. No
        lineage artifact at all is `TopologyArtifactUnavailableError` ("rescan
        the project"); a scope with no entries is
        `TopologyDesignUnavailableError` ("that name is not in the scan").
        Collapsing them would tell a user to rescan over a typo.
        """
        project_id = self._ready(project_id)
        names = tuple(_validated_query(name, subject="job name") for name in jobs)
        if len(names) > LINEAGE_MAX_JOBS:
            raise TopologyProviderError(
                f"At most {LINEAGE_MAX_JOBS} job names can be read in one lineage request."
            )
        group_name = _validated_query(group, subject="group name") if group else ""
        if not names and not group_name:
            raise TopologyProviderError("A lineage read needs a job name or a group name.")

        query: list[tuple[str, str]] = [("job", name) for name in names]
        if group_name:
            query.append(("group", group_name))
        # Clamped rather than rejected: the cap is this package's, and a caller
        # asking for more than the contract allows should get the contract's
        # maximum, not a failed read.
        query.append(("offset", str(max(offset, 0))))
        query.append(("limit", str(min(max(limit, 1), LINEAGE_PAGE_LIMIT_MAX))))
        encoded = urlencode(query)

        fingerprint, lineage = await self._artifact(
            # The page is part of the identity: two offsets over the same scan
            # are different documents, so they cannot share a cache entry.
            cache_key=f"job-lineage:{project_id}:{encoded}",
            path=f"{_JOB_LINEAGE_PATH_TEMPLATE.format(project_id=project_id)}?{encoded}",
            model=JobFieldLineage,
            project_id=project_id,
            access_token=access_token,
            unreadable=(
                f"The field lineage for project '{project_id}' is not readable. "
                "It needs to be regenerated by a rescan."
            ),
            on_missing=_lineage_missing(project_id=project_id, jobs=names, group=group_name),
        )
        return JobLineageArtifact(
            project_id=project_id,
            requested_jobs=names,
            requested_group=group_name,
            fingerprint=fingerprint,
            lineage=lineage,
        )

    def clear_cache(self) -> None:
        """Drop every cached artifact. Used by tests and by a future admin reset."""
        self._cache.clear()

    def _ready(self, project_id: str) -> str:
        if not self._base_url:
            raise TopologyProviderError("Topology provider is not configured.")
        return _validated_project_id(project_id)

    async def _artifact(
        self,
        *,
        cache_key: str,
        path: str,
        model: type[ArtifactModel],
        project_id: str,
        access_token: str | None,
        unreadable: str,
        missing: TopologyError | None = None,
        mapping_fallback: TopologyError | None = None,
        on_missing: Callable[[httpx.Response], TopologyError] | None = None,
    ) -> tuple[LandscapeFingerprint, ArtifactModel]:
        """Fetch, fingerprint, validate and cache one artifact.

        One path for all five families so the identity rules (fingerprint beats
        TTL, parsed object reused on an unchanged fingerprint) cannot drift apart
        between them.

        `missing` is one fixed error for a 404. `on_missing` is for the family
        whose 404 carries two distinct meanings and has to read the body to tell
        them apart; it wins over `missing` when both are supplied.
        """
        cached = self._cache.get(cache_key)
        if cached is not None and cached.expires_at > time.monotonic():
            return cached.fingerprint, _typed(cached.model, model)

        payload = await self._fetch(
            path=path,
            project_id=project_id,
            access_token=access_token,
            missing=missing,
            mapping_fallback=mapping_fallback,
            on_missing=on_missing,
        )
        fingerprint = _fingerprint(payload)

        if cached is not None and cached.fingerprint == fingerprint:
            # Same artifact: keep the already-parsed object rather than paying
            # for a second validation of a multi-megabyte document.
            cached.expires_at = time.monotonic() + self._cache_ttl
            return fingerprint, _typed(cached.model, model)

        try:
            parsed = model.model_validate(payload)
        except ValidationError:
            logger.warning(
                "Topology artifact %s for project %s failed validation",
                model.__name__,
                project_id,
                exc_info=True,
            )
            raise TopologyPayloadError(unreadable) from None

        self._cache[cache_key] = _CacheEntry(
            fingerprint=fingerprint,
            model=parsed,
            expires_at=time.monotonic() + self._cache_ttl,
        )
        return fingerprint, parsed

    async def _fetch(
        self,
        *,
        path: str,
        project_id: str,
        access_token: str | None,
        missing: TopologyError | None,
        mapping_fallback: TopologyError | None,
        on_missing: Callable[[httpx.Response], TopologyError] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                cookies={"access_token": access_token} if access_token else None,
            ) as client:
                response = await client.get(path)
        except httpx.TimeoutException as exc:
            raise TopologyProviderTimeoutError(
                f"Topology provider timed out after {self._timeout:g}s."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Topology provider unreachable for project %s", project_id, exc_info=True
            )
            raise TopologyProviderError("Topology provider could not be reached.") from exc

        if response.status_code == 404:
            if mapping_fallback is not None and _is_mapping_fallback(response):
                raise mapping_fallback
            if on_missing is not None:
                raise on_missing(response)
            raise missing if missing is not None else TopologyArtifactUnavailableError(project_id)
        if response.status_code >= 400:
            logger.warning(
                "Topology provider returned HTTP %s for project %s",
                response.status_code,
                project_id,
            )
            raise TopologyProviderError(f"Topology provider returned HTTP {response.status_code}.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise TopologyPayloadError("Topology provider returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise TopologyPayloadError("Topology provider returned an invalid response.")
        return payload


def _typed(model: BaseModel, expected: type[ArtifactModel]) -> ArtifactModel:
    """Guard the shared cache against a key collision between families.

    The cache holds four artifact types under one dict. A mismatch can only come
    from a coding error, but returning the wrong document would surface as an
    inexplicably empty answer rather than as an error, so it is checked.
    """
    if not isinstance(model, expected):
        raise TopologyPayloadError("Topology provider returned an unexpected artifact.")
    return model


def _is_mapping_fallback(response: httpx.Response) -> bool:
    """Whether a 404 is upstream's documented "no deeper graph" signal.

    Upstream raises it as a FastAPI detail (`{"detail": {"fallback": ...}}`);
    the browser-facing proxy flattens it to `{"fallback": ...}`. Both shapes are
    accepted so this does not depend on which hop answered.
    """
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    for candidate in (body, body.get("detail")):
        if isinstance(candidate, dict) and candidate.get("fallback") == "inspector":
            return True
    return False


def _lineage_missing(
    *, project_id: str, jobs: tuple[str, ...], group: str
) -> Callable[[httpx.Response], TopologyError]:
    """Resolve a lineage 404 into one of its two distinct meanings.

    Returns a closure rather than an error so the body is only read when a 404
    actually happens, and so the two messages can name the scope that was asked
    for. Neither message interpolates the upstream body: it is logged instead,
    exactly as everywhere else in this module.
    """

    def resolve(response: httpx.Response) -> TopologyError:
        if _is_lineage_artifact_absent(response):
            logger.info("Project %s publishes no field-lineage artifact", project_id)
            return TopologyArtifactUnavailableError(project_id)
        scope = ", ".join(jobs) if jobs else group
        logger.info("No field lineage for scope '%s' in project %s", scope, project_id)
        return TopologyDesignUnavailableError(
            f"No field lineage is recorded for '{scope}' in this project's scan. "
            "The project has been scanned, so this is a name that is not in it "
            "rather than missing lineage data."
        )

    return resolve


def _is_lineage_artifact_absent(response: httpx.Response) -> bool:
    """Whether a lineage 404 means the project has no lineage artifact at all.

    Prefers a structured `reason` on the body (or on FastAPI's nested `detail`,
    since the browser-facing proxy flattens one and not the other) and falls
    back to the detail text. A body that says neither is treated as the
    name-not-found case; see `_LINEAGE_ARTIFACT_ABSENT_MARKERS`.
    """
    try:
        body = response.json()
    except ValueError:
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    for candidate in (body, detail):
        if isinstance(candidate, dict) and candidate.get("reason") == _LINEAGE_ABSENT_REASON:
            return True
    text = ""
    if isinstance(detail, str):
        text = detail
    elif isinstance(detail, dict):
        text = str(detail.get("message") or detail.get("detail") or "")
    elif isinstance(body, dict):
        text = str(body.get("message") or "")
    folded = text.casefold()
    return any(marker in folded for marker in _LINEAGE_ARTIFACT_ABSENT_MARKERS)


def _segment(value: str) -> str:
    """Percent-encode one path segment, as the browser client does."""
    return quote(value, safe="")


def _validated_name(value: str, *, subject: str) -> str:
    try:
        return validated_artifact_name(value)
    except ValueError as exc:
        raise TopologyProviderError(f"Invalid {subject}.") from exc


def _validated_query(value: str, *, subject: str) -> str:
    """Validate a value bound for a query parameter rather than a path segment."""
    try:
        return validated_query_value(value)
    except ValueError as exc:
        raise TopologyProviderError(f"Invalid {subject}.") from exc


def _fingerprint(payload: dict[str, Any]) -> LandscapeFingerprint:
    business = payload.get("business_topology")
    scan = ""
    if isinstance(business, dict):
        metadata = business.get("metadata")
        scan = str(
            business.get("scan_fingerprint")
            or (metadata.get("scan_fingerprint") if isinstance(metadata, dict) else "")
            or ""
        )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    content = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return LandscapeFingerprint(scan=scan, content=content)


def _validated_project_id(value: str) -> str:
    project_id = value.strip()
    if re.fullmatch(PROJECT_ID_PATTERN, project_id) is None:
        raise TopologyProviderError(
            "Invalid project id. Use letters, numbers, hyphens, or underscores."
        )
    return project_id


_default_provider: TopologyProvider | None = None


def default_provider() -> TopologyProvider:
    """Process-wide provider, so its artifact cache is shared across turns.

    Resolved at call time rather than at import time so tests can substitute a
    stub, matching the pattern used by `src.runtime.client._resolve_provider`.
    """
    global _default_provider
    if _default_provider is None:
        _default_provider = TopologyProvider()
    return _default_provider
