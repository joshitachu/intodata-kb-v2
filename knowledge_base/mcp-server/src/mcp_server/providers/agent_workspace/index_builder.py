"""``index/*.jsonl``: the deterministic navigation layer (PRD 8.1-8.5).

The last production run over the reference estate (run 250, 142 designs,
15.6 MB of parsed evidence) opened 8 design files and published 92 KB. Every
omission that was checked by hand afterwards was an ENUMERATION failure, not
an analysis failure: 19 designs carry a literal ``cTimer PERIOD`` -- one of
them ``30`` milliseconds, verified in that estate's ``scan/jobs/`` -- and the
documentation said "Cadence: Not established" nine times; 52 designs have
active error constructs and 34 were listed; 0 of 19 routines were addressed.
Each of those is a join over data that was already staged. This module is that
join: pure Python over the parsed scanner output, no model, no heuristic that
needs one.

What it writes, and what it deliberately does not:

* **``candidates``** -- starting points (PRD 8.1). A row says *why* an element
  might carry an integration and never that it *is* one; the classification
  is the agent's (PRD 9, 10.1).
* **``relationships``** -- normalised edges with a state and a canonical
  evidence reference (PRD 8.2). **Every forward row has a generated reverse
  row.** "What breaks if I change this?" is unanswerable without ``used-by``,
  ``called-by`` and ``handles-errors-for``, and the 0-of-19 routine result
  above is exactly what an estate looks like when nothing indexes them.
* **``fields``** -- field lineage (PRD 8.3), including the rows that are
  usually missing: fields the mapper was offered and never wrote
  (``dropped``), inputs whose schema could not be enumerated, and lineage the
  scanner could not resolve. A silently absent row reads as "no such field".
* **``configuration``** -- safe configuration METADATA joined to what consumes
  it (PRD 8.4). No value is ever written. A redacted value is ``redacted``,
  never absent: on the reference estate a client-secret variable carries the
  ``[REDACTED]`` marker in every environment and an EMPTY descriptor map, so a
  consumer keyed on descriptors alone would report it as a configured literal.
* **``runtime``** -- the operational index (PRD 8.5): executions, bindings
  between deployed and scanned units, runtime-only units as unresolved
  records, and the STATIC error and monitoring joins (which need no mirror).

Not written here, and stated in the manifest rather than left to be inferred
from an absent file: ``units`` and ``interfaces`` are already
``facts/navigation/units`` and ``facts/navigation/interfaces`` +
``channel_pairings`` (``navigation.py``), row for row; ``integrations`` is the
agent's own discovery and lives in ``notes/integration_model.json``.

File conventions follow ``navigation.py``, which settled them: one complete
JSON record per line so ``grep`` returns a record and not a fragment; shards
split by BYTES, never by row count; a manifest naming every index, its shards
in order, its row count and the canonical artifact its rows resolve to; an
index with no rows on this estate is absent and the manifest says so. Every
builder sorts on a TOTAL key that ends in the serialised row, never on a
lossy one -- a set sorted on ``casefold`` alone once left two real group names
differing only in case in set-iteration order, and four runs of identical
input produced three different files.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_server.providers.agent_workspace.contract import (
    FACTS,
    NAVIGATION_SHARD_BYTES,
    RUNTIME,
    SCAN,
    WorkspaceUnavailableError,
)
from mcp_server.providers.agent_workspace.fact_state import (
    STATE_MEANINGS,
    FactState,
    state_from_resolution,
    state_of_value,
    weakest,
)
from mcp_server.providers.agent_workspace.staged_data import (
    StagedDataError,
    read_complete_list,
)

MANIFEST_SCHEMA = "agent-workspace-index-1"

#: The workspace-root directory the PRD names (section 6). NOT under
#: ``facts/`` -- these indexes are derived from ``facts/`` AND ``scan/`` AND
#: ``runtime/`` and would be mis-described as facts.
INDEX_DIR = Path("index")
INDEX_MANIFEST = INDEX_DIR / "manifest.json"

#: One ceiling for one reason. A shard has to be readable whole when grep is
#: not enough, and ``navigation.py`` already chose the number for that.
INDEX_SHARD_BYTES = NAVIGATION_SHARD_BYTES

INDEX_ORDER: tuple[str, ...] = (
    "candidates",
    "relationships",
    "fields",
    "configuration",
    "runtime",
)

#: What each index answers and where a row resolves to. Used for the manifest
#: and offered to whoever renders ``INDEX.md`` so the two cannot disagree.
INDEX_DOC: dict[str, tuple[str, str]] = {
    "candidates": (
        "starting points that MAY carry an integration -- entry jobs, interfaces, "
        "schedules, event producers and consumers, API clients and services, "
        "cross-project calls, runtime-observed workloads -- each with the evidence "
        "that made it a candidate; never a verdict",
        "scan/jobs/<group>/<design>.json, facts/interfaces.json, runtime/evidence.json",
    ),
    "relationships": (
        "every normalised relationship between units, shared components, interfaces, "
        "configuration and runtime records, forward AND reverse, each with a state",
        "facts/dependencies.json, facts/interfaces.json, facts/configuration.json, "
        "scan/jobs/<group>/<design>.json, runtime/evidence.json",
    ),
    "fields": (
        "field lineage per mapper output field, distinguishing direct, rename, constant, "
        "default, lookup, calculation, conditional, filter, dropped, dynamic and "
        "unresolved rows",
        "facts/mappings/<design>__<mapper>.md, scan/mapping_accounting.json, scan/lineage.json",
    ),
    "configuration": (
        "safe configuration metadata -- names, types, environments, per-environment "
        "value STATE, never a value -- joined to the units, components and parameter "
        "roles that consume it",
        "facts/configuration.json, scan/connections.json, scan/jobs/<group>/<design>.json",
    ),
    "runtime": (
        "the operational picture: mirror status, per-unit executions and failures, "
        "deployed-to-scanned bindings, runtime-only units, and the static error-handling "
        "and monitoring joins",
        "runtime/evidence.json, scan/jobs/<group>/<design>.json, facts/units.json",
    ),
}

#: PRD section 6 names three more index files. They are not built here, on
#: purpose, and the manifest says where each already lives.
NOT_BUILT: dict[str, str] = {
    "units": (
        "already written row-for-row as facts/navigation/units/ by navigation.py; "
        "a second unit list would be a second source of truth"
    ),
    "interfaces": (
        "already written as facts/navigation/interfaces/ (per binding) and "
        "facts/navigation/channel_pairings/ (per channel identity) by navigation.py"
    ),
    "integrations": (
        "an integration is the agent's discovery (PRD 9); the deterministic layer "
        "must not assert one. The agent's model is notes/integration_model.json and "
        "the compiled catalogue is output/reference/integrations.md"
    ),
}

# ---------------------------------------------------------------------------
# Relationship vocabulary
#
# Forward relation -> its generated inverse. Closed: a builder that needs a
# relation not listed here has to add it HERE, with its inverse, so a forward
# row can never exist without its reverse.
# ---------------------------------------------------------------------------

RELATION_INVERSE: dict[str, str] = {
    "calls": "called-by",
    "uses-joblet": "joblet-used-by",
    "reads-from": "read-by",
    "writes-to": "written-by",
    "exchanges-with": "exchanged-with",
    "maps-with": "mapped-by",
    "configured-by": "configures",
    "queries": "queried-by",
    "routes-errors-to": "handles-errors-for",
    "monitored-by": "monitors",
    "publishes-to": "published-by",
    "consumes": "consumed-by",
    "invokes-routine": "used-by",
    "implements-interface": "implemented-by",
    "runtime-observed-as": "runtime-record-of",
}

RELATION_MEANINGS: dict[str, str] = {
    "calls": "a job or route starts another job (tRunJob, cTalendJob)",
    "uses-joblet": "a design embeds a shared joblet or routelet",
    "reads-from": "a unit reads an interface (file, database table, GET-style API call)",
    "writes-to": "a unit writes an interface (file, table, mail, mutating API call)",
    "exchanges-with": "a unit is bound to an interface whose direction the parser did not settle",
    "maps-with": "a unit owns a mapper whose exact table is in facts/mappings/",
    "configured-by": "a unit is attached to a context group or repository connection",
    "queries": "a unit issues a query against a database",
    "routes-errors-to": (
        "an error path leads from the source to the target (error connector, exception "
        "handler, or a handler chain that invokes another unit)"
    ),
    "monitored-by": "a unit's events are caught by a catcher component",
    "publishes-to": "a unit produces onto a channel (JMS, direct, seda ...)",
    "consumes": "a unit consumes from a channel",
    "invokes-routine": "a unit calls a shared routine",
    "implements-interface": "a unit exposes a service endpoint (REST, SOAP, HTTP provider)",
    "runtime-observed-as": "a scanned unit was matched to a runtime job in the mirror",
}

# ---------------------------------------------------------------------------
# Scanner vocabularies
#
# Mirrored from ``providers/custom/enterprise/workspace.py`` rather than
# imported: those names are private to a module this lane otherwise only calls
# through ``build_exact_tables``. The mirror is PINNED by
# ``tests/test_agent_workspace/test_index_builder.py``, which is the same
# arrangement ``contract.CODE_PARAM_NAMES`` has with the scanner.
# ---------------------------------------------------------------------------

ERROR_COMPONENTS: frozenset[str] = frozenset(
    {
        "tDie",
        "tWarn",
        "tLogCatcher",
        "tStatCatcher",
        "tAssertCatcher",
        "tFlowMeterCatcher",
        "cOnException",
        "cErrorHandler",
        "cDeadLetterChannel",
        "cTry",
    }
)
ERROR_CONNECTORS: frozenset[str] = frozenset(
    {
        "REJECT",
        "COMPONENT_ERROR",
        "SUBJOB_ERROR",
        "FAULT",
        "ON_COMPONENT_ERROR",
        "ON_SUBJOB_ERROR",
        "REJECTS",
        "ERROR",
        "TRIGGER_OUTPUT_ERROR",
    }
)
#: Catchers: components that RECEIVE events rather than raise them. A subset
#: of :data:`ERROR_COMPONENTS`; split out because ``tDie`` raises and
#: ``tLogCatcher`` observes, and only the second is monitoring.
MONITOR_COMPONENTS: frozenset[str] = frozenset(
    {"tLogCatcher", "tStatCatcher", "tAssertCatcher", "tFlowMeterCatcher"}
)
CALL_COMPONENTS: frozenset[str] = frozenset({"cTalendJob", "tRunJob"})
SCHEDULER_COMPONENTS: frozenset[str] = frozenset({"cTimer", "cScheduler", "cQuartz"})
#: Header-setting components whose ``VALUES`` table names message headers.
HEADER_COMPONENTS: frozenset[str] = frozenset({"cSetHeader"})
LOGGING_COMPONENTS: frozenset[str] = frozenset({"cLog"})
#: Selection / routing predicates the parser keeps as component parameters.
#: ``cMessageRouter`` is deliberately ABSENT: its ``when`` predicates are
#: connection-level ``CONDITION`` / ``ROUTETYPE`` parameters in the ``.item``
#: and the scanner emits connections without them (verified against a
#: sanitized route export on the reference estate: the ``ROUTE_WHEN``
#: connection carries a jsonpath ``CONDITION``; the parsed connection carries
#: only name, source, target, connector name, line style and active flag).
FILTER_COMPONENTS: frozenset[str] = frozenset({"cMessageFilter"})
ROUTING_COMPONENTS: frozenset[str] = frozenset(
    {"cMessageRouter", "cRecipientList", "cDynamicRouter", "cRoutingSlip"}
)

MESSAGING_CHANNEL_TYPES: frozenset[str] = frozenset(
    {"jms", "activemq", "amqp", "kafka", "mqtt", "mq", "topic", "queue", "sqs", "servicebus"}
)
IN_MEMORY_CHANNEL_TYPES: frozenset[str] = frozenset(
    {"direct", "direct-vm", "directvm", "vm", "seda"}
)
#: Channel types whose endpoints are visible only inside ONE CamelContext.
#: Every Talend route is deployed as its own context, so a ``direct:`` name
#: shared by two standalone routes is two channels; only a routelet or joblet
#: EMBEDDED in the route shares its context. Verified consequence of ignoring
#: this: 30 routes each carrying their own ``direct:`` error collector were
#: joined to each other as mutual error handlers (905 rows, ~30 per route).
CONTEXT_LOCAL_CHANNEL_TYPES: frozenset[str] = frozenset({"direct", "seda"})
#: Channel types that cross contexts: a broker, or the JVM-wide Camel endpoints.
CROSS_CONTEXT_CHANNEL_TYPES: frozenset[str] = MESSAGING_CHANNEL_TYPES | frozenset(
    {"direct-vm", "directvm", "vm"}
)
SERVICE_ENDPOINT_TYPES: frozenset[str] = frozenset(
    {"rest", "soap", "http", "https", "ws", "cxf", "cxfrs", "esb"}
)
API_FAMILIES: frozenset[str] = frozenset({"rest", "soap", "esb"})
MESSAGING_FAMILIES: frozenset[str] = frozenset({"messaging"})
#: Component-kind interface families that are wiring, not interfaces to a
#: system: the route/job bridge is the ``calls`` edge already, in-memory
#: channels are ``publishes-to``/``consumes`` rows from the channel table, and
#: a scheduler is a ``scheduled-job`` candidate.
WIRING_FAMILIES: frozenset[str] = frozenset({"route_bridge", "in_memory", "scheduler"})

READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Parameters that are Studio decoration, never configuration.
NOISE_PARAMS: frozenset[str] = frozenset(
    {
        "LABEL",
        "WARNING",
        "NOTE",
        "NOTICE",
        "HINT",
        "INFORMATION",
        "ROUTE_BREAKPOINT_EXAMPLE",
        "CONNECTION_FORMAT",
        "UNIQUE_NAME",
    }
)
ADDRESS_PARAMS: tuple[str, ...] = (
    "URL",
    "URI",
    "URIS",
    "ENDPOINT",
    "PATH",
    "DESTINATION",
    "FILENAME",
    "DIRECTORY",
    "FILEPATH",
    "BASE_FILE_PATH",
    "SERVICE_NAME",
    "WSDL",
    "HOST",
    "PORT",
)
QUERY_PARAMS: frozenset[str] = frozenset({"QUERY", "SQLQUERY", "TABLE", "DBTABLE"})
SCHEDULE_PARAMS: frozenset[str] = frozenset(
    {"PERIOD", "DELAY", "REPEATCOUNT", "FIXEDRATE", "CRON", "CRON_EXPRESSION"}
)
CHANNEL_PARAMS: frozenset[str] = frozenset({"DESTINATION", "TOPIC", "QUEUE"})
TRANSFORMATION_PARAMS: frozenset[str] = frozenset(
    {"EXPRESSION", "EXPRESSIONTXT", "VALUES", "VALUE", "MAPPING"}
)
TRANSFORMATION_COMPONENTS: frozenset[str] = frozenset(
    {
        "cSetHeader",
        "cSetBody",
        "cSetProperty",
        "cConvertBodyTo",
        "tMap",
        "tXMLMap",
        "tHMap",
        "tXSLT",
        "tJavaRow",
        "tJava",
        "tJavaFlex",
        "tConvertType",
    }
)

#: A context variable reference. The trailing ``\b`` is load-bearing:
#: ``context.token`` must not match inside ``context.tokenId``; both are real
#: names on real estates.
_CONTEXT_REF_RE = re.compile(r"\bcontext\.([A-Za-z_][A-Za-z0-9_]*)\b")
_TERNARY_RE = re.compile(r"\?[^:]*:")
_MAPPING_TABLE_HEADER_RE = re.compile(r"^# Exact mapping table ([0-9a-f]+)\s*$")

_SAFE_ROOTS: frozenset[str] = frozenset({SCAN.as_posix(), FACTS.as_posix(), RUNTIME.as_posix()})


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


@dataclass
class IndexSource:
    """Everything the builders read, loaded once from the STAGED tree.

    Staged rather than scanned, for the same reason ``navigation.py`` reads
    staged: a row can only ever describe a file the agent actually has.
    """

    units: dict[str, dict[str, Any]] = field(default_factory=dict)
    dependencies: dict[str, Any] = field(default_factory=dict)
    interfaces: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    #: ``(unit, component, channel expression) -> channels-table row``. The
    #: per-unit binding items in ``facts/interfaces.json`` carry no
    #: ``channel_binding``; the estate-level ``channels`` list does, and on the
    #: reference estate 41 bindings are ``dynamic`` there while their
    #: expression is the parser's placeholder text, which no textual test
    #: would classify as dynamic.
    channel_bindings: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    #: ``unit_id -> projected design`` for every unit whose ``scan_path`` exists.
    designs: dict[str, dict[str, Any]] = field(default_factory=dict)
    mapping_tables: list[dict[str, Any]] = field(default_factory=list)
    #: ``table_id -> facts/mappings/<file>.md``, recovered from the table header.
    mapping_paths: dict[str, str] = field(default_factory=dict)
    lineage_entries: list[dict[str, Any]] = field(default_factory=list)
    lineage_unmapped: list[dict[str, Any]] = field(default_factory=list)
    routines: list[dict[str, Any]] = field(default_factory=list)
    services: list[dict[str, Any]] = field(default_factory=list)
    stylesheets: list[dict[str, Any]] = field(default_factory=list)
    tdm_maps: list[dict[str, Any]] = field(default_factory=list)
    connections: list[dict[str, Any]] = field(default_factory=list)
    #: ``runtime/evidence.json`` as staged, or ``None`` when it is not there.
    runtime: dict[str, Any] | None = None
    #: Relative paths of optional artifacts that were not on disk. Facts about
    #: this estate, reported in the manifest.
    absent: list[str] = field(default_factory=list)
    read: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def _fail(message: str) -> WorkspaceUnavailableError:
    return WorkspaceUnavailableError(message, stage="navigation")


def _read_required(staging_dir: Path, relative: Path, *, expect: type) -> Any:
    path = staging_dir / relative
    if not path.is_file():
        raise _fail(
            f"{relative.as_posix()} is missing from the staged workspace; the indexes "
            "cannot be built over an incomplete fact set"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _fail(f"{relative.as_posix()} is not readable JSON") from exc
    if not isinstance(payload, expect):
        raise _fail(f"{relative.as_posix()} has the wrong shape (expected {expect.__name__})")
    return payload


def _read_optional(staging_dir: Path, relative: Path, source: IndexSource, *, expect: type) -> Any:
    """An optional artifact: absent is a fact, malformed is a failure."""

    path = staging_dir / relative
    if not path.is_file():
        source.absent.append(relative.as_posix())
        return None
    source.read.append(relative.as_posix())
    return _read_required(staging_dir, relative, expect=expect)


def _read_optional_list(staging_dir: Path, relative: Path, source: IndexSource) -> list[Any]:
    """An optional list-shaped collection, reassembled when the stager split it."""

    path = staging_dir / relative
    if not path.is_file():
        source.absent.append(relative.as_posix())
        return []
    source.read.append(relative.as_posix())
    try:
        rows = read_complete_list(path)
    except StagedDataError as exc:
        raise _fail(f"{relative.as_posix()}: {exc}") from exc
    return [row for row in rows if isinstance(row, dict)]


def _read_required_list(staging_dir: Path, relative: Path, source: IndexSource) -> list[Any]:
    path = staging_dir / relative
    if not path.is_file():
        raise _fail(f"{relative.as_posix()} is missing from the staged workspace")
    source.read.append(relative.as_posix())
    try:
        rows = read_complete_list(path)
    except StagedDataError as exc:
        raise _fail(f"{relative.as_posix()}: {exc}") from exc
    return [row for row in rows if isinstance(row, dict)]


def _safe_relative(candidate: str) -> Path | None:
    relative = Path(candidate)
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[0] not in _SAFE_ROOTS
    ):
        return None
    return relative


def load_source(staging_dir: Path) -> IndexSource:
    """Read the staged tree. Fail-closed: a malformed required input raises.

    Design files are loaded for every unit whose fact row names a
    ``scan_path``: the stager writes that key only for designs it staged, so a
    named file that is not there is a broken workspace, not an optional gap.
    """

    staging_dir = Path(staging_dir)
    source = IndexSource()

    units_payload = _read_required(staging_dir, FACTS / "units.json", expect=dict)
    units = units_payload.get("units")
    if not isinstance(units, dict):
        raise _fail("facts/units.json carries no 'units' object")
    source.units = {str(unit_id): unit for unit_id, unit in units.items() if isinstance(unit, dict)}
    source.dependencies = _read_required(staging_dir, FACTS / "dependencies.json", expect=dict)
    source.interfaces = _read_required(staging_dir, FACTS / "interfaces.json", expect=dict)
    source.configuration = _read_required(staging_dir, FACTS / "configuration.json", expect=dict)
    for row in source.interfaces.get("channels") or []:
        if isinstance(row, dict):
            key = (
                str(row.get("artifact") or ""),
                str(row.get("component_name") or ""),
                str(row.get("channel") or ""),
            )
            source.channel_bindings.setdefault(key, row)
    source.read += [
        f"{FACTS.as_posix()}/units.json",
        f"{FACTS.as_posix()}/dependencies.json",
        f"{FACTS.as_posix()}/interfaces.json",
        f"{FACTS.as_posix()}/configuration.json",
    ]

    for unit_id in sorted(source.units):
        scan_path = str(source.units[unit_id].get("scan_path") or "")
        if not scan_path:
            source.notices.append(f"unit {unit_id} names no staged design; indexed from facts only")
            continue
        relative = _safe_relative(scan_path)
        if relative is None or relative.parts[0] != SCAN.as_posix():
            raise _fail(f"facts/units.json points unit {unit_id} at an unsafe path")
        path = staging_dir / relative
        if not path.is_file():
            raise _fail(
                f"{relative.as_posix()} is named by facts/units.json for unit {unit_id} "
                "but is not in the staged workspace"
            )
        try:
            design = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise _fail(f"{relative.as_posix()} is not readable JSON") from exc
        if not isinstance(design, dict):
            raise _fail(f"{relative.as_posix()} is not a design object")
        source.designs[unit_id] = design

    source.mapping_tables = _read_required_list(
        staging_dir, SCAN / "mapping_accounting.json", source
    )
    source.mapping_paths = _mapping_paths(staging_dir)

    lineage = _read_optional(staging_dir, SCAN / "lineage.json", source, expect=dict)
    if isinstance(lineage, dict):
        entries = lineage.get("entries")
        unmapped = lineage.get("unmapped_columns")
        source.lineage_entries = [row for row in entries or [] if isinstance(row, dict)]
        source.lineage_unmapped = [row for row in unmapped or [] if isinstance(row, dict)]

    source.routines = _read_optional_list(staging_dir, SCAN / "routines.json", source)
    source.services = _read_optional_list(staging_dir, SCAN / "services.json", source)
    source.stylesheets = _read_optional_list(staging_dir, SCAN / "xslt.json", source)
    source.tdm_maps = _read_optional_list(staging_dir, SCAN / "tdm_maps.json", source)
    source.connections = _read_optional_list(staging_dir, SCAN / "connections.json", source)

    runtime = _read_optional(staging_dir, RUNTIME / "evidence.json", source, expect=dict)
    source.runtime = runtime if isinstance(runtime, dict) else None
    return source


def _mapping_paths(staging_dir: Path) -> dict[str, str]:
    """``table_id -> staged path`` from each table's own first line.

    The stager renames colliding mappers, and only it knows the outcome; the
    rendered table carries ``# Exact mapping table <table_id>`` as line one,
    so the id is read back off the file that exists rather than re-derived.
    """

    mappings_dir = staging_dir / FACTS / "mappings"
    paths: dict[str, str] = {}
    if not mappings_dir.is_dir():
        return paths
    for path in sorted(mappings_dir.glob("*.md")):
        try:
            with path.open(encoding="utf-8") as handle:
                first = handle.readline()
        except OSError as exc:
            raise _fail(f"{FACTS.as_posix()}/mappings/{path.name} could not be read") from exc
        match = _MAPPING_TABLE_HEADER_RE.match(first.rstrip("\n"))
        if match is None:
            raise _fail(
                f"{FACTS.as_posix()}/mappings/{path.name} does not start with the exact "
                "mapping table header"
            )
        table_id = match.group(1)
        if table_id in paths:
            raise _fail(f"two staged mapping tables claim table id {table_id}")
        paths[table_id] = f"{FACTS.as_posix()}/mappings/{path.name}"
    return paths


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _params(component: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``name -> param`` (first occurrence wins; the parser emits each once)."""

    out: dict[str, dict[str, Any]] = {}
    for param in component.get("params") or []:
        if isinstance(param, dict):
            name = str(param.get("name") or "")
            if name and name not in out:
                out[name] = param
    return out


def _param_text(params: Mapping[str, Mapping[str, Any]], name: str) -> str | None:
    param = params.get(name)
    if param is None:
        return None
    value = param.get("value")
    return None if value is None else str(value)


def _unquote(value: str) -> str:
    """A Talend TEXT literal is written with its quotes; a reference is not."""

    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _unit_evidence(source: IndexSource, unit_id: str) -> str:
    unit = source.units.get(unit_id)
    scan_path = str((unit or {}).get("scan_path") or "") if isinstance(unit, dict) else ""
    return scan_path or f"{FACTS.as_posix()}/units.json"


def _components(design: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [item for item in design.get("components") or [] if isinstance(item, dict)]


def _connections(design: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [item for item in design.get("connections") or [] if isinstance(item, dict)]


def _is_active(item: Mapping[str, Any]) -> bool:
    return item.get("active", True) is not False


def _row_sort_key(row: Mapping[str, Any], *keys: str) -> tuple[str, ...]:
    """Total order: the named keys, then the whole serialised row."""

    return tuple(str(row.get(key, "")) for key in keys) + (
        json.dumps(row, sort_keys=True, ensure_ascii=False),
    )


def _channel_identity(
    source: IndexSource, unit_id: str, item: Mapping[str, Any]
) -> tuple[str, FactState, dict[str, Any]]:
    """``(channel_id, state, detail)`` for one channel binding.

    Three shapes, three answers. A literal name is the channel's identity and
    joins producers to consumers by NAME, as ``facts/navigation/channel_pairings``
    and the scanner's landscape already do; ``context_local`` in the detail
    says whether that join means connectivity (a broker or JVM-wide channel)
    or a shared name (``direct:``/``seda:``, one context per route). A dynamic
    binding -- flagged by the channels table, or textually a
    ``context.``/``globalMap`` form -- keeps the expression as its identity
    and is ``dynamic``. An EMPTY name (verified: 226 ``cDirect`` producers on
    the reference estate carry ``""``) gets a component-local identity:
    folding every unnamed channel into one ``channel:direct:""`` would pair
    226 producers with each other and with nothing real.
    """

    itype = str(item.get("type") or "")
    component = str(item.get("component_name") or "")
    expression = str(item.get("channel") or "")
    binding = source.channel_bindings.get((unit_id, component, expression), {})
    declared = str(binding.get("channel_binding") or "")
    dynamic = bool(item.get("dynamic")) or bool(binding.get("dynamic")) or declared == "dynamic"
    detail: dict[str, Any] = {
        "channel_type": itype,
        "binding": declared or ("dynamic" if dynamic else "literal"),
        "context_local": itype in CONTEXT_LOCAL_CHANNEL_TYPES,
    }
    stripped = expression.strip()
    if not stripped or stripped in {'""', "''"}:
        detail["note"] = "channel name not captured by the parser; identity is component-local"
        return f"channel:{itype}:{unit_id}/{component}", FactState.NOT_OBSERVED, detail
    state = state_of_value(expression)
    if state is FactState.EXACT and dynamic:
        state = FactState.DYNAMIC
    name = _unquote(expression) if state is FactState.EXACT else stripped
    return f"channel:{itype}:{name}", state, detail


_Producers = dict[tuple[str, str], list[tuple[str, FactState, str]]]
_Consumers = dict[str, list[tuple[str, str]]]


def _channel_graph(source: IndexSource) -> tuple[_Producers, _Consumers]:
    """Producers by ``(unit, component)`` and consumers by channel identity.

    Producer entries are ``(channel_id, state, channel_type)``; consumer
    entries are ``(unit, component)``. This is the hop an in-design walk
    cannot take on its own: on the reference estate every ``cOnException``
    chain ends at a ``cDirect`` PRODUCER, and what handles the error is
    whatever consumes that channel -- in the same context.
    """

    producers: _Producers = {}
    consumers: _Consumers = {}
    by_unit = source.interfaces.get("by_unit")
    if not isinstance(by_unit, dict):
        return producers, consumers
    for unit_id in sorted(by_unit):
        for item in by_unit[unit_id] or []:
            if not isinstance(item, dict) or item.get("kind") != "channel":
                continue
            channel_id, state, _ = _channel_identity(source, str(unit_id), item)
            if state is FactState.NOT_OBSERVED:
                continue
            itype = str(item.get("type") or "")
            component = str(item.get("component_name") or "")
            direction = str(item.get("direction") or "")
            if direction == "outbound":
                producers.setdefault((str(unit_id), component), []).append(
                    (channel_id, state, itype)
                )
            elif direction == "inbound":
                consumers.setdefault(channel_id, []).append((str(unit_id), component))
    return producers, {key: sorted(set(value)) for key, value in consumers.items()}


def _embedded_units(source: IndexSource) -> dict[str, set[str]]:
    """``unit -> joblets/routelets it embeds`` (they share its CamelContext)."""

    embedded: dict[str, set[str]] = {}
    for edge in source.dependencies.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        if "joblet" not in str(edge.get("relation_type") or ""):
            continue
        src, dst = str(edge.get("source") or ""), str(edge.get("target") or "")
        if src and dst:
            embedded.setdefault(src, set()).add(dst)
    return embedded


# ---------------------------------------------------------------------------
# Design-level joins shared by several indexes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ErrorPath:
    """One static error path inside a design, and where it leads."""

    unit_id: str
    origin: str  # component that raises or catches
    origin_type: str
    connector: str  # error connector name, or "" for an exception handler
    target: str  # component the path first reaches
    handler_components: tuple[str, ...]  # every component reached along the path
    #: ``(via component, mechanism, unit, state)``; mechanism is ``"call"`` for
    #: a tRunJob/cTalendJob or the channel identity for a channel hop.
    handler_units: tuple[tuple[str, str, str, FactState], ...]
    active: bool


def _call_targets(
    design: Mapping[str, Any], units: Mapping[str, Any]
) -> dict[str, tuple[str, FactState]]:
    """``call component -> (target unit, state)`` for one design."""

    targets: dict[str, tuple[str, FactState]] = {}
    for ref in design.get("artifact_refs") or []:
        if not isinstance(ref, dict):
            continue
        component = str(ref.get("component_name") or "")
        target = str(ref.get("resolved_artifact_id") or ref.get("target_name") or "")
        if component and target:
            targets[component] = (
                target,
                state_from_resolution(ref.get("resolution"), resolved=target in units),
            )
    # ``trunjob_refs`` carry no component name; a design with one tRunJob and
    # one ref is unambiguous, anything else is left to the dependency table.
    trunjob_refs = [ref for ref in design.get("trunjob_refs") or [] if isinstance(ref, dict)]
    run_components = [
        str(item.get("unique_name") or "")
        for item in _components(design)
        if str(item.get("component_type") or "") == "tRunJob"
    ]
    if len(trunjob_refs) == 1 and len(run_components) == 1:
        ref = trunjob_refs[0]
        target = str(ref.get("resolved_job_id") or ref.get("child_job") or "")
        if target:
            state = state_from_resolution(ref.get("resolution"), resolved=target in units)
            if ref.get("dynamic_job"):
                state = FactState.DYNAMIC
            targets[run_components[0]] = (target, state)
    return targets


def _error_paths(source: IndexSource) -> list[_ErrorPath]:
    """Every static error path in every staged design.

    Two shapes exist in the parsed data. A DI job routes errors along a
    connector (``SUBJOB_ERROR``, ``COMPONENT_ERROR``, ``REJECT``, ``FAULT``);
    the path starts at the connection's target. A Camel route declares a
    handler component (``cOnException``) with no incoming connection; the path
    starts at the handler's own outgoing connections. Both are followed along
    ACTIVE connections, and across a channel hop INSIDE the design (a producer
    to its own unit's consumer of the same channel), to whatever they reach.
    A call component reached resolves to the unit it invokes. A channel
    producer reached resolves to another unit only where the channel actually
    crosses contexts: a broker or JVM-wide channel type to any consumer, a
    context-local one only to a routelet or joblet this unit embeds. That is
    how "which job handles this route's exceptions" becomes a row instead of
    a reading, without joining thirty routes to each other on a shared name.
    """

    producers, consumers = _channel_graph(source)
    embedded = _embedded_units(source)
    paths: list[_ErrorPath] = []
    for unit_id in sorted(source.designs):
        design = source.designs[unit_id]
        components = {str(c.get("unique_name") or ""): c for c in _components(design)}
        adjacency: dict[str, list[str]] = {}
        for connection in _connections(design):
            if not _is_active(connection):
                continue
            src = str(connection.get("source") or "")
            dst = str(connection.get("target") or "")
            if src and dst:
                adjacency.setdefault(src, []).append(dst)
        # Intra-unit channel hops: a producer continues at its own unit's
        # consumer of the same channel identity.
        for name in sorted(components):
            for channel_id, _state, _itype in producers.get((unit_id, name), []):
                for consumer_unit, consumer_component in consumers.get(channel_id, []):
                    if consumer_unit == unit_id and consumer_component != name:
                        adjacency.setdefault(name, []).append(consumer_component)
        calls = _call_targets(design, source.units)

        def walk(
            start: str,
        ) -> tuple[tuple[str, ...], tuple[tuple[str, str, str, FactState], ...]]:
            seen: list[str] = []
            queue: deque[str] = deque([start])
            visited = {start}
            while queue:
                node = queue.popleft()
                seen.append(node)
                for nxt in sorted(adjacency.get(node, [])):
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)
            handlers: list[tuple[str, str, str, FactState]] = []
            for component in seen:
                if component in calls:
                    target, state = calls[component]
                    handlers.append((component, "call", target, state))
                for channel_id, channel_state, itype in producers.get((unit_id, component), []):
                    for consumer_unit, _consumer_component in consumers.get(channel_id, []):
                        if consumer_unit == unit_id:
                            continue
                        crosses = (
                            itype in CROSS_CONTEXT_CHANNEL_TYPES
                            or consumer_unit in embedded.get(unit_id, set())
                        )
                        if crosses:
                            handlers.append(
                                (
                                    component,
                                    channel_id,
                                    consumer_unit,
                                    weakest([FactState.EXACT, channel_state]),
                                )
                            )
            return tuple(seen), tuple(sorted(set(handlers), key=lambda h: h[:3]))

        for connection in _connections(design):
            connector = str(connection.get("connector_name") or "")
            if connector not in ERROR_CONNECTORS:
                continue
            origin = str(connection.get("source") or "")
            target = str(connection.get("target") or "")
            if not origin or not target:
                continue
            reached, handlers = walk(target) if _is_active(connection) else ((target,), ())
            paths.append(
                _ErrorPath(
                    unit_id=unit_id,
                    origin=origin,
                    origin_type=str((components.get(origin) or {}).get("component_type") or ""),
                    connector=connector,
                    target=target,
                    handler_components=reached,
                    handler_units=handlers,
                    active=_is_active(connection),
                )
            )
        for name in sorted(components):
            component = components[name]
            ctype = str(component.get("component_type") or "")
            if ctype not in ERROR_COMPONENTS or ctype in MONITOR_COMPONENTS:
                continue
            active = _is_active(component)
            first = sorted(adjacency.get(name, []))
            reached, handlers = walk(name) if active else ((name,), ())
            paths.append(
                _ErrorPath(
                    unit_id=unit_id,
                    origin=name,
                    origin_type=ctype,
                    connector="",
                    target=first[0] if first else "",
                    handler_components=reached,
                    handler_units=handlers,
                    active=active,
                )
            )
    return paths


def _interface_binding(
    source: IndexSource, unit_id: str, item: Mapping[str, Any]
) -> tuple[str, str, str, FactState, dict[str, Any]] | None:
    """``(relation, interface_id, interface_kind, state, detail)`` for one binding.

    Returns ``None`` for wiring (route bridges, in-memory component rows,
    schedulers, timer endpoints): each of those is represented elsewhere and
    a second row for it would be the duplication this index avoids.
    """

    kind = str(item.get("kind") or "")
    itype = str(item.get("type") or "")
    direction = str(item.get("direction") or "unknown")
    component = str(item.get("component_name") or "")
    params = _component_params(source, unit_id, component)
    detail: dict[str, Any] = {"component_name": component, "direction": direction}

    if kind == "channel":
        interface_id, state, channel_detail = _channel_identity(source, unit_id, item)
        relation = {"outbound": "publishes-to", "inbound": "consumes"}.get(
            direction, "exchanges-with"
        )
        return relation, interface_id, "channel", state, {**detail, **channel_detail}

    if kind == "endpoint":
        if itype == "timer":
            return None
        address, address_state = _address(params)
        interface_id = (
            f"endpoint:{itype}:{address}"
            if address is not None and address_state is not FactState.NOT_OBSERVED
            else f"endpoint:{itype}:{unit_id}/{component}"
        )
        detail["endpoint_type"] = itype
        if address is not None:
            detail["address"] = address
            detail["address_state"] = address_state.value
        state = address_state if address is not None else FactState.EXACT
        if state is FactState.NOT_OBSERVED:
            state = FactState.EXACT  # the binding exists; only the address is unknown
        if itype in SERVICE_ENDPOINT_TYPES:
            if direction in {"inbound", "both"}:
                return "implements-interface", interface_id, "endpoint", state, detail
            return (
                _client_relation(params, direction, detail),
                interface_id,
                "endpoint",
                state,
                detail,
            )
        return _directional(direction), interface_id, "endpoint", state, detail

    if kind == "resource":
        name = str(item.get("resource") or "") or f"{unit_id}/{component}"
        detail["resource_type"] = itype
        return (
            _directional(direction),
            f"resource:{itype}:{name}",
            "resource",
            FactState.EXACT,
            detail,
        )

    if kind == "component":
        family = str(item.get("family") or "")
        if family in WIRING_FAMILIES:
            return None
        detail["family"] = family
        detail["component_type"] = itype
        detail["active"] = _is_active(item)
        address, address_state = _address(params)
        if address is not None:
            detail["address"] = address
            detail["address_state"] = address_state.value
        state = FactState.EXACT
        if address is not None and address_state in {FactState.DYNAMIC, FactState.REDACTED}:
            state = address_state
        if family == "database":
            connection = str(item.get("repository_connection") or "")
            target = f"database:{connection}" if connection else f"database:{unit_id}/{component}"
            query = _param_text(params, "QUERY") or _param_text(params, "SQLQUERY")
            if query is not None and query.strip() not in {"", '""'}:
                detail["query_state"] = state_of_value(query).value
                return "queries", target, "database", state, detail
            return _directional(direction), target, "database", state, detail
        if family in MESSAGING_FAMILIES:
            channel_type = (_param_text(params, "TYPE") or family).strip().casefold()
            name = address if address is not None else f"{unit_id}/{component}"
            relation = {"outbound": "publishes-to", "inbound": "consumes"}.get(
                direction, "exchanges-with"
            )
            return relation, f"channel:{channel_type}:{name}", "channel", state, detail
        interface_id = (
            f"{family}:{address}"
            if address is not None and address_state is not FactState.NOT_OBSERVED
            else f"{family}:{unit_id}/{component}"
        )
        if family in API_FAMILIES:
            if direction in {"inbound", "both"}:
                return "implements-interface", interface_id, "endpoint", state, detail
            if direction == "outbound":
                return (
                    _client_relation(params, direction, detail),
                    interface_id,
                    "endpoint",
                    state,
                    detail,
                )
            return "exchanges-with", interface_id, "endpoint", state, detail
        return _directional(direction), interface_id, family or "interface", state, detail
    return None


def _directional(direction: str) -> str:
    return {"inbound": "reads-from", "outbound": "writes-to"}.get(direction, "exchanges-with")


def _client_relation(
    params: Mapping[str, Mapping[str, Any]], direction: str, detail: dict[str, Any]
) -> str:
    """Read or write, from the HTTP method when the parser kept one."""

    method = (_param_text(params, "METHOD") or _param_text(params, "HTTP_METHOD") or "").strip()
    method = _unquote(method).upper()
    if method:
        detail["method"] = method
    if method in READ_METHODS:
        return "reads-from"
    if method in WRITE_METHODS:
        return "writes-to"
    return _directional(direction) if direction == "outbound" and not method else "exchanges-with"


def _address(params: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, FactState]:
    """The first address-shaped parameter with a value, and its state.

    The value is copied when present: every byte here already passed the
    scanner's redaction verification and sits in the design file the agent
    reads. A redaction marker is kept AS the address so the row says
    ``redacted`` rather than falling back to a component-local id.
    """

    for name in ADDRESS_PARAMS:
        text = _param_text(params, name)
        if text is None:
            continue
        state = state_of_value(text)
        if state is FactState.NOT_OBSERVED:
            continue
        return (_unquote(text) if state is FactState.EXACT else text.strip()), state
    return None, FactState.NOT_OBSERVED


def _component_params(
    source: IndexSource, unit_id: str, component_name: str
) -> dict[str, dict[str, Any]]:
    design = source.designs.get(unit_id)
    if not design or not component_name:
        return {}
    for component in _components(design):
        if str(component.get("unique_name") or "") == component_name:
            return _params(component)
    return {}


# ---------------------------------------------------------------------------
# candidates (8.1)
# ---------------------------------------------------------------------------


def _candidate(
    source: IndexSource,
    *,
    unit_id: str,
    kind: str,
    component: str,
    component_type: str,
    detail: Mapping[str, Any],
    state: FactState,
    evidence: str,
    evidence_ref: str,
) -> dict[str, Any]:
    unit = source.units.get(unit_id) or {}
    return {
        "index": "candidates",
        "candidate_id": f"{unit_id or '-'}::{kind}::{component or '-'}",
        "candidate_kind": kind,
        "unit_id": unit_id,
        "display_name": str(unit.get("display_name") or ""),
        "unit_kind": str(unit.get("kind") or ""),
        "group": str(unit.get("group") or ""),
        "source_root": str(unit.get("source_root") or ""),
        "component_name": component,
        "component_type": component_type,
        "detail": dict(sorted(detail.items())),
        "state": state.value,
        "evidence_ref": evidence_ref,
        "evidence": evidence,
    }


def _candidate_rows(source: IndexSource) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_unit = source.interfaces.get("by_unit")
    by_unit = by_unit if isinstance(by_unit, dict) else {}
    edges = [edge for edge in source.dependencies.get("edges") or [] if isinstance(edge, dict)]

    for unit_id in sorted(source.units):
        unit = source.units[unit_id]
        evidence = _unit_evidence(source, unit_id)
        kind = str(unit.get("kind") or "")
        if unit.get("entry_point") and kind in {"job", "route"}:
            rows.append(
                _candidate(
                    source,
                    unit_id=unit_id,
                    kind="entry-job",
                    component="",
                    component_type="",
                    detail={
                        "trigger_interfaces": bool(unit.get("trigger_interfaces")),
                        "inbound_interfaces": bool(unit.get("inbound_interfaces")),
                        "callers": sorted(str(c) for c in unit.get("callers") or []),
                    },
                    state=FactState.EXACT,
                    evidence=evidence,
                    evidence_ref=f"unit:{unit_id}",
                )
            )

        for item in by_unit.get(unit_id) or []:
            if not isinstance(item, dict):
                continue
            candidate = _interface_candidate(source, unit_id, item)
            if candidate is not None:
                rows.append(candidate)

        design = source.designs.get(unit_id)
        if design is not None:
            for component in _components(design):
                ctype = str(component.get("component_type") or "")
                if ctype not in SCHEDULER_COMPONENTS:
                    continue
                params = _params(component)
                period = _param_text(params, "PERIOD")
                cron = _param_text(params, "CRON") or _param_text(params, "CRON_EXPRESSION")
                schedule = period if period is not None else cron
                schedule_state = (
                    state_of_value(schedule) if schedule is not None else FactState.NOT_OBSERVED
                )
                detail: dict[str, Any] = {
                    "active": _is_active(component),
                    "schedule_state": schedule_state.value,
                }
                if period is not None:
                    detail["period"] = _unquote(period)
                    detail["period_unit"] = "milliseconds" if ctype == "cTimer" else ""
                if cron is not None:
                    detail["cron"] = _unquote(cron)
                for name in ("DELAY", "REPEATCOUNT", "FIXEDRATE"):
                    text = _param_text(params, name)
                    if text is not None and text.strip() not in {"", '""'}:
                        detail[name.lower()] = _unquote(text)
                rows.append(
                    _candidate(
                        source,
                        unit_id=unit_id,
                        kind="scheduled-job",
                        component=str(component.get("unique_name") or ""),
                        component_type=ctype,
                        detail=detail,
                        state=schedule_state if schedule is not None else FactState.PARTIAL,
                        evidence=evidence,
                        evidence_ref=f"component:{unit_id}/{component.get('unique_name') or ''}",
                    )
                )
        else:
            # No staged design: the timer endpoint in the fact table is the
            # only evidence, and it carries no period.
            for item in by_unit.get(unit_id) or []:
                if (
                    isinstance(item, dict)
                    and item.get("kind") == "endpoint"
                    and item.get("type") == "timer"
                ):
                    rows.append(
                        _candidate(
                            source,
                            unit_id=unit_id,
                            kind="scheduled-job",
                            component=str(item.get("component_name") or ""),
                            component_type=str(item.get("provenance") or "timer"),
                            detail={"schedule_state": FactState.PARTIAL.value},
                            state=FactState.PARTIAL,
                            evidence=f"{FACTS.as_posix()}/interfaces.json",
                            evidence_ref=f"endpoint:timer:{unit_id}/{item.get('component_name') or ''}",
                        )
                    )

    for edge in edges:
        src, dst = str(edge.get("source") or ""), str(edge.get("target") or "")
        src_root = str((source.units.get(src) or {}).get("source_root") or "")
        dst_root = str((source.units.get(dst) or {}).get("source_root") or "")
        if not src or not dst or src_root == dst_root:
            continue
        rows.append(
            _candidate(
                source,
                unit_id=src,
                kind="cross-project-call",
                component=str(edge.get("component_name") or ""),
                component_type=str(edge.get("relation_type") or ""),
                detail={
                    "target": dst,
                    "target_source_root": dst_root,
                    "resolution": str(edge.get("resolution") or ""),
                },
                state=state_from_resolution(edge.get("resolution"), resolved=True),
                evidence=f"{FACTS.as_posix()}/dependencies.json",
                evidence_ref=_edge_ref(edge),
            )
        )

    for service in source.services:
        name = str(service.get("name") or "")
        if not name:
            continue
        operations = sorted(
            str(op.get("operation") or "")
            for op in service.get("operations") or []
            if isinstance(op, dict) and op.get("operation")
        )
        outcome = str(service.get("parse_outcome") or "")
        rows.append(
            _candidate(
                source,
                unit_id="",
                kind="api-service",
                component=name,
                component_type="service-definition",
                detail={
                    "operations": operations,
                    "parse_outcome": outcome,
                    "source_root": str(service.get("source_root") or ""),
                },
                state=FactState.PARTIAL if outcome == "partial" else FactState.EXACT,
                evidence=f"{SCAN.as_posix()}/services.json",
                evidence_ref=f"service:{name}",
            )
        )

    rows += _runtime_candidates(source)
    return sorted(
        rows, key=lambda row: _row_sort_key(row, "unit_id", "candidate_kind", "component_name")
    )


def _interface_candidate(
    source: IndexSource, unit_id: str, item: Mapping[str, Any]
) -> dict[str, Any] | None:
    kind = str(item.get("kind") or "")
    itype = str(item.get("type") or "")
    direction = str(item.get("direction") or "unknown")
    component = str(item.get("component_name") or "")
    evidence = (
        _unit_evidence(source, unit_id)
        if source.designs.get(unit_id)
        else f"{FACTS.as_posix()}/interfaces.json"
    )
    ref = f"interface:{unit_id}/{component or kind}:{itype}"

    def emit(
        candidate_kind: str, detail: dict[str, Any], state: FactState, ctype: str
    ) -> dict[str, Any]:
        detail = {**detail, "direction": direction, "interface_kind": kind, "interface_type": itype}
        return _candidate(
            source,
            unit_id=unit_id,
            kind=candidate_kind,
            component=component,
            component_type=ctype,
            detail=detail,
            state=state,
            evidence=evidence,
            evidence_ref=ref,
        )

    if kind == "channel":
        if itype in IN_MEMORY_CHANNEL_TYPES:
            return None  # wiring between units; the relationships index pairs it
        channel_id, state, channel_detail = _channel_identity(source, unit_id, item)
        candidate_kind = {"inbound": "event-consumer", "outbound": "event-publisher"}.get(
            direction, "event-participant"
        )
        return emit(
            candidate_kind,
            {**channel_detail, "channel_id": channel_id},
            state,
            str(item.get("provenance") or ""),
        )

    if kind == "endpoint":
        if itype == "timer":
            return None  # the scheduled-job candidate carries the period
        params = _component_params(source, unit_id, component)
        address, address_state = _address(params)
        detail: dict[str, Any] = {}
        if address is not None:
            detail["address"] = address
            detail["address_state"] = address_state.value
        method = _unquote(
            _param_text(params, "METHOD") or _param_text(params, "HTTP_METHOD") or ""
        ).upper()
        if method:
            detail["method"] = method
        state = address_state if address is not None else FactState.EXACT
        if itype in SERVICE_ENDPOINT_TYPES:
            candidate_kind = "api-client" if direction == "outbound" else "api-service"
        else:
            candidate_kind = {"inbound": "inbound-interface", "outbound": "outbound-interface"}.get(
                direction, "undirected-interface"
            )
        return emit(candidate_kind, detail, state, str(item.get("provenance") or ""))

    if kind == "resource":
        candidate_kind = {"inbound": "inbound-interface", "outbound": "outbound-interface"}.get(
            direction, "undirected-interface"
        )
        return emit(
            candidate_kind,
            {"resource": str(item.get("resource") or "")},
            FactState.EXACT,
            str(item.get("provenance") or ""),
        )

    if kind == "component":
        family = str(item.get("family") or "")
        if family in WIRING_FAMILIES:
            return None
        params = _component_params(source, unit_id, component)
        address, address_state = _address(params)
        detail = {"family": family, "active": _is_active(item)}
        if address is not None:
            detail["address"] = address
            detail["address_state"] = address_state.value
        state = FactState.EXACT
        if address is not None and address_state in {FactState.DYNAMIC, FactState.REDACTED}:
            state = address_state
        if family == "database":
            query = _param_text(params, "QUERY") or _param_text(params, "SQLQUERY")
            detail["has_query"] = bool(query and query.strip() not in {"", '""'})
            detail["repository_connection"] = str(item.get("repository_connection") or "")
            return emit("database-operation", detail, state, itype)
        if family in MESSAGING_FAMILIES:
            candidate_kind = {"inbound": "event-consumer", "outbound": "event-publisher"}.get(
                direction, "event-participant"
            )
            return emit(candidate_kind, detail, state, itype)
        if family in API_FAMILIES:
            method = _unquote(
                _param_text(params, "METHOD") or _param_text(params, "HTTP_METHOD") or ""
            ).upper()
            if method:
                detail["method"] = method
            if direction == "outbound":
                return emit("api-client", detail, state, itype)
            if direction in {"inbound", "both"}:
                return emit("api-service", detail, state, itype)
            return emit("undirected-interface", detail, state, itype)
        candidate_kind = {"inbound": "inbound-interface", "outbound": "outbound-interface"}.get(
            direction, "undirected-interface"
        )
        return emit(candidate_kind, detail, state, itype)
    return None


def _runtime_candidates(source: IndexSource) -> list[dict[str, Any]]:
    runtime = source.runtime
    if not isinstance(runtime, dict) or str(runtime.get("mode") or "") != "data":
        return []
    rows: list[dict[str, Any]] = []
    by_unit = runtime.get("by_unit")
    by_unit = by_unit if isinstance(by_unit, dict) else {}
    evidence = f"{RUNTIME.as_posix()}/evidence.json"
    for unit_id in sorted(by_unit):
        item = by_unit[unit_id]
        if not isinstance(item, dict):
            continue
        total = int(item.get("total_runs") or 0)
        if total <= 0:
            continue
        rows.append(
            _candidate(
                source,
                unit_id=str(unit_id),
                kind="runtime-observed-workload",
                component="",
                component_type="",
                detail={
                    "scanned": str(unit_id) in source.units,
                    "runtime_job_name": str(item.get("job_name") or ""),
                    "total_runs": total,
                    "failed_runs": int(item.get("failed_runs") or 0),
                    "last_run_at": item.get("last_run_at"),
                    "environments": sorted(str(env) for env in item.get("environments") or []),
                },
                state=FactState.EXACT,
                evidence=evidence,
                evidence_ref=f"runtime:{item.get('source_key') or ''}:{item.get('job_name') or unit_id}",
            )
        )
    for job in sorted(str(job) for job in runtime.get("orphan_runtime_jobs") or [] if job):
        rows.append(
            _candidate(
                source,
                unit_id=job,
                kind="runtime-observed-workload",
                component="",
                component_type="",
                detail={"scanned": False, "runtime_job_name": job},
                state=FactState.UNRESOLVED,
                evidence=evidence,
                evidence_ref=f"runtime:{runtime.get('source_key') or ''}:{job}",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# relationships (8.2)
# ---------------------------------------------------------------------------


def _edge_ref(edge: Mapping[str, Any]) -> str:
    return (
        f"dependency:{edge.get('source') or ''}->{edge.get('target') or edge.get('declared_target') or ''}"
        f"#{edge.get('component_name') or edge.get('relation_type') or ''}"
    )


def _relationship(
    *,
    source_id: str,
    source_kind: str,
    relation: str,
    target_id: str,
    target_kind: str,
    evidence_ref: str,
    evidence: str,
    state: FactState,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if relation not in RELATION_INVERSE:
        raise _fail(f"relation {relation!r} has no registered inverse; add it to RELATION_INVERSE")
    return {
        "index": "relationships",
        "source_id": source_id,
        "source_kind": source_kind,
        "relation": relation,
        "target_id": target_id,
        "target_kind": target_kind,
        "direction": "forward",
        "state": state.value,
        "detail": dict(sorted((detail or {}).items())),
        "evidence_ref": evidence_ref,
        "evidence": evidence,
    }


def _reverse(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "source_id": row["target_id"],
        "source_kind": row["target_kind"],
        "relation": RELATION_INVERSE[str(row["relation"])],
        "target_id": row["source_id"],
        "target_kind": row["source_kind"],
        "direction": "reverse",
    }


def _relationship_rows(source: IndexSource, error_paths: list[_ErrorPath]) -> list[dict[str, Any]]:
    forward: list[dict[str, Any]] = []
    dependencies_evidence = f"{FACTS.as_posix()}/dependencies.json"

    # -- calls / uses-joblet -------------------------------------------------
    for bucket, resolved in (("edges", True), ("unresolved", False)):
        for edge in source.dependencies.get(bucket) or []:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("source") or "")
            dst = str(edge.get("target") or "") or str(edge.get("declared_target") or "")
            if not src or not dst:
                continue
            relation_type = str(edge.get("relation_type") or "")
            relation = "uses-joblet" if "joblet" in relation_type else "calls"
            state = state_from_resolution(edge.get("resolution"), resolved=resolved)
            if edge.get("dynamic"):
                state = FactState.DYNAMIC
            forward.append(
                _relationship(
                    source_id=src,
                    source_kind="unit",
                    relation=relation,
                    target_id=dst,
                    target_kind="unit" if resolved else "unresolved-unit",
                    evidence_ref=_edge_ref(edge),
                    evidence=dependencies_evidence,
                    state=state,
                    detail={
                        "relation_type": relation_type,
                        "component_name": str(edge.get("component_name") or ""),
                        "context_name": str(edge.get("context_name") or ""),
                        "resolution": str(edge.get("resolution") or ""),
                    },
                )
            )

    # -- invokes-routine / used-by ------------------------------------------
    for bucket, resolved in (("routine_links", True), ("unresolved_routine_refs", False)):
        links = source.dependencies.get(bucket)
        if not isinstance(links, dict):
            continue
        for unit_id in sorted(links):
            for link in links[unit_id] or []:
                if isinstance(link, dict):
                    name = str(
                        link.get("name") or link.get("routine") or link.get("routine_name") or ""
                    )
                    resolution = link.get("resolution")
                else:
                    name, resolution = str(link), ("resolved" if resolved else "unresolved")
                if not name:
                    continue
                forward.append(
                    _relationship(
                        source_id=str(unit_id),
                        source_kind="unit",
                        relation="invokes-routine",
                        target_id=f"routine:{name}",
                        target_kind="routine" if resolved else "unresolved-routine",
                        evidence_ref=f"routine:{unit_id}->{name}",
                        evidence=dependencies_evidence,
                        state=state_from_resolution(resolution, resolved=resolved),
                        detail={
                            "routine_evidence": f"{SCAN.as_posix()}/routines.json"
                            if resolved
                            else "",
                            "resolution": str(resolution or ""),
                        },
                    )
                )

    # -- interfaces ------------------------------------------------------------
    by_unit = source.interfaces.get("by_unit")
    if isinstance(by_unit, dict):
        interfaces_evidence = f"{FACTS.as_posix()}/interfaces.json"
        for unit_id in sorted(by_unit):
            for item in by_unit[unit_id] or []:
                if not isinstance(item, dict):
                    continue
                binding = _interface_binding(source, str(unit_id), item)
                if binding is None:
                    continue
                relation, interface_id, interface_kind, state, detail = binding
                forward.append(
                    _relationship(
                        source_id=str(unit_id),
                        source_kind="unit",
                        relation=relation,
                        target_id=interface_id,
                        target_kind=interface_kind,
                        evidence_ref=f"interface:{unit_id}/{item.get('component_name') or item.get('kind')}:{item.get('type') or ''}",
                        evidence=interfaces_evidence,
                        state=state,
                        detail=detail,
                    )
                )

    # -- maps-with -------------------------------------------------------------
    for table in sorted(source.mapping_tables, key=lambda t: str(t.get("table_id") or "")):
        table_id = str(table.get("table_id") or "")
        unit_id = str(table.get("unit_id") or "")
        if not table_id or not unit_id:
            continue
        staged = source.mapping_paths.get(table_id, "")
        forward.append(
            _relationship(
                source_id=unit_id,
                source_kind="unit",
                relation="maps-with",
                target_id=f"mapping:{table_id}",
                target_kind="mapping-table",
                evidence_ref=f"mapping:{table_id}",
                evidence=staged or f"{SCAN.as_posix()}/mapping_accounting.json",
                state=FactState.EXACT if staged else FactState.PARTIAL,
                detail={
                    "component_name": str(table.get("component_name") or ""),
                    "mapper_kind": str(table.get("mapper_kind") or ""),
                },
            )
        )
    for sheet in source.stylesheets:
        name = str(sheet.get("name") or "")
        for unit_id in sorted(str(u) for u in sheet.get("referenced_by") or [] if u):
            if unit_id in source.units:
                forward.append(
                    _relationship(
                        source_id=unit_id,
                        source_kind="unit",
                        relation="maps-with",
                        target_id=f"stylesheet:{name}",
                        target_kind="stylesheet",
                        evidence_ref=f"stylesheet:{unit_id}->{name}",
                        evidence=f"{SCAN.as_posix()}/xslt.json",
                        state=FactState.EXACT,
                    )
                )
    for tdm in source.tdm_maps:
        name = str(tdm.get("name") or "")
        owner = str(tdm.get("owning_artifact") or "")
        if name and owner in source.units:
            forward.append(
                _relationship(
                    source_id=owner,
                    source_kind="unit",
                    relation="maps-with",
                    target_id=f"tdm:{name}",
                    target_kind="tdm-map",
                    evidence_ref=f"tdm:{owner}->{name}",
                    evidence=f"{SCAN.as_posix()}/tdm_maps.json",
                    state=FactState.EXACT,
                    detail={"association": str(tdm.get("association") or "")},
                )
            )

    # -- configured-by ---------------------------------------------------------
    configuration_evidence = f"{FACTS.as_posix()}/configuration.json"
    seen_context: set[tuple[str, str]] = set()
    for group in source.configuration.get("context_groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or group.get("name") or "")
        for unit_id in sorted(str(u) for u in group.get("used_by") or [] if u):
            seen_context.add((unit_id, key))
            forward.append(
                _relationship(
                    source_id=unit_id,
                    source_kind="unit",
                    relation="configured-by",
                    target_id=f"context:{key}",
                    target_kind="context-group",
                    evidence_ref=f"context:{unit_id}->{key}",
                    evidence=configuration_evidence,
                    state=FactState.EXACT,
                    detail={"group_name": str(group.get("name") or "")},
                )
            )
    links = source.configuration.get("job_context_links")
    if isinstance(links, dict):
        for unit_id in sorted(links):
            for link in links[unit_id] or []:
                if not isinstance(link, dict):
                    continue
                key = str(link.get("context_key") or "")
                if not key or (str(unit_id), key) in seen_context:
                    continue
                seen_context.add((str(unit_id), key))
                forward.append(
                    _relationship(
                        source_id=str(unit_id),
                        source_kind="unit",
                        relation="configured-by",
                        target_id=f"context:{key}",
                        target_kind="context-group",
                        evidence_ref=f"context:{unit_id}->{key}",
                        evidence=configuration_evidence,
                        state=state_from_resolution(link.get("resolution"), resolved=True),
                        detail={"resolution": str(link.get("resolution") or "")},
                    )
                )
    unresolved_refs = source.configuration.get("unresolved_context_refs")
    if isinstance(unresolved_refs, dict):
        for unit_id in sorted(unresolved_refs):
            for ref in unresolved_refs[unit_id] or []:
                if not isinstance(ref, dict):
                    continue
                context_id = str(ref.get("context_id") or ref.get("context_key") or "")
                if not context_id:
                    continue
                forward.append(
                    _relationship(
                        source_id=str(unit_id),
                        source_kind="unit",
                        relation="configured-by",
                        target_id=f"context:{context_id}",
                        target_kind="unresolved-context",
                        evidence_ref=f"context:{unit_id}->{context_id}",
                        evidence=configuration_evidence,
                        state=FactState.UNRESOLVED,
                        detail={"resolution": str(ref.get("resolution") or "unresolved")},
                    )
                )
    for connection in source.configuration.get("repository_connections") or []:
        if not isinstance(connection, dict):
            continue
        name = str(connection.get("name") or "")
        for unit_id in sorted(str(u) for u in connection.get("used_by") or [] if u):
            forward.append(
                _relationship(
                    source_id=unit_id,
                    source_kind="unit",
                    relation="configured-by",
                    target_id=f"connection:{name}",
                    target_kind="repository-connection",
                    evidence_ref=f"connection:{unit_id}->{name}",
                    evidence=configuration_evidence,
                    state=FactState.EXACT,
                    detail={"connection_type": str(connection.get("connection_type") or "")},
                )
            )

    # -- routes-errors-to ------------------------------------------------------
    for path in error_paths:
        evidence = _unit_evidence(source, path.unit_id)
        origin_id = f"component:{path.unit_id}/{path.origin}"
        if path.connector:
            forward.append(
                _relationship(
                    source_id=origin_id,
                    source_kind="component",
                    relation="routes-errors-to",
                    target_id=f"component:{path.unit_id}/{path.target}",
                    target_kind="component",
                    evidence_ref=f"error:{path.unit_id}/{path.origin}-{path.connector}->{path.target}",
                    evidence=evidence,
                    state=FactState.EXACT,
                    detail={
                        "connector": path.connector,
                        "active": path.active,
                        "origin_type": path.origin_type,
                        "unit_id": path.unit_id,
                    },
                )
            )
        else:
            forward.append(
                _relationship(
                    source_id=path.unit_id,
                    source_kind="unit",
                    relation="routes-errors-to",
                    target_id=origin_id,
                    target_kind="component",
                    evidence_ref=f"error:{path.unit_id}/{path.origin}",
                    evidence=evidence,
                    state=FactState.EXACT,
                    detail={
                        "handler_type": path.origin_type,
                        "active": path.active,
                        "first_target": path.target,
                    },
                )
            )
        for via_component, mechanism, handler_unit, state in path.handler_units:
            forward.append(
                _relationship(
                    source_id=path.unit_id,
                    source_kind="unit",
                    relation="routes-errors-to",
                    target_id=handler_unit,
                    target_kind="unit" if handler_unit in source.units else "unresolved-unit",
                    evidence_ref=(
                        f"error:{path.unit_id}/{path.origin}->{via_component}"
                        f"[{mechanism}]->{handler_unit}"
                    ),
                    evidence=evidence,
                    state=weakest([FactState.EXACT, state]),
                    detail={
                        "via": path.origin,
                        "via_type": path.origin_type,
                        "connector": path.connector,
                        "via_component": via_component,
                        "mechanism": mechanism,
                    },
                )
            )

    # -- monitored-by ----------------------------------------------------------
    for unit_id in sorted(source.designs):
        for component in _components(source.designs[unit_id]):
            ctype = str(component.get("component_type") or "")
            if ctype not in MONITOR_COMPONENTS:
                continue
            name = str(component.get("unique_name") or "")
            params = _params(component)
            catches = sorted(
                key.removeprefix("CATCH_").casefold()
                for key, param in params.items()
                if key.startswith("CATCH_") and str(param.get("value")).casefold() == "true"
            )
            forward.append(
                _relationship(
                    source_id=unit_id,
                    source_kind="unit",
                    relation="monitored-by",
                    target_id=f"component:{unit_id}/{name}",
                    target_kind="component",
                    evidence_ref=f"monitor:{unit_id}/{name}",
                    evidence=_unit_evidence(source, unit_id),
                    state=FactState.EXACT,
                    detail={
                        "component_type": ctype,
                        "catches": catches,
                        "active": _is_active(component),
                    },
                )
            )

    # -- runtime-observed-as ---------------------------------------------------
    runtime = source.runtime
    if isinstance(runtime, dict):
        by_unit_rt = runtime.get("by_unit")
        if isinstance(by_unit_rt, dict):
            for unit_id in sorted(by_unit_rt):
                item = by_unit_rt[unit_id]
                if not isinstance(item, dict):
                    continue
                job = str(item.get("job_name") or unit_id)
                forward.append(
                    _relationship(
                        source_id=str(unit_id),
                        source_kind="unit" if str(unit_id) in source.units else "unresolved-unit",
                        relation="runtime-observed-as",
                        target_id=f"runtime:{item.get('source_key') or ''}:{job}",
                        target_kind="runtime-job",
                        evidence_ref=f"runtime:{item.get('source_key') or ''}:{job}",
                        evidence=f"{RUNTIME.as_posix()}/evidence.json",
                        state=FactState.EXACT,
                        detail={
                            "alias_kind": str(item.get("alias_kind") or ""),
                            "matched_alias": str(item.get("matched_alias") or ""),
                            "environments": sorted(str(e) for e in item.get("environments") or []),
                        },
                    )
                )

    rows = forward + [_reverse(row) for row in forward]
    return sorted(
        rows,
        key=lambda row: _row_sort_key(row, "source_id", "relation", "target_id", "evidence_ref"),
    )


# ---------------------------------------------------------------------------
# fields (8.3)
# ---------------------------------------------------------------------------

LINEAGE_KINDS: tuple[str, ...] = (
    "direct",
    "rename",
    "constant",
    "default",
    "lookup",
    "calculation",
    "conditional",
    "filter",
    "dropped",
    "dynamic",
    "unresolved",
)


def _lineage_kind(row: Mapping[str, Any], table: Mapping[str, Any]) -> tuple[str, FactState]:
    """Classify one mapper output row. Deterministic, from the scanner's fields.

    The scanner says ``direct_map`` / ``constant`` / ``expression`` /
    ``dynamic`` / ``unresolved``; the PRD asks for eleven kinds. The extra
    distinctions are all decidable from the row itself: a direct map whose
    single source field has a different name is a ``rename``; a source field
    on one of the mapper's declared lookup tables is a ``lookup``; an
    expression carrying a ternary is ``conditional``; an empty expression on a
    written field is a ``default`` (the field takes its type default -- the
    schema default itself is not joined here, hence ``partial``).
    """

    classification = str(row.get("classification") or "")
    expression = str(row.get("expression") or "").strip()
    refs = [str(ref) for ref in row.get("source_refs") or [] if ref]
    dynamic_refs = [str(ref) for ref in row.get("dynamic_refs") or [] if ref]
    lookup_names = {
        str(lookup.get("name") or "")
        for lookup in table.get("lookup_tables") or []
        if isinstance(lookup, dict)
    }
    output_field = str(row.get("output_field") or "")

    if classification == "unresolved":
        return "unresolved", FactState.UNRESOLVED
    if classification == "dynamic" or dynamic_refs:
        return "dynamic", FactState.DYNAMIC
    if not expression or expression in {'""', "''"}:
        return "default", FactState.PARTIAL
    if refs and any(ref.split(".", 1)[0] in lookup_names for ref in refs):
        return "lookup", FactState.EXACT
    if classification == "direct_map":
        if len(refs) == 1 and refs[0].rsplit(".", 1)[-1] == output_field:
            return "direct", FactState.EXACT
        return "rename", FactState.EXACT
    if classification == "constant":
        return "constant", FactState.EXACT
    if _TERNARY_RE.search(expression):
        return "conditional", FactState.EXACT
    return "calculation", FactState.EXACT


def _field_row(
    *,
    unit_id: str,
    component_name: str,
    mapper_kind: str,
    table_id: str,
    mapping_table: str,
    output_table: str,
    output_field: str,
    output_role: str,
    lineage_kind: str,
    source_fields: Iterable[str],
    dynamic_refs: Iterable[str],
    expression: str,
    source_component: str,
    target_component: str,
    lineage_confidence: str,
    note: str,
    state: FactState,
    evidence: str,
) -> dict[str, Any]:
    if lineage_kind not in LINEAGE_KINDS:
        raise _fail(f"lineage kind {lineage_kind!r} is not in the closed set")
    return {
        "index": "fields",
        "unit_id": unit_id,
        "component_name": component_name,
        "mapper_kind": mapper_kind,
        "table_id": table_id,
        "mapping_table": mapping_table,
        "output_table": output_table,
        "output_field": output_field,
        "output_role": output_role,
        "lineage_kind": lineage_kind,
        "source_fields": sorted(set(str(item) for item in source_fields)),
        "dynamic_refs": sorted(set(str(item) for item in dynamic_refs)),
        "expression": expression,
        "source_component": source_component,
        "target_component": target_component,
        "lineage_confidence": lineage_confidence,
        "note": note,
        "state": state.value,
        "evidence": evidence,
    }


def _field_rows(source: IndexSource) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    accounting_evidence = f"{SCAN.as_posix()}/mapping_accounting.json"
    lineage_evidence = f"{SCAN.as_posix()}/lineage.json"

    lineage_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for entry in source.lineage_entries:
        key = (
            str(entry.get("job_name") or ""),
            str(entry.get("target_table") or ""),
            str(entry.get("target_column") or ""),
        )
        lineage_by_key.setdefault(key, []).append(entry)
    matched_lineage: set[int] = set()

    for table in sorted(source.mapping_tables, key=lambda t: str(t.get("table_id") or "")):
        table_id = str(table.get("table_id") or "")
        unit_id = str(table.get("unit_id") or "")
        component = str(table.get("component_name") or "")
        mapper_kind = str(table.get("mapper_kind") or "")
        mapping_table = source.mapping_paths.get(table_id, "")
        evidence = mapping_table or accounting_evidence
        rejects = {str(item) for item in table.get("reject_outputs") or []}
        owner_note = (
            "" if unit_id else "the scanner did not bind this mapper to an implementing unit"
        )

        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            lineage_kind, state = _lineage_kind(row, table)
            output_table = str(row.get("output_table") or "")
            output_field = str(row.get("output_field") or "")
            source_component = target_component = confidence = ""
            candidates = lineage_by_key.get((unit_id, output_table, output_field), [])
            if len(candidates) == 1:
                entry = candidates[0]
                matched_lineage.add(id(entry))
                source_component = str(entry.get("source_component") or "")
                target_component = str(entry.get("target_component") or "")
                confidence = str(entry.get("confidence") or "")
            if not unit_id:
                state = weakest([state, FactState.PARTIAL])
            rows.append(
                _field_row(
                    unit_id=unit_id,
                    component_name=component,
                    mapper_kind=mapper_kind,
                    table_id=table_id,
                    mapping_table=mapping_table,
                    output_table=output_table,
                    output_field=output_field,
                    output_role="reject" if output_table in rejects else "output",
                    lineage_kind=lineage_kind,
                    source_fields=[str(r) for r in row.get("source_refs") or []],
                    dynamic_refs=[str(r) for r in row.get("dynamic_refs") or []],
                    expression=str(row.get("expression") or ""),
                    source_component=source_component,
                    target_component=target_component,
                    lineage_confidence=confidence,
                    note=owner_note,
                    state=state,
                    evidence=evidence,
                )
            )

        for dropped in sorted(str(item) for item in table.get("unwritten_offered") or [] if item):
            rows.append(
                _field_row(
                    unit_id=unit_id,
                    component_name=component,
                    mapper_kind=mapper_kind,
                    table_id=table_id,
                    mapping_table=mapping_table,
                    output_table="",
                    output_field="",
                    output_role="none",
                    lineage_kind="dropped",
                    source_fields=[dropped],
                    dynamic_refs=[],
                    expression="",
                    source_component="",
                    target_component="",
                    lineage_confidence="",
                    note="offered to the mapper and written to no output",
                    state=FactState.EXACT if unit_id else FactState.PARTIAL,
                    evidence=evidence,
                )
            )
        for expression in sorted(str(item) for item in table.get("filters") or [] if item):
            rows.append(
                _field_row(
                    unit_id=unit_id,
                    component_name=component,
                    mapper_kind=mapper_kind,
                    table_id=table_id,
                    mapping_table=mapping_table,
                    output_table="",
                    output_field="",
                    output_role="filter",
                    lineage_kind="filter",
                    source_fields=[],
                    dynamic_refs=[
                        f"context.{name}"
                        for name in sorted(set(_CONTEXT_REF_RE.findall(expression)))
                    ],
                    expression=expression,
                    source_component="",
                    target_component="",
                    lineage_confidence="",
                    note="mapper-level filter expression",
                    state=state_of_value(expression),
                    evidence=evidence,
                )
            )
        for lookup in table.get("lookup_tables") or []:
            if not isinstance(lookup, dict):
                continue
            filter_expression = str(lookup.get("expression_filter") or "").strip()
            if filter_expression:
                rows.append(
                    _field_row(
                        unit_id=unit_id,
                        component_name=component,
                        mapper_kind=mapper_kind,
                        table_id=table_id,
                        mapping_table=mapping_table,
                        output_table=str(lookup.get("name") or ""),
                        output_field="",
                        output_role="lookup-filter",
                        lineage_kind="filter",
                        source_fields=[],
                        dynamic_refs=[],
                        expression=filter_expression,
                        source_component="",
                        target_component="",
                        lineage_confidence="",
                        note="lookup table filter expression",
                        state=state_of_value(filter_expression),
                        evidence=evidence,
                    )
                )
        for reason in sorted(str(item) for item in table.get("unenumerable_inputs") or [] if item):
            rows.append(
                _field_row(
                    unit_id=unit_id,
                    component_name=component,
                    mapper_kind=mapper_kind,
                    table_id=table_id,
                    mapping_table=mapping_table,
                    output_table="",
                    output_field="",
                    output_role="input",
                    lineage_kind="unresolved",
                    source_fields=[],
                    dynamic_refs=[],
                    expression="",
                    source_component="",
                    target_component="",
                    lineage_confidence="",
                    note=f"input schema not enumerable: {reason}",
                    state=FactState.PARTIAL,
                    evidence=evidence,
                )
            )

    # Lineage entries no accounting row claimed: cross-job or component-level
    # lineage the mapper tables do not carry. Emitted from the lineage file so
    # the field is not silently absent.
    for entry in source.lineage_entries:
        if id(entry) in matched_lineage:
            continue
        transformations = [t for t in entry.get("transformations") or [] if isinstance(t, dict)]
        kinds = sorted({str(t.get("transformation_type") or "") for t in transformations})
        expression = " | ".join(str(t.get("expression") or "") for t in transformations)
        row_like = {
            "classification": kinds[0] if len(kinds) == 1 else ("expression" if kinds else ""),
            "expression": expression,
            "source_refs": [
                f"{entry.get('source_table') or ''}.{entry.get('source_column') or ''}".strip(".")
            ]
            if entry.get("source_column")
            else [],
            "dynamic_refs": [str(r) for r in entry.get("dynamic_refs") or []],
            "output_field": str(entry.get("target_column") or ""),
        }
        lineage_kind, state = _lineage_kind(row_like, {})
        if entry.get("resolved") is False and lineage_kind not in {
            "dynamic",
            "unresolved",
            "constant",
        }:
            state = weakest([state, FactState.PARTIAL])
        rows.append(
            _field_row(
                unit_id=str(entry.get("job_name") or ""),
                component_name=str(entry.get("target_component") or ""),
                mapper_kind="lineage",
                table_id="",
                mapping_table="",
                output_table=str(entry.get("target_table") or ""),
                output_field=str(entry.get("target_column") or ""),
                output_role="output",
                lineage_kind=lineage_kind,
                source_fields=row_like["source_refs"],
                dynamic_refs=row_like["dynamic_refs"],
                expression=expression,
                source_component=str(entry.get("source_component") or ""),
                target_component=str(entry.get("target_component") or ""),
                lineage_confidence=str(entry.get("confidence") or ""),
                note="lineage entry without a mapper table row",
                state=state,
                evidence=lineage_evidence,
            )
        )
    for unmapped in source.lineage_unmapped:
        rows.append(
            _field_row(
                unit_id=str(unmapped.get("job_name") or ""),
                component_name=str(unmapped.get("table") or ""),
                mapper_kind=str(unmapped.get("mapping_type") or "").casefold(),
                table_id="",
                mapping_table="",
                output_table="",
                output_field=str(unmapped.get("column") or ""),
                output_role="output",
                lineage_kind="unresolved",
                source_fields=[],
                dynamic_refs=[],
                expression="",
                source_component="",
                target_component="",
                lineage_confidence="",
                note=str(unmapped.get("reason") or "lineage not modelled by the scanner"),
                state=FactState.UNSUPPORTED,
                evidence=lineage_evidence,
            )
        )

    return sorted(
        rows,
        key=lambda row: _row_sort_key(
            row, "unit_id", "component_name", "output_table", "output_field", "lineage_kind"
        ),
    )


# ---------------------------------------------------------------------------
# configuration (8.4)
# ---------------------------------------------------------------------------


def _param_role(component_type: str, param_name: str) -> str:
    if component_type in FILTER_COMPONENTS:
        return "filter"
    if component_type in ROUTING_COMPONENTS:
        return "routing"
    if component_type in SCHEDULER_COMPONENTS or param_name in SCHEDULE_PARAMS:
        return "schedule"
    if param_name in CHANNEL_PARAMS:
        return "channel"
    if param_name in QUERY_PARAMS:
        return "query"
    if param_name in ADDRESS_PARAMS:
        return "endpoint"
    if component_type in ERROR_COMPONENTS:
        return "error-handling"
    if component_type in CALL_COMPONENTS or param_name.startswith("CONTEXTPARAM"):
        return "invocation"
    if param_name in TRANSFORMATION_PARAMS or component_type in TRANSFORMATION_COMPONENTS:
        return "transformation"
    return "other"


def _context_consumers(source: IndexSource) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``(unit_id, variable) -> [consumer]`` from every staged design and mapper."""

    consumers: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def add(unit_id: str, variable: str, consumer: dict[str, Any]) -> None:
        bucket = consumers.setdefault((unit_id, variable), [])
        if consumer not in bucket:
            bucket.append(consumer)

    def scan_text(unit_id: str, text: Any, consumer: dict[str, Any]) -> None:
        if not isinstance(text, str) or "context." not in text:
            return
        for variable in sorted(set(_CONTEXT_REF_RE.findall(text))):
            add(unit_id, variable, consumer)

    for unit_id in sorted(source.designs):
        design = source.designs[unit_id]
        for component in _components(design):
            name = str(component.get("unique_name") or "")
            ctype = str(component.get("component_type") or "")
            for param_name, param in sorted(_params(component).items()):
                if param_name in NOISE_PARAMS:
                    continue
                consumer = {
                    "unit_id": unit_id,
                    "component_name": name,
                    "component_type": ctype,
                    "param": param_name,
                    "role": _param_role(ctype, param_name),
                }
                scan_text(unit_id, param.get("value"), consumer)
                for row in param.get("rows") or []:
                    if isinstance(row, dict):
                        for cell in row.values():
                            scan_text(unit_id, cell, consumer)
        for bucket in ("trunjob_refs", "artifact_refs"):
            for ref in design.get(bucket) or []:
                if not isinstance(ref, dict):
                    continue
                consumer = {
                    "unit_id": unit_id,
                    "component_name": str(ref.get("component_name") or ""),
                    "component_type": str(ref.get("relation_type") or bucket),
                    "param": "context_params",
                    "role": "invocation",
                }
                for param in ref.get("context_params") or []:
                    if isinstance(param, dict):
                        scan_text(unit_id, param.get("value"), consumer)
    for table in source.mapping_tables:
        unit_id = str(table.get("unit_id") or "")
        if not unit_id:
            continue
        consumer = {
            "unit_id": unit_id,
            "component_name": str(table.get("component_name") or ""),
            "component_type": str(table.get("mapper_kind") or ""),
            "param": "expression",
            "role": "transformation",
        }
        for row in table.get("rows") or []:
            if isinstance(row, dict):
                for ref in row.get("dynamic_refs") or []:
                    scan_text(unit_id, str(ref), consumer)
                scan_text(unit_id, row.get("expression"), consumer)
    return consumers


def _value_states(variable: Mapping[str, Any]) -> dict[str, str]:
    values = variable.get("values")
    values = values if isinstance(values, dict) else {}
    redacted = {str(env) for env in variable.get("redacted_environments") or []}
    descriptors = variable.get("redaction_descriptors")
    if isinstance(descriptors, dict):
        redacted |= {str(env) for env in descriptors}
    return {
        str(env): state_of_value(values[env], redacted=str(env) in redacted).value
        for env in sorted(values)
    }


def _environment_differences(variable: Mapping[str, Any], states: Mapping[str, str]) -> str:
    """Whether a variable differs by environment -- WITHOUT comparing redacted text.

    Two ``[URL_REDACTED]`` markers compare equal while the URLs behind them
    may not, so a redacted variable is ``unknown-redacted`` unless the
    redaction DESCRIPTORS (scheme, tls, auth) themselves differ, which is a
    difference the scanner has already made safe to state.
    """

    values = variable.get("values")
    values = values if isinstance(values, dict) else {}
    if not values:
        return "not-observed"
    if len(values) == 1:
        return "single-environment"
    redacted_envs = [env for env, state in states.items() if state == FactState.REDACTED.value]
    if redacted_envs:
        descriptors = variable.get("redaction_descriptors")
        descriptors = descriptors if isinstance(descriptors, dict) else {}
        shapes = {json.dumps(descriptors.get(env), sort_keys=True) for env in redacted_envs}
        if len(shapes) > 1:
            return "differs"
        clear = {str(values[env]) for env in values if env not in redacted_envs}
        if len(clear) > 1:
            return "differs"
        return "unknown-redacted"
    return "differs" if len({str(value) for value in values.values()}) > 1 else "same"


def _summary_state(states: Mapping[str, str]) -> FactState:
    """One state for a whole variable; ``value_states`` stays the per-env truth."""

    parsed = [FactState(state) for state in states.values()]
    if not parsed or all(state is FactState.NOT_OBSERVED for state in parsed):
        return FactState.NOT_OBSERVED
    if any(state is FactState.REDACTED for state in parsed):
        return FactState.REDACTED
    if any(state is FactState.DYNAMIC for state in parsed):
        return FactState.DYNAMIC
    if any(state is FactState.NOT_OBSERVED for state in parsed):
        return FactState.PARTIAL
    return FactState.EXACT


def _descriptor_summary(variable: Mapping[str, Any]) -> dict[str, Any]:
    """Safe redaction metadata the scanner emitted on purpose, per environment."""

    descriptors = variable.get("redaction_descriptors")
    if not isinstance(descriptors, dict):
        return {}
    out: dict[str, Any] = {}
    for env in sorted(descriptors):
        descriptor = descriptors[env]
        if isinstance(descriptor, dict):
            out[str(env)] = {
                str(key): descriptor[key]
                for key in sorted(descriptor)
                if key in {"scheme", "category", "auth_present", "auth_type", "tls", "host_present"}
            }
    return out


def _configuration_rows(source: IndexSource) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    evidence = f"{FACTS.as_posix()}/configuration.json"
    consumers = _context_consumers(source)
    defined: set[tuple[str, str]] = set()  # (unit_id, variable) with a linked definition

    def variable_row(
        *,
        scope: str,
        group_key: str,
        group_name: str,
        source_root: str,
        variable: Mapping[str, Any],
        linked_units: list[str],
    ) -> dict[str, Any]:
        name = str(variable.get("name") or "")
        states = _value_states(variable)
        unit_consumers: list[dict[str, Any]] = []
        for unit_id in linked_units:
            defined.add((unit_id, name))
            unit_consumers += consumers.get((unit_id, name), [])
        unit_consumers = sorted(
            unit_consumers, key=lambda c: json.dumps(c, sort_keys=True, ensure_ascii=False)
        )
        return {
            "index": "configuration",
            "kind": "context-variable",
            "scope": scope,
            "group_key": group_key,
            "group_name": group_name,
            "source_root": source_root,
            "variable": name,
            "type": str(variable.get("type") or ""),
            "environments": sorted(states),
            "value_states": states,
            "environment_differences": _environment_differences(variable, states),
            "redaction_descriptor": _descriptor_summary(variable),
            "linked_units": sorted(linked_units),
            "consumers": unit_consumers,
            "consumer_roles": sorted({str(c["role"]) for c in unit_consumers}),
            "state": _summary_state(states).value,
            "evidence": evidence,
        }

    group_links: dict[str, set[str]] = {}
    links = source.configuration.get("job_context_links")
    if isinstance(links, dict):
        for unit_id in sorted(links):
            for link in links[unit_id] or []:
                if isinstance(link, dict) and link.get("context_key"):
                    group_links.setdefault(str(link["context_key"]), set()).add(str(unit_id))

    for group in source.configuration.get("context_groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or group.get("name") or "")
        linked = {str(u) for u in group.get("used_by") or [] if u} | group_links.get(key, set())
        linked_units = sorted(u for u in linked if u in source.units)
        for variable in group.get("variables") or []:
            if isinstance(variable, dict) and variable.get("name"):
                rows.append(
                    variable_row(
                        scope="repository",
                        group_key=key,
                        group_name=str(group.get("name") or ""),
                        source_root=str(group.get("source_root") or ""),
                        variable=variable,
                        linked_units=linked_units,
                    )
                )

    inline = source.configuration.get("inline_contexts")
    if isinstance(inline, dict):
        for unit_id in sorted(inline):
            for group in inline[unit_id] or []:
                if not isinstance(group, dict):
                    continue
                for variable in group.get("variables") or []:
                    if isinstance(variable, dict) and variable.get("name"):
                        rows.append(
                            variable_row(
                                scope="inline",
                                group_key=f"inline:{unit_id}",
                                group_name=str(group.get("name") or "inline"),
                                source_root=str(
                                    (source.units.get(str(unit_id)) or {}).get("source_root") or ""
                                ),
                                variable=variable,
                                linked_units=[str(unit_id)],
                            )
                        )

    # References with no linked definition: the value's origin is not in the
    # scan. Stated as unresolved rather than left to read as "not configured".
    for unit_id, variable in sorted(consumers):
        if (unit_id, variable) in defined:
            continue
        unit_consumers = sorted(
            consumers[(unit_id, variable)],
            key=lambda c: json.dumps(c, sort_keys=True, ensure_ascii=False),
        )
        rows.append(
            {
                "index": "configuration",
                "kind": "context-reference",
                "scope": "unresolved",
                "group_key": "",
                "group_name": "",
                "source_root": str((source.units.get(unit_id) or {}).get("source_root") or ""),
                "variable": variable,
                "type": "",
                "environments": [],
                "value_states": {},
                "environment_differences": "not-observed",
                "redaction_descriptor": {},
                "linked_units": [unit_id],
                "consumers": unit_consumers,
                "consumer_roles": sorted({str(c["role"]) for c in unit_consumers}),
                "state": FactState.UNRESOLVED.value,
                "note": "referenced by the design; no context group linked to this unit defines it",
                "evidence": _unit_evidence(source, unit_id),
            }
        )

    unresolved_refs = source.configuration.get("unresolved_context_refs")
    if isinstance(unresolved_refs, dict):
        for unit_id in sorted(unresolved_refs):
            for ref in unresolved_refs[unit_id] or []:
                if not isinstance(ref, dict):
                    continue
                rows.append(
                    {
                        "index": "configuration",
                        "kind": "context-link",
                        "scope": "unresolved",
                        "group_key": str(ref.get("context_id") or ref.get("context_key") or ""),
                        "group_name": "",
                        "source_root": str(
                            (source.units.get(str(unit_id)) or {}).get("source_root") or ""
                        ),
                        "variable": "",
                        "type": "",
                        "environments": [],
                        "value_states": {},
                        "environment_differences": "not-observed",
                        "redaction_descriptor": {},
                        "linked_units": [str(unit_id)],
                        "consumers": [],
                        "consumer_roles": [],
                        "state": FactState.UNRESOLVED.value,
                        "note": "the unit links a context group the scan does not contain",
                        "evidence": evidence,
                    }
                )

    scanned_connections = {
        str(c.get("name") or ""): c for c in source.connections if isinstance(c, dict)
    }
    for connection in source.configuration.get("repository_connections") or []:
        if not isinstance(connection, dict):
            continue
        name = str(connection.get("name") or "")
        scanned = scanned_connections.get(name, {})
        host_redaction = scanned.get("host_redaction")
        host_redacted = isinstance(host_redaction, dict) and any(
            value not in (None, False) for value in host_redaction.values()
        )
        field_states: dict[str, str] = {}
        referenced: set[str] = set()
        additional = scanned.get("additional_params")
        additional = additional if isinstance(additional, dict) else {}
        for field_name in ("host", "port", "database", "schema_name", "file_path"):
            if field_name in scanned:
                value = scanned.get(field_name)
                field_states[field_name] = state_of_value(
                    value, redacted=(field_name == "host" and host_redacted)
                ).value
                if isinstance(value, str):
                    referenced |= set(_CONTEXT_REF_RE.findall(value))
        for field_name in ("username", "driver_class", "product_id", "context_mode"):
            if field_name in additional:
                value = additional.get(field_name)
                field_states[field_name] = state_of_value(value).value
                if isinstance(value, str):
                    referenced |= set(_CONTEXT_REF_RE.findall(value))
        states = list(field_states.values())
        rows.append(
            {
                "index": "configuration",
                "kind": "repository-connection",
                "scope": "repository",
                "group_key": name,
                "group_name": name,
                "source_root": str(connection.get("source_root") or ""),
                "variable": "",
                "type": str(connection.get("connection_type") or ""),
                "environments": [],
                "value_states": field_states,
                "environment_differences": "not-observed",
                "redaction_descriptor": {},
                "linked_units": sorted(str(u) for u in connection.get("used_by") or [] if u),
                "consumers": [],
                "consumer_roles": [],
                "context_variables_referenced": sorted(referenced),
                "state": (
                    _summary_state(dict(zip(field_states, states))) if states else FactState.EXACT
                ).value,
                "evidence": evidence,
            }
        )

    return sorted(
        rows, key=lambda row: _row_sort_key(row, "kind", "scope", "group_key", "variable")
    )


# ---------------------------------------------------------------------------
# runtime (8.5)
# ---------------------------------------------------------------------------


def _runtime_row(
    kind: str, unit_id: str, state: FactState, evidence: str, **fields: Any
) -> dict[str, Any]:
    return {
        "index": "runtime",
        "kind": kind,
        "unit_id": unit_id,
        **{key: fields[key] for key in sorted(fields)},
        "state": state.value,
        "evidence": evidence,
    }


def _runtime_rows(
    source: IndexSource,
    error_paths: list[_ErrorPath],
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    runtime = source.runtime
    evidence = f"{RUNTIME.as_posix()}/evidence.json"

    # -- mirror status: written in every case, saying which case --------------
    if not isinstance(runtime, dict):
        rows.append(
            _runtime_row(
                "mirror-status",
                "",
                FactState.NOT_OBSERVED,
                f"{RUNTIME.as_posix()}/",
                mode="not-staged",
                note="runtime/evidence.json is not in this workspace; no runtime fact below is from a mirror",
            )
        )
        mode = "not-staged"
    else:
        mode = str(runtime.get("mode") or "")
        by_unit = runtime.get("by_unit") if isinstance(runtime.get("by_unit"), dict) else {}
        unmatched = sorted(str(u) for u in runtime.get("unmatched_units") or [])
        unretrieved = sorted(str(u) for u in runtime.get("unretrieved_units") or [])
        orphans = sorted(str(u) for u in runtime.get("orphan_runtime_jobs") or [] if u)
        status_state = {
            "data": FactState.EXACT,
            "opted_out": FactState.NOT_OBSERVED,
            "unavailable": FactState.UNRESOLVED,
        }.get(mode, FactState.UNRESOLVED)
        rows.append(
            _runtime_row(
                "mirror-status",
                "",
                status_state,
                evidence,
                mode=mode,
                window_days=runtime.get("window_days"),
                requested_units=int(runtime.get("requested_units") or 0),
                matched_units=len(by_unit),
                unmatched_units=len(unmatched),
                unretrieved_units=len(unretrieved),
                orphan_runtime_jobs=len(orphans),
                partial=bool(runtime.get("partial")),
                scope_mismatch=bool(runtime.get("scope_mismatch")),
                read_environments=sorted(str(e) for e in runtime.get("read_environments") or []),
                shared_read=bool(runtime.get("shared_read")),
                source_key=str(runtime.get("source_key") or ""),
                note={
                    "data": "the mirror answered; per-unit rows below are observations",
                    "opted_out": "runtime evidence was switched off for this run; nothing was looked up",
                    "unavailable": "the mirror could not be reached; absence below is not non-execution",
                }.get(mode, "unrecognised mirror mode"),
            )
        )

        rows += _runtime_binding_rows(
            source, runtime, by_unit, unmatched, unretrieved, orphans, mode
        )

        for unit_id in sorted(by_unit):
            item = by_unit[unit_id]
            if not isinstance(item, dict):
                continue
            rows += _execution_rows(str(unit_id), item, evidence)

        interface_ids: dict[str, list[str]] = {}
        for row in relationships:
            if row.get("direction") == "forward" and row.get("target_kind") in {
                "channel",
                "endpoint",
                "resource",
                "database",
                "file",
                "mail",
            }:
                interface_ids.setdefault(str(row["source_id"]), []).append(str(row["target_id"]))
        for unit_id in sorted(by_unit):
            item = by_unit[unit_id]
            if not isinstance(item, dict):
                continue
            for interface_id in sorted(set(interface_ids.get(str(unit_id), []))):
                rows.append(
                    _runtime_row(
                        "interface-observation",
                        str(unit_id),
                        FactState.PARTIAL,
                        evidence,
                        interface_id=interface_id,
                        unit_total_runs=int(item.get("total_runs") or 0),
                        unit_failed_runs=int(item.get("failed_runs") or 0),
                        unit_last_run_at=item.get("last_run_at"),
                        note=(
                            "volume is the implementing unit's execution count; the mirror "
                            "records no per-interface message volume"
                        ),
                    )
                )

    # -- static joins: no mirror needed -----------------------------------------
    by_unit_paths: dict[str, list[_ErrorPath]] = {}
    for path in error_paths:
        by_unit_paths.setdefault(path.unit_id, []).append(path)
    for unit_id in sorted(source.units):
        unit = source.units[unit_id]
        constructs = sorted(str(c) for c in unit.get("error_handling_constructs") or [])
        paths = by_unit_paths.get(unit_id, [])
        if not constructs and not paths:
            continue
        handler_units = sorted(
            {handler for path in paths for _, _, handler, _ in path.handler_units}
        )
        handler_components = sorted(
            {component for path in paths for component in path.handler_components}
        )
        states = [FactState.EXACT] + [
            state for path in paths for _, _, _, state in path.handler_units
        ]
        rows.append(
            _runtime_row(
                "error-handling",
                unit_id,
                weakest(states),
                _unit_evidence(source, unit_id),
                constructs=constructs,
                error_paths=len(paths),
                inactive_paths=sum(1 for path in paths if not path.active),
                handler_components=handler_components,
                handler_units=handler_units,
                design_staged=unit_id in source.designs,
            )
        )

    headers: dict[str, list[dict[str, str]]] = {}
    for unit_id in sorted(source.designs):
        for component in _components(source.designs[unit_id]):
            ctype = str(component.get("component_type") or "")
            name = str(component.get("unique_name") or "")
            params = _params(component)
            if ctype in MONITOR_COMPONENTS or ctype in LOGGING_COMPONENTS:
                identifier = _unquote(_param_text(params, "LOGGING_CATEGORY") or "")
                level = _param_text(params, "LEVEL_TYPE") or ""
                catches = sorted(
                    key.removeprefix("CATCH_").casefold()
                    for key, param in params.items()
                    if key.startswith("CATCH_") and str(param.get("value")).casefold() == "true"
                )
                rows.append(
                    _runtime_row(
                        "monitoring-construct",
                        unit_id,
                        FactState.EXACT,
                        _unit_evidence(source, unit_id),
                        component_name=name,
                        component_type=ctype,
                        identifier=identifier,
                        level=level,
                        catches=catches,
                        active=_is_active(component),
                    )
                )
            if ctype in HEADER_COMPONENTS:
                values = params.get("VALUES") or {}
                for row in values.get("rows") or []:
                    if not isinstance(row, dict):
                        continue
                    header = _unquote(str(row.get("NAME") or ""))
                    if header:
                        headers.setdefault(header, []).append(
                            {"unit_id": unit_id, "component_name": name}
                        )
    for header in sorted(headers):
        setters = sorted(headers[header], key=lambda s: (s["unit_id"], s["component_name"]))
        rows.append(
            _runtime_row(
                "message-header",
                "",
                FactState.EXACT,
                f"{SCAN.as_posix()}/jobs/",
                header=header,
                set_by=setters,
                setting_units=sorted({s["unit_id"] for s in setters}),
                note="header NAME only; the expression that sets it is in the design file",
            )
        )

    return sorted(rows, key=lambda row: _row_sort_key(row, "kind", "unit_id"))


def _runtime_binding_rows(
    source: IndexSource,
    runtime: Mapping[str, Any],
    by_unit: Mapping[str, Any],
    unmatched: list[str],
    unretrieved: list[str],
    orphans: list[str],
    mode: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    evidence = f"{RUNTIME.as_posix()}/evidence.json"
    source_key = str(runtime.get("source_key") or "")
    for unit_id in sorted(by_unit):
        item = by_unit[unit_id]
        if not isinstance(item, dict):
            continue
        rows.append(
            _runtime_row(
                "unit-binding",
                str(unit_id),
                FactState.EXACT,
                evidence,
                runtime_job_name=str(item.get("job_name") or ""),
                alias_kind=str(item.get("alias_kind") or ""),
                matched_alias=str(item.get("matched_alias") or ""),
                resolved_by=str(item.get("resolved_by") or ""),
                source_key=str(item.get("source_key") or source_key),
                scanned=str(unit_id) in source.units,
            )
        )
    for unit_id in unmatched:
        rows.append(
            _runtime_row(
                "unit-binding",
                unit_id,
                FactState.NOT_OBSERVED,
                evidence,
                runtime_job_name="",
                alias_kind="",
                matched_alias="",
                resolved_by="",
                source_key=source_key,
                scanned=unit_id in source.units,
                note=(
                    "looked up under both identifiers; no runtime record"
                    if mode == "data"
                    else f"not looked up (mirror mode {mode})"
                ),
            )
        )
    for unit_id in unretrieved:
        rows.append(
            _runtime_row(
                "unit-binding",
                unit_id,
                FactState.UNRESOLVED,
                evidence,
                runtime_job_name="",
                alias_kind="",
                matched_alias="",
                resolved_by="",
                source_key=source_key,
                scanned=unit_id in source.units,
                note="the lookup batch for this unit did not complete; unknown is not absent",
            )
        )
    for job in orphans:
        rows.append(
            _runtime_row(
                "runtime-only-unit",
                job,
                FactState.UNRESOLVED,
                evidence,
                runtime_job_name=job,
                source_key=source_key,
                scanned=False,
                note="a runtime job with no scanned counterpart under either identifier",
            )
        )
    return rows


def _execution_rows(unit_id: str, item: Mapping[str, Any], evidence: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cadence = _cadence(item)
    rows.append(
        _runtime_row(
            "execution-profile",
            unit_id,
            FactState.EXACT,
            evidence,
            runtime_job_name=str(item.get("job_name") or ""),
            environments=sorted(str(e) for e in item.get("environments") or []),
            total_runs=int(item.get("total_runs") or 0),
            success_runs=int(item.get("success_runs") or 0),
            failed_runs=int(item.get("failed_runs") or 0),
            unfinished_runs=int(item.get("unfinished_runs") or 0),
            failure_rate=item.get("failure_rate"),
            current_failure_streak=int(item.get("current_failure_streak") or 0),
            first_run_at=item.get("first_run_at"),
            last_run_at=item.get("last_run_at"),
            runs_per_day=item.get("runs_per_day"),
            p50_duration_ms=item.get("p50_duration_ms"),
            p95_duration_ms=item.get("p95_duration_ms"),
            max_duration_ms=item.get("max_duration_ms"),
            gap_p50_seconds=item.get("gap_p50_seconds"),
            gap_sample_size=item.get("gap_sample_size"),
            cadence=cadence,
            plans=sorted(str(p) for p in item.get("plans") or []),
        )
    )
    for failure in item.get("recent_failures") or []:
        if not isinstance(failure, dict):
            continue
        rows.append(
            _runtime_row(
                "failure-mode",
                unit_id,
                FactState.EXACT,
                evidence,
                failed_at=str(failure.get("failed_at") or ""),
                error_summary=str(failure.get("error_summary") or ""),
                occurrences=int(failure.get("occurrences") or 1),
            )
        )
    for insight in item.get("incident_insights") or []:
        if not isinstance(insight, dict):
            continue
        rows.append(
            _runtime_row(
                "incident-insight",
                unit_id,
                FactState.EXACT,
                evidence,
                pattern_id=str(insight.get("pattern_id") or ""),
                confidence=str(insight.get("confidence") or ""),
                occurrences=int(insight.get("occurrences") or 0),
                most_recent_at=str(insight.get("most_recent_at") or ""),
                failed_component=str(insight.get("failed_component") or ""),
                termination_component=str(insight.get("termination_component") or ""),
                error_code=str(insight.get("error_code") or ""),
                exception_class=str(insight.get("exception_class") or ""),
                cause_origin=str(insight.get("cause_origin") or ""),
            )
        )
    return rows


def _cadence(item: Mapping[str, Any]) -> dict[str, Any]:
    """The project's own cadence classifier, or a stated non-classification.

    Imported lazily and guarded: the classifier lives with the runtime lane
    and pulls its configuration; an index build must not fail because a
    runtime module could not import.
    """

    try:
        from mcp_server.providers.custom.runtime_evidence import RuntimeUnitEvidence

        return RuntimeUnitEvidence.from_payload(item).cadence().to_json()
    except Exception:  # pragma: no cover - defensive; the classifier is deterministic
        return {"label": "not classified", "basis": "classifier unavailable"}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _validate_evidence_paths(rows: Iterable[Mapping[str, Any]]) -> None:
    """Reject a row whose evidence escapes the safe staged roots."""

    for row in rows:
        for key in ("evidence", "mapping_table"):
            value = row.get(key)
            if not isinstance(value, str) or not value:
                continue
            if _safe_relative(value) is None:
                raise _fail(f"unsafe index evidence path: {value!r}")


def _write_shards(index_dir: Path, name: str, rows: list[dict[str, Any]]) -> list[str]:
    """``index/<name>.jsonl`` first, overflow as ``index/<name>.part_NNN.jsonl``.

    The PRD spells the flat name, so the flat name is always the first shard;
    the ``<name>*.jsonl`` glob and the manifest both reach every shard. Split
    by UTF-8 bytes, never by row count, and never inside a row.
    """

    shards: list[str] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        ordinal = len(shards) + 1
        filename = f"{name}.jsonl" if ordinal == 1 else f"{name}.part_{ordinal:03d}.jsonl"
        (index_dir / filename).write_text("".join(buffer), encoding="utf-8")
        shards.append(f"{INDEX_DIR.as_posix()}/{filename}")

    for row in rows:
        line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
        encoded = len(line.encode("utf-8"))
        if buffer and size + encoded > INDEX_SHARD_BYTES:
            flush()
            buffer, size = [], 0
        buffer.append(line)
        size += encoded
    if buffer:
        flush()
    return shards


def build_indexes(staging_dir: Path) -> dict[str, int]:
    """Write ``index/*.jsonl`` from the staged tree. Returns rows per index.

    Everything is computed before anything is written, so a failure leaves
    no half-built ``index/`` behind to be mistaken for a complete one; the
    previous ``index/`` is removed and rebuilt from scratch on every call.
    """

    staging_dir = Path(staging_dir)
    source = load_source(staging_dir)
    error_paths = _error_paths(source)

    rows_by_index: dict[str, list[dict[str, Any]]] = {}
    rows_by_index["candidates"] = _candidate_rows(source)
    rows_by_index["relationships"] = _relationship_rows(source, error_paths)
    rows_by_index["fields"] = _field_rows(source)
    rows_by_index["configuration"] = _configuration_rows(source)
    rows_by_index["runtime"] = _runtime_rows(source, error_paths, rows_by_index["relationships"])
    for name in INDEX_ORDER:
        _validate_evidence_paths(rows_by_index[name])

    index_dir = staging_dir / INDEX_DIR
    if index_dir.exists():
        shutil.rmtree(index_dir)
    index_dir.mkdir(parents=True)

    row_counts: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    for name in INDEX_ORDER:
        rows = rows_by_index[name]
        row_counts[name] = len(rows)
        describes, resolves_to = INDEX_DOC[name]
        entry: dict[str, Any] = {
            "name": name,
            "describes": describes,
            "resolves_to": resolves_to,
            "rows": len(rows),
            "present": bool(rows),
            "shards": _write_shards(index_dir, name, rows) if rows else [],
        }
        if not rows:
            entry["absent_because"] = (
                f"this estate produced no {name} rows; the file is not written, which is "
                "a fact about the estate rather than a gap in the index"
            )
        if name == "runtime":
            runtime = source.runtime
            entry["mirror_mode"] = (
                str(runtime.get("mode") or "") if isinstance(runtime, dict) else "not-staged"
            )
        entries.append(entry)

    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "shard_bytes": INDEX_SHARD_BYTES,
        "how_to_read": (
            "One complete JSON record per line. grep index/<name>*.jsonl for a unit id, "
            "a routine, a channel or a variable name; every record names the canonical "
            "file its facts came from in 'evidence'. Records route; the evidence file "
            "holds more than the record does. A candidate is a starting point, never a "
            "verdict that something is an integration."
        ),
        "states": dict(STATE_MEANINGS),
        "relations": {
            relation: {"inverse": inverse, "meaning": RELATION_MEANINGS.get(relation, "")}
            for relation, inverse in RELATION_INVERSE.items()
        },
        "reverse_rows": (
            "every forward relationship has a generated reverse row with the inverse "
            "relation, the same evidence_ref and direction 'reverse'; grep the inverse "
            "to answer 'what depends on this'"
        ),
        "lineage_kinds": list(LINEAGE_KINDS),
        "indexes": entries,
        "not_built": dict(NOT_BUILT),
        "inputs": {
            "read": sorted(set(source.read)),
            "absent": sorted(set(source.absent)),
            "designs_indexed": len(source.designs),
            "units": len(source.units),
        },
        "notices": list(source.notices),
    }
    (staging_dir / INDEX_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return row_counts
