"""Fixed allowlist of topology tools.

Closed over the seven names in `TOPOLOGY_TOOL_NAMES`. Unknown names and invalid
arguments are rejected here, before the provider is reached, so no HTTP call is
ever made on behalf of an argument set this module has not validated.

Descriptions are part of the interface: they are the only thing steering a model
that has ~28 tools and four loop iterations. They say explicitly that a name is
accepted, so the model does not spend an iteration on a `find` it does not need,
and they say which level each tool reads, so a job question is not answered from
the landscape rollup (which contains no jobs) or from a document.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from src.runtime.models import RuntimeToolDefinition
from src.topology.models import (
    FindTopologyNodeInput,
    GetComponentDetailInput,
    GetFieldLineageInput,
    GetJobDetailInput,
    GetTopologyNodeInput,
    GetTopologyOverviewInput,
    ListTopologyDependenciesInput,
    TopologyProjectInput,
)
from src.topology.provider import TopologyProvider
from src.topology.tools import (
    TopologyToolPayload,
    find_topology_node,
    get_component_detail,
    get_field_lineage,
    get_job_detail,
    get_topology_node,
    get_topology_overview,
    list_topology_dependencies,
)

#: Handlers take the validated input model plus the identity-bearing keywords.
#: `access_token` is keyword-only the whole way down so it can never be bound
#: positionally from an argument dict.
TopologyToolHandler = Callable[..., Awaitable[TopologyToolPayload]]


class UnknownTopologyToolError(Exception):
    """Raised when a tool name is outside the fixed allowlist."""


class TopologyToolValidationError(Exception):
    """Raised when arguments fail validation against the tool's input model."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class TopologyToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: TopologyToolHandler


_TOOL_SPECS: dict[str, TopologyToolSpec] = {
    spec.name: spec
    for spec in (
        TopologyToolSpec(
            name="get_topology_overview",
            description=(
                "START HERE for architecture and landscape questions about a project: "
                "how many systems and integration applications exist, what kinds they "
                "are, how they are connected, which application roles are present, what "
                "the scan flagged as a diagnostic, and whether a validated business "
                "topology overlay is available, stale, or missing. Requires project_id. "
                "Call this before naming any system so the answer uses real entities."
            ),
            input_model=GetTopologyOverviewInput,
            handler=get_topology_overview,
        ),
        TopologyToolSpec(
            name="find_topology_node",
            description=(
                "Search the whole project for anything named: systems, integration "
                "applications, functional groups, jobs/routes (designs), and components. "
                "Use this when you have a name and do not know what kind of thing it is - "
                "a name like 'Quintiq2Maximo_WorkOrderPickedUpXML' is a JOB and does not "
                "exist in the landscape rollup at all. Returns EVERY candidate with its "
                "entity kind and source root, because a project can genuinely contain two "
                "different things with the same name. Component coverage at project level "
                "only includes components that touch a channel, endpoint or resource; pass "
                "job=<design name> to search every component of one design."
            ),
            input_model=FindTopologyNodeInput,
            handler=find_topology_node,
        ),
        TopologyToolSpec(
            name="get_topology_node",
            description=(
                "Get one system or integration application with its evidence and its "
                "immediate relationships. Accepts either an id or a NAME in name_or_id - "
                "do not call find_topology_node first. If the name matches more than one "
                "entity the result lists every candidate instead of guessing, and you "
                "should then ask the user which one or call again with an id. Also "
                "accepts a CONNECTION: pass the edge key or stream id from the page "
                "scope and it returns that relationship - both endpoints, mechanism, "
                "protocols, business objects and provenance - and says plainly that a "
                "relationship matched rather than an entity."
            ),
            input_model=GetTopologyNodeInput,
            handler=get_topology_node,
        ),
        TopologyToolSpec(
            name="list_topology_dependencies",
            description=(
                "Answer 'what depends on X?' and 'what does X depend on?' for one system "
                "or integration application. Accepts either an id or a NAME in "
                "name_or_id. Set direction to upstream (things X depends on), downstream "
                "(things depending on X), or both (default). hops is 1 (default) or 2; "
                "2 is the maximum."
            ),
            input_model=ListTopologyDependenciesInput,
            handler=list_topology_dependencies,
        ),
        TopologyToolSpec(
            name="get_job_detail",
            description=(
                "Get ONE design (job or route) as it was built: its components, how they "
                "are wired, which designs call it and which it calls, the external systems "
                "it touches, and its logging and error-handling structure. This is the "
                "level a question about an implementation or a data flow between two "
                "systems is actually about - the landscape rollup has no jobs in it. "
                "Accepts a NAME in name_or_id; do not call find_topology_node first. On a "
                "composite upload pass source_root to pin one partition's copy. A real "
                "design has ~250 components, so the component and connection listings are "
                "paged: each reports its total and its next_offset, and passing "
                "offset=<that value> reads the next page. Set include_parameters=true to "
                "get the CONFIGURED SETTINGS of every component on the page in this one "
                "call - the scalar parameters plus the row-structured tables where a "
                "design's decision logic lives (header/variable values, filter "
                "conditions, XML/JSON field mappings). Use that instead of calling "
                "get_component_detail once per component when you are looking for where "
                "something is decided and do not yet know which component decides it. "
                "Says so explicitly when the design is not in the scan; never describe a "
                "design you did not read."
            ),
            input_model=GetJobDetailInput,
            handler=get_job_detail,
        ),
        TopologyToolSpec(
            name="get_component_detail",
            description=(
                "Get ONE component inside one design: what it is, what feeds it, what it "
                "feeds, its configured settings (scalar parameters plus row-structured "
                "tables such as header/variable values, filter conditions and field "
                "mappings), and - for mapping components only - its column-level mapping "
                "tables and expressions. Requires job and component names. To read the "
                "settings of SEVERAL components of one design, call get_job_detail with "
                "include_parameters=true instead of calling this once per component. Most "
                "components publish no deeper mapping graph, and the result says that "
                "outright rather than returning an empty mapping: report it as 'no "
                "column-level mapping is published for this component', never as 'no "
                "transformations'."
            ),
            input_model=GetComponentDetailInput,
            handler=get_component_detail,
        ),
        TopologyToolSpec(
            name="get_field_lineage",
            description=(
                "THE operation for 'what data moves, and how is it transformed?'. Returns "
                "field-level lineage: which source column lands in which target column, "
                "through which expression, in which component, and which target tables "
                "are written. Takes job=<design name> or group=<functional group name> - "
                "a group covers a whole integration's designs at once. This is deeper "
                "than get_component_detail, which reaches only one mapping component: use "
                "this for a design, a group, or any question about columns, fields, "
                "mappings or transformations. Leads with a rollup (how many field mappings "
                "exist, how many the scan established, how many are decided at RUNTIME) "
                "and then one page of concrete mappings; pass offset=<next_offset> for the "
                "next page. Runtime-determined mappings are COUNTED, never dropped: they "
                "are roughly two in five across this corpus, so report that count - and "
                "never report a mapping the scan could not resolve as an absence of "
                "transformation."
            ),
            input_model=GetFieldLineageInput,
            handler=get_field_lineage,
        ),
    )
}


def list_topology_tool_definitions() -> list[RuntimeToolDefinition]:
    return [
        RuntimeToolDefinition(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_model.model_json_schema(),
        )
        for spec in _TOOL_SPECS.values()
    ]


def resolve_topology_tool_call(
    tool_name: str, arguments: dict[str, Any]
) -> tuple[TopologyToolSpec, TopologyProjectInput]:
    spec = _TOOL_SPECS.get(tool_name)
    if spec is None:
        raise UnknownTopologyToolError(tool_name)
    try:
        params = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        raise TopologyToolValidationError(str(exc)) from exc
    return spec, params


async def run_topology_tool(
    spec: TopologyToolSpec,
    params: TopologyProjectInput,
    *,
    provider: TopologyProvider,
    access_token: str | None,
) -> TopologyToolPayload:
    return await spec.handler(params, provider=provider, access_token=access_token)
