"""Deterministic evidence-batch planner for the Grounded experiments pipeline.

The Grounded documentation recipe used to hard-code exactly two analysis
workers with named output files. Pointed at a large project (e.g. werkorder:
429 units across 61 groups) that meant only the two named groups were ever
analysed and the other 59 groups were silently never scheduled.

This module removes that ceiling. Given the frozen canonical units, the
FACT_LEDGER call graph, and connection-usage edges, it partitions **every**
unit into evidence-sized batches under these hard constraints:

- **A unit is atomic** -- a job/route is never split across two batches.
- **Cut along coupling seams** -- a caller and its callees stay together; units
  that share a connection are pulled together; the group is the default seam.
  Concretely, the planner builds one coupling cluster per connected component
  of ``call-edge | shared-connection | same-group`` and keeps each whole
  cluster in a single batch whenever the byte budget allows, so no coupling
  edge is cut unless a single cluster is itself larger than one batch.
- **Size by evidence bytes** -- batches are bin-packed by the real per-unit
  verbatim-evidence byte size (the Slice-2 bundle size), not a fixed
  group/unit count. A 61-group project fans out to ~10-12 batches; a 2-group
  project collapses to one.
- **Coverage is a partition** -- :meth:`BatchPlan.assert_covers` fails the run
  with a clear error if the batches do not cover exactly the canonical units.
- **Boundary manifest per batch** -- every batch records the call edges and
  shared connections that cross its boundary plus a one-line summary of each
  external neighbour, so no analysis worker is blind to its dependencies.

The planner is intentionally standalone (primitive dataclasses in, plan out),
so the Slice-4 writer/doc fan-out can reuse it for the same partitioning. It
holds no reference to the staging layout, the runtime, or ``providers.custom``.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any


class BatchCoverageError(ValueError):
    """Raised when planned batches are not an exact partition of the units."""


@dataclass(frozen=True, slots=True)
class UnitFact:
    """One canonical unit as the planner needs to see it.

    ``copy_count`` / ``copy_source_paths`` carry the duplicate-copy multiplicity
    the planner records when several physical canonical units share a name (see
    :class:`UnitMultiplicity`). They default to a single copy so a normal
    unique-named unit is unaffected.
    """

    name: str
    group: str
    evidence_bytes: int
    summary: str = ""
    # Physical source location of THIS copy (``project_source_path`` in the
    # ledger). Used only to record where duplicate-named copies live.
    source_path: str = ""
    # Number of physical canonical copies that share this unit's name (1 = a
    # normal unique unit). The extra copies' source paths are in
    # ``copy_source_paths``; the kept copy's own path stays in ``source_path``.
    copy_count: int = 1
    copy_source_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UnitMultiplicity:
    """A canonical unit name deployed as more than one physical copy.

    Duplicate-named canonical units are legitimate: the same logging/monitoring
    route (e.g. ``MessageMonitoring``) is copied into every Talend export root's
    ``REFERENCES/`` directory. Such copies share BOTH name and group; only their
    ``project_source_path`` differs, so neither ``name`` nor ``(group, name)`` is
    unique across the raw canonical-unit list. The planner keeps exactly ONE unit
    per name (first-wins), matching the evidence/doc/mapping side which already
    keys on unique name, and records the multiplicity here so the duplication is
    surfaced -- never silently discarded.

    ``copy_count`` is the total number of physical copies (>= 2 here);
    ``copy_source_paths`` lists the source paths of the EXTRA copies (all copies
    except the kept first-wins one).
    """

    name: str
    copy_count: int
    copy_source_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "copy_count": self.copy_count,
            "copy_source_paths": list(self.copy_source_paths),
        }


@dataclass(frozen=True, slots=True)
class NeighborRef:
    """A unit in another batch that the owning batch depends on."""

    unit: str
    batch_id: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {"unit": self.unit, "batch_id": self.batch_id, "summary": self.summary}


@dataclass(frozen=True, slots=True)
class CrossBatchCallEdge:
    """A caller->callee edge whose endpoints live in different batches."""

    caller: str
    callee: str
    caller_batch: str
    callee_batch: str
    direction: str  # "outbound" (this batch calls out) | "inbound" (called in)

    def to_dict(self) -> dict[str, str]:
        return {
            "caller": self.caller,
            "callee": self.callee,
            "caller_batch": self.caller_batch,
            "callee_batch": self.callee_batch,
            "direction": self.direction,
        }


@dataclass(frozen=True, slots=True)
class SharedConnectionRef:
    """A connection used by this batch and by units in other batches."""

    connection: str
    external_units: tuple[NeighborRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "connection": self.connection,
            "external_units": [ref.to_dict() for ref in self.external_units],
        }


@dataclass(frozen=True, slots=True)
class BoundaryManifest:
    """Everything a batch must know about its cross-batch dependencies."""

    batch_id: str
    cross_batch_call_edges: tuple[CrossBatchCallEdge, ...]
    shared_connections: tuple[SharedConnectionRef, ...]
    neighbor_summaries: tuple[NeighborRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "description": (
                "Cross-batch dependency manifest for this Phase 2 analysis batch. "
                "The units listed here are analysed by OTHER batches; they are "
                "present in the project even though their verbatim evidence is not "
                "staged in this packet. Use it to reason about callers/callees and "
                "shared connections that cross this batch boundary; never claim a "
                "listed neighbour is absent from the project."
            ),
            "batch_id": self.batch_id,
            "cross_batch_call_edges": [
                edge.to_dict() for edge in self.cross_batch_call_edges
            ],
            "shared_connections": [
                shared.to_dict() for shared in self.shared_connections
            ],
            "neighbor_summaries": [ref.to_dict() for ref in self.neighbor_summaries],
        }


@dataclass(frozen=True, slots=True)
class AnalysisBatch:
    """One analysis batch: a set of whole units plus its boundary manifest."""

    batch_id: str
    unit_names: tuple[str, ...]
    total_evidence_bytes: int
    boundary_manifest: BoundaryManifest


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """The full partition of canonical units into analysis batches.

    ``duplicates`` records every unit name that had more than one physical
    canonical copy (deduped first-wins during planning). It is the audit trail
    that makes the dedup a surfaced fact, not a silent drop, and lets the
    evidence/doc side note "this unit is deployed as N copies across roots".
    """

    batches: tuple[AnalysisBatch, ...]
    max_batch_bytes: int
    duplicates: tuple[UnitMultiplicity, ...] = ()

    def duplicate_index(self) -> dict[str, UnitMultiplicity]:
        """Map each duplicated unit name to its multiplicity record."""

        return {dup.name: dup for dup in self.duplicates}

    def duplicate_summary(self) -> str | None:
        """One-line human summary of the dedup, or ``None`` if nothing deduped."""

        if not self.duplicates:
            return None
        duplicated_names = len(self.duplicates)
        extra_copies = sum(dup.copy_count - 1 for dup in self.duplicates)
        unique_units = sum(len(batch.unit_names) for batch in self.batches)
        return (
            f"{duplicated_names} unit name(s) duplicated across export roots "
            f"({extra_copies} extra physical copies); deduped first-wins to "
            f"{unique_units} unique unit(s) for planning/coverage/evidence."
        )

    def assert_covers(self, expected_unit_names: Sequence[str]) -> None:
        """Fail unless the batches are an exact partition of ``expected``.

        This is the coverage gate: it raises :class:`BatchCoverageError` when a
        unit is assigned to more than one batch, when a canonical unit is
        unaccounted for, or when a batch invents a unit that is not canonical.
        """

        expected = set(expected_unit_names)
        if len(expected) != len(expected_unit_names):
            raise BatchCoverageError("expected unit names contain duplicates")
        assigned: list[str] = [
            unit for batch in self.batches for unit in batch.unit_names
        ]
        assigned_set = set(assigned)
        if len(assigned) != len(assigned_set):
            duplicates = sorted({name for name in assigned if assigned.count(name) > 1})
            raise BatchCoverageError(
                "unit assigned to more than one batch: " + ", ".join(duplicates)
            )
        missing = sorted(expected - assigned_set)
        extra = sorted(assigned_set - expected)
        if missing or extra:
            raise BatchCoverageError(
                "analysis batches are not an exact partition of the canonical units "
                f"(unassigned={missing}; not-canonical={extra})"
            )

    def assert_production_covered(self, production_unit_names: Sequence[str]) -> None:
        """Fail unless every production unit is assigned to some batch.

        This is the Tier-A per-unit documentation coverage gate. Because a
        per-unit doc writer fans out over exactly these batches (one writer per
        batch, owning one deep document per unit in the batch), a production
        unit that no batch covers would get NO deep document. That is a hard
        failure -- no "representative examples" -- so it raises
        :class:`BatchCoverageError` rather than silently under-documenting a real
        job. Test/scratch units are intentionally allowed to be absent here;
        they receive lighter treatment, not a required deep doc.
        """

        assigned = {unit for batch in self.batches for unit in batch.unit_names}
        missing = sorted(set(production_unit_names) - assigned)
        if missing:
            raise BatchCoverageError(
                "production units have no deep per-unit document (uncovered by any "
                f"batch): {missing}"
            )


class AnalysisBatchPlanner:
    """Deterministic graph-partitioning bin-packer over the call/connection graph."""

    def __init__(self, *, max_batch_bytes: int) -> None:
        if max_batch_bytes < 1:
            raise ValueError("analysis batch byte budget must be positive")
        self.max_batch_bytes = max_batch_bytes

    # -- public API --------------------------------------------------------

    def plan(
        self,
        units: Sequence[UnitFact],
        *,
        call_edges: Sequence[tuple[str, Sequence[str]]] = (),
        connection_usage: Sequence[tuple[str, Sequence[str]]] = (),
    ) -> BatchPlan:
        """Partition ``units`` into evidence-sized, coupling-aware batches."""

        index: dict[str, UnitFact] = {}
        occurrence_counts: dict[str, int] = {}
        extra_paths: dict[str, list[str]] = {}
        for unit in units:
            name = unit.name.strip()
            if not name:
                raise ValueError("analysis batch planner requires named units")
            if name not in index:
                index[name] = unit
                occurrence_counts[name] = 1
                continue
            # A legitimate duplicate name -- the same logging/monitoring route is
            # copied into several export roots, sharing name AND group. Keep the
            # first copy (first-wins, matching the evidence/doc/mapping side which
            # keys on unique name) and record the extra copy so the multiplicity
            # is surfaced on the plan, never silently discarded.
            occurrence_counts[name] += 1
            if unit.source_path:
                extra_paths.setdefault(name, []).append(unit.source_path)
        if not index:
            return BatchPlan(batches=(), max_batch_bytes=self.max_batch_bytes)

        duplicates = tuple(
            UnitMultiplicity(
                name=name,
                copy_count=count,
                copy_source_paths=tuple(sorted(extra_paths.get(name, ()))),
            )
            for name, count in sorted(occurrence_counts.items())
            if count > 1
        )
        # Annotate the kept fact so per-unit consumers that read the UnitFact
        # (not just the plan) also see the multiplicity.
        for dup in duplicates:
            index[dup.name] = replace(
                index[dup.name],
                copy_count=dup.copy_count,
                copy_source_paths=dup.copy_source_paths,
            )

        call_edges = self._filter_call_edges(call_edges, index)
        connection_usage = self._filter_connection_usage(connection_usage, index)

        clusters = self._coupling_clusters(index, call_edges, connection_usage)
        bins = self._pack(clusters, index, call_edges, connection_usage)

        # Deterministic batch ordering + identity: by the sorted first unit name.
        bins.sort(key=lambda members: members[0])
        unit_to_batch: dict[str, str] = {}
        batch_units: list[tuple[str, tuple[str, ...]]] = []
        for position, members in enumerate(bins, start=1):
            batch_id = f"analysis_batch_{position:03d}"
            batch_units.append((batch_id, tuple(members)))
            for name in members:
                unit_to_batch[name] = batch_id

        batches = tuple(
            AnalysisBatch(
                batch_id=batch_id,
                unit_names=members,
                total_evidence_bytes=sum(index[name].evidence_bytes for name in members),
                boundary_manifest=self._boundary_manifest(
                    batch_id,
                    members,
                    index,
                    call_edges,
                    connection_usage,
                    unit_to_batch,
                ),
            )
            for batch_id, members in batch_units
        )
        return BatchPlan(
            batches=batches,
            max_batch_bytes=self.max_batch_bytes,
            duplicates=duplicates,
        )

    @classmethod
    def from_fact_ledger(
        cls,
        ledger: Mapping[str, Any],
        *,
        evidence_bytes: Mapping[str, int],
        max_batch_bytes: int,
    ) -> tuple["AnalysisBatchPlanner", BatchPlan]:
        """Adapt a compiled FACT_LEDGER + per-unit evidence sizes into a plan.

        Returns the planner and the plan so callers can reuse the byte budget.
        Units with no measured evidence size default to zero bytes (they still
        get scheduled -- a unit is never dropped for lack of evidence).
        """

        units = tuple(
            UnitFact(
                name=str(item["name"]),
                group=str(item.get("group") or "ungrouped"),
                evidence_bytes=int(evidence_bytes.get(str(item["name"]), 0)),
                summary=_unit_summary(item),
                source_path=str(
                    item.get("project_source_path") or item.get("source") or ""
                ),
            )
            for item in ledger.get("canonical_units", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        call_edges = tuple(
            (str(edge["caller"]), tuple(str(c) for c in edge.get("callees", []) if isinstance(c, str)))
            for edge in ledger.get("call_graph", [])
            if isinstance(edge, dict) and str(edge.get("caller") or "").strip()
        )
        connection_usage = tuple(
            (str(conn["name"]), tuple(str(u) for u in conn.get("used_by", []) if isinstance(u, str)))
            for conn in ledger.get("connections", [])
            if isinstance(conn, dict) and str(conn.get("name") or "").strip()
        )
        planner = cls(max_batch_bytes=max_batch_bytes)
        plan = planner.plan(
            units,
            call_edges=call_edges,
            connection_usage=connection_usage,
        )
        return planner, plan

    # -- coupling clusters (union-find) ------------------------------------

    def _coupling_clusters(
        self,
        index: Mapping[str, UnitFact],
        call_edges: Sequence[tuple[str, tuple[str, ...]]],
        connection_usage: Sequence[tuple[str, tuple[str, ...]]],
    ) -> list[list[str]]:
        parent = {name: name for name in index}

        def find(node: str) -> str:
            root = node
            while parent[root] != root:
                root = parent[root]
            while parent[node] != root:
                parent[node], node = root, parent[node]
            return root

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            # Deterministic root: smaller name wins.
            if rb < ra:
                ra, rb = rb, ra
            parent[rb] = ra

        # Group is the default seam.
        by_group: dict[str, list[str]] = defaultdict(list)
        for name, unit in index.items():
            by_group[unit.group].append(name)
        for members in by_group.values():
            for other in members[1:]:
                union(members[0], other)
        # Caller/callee coupling.
        for caller, callees in call_edges:
            for callee in callees:
                union(caller, callee)
        # Shared-connection coupling.
        for _connection, used_by in connection_usage:
            for other in used_by[1:]:
                union(used_by[0], other)

        clusters: dict[str, list[str]] = defaultdict(list)
        for name in index:
            clusters[find(name)].append(name)
        return [sorted(members) for members in clusters.values()]

    # -- bin packing -------------------------------------------------------

    def _pack(
        self,
        clusters: list[list[str]],
        index: Mapping[str, UnitFact],
        call_edges: Sequence[tuple[str, tuple[str, ...]]],
        connection_usage: Sequence[tuple[str, tuple[str, ...]]],
    ) -> list[list[str]]:
        def cluster_bytes(members: Sequence[str]) -> int:
            return sum(index[name].evidence_bytes for name in members)

        # First-fit-decreasing: largest clusters first so small clusters fill gaps.
        clusters = sorted(clusters, key=lambda members: (-cluster_bytes(members), members[0]))
        bins: list[list[str]] = []
        bin_bytes: list[int] = []
        for members in clusters:
            total = cluster_bytes(members)
            if total <= self.max_batch_bytes:
                placed = False
                for position, current in enumerate(bin_bytes):
                    if current + total <= self.max_batch_bytes:
                        bins[position].extend(members)
                        bin_bytes[position] += total
                        placed = True
                        break
                if not placed:
                    bins.append(list(members))
                    bin_bytes.append(total)
                continue
            # A single coupling cluster exceeds one batch: it must span several
            # batches. Split it into byte-sized chunks of WHOLE units, ordered so
            # coupled units stay adjacent, so the fewest coupling edges are cut.
            for chunk in self._split_cluster(members, index, call_edges, connection_usage):
                bins.append(list(chunk))
                bin_bytes.append(cluster_bytes(chunk))
        # Sort each batch's members for stable identity/output.
        return [sorted(members) for members in bins]

    def _split_cluster(
        self,
        members: Sequence[str],
        index: Mapping[str, UnitFact],
        call_edges: Sequence[tuple[str, tuple[str, ...]]],
        connection_usage: Sequence[tuple[str, tuple[str, ...]]],
    ) -> list[list[str]]:
        member_set = set(members)
        adjacency: dict[str, set[str]] = {name: set() for name in members}
        for caller, callees in call_edges:
            if caller not in member_set:
                continue
            for callee in callees:
                if callee in member_set:
                    adjacency[caller].add(callee)
                    adjacency[callee].add(caller)
        for _connection, used_by in connection_usage:
            present = [name for name in used_by if name in member_set]
            for first in present:
                for second in present:
                    if first != second:
                        adjacency[first].add(second)
        # Deterministic BFS keeps coupled units next to each other in the order.
        ordered: list[str] = []
        visited: set[str] = set()
        for start in sorted(members):
            if start in visited:
                continue
            queue: deque[str] = deque([start])
            visited.add(start)
            while queue:
                current = queue.popleft()
                ordered.append(current)
                for neighbour in sorted(adjacency[current]):
                    if neighbour not in visited:
                        visited.add(neighbour)
                        queue.append(neighbour)
        chunks: list[list[str]] = []
        current_chunk: list[str] = []
        current_bytes = 0
        for name in ordered:
            unit_bytes = index[name].evidence_bytes
            if current_chunk and current_bytes + unit_bytes > self.max_batch_bytes:
                chunks.append(current_chunk)
                current_chunk = []
                current_bytes = 0
            # A single unit larger than the budget still ships whole in its own
            # chunk -- a unit is never split.
            current_chunk.append(name)
            current_bytes += unit_bytes
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    # -- boundary manifest -------------------------------------------------

    def _boundary_manifest(
        self,
        batch_id: str,
        members: tuple[str, ...],
        index: Mapping[str, UnitFact],
        call_edges: Sequence[tuple[str, tuple[str, ...]]],
        connection_usage: Sequence[tuple[str, tuple[str, ...]]],
        unit_to_batch: Mapping[str, str],
    ) -> BoundaryManifest:
        member_set = set(members)
        neighbours: dict[str, NeighborRef] = {}

        def neighbour_ref(name: str) -> NeighborRef:
            ref = neighbours.get(name)
            if ref is None:
                ref = NeighborRef(
                    unit=name,
                    batch_id=unit_to_batch[name],
                    summary=index[name].summary,
                )
                neighbours[name] = ref
            return ref

        cross_edges: list[CrossBatchCallEdge] = []
        for caller, callees in call_edges:
            for callee in callees:
                caller_in = caller in member_set
                callee_in = callee in member_set
                if caller_in == callee_in:
                    continue
                if caller_in:
                    neighbour_ref(callee)
                    direction = "outbound"
                else:
                    neighbour_ref(caller)
                    direction = "inbound"
                cross_edges.append(
                    CrossBatchCallEdge(
                        caller=caller,
                        callee=callee,
                        caller_batch=unit_to_batch[caller],
                        callee_batch=unit_to_batch[callee],
                        direction=direction,
                    )
                )

        shared: list[SharedConnectionRef] = []
        for connection, used_by in connection_usage:
            present = set(used_by)
            if not (present & member_set):
                continue
            external = sorted(present - member_set)
            if not external:
                continue
            shared.append(
                SharedConnectionRef(
                    connection=connection,
                    external_units=tuple(neighbour_ref(name) for name in external),
                )
            )

        return BoundaryManifest(
            batch_id=batch_id,
            cross_batch_call_edges=tuple(
                sorted(
                    cross_edges,
                    key=lambda edge: (edge.direction, edge.caller, edge.callee),
                )
            ),
            shared_connections=tuple(
                sorted(shared, key=lambda item: item.connection)
            ),
            neighbor_summaries=tuple(
                neighbours[name] for name in sorted(neighbours)
            ),
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _filter_call_edges(
        call_edges: Sequence[tuple[str, Sequence[str]]],
        index: Mapping[str, UnitFact],
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        filtered: list[tuple[str, tuple[str, ...]]] = []
        for caller, callees in call_edges:
            if caller not in index:
                continue
            kept = tuple(callee for callee in callees if callee in index and callee != caller)
            if kept:
                filtered.append((caller, kept))
        return tuple(filtered)

    @staticmethod
    def _filter_connection_usage(
        connection_usage: Sequence[tuple[str, Sequence[str]]],
        index: Mapping[str, UnitFact],
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        filtered: list[tuple[str, tuple[str, ...]]] = []
        for connection, used_by in connection_usage:
            # Preserve first-seen order while removing units absent from the scan.
            kept: list[str] = []
            seen: set[str] = set()
            for name in used_by:
                if name in index and name not in seen:
                    seen.add(name)
                    kept.append(name)
            if len(kept) >= 2:
                filtered.append((connection, tuple(kept)))
        return tuple(filtered)


def _unit_summary(unit: Mapping[str, Any]) -> str:
    """One-line neighbour summary drawn from deterministic ledger facts."""

    name = str(unit.get("name") or "")
    kind = str(unit.get("kind") or "unit")
    group = str(unit.get("group") or "ungrouped")
    components = unit.get("component_count")
    parts = [f"{name} ({kind}, group '{group}'"]
    if isinstance(components, int) and not isinstance(components, bool):
        parts.append(f", {components} components")
    return "".join(parts) + ")"
