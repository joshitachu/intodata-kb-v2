from __future__ import annotations

from decimal import Decimal

import pytest

from mcp_server.experiments.infrastructure.validation.timing import (
    TaskTimingObservation,
    aggregate_timing,
    parallelism_ratio,
)


def test_parallel_dag_uses_longest_dependency_path_and_nearest_rank_percentiles() -> None:
    metrics = aggregate_timing(
        (
            TaskTimingObservation("a", started_ms=0, completed_ms=10, provider_latency_ms=10),
            TaskTimingObservation("b", started_ms=0, completed_ms=20, provider_latency_ms=20),
            TaskTimingObservation("c", ("a", "b"), started_ms=20, completed_ms=50, provider_latency_ms=30),
        )
    )

    assert metrics.generation_wall_ms == 50
    assert metrics.critical_path_ms == 50
    assert metrics.critical_path_task_ids == ("b", "c")
    assert metrics.provider_latency_sum_ms == 60
    assert metrics.provider_latency_p50_ms == 20
    assert metrics.provider_latency_p95_ms == 30
    assert parallelism_ratio(metrics) == Decimal("1.2")


def test_unknown_timing_and_zero_wall_are_incomparable() -> None:
    unknown = aggregate_timing((TaskTimingObservation("a", provider_latency_ms=None),))
    zero = aggregate_timing((TaskTimingObservation("a", started_ms=0, completed_ms=0, provider_latency_ms=0),))

    assert unknown.generation_wall_ms is None
    assert unknown.critical_path_ms is None
    assert parallelism_ratio(unknown) is None
    assert zero.generation_wall_ms == 0
    assert parallelism_ratio(zero) is None


def test_timing_rejects_cycles_and_unknown_dependencies() -> None:
    with pytest.raises(ValueError, match="cycle"):
        aggregate_timing(
            (
                TaskTimingObservation("a", ("b",), 0, 1),
                TaskTimingObservation("b", ("a",), 0, 1),
            )
        )
    with pytest.raises(ValueError, match="unknown timing dependency"):
        aggregate_timing((TaskTimingObservation("a", ("missing",), 0, 1),))
