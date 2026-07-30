"""Offline acceptance tests for the generic experiment DAG scheduler."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from mcp_server.experiments.application.schedule_graph import (
    DuplicateLeaseError,
    ExperimentGraphScheduler,
    RetryNotAllowed,
)
from mcp_server.experiments.domain.scheduling import (
    AttemptRecord,
    GraphBudgetLimits,
    GraphUsage,
    ScheduledGraph,
    ScheduledTask,
    ScheduledTaskStatus,
    SchedulerRunStatus,
    TaskExecutionOutcome,
    TaskLease,
    TaskState,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(minutes=5)


def _graph(*tasks: ScheduledTask, concurrency: int = 2, attempts: int = 2, **budget: object) -> ScheduledGraph:
    return ScheduledGraph(
        graph_id="candidate-run-7",
        tasks=tuple(tasks),
        max_concurrency=concurrency,
        max_attempts=attempts,
        budgets=GraphBudgetLimits(**budget),
    )


def _ok(*outputs: str, usage: GraphUsage | None = None) -> TaskExecutionOutcome:
    return TaskExecutionOutcome(succeeded=True, outputs=outputs, usage=usage or GraphUsage())


def _failed(category: str = "provider_error", usage: GraphUsage | None = None) -> TaskExecutionOutcome:
    return TaskExecutionOutcome(succeeded=False, failure_category=category, usage=usage or GraphUsage())


class FakeExecutor:
    def __init__(self, outcomes: dict[str, TaskExecutionOutcome]) -> None:
        self.outcomes = outcomes
        self.calls: list[TaskLease] = []
        self.active = 0
        self.high_water = 0

    async def execute(self, lease: TaskLease) -> TaskExecutionOutcome:
        self.calls.append(lease)
        self.active += 1
        self.high_water = max(self.high_water, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        return self.outcomes[lease.task_id]


def test_topological_readiness_concurrent_branches_and_bounded_executor() -> None:
    scheduler = ExperimentGraphScheduler(
        _graph(
            ScheduledTask("root"),
            ScheduledTask("left", dependencies=("root",), required_outputs=("left.md",)),
            ScheduledTask("right", dependencies=("root",), required_outputs=("right.md",)),
            ScheduledTask("final", dependencies=("left", "right"), required_outputs=("final.md",)),
            concurrency=2,
        )
    )
    executor = FakeExecutor(
        {
            "root": _ok(),
            "left": _ok("left.md"),
            "right": _ok("right.md"),
            "final": _ok("final.md"),
        }
    )

    async def run() -> None:
        assert tuple(item.task_id for item in await scheduler.execute_ready(executor=executor, worker_id="w", now=NOW, lease_ttl=TTL)) == ("root",)
        assert tuple(item.task_id for item in await scheduler.execute_ready(executor=executor, worker_id="w", now=NOW, lease_ttl=TTL)) == ("left", "right")
        assert tuple(item.task_id for item in await scheduler.execute_ready(executor=executor, worker_id="w", now=NOW, lease_ttl=TTL)) == ("final",)

    asyncio.run(run())
    assert executor.high_water == 2
    assert scheduler.run_status() == SchedulerRunStatus.SUCCEEDED
    assert scheduler.required_outputs_present()


def test_failed_dependency_deterministically_skips_all_descendants() -> None:
    scheduler = ExperimentGraphScheduler(
        _graph(
            ScheduledTask("extract"),
            ScheduledTask("analyse", dependencies=("extract",)),
            ScheduledTask("render", dependencies=("analyse",), required_outputs=("doc.md",)),
        )
    )
    lease = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    scheduler.complete(lease=lease, outcome=_failed("bad_source"), now=NOW)

    assert scheduler.task_state("extract").status == ScheduledTaskStatus.FAILED
    assert scheduler.task_state("analyse").status == ScheduledTaskStatus.SKIPPED
    assert scheduler.task_state("render").status == ScheduledTaskStatus.SKIPPED
    assert scheduler.run_status() == SchedulerRunStatus.FAILED


def test_temporary_reservation_pressure_defers_work_until_the_live_lease_commits() -> None:
    scheduler = ExperimentGraphScheduler(
        _graph(
            ScheduledTask("a", reservation=GraphUsage(input_tokens=6, billed_cost_usd=Decimal("0.10"))),
            ScheduledTask("b", reservation=GraphUsage(input_tokens=6, billed_cost_usd=Decimal("0.10"))),
            max_input_tokens=10,
        )
    )
    leases = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)
    assert tuple(lease.task_id for lease in leases) == ("a",)
    assert scheduler.task_state("b").status == ScheduledTaskStatus.PENDING
    scheduler.complete(lease=leases[0], outcome=_ok(usage=GraphUsage(input_tokens=1, billed_cost_usd=Decimal("0.01"))), now=NOW)
    second = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)
    assert tuple(lease.task_id for lease in second) == ("b",)
    scheduler.complete(lease=second[0], outcome=_ok(usage=GraphUsage(input_tokens=1, billed_cost_usd=None)), now=NOW)
    assert scheduler.committed_usage.billed_cost_usd is None
    assert scheduler.run_status() == SchedulerRunStatus.SUCCEEDED


def test_permanently_unsafe_budget_is_terminal_without_a_provider_lease() -> None:
    scheduler = ExperimentGraphScheduler(
        _graph(
            ScheduledTask("a", reservation=GraphUsage(input_tokens=11, billed_cost_usd=Decimal("0.10"))),
            max_input_tokens=10,
            max_billed_cost_usd=Decimal("1.00"),
        )
    )
    assert scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL) == ()
    state = scheduler.task_state("a")
    assert state.status == ScheduledTaskStatus.FAILED
    assert state.attempts[-1].failure_category == "budget_exhausted"


def test_failed_attempt_is_preserved_and_only_explicit_retry_gets_new_stable_identity() -> None:
    scheduler = ExperimentGraphScheduler(_graph(ScheduledTask("task"), attempts=2))
    first = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    scheduler.complete(lease=first, outcome=_failed(), now=NOW)
    state = scheduler.task_state("task")
    assert len(state.attempts) == 1
    assert state.attempts[0].idempotency_key == first.idempotency_key
    assert scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL) == ()

    scheduler.retry(task_id="task")
    second = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    assert second.attempt_number == 2
    assert second.idempotency_key != first.idempotency_key
    scheduler.complete(lease=second, outcome=_failed(), now=NOW)
    with pytest.raises(RetryNotAllowed, match="exhausted"):
        scheduler.retry(task_id="task")


def test_duplicate_lease_is_prevented_and_restart_keeps_live_lease() -> None:
    graph = _graph(ScheduledTask("task"))
    scheduler = ExperimentGraphScheduler(graph)
    lease = scheduler.lease_ready(worker_id="first", now=NOW, lease_ttl=TTL)[0]
    assert scheduler.lease_ready(worker_id="second", now=NOW, lease_ttl=TTL) == ()
    restarted = ExperimentGraphScheduler(graph, snapshot=scheduler.snapshot())
    assert restarted.lease_ready(worker_id="second", now=NOW + timedelta(minutes=1), lease_ttl=TTL) == ()
    with pytest.raises(DuplicateLeaseError):
        restarted.complete(lease=replace(lease, lease_id="foreign"), outcome=_ok(), now=NOW)


def test_expired_lease_is_reconciled_without_silent_provider_retry_then_can_be_explicitly_retried() -> None:
    scheduler = ExperimentGraphScheduler(_graph(ScheduledTask("task")))
    lease = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    restarted = ExperimentGraphScheduler(_graph(ScheduledTask("task")), snapshot=scheduler.snapshot())
    assert restarted.reconcile_expired_leases(now=lease.expires_at) == ("task",)
    failed = restarted.task_state("task")
    assert failed.status == ScheduledTaskStatus.FAILED
    assert failed.attempts[-1].failure_category == "lease_expired"
    assert restarted.lease_ready(worker_id="worker", now=lease.expires_at, lease_ttl=TTL) == ()
    restarted.retry(task_id="task")
    assert restarted.lease_ready(worker_id="worker", now=lease.expires_at, lease_ttl=TTL)[0].attempt_number == 2


def test_explicit_retry_reopens_only_descendants_skipped_by_that_failure() -> None:
    scheduler = ExperimentGraphScheduler(
        _graph(
            ScheduledTask("extract"),
            ScheduledTask("render", dependencies=("extract",), required_outputs=("doc.md",)),
        )
    )
    first = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    scheduler.complete(lease=first, outcome=_failed(), now=NOW)
    assert scheduler.task_state("render").status == ScheduledTaskStatus.SKIPPED

    scheduler.retry(task_id="extract")
    assert scheduler.task_state("render").status == ScheduledTaskStatus.PENDING
    second = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    scheduler.complete(lease=second, outcome=_ok(), now=NOW)
    render = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    scheduler.complete(lease=render, outcome=_ok("doc.md"), now=NOW)
    assert scheduler.run_status() == SchedulerRunStatus.SUCCEEDED


def test_success_without_all_required_outputs_is_terminal_failure() -> None:
    scheduler = ExperimentGraphScheduler(_graph(ScheduledTask("render", required_outputs=("doc.md",))))
    lease = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    state = scheduler.complete(lease=lease, outcome=_ok(), now=NOW)
    assert state.status == ScheduledTaskStatus.FAILED
    assert state.attempts[-1].failure_category == "missing_required_output"
    assert not scheduler.required_outputs_present()
    assert scheduler.run_status() == SchedulerRunStatus.FAILED


def test_restored_snapshot_rejects_exhausted_pending_task_and_invalid_live_lease() -> None:
    graph = _graph(ScheduledTask("task"), attempts=1)
    scheduler = ExperimentGraphScheduler(graph)
    poisoned_pending = replace(
        scheduler.snapshot(),
        task_states=(
            TaskState(
                task_id="task",
                attempts=(
                    AttemptRecord(
                        task_id="task",
                        attempt_number=1,
                        attempt_id="a",
                        idempotency_key="i",
                        status=ScheduledTaskStatus.FAILED,
                        leased_at=NOW,
                        completed_at=NOW,
                        usage=GraphUsage(),
                        failure_category="provider_error",
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="exhausted"):
        ExperimentGraphScheduler(graph, snapshot=poisoned_pending)

    lease = scheduler.lease_ready(worker_id="worker", now=NOW, lease_ttl=TTL)[0]
    poisoned_lease = replace(
        scheduler.snapshot(),
        task_states=(replace(scheduler.task_state("task"), active_lease=replace(lease, expires_at=lease.leased_at)),),
    )
    with pytest.raises(ValueError, match="expiry"):
        ExperimentGraphScheduler(graph, snapshot=poisoned_lease)
