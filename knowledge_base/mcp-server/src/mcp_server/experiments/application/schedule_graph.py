"""In-memory, deterministic scheduler for isolated experiment task DAGs.

Persistence adapters can snapshot and restore this object, but the scheduling
rules themselves intentionally depend on neither SQLAlchemy nor a provider.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from mcp_server.experiments.domain.scheduling import (
    AttemptRecord,
    GraphBudgetLimits,
    GraphUsage,
    ScheduledGraph,
    ScheduledTask,
    ScheduledTaskExecutor,
    ScheduledTaskStatus,
    SchedulerRunStatus,
    SchedulerSnapshot,
    TaskExecutionOutcome,
    TaskLease,
    TaskState,
)


class SchedulerError(RuntimeError):
    pass


class DuplicateLeaseError(SchedulerError):
    pass


class RetryNotAllowed(SchedulerError):
    pass


class BudgetExhausted(SchedulerError):
    pass


_TERMINAL = frozenset({ScheduledTaskStatus.SUCCEEDED, ScheduledTaskStatus.FAILED, ScheduledTaskStatus.SKIPPED})


class ExperimentGraphScheduler:
    """Leases ready work and records one explicit attempt at a time per task.

    Retrying is deliberately a separate operation.  Neither lease expiry nor
    executor failure can issue another provider request by itself.
    """

    def __init__(self, graph: ScheduledGraph, *, snapshot: SchedulerSnapshot | None = None) -> None:
        self._graph = graph
        self._tasks = {task.task_id: task for task in graph.tasks}
        self._order = _topological_order(graph.tasks)
        if snapshot is None:
            self._states = {task.task_id: TaskState(task_id=task.task_id) for task in graph.tasks}
            self._committed_usage = GraphUsage(billed_cost_usd=Decimal("0"))
        else:
            if snapshot.graph_id != graph.graph_id:
                raise ValueError("snapshot graph identity does not match")
            if {state.task_id for state in snapshot.task_states} != set(self._tasks):
                raise ValueError("snapshot task state does not match graph")
            self._states = {state.task_id: state for state in snapshot.task_states}
            self._committed_usage = snapshot.committed_usage
            self._validate_snapshot()
            self._skip_blocked_descendants()

    @property
    def graph(self) -> ScheduledGraph:
        return self._graph

    @property
    def committed_usage(self) -> GraphUsage:
        return self._committed_usage

    def snapshot(self) -> SchedulerSnapshot:
        return SchedulerSnapshot(
            graph_id=self._graph.graph_id,
            task_states=tuple(self._states[task_id] for task_id in self._order),
            committed_usage=self._committed_usage,
        )

    def task_state(self, task_id: str) -> TaskState:
        return self._state(task_id)

    def run_status(self) -> SchedulerRunStatus:
        states = tuple(self._states.values())
        if all(state.status == ScheduledTaskStatus.SUCCEEDED for state in states) and self.required_outputs_present():
            return SchedulerRunStatus.SUCCEEDED
        if all(state.status in _TERMINAL for state in states):
            return SchedulerRunStatus.FAILED
        if any(state.status != ScheduledTaskStatus.PENDING for state in states):
            return SchedulerRunStatus.RUNNING
        return SchedulerRunStatus.PENDING

    def required_outputs_present(self) -> bool:
        produced = {
            output
            for state in self._states.values()
            for attempt in state.attempts
            if attempt.status == ScheduledTaskStatus.SUCCEEDED
            for output in attempt.outputs
        }
        return all(output in produced for task in self._tasks.values() for output in task.required_outputs)

    def reconcile_expired_leases(self, *, now: datetime) -> tuple[str, ...]:
        """Mark stale in-flight work failed without re-dispatching it."""

        _utc(now, "now")
        expired: list[str] = []
        for task_id in self._order:
            state = self._states[task_id]
            lease = state.active_lease
            if state.status == ScheduledTaskStatus.LEASED and lease is not None and lease.expires_at <= now:
                self._finish_failure(
                    task_id=task_id,
                    now=now,
                    failure_category="lease_expired",
                    usage=GraphUsage(),
                )
                expired.append(task_id)
        self._skip_blocked_descendants()
        return tuple(expired)

    def lease_ready(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> tuple[TaskLease, ...]:
        """Claim a deterministic bounded batch; a task can only have one lease."""

        _utc(now, "now")
        if not worker_id or not worker_id.strip():
            raise ValueError("worker_id must be non-blank")
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        self.reconcile_expired_leases(now=now)
        self._skip_blocked_descendants()
        available = self._graph.max_concurrency - self._active_lease_count()
        if available <= 0:
            return ()
        leases: list[TaskLease] = []
        for task_id in self._order:
            if len(leases) >= available:
                break
            state = self._states[task_id]
            task = self._tasks[task_id]
            if state.status != ScheduledTaskStatus.PENDING or not self._dependencies_succeeded(task):
                continue
            if not self._can_fit_against_committed(task.reservation):
                # This task cannot ever run under the remaining graph budget.
                self._mark_budget_exhausted(task_id=task_id, now=now)
                self._skip_blocked_descendants()
                continue
            if not self._can_reserve(task.reservation):
                # Another live lease merely holds a temporary reservation.  Keep
                # this task pending: the earlier task may commit below its cap.
                continue
            attempt_number = len(state.attempts) + 1
            attempt_id = _stable_id(self._graph.graph_id, task_id, str(attempt_number), "attempt")
            idempotency_key = _stable_id(self._graph.graph_id, task_id, str(attempt_number), "idempotency")
            lease_id = _stable_id(self._graph.graph_id, task_id, str(attempt_number), worker_id, now.isoformat(), "lease")
            lease = TaskLease(
                graph_id=self._graph.graph_id,
                task_id=task_id,
                attempt_number=attempt_number,
                attempt_id=attempt_id,
                idempotency_key=idempotency_key,
                lease_id=lease_id,
                leased_by=worker_id,
                leased_at=now,
                expires_at=now + lease_ttl,
            )
            self._states[task_id] = replace(state, status=ScheduledTaskStatus.LEASED, active_lease=lease)
            leases.append(lease)
        return tuple(leases)

    def complete(self, *, lease: TaskLease, outcome: TaskExecutionOutcome, now: datetime) -> TaskState:
        """Record a terminal outcome exactly once; stale/foreign leases are rejected."""

        _utc(now, "now")
        if lease.graph_id != self._graph.graph_id:
            raise DuplicateLeaseError("lease graph identity does not match scheduler")
        state = self._state(lease.task_id)
        if state.status != ScheduledTaskStatus.LEASED or state.active_lease != lease:
            raise DuplicateLeaseError("lease is not the current active lease for the task")
        if outcome.succeeded:
            required = set(self._tasks[lease.task_id].required_outputs)
            if not required.issubset(outcome.outputs):
                return self._finish_failure(
                    task_id=lease.task_id,
                    now=now,
                    failure_category="missing_required_output",
                    usage=outcome.usage,
                    outputs=outcome.outputs,
                )
            if not self._can_commit(outcome.usage, replacing_reservation=self._tasks[lease.task_id].reservation):
                return self._finish_failure(
                    task_id=lease.task_id,
                    now=now,
                    failure_category="budget_exhausted",
                    usage=outcome.usage,
                    outputs=outcome.outputs,
                )
            record = AttemptRecord(
                task_id=lease.task_id,
                attempt_number=lease.attempt_number,
                attempt_id=lease.attempt_id,
                idempotency_key=lease.idempotency_key,
                status=ScheduledTaskStatus.SUCCEEDED,
                leased_at=lease.leased_at,
                completed_at=now,
                usage=outcome.usage,
                outputs=outcome.outputs,
            )
            self._committed_usage = self._committed_usage.plus(outcome.usage)
            self._states[lease.task_id] = TaskState(
                task_id=lease.task_id,
                status=ScheduledTaskStatus.SUCCEEDED,
                attempts=state.attempts + (record,),
            )
        else:
            self._finish_failure(
                task_id=lease.task_id,
                now=now,
                failure_category=outcome.failure_category or "executor_failed",
                usage=outcome.usage,
                outputs=outcome.outputs,
            )
        self._skip_blocked_descendants()
        return self._state(lease.task_id)

    def retry(self, *, task_id: str) -> TaskState:
        """Explicitly make one failed task eligible for another provider attempt."""

        state = self._state(task_id)
        task = self._tasks[task_id]
        if state.status != ScheduledTaskStatus.FAILED:
            raise RetryNotAllowed("only failed tasks can be explicitly retried")
        if state.attempts and state.attempts[-1].failure_category == "budget_exhausted":
            raise RetryNotAllowed("budget-exhausted tasks cannot be retried")
        if len(state.attempts) >= self._attempt_limit(task):
            raise RetryNotAllowed("task has exhausted its maximum explicit attempts")
        if any(self._states[dependency].status != ScheduledTaskStatus.SUCCEEDED for dependency in task.dependencies):
            raise RetryNotAllowed("task dependencies are not all successful")
        self._states[task_id] = replace(state, status=ScheduledTaskStatus.PENDING)
        self._reopen_descendants_after_explicit_retry()
        return self._states[task_id]

    async def execute_ready(
        self,
        *,
        executor: ScheduledTaskExecutor,
        worker_id: str,
        now: datetime,
        lease_ttl: timedelta,
    ) -> tuple[TaskState, ...]:
        """Execute one ready batch concurrently. It never performs implicit retries."""

        leases = self.lease_ready(worker_id=worker_id, now=now, lease_ttl=lease_ttl)
        if not leases:
            return ()

        async def execute_one(lease: TaskLease) -> TaskState:
            try:
                outcome = await executor.execute(lease)
            except Exception:  # executor errors are retained as failed attempts, never retried here
                outcome = TaskExecutionOutcome(succeeded=False, failure_category="executor_exception")
            return self.complete(lease=lease, outcome=outcome, now=now)

        return tuple(await asyncio.gather(*(execute_one(lease) for lease in leases)))

    def _finish_failure(
        self,
        *,
        task_id: str,
        now: datetime,
        failure_category: str,
        usage: GraphUsage,
        outputs: tuple[str, ...] = (),
    ) -> TaskState:
        state = self._state(task_id)
        lease = state.active_lease
        if lease is None:
            raise SchedulerError("cannot finish a task without an active lease")
        # Observed usage remains in the ledger even when the task failed.
        self._committed_usage = self._committed_usage.plus(usage)
        record = AttemptRecord(
            task_id=task_id,
            attempt_number=lease.attempt_number,
            attempt_id=lease.attempt_id,
            idempotency_key=lease.idempotency_key,
            status=ScheduledTaskStatus.FAILED,
            leased_at=lease.leased_at,
            completed_at=now,
            usage=usage,
            outputs=outputs,
            failure_category=failure_category,
        )
        result = TaskState(task_id=task_id, status=ScheduledTaskStatus.FAILED, attempts=state.attempts + (record,))
        self._states[task_id] = result
        return result

    def _mark_budget_exhausted(self, *, task_id: str, now: datetime) -> None:
        state = self._state(task_id)
        if state.status != ScheduledTaskStatus.PENDING:
            return
        # No provider request happened, so this is a deterministic terminal state,
        # not a failed provider attempt.
        self._states[task_id] = TaskState(
            task_id=task_id,
            status=ScheduledTaskStatus.FAILED,
            attempts=state.attempts
            + (
                AttemptRecord(
                    task_id=task_id,
                    attempt_number=len(state.attempts) + 1,
                    attempt_id=_stable_id(self._graph.graph_id, task_id, str(len(state.attempts) + 1), "budget"),
                    idempotency_key=_stable_id(self._graph.graph_id, task_id, str(len(state.attempts) + 1), "budget-idempotency"),
                    status=ScheduledTaskStatus.FAILED,
                    leased_at=now,
                    completed_at=now,
                    usage=GraphUsage(),
                    failure_category="budget_exhausted",
                ),
            ),
        )

    def _skip_blocked_descendants(self) -> None:
        for task_id in self._order:
            state = self._states[task_id]
            task = self._tasks[task_id]
            if state.status != ScheduledTaskStatus.PENDING:
                continue
            if any(self._states[dependency].status in {ScheduledTaskStatus.FAILED, ScheduledTaskStatus.SKIPPED} for dependency in task.dependencies):
                self._states[task_id] = replace(state, status=ScheduledTaskStatus.SKIPPED)

    def _reopen_descendants_after_explicit_retry(self) -> None:
        """Undo only deterministic skip markers made obsolete by an explicit retry."""

        for task_id in self._order:
            state = self._states[task_id]
            if state.status != ScheduledTaskStatus.SKIPPED:
                continue
            dependencies = self._tasks[task_id].dependencies
            if all(
                self._states[dependency].status
                in {ScheduledTaskStatus.PENDING, ScheduledTaskStatus.LEASED, ScheduledTaskStatus.SUCCEEDED}
                for dependency in dependencies
            ):
                self._states[task_id] = replace(state, status=ScheduledTaskStatus.PENDING)

    def _dependencies_succeeded(self, task: ScheduledTask) -> bool:
        return all(self._states[dependency].status == ScheduledTaskStatus.SUCCEEDED for dependency in task.dependencies)

    def _active_lease_count(self) -> int:
        return sum(state.status == ScheduledTaskStatus.LEASED for state in self._states.values())

    def _attempt_limit(self, task: ScheduledTask) -> int:
        return task.max_attempts if task.max_attempts is not None else self._graph.max_attempts

    def _can_reserve(self, reservation: GraphUsage) -> bool:
        # Unknown reservation cost cannot safely satisfy a hard cost ceiling.
        if self._graph.budgets.max_billed_cost_usd is not None and reservation.billed_cost_usd is None:
            return False
        reserved = self._reserved_usage()
        return _fits(self._committed_usage, reserved, reservation, self._graph.budgets)

    def _can_fit_against_committed(self, reservation: GraphUsage) -> bool:
        """Whether a task is permanently unsafe, independent of live leases."""

        if self._graph.budgets.max_billed_cost_usd is not None and reservation.billed_cost_usd is None:
            return False
        return _fits(self._committed_usage, _KnownUsage(), reservation, self._graph.budgets)

    def _can_commit(self, usage: GraphUsage, *, replacing_reservation: GraphUsage) -> bool:
        reserved = self._reserved_usage().minus_known(replacing_reservation)
        return _fits(self._committed_usage, reserved, usage, self._graph.budgets)

    def _reserved_usage(self) -> "_KnownUsage":
        current = _KnownUsage()
        for state in self._states.values():
            if state.status == ScheduledTaskStatus.LEASED:
                current = current.plus(self._tasks[state.task_id].reservation)
        return current

    def _state(self, task_id: str) -> TaskState:
        try:
            return self._states[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown graph task {task_id!r}") from exc

    def _validate_snapshot(self) -> None:
        for task_id, state in self._states.items():
            task = self._tasks[task_id]
            if state.status == ScheduledTaskStatus.LEASED and state.active_lease is None:
                raise ValueError("leased snapshot task requires an active lease")
            if state.status != ScheduledTaskStatus.LEASED and state.active_lease is not None:
                raise ValueError("only leased snapshot task may retain an active lease")
            if any(record.task_id != task_id for record in state.attempts):
                raise ValueError("snapshot attempt task identity does not match state")
            numbers = tuple(record.attempt_number for record in state.attempts)
            if numbers != tuple(range(1, len(numbers) + 1)):
                raise ValueError("snapshot attempts must be contiguous")
            if state.status == ScheduledTaskStatus.PENDING and len(state.attempts) >= self._attempt_limit(task):
                raise ValueError("pending snapshot task cannot have exhausted its attempt limit")
            if state.active_lease is not None:
                lease = state.active_lease
                if lease.graph_id != self._graph.graph_id or lease.task_id != task_id:
                    raise ValueError("snapshot lease identity does not match graph task")
                if lease.attempt_number != len(state.attempts) + 1:
                    raise ValueError("snapshot lease attempt number must follow completed attempts")
                if not lease.lease_id or not lease.idempotency_key or not lease.attempt_id:
                    raise ValueError("snapshot lease identifiers must be non-blank")
                _utc(lease.leased_at, "snapshot lease leased_at")
                _utc(lease.expires_at, "snapshot lease expires_at")
                if lease.expires_at <= lease.leased_at:
                    raise ValueError("snapshot lease expiry must be after its start")


class _KnownUsage:
    """Internal sum retaining known-cost information for reservation checks."""

    def __init__(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        tool_bytes: int = 0,
        elapsed_ms: int = 0,
        billed_cost_usd: Decimal = Decimal("0"),
        cost_known: bool = True,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.tool_calls = tool_calls
        self.tool_bytes = tool_bytes
        self.elapsed_ms = elapsed_ms
        self.billed_cost_usd = billed_cost_usd
        self.cost_known = cost_known

    def plus(self, value: GraphUsage) -> "_KnownUsage":
        return _KnownUsage(
            self.input_tokens + value.input_tokens,
            self.output_tokens + value.output_tokens,
            self.tool_calls + value.tool_calls,
            self.tool_bytes + value.tool_bytes,
            self.elapsed_ms + value.elapsed_ms,
            self.billed_cost_usd + (value.billed_cost_usd or Decimal("0")),
            self.cost_known and value.billed_cost_usd is not None,
        )

    def minus_known(self, value: GraphUsage) -> "_KnownUsage":
        return _KnownUsage(
            self.input_tokens - value.input_tokens,
            self.output_tokens - value.output_tokens,
            self.tool_calls - value.tool_calls,
            self.tool_bytes - value.tool_bytes,
            self.elapsed_ms - value.elapsed_ms,
            self.billed_cost_usd - (value.billed_cost_usd or Decimal("0")),
            self.cost_known and value.billed_cost_usd is not None,
        )


def _fits(committed: GraphUsage, reserved: _KnownUsage, next_value: GraphUsage, limits: GraphBudgetLimits) -> bool:
    checks = (
        (committed.input_tokens + reserved.input_tokens + next_value.input_tokens, limits.max_input_tokens),
        (committed.output_tokens + reserved.output_tokens + next_value.output_tokens, limits.max_output_tokens),
        (committed.tool_calls + reserved.tool_calls + next_value.tool_calls, limits.max_tool_calls),
        (committed.tool_bytes + reserved.tool_bytes + next_value.tool_bytes, limits.max_tool_bytes),
        (committed.elapsed_ms + reserved.elapsed_ms + next_value.elapsed_ms, limits.max_elapsed_ms),
    )
    if any(limit is not None and actual > limit for actual, limit in checks):
        return False
    if limits.max_billed_cost_usd is None:
        return True
    if committed.billed_cost_usd is None or not reserved.cost_known or next_value.billed_cost_usd is None:
        return False
    return committed.billed_cost_usd + reserved.billed_cost_usd + next_value.billed_cost_usd <= limits.max_billed_cost_usd


def _topological_order(tasks: tuple[ScheduledTask, ...]) -> tuple[str, ...]:
    by_id = {task.task_id: task for task in tasks}
    remaining = {task.task_id: set(task.dependencies) for task in tasks}
    order: list[str] = []
    while remaining:
        ready = sorted(task_id for task_id, dependencies in remaining.items() if not dependencies)
        if not ready:
            raise ValueError("graph dependencies must be acyclic")
        for task_id in ready:
            order.append(task_id)
            remaining.pop(task_id)
        done = set(ready)
        for dependencies in remaining.values():
            dependencies.difference_update(done)
    if set(order) != set(by_id):
        raise ValueError("unable to order graph tasks")
    return tuple(order)


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be UTC-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC")
