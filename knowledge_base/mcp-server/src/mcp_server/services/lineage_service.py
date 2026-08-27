"""Field/column and job/group lineage query service (S4 query-API half).

Loads `scan/lineage.json` (written by the deterministic `intodoc.lineage`
scanner pass -- see `RAG_pipeline/docs/topology-lineage-runtime-architecture.md`
sec 2.2) and answers two questions at two altitudes:

* `build_field_lineage` -- "what feeds this field / what does this field feed",
  a cycle-safe, depth-capped BFS over the lineage graph from one column.
* `build_job_lineage` -- "what data moves out of these jobs, into which
  tables, and how is it transformed", a flat paged rollup over whole jobs or a
  functional group. This is the entry point for a caller who does not yet know
  which column is worth tracing (an LLM, or a landscape UI drilling in).

Patterns:
- Repository-style loaders (`_load_index`, `_load_group_index`) own all I/O and
  an in-memory cache keyed by the resolved file path, invalidated on the file's
  mtime -- mirrors the "cache keyed by fingerprint/mtime" approach used
  elsewhere in this service layer (e.g. `scan_fingerprint_service.py`).
- Pure functions over an already-built index (`_walk`, `aggregate_job_lineage`,
  `resolve_group_jobs`, `match_job_names`) do the actual work; no I/O, so every
  selection and aggregation rule is testable without a filesystem.

Working over raw JSON dicts (not `intodoc.models.LineageEntry`) deliberately:
every other project-graph service in this package (`job_graph_service.py`,
`component_graph_service.py`) treats scan-cache JSON as untyped dicts rather
than importing intodoc's Pydantic models, keeping the MCP API layer decoupled
from the scanner package's internal schema versioning. Same convention here.

Lineage does not cross job boundaries yet (cross-job stitching via
trunjob/table-identity is slice S5), so a *field* query is always scoped to one
`job_name` and the BFS never needs to jump jobs. The job rollup unions several
jobs' entries but still never stitches an edge across them.

TWO NAMING SPACES COEXIST IN `lineage.json`
-------------------------------------------
`job_name` is whatever the scanner resolved for that unit, and for a composite
(multi-zip) scan that is one of two very different things:

* a short Talend design name -- `RHM2Smartflow_Employee`
* a long, hash-suffixed composite id --
  `VolkerVessels_Workorder_Talend_exports_VQ_RHM2SF___intod-31f9ad59e878`,
  minted when a display name is not unique across zips (see the partition
  planner's per-zip unit allowlist).

Both appear in the same file, sometimes inside the same group. Job selection is
therefore EXACT ONLY (`match_job_names`): exact match first, then a
case-insensitive exact fallback, and nothing else. No prefix, substring or
fuzzy matching -- `RHM2Smartflow_Workorders` vs `Copy_of_RHM2Smartflow_Workorders`
are different integrations, and silently attributing one's field mappings to
the other is a correctness failure that reads as authoritative. A requested name
that matches nothing is simply absent from the response's `jobs` list.

GROUP RESOLUTION SOURCE
-----------------------
`lineage.json` entries carry no group field, so `group=` is resolved through
`scan/manifest.json` -> `job_groups`, which is a flat
``{job_name: group_path}`` dict (NOT group -> jobs; it is inverted here).
`group_path` is a full source path, e.g.
``VolkerVessels_Workorder/Talend exports/EnterpriseServiceBus.__intodoc_zip__/ENTERPRISESERVICEBUS/esb_main``
-- nobody types that. `resolve_group_jobs` matches in tiers, most specific
first, and the first tier that matches anything wins (so a mid-path segment
match can never dilute a real trailing-segment hit):

1. the whole path, case-insensitively
2. the trailing path segment (`esb_main`)
3. any path segment, with a `.__intodoc_zip__` suffix stripped (`VQ_RHM2SF`,
   which is both a zip name and an intermediate directory)

Every tier is exact segment equality -- never substring -- for the same reason
job matching is exact.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from mcp_server.providers.custom.source_context import redact_inline_credentials
from mcp_server.utils.source_origin import ZIP_MARKER, strip_origin_marker
from mcp_server.schemas import (
    LineageConfidenceTotals,
    LineageFieldEdge,
    LineageFieldNode,
    LineageFieldResponse,
    LineageJobResponse,
    LineageRollupEdge,
    LineageRollupTotals,
    LineageTransformationStep,
)

logger = logging.getLogger(__name__)

# (job_name, table, column)
_FieldKey = tuple[str, str, str]
# (table, column) -- job_name is constant within one query/walk.
_NodeKey = tuple[str, str]

_DEFAULT_CONFIDENCE = "opaque"

#: Confidence buckets `LineageConfidenceTotals` exposes. Anything else the
#: scanner emits is counted under `_DEFAULT_CONFIDENCE` (see that model's
#: docstring) -- the raw value still survives on the edge itself.
_CONFIDENCE_BUCKETS = ("certain", "likely", "dynamic", "inferred", "opaque")

#: Hard ceiling on a returned `expression`. These are raw generated Java
#: bodies -- the werkorder scan holds one over 11 KB -- and this endpoint feeds
#: an LLM prompt, so an unbounded field is a context-budget hazard as well as a
#: leak surface. 400 chars keeps the mapping recognisable (`row1.WorkorderNr`,
#: the head of a ternary chain) without shipping whole code bodies; callers that
#: need the full body have the field API and the evidence bundles.
EXPRESSION_MAX_CHARS = 400

#: Cap on the distinct `target_tables` summary list. A bounded orientation aid,
#: not an inventory -- the full table set is derivable by paging `edges`.
TARGET_TABLE_CAP = 40

#: Ceiling on how many job names one request may name. Bounds the union work
#: and keeps the response's `jobs` list readable.
MAX_JOBS_PER_REQUEST = 20

#: Kept for callers that import it; the marker set itself now lives in
#: ``utils.source_origin`` so zip and repo origins strip identically.
_ZIP_MARKER_SUFFIX = ZIP_MARKER


class LineageQueryError(Exception):
    """Base for the lineage query failures that a route turns into a 404.

    Every subclass carries a `reason`: a STABLE, machine-readable discriminator
    that goes into the HTTP error body next to the human-readable message.
    It exists because these 404s drive opposite user-facing outcomes ("rescan
    the project" vs "that name is not in the scan"), and a consumer must never
    have to pattern-match prose to tell them apart -- rewording a message would
    silently flip a downstream behaviour. Reason codes are part of the API
    contract: rename one only as a breaking change.

    The codes are mirrored in `schemas.LineageErrorDetail.reason` (a closed
    Literal) and a test asserts every subclass's reason is declared there, so a
    new failure mode cannot ship without a discriminator.
    """

    reason: ClassVar[str] = "lineage_error"


class LineageDataUnavailableError(LineageQueryError):
    """Raised when a project has no `scan/lineage.json`.

    Covers both "never scanned" and "scanned before the S4 lineage pass
    existed" -- the caller (route) maps this to a 404, never a fabricated
    empty-but-200 response, so the two are never confused with "field has
    no lineage edges" (which is a legitimate 200 with empty edges).

    Consumer outcome: the project needs a (re)scan.
    """

    reason: ClassVar[str] = "lineage_artifact_absent"


class LineageSelectionEmptyError(LineageQueryError):
    """Raised when lineage data EXISTS but the requested job/group matched none.

    Deliberately a SIBLING of `LineageDataUnavailableError`, never a subclass:
    both map to 404, but they mean opposite things. "This project has no lineage
    at all, go run a scan" versus "the project has lineage, your job or group
    name is wrong". Collapsing them sends an operator to re-run a full scan
    because of a typo, which is the harm this split exists to prevent.

    Consumer outcome: the requested name is not in the scan.
    """

    reason: ClassVar[str] = "lineage_selection_empty"


@dataclass(frozen=True)
class _LineageIndex:
    """Indices over lineage.json entries.

    `by_target`/`by_source` are the field-BFS adjacency, keyed by
    (job, table, column). `by_job` is the rollup index: the job rollup must not
    rescan the whole entries list per request (werkorder: 2222 entries, 91 jobs),
    and `jobs_by_lower` backs the case-insensitive exact fallback without a
    second pass.
    """

    by_target: dict[_FieldKey, list[dict[str, Any]]] = field(default_factory=dict)
    by_source: dict[_FieldKey, list[dict[str, Any]]] = field(default_factory=dict)
    by_job: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # lowercased job_name -> the actual job_name(s) that lowercase to it.
    jobs_by_lower: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _GroupIndex:
    """Group -> job names, inverted out of `manifest.json`'s `job_groups`.

    `jobs_by_group` is keyed by the verbatim group path; the lookup maps are
    precomputed so `resolve_group_jobs` stays a pure dict-hit function (the
    manifest is ~1.4 MB for werkorder -- neither parsing nor inverting it
    belongs in a per-request path).
    """

    jobs_by_group: dict[str, list[str]] = field(default_factory=dict)
    # lowercased whole group path -> group paths
    by_lower_path: dict[str, list[str]] = field(default_factory=dict)
    # lowercased trailing path segment -> group paths
    by_lower_leaf: dict[str, list[str]] = field(default_factory=dict)
    # lowercased any path segment (zip marker stripped) -> group paths
    by_lower_segment: dict[str, list[str]] = field(default_factory=dict)


# str(lineage.json path) -> (mtime, built index). Keyed by the resolved path
# rather than bare project_id so a project_id reused against a different
# output_dir (e.g. config changes between test runs in the same process)
# can never be served another directory's stale cached index.
_INDEX_CACHE: dict[str, tuple[float, _LineageIndex]] = {}

# str(manifest.json path) -> (mtime, built group index). Same keying rationale.
_GROUP_CACHE: dict[str, tuple[float, _GroupIndex]] = {}


def _lineage_path(project_id: str, output_dir: Path) -> Path:
    return output_dir / project_id / "scan" / "lineage.json"


def _manifest_path(project_id: str, output_dir: Path) -> Path:
    return output_dir / project_id / "scan" / "manifest.json"


def _build_index(entries: list[dict[str, Any]]) -> _LineageIndex:
    by_target: dict[_FieldKey, list[dict[str, Any]]] = {}
    by_source: dict[_FieldKey, list[dict[str, Any]]] = {}
    by_job: dict[str, list[dict[str, Any]]] = {}
    jobs_by_lower: dict[str, list[str]] = {}
    for entry in entries:
        job_name = str(entry.get("job_name") or "")
        target_key: _FieldKey = (
            job_name,
            str(entry.get("target_table") or ""),
            str(entry.get("target_column") or ""),
        )
        by_target.setdefault(target_key, []).append(entry)

        if job_name not in by_job:
            by_job[job_name] = []
            jobs_by_lower.setdefault(job_name.lower(), []).append(job_name)
        by_job[job_name].append(entry)

        # Only resolved entries carry a real source field; constant/dynamic
        # entries have an empty source and are never a valid BFS hop origin.
        if entry.get("resolved"):
            source_key: _FieldKey = (
                job_name,
                str(entry.get("source_table") or ""),
                str(entry.get("source_column") or ""),
            )
            by_source.setdefault(source_key, []).append(entry)
    return _LineageIndex(
        by_target=by_target, by_source=by_source, by_job=by_job, jobs_by_lower=jobs_by_lower
    )


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


def _split_group_path(group_path: str) -> list[str]:
    """Split a manifest group path into segments, tolerating Windows separators."""
    normalized = group_path.replace("\\", "/")
    return [segment for segment in normalized.split("/") if segment]


def _strip_zip_marker(segment: str) -> str:
    """`VQ_RHM2SF.__intodoc_zip__` -> `VQ_RHM2SF`; other segments unchanged.

    Composite scans name each sub-project directory after its origin -- the
    inner zip (`.__intodoc_zip__`) or the cloned member repo
    (`.__intodoc_repo__`) -- with the marker appended. Operators refer to the
    zip or repo, not the marker, so the marker must not defeat an otherwise
    exact segment match.

    Delegated to `utils.source_origin` so the disambiguated collision form
    (`X.__intodoc_zip__-<digest>`) reduces to `X` too; the old bare-suffix
    match left those segments unstripped and therefore unmatchable.
    """
    return strip_origin_marker(segment)


def _build_group_index(job_groups: dict[str, Any]) -> _GroupIndex:
    """Invert `manifest.json`'s `{job_name: group_path}` into group -> jobs."""
    jobs_by_group: dict[str, list[str]] = {}
    for job_name, group_path in job_groups.items():
        if not isinstance(group_path, str) or not group_path:
            continue
        jobs_by_group.setdefault(group_path, []).append(str(job_name))

    by_lower_path: dict[str, list[str]] = {}
    by_lower_leaf: dict[str, list[str]] = {}
    by_lower_segment: dict[str, list[str]] = {}
    for group_path, jobs in jobs_by_group.items():
        jobs.sort()
        by_lower_path.setdefault(group_path.lower(), []).append(group_path)

        segments = _split_group_path(group_path)
        if not segments:
            continue
        by_lower_leaf.setdefault(segments[-1].lower(), []).append(group_path)
        for segment in {_strip_zip_marker(s).lower() for s in segments}:
            by_lower_segment.setdefault(segment, []).append(group_path)

    return _GroupIndex(
        jobs_by_group=jobs_by_group,
        by_lower_path=by_lower_path,
        by_lower_leaf=by_lower_leaf,
        by_lower_segment=by_lower_segment,
    )


def _load_group_index(project_id: str, output_dir: Path) -> _GroupIndex:
    """Load + invert `manifest.json`'s `job_groups`, cached until its mtime changes.

    A missing or unreadable manifest is NOT fatal: it yields an empty index, so
    a `group=` request degrades into "matched no jobs" (a 404 naming the group)
    rather than a 500. The job-name path never touches the manifest at all.
    """
    path = _manifest_path(project_id, output_dir)
    if not path.exists():
        logger.warning(
            "No scan/manifest.json for project %s -- group-based lineage selection unavailable",
            project_id,
        )
        return _GroupIndex()

    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        logger.error(
            "Cannot stat manifest.json for project %s: %s", project_id, exc, exc_info=True
        )
        return _GroupIndex()

    cache_key = str(path)
    cached = _GROUP_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(
            "Unreadable manifest.json for project %s: %s", project_id, exc, exc_info=True
        )
        return _GroupIndex()

    job_groups = raw.get("job_groups")
    if not isinstance(job_groups, dict):
        logger.warning(
            "manifest.json for project %s has no usable job_groups mapping (%s)",
            project_id,
            type(job_groups).__name__,
        )
        return _GroupIndex()

    index = _build_group_index(job_groups)
    _GROUP_CACHE[cache_key] = (mtime, index)
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


# --------------------------------------------------------------------------
# Job/group rollup (GET /lineage/job)
# --------------------------------------------------------------------------


def resolve_group_jobs(group_index: _GroupIndex, group: str) -> list[str]:
    """Resolve one functional-group name to the job names inside it.

    Tiered, most specific first; the first tier that matches anything wins.
    See the module docstring ("GROUP RESOLUTION SOURCE") for why the tiers
    exist and why each one is exact equality rather than a substring test.

    Returns a sorted, de-duplicated job-name list -- possibly empty, which the
    caller reports as "no lineage entries for group X" rather than treating as
    an error here.
    """
    needle = group.strip().lower()
    if not needle:
        return []

    for lookup in (
        group_index.by_lower_path,
        group_index.by_lower_leaf,
        group_index.by_lower_segment,
    ):
        group_paths = lookup.get(needle)
        if not group_paths:
            continue
        jobs: set[str] = set()
        for group_path in group_paths:
            jobs.update(group_index.jobs_by_group.get(group_path, []))
        if jobs:
            return sorted(jobs)
    return []


def match_job_names(index: _LineageIndex, requested: Iterable[str]) -> list[str]:
    """Resolve requested job names against the names present in lineage.json.

    EXACT ONLY: exact match first, then a case-insensitive exact fallback.
    Never prefix, substring or fuzzy -- see the module docstring ("TWO NAMING
    SPACES"). A name that matches nothing is dropped silently; the response's
    `jobs` list is what tells the caller which of its names landed.

    A case-insensitive collision (two real jobs differing only in case, which
    the werkorder scan does contain at group level) resolves to BOTH names --
    dropping one would silently hide an integration, and picking one would be
    arbitrary.
    """
    matched: set[str] = set()
    for raw_name in requested:
        name = raw_name.strip()
        if not name:
            continue
        if name in index.by_job:
            matched.add(name)
            continue
        for actual in index.jobs_by_lower.get(name.lower(), []):
            matched.add(actual)
    return sorted(matched)


def _redact_text(raw: str) -> str:
    """Credential-redact one field of scanner-derived text.

    Reuses the pipeline's own redactor (`source_context.redact_inline_credentials`,
    the same function `claude_service` and the Phase 3 evidence lane apply to
    scan-cache code) rather than a second implementation -- a divergent
    redactor is how a closed leak reopens. Project rule: never send raw
    credentials to an LLM, and this endpoint's output is prompt material.
    """
    if not raw:
        return ""
    redacted, count = redact_inline_credentials(raw)
    if count:
        logger.info("Redacted %d inline credential value(s) from lineage rollup text", count)
    return redacted


def _safe_expression(raw: str) -> str:
    """Credential-redact then hard-clip one transformation expression.

    Order matters and is not interchangeable: redaction runs on the FULL text
    before clipping. Clipping first can cut a `password=RAW(...)` literal in
    half, which stops `redact_inline_credentials` from matching it and ships the
    surviving prefix of the secret. Redact-then-clip cannot leak that way.

    The clip keeps the string at most `EXPRESSION_MAX_CHARS` characters, with a
    trailing ellipsis so a caller can tell a clipped expression from a short one.
    """
    redacted = _redact_text(raw)
    if len(redacted) <= EXPRESSION_MAX_CHARS:
        return redacted
    return redacted[: EXPRESSION_MAX_CHARS - 3] + "..."


def _rollup_edge_dedup_key(entry: dict[str, Any], transformation: dict[str, Any]) -> tuple:
    """Identity of one flattened (entry, transformation) row.

    Mirrors `_edge_dedup_key` (the field API's equivalent) and extends it with
    the flattened transformation fields, because the rollup emits one row per
    transformation step rather than one row per entry.

    Keyed on the RAW expression, deliberately: two genuinely different
    expressions that happen to share their first 400 characters -- long ternary
    chains in generated Talend code frequently do -- must stay two rows.
    Deduping on the clipped text would collapse them into one and silently drop
    a real mapping.
    """
    return (
        str(entry.get("job_name") or ""),
        str(entry.get("source_component") or ""),
        str(entry.get("source_table") or ""),
        str(entry.get("source_column") or ""),
        str(entry.get("target_component") or ""),
        str(entry.get("target_table") or ""),
        str(entry.get("target_column") or ""),
        str(transformation.get("transformation_type") or ""),
        str(transformation.get("expression") or ""),
        str(entry.get("confidence") or _DEFAULT_CONFIDENCE),
        bool(entry.get("resolved", False)),
        tuple(str(r) for r in entry.get("dynamic_refs") or []),
    )


def _to_rollup_edge(entry: dict[str, Any], transformation: dict[str, Any]) -> LineageRollupEdge:
    return LineageRollupEdge(
        job_name=str(entry.get("job_name") or ""),
        source_component=str(entry.get("source_component") or ""),
        source_table=str(entry.get("source_table") or ""),
        source_column=str(entry.get("source_column") or ""),
        target_component=str(entry.get("target_component") or ""),
        target_table=str(entry.get("target_table") or ""),
        target_column=str(entry.get("target_column") or ""),
        transformation_type=str(transformation.get("transformation_type") or ""),
        expression=_safe_expression(str(transformation.get("expression") or "")),
        # Redacted but NOT clipped: the contract caps `expression` only, and
        # an explanation is prose the scanner wrote, not a code body.
        human_explanation=_redact_text(str(transformation.get("human_explanation") or "")),
        confidence=str(entry.get("confidence") or _DEFAULT_CONFIDENCE),
        resolved=bool(entry.get("resolved", False)),
        dynamic_refs=[str(r) for r in entry.get("dynamic_refs") or []],
    )


def _rollup_sort_key(edge: LineageRollupEdge) -> tuple:
    """Total order over rollup rows -- the contract that makes paging stable.

    Primary key is `not resolved`, so CONFIRMED mappings fill page 1: a caller
    that reads only the first page (an LLM with a bounded context, a UI table)
    sees the resolved column-to-column mappings, not 600 dynamic
    `globalMap.get(...)` rows that carry no source field.

    Then the contract's declared keys (job, target table/column, source
    table/column), then transformation type and expression as final
    tiebreakers. Without those last two, the several rows of one multi-step
    mapping would compare equal, and Python's stable sort would order them by
    whatever order the entries happened to be indexed in -- fine within one
    call, but `offset`/`limit` paging across calls needs a TOTAL order, not a
    merely deterministic one.
    """
    return (
        not edge.resolved,
        edge.job_name,
        edge.target_table,
        edge.target_column,
        edge.source_table,
        edge.source_column,
        edge.transformation_type,
        edge.expression,
    )


def _confidence_totals(entries: Iterable[dict[str, Any]]) -> LineageConfidenceTotals:
    counts = {bucket: 0 for bucket in _CONFIDENCE_BUCKETS}
    unknown: set[str] = set()
    for entry in entries:
        confidence = str(entry.get("confidence") or _DEFAULT_CONFIDENCE)
        if confidence in counts:
            counts[confidence] += 1
        else:
            unknown.add(confidence)
            counts[_DEFAULT_CONFIDENCE] += 1
    if unknown:
        # Once per request, not once per entry: a new scanner confidence level
        # is a schema change worth noticing, not per-row log noise.
        logger.warning(
            "Lineage rollup bucketed unrecognised confidence value(s) %s under '%s'",
            sorted(unknown),
            _DEFAULT_CONFIDENCE,
        )
    return LineageConfidenceTotals(**counts)


def aggregate_job_lineage(
    index: _LineageIndex, job_names: Sequence[str]
) -> tuple[LineageRollupTotals, list[str], list[LineageRollupEdge]]:
    """Pure aggregation over an already-built index. No I/O.

    Returns (totals, capped distinct target tables, fully sorted+deduped rows).
    Paging is applied by the caller so `total_edges`/`has_more` are computed
    against the complete selection rather than a page.

    `totals` counts ENTRIES verbatim (no dedup), so selecting every job
    reproduces `lineage.json`'s own `summary` block and the numbers stay
    auditable against the scanner. `edges` is deduped, so
    `totals.entries != len(edges)` is normal in both directions: a multi-step
    mapping is one entry and several rows, and an entry duplicated across the
    file collapses to one row.
    """
    selected: list[dict[str, Any]] = []
    for job_name in job_names:
        selected.extend(index.by_job.get(job_name, []))

    resolved_count = sum(1 for entry in selected if entry.get("resolved"))
    totals = LineageRollupTotals(
        entries=len(selected),
        resolved=resolved_count,
        unresolved=len(selected) - resolved_count,
        confidence=_confidence_totals(selected),
    )

    target_tables: set[str] = set()
    edges: list[LineageRollupEdge] = []
    seen: set[tuple] = set()
    for entry in selected:
        table = str(entry.get("target_table") or "")
        if table:
            target_tables.add(table)
        # An entry with no transformations still produces a row: the mapping
        # exists even when the scanner captured no expression for it, and
        # dropping it would understate the flow.
        transformations = list(entry.get("transformations") or []) or [{}]
        for transformation in transformations:
            if not isinstance(transformation, dict):
                continue
            key = _rollup_edge_dedup_key(entry, transformation)
            if key in seen:
                continue
            seen.add(key)
            edges.append(_to_rollup_edge(entry, transformation))

    edges.sort(key=_rollup_sort_key)
    return totals, sorted(target_tables)[:TARGET_TABLE_CAP], edges


def build_job_lineage(
    project_id: str,
    output_dir: Path,
    *,
    jobs: Sequence[str] = (),
    group: str = "",
    offset: int = 0,
    limit: int = 50,
) -> LineageJobResponse:
    """Answer a job/group lineage rollup: every mapping the selected jobs perform.

    Selection is the union of `jobs` (exact / case-insensitive-exact only) and,
    when `group` is given, the jobs the scan manifest places in that group.

    Raises:
        LineageDataUnavailableError: the project has no `scan/lineage.json`.
        LineageSelectionEmptyError: lineage data exists, but nothing in it
            belongs to the requested jobs/group. Distinct types on purpose --
            see `LineageSelectionEmptyError`.
    """
    index = _load_index(project_id, output_dir)

    requested: list[str] = [str(j) for j in jobs]
    group_name = group.strip()
    if group_name:
        group_index = _load_group_index(project_id, output_dir)
        requested.extend(resolve_group_jobs(group_index, group_name))

    matched = match_job_names(index, requested)
    if not matched:
        raise LineageSelectionEmptyError(_selection_empty_message(jobs, group_name))

    totals, target_tables, edges = aggregate_job_lineage(index, matched)

    page = edges[offset : offset + limit]
    return LineageJobResponse(
        project_id=project_id,
        jobs=matched,
        totals=totals,
        target_tables=target_tables,
        edges=page,
        offset=offset,
        limit=limit,
        returned=len(page),
        total_edges=len(edges),
        has_more=(offset + len(page)) < len(edges),
    )


def _selection_empty_message(jobs: Sequence[str], group: str) -> str:
    """Human-readable 404 detail naming exactly what failed to match.

    Always starts with "No lineage entries for" so it is trivially
    distinguishable from `LineageDataUnavailableError`'s "No lineage data for
    project ..." -- the two 404s mean different things and must not read alike.
    """
    parts: list[str] = []
    if group:
        parts.append(f"group '{group}'")
    named = [str(j).strip() for j in jobs if str(j).strip()]
    if named:
        parts.append("job(s) " + ", ".join(f"'{name}'" for name in named))
    selection = " and ".join(parts) if parts else "the requested selection"
    return (
        f"No lineage entries for {selection} -- the project has lineage data, but nothing "
        "in it matches these names (job names are matched exactly; check the scan manifest)"
    )
