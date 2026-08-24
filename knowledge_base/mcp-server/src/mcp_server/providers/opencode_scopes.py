"""Deterministic logical-scope planning for OpenCode documentation runs.

Contracts and adaptive execution batches remain the unit of model work.  A
logical scope is the coarser, durable project boundary used to keep related
groups and units together as project size grows.  This module is deliberately
independent from the OpenCode subprocess adapter so its plan can be created,
serialized, resumed, and tested without starting a model runtime.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.config import config
from mcp_server.providers.custom.models import FactsManifest, UnitFact

if TYPE_CHECKING:
    from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts
    from mcp_server.providers.custom.partition_planner import Partition


SCOPE_PLAN_VERSION = "1"
SCOPES_RELATIVE_PATH = Path("_evidence") / "scopes.json"


class LogicalScope(BaseModel):
    """One cohesive, independently addressable project scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_id: str
    ordinal: int = Field(ge=1)
    partition_name: str
    source_roots: tuple[str, ...] = ()
    groups: tuple[str, ...]
    unit_names: tuple[str, ...]
    group_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    internal_dependency_edges: int = Field(default=0, ge=0)


class LogicalScopePlan(BaseModel):
    """Stable scope plan persisted as ``_evidence/scopes.json``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCOPE_PLAN_VERSION
    project_id: str
    platform: str
    input_digest: str
    max_units_per_scope: int = Field(ge=1)
    max_groups_per_scope: int = Field(ge=1)
    partition_count: int = Field(ge=0)
    group_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    dependency_edges_considered: int = Field(default=0, ge=0)
    scope_count: int = Field(ge=0)
    scopes: tuple[LogicalScope, ...] = ()

    def write_to_staging(self, staging_dir: Path) -> Path:
        """Atomically write a canonical, byte-stable plan beneath staging."""

        path = Path(staging_dir) / SCOPES_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            self.model_dump(mode="json"),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(path)
        return path

    @classmethod
    def load_from_staging(cls, staging_dir: Path) -> "LogicalScopePlan":
        """Load and validate the durable plan for a resumed run."""

        path = Path(staging_dir) / SCOPES_RELATIVE_PATH
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class _PartitionView:
    name: str
    source_roots: tuple[str, ...]
    units: tuple[UnitFact, ...]


@dataclass(frozen=True)
class _Chunk:
    group: str
    unit_names: tuple[str, ...]
    index: int

    @property
    def key(self) -> tuple[str, tuple[str, ...], int]:
        return self.group, self.unit_names, self.index


def plan_logical_scopes(
    facts: FactsManifest,
    *,
    partitions: Sequence["Partition"] | None = None,
    engineering_facts: "EngineeringFacts | None" = None,
    max_units_per_scope: int | None = None,
    max_groups_per_scope: int | None = None,
) -> LogicalScopePlan:
    """Build a deterministic, adaptive logical-scope plan.

    Partitions never mix.  Within a partition, whole groups stay together when
    they fit; oversized groups are split along dependency-connected unit
    components before the count ceiling is applied.  Cross-group dependency
    weight then guides deterministic greedy packing.  Without dependency facts,
    the same algorithm falls back to stable group/name order.
    """

    unit_ceiling = max(
        1,
        int(
            config.opencode_scope_max_units
            if max_units_per_scope is None
            else max_units_per_scope
        ),
    )
    group_ceiling = max(
        1,
        int(
            config.opencode_scope_max_groups
            if max_groups_per_scope is None
            else max_groups_per_scope
        ),
    )
    partition_views = _partition_views(facts, partitions)
    selected_names = {unit.name for view in partition_views for unit in view.units}
    dependency_edges = _dependency_edges(engineering_facts, selected_names)

    planned: list[tuple[_PartitionView, tuple[str, ...], tuple[str, ...], int]] = []
    for view in partition_views:
        view_names = {unit.name for unit in view.units}
        view_edges = tuple(
            edge
            for edge in dependency_edges
            if edge[0] in view_names and edge[1] in view_names
        )
        for groups, units, internal_edges in _plan_partition_scopes(
            view.units,
            view_edges,
            max_units=unit_ceiling,
            max_groups=group_ceiling,
        ):
            planned.append((view, groups, units, internal_edges))

    # Partition planning and each inner packing step are deterministic, but an
    # explicit final order makes durable IDs insensitive to mapping insertion
    # order in scanner or engineering-facts inputs.
    planned.sort(key=lambda item: (item[0].name, item[1], item[2]))
    scopes: list[LogicalScope] = []
    for ordinal, (view, groups, units, internal_edges) in enumerate(planned, start=1):
        scopes.append(
            LogicalScope(
                scope_id=f"scope-{ordinal:03d}-{_slug(view.name)}",
                ordinal=ordinal,
                partition_name=view.name,
                source_roots=view.source_roots,
                groups=groups,
                unit_names=units,
                group_count=len(groups),
                unit_count=len(units),
                internal_dependency_edges=internal_edges,
            )
        )

    unique_units = {
        (view.name, unit.group, unit.name, unit.path, unit.source_root)
        for view in partition_views
        for unit in view.units
    }
    unique_groups = {(view.name, unit.group) for view in partition_views for unit in view.units}
    digest = _input_digest(
        facts,
        partition_views,
        dependency_edges,
        max_units=unit_ceiling,
        max_groups=group_ceiling,
    )
    return LogicalScopePlan(
        project_id=facts.project_id,
        platform=facts.platform,
        input_digest=digest,
        max_units_per_scope=unit_ceiling,
        max_groups_per_scope=group_ceiling,
        partition_count=len(partition_views),
        group_count=len(unique_groups),
        unit_count=len(unique_units),
        dependency_edges_considered=len(dependency_edges),
        scope_count=len(scopes),
        scopes=tuple(scopes),
    )


def ensure_scope_plan(
    staging_dir: Path,
    facts: FactsManifest,
    *,
    partitions: Sequence["Partition"] | None = None,
    engineering_facts: "EngineeringFacts | None" = None,
    max_units_per_scope: int | None = None,
    max_groups_per_scope: int | None = None,
) -> LogicalScopePlan:
    """Resume an identical plan, or atomically replace a stale/corrupt plan."""

    if engineering_facts is None:
        engineering_facts = _load_engineering_facts(Path(staging_dir))
    desired = plan_logical_scopes(
        facts,
        partitions=partitions,
        engineering_facts=engineering_facts,
        max_units_per_scope=max_units_per_scope,
        max_groups_per_scope=max_groups_per_scope,
    )
    path = Path(staging_dir) / SCOPES_RELATIVE_PATH
    if path.is_file():
        try:
            existing = LogicalScopePlan.load_from_staging(staging_dir)
        except (OSError, UnicodeDecodeError, ValueError):
            existing = None
        if existing == desired:
            return existing
    desired.write_to_staging(staging_dir)
    return desired


def _load_engineering_facts(staging_dir: Path) -> "EngineeringFacts | None":
    """Use the deterministic dependency artifact when preprocessing wrote it."""

    path = staging_dir / "engineering_facts.json"
    if not path.is_file():
        return None
    from mcp_server.providers.custom.engineering_facts.models import EngineeringFacts

    try:
        return EngineeringFacts.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _partition_views(
    facts: FactsManifest,
    partitions: Sequence["Partition"] | None,
) -> tuple[_PartitionView, ...]:
    ordered_units = tuple(sorted(facts.units, key=_unit_key))
    if partitions is None:
        return (
            _PartitionView(
                name="project",
                source_roots=tuple(sorted({unit.source_root for unit in ordered_units})),
                units=ordered_units,
            ),
        )

    views: list[_PartitionView] = []
    for partition in sorted(partitions, key=lambda item: item.name):
        allowed_names = set(partition.unit_names)
        roots = set(partition.source_roots)
        split_groups = set(partition.split_groups or ())
        selected = []
        for unit in ordered_units:
            if allowed_names and unit.name not in allowed_names:
                continue
            if roots and unit.source_root not in roots:
                continue
            if split_groups and unit.group not in split_groups:
                continue
            selected.append(unit)
        views.append(
            _PartitionView(
                name=partition.name,
                source_roots=tuple(sorted(roots)),
                units=tuple(selected),
            )
        )
    return tuple(views)


def _dependency_edges(
    engineering_facts: "EngineeringFacts | None",
    selected_unit_names: set[str],
) -> tuple[tuple[str, str], ...]:
    if engineering_facts is None or not selected_unit_names:
        return ()

    folded: dict[str, set[str]] = defaultdict(set)
    for name in selected_unit_names:
        folded[name.casefold()].add(name)

    aliases: dict[str, set[str]] = defaultdict(set)
    for entity in engineering_facts.entities:
        unit_name = entity.unit_name
        if unit_name not in selected_unit_names:
            continue
        aliases[entity.id.casefold()].add(unit_name)
        aliases[entity.name.casefold()].add(unit_name)

    def resolve(value: str) -> str | None:
        if value in selected_unit_names:
            return value
        direct = folded.get(value.casefold(), set())
        if len(direct) == 1:
            return next(iter(direct))
        entity_units = aliases.get(value.casefold(), set())
        if len(entity_units) == 1:
            return next(iter(entity_units))
        return None

    edges: set[tuple[str, str]] = set()
    for dependency in engineering_facts.dependencies:
        source = resolve(dependency.source)
        target = resolve(dependency.target)
        if source is None or target is None or source == target:
            continue
        edges.add(tuple(sorted((source, target))))
    return tuple(sorted(edges))


def _plan_partition_scopes(
    units: Sequence[UnitFact],
    edges: Sequence[tuple[str, str]],
    *,
    max_units: int,
    max_groups: int,
) -> list[tuple[tuple[str, ...], tuple[str, ...], int]]:
    if not units:
        return []
    by_group: dict[str, list[str]] = defaultdict(list)
    for unit in units:
        by_group[unit.group].append(unit.name)

    chunks: list[_Chunk] = []
    for group in sorted(by_group):
        group_chunks = _group_chunks(by_group[group], edges, max_units=max_units)
        chunks.extend(
            _Chunk(group=group, unit_names=chunk, index=index)
            for index, chunk in enumerate(group_chunks, start=1)
        )

    unit_to_chunk: dict[str, int] = {}
    for chunk_index, chunk in enumerate(chunks):
        for unit_name in chunk.unit_names:
            unit_to_chunk[unit_name] = chunk_index

    weights: dict[tuple[int, int], int] = defaultdict(int)
    for source, target in edges:
        left = unit_to_chunk.get(source)
        right = unit_to_chunk.get(target)
        if left is None or right is None or left == right:
            continue
        weights[tuple(sorted((left, right)))] += 1

    def degree(index: int) -> int:
        return sum(weight for pair, weight in weights.items() if index in pair)

    remaining = set(range(len(chunks)))
    packed: list[list[int]] = []
    while remaining:
        seed = min(remaining, key=lambda index: (-degree(index), chunks[index].key))
        current = [seed]
        remaining.remove(seed)
        unit_count = len(chunks[seed].unit_names)
        groups = {chunks[seed].group}

        while remaining:
            eligible = [
                index
                for index in remaining
                if unit_count + len(chunks[index].unit_names) <= max_units
                and len(groups | {chunks[index].group}) <= max_groups
            ]
            if not eligible:
                break

            def candidate_key(index: int) -> tuple[int, int, int, tuple[str, tuple[str, ...], int]]:
                affinity = sum(
                    weights.get(tuple(sorted((index, existing))), 0)
                    for existing in current
                )
                same_group = int(chunks[index].group in groups)
                return -affinity, -same_group, -degree(index), chunks[index].key

            chosen = min(eligible, key=candidate_key)
            current.append(chosen)
            remaining.remove(chosen)
            unit_count += len(chunks[chosen].unit_names)
            groups.add(chunks[chosen].group)
        packed.append(current)

    result: list[tuple[tuple[str, ...], tuple[str, ...], int]] = []
    for packed_scope in packed:
        scope_groups = tuple(sorted({chunks[index].group for index in packed_scope}))
        scope_units = tuple(
            sorted(
                unit
                for index in packed_scope
                for unit in chunks[index].unit_names
            )
        )
        unit_set = set(scope_units)
        internal_edges = sum(
            1 for source, target in edges if source in unit_set and target in unit_set
        )
        result.append((scope_groups, scope_units, internal_edges))
    return result


def _group_chunks(
    unit_names: Iterable[str],
    edges: Sequence[tuple[str, str]],
    *,
    max_units: int,
) -> list[tuple[str, ...]]:
    names = tuple(sorted(set(unit_names)))
    if len(names) <= max_units:
        return [names]

    name_set = set(names)
    adjacency = {name: set() for name in names}
    for source, target in edges:
        if source in name_set and target in name_set:
            adjacency[source].add(target)
            adjacency[target].add(source)

    components: list[tuple[str, ...]] = []
    unseen = set(names)
    while unseen:
        start = min(unseen)
        stack = [start]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            unseen.discard(current)
            stack.extend(sorted(adjacency[current] - component, reverse=True))
        components.append(tuple(sorted(component)))

    pieces: list[tuple[str, ...]] = []
    for component in sorted(components, key=lambda item: (-len(item), item)):
        pieces.extend(
            tuple(component[offset : offset + max_units])
            for offset in range(0, len(component), max_units)
        )

    bins: list[list[str]] = []
    for piece in pieces:
        destination = next(
            (bucket for bucket in bins if len(bucket) + len(piece) <= max_units),
            None,
        )
        if destination is None:
            bins.append(list(piece))
        else:
            destination.extend(piece)
    return sorted((tuple(sorted(bucket)) for bucket in bins), key=lambda item: item)


def _input_digest(
    facts: FactsManifest,
    partitions: Sequence[_PartitionView],
    dependency_edges: Sequence[tuple[str, str]],
    *,
    max_units: int,
    max_groups: int,
) -> str:
    payload = {
        "schema_version": SCOPE_PLAN_VERSION,
        "project_id": facts.project_id,
        "platform": facts.platform,
        "max_units_per_scope": max_units,
        "max_groups_per_scope": max_groups,
        "partitions": [
            {
                "name": partition.name,
                "source_roots": partition.source_roots,
                "units": [
                    {
                        "name": unit.name,
                        "group": unit.group,
                        "path": unit.path,
                        "source_root": unit.source_root,
                    }
                    for unit in partition.units
                ],
            }
            for partition in partitions
        ],
        "dependency_edges": dependency_edges,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unit_key(unit: UnitFact) -> tuple[str, str, str, str]:
    return unit.group, unit.name, unit.path, unit.source_root


def _slug(value: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part) or "project"
