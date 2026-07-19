"""Build the Level-3 tMap column/table graph from per-job scan cache data.

Pure transformation layer (no I/O), mirroring `job_graph_service.py`: the
projects route loads the per-job scan JSON already used by the job detail
and Level-2 job graph endpoints, resolves which component this is, and this
module turns its `tmap_data` entry into a `ComponentGraphResponse`.

Contract notes (see topology-lineage-runtime-architecture.md S2/S4):
- **tMap only.** No other component type has captured intra-component
  structure today. Callers must check `is_tmap_component` first and return
  the inspector-fallback 404 for every other type -- never an empty or
  fabricated graph.
- **Input table columns are not captured by the current scanner.** The
  Talend parser records input table *names* only, never their entries/
  columns (`platforms/talend/parser.py::_parse_mapper_data` -- the
  `inputTables` branch has no `mapperTableEntries` loop, unlike
  `outputTables`/`varTables`). Input tables in the response therefore always
  carry an empty `columns` list until the S4 lineage tokenizer back-fills
  them from expression references.
- **Input-table row names are resolved to their upstream component via the
  job's `connections` array** (`connection.target == this component` and
  `connection.name == the row name`) -- the same connections-based approach
  `job_graph_service._build_edges` uses, never the per-component
  `incoming_connections`/`outgoing_connections` fields (empty/unreliable on
  real scans; verified in `job_graph_service.py` module docstring).
- **`tmap_data` entries carry no component name.** They are appended in node
  parse order, so the Nth tMap-typed component owns the Nth `tmap_data`
  entry -- the same positional convention
  `job_graph_service._build_tmap_tables` uses.
- Mapping edges expose the raw `TMapEntry.expression` only. `resolved_refs`
  stays empty and `confidence` stays `"opaque"` until slice S4 lands the
  deterministic expression tokenizer -- getting the shape right here, not
  resolving references.
"""

from __future__ import annotations

from typing import Any

from mcp_server.schemas import (
    ComponentGraphEdge,
    ComponentGraphResponse,
    ComponentGraphTable,
)

_TMAP_TYPE = "tMap"
_VAR_TABLE_NAME = "Var"


def component_exists(job_data: dict[str, Any], unique_name: str) -> bool:
    """True if `unique_name` names any component in this job's scan data."""
    return any(
        str(comp.get("unique_name") or "") == unique_name
        for comp in job_data.get("components") or []
    )


def is_tmap_component(job_data: dict[str, Any], unique_name: str) -> bool:
    """True if `unique_name` names a tMap component in this job's scan data.

    Callers must check this (or handle its False case) before calling
    `build_component_graph` -- every non-tMap component type is a 404
    inspector-fallback, never a graph.
    """
    for comp in job_data.get("components") or []:
        if str(comp.get("unique_name") or "") == unique_name:
            return str(comp.get("component_type") or "") == _TMAP_TYPE
    return False


def _tmap_positional_index(job_data: dict[str, Any], unique_name: str) -> int | None:
    """Index of this tMap component among tMap-typed components in parse
    order. `tmap_data` entries carry no component name, so the Nth tMap
    component owns the Nth `tmap_data` entry (see module docstring)."""
    tmap_idx = 0
    for comp in job_data.get("components") or []:
        if str(comp.get("component_type") or "") != _TMAP_TYPE:
            continue
        if str(comp.get("unique_name") or "") == unique_name:
            return tmap_idx
        tmap_idx += 1
    return None


def _resolve_input_source(
    job_data: dict[str, Any], component_unique_name: str, row_name: str
) -> str | None:
    """Upstream component feeding this input row.

    Resolved via the job's `connections` array (never the per-component
    incoming/outgoing_connections fields -- see module docstring): the row
    name is the connection's `name`, and the connection's `target` is this
    tMap component.
    """
    for conn in job_data.get("connections") or []:
        if (
            str(conn.get("target") or "") == component_unique_name
            and str(conn.get("name") or "") == row_name
        ):
            source = str(conn.get("source") or "")
            return source or None
    return None


def _dedupe_columns(entries: list[dict[str, Any]], table_name: str) -> list[str]:
    """Ordered, de-duplicated column names for one table from tmap entries."""
    seen: dict[str, None] = {}
    for entry in entries:
        if str(entry.get("table_name") or "") != table_name:
            continue
        column = str(entry.get("column_name") or "")
        if column:
            seen.setdefault(column, None)
    return list(seen)


def _build_edges(entries: list[dict[str, Any]]) -> list[ComponentGraphEdge]:
    """One mapping edge per tmap entry, raw expression only.

    Real reference resolution lands in S4; `resolved_refs` stays empty and
    `confidence` stays "opaque" here by design.
    """
    return [
        ComponentGraphEdge(
            target_table=str(entry.get("table_name") or ""),
            target_column=str(entry.get("column_name") or ""),
            expression=str(entry.get("expression") or ""),
            resolved_refs=[],
            confidence="opaque",
        )
        for entry in entries
    ]


def build_component_graph(
    project_id: str,
    job_name: str,
    component_unique_name: str,
    job_data: dict[str, Any],
) -> ComponentGraphResponse | None:
    """Transform one tMap component's scan data into the Level-3 graph.

    Returns None when this tMap component has no positionally-matched
    `tmap_data` entry (an old-scan count mismatch) -- callers must treat
    that the same as the non-tMap 404 inspector fallback, since there is no
    real graph to show. Callers must have already verified
    `is_tmap_component` before calling this.
    """
    tmap_list: list[dict[str, Any]] = job_data.get("tmap_data") or []
    idx = _tmap_positional_index(job_data, component_unique_name)
    if idx is None or idx >= len(tmap_list):
        return None
    tmap = tmap_list[idx]

    input_tables = [
        ComponentGraphTable(
            table_name=row_name,
            columns=[],
            source_component=_resolve_input_source(
                job_data, component_unique_name, row_name
            ),
        )
        for row_name in (tmap.get("input_tables") or [])
    ]

    var_entries: list[dict[str, Any]] = tmap.get("var_entries") or []
    var_table_name = (
        str(var_entries[0].get("table_name") or _VAR_TABLE_NAME)
        if var_entries
        else _VAR_TABLE_NAME
    )
    var_table = ComponentGraphTable(
        table_name=var_table_name,
        columns=_dedupe_columns(var_entries, var_table_name),
    )

    entries: list[dict[str, Any]] = tmap.get("entries") or []
    output_tables = [
        ComponentGraphTable(
            table_name=table_name,
            columns=_dedupe_columns(entries, table_name),
        )
        for table_name in (tmap.get("output_tables") or [])
    ]

    edges = _build_edges(entries) + _build_edges(var_entries)

    return ComponentGraphResponse(
        project_id=project_id,
        job_name=job_name,
        component_unique_name=component_unique_name,
        input_tables=input_tables,
        var_table=var_table,
        output_tables=output_tables,
        edges=edges,
    )
