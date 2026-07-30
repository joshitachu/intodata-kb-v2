"""Generic selection and bounded graph traversal for source/evidence snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping

from mcp_server.experiments.domain.evidence import Relationship


@dataclass(frozen=True)
class ScopeBudgets:
    max_source_items: int = 1_000
    max_evidence_items: int = 10_000
    max_bytes: int = 100 * 1024 * 1024
    max_input_tokens: int = 1_000_000
    max_tool_calls: int = 100
    max_elapsed_seconds: int = 3_600

    def __post_init__(self) -> None:
        if any(value < 1 for value in (
            self.max_source_items, self.max_evidence_items, self.max_bytes,
            self.max_input_tokens, self.max_tool_calls, self.max_elapsed_seconds,
        )):
            raise ValueError("scope budgets must be positive")


@dataclass(frozen=True)
class ScopePredicate:
    field: str
    operator: str
    value: object

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("scope predicate field must not be blank")
        if self.operator not in {"equals", "in", "contains"}:
            raise ValueError("unsupported generic scope predicate operator")


@dataclass(frozen=True)
class TraversalSpec:
    direction: str = "outbound"
    max_depth: int = 0
    relationship_predicates: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.direction not in {"outbound", "inbound", "both"} or self.max_depth < 0:
            raise ValueError("invalid generic traversal specification")


@dataclass(frozen=True)
class ScopeSpec:
    include: tuple[ScopePredicate, ...] = ()
    exclude: tuple[ScopePredicate, ...] = ()
    traversal: TraversalSpec = field(default_factory=TraversalSpec)
    start_at: datetime | None = None
    end_at: datetime | None = None
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    optional_capabilities: frozenset[str] = field(default_factory=frozenset)
    requested_output_ids: tuple[str, ...] = ()
    budgets: ScopeBudgets = field(default_factory=ScopeBudgets)
    unknown_policy: str = "disclose"

    def __post_init__(self) -> None:
        if self.start_at is not None and self.end_at is not None and self.start_at > self.end_at:
            raise ValueError("scope time range must be ordered")
        output_ids = tuple(output_id.strip() for output_id in self.requested_output_ids)
        if any(not output_id for output_id in output_ids) or len(output_ids) != len(set(output_ids)):
            raise ValueError("requested output ids must be non-blank and unique")
        if self.required_capabilities & self.optional_capabilities:
            raise ValueError("required and optional capabilities must not overlap")
        if self.unknown_policy not in {"disclose", "exclude", "fail"}:
            raise ValueError("unsupported scope unknown_policy")


def matches_scope(record: Mapping[str, object], scope: ScopeSpec) -> bool:
    capabilities = set(record.get("capabilities", ()))
    if not scope.required_capabilities.issubset(capabilities):
        return False
    timestamp = record.get("timestamp")
    if scope.start_at is not None or scope.end_at is not None:
        if not isinstance(timestamp, datetime):
            return False
        if scope.start_at is not None and timestamp < scope.start_at:
            return False
        if scope.end_at is not None and timestamp > scope.end_at:
            return False
    if scope.include and not all(_matches(record, predicate) for predicate in scope.include):
        return False
    return not any(_matches(record, predicate) for predicate in scope.exclude)


def select_records(records: Iterable[Mapping[str, object]], scope: ScopeSpec) -> tuple[Mapping[str, object], ...]:
    selected = tuple(record for record in records if matches_scope(record, scope))
    if len(selected) > scope.budgets.max_source_items:
        raise ValueError("scope source-item budget exceeded")
    return selected


def traverse_relationships(
    *, start_subject_ids: Iterable[str], relationships: Iterable[Relationship], spec: TraversalSpec
) -> frozenset[str]:
    """Traverse a generic relationship graph with cycle-safe depth bounds."""

    seen = set(start_subject_ids)
    frontier = set(seen)
    edges = tuple(relationships)
    for _ in range(spec.max_depth):
        next_frontier: set[str] = set()
        for edge in edges:
            if spec.relationship_predicates and edge.predicate not in spec.relationship_predicates:
                continue
            if spec.direction in {"outbound", "both"} and edge.source_subject_id in frontier:
                next_frontier.add(edge.target_subject_id)
            if spec.direction in {"inbound", "both"} and edge.target_subject_id in frontier:
                next_frontier.add(edge.source_subject_id)
        next_frontier.difference_update(seen)
        if not next_frontier:
            break
        seen.update(next_frontier)
        frontier = next_frontier
    return frozenset(seen)


def _matches(record: Mapping[str, object], predicate: ScopePredicate) -> bool:
    value: object = record
    for part in predicate.field.split("."):
        if not isinstance(value, Mapping):
            return False
        value = value.get(part)
    if predicate.operator == "equals":
        return value == predicate.value
    if predicate.operator == "in":
        return value in predicate.value if isinstance(predicate.value, (set, frozenset, tuple, list)) else False
    if predicate.operator == "contains":
        return predicate.value in value if isinstance(value, (set, frozenset, tuple, list, str)) else False
    raise ValueError(f"unsupported generic scope predicate operator: {predicate.operator}")
