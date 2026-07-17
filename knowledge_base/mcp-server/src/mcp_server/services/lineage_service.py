"""Field/column lineage query service (S4 query-API half).

Loads `scan/lineage.json` (written by the deterministic `intodoc.lineage`
scanner pass -- see `RAG_pipeline/docs/topology-lineage-runtime-architecture.md`
sec 2.2) and answers "what feeds this field / what does this field feed"
queries with a cycle-safe, depth-capped BFS over the lineage graph.

Patterns:
- Repository-style loader (`_load_index`) owns all I/O and an in-memory
  index cache keyed by project_id, invalidated on the file's mtime -- mirrors
  the "cache keyed by fingerprint/mtime" approach used elsewhere in this
  service layer (e.g. `scan_fingerprint_service.py`).
- Pure BFS traversal (`_walk`) takes an already-built index and returns
  data; no I/O, trivially testable in isolation.

Working over raw JSON dicts (not `intodoc.models.LineageEntry`) deliberately:
every other project-graph service in this package (`job_graph_service.py`,
`component_graph_service.py`) treats scan-cache JSON as untyped dicts rather
than importing intodoc's Pydantic models, keeping the MCP API layer decoupled
from the scanner package's internal schema versioning. Same convention here.

Lineage does not cross job boundaries yet (cross-job stitching via
trunjob/table-identity is slice S5), so a single query is always scoped to
one `job_name` and the BFS never needs to jump jobs.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mcp_server.schemas import (
    LineageFieldEdge,
    LineageFieldNode,
    LineageFieldResponse,
    LineageTransformationStep,
)

logger = logging.getLogger(__name__)

# (job_name, table, column)
_FieldKey = tuple[str, str, str]
# (table, column) -- job_name is constant within one query/walk.
_NodeKey = tuple[str, str]

_DEFAULT_CONFIDENCE = "opaque"


class LineageDataUnavailableError(Exception):
    """Raised when a project has no `scan/lineage.json`.

    Covers both "never scanned" and "scanned before the S4 lineage pass
    existed" -- the caller (route) maps this to a 404, never a fabricated
    empty-but-200 response, so the two are never confused with "field has
    no lineage edges" (which is a legitimate 200 with empty edges).
    """


@dataclass(frozen=True)
class _LineageIndex:
    """Adjacency indices over lineage.json entries, keyed by (job, table, column)."""

    by_target: dict[_FieldKey, list[dict[str, Any]]] = field(default_factory=dict)
    by_source: dict[_FieldKey, list[dict[str, Any]]] = field(default_factory=dict)


# str(lineage.json path) -> (mtime, built index). Keyed by the resolved path
# rather than bare project_id so a project_id reused against a different
# output_dir (e.g. config changes between test runs in the same process)
# can never be served another directory's stale cached index.
_INDEX_CACHE: dict[str, tuple[float, _LineageIndex]] = {}


def _lineage_path(project_id: str, output_dir: Path) -> Path:
    return output_dir / project_id / "scan" / "lineage.json"


def _build_index(entries: list[dict[str, Any]]) -> _LineageIndex:
    by_target: dict[_FieldKey, list[dict[str, Any]]] = {}
    by_source: dict[_FieldKey, list[dict[str, Any]]] = {}
    for entry in entries:
        job_name = str(entry.get("job_name") or "")
        target_key: _FieldKey = (
            job_name,
            str(entry.get("target_table") or ""),
            str(entry.get("target_column") or ""),
        )
        by_target.setdefault(target_key, []).append(entry)

        # Only resolved entries carry a real source field; constant/dynamic
        # entries have an empty source and are never a valid BFS hop origin.
        if entry.get("resolved"):
            source_key: _FieldKey = (
                job_name,
                str(entry.get("source_table") or ""),
                str(entry.get("source_column") or ""),
            )
            by_source.setdefault(source_key, []).append(entry)
    return _LineageIndex(by_target=by_target, by_source=by_source)


def _load_index(project_id: str, output_dir: Path) -> _LineageIndex:
    """Load + index `lineage.json`, cached per project until its mtime changes."""
    path = _lineage_path(project_id, output_dir)
    if not path.exists():
        raise LineageDataUnavailableError(
            f"No lineage data for project '{project_id}' -- run a scan to generate scan/lineage.json"
        )

    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        logger.error("Cannot stat lineage.json for project %s: %s", project_id, exc, exc_info=True)
        raise LineageDataUnavailableError(f"Cannot read lineage data for project '{project_id}'") from exc

    cache_key = str(path)
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Unreadable lineage.json for project %s: %s", project_id, exc, exc_info=True)
        raise LineageDataUnavailableError(f"Cannot read lineage data for project '{project_id}'") from exc

    index = _build_index(list(raw.get("entries") or []))
    _INDEX_CACHE[cache_key] = (mtime, index)
    return index


def _to_edge(entry: dict[str, Any]) -> LineageFieldEdge:
    transformations = [
        LineageTransformationStep(
            transformation_type=str(t.get("transformation_type") or ""),
            expression=str(t.get("expression") or ""),
            human_explanation=str(t.get("human_explanation") or ""),
        )
        for t in entry.get("transformations") or []
    ]
    return LineageFieldEdge(
        job_name=str(entry.get("job_name") or ""),
        source_table=str(entry.get("source_table") or ""),
        source_column=str(entry.get("source_column") or ""),
        source_component=str(entry.get("source_component") or ""),
        target_table=str(entry.get("target_table") or ""),
        target_column=str(entry.get("target_column") or ""),
        target_component=str(entry.get("target_component") or ""),
        confidence=str(entry.get("confidence") or _DEFAULT_CONFIDENCE),
        resolved=bool(entry.get("resolved", False)),
        dynamic_refs=[str(r) for r in entry.get("dynamic_refs") or []],
        transformations=transformations,
    )


def _edge_dedup_key(entry: dict[str, Any]) -> tuple:
    """Identity for an entry as an edge, so `both`-direction BFS (which can
    reach the same underlying entry from either side) never emits a duplicate."""
    return (
        str(entry.get("job_name") or ""),
        str(entry.get("source_table") or ""),
        str(entry.get("source_column") or ""),
        str(entry.get("target_table") or ""),
        str(entry.get("target_column") or ""),
        tuple(str(t.get("expression") or "") for t in entry.get("transformations") or []),
    )


def _walk(
    index: _LineageIndex,
    job_name: str,
    start_table: str,
    start_column: str,
    start_component: str,
    walk_direction: Literal["up", "down"],
    max_depth: int,
    nodes: dict[_NodeKey, LineageFieldNode],
    edges: list[LineageFieldEdge],
    edge_seen: set[tuple],
) -> bool:
    """BFS in one direction from the start field. Returns True if depth-capped.

    "up" walks by_target (this node is the target of each candidate entry,
    so the next hop is the entry's source). "down" walks by_source (this
    node is the source, next hop is the entry's target). Cycle-safe via a
    per-direction visited set; the same physical entry discovered again on
    revisit still gets its edge recorded (so a cycle's full edge set shows
    up) but is never re-enqueued.
    """
    visited: set[_NodeKey] = {(start_table, start_column)}
    # (table, column, component-filter-for-this-hop-only, depth)
    queue: deque[tuple[str, str, str, int]] = deque([(start_table, start_column, start_component, 0)])
    truncated = False

    while queue:
        table, column, component_filter, depth = queue.popleft()
        lookup_key: _FieldKey = (job_name, table, column)
        candidates = (
            index.by_target.get(lookup_key, []) if walk_direction == "up" else index.by_source.get(lookup_key, [])
        )

        if depth >= max_depth:
            if candidates:
                truncated = True
            continue

        for entry in candidates:
            if component_filter:
                role_component = str(
                    entry.get("target_component" if walk_direction == "up" else "source_component") or ""
                )
                if role_component != component_filter:
                    continue

            dedup_key = _edge_dedup_key(entry)
            if dedup_key not in edge_seen:
                edge_seen.add(dedup_key)
                edges.append(_to_edge(entry))

            if walk_direction == "up":
                if not entry.get("resolved"):
                    continue  # constant/dynamic source -- edge recorded, no further node
                next_table = str(entry.get("source_table") or "")
                next_column = str(entry.get("source_column") or "")
                next_component = str(entry.get("source_component") or "")
            else:
                next_table = str(entry.get("target_table") or "")
                next_column = str(entry.get("target_column") or "")
                next_component = str(entry.get("target_component") or "")

            next_key: _NodeKey = (next_table, next_column)
            nodes.setdefault(
                next_key,
                LineageFieldNode(job_name=job_name, table=next_table, column=next_column, component=next_component),
            )
            if next_key not in visited:
                visited.add(next_key)
                queue.append((next_table, next_column, "", depth + 1))

    return truncated


def build_field_lineage(
    project_id: str,
    output_dir: Path,
    *,
    job_name: str,
    table: str,
    column: str,
    component: str = "",
    direction: Literal["up", "down", "both"] = "both",
    max_depth: int = 10,
) -> LineageFieldResponse:
    """Answer a field-lineage query: BFS over `scan/lineage.json`'s graph.

    Never raises for a field with no lineage edges -- that is a legitimate,
    meaningful 200 (empty `nodes`/`edges` beyond the start field). Raises
    `LineageDataUnavailableError` only when the project has no lineage data
    at all, which the route maps to 404.
    """
    index = _load_index(project_id, output_dir)

    nodes: dict[_NodeKey, LineageFieldNode] = {
        (table, column): LineageFieldNode(job_name=job_name, table=table, column=column, component=component)
    }
    edges: list[LineageFieldEdge] = []
    edge_seen: set[tuple] = set()
    truncated = False

    walk_directions: list[Literal["up", "down"]] = []
    if direction in ("up", "both"):
        walk_directions.append("up")
    if direction in ("down", "both"):
        walk_directions.append("down")

    for walk_direction in walk_directions:
        hit_cap = _walk(
            index, job_name, table, column, component, walk_direction, max_depth, nodes, edges, edge_seen
        )
        truncated = truncated or hit_cap

    return LineageFieldResponse(
        project_id=project_id,
        job_name=job_name,
        start=nodes[(table, column)],
        direction=direction,
        max_depth=max_depth,
        nodes=list(nodes.values()),
        edges=edges,
        truncated=truncated,
    )
