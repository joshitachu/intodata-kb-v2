"""Shared artifact fixtures for the topology tools. All are real scanner output.

Landscape rollups (L1/L2):

`alitalend_landscape.json` is a real published artifact, verbatim: two distinct
scanner systems named "Log", promoted into two distinct overlay systems with the
same name. It is the ambiguity case the tools must never collapse.

`werkorder_composite_landscape.json` is a real composite (multi-partition)
upload. Entity counts are the real ones -- 43 scanner systems, 25 edges, 38
diagnostics, 20 curated overlay systems -- so every cap in `tools.py` is
exceeded by it. Only the repeated per-entity evidence tails were trimmed to keep
the file committable. It has NO job or component nodes, which is exactly why the
depth tools exist.

Design manifests (L3/L4), generated from the same two local scans:

`*_project_graph.json` are real `GET /projects/{id}/graph` payloads. The
werkorder one is trimmed to 115 of its 429 designs -- every one of the 14 real
source roots is represented, plus the whole Quintiq/Maximo family and the two
richest designs -- with per-artifact facts bounded and the descriptors the models
never parse dropped. Records that remain are verbatim.

`*_job_graph.json` are real `GET .../jobs/{job}/graph` payloads, untrimmed:
  - `werkorder_quintiq_job_graph`  40 components / 37 connections (28 KB raw)
  - `werkorder_richest_job_graph`  251 components / 201 connections (142 KB raw)
  - `werkorder_mapping_job_graph`  the design owning the richest mapping component
  - `alitalend_job_graph`          a single-root design with one tMap

`pluripharm_collector_job_graph.json` is the real job graph of the Camel route
`collector_jde_shipping_notification_from_db_c006_mediq` (41 components, 32
connections), produced by running the MCP job-graph builder over that project's
own scan cache. It is the artifact behind a measured agent failure: asked how a
shipment is routed to one flow or the other, an agent opened 15 of these
components one at a time and still reported the criterion as undeterminable,
because the answer is a ROW of `cSetHeader_5`'s VALUES table
(`AN8_CHECK = xpath(".../SZAN8/text()")`) and row-structured params were dropped
before they reached the tool layer. `pluripharm_project_graph.json` is the
matching single-design slice of `GET /projects/{id}/graph`, with the group,
source root, external systems and component facts taken from that scan's own
`manifest.json`.

`*_component_graph.json` are real `GET .../components/{name}/graph` payloads.
`werkorder_richest_component_graph` is the largest mapping graph in the whole
upload at 27 KB raw, which is the cap case for `get_component_detail`.

Field lineage (the deepest artifact):

`werkorder_rhm2sf_field_lineage.json` is the real `scan/lineage.json` slice for
the RHM2SF design, flattened into the `GET /projects/{id}/lineage/job` contract
shape: 85 field mappings, 73 resolved, 12 runtime-determined, across 7 target
tables. It is served UNPAGED and `lineage_responder` filters and pages it the
way upstream does, so the tool's rollup arithmetic is tested against arithmetic
rather than against a fixture that already agrees with it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.topology.provider import TopologyProvider

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


class RecordingTransport(httpx.MockTransport):
    """Mock transport that keeps every request for identity assertions."""

    def __init__(self, responder: Any) -> None:
        self.requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return responder(request)

        super().__init__(handler)

    @property
    def call_count(self) -> int:
        return len(self.requests)


def json_transport(payload: dict[str, Any], status_code: int = 200) -> RecordingTransport:
    return RecordingTransport(lambda request: httpx.Response(status_code, json=payload))


#: A route serves either a fixed document or a handler. Handlers exist for the
#: one artifact family that is paged upstream (field lineage): its response
#: depends on the query string, so a fixed document could not exercise the
#: paging or the totals arithmetic that reads from it.
RouteHandler = Callable[[httpx.Request], httpx.Response]
Route = dict[str, Any] | RouteHandler


class RoutedTransport(RecordingTransport):
    """Serve a different artifact per URL path, 404 for anything unrouted.

    Landscape-level and depth-level reads hit different endpoints, so the tests
    that assert a deep endpoint was NOT called need a transport that can tell
    them apart. An unrouted path 404s rather than falling back to some other
    artifact, so a wrong URL fails loudly instead of quietly answering.

    Routing is on the PATH only, so a route handler that cares about the query
    string reads it off the request itself.
    """

    def __init__(self, routes: dict[str, Route]) -> None:
        self.routes = routes

        def responder(request: httpx.Request) -> httpx.Response:
            route = routes.get(request.url.path)
            if route is None:
                return httpx.Response(404, json={"detail": "not found"})
            if callable(route):
                return route(request)
            return httpx.Response(200, json=route)

        super().__init__(responder)

    def queries_matching(self, fragment: str) -> list[str]:
        """Full query strings of the requests whose path contains `fragment`."""
        return [
            request.url.query.decode() for request in self.requests if fragment in request.url.path
        ]

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def paths_matching(self, fragment: str) -> list[str]:
        return [path for path in self.paths if fragment in path]


def landscape_path(project_id: str) -> str:
    return f"/mcp/projects/{project_id}/landscape"


def project_graph_path(project_id: str) -> str:
    return f"/mcp/projects/{project_id}/graph"


def job_graph_path(project_id: str, job_name: str) -> str:
    # Unencoded: httpx decodes `request.url.path`, so a route keyed with the
    # percent-encoded form would never match the request the provider sent.
    return f"/mcp/projects/{project_id}/jobs/{job_name}/graph"


def component_graph_path(project_id: str, job_name: str, component_name: str) -> str:
    return f"/mcp/projects/{project_id}/jobs/{job_name}/components/{component_name}/graph"


def field_lineage_path(project_id: str) -> str:
    return f"/mcp/projects/{project_id}/lineage/job"


#: The two 404 detail messages the lineage endpoint distinguishes its outcomes
#: with. The first is `LineageDataUnavailableError`'s own wording from the MCP
#: lineage service, which is the string the provider's marker list is written
#: against; the second is a scope miss inside a project that HAS been scanned.
LINEAGE_NO_ARTIFACT_DETAIL = (
    "No lineage data for project 'werkorder' -- run a scan to generate scan/lineage.json"
)
LINEAGE_NO_ENTRIES_DETAIL = "No lineage entries for job 'NotADesign' in project 'werkorder'"


def lineage_responder(
    document: dict[str, Any],
    *,
    groups: dict[str, list[str]] | None = None,
) -> RouteHandler:
    """Serve `GET /projects/{id}/lineage/job` the way the frozen contract does.

    Filters the document's edges by the repeatable `job` parameter and by
    `group`, then pages by `offset`/`limit`. `totals` are recomputed over the
    FILTERED set, never over the returned page, because that split is exactly
    what the tool's rollup arithmetic depends on: a fixture that pre-agreed with
    the expected numbers would not test the arithmetic at all.

    404s with the scope-miss detail when the filter selects nothing, which is
    the contract's second 404 meaning.
    """
    group_jobs = groups or {}

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        wanted = set(params.get_list("job"))
        group = params.get("group")
        if group:
            wanted |= set(group_jobs.get(group, []))
        edges = [edge for edge in document["edges"] if not wanted or edge["job_name"] in wanted]
        if not edges:
            return httpx.Response(404, json={"detail": LINEAGE_NO_ENTRIES_DETAIL})

        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 200))
        page = edges[offset : offset + limit]
        resolved = sum(1 for edge in edges if edge["resolved"])
        confidence: dict[str, int] = {}
        for edge in edges:
            key = edge["confidence"]
            confidence[key] = confidence.get(key, 0) + 1
        return httpx.Response(
            200,
            json={
                "project_id": document["project_id"],
                "jobs": sorted({edge["job_name"] for edge in edges}),
                "totals": {
                    "entries": len(edges),
                    "resolved": resolved,
                    "unresolved": len(edges) - resolved,
                    "confidence": confidence,
                },
                "target_tables": list(
                    dict.fromkeys(edge["target_table"] for edge in edges if edge["target_table"])
                ),
                "edges": page,
                "offset": offset,
                "limit": limit,
                "returned": len(page),
                "total_edges": len(edges),
                "has_more": offset + len(page) < len(edges),
            },
        )

    return handler


def lineage_404_transport(detail: str) -> RoutedTransport:
    """A lineage endpoint that always 404s with one specific detail message."""
    return RoutedTransport(
        {
            field_lineage_path("werkorder"): lambda _request: httpx.Response(
                404, json={"detail": detail}
            )
        }
    )


def make_provider(
    transport: httpx.AsyncBaseTransport,
    *,
    cache_ttl_seconds: float = 60.0,
) -> TopologyProvider:
    return TopologyProvider(
        base_url="http://mcp.internal/mcp",
        timeout=5.0,
        cache_ttl_seconds=cache_ttl_seconds,
        transport=transport,
    )


@pytest.fixture
def alitalend() -> dict[str, Any]:
    return load_fixture("alitalend_landscape")


@pytest.fixture
def werkorder() -> dict[str, Any]:
    return load_fixture("werkorder_composite_landscape")


@pytest.fixture
def werkorder_graph() -> dict[str, Any]:
    return load_fixture("werkorder_project_graph")


@pytest.fixture
def quintiq_job_graph() -> dict[str, Any]:
    return load_fixture("werkorder_quintiq_job_graph")


@pytest.fixture
def richest_job_graph() -> dict[str, Any]:
    return load_fixture("werkorder_richest_job_graph")


@pytest.fixture
def mapping_job_graph() -> dict[str, Any]:
    return load_fixture("werkorder_mapping_job_graph")


@pytest.fixture
def richest_component_graph() -> dict[str, Any]:
    return load_fixture("werkorder_richest_component_graph")


@pytest.fixture
def rhm2sf_lineage() -> dict[str, Any]:
    """The real RHM2SF field lineage: 85 mappings, 73 of them resolved."""
    return load_fixture("werkorder_rhm2sf_field_lineage")


@pytest.fixture
def pluripharm_graph() -> dict[str, Any]:
    return load_fixture("pluripharm_project_graph")


@pytest.fixture
def collector_job_graph() -> dict[str, Any]:
    return load_fixture("pluripharm_collector_job_graph")


@pytest.fixture
def pluripharm_provider(pluripharm_graph, collector_job_graph):
    """The design manifest and job graph behind the measured routing question."""
    transport = werkorder_routes(
        landscape={},
        project_graph=pluripharm_graph,
        job_graphs={COLLECTOR_JOB: collector_job_graph},
        project_id=PLURIPHARM_PROJECT,
    )
    return transport, make_provider(transport)


@pytest.fixture
def alitalend_graph() -> dict[str, Any]:
    return load_fixture("alitalend_project_graph")


@pytest.fixture
def alitalend_job_graph() -> dict[str, Any]:
    return load_fixture("alitalend_job_graph")


@pytest.fixture
def alitalend_component_graph() -> dict[str, Any]:
    return load_fixture("alitalend_component_graph")


#: Real names from the two scans, used across the depth tests.
#: `RHM2SF_LINEAGE_JOB` is the lineage artifact's own name for the RHM2SF
#: design. It deliberately does NOT match the project manifest's name for the
#: same partition (`..._intod-249ee1cc0d2b`): the two artifacts hash the
#: composite path differently, which is why `get_field_lineage` passes the name
#: through instead of resolving it against the manifest first.
RHM2SF_LINEAGE_JOB = "VolkerVessels_Workorder_Talend_exports_VQ_RHM2SF___intod-31f9ad59e878"
RHM2SF_GROUP = "VolkerVessels_Workorder/Talend exports/VQ_RHM2SF.__intodoc_zip__/VQ_RHM2SF"
#: The real page-scope id for a selected CONNECTION on the werkorder canvas.
#: The client sends both this bare form and the `business-stream`-prefixed key.
SELECTED_EDGE_STREAM_ID = "ds-enterpriseservicebus-rhm--smartflow-message"
QUINTIQ_JOB = "Quintiq2Maximo_WorkOrderPickedUpXML"
RICHEST_JOB = "forSuppliersFourpsVendorData"
MAPPING_JOB = "VISE_SP_VIP_OTL"
MAPPING_COMPONENT = "tMap_1"
QUINTIQ_SOURCE_ROOT = (
    "VolkerVessels_Workorder/Talend exports/VQ_Quintiq2MX.__intodoc_zip__/VQ_QUINTIQ2MX"
)
#: The Camel route from the measured routing failure, and the component that
#: holds the criterion: `cSetHeader_5` ("setXmlVar") defines AN8_CHECK from the
#: shipment's JD Edwards ship-to address-book number, which the cMessageRouter
#: downstream of it branches on.
PLURIPHARM_PROJECT = "pluripharm"
COLLECTOR_JOB = "collector_jde_shipping_notification_from_db_c006_mediq"
COLLECTOR_ROUTING_COMPONENT = "cSetHeader_5"


def werkorder_routes(
    *,
    landscape: dict[str, Any],
    project_graph: dict[str, Any] | None = None,
    job_graphs: dict[str, dict[str, Any]] | None = None,
    component_graphs: dict[tuple[str, str], dict[str, Any]] | None = None,
    lineage: dict[str, Any] | None = None,
    lineage_groups: dict[str, list[str]] | None = None,
    project_id: str = "werkorder",
) -> RoutedTransport:
    routes: dict[str, Route] = {landscape_path(project_id): landscape}
    if lineage is not None:
        routes[field_lineage_path(project_id)] = lineage_responder(lineage, groups=lineage_groups)
    if project_graph is not None:
        routes[project_graph_path(project_id)] = project_graph
    for job_name, payload in (job_graphs or {}).items():
        routes[job_graph_path(project_id, job_name)] = payload
    for (job_name, component_name), payload in (component_graphs or {}).items():
        routes[component_graph_path(project_id, job_name, component_name)] = payload
    return RoutedTransport(routes)
