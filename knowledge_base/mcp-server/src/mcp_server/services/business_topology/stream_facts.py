"""Scanner-side facts for topology enrichment: designs, components, lineage.

Read-only and total. Every loader here returns an empty index plus a recorded
reason instead of raising, because enrichment is additive: a malformed or
missing scan artifact must degrade the overlay, never take down Phase 3.7.

Nothing in this module invents meaning. Component roles are derived from the
component TYPE and its POSITION in the design's data graph (a node with no
incoming data edge reads; a node with no outgoing data edge writes), which is
the same reading a developer does when opening the design.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from mcp_server.services.business_topology.evidence import artifact_evidence_id

#: Connector names that carry DATA between two components (as opposed to
#: triggers such as ``SUBJOB_OK``/``RUN_IF`` that only order subjobs).
_DATA_CONNECTORS = frozenset(
    {
        "FLOW",
        "MAIN",
        "LOOKUP",
        "FILTER",
        "UNIQUE",
        "DUPLICATE",
        "ITERATE",
        "SPLIT",
        "AGGREGATE",
        "ROUTE",
        "ROUTE_WHEN",
        "ROUTE_OTHER",
        "ROUTE_ENDBLOCK",
        "ROUTE_TRY",
    }
)

#: Trigger connectors whose edge may carry a condition of its own.
_TRIGGER_CONNECTORS = frozenset(
    {"RUN_IF", "SUBJOB_OK", "SUBJOB_ERROR", "COMPONENT_OK", "COMPONENT_ERROR"}
)

#: Logging / control components: real, but never part of the delivery.
_UTILITY_TYPES = frozenset(
    {
        "tLogRow",
        "tLogCatcher",
        "tStatCatcher",
        "tFlowMeter",
        "tFlowMeterCatcher",
        "tAssertCatcher",
        "tWarn",
        "tDie",
        "tJava",
        "tSetGlobalVar",
        "tPrejob",
        "tPostjob",
        "tChronometerStart",
        "tChronometerStop",
        "tSleep",
        "tContextLoad",
        "tContextDump",
        "tSendMail",
        "cLog",
    }
)

#: In-memory buffers and job-to-route hand-offs. They look like outputs by
#: name but the data does not leave the integration layer through them.
_INTERNAL_BUFFER_TYPES = frozenset(
    {
        "tHashOutput",
        "tHashInput",
        "tBufferOutput",
        "tBufferInput",
        "tRouteOutput",
        "tRouteInput",
        "tRunJob",
        "cTalendJob",
    }
)

#: Request/response clients: they read when they start a flow, write otherwise.
_REQUEST_CLIENT_TYPES = frozenset(
    {
        "tRESTClient",
        "tHttpRequest",
        "tHTTPRequest",
        "tHTTPClient",
        "tSOAP",
        "tWebService",
        "tWebServiceInput",
        "tESBConsumer",
    }
)

#: Components that RECEIVE what the source side emits: the source pushes.
_PUSH_RECEIVER_TYPES = frozenset(
    {
        "tRESTRequest",
        "tESBProviderRequest",
        "tJMSInput",
        "tKafkaInput",
        "tMomInput",
        "tMQInput",
        "tAMQPInput",
    }
)

#: ESB route endpoint families (Camel URIs). Prefix-matched on the type.
_ROUTE_ENDPOINT_PREFIXES = (
    "cJMS",
    "cMQ",
    "cActiveMQ",
    "cAMQP",
    "cKafka",
    "cCXF",
    "cREST",
    "cHttp",
    "cJetty",
    "cServlet",
    "cSOAP",
    "cFile",
    "cFTP",
    "cSFTP",
    "cMail",
    "cTimer",
    "cQuartz",
    "cAWS",
    "cAzure",
    "cMessagingEndpoint",
)

#: Route consumer endpoints that are driven by the sender (push) ...
_ROUTE_PUSH_PREFIXES = (
    "cJMS",
    "cMQ",
    "cActiveMQ",
    "cAMQP",
    "cKafka",
    "cCXF",
    "cREST",
    "cHttp",
    "cJetty",
    "cServlet",
    "cSOAP",
)
#: ... and those that poll or fire on a clock (pull).
_ROUTE_PULL_PREFIXES = ("cFile", "cFTP", "cSFTP", "cMail", "cTimer", "cQuartz", "cAWS", "cAzure")

#: Session management around a real reader/writer (tDBConnection, tMSSqlCommit,
#: tFTPClose ...): they open and close the pipe, they do not move the data.
_SESSION_SUFFIXES = ("Connection", "Commit", "Rollback", "Close")
#: Route-level configuration nodes that sit unconnected on the canvas.
_ROUTE_CONFIG_TYPES = frozenset(
    {"cConfig", "cBeanRegister", "cMQConnectionFactory", "cJMSConnectionFactory"}
)

_SOURCE_SUFFIXES = ("Get", "List", "FileList", "Receive", "Read")
_SINK_SUFFIXES = ("Put", "Send", "Write", "Insert", "Upsert")


def component_type_family(component_type: str) -> str:
    """Return ``utility``/``internal``/``request``/``push_receiver``/
    ``route_endpoint``/``route_step``/``input``/``output``/``transform``."""

    ctype = component_type.strip()
    if not ctype:
        return "transform"
    if ctype in _UTILITY_TYPES or ctype.endswith(_SESSION_SUFFIXES) or ctype in _ROUTE_CONFIG_TYPES:
        return "utility"
    if ctype in _INTERNAL_BUFFER_TYPES:
        return "internal"
    if ctype in _REQUEST_CLIENT_TYPES:
        return "request"
    if ctype in _PUSH_RECEIVER_TYPES:
        return "push_receiver"
    if ctype.startswith("c") and ctype[1:2].isupper():
        if ctype.startswith(_ROUTE_ENDPOINT_PREFIXES):
            return "route_endpoint"
        return "route_step"
    if "Input" in ctype or ctype.endswith(_SOURCE_SUFFIXES):
        return "input"
    if "Output" in ctype or ctype.endswith(_SINK_SUFFIXES):
        return "output"
    return "transform"


def _param_map(item: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for param in item.get("params") or []:
        if not isinstance(param, Mapping):
            continue
        name = str(param.get("name") or "")
        if name and name not in result:
            result[name] = str(param.get("value") if param.get("value") is not None else "")
    return result


def is_active(item: Mapping[str, Any]) -> bool:
    """Scanner convention: ACTIVATE absent means active, ``false`` disabled.

    The scanner materialises ``active`` on newer scans; older scans only carry
    the raw ACTIVATE parameter, so both are read and either can disable.
    """

    if item.get("active") is False:
        return False
    return _param_map(item).get("ACTIVATE", "").strip().casefold() != "false"


@dataclass(frozen=True)
class Design:
    """One scanned job/route/joblet, indexed for enrichment."""

    artifact_id: str
    name: str
    job_id: str
    kind: str
    is_route: bool
    active: bool
    version: str
    platform: str
    components: tuple[dict[str, Any], ...]
    connections: tuple[dict[str, Any], ...]

    @property
    def keys(self) -> tuple[str, ...]:
        """Exact identities this design is known under (no normalisation)."""

        return tuple(dict.fromkeys(key for key in (self.job_id, self.name) if key))

    def component(self, name: str) -> dict[str, Any] | None:
        return self._components_by_name.get(name)

    @cached_property
    def _components_by_name(self) -> dict[str, dict[str, Any]]:
        by_name: dict[str, dict[str, Any]] = {}
        for item in self.components:
            by_name.setdefault(str(item.get("unique_name") or ""), item)
        return by_name

    @cached_property
    def graph(self) -> "DesignGraph":
        return design_graph(self)


@dataclass
class ScanIndex:
    platform: str = ""
    designs: dict[str, Design] = field(default_factory=dict)
    #: exact lineage ``job_name`` -> entries
    lineage: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: design key -> caller design keys (manifest ``job_called_by``)
    called_by: dict[str, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def lineage_for(self, design: Design) -> list[dict[str, Any]]:
        seen: set[int] = set()
        rows: list[dict[str, Any]] = []
        for key in design.keys:
            for entry in self.lineage.get(key, []):
                if id(entry) in seen:
                    continue
                seen.add(id(entry))
                rows.append(entry)
        return rows

    def callers_of(self, design: Design) -> list[str]:
        callers: set[str] = set()
        for key in design.keys:
            callers.update(self.called_by.get(key, []))
        names = {
            other.name or other.job_id
            for other in self.designs.values()
            if set(other.keys) & callers
        }
        # Unresolvable caller keys are still facts; keep them verbatim.
        resolved_keys = {
            key for other in self.designs.values() for key in other.keys
        }
        names.update(key for key in callers if key not in resolved_keys)
        return sorted(names)


def _load_json(path: Path, problems: list[str]) -> Any:
    if not path.is_file():
        problems.append(f"{path.name} not found")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        problems.append(f"{path.name} unreadable ({type(exc).__name__})")
        return None


def load_scan_index(
    scan_dir: Path, unit_allowlist: frozenset[str] | None = None
) -> ScanIndex:
    """Index the scan cache. Total: problems are recorded, never raised.

    ``unit_allowlist`` has the semantics of
    :func:`build_business_topology_evidence`: a job is in scope when its
    ``job_id`` (or, lacking one, its ``name``) is allowlisted.
    """

    index = ScanIndex()
    scan = _load_json(scan_dir / "scan_result.json", index.problems)
    if not isinstance(scan, Mapping):
        return index
    index.platform = str(scan.get("platform") or "")
    for job in scan.get("jobs") or []:
        if not isinstance(job, Mapping):
            continue
        if unit_allowlist is not None and str(
            job.get("job_id") or job.get("name") or ""
        ) not in unit_allowlist:
            continue
        try:
            artifact_id = artifact_evidence_id(dict(job))
        except (TypeError, ValueError):
            continue
        kind = str(job.get("artifact_kind") or "")
        index.designs[artifact_id] = Design(
            artifact_id=artifact_id,
            name=str(job.get("name") or ""),
            job_id=str(job.get("job_id") or ""),
            kind=kind,
            is_route=bool(job.get("is_esb_route")) or kind in {"route", "routelet"},
            active=job.get("active") is not False,
            version=str(job.get("version") or ""),
            platform=index.platform,
            components=tuple(
                dict(item) for item in job.get("components") or [] if isinstance(item, Mapping)
            ),
            connections=tuple(
                dict(item) for item in job.get("connections") or [] if isinstance(item, Mapping)
            ),
        )
    in_scope_keys = {key for design in index.designs.values() for key in design.keys}

    lineage = _load_json(scan_dir / "scan" / "lineage.json", index.problems)
    if isinstance(lineage, Mapping):
        for entry in lineage.get("entries") or []:
            if not isinstance(entry, Mapping):
                continue
            job_name = str(entry.get("job_name") or "")
            if job_name and job_name in in_scope_keys:
                index.lineage.setdefault(job_name, []).append(dict(entry))

    manifest = _load_json(scan_dir / "scan" / "manifest.json", index.problems)
    if isinstance(manifest, Mapping):
        called_by = manifest.get("job_called_by")
        if isinstance(called_by, Mapping):
            for key, callers in called_by.items():
                if isinstance(callers, list):
                    index.called_by[str(key)] = sorted(
                        {str(item) for item in callers if str(item)}
                    )
    return index


# --- Design graph -------------------------------------------------------------


@dataclass(frozen=True)
class DesignGraph:
    """Active data edges of one design and each component's role."""

    order: tuple[str, ...]
    roles: dict[str, str]
    types: dict[str, str]


def _is_data_connector(connector: str) -> bool:
    return connector in _DATA_CONNECTORS or connector.startswith("OUTPUT_")


def design_graph(design: Design) -> DesignGraph:
    active = {
        str(item.get("unique_name") or ""): str(item.get("component_type") or "")
        for item in design.components
        if is_active(item) and str(item.get("unique_name") or "")
    }
    incoming: dict[str, set[str]] = {name: set() for name in active}
    outgoing: dict[str, set[str]] = {name: set() for name in active}
    for connection in design.connections:
        if not is_active(connection):
            continue
        if not _is_data_connector(str(connection.get("connector_name") or "")):
            continue
        source = str(connection.get("source") or "")
        target = str(connection.get("target") or "")
        if source in active and target in active and source != target:
            outgoing[source].add(target)
            incoming[target].add(source)

    roles: dict[str, str] = {}
    for name, ctype in active.items():
        family = component_type_family(ctype)
        starts = not incoming[name]
        ends = not outgoing[name]
        if family in {"utility", "internal"}:
            roles[name] = family
        elif family in {"input", "push_receiver"}:
            roles[name] = "source"
        elif family == "output":
            roles[name] = "sink"
        elif family == "request":
            roles[name] = "source" if starts and not ends else "sink"
        elif family == "route_endpoint":
            if starts and not ends:
                roles[name] = "source"
            elif ctype.startswith(("cTimer", "cQuartz")):
                roles[name] = "source"
            else:
                roles[name] = "sink"
        else:
            roles[name] = "transform"

    # Breadth-first from the reading nodes, in name order, for a stable chain.
    # Only nodes on a data edge start the walk: an unconnected node (a session
    # opener, a config bean) is not where the data enters. They are appended
    # last so the order stays total.
    connected = {name for name in active if incoming[name] or outgoing[name]}
    starts = sorted(name for name in connected if not incoming[name])
    order: list[str] = []
    seen: set[str] = set()
    frontier = list(starts)
    while frontier:
        next_frontier: list[str] = []
        for name in frontier:
            if name in seen:
                continue
            seen.add(name)
            order.append(name)
            next_frontier.extend(sorted(outgoing[name] - seen))
        frontier = next_frontier
    order.extend(sorted(connected - seen))
    order.extend(sorted(set(active) - connected - seen))
    return DesignGraph(order=tuple(order), roles=roles, types=active)


# --- Per-design facts ---------------------------------------------------------


def design_label(design: Design) -> str:
    platform = (design.platform or "").strip()
    if design.is_route:
        return "ESB routelet" if design.kind == "routelet" else "ESB route"
    if platform.casefold() == "talend" or not platform:
        if design.kind == "joblet":
            return "Talend joblet"
        return "Talend DI job"
    return f"{platform} {design.kind or 'design'}".strip()


def mechanism_chain(design: Design, *, max_steps: int = 5) -> str:
    """``"Talend DI job: tDBInput -> tMap -> tRESTClient"`` or ``""``."""

    graph = design.graph
    steps: list[str] = []
    for name in graph.order:
        if graph.roles.get(name) in {"utility", "internal"}:
            continue
        ctype = graph.types.get(name, "")
        if ctype and ctype not in steps:
            steps.append(ctype)
    if not steps:
        return ""
    shown = steps[:max_steps]
    chain = " -> ".join(shown)
    if len(steps) > max_steps:
        chain += f" -> ... (+{len(steps) - max_steps} component types)"
    return f"{design_label(design)}: {chain}"


def direction_signals(design: Design) -> tuple[set[str], list[str]]:
    """Return ``({"push", "pull"} subset, supporting notes)`` for one design."""

    graph = design.graph
    signals: set[str] = set()
    notes: list[str] = []
    for name in graph.order:
        if graph.roles.get(name) != "source":
            continue
        ctype = graph.types.get(name, "")
        family = component_type_family(ctype)
        if family == "push_receiver" or (
            family == "route_endpoint" and ctype.startswith(_ROUTE_PUSH_PREFIXES)
        ):
            signals.add("push")
            notes.append(f"{design.name}/{name} ({ctype}) receives")
        elif family in {"input", "request"} or (
            family == "route_endpoint" and ctype.startswith(_ROUTE_PULL_PREFIXES)
        ):
            signals.add("pull")
            notes.append(f"{design.name}/{name} ({ctype}) reads")
    return signals, notes


def deactivated_components(design: Design) -> list[str]:
    return sorted(
        str(item.get("unique_name") or "")
        for item in design.components
        if not is_active(item) and str(item.get("unique_name") or "")
    )


def io_components(design: Design) -> list[dict[str, Any]]:
    return [
        item
        for item in design.components
        if component_type_family(str(item.get("component_type") or ""))
        in {"input", "output", "request", "push_receiver", "route_endpoint"}
    ]


def design_is_stopped(design: Design) -> bool:
    """Deactivated as a whole, or every I/O component in it is deactivated."""

    if not design.active:
        return True
    io = io_components(design)
    return bool(io) and all(not is_active(item) for item in io)


@dataclass(frozen=True)
class EdgeCondition:
    kind: str
    statement: str
    evidence_note: str


def edge_conditions(design: Design) -> list[EdgeCondition]:
    """Branch predicates (``ROUTE_WHEN``) and conditional triggers (``RUN_IF``).

    Read from ``Connection.params`` (``CONDITION`` + ``ROUTETYPE``). Scans that
    predate edge parameters carry none, and yield nothing -- never a guess.
    """

    results: list[EdgeCondition] = []
    for connection in design.connections:
        connector = str(connection.get("connector_name") or "")
        params = _param_map(connection)
        condition = params.get("CONDITION", "").strip()
        if not condition:
            continue
        source = str(connection.get("source") or "")
        target = str(connection.get("target") or "")
        edge = str(connection.get("name") or "")
        note = f"{design.name} connection {edge or '?'} ({source} -> {target}, {connector})"
        if not is_active(connection):
            note += "; edge deactivated"
        if connector == "ROUTE_WHEN":
            language = params.get("ROUTETYPE", "").strip()
            statement = (
                f"{target} receives only messages matching "
                f"{language + ' ' if language else ''}predicate {condition}"
            )
            results.append(EdgeCondition("filter", statement, note))
        elif connector in _TRIGGER_CONNECTORS:
            statement = f"{target} runs after {source} only when {condition}"
            results.append(EdgeCondition("trigger", statement, note))
    return results


# --- Lineage ------------------------------------------------------------------


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def lineage_expression(entry: Mapping[str, Any], *, max_chars: int = 2000) -> str:
    """The verbatim transformation rule(s) of one lineage entry, in order."""

    expressions: list[str] = []
    for step in entry.get("transformations") or []:
        if not isinstance(step, Mapping):
            continue
        expression = str(step.get("expression") or "").strip()
        if expression and expression not in expressions:
            expressions.append(expression)
    text = " -> ".join(expressions)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " ... [truncated]"
    return text


def lineage_category(entry: Mapping[str, Any]) -> str:
    kinds: list[str] = []
    for step in entry.get("transformations") or []:
        if not isinstance(step, Mapping):
            continue
        kind = str(step.get("transformation_type") or "").strip()
        if kind and kind not in kinds:
            kinds.append(kind)
    return "+".join(kinds)


def entry_reaches_target(design: Design, entry: Mapping[str, Any]) -> str:
    target = str(entry.get("target_component") or "")
    component = design.component(target) if target else None
    if component is not None and not is_active(component):
        return "disabled"
    if not entry.get("resolved") or not target:
        return "unknown"
    return "yes" if design.graph.roles.get(target) == "sink" else "unknown"


def source_schema_columns(design: Design) -> tuple[set[str] | None, str]:
    """Columns declared on the design's reading components' row schemas.

    ``(None, reason)`` when nothing enumerable is recorded: no schemas on this
    scan, or a dynamic schema (an unknown number of real columns).
    """

    graph = design.graph
    columns: set[str] = set()
    saw_schema = False
    for name, role in sorted(graph.roles.items()):
        if role != "source":
            continue
        component = design.component(name) or {}
        for schema in component.get("schemas") or []:
            if not isinstance(schema, Mapping):
                continue
            connector = str(schema.get("connector") or "").upper()
            if connector not in {"", "FLOW", "MAIN"}:
                continue
            if schema.get("dynamic"):
                return None, f"{design.name}/{name} declares a dynamic schema"
            for column in schema.get("columns") or []:
                if isinstance(column, Mapping) and _norm(column.get("name")):
                    saw_schema = True
                    columns.add(_norm(column.get("name")).casefold())
    if not saw_schema:
        return None, "source row schemas not recorded by scanner"
    return columns, ""


def column_key(value: Any) -> str:
    return _norm(value).casefold()


def iter_sorted_entries(entries: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        entries,
        key=lambda entry: (
            str(entry.get("job_name") or ""),
            str(entry.get("source_table") or ""),
            str(entry.get("source_column") or ""),
            str(entry.get("target_table") or ""),
            str(entry.get("target_column") or ""),
            str(entry.get("source_component") or ""),
            str(entry.get("target_component") or ""),
            lineage_expression(entry),
        ),
    )
