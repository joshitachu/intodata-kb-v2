"""Generic DAG timing aggregation with explicit unknown-state handling."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from mcp_server.experiments.domain.evaluation import TimingMetrics


@dataclass(frozen=True, slots=True)
class TaskTimingObservation:
    node_id: str
    dependency_node_ids: tuple[str, ...] = ()
    started_ms: int | None = None
    completed_ms: int | None = None
    provider_latency_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise ValueError("node_id must not be blank")
        for value, field_name in (
            (self.started_ms, "started_ms"),
            (self.completed_ms, "completed_ms"),
            (self.provider_latency_ms, "provider_latency_ms"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.started_ms is not None and self.completed_ms is not None:
            if self.completed_ms < self.started_ms:
                raise ValueError("completed_ms cannot precede started_ms")

    @property
    def duration_ms(self) -> int | None:
        if self.started_ms is None or self.completed_ms is None:
            return None
        return self.completed_ms - self.started_ms


def _nearest_rank(values: list[int], percentile: Decimal) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(1, ceil(float(percentile * len(ordered))))
    return ordered[position - 1]


def _critical_path(observations: tuple[TaskTimingObservation, ...]) -> tuple[int | None, tuple[str, ...]]:
    if not observations or any(item.duration_ms is None for item in observations):
        return None, ()
    by_id = {item.node_id: item for item in observations}
    if len(by_id) != len(observations):
        raise ValueError("task timing observations must have unique node IDs")
    for item in observations:
        unknown = set(item.dependency_node_ids) - set(by_id)
        if unknown:
            raise ValueError(f"unknown timing dependency: {sorted(unknown)[0]}")

    visiting: set[str] = set()
    resolved: dict[str, tuple[int, tuple[str, ...]]] = {}

    def visit(node_id: str) -> tuple[int, tuple[str, ...]]:
        if node_id in resolved:
            return resolved[node_id]
        if node_id in visiting:
            raise ValueError("timing dependency graph contains a cycle")
        visiting.add(node_id)
        item = by_id[node_id]
        predecessors = [visit(parent) for parent in sorted(item.dependency_node_ids)]
        predecessor = max(predecessors, key=lambda item: (item[0], item[1]), default=(0, ()))
        assert item.duration_ms is not None
        result = (predecessor[0] + item.duration_ms, predecessor[1] + (node_id,))
        visiting.remove(node_id)
        resolved[node_id] = result
        return result

    return max((visit(node_id) for node_id in sorted(by_id)), key=lambda item: (item[0], item[1]))


def aggregate_timing(
    observations: Iterable[TaskTimingObservation],
    *,
    experiment_wall_ms: int | None = None,
    queue_ms: int | None = None,
    snapshot_ms: int | None = None,
    validation_ms: int | None = None,
) -> TimingMetrics:
    """Derive measurable timing only when no required observation is unknown."""
    tasks = tuple(observations)
    if len({item.node_id for item in tasks}) != len(tasks):
        raise ValueError("task timing observations must have unique node IDs")
    starts = [item.started_ms for item in tasks]
    finishes = [item.completed_ms for item in tasks]
    generation_wall_ms = None
    if tasks and all(value is not None for value in starts + finishes):
        generation_wall_ms = max(finishes) - min(starts)  # type: ignore[arg-type]

    latencies = [item.provider_latency_ms for item in tasks]
    provider_latencies = None
    if tasks and all(value is not None for value in latencies):
        provider_latencies = [int(value) for value in latencies]
    critical_path_ms, critical_path_task_ids = _critical_path(tasks)
    return TimingMetrics(
        experiment_wall_ms=experiment_wall_ms,
        generation_wall_ms=generation_wall_ms,
        queue_ms=queue_ms,
        snapshot_ms=snapshot_ms,
        validation_ms=validation_ms,
        provider_latency_sum_ms=None if provider_latencies is None else sum(provider_latencies),
        provider_latency_p50_ms=None
        if provider_latencies is None
        else _nearest_rank(provider_latencies, Decimal("0.50")),
        provider_latency_p95_ms=None
        if provider_latencies is None
        else _nearest_rank(provider_latencies, Decimal("0.95")),
        critical_path_ms=critical_path_ms,
        critical_path_task_ids=critical_path_task_ids,
    )


def parallelism_ratio(metrics: TimingMetrics) -> Decimal | None:
    """Provider-latency sum divided by generation wall time; zero is incomparable."""
    if metrics.provider_latency_sum_ms is None or metrics.generation_wall_ms in (None, 0):
        return None
    return Decimal(metrics.provider_latency_sum_ms) / Decimal(metrics.generation_wall_ms)
