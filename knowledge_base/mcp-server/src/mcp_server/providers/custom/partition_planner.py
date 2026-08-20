"""Deterministic sub-project partitioning for composite (multi-source-root) uploads.

A composite upload bundles several independent Talend exports into one scan. The
legacy doc pipeline sizes a run for a single *small* project (call budget =
``docgen_call_budget_base + groups * docgen_call_budget_per_group``, capped by
``docgen_max_run_calls``). Running a composite whole starves that budget: on the
werkorder scan (429 units / 61 groups) Phase 1 alone ate ~124 of a 200-call run
budget and Phase 2 (one analysis call per unit) needed 429+, so ~350 units hit
``budget_exhaustion`` and the coverage gate failed.

This module splits one composite scan into N independent sub-project partitions --
one per composite-zip source root -- so each runs as its own scoped generation
with its own call budget (recomputed by the harness from the scoped group count).

Sub-project identity is derived from ``UnitFact.source_root``:

* Composite-ZIP scans embed the marker ``.__intodoc_zip__`` in each source_root,
  e.g. ``.../Assetcheck.__intodoc_zip__/ASSETCHECK`` and
  ``.../Assetcheck.__intodoc_zip__/REFERENCES``. The path segment ending in
  ``.__intodoc_zip__`` names the sub-project (stem = ``Assetcheck``). A shared
  ``REFERENCES`` root nested *inside* a zip folds into that zip automatically, so
  no unit is dropped or duplicated.
* If composite markers are present but a unit's source_root carries no zip stem
  (a shared root that cannot be attributed to one zip), it is placed in a single
  ``__shared__`` partition rather than dropped. Consuming sub-projects reference
  it through the cross-project index. (The werkorder scan never hits this branch;
  every REFERENCES root there is nested inside a specific zip.)
* If NO source_root carries a composite marker -- every existing single-root
  project (alitalend, kruitbosch, legacy assetcheck, reversEngineer_ALI, ...) --
  the whole scan is a single partition named :data:`WHOLE_PROJECT_PARTITION`.
  ``is_composite`` is ``False`` and the orchestrator runs exactly one scoped
  generation with the un-subdivided output path: byte-for-byte today's behaviour.

Partitioning is by ``source_root`` (unit identity), never by group. On the
werkorder scan 9 groups (``Global``, ``MessageLogging``, ``Misc``, ``maximo``,
...) span multiple sub-projects; scoping by group would split a unit's evidence.
Scoping by source_root keeps every unit whole and yields, per partition, the
subset of each shared-label group whose units live in that partition. The union
of all partitions' units therefore equals the full scan with no overlap.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mcp_server.config import config
from mcp_server.providers.custom.models import FactsManifest, UnitFact

# Marker the composite-ZIP scanner embeds in every source_root segment it derives
# from an inner uploaded archive.
ZIP_MARKER = ".__intodoc_zip__"

# Partition name for composite units whose source_root has no zip stem.
SHARED_PARTITION = "__shared__"

# Partition name used when the scan is not composite (single sub-project).
WHOLE_PROJECT_PARTITION = "__all__"


# --------------------------------------------------------------------------- #
# Key-system denoising
#
# Per-unit ``key_systems`` are harvested from component params whose *name*
# hints at a system (url/host/endpoint/server/database) plus scanner manifest
# externals. On real Talend exports the harvested *values* include junk that
# is not a system identity (verified on the werkorder run's
# ``topology/cross_project_index.json``): literal ``true``/``false``, empty
# quoted strings, ``localhost``, bare numbers (ports), Talend component
# instance names (``cDirect_1``, ``cSEDA_2``), raw Java/tMap expressions
# (``((String) globalMap.get("objectUrl")) + ...``), ``context.*`` variable
# references, and quoted-literal wrappers around otherwise meaningful values
# (``"[SERVER_IP]"``). These helpers normalize and filter deterministically at
# the shared extraction seam (``validators._systems_for`` and the scoped
# manifest recompute below) so every consumer -- facts manifest, cross-project
# index/topology markdown, business-topology evidence, landscape rendering --
# sees only meaningful system identities.
# --------------------------------------------------------------------------- #

# Talend component *instance* names: c-prefixed Camel components (cDirect_1,
# cJMS_3) and t-prefixed DI components (tMSSqlOutput_2). These are code
# artifacts, never external system identities.
_COMPONENT_INSTANCE_RE = re.compile(r"^[ct][A-Z]\w*_\d+$")

# Bare numeric values (ports, counters), optionally dotted (e.g. "1.5").
_BARE_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*$")

# Literal values that carry no system identity, compared lowercase.
_MEANINGLESS_LITERALS = {"true", "false", "null", "none", "localhost", "127.0.0.1", "::1"}

# Characters that only appear in code/expressions, never in host, DB, queue,
# or redaction-placeholder names. Quotes remaining AFTER normalization signal
# an embedded string literal inside an expression.
_CODE_MARKER_CHARS = ("(", ")", "+", ";", "\n", "\r", "\t", '"', "'")

# System keys longer than this are code fragments or path dumps, not names.
_MAX_SYSTEM_KEY_LEN = 80


def normalize_system_key(value: str) -> str:
    """Strip quoted-literal wrappers from a harvested key-system value.

    Talend serializes param values as Java string literals, so meaningful
    identities often arrive wrapped in quotes (``"[SERVER_IP]"`` -> the
    redaction placeholder ``[SERVER_IP]``, which must be KEPT). Matched
    surrounding quote pairs are stripped repeatedly; a stray unbalanced
    leading/trailing quote (truncated literals like ``"[URL_REDACTED]``) is
    stripped as well. Whitespace is trimmed at every step.
    """
    text = str(value or "").strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text.strip("'\"").strip()


def is_meaningful_system_key(value: str) -> bool:
    """True when a harvested ``key_systems`` value names an actual system.

    Deterministic denoise filter (see the section comment above for the junk
    categories verified on the werkorder run). Legitimate host names, DB and
    queue names, endpoint paths, and redaction placeholders
    (``[SERVER_IP]``, ``[REDACTED-*]``) all pass.
    """
    text = normalize_system_key(value)
    if not text:
        return False
    if text.lower() in _MEANINGLESS_LITERALS:
        return False
    if _BARE_NUMBER_RE.match(text):
        return False
    if len(text) > _MAX_SYSTEM_KEY_LEN:
        return False
    if _COMPONENT_INSTANCE_RE.match(text):
        return False
    if text.startswith("context."):
        return False
    if any(marker in text for marker in _CODE_MARKER_CHARS):
        return False
    return True


def clean_system_keys(values: Iterable[str]) -> list[str]:
    """Normalize, filter, dedupe, and sort a collection of key-system values."""
    return sorted({
        normalize_system_key(value)
        for value in values
        if is_meaningful_system_key(value)
    })


def subproject_key(source_root: str) -> str | None:
    """Return the sub-project name for a source_root, or ``None`` if it carries no
    composite-zip marker.

    The sub-project is the stem of the path segment ending in
    :data:`ZIP_MARKER`. A nested ``.../X.__intodoc_zip__/REFERENCES`` folds into
    ``X`` because the marker segment is matched anywhere in the path.
    """
    normalized = (source_root or "").replace("\\", "/")
    for segment in normalized.split("/"):
        if segment.endswith(ZIP_MARKER):
            return segment[: -len(ZIP_MARKER)] or SHARED_PARTITION
    return None


# Planning ratio for Phase 1 investigation tasks: whole jobs bin-pack into
# ~48 KB evidence tasks, typically 3-4 jobs per task on real Talend exports
# (verified: the werkorder scan spent ~124 Phase 1 calls on 429 units).
_PHASE1_UNITS_PER_TASK = 3


def _catalog_doc_counts() -> tuple[int, int]:
    """(group-level doc count, per-unit catalog entry count) from the catalog."""
    from mcp_server.providers.custom.doc_catalog import load_doc_catalog

    catalog = load_doc_catalog()
    per_unit = len(catalog.per_unit_docs)
    return len(catalog.docs) - per_unit, per_unit


def estimate_partition_calls(
    unit_count: int,
    group_count: int,
    *,
    base: int | None = None,
) -> int:
    """Estimate the model calls one scoped run needs (uncapped).

    Mirrors ``harness._plan_call_budget`` at planning altitude (the planner
    has unit/group counts, not evidence bytes):

    - evidence agents: 0 on standard runs unless the default agents are
      explicitly enabled (R2.3);
    - Phase 1: ~1 task per ``_PHASE1_UNITS_PER_TASK`` units, at least 1/group
      (~2/group when the agentic chunk-manifest investigator is enabled);
    - Phase 2: 1 claim task per unit packet (``phase2_claim_units_per_task``)
      plus the repair-retry allowance (R2.1);
    - Phase 2.6: 0 when the LLM scorecard is disabled (R2.2); otherwise
      slices + reducer (~1 + ceil(0.75 x groups), verified 17 groups -> 13);
    - Phase 3: allowed_slices per group-level doc + 2 calls per per-unit doc.

    The old ``base + groups * per_group`` heuristic under-counted job-dense
    groups ~7x and produced partitions that exhausted their budgets in Phase 2.
    """
    base = config.docgen_call_budget_base if base is None else base
    allowed_slices = max(1, config.docgen_allowed_slices_per_doc)
    units = max(1, unit_count)
    groups = max(1, group_count)
    evidence_agents = 4 if config.docgen_default_evidence_agents_enabled else 0
    phase1 = (
        2 * groups
        if config.agent_phase1_agentic
        else max(groups, math.ceil(units / _PHASE1_UNITS_PER_TASK))
    )
    phase2 = math.ceil(units / max(1, config.phase2_claim_units_per_task))
    phase2_retries = math.ceil(phase2 * max(0.0, config.docgen_phase2_retry_allowance))
    scorecard = (
        1 + math.ceil(groups * 3 / 4) if config.docgen_llm_scorecard_enabled else 0
    )
    group_docs, per_unit_entries = _catalog_doc_counts()
    unit_docs = units * per_unit_entries if config.docgen_unit_docs_enabled else 0
    phase3 = group_docs * allowed_slices + unit_docs * 2
    return (
        base + evidence_agents + phase1 + phase2 + phase2_retries + scorecard + phase3 + 2
    )


def _estimate_call_budget(
    group_count: int,
    *,
    base: int | None = None,
    per_group: int | None = None,
    cap: int | None = None,
    unit_count: int | None = None,
) -> int:
    """Estimate the per-run model-call budget (capped) for one partition.

    Work-plan based when ``unit_count`` is known (the normal path); the legacy
    ``base + groups * per_group`` floor is kept as a lower bound for callers
    that pass explicit legacy knobs.
    """
    base = config.docgen_call_budget_base if base is None else base
    per_group = config.docgen_call_budget_per_group if per_group is None else per_group
    cap = config.docgen_max_run_calls if cap is None else cap
    legacy = base + max(1, group_count) * per_group
    if unit_count is None:
        return min(cap, legacy)
    planned = estimate_partition_calls(unit_count, group_count, base=base)
    return min(cap, max(legacy, planned))


@dataclass(frozen=True)
class Partition:
    """One scoped sub-project derived from a composite scan.

    Two membership modes share this type:

    * top-level partitions (one per composite zip, or the whole project) select
      units by ``source_root`` membership;
    * size-split partitions (an oversized top-level partition subdivided so it
      fits the per-run call budget) additionally carry ``split_groups`` and
      ``parent``; membership is then "``source_root`` in this partition's roots
      AND ``group`` in ``split_groups``". Whole groups never straddle splits, so
      duplicate unit *names* in different groups can never cross-wire.
    """

    name: str
    source_roots: tuple[str, ...]
    unit_names: tuple[str, ...]
    unit_count: int
    groups: tuple[str, ...]
    group_count: int
    call_budget: int
    budget_fits: bool
    # Size-split fields (P3). ``None`` for top-level partitions.
    split_groups: tuple[str, ...] | None = None
    parent: str | None = None


@dataclass(frozen=True)
class PartitionPlan:
    """The full partitioning of a scan into scoped sub-projects."""

    is_composite: bool
    partitions: tuple[Partition, ...]
    cap: int
    total_units: int = 0

    def names(self) -> list[str]:
        return [p.name for p in self.partitions]

    def table(self) -> str:
        """Human-readable dry-run table (sub-project / units / groups / budget)."""
        lines = [
            f"Partition plan (composite={self.is_composite}, cap={self.cap})",
            f"{'sub-project':24s} {'units':>6s} {'groups':>7s} {'budget':>7s} {'fits':>5s}",
        ]
        for part in self.partitions:
            lines.append(
                f"{part.name:24s} {part.unit_count:6d} {part.group_count:7d} "
                f"{part.call_budget:7d} {str(part.budget_fits):>5s}"
            )
        lines.append(f"{'TOTAL':24s} {self.total_units:6d}")
        return "\n".join(lines)


def plan_partitions(
    facts: FactsManifest,
    *,
    base: int | None = None,
    per_group: int | None = None,
    cap: int | None = None,
) -> PartitionPlan:
    """Group ``facts.units`` into deterministic sub-project partitions.

    Non-composite scans (no unit carries :data:`ZIP_MARKER`) yield a single
    :data:`WHOLE_PROJECT_PARTITION` partition == today's behaviour. Composite
    scans yield one partition per zip stem, with un-attributable shared roots
    collected under :data:`SHARED_PARTITION`.
    """
    cap = config.docgen_max_run_calls if cap is None else cap
    base = config.docgen_call_budget_base if base is None else base
    per_group = config.docgen_call_budget_per_group if per_group is None else per_group

    is_composite = any(subproject_key(u.source_root) is not None for u in facts.units)

    buckets: dict[str, list[UnitFact]] = {}
    for unit in facts.units:
        if is_composite:
            key = subproject_key(unit.source_root) or SHARED_PARTITION
        else:
            key = WHOLE_PROJECT_PARTITION
        buckets.setdefault(key, []).append(unit)

    partitions: list[Partition] = []
    for name in sorted(buckets):
        units = buckets[name]
        groups = tuple(sorted({u.group for u in units}))
        roots = tuple(sorted({u.source_root for u in units}))
        budget = _estimate_call_budget(
            len(groups), base=base, per_group=per_group, cap=cap, unit_count=len(units)
        )
        partitions.append(
            Partition(
                name=name,
                source_roots=roots,
                unit_names=tuple(sorted(u.name for u in units)),
                unit_count=len(units),
                groups=groups,
                group_count=len(groups),
                call_budget=budget,
                budget_fits=estimate_partition_calls(len(units), len(groups), base=base) <= cap,
            )
        )

    partitions = _split_oversized_partitions(
        partitions, buckets, base=base, per_group=per_group, cap=cap
    )

    return PartitionPlan(
        is_composite=is_composite,
        partitions=tuple(partitions),
        cap=cap,
        total_units=len(facts.units),
    )


def _split_oversized_partitions(
    partitions: list[Partition],
    buckets: dict[str, list[UnitFact]],
    *,
    base: int,
    per_group: int,
    cap: int,
) -> list[Partition]:
    """Subdivide any partition whose planned work exceeds the call-budget cap.

    A partition needs splitting when its work-plan estimate
    (:func:`estimate_partition_calls` -- Phase 1/2 tasks scale with units, not
    groups) exceeds the cap. Splits are made along whole *groups* (the Phase
    1/2 work unit), packed greedily in alphabetical group order: groups are
    added to the current bin until the bin's estimated calls would exceed the
    cap. A unit's evidence never straddles bins because a unit belongs to
    exactly one group. A single group that alone exceeds the cap cannot be
    subdivided here (Phase 2 already bin-packs jobs within a group) and
    becomes one oversized split with ``budget_fits=False``.
    """
    result: list[Partition] = []
    for part in partitions:
        parent_units = buckets.get(part.name, [])
        units_by_group: dict[str, int] = {}
        for unit in parent_units:
            units_by_group[unit.group] = units_by_group.get(unit.group, 0) + 1
        needs_split = (
            estimate_partition_calls(part.unit_count, part.group_count, base=base) > cap
        )
        if not needs_split or part.group_count <= 1:
            result.append(part)
            continue
        # Greedy pack whole groups until the bin's work-plan estimate hits the cap.
        bins: list[list[str]] = [[]]
        bin_units = [0]
        for group in part.groups:
            group_units = units_by_group.get(group, 0)
            candidate_units = bin_units[-1] + group_units
            candidate_groups = len(bins[-1]) + 1
            if bins[-1] and estimate_partition_calls(
                candidate_units, candidate_groups, base=base
            ) > cap:
                bins.append([])
                bin_units.append(0)
            bins[-1].append(group)
            bin_units[-1] += group_units
        prefix = "" if part.name == WHOLE_PROJECT_PARTITION else f"{part.name}_"
        if len(bins) == 1:
            # One group alone exceeds the cap: keep it whole and flag it.
            result.append(replace(part, budget_fits=False))
            continue
        for index, chunk_list in enumerate(bins):
            chunk = tuple(chunk_list)
            if not chunk:
                continue
            chunk_set = set(chunk)
            chunk_units = [u for u in parent_units if u.group in chunk_set]
            budget = _estimate_call_budget(
                len(chunk), base=base, per_group=per_group, cap=cap,
                unit_count=len(chunk_units),
            )
            result.append(
                replace(
                    part,
                    name=f"{prefix}part{index + 1}",
                    unit_names=tuple(sorted(u.name for u in chunk_units)),
                    unit_count=len(chunk_units),
                    groups=chunk,
                    group_count=len(chunk),
                    call_budget=budget,
                    budget_fits=estimate_partition_calls(
                        len(chunk_units), len(chunk), base=base
                    ) <= cap,
                    split_groups=chunk,
                    parent=part.name,
                )
            )
    return result


def top_level_partitions(plan: PartitionPlan) -> list[Partition]:
    """Fold size-split partitions back into their zip-level parents.

    The cross-project index (the connective L1 layer) is defined at the
    sub-project (zip) level: budget splits of one zip (``ETTM_part1`` ...) are
    an execution detail and must not appear as distinct sub-projects, or their
    intra-zip job calls would surface as false cross-project edges. Splits of
    the same parent share the parent's source roots and split along whole
    groups, so the merged partition covers exactly the parent's unit set.
    """
    merged: dict[str, list[Partition]] = {}
    order: list[str] = []
    for part in plan.partitions:
        key = part.parent or part.name
        if key not in merged:
            order.append(key)
        merged.setdefault(key, []).append(part)
    result: list[Partition] = []
    for key in order:
        parts = merged[key]
        if len(parts) == 1 and parts[0].split_groups is None:
            result.append(parts[0])
            continue
        roots = tuple(sorted({root for p in parts for root in p.source_roots}))
        unit_names = tuple(sorted({name for p in parts for name in p.unit_names}))
        groups = tuple(sorted({g for p in parts for g in p.groups}))
        result.append(
            Partition(
                name=key,
                source_roots=roots,
                unit_names=unit_names,
                unit_count=sum(p.unit_count for p in parts),
                groups=groups,
                group_count=len(groups),
                call_budget=max(p.call_budget for p in parts),
                budget_fits=all(p.budget_fits for p in parts),
            )
        )
    return result


def _partition_source_roots(plan: PartitionPlan, partition_name: str) -> set[str]:
    for part in plan.partitions:
        if part.name == partition_name:
            return set(part.source_roots)
    raise KeyError(f"partition {partition_name!r} not in plan ({plan.names()})")


def filter_manifest(facts: FactsManifest, partition: Partition) -> FactsManifest:
    """Return a scoped ``FactsManifest`` containing only ``partition``'s units.

    Membership is by ``source_root`` (the partition's set of source roots), so no
    unit is dropped, duplicated, or split. ``groups``, ``component_counts`` and
    ``key_systems`` are recomputed from the scoped unit set; the recomputed
    ``key_systems`` pass through :func:`clean_system_keys` so the cross-project
    index / topology markdown never surface junk keys even when the input
    manifest predates unit-level denoising. ``scan_warnings`` and
    ``raw_source_audit`` are project-level context and are carried through
    unchanged (per-partition raw-source auditing is a documented follow-up).
    """
    roots = set(partition.source_roots)
    # WHOLE_PROJECT_PARTITION covers every unit including empty source_roots; a
    # composite partition matches only its own roots. A size-split partition
    # additionally requires group membership (whole groups never straddle
    # splits, so same-named units in different groups cannot cross-wire).
    if partition.split_groups is not None:
        split_groups = set(partition.split_groups)
        parent_is_whole = partition.parent == WHOLE_PROJECT_PARTITION
        units = [
            u for u in facts.units
            if u.group in split_groups and (parent_is_whole or u.source_root in roots)
        ]
    elif partition.name == WHOLE_PROJECT_PARTITION:
        units = list(facts.units)
    else:
        units = [u for u in facts.units if u.source_root in roots]

    groups: dict[str, int] = {}
    component_counts: dict[str, int] = {}
    key_systems: set[str] = set()
    for unit in units:
        groups[unit.group] = groups.get(unit.group, 0) + 1
        for ctype, count in unit.component_types.items():
            component_counts[ctype] = component_counts.get(ctype, 0) + count
        key_systems.update(unit.key_systems)

    return FactsManifest(
        project_id=facts.project_id,
        platform=facts.platform,
        canonical_unit_label=facts.canonical_unit_label,
        unit_count=len(units),
        groups=dict(sorted(groups.items())),
        units=sorted(units, key=lambda u: (u.group, u.name)),
        component_counts=dict(sorted(component_counts.items())),
        key_systems=clean_system_keys(key_systems),
        scan_warnings=list(facts.scan_warnings),
        raw_source_audit=dict(facts.raw_source_audit),
    )


def build_cross_project_index(
    facts: FactsManifest,
    plan: PartitionPlan,
    *,
    scan_manifest: dict[str, Any] | None = None,
    landscape: dict[str, Any] | None = None,
) -> dict:
    """Deterministic, evidence-backed cross-project index for a composite upload.

    Layers (each independently sourced, all derived without any LLM call):

    * ``subprojects``      -- per-partition inventory (units, groups, key systems).
    * ``shared_systems``   -- external key systems referenced by >1 sub-project
      (from the facts manifest's per-unit ``key_systems``).
    * ``call_edges``       -- cross-partition job-call edges from the scanner
      manifest's ``job_calls`` / ``unresolved_job_calls`` graph. The call graph
      is keyed by *display* names, which repeat across zips (each zip carries
      its own copy of the shared REFERENCES logging/monitoring jobs), so callee
      resolution is same-partition-first: a callee name that exists in the
      caller's own partition resolves there (Talend resolves within its own
      project) and produces NO cross edge. Only when the callee name is absent
      from the caller's partition but present in another partition is a cross
      edge emitted -- one per (caller-partition, callee-partition) pair, with
      the resolved canonical unit ids on both ends.
    * ``shared_landscape_systems`` -- external systems that scanner
      ``landscape.json`` edges connect to applications in >1 sub-project
      (mechanism-tagged: db / api / context / ...).

    ``scan_manifest`` and ``landscape`` are the parsed ``scan/manifest.json``
    and ``scan/landscape.json`` payloads; both are optional so callers with
    only a facts manifest still get the inventory + shared-systems layers.
    """
    system_to_partitions: dict[str, set[str]] = {}
    per_partition: dict[str, dict] = {}
    # display name -> {partition -> canonical unit name}
    display_map: dict[str, dict[str, str]] = {}
    root_to_partition: dict[str, str] = {}

    for part in top_level_partitions(plan):
        scoped = filter_manifest(facts, part)
        per_partition[part.name] = {
            "unit_count": scoped.unit_count,
            "group_count": len(scoped.groups),
            "key_systems": scoped.key_systems,
            "source_roots": list(part.source_roots),
        }
        for root in part.source_roots:
            root_to_partition.setdefault(root, part.name)
        for unit in scoped.units:
            display = unit.display_name or unit.name
            display_map.setdefault(display, {})[part.name] = unit.name
        for system in scoped.key_systems:
            system_to_partitions.setdefault(system, set()).add(part.name)

    shared_systems = {
        system: sorted(parts)
        for system, parts in sorted(system_to_partitions.items())
        if len(parts) > 1
    }

    call_edges = _cross_partition_call_edges(scan_manifest or {}, display_map)
    shared_landscape = _shared_landscape_systems(landscape or {}, root_to_partition)
    app_edges = _cross_partition_application_edges(landscape or {}, root_to_partition)

    return {
        "project_id": facts.project_id,
        "platform": facts.platform,
        "is_composite": plan.is_composite,
        "subprojects": per_partition,
        "shared_systems": shared_systems,
        "call_edges": call_edges,
        "application_edges": app_edges,
        "shared_landscape_systems": shared_landscape,
        "notes": (
            "Deterministic index derived from the scan (facts manifest, "
            "scan/manifest.json job-call graph, scan/landscape.json edges). "
            "call_edges lists cross-sub-project job calls with same-partition-"
            "first resolution; shared REFERENCES copies of the same job name "
            "resolve inside their own sub-project and never produce an edge. "
            "shared_systems / shared_landscape_systems list external systems "
            "touched by more than one sub-project."
        ),
    }


def _cross_partition_call_edges(
    scan_manifest: dict[str, Any],
    display_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Cross-partition job-call edges (same-partition-first resolution)."""
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _emit(caller: str, callee: str, resolution: str) -> None:
        caller_parts = display_map.get(caller, {})
        callee_parts = display_map.get(callee, {})
        if not caller_parts or not callee_parts:
            return
        for caller_partition, caller_unit in sorted(caller_parts.items()):
            if caller_partition in callee_parts:
                continue  # resolves within its own sub-project: not a cross edge
            for callee_partition, callee_unit in sorted(callee_parts.items()):
                key = (caller_partition, callee_partition, caller, callee)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({
                    "from_subproject": caller_partition,
                    "to_subproject": callee_partition,
                    "caller": caller,
                    "callee": callee,
                    "caller_unit": caller_unit,
                    "callee_unit": callee_unit,
                    "edge_type": "job_call",
                    "resolution": resolution,
                })

    job_calls = scan_manifest.get("job_calls") or {}
    if isinstance(job_calls, dict):
        for caller, callees in sorted(job_calls.items()):
            for callee in callees or []:
                _emit(str(caller), str(callee), "resolved")

    unresolved = scan_manifest.get("unresolved_job_calls") or {}
    if isinstance(unresolved, dict):
        for caller, entries in sorted(unresolved.items()):
            for entry in entries or []:
                callee = str((entry or {}).get("child_job") or "") if isinstance(entry, dict) else str(entry)
                if callee:
                    _emit(str(caller), callee, "unresolved_in_own_subproject")

    return edges


def _landscape_apps_by_partition(
    landscape: dict[str, Any],
    root_to_partition: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Map landscape application ids to (partition, label)."""
    apps: dict[str, str] = {}
    labels: dict[str, str] = {}
    for app in landscape.get("applications") or []:
        if not isinstance(app, dict) or not app.get("id"):
            continue
        partition = root_to_partition.get(str(app.get("source_root") or ""))
        if partition is None:
            for root in app.get("source_roots") or []:
                partition = root_to_partition.get(str(root))
                if partition is not None:
                    break
        if partition is not None:
            app_id = str(app["id"])
            apps[app_id] = partition
            labels[app_id] = str(app.get("label") or app.get("name") or app_id)
    return apps, labels


def _cross_partition_application_edges(
    landscape: dict[str, Any],
    root_to_partition: dict[str, str],
) -> list[dict[str, Any]]:
    """Direct application-to-application landscape edges across sub-projects."""
    apps, labels = _landscape_apps_by_partition(landscape, root_to_partition)
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for edge in landscape.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        source_part = apps.get(source)
        target_part = apps.get(target)
        if source_part is None or target_part is None or source_part == target_part:
            continue
        key = (source_part, target_part, source, target)
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "from_subproject": source_part,
            "to_subproject": target_part,
            "source_application": labels.get(source, source),
            "target_application": labels.get(target, target),
            "dependency_type": str(edge.get("dependency_type") or ""),
            "mechanism": str(edge.get("mechanism") or ""),
            "direction": str(edge.get("direction") or ""),
            "edge_type": "landscape_application_edge",
        })
    return edges


def _shared_landscape_systems(
    landscape: dict[str, Any],
    root_to_partition: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """External landscape systems connected to applications in >1 sub-project."""
    apps, _labels = _landscape_apps_by_partition(landscape, root_to_partition)

    system_labels: dict[str, str] = {}
    system_kinds: dict[str, str] = {}
    for system in landscape.get("systems") or []:
        if isinstance(system, dict) and system.get("id"):
            system_labels[str(system["id"])] = str(system.get("name") or system.get("label") or system["id"])
            system_kinds[str(system["id"])] = str(system.get("kind") or "")

    touches: dict[str, dict[str, Any]] = {}
    for edge in landscape.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        for app_id, sys_id in ((source, target), (target, source)):
            partition = apps.get(app_id)
            if partition is None or sys_id in apps or sys_id not in system_labels:
                continue
            entry = touches.setdefault(sys_id, {
                "system": system_labels[sys_id],
                "kind": system_kinds.get(sys_id, ""),
                "subprojects": set(),
                "mechanisms": set(),
            })
            entry["subprojects"].add(partition)
            mechanism = str(edge.get("mechanism") or "")
            if mechanism:
                entry["mechanisms"].add(mechanism)

    return {
        sys_id: {
            "system": entry["system"],
            "kind": entry["kind"],
            "subprojects": sorted(entry["subprojects"]),
            "mechanisms": sorted(entry["mechanisms"]),
        }
        for sys_id, entry in sorted(touches.items())
        if len(entry["subprojects"]) > 1
    }


# --------------------------------------------------------------------------- #
# Orchestration path + rendering helpers
# --------------------------------------------------------------------------- #

_SUBDIR_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def partition_output_subdir(partition_name: str) -> str | None:
    """Filesystem-safe output sub-directory for a partition.

    Returns ``None`` for :data:`WHOLE_PROJECT_PARTITION` (single-run projects
    keep today's ``output/{project_id}/docs`` layout unchanged).
    """
    if partition_name == WHOLE_PROJECT_PARTITION:
        return None
    safe = _SUBDIR_SAFE_RE.sub("_", partition_name).strip("._")
    return safe or "subproject"


def upload_topology_dir(project_id: str) -> Path:
    """Upload-level topology directory (outside any per-partition docs tree)."""
    return config.project_root / "output" / project_id / "topology"


def cross_project_index_path(project_id: str) -> Path:
    """Canonical location of the deterministic cross-project index."""
    return upload_topology_dir(project_id) / "cross_project_index.json"


def render_cross_project_refs_md(index: dict, partition_name: str) -> str | None:
    """Render one sub-project's external references as bounded markdown.

    Consumed as engineering-fact context by that sub-project's Phase 2/3 runs:
    the doc writers see WHICH other sub-projects this one calls / is called by /
    shares systems with, without loading any other sub-project's content.
    Returns ``None`` when the partition has no cross-project relationship.
    """
    outbound = [
        e for e in index.get("call_edges", [])
        if e.get("from_subproject") == partition_name
    ]
    inbound = [
        e for e in index.get("call_edges", [])
        if e.get("to_subproject") == partition_name
    ]
    app_out = [
        e for e in index.get("application_edges", [])
        if e.get("from_subproject") == partition_name
    ]
    app_in = [
        e for e in index.get("application_edges", [])
        if e.get("to_subproject") == partition_name
    ]
    shared = {
        system: parts
        for system, parts in (index.get("shared_systems") or {}).items()
        if partition_name in parts
    }
    shared_landscape = {
        sys_id: entry
        for sys_id, entry in (index.get("shared_landscape_systems") or {}).items()
        if partition_name in entry.get("subprojects", [])
    }
    if not any((outbound, inbound, app_out, app_in, shared, shared_landscape)):
        return None

    other = sorted(set(index.get("subprojects", {})) - {partition_name})
    lines: list[str] = [
        "# Cross-Project References\n",
        f"This sub-project (`{partition_name}`) was documented as part of a composite "
        f"upload that also contains: {', '.join(f'`{name}`' for name in other)}.\n",
        "The relationships below are deterministic scanner facts (job-call graph and "
        "landscape edges). Cite them as external references; do NOT invent detail "
        "about other sub-projects' internals -- their own documentation covers that.\n",
    ]

    def _call_table(title: str, edges: list[dict], direction: str) -> None:
        if not edges:
            return
        lines.append(f"\n## {title}\n")
        lines.append("| Caller | Callee | Other sub-project | Resolution |")
        lines.append("|--------|--------|-------------------|------------|")
        for e in edges:
            other_sub = e["to_subproject"] if direction == "out" else e["from_subproject"]
            lines.append(
                f"| {e.get('caller', '?')} | {e.get('callee', '?')} | {other_sub} "
                f"| {e.get('resolution', '')} |"
            )

    _call_table("Jobs here that call jobs in other sub-projects", outbound, "out")
    _call_table("Jobs in other sub-projects that call jobs here", inbound, "in")

    app_edges = app_out + app_in
    if app_edges:
        lines.append("\n## Application-level landscape edges\n")
        lines.append("| From | To | Dependency | Mechanism | Direction |")
        lines.append("|------|----|------------|-----------|-----------|")
        for e in app_edges:
            lines.append(
                f"| {e.get('source_application', '?')} ({e.get('from_subproject')}) "
                f"| {e.get('target_application', '?')} ({e.get('to_subproject')}) "
                f"| {e.get('dependency_type', '')} | {e.get('mechanism', '')} "
                f"| {e.get('direction', '')} |"
            )

    if shared_landscape:
        lines.append("\n## Shared external systems (scanner landscape)\n")
        lines.append("| System | Kind | Mechanisms | Also used by |")
        lines.append("|--------|------|------------|--------------|")
        for entry in shared_landscape.values():
            others = [p for p in entry.get("subprojects", []) if p != partition_name]
            lines.append(
                f"| {entry.get('system', '?')} | {entry.get('kind', '')} "
                f"| {', '.join(entry.get('mechanisms', []))} | {', '.join(others)} |"
            )

    if shared:
        lines.append("\n## Shared key systems (component evidence)\n")
        lines.append("| System | Also used by |")
        lines.append("|--------|--------------|")
        for system, parts in sorted(shared.items()):
            others = [p for p in parts if p != partition_name]
            lines.append(f"| `{system}` | {', '.join(others)} |")

    return "\n".join(lines) + "\n"


def render_cross_project_topology_md(index: dict) -> str:
    """Upload-level deterministic connective map (the L1 business layer seed).

    Renders the cross-project index as human-readable markdown: sub-projects as
    nodes, job-call and landscape edges as connections, shared systems as the
    integration fabric. This document is published at the upload level and
    pushed to RAG so chat queries can answer "how do the products connect".
    """
    subprojects = index.get("subprojects", {})
    lines: list[str] = [
        "# Cross-Project Topology\n",
        f"Composite upload `{index.get('project_id', '?')}` "
        f"({index.get('platform', '?')}) containing {len(subprojects)} sub-projects. "
        "Everything below is derived deterministically from scanner evidence "
        "(job-call graph, landscape edges, component key systems).\n",
        "\n## Sub-projects\n",
        "| Sub-project | Units | Groups | Key systems (top 8) |",
        "|-------------|-------|--------|---------------------|",
    ]
    for name, info in sorted(subprojects.items()):
        systems = ", ".join(info.get("key_systems", [])[:8])
        lines.append(
            f"| {name} | {info.get('unit_count', 0)} | {info.get('group_count', 0)} | {systems} |"
        )

    call_edges = index.get("call_edges", [])
    lines.append("\n## Cross-project job calls\n")
    if call_edges:
        lines.append("| From | To | Caller job | Callee job | Resolution |")
        lines.append("|------|----|------------|------------|------------|")
        for e in call_edges:
            lines.append(
                f"| {e.get('from_subproject')} | {e.get('to_subproject')} "
                f"| {e.get('caller', '?')} | {e.get('callee', '?')} | {e.get('resolution', '')} |"
            )
    else:
        lines.append(
            "No direct cross-sub-project job calls were found in the scanner call "
            "graph. Sub-projects connect through shared external systems below."
        )

    app_edges = index.get("application_edges", [])
    if app_edges:
        lines.append("\n## Application-level edges (scanner landscape)\n")
        lines.append("| From | To | Dependency | Mechanism | Direction |")
        lines.append("|------|----|------------|-----------|-----------|")
        for e in app_edges:
            lines.append(
                f"| {e.get('source_application', '?')} ({e.get('from_subproject')}) "
                f"| {e.get('target_application', '?')} ({e.get('to_subproject')}) "
                f"| {e.get('dependency_type', '')} | {e.get('mechanism', '')} "
                f"| {e.get('direction', '')} |"
            )

    shared_landscape = index.get("shared_landscape_systems", {})
    if shared_landscape:
        lines.append("\n## Shared external systems (integration fabric)\n")
        lines.append("| System | Kind | Mechanisms | Sub-projects |")
        lines.append("|--------|------|------------|--------------|")
        for entry in shared_landscape.values():
            lines.append(
                f"| {entry.get('system', '?')} | {entry.get('kind', '')} "
                f"| {', '.join(entry.get('mechanisms', []))} "
                f"| {', '.join(entry.get('subprojects', []))} |"
            )

    shared = index.get("shared_systems", {})
    if shared:
        lines.append("\n## Shared key systems (component evidence)\n")
        lines.append("| System | Sub-projects |")
        lines.append("|--------|--------------|")
        for system, parts in sorted(shared.items()):
            lines.append(f"| `{system}` | {', '.join(parts)} |")

    lines.append(
        "\n---\nEach sub-project's own documentation lives under its sub-directory "
        "and notes these cross-references from its side.\n"
    )
    return "\n".join(lines)
