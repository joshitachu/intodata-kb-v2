"""Provider- and persistence-neutral scheduling contracts for experiment DAGs.

The scheduler deliberately owns only orchestration state.  It cannot publish a
document, reach a provider, or acquire source material; those are separate
ports.  This makes a candidate run safe to exercise with a fake executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ScheduledTaskStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SchedulerRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GraphBudgetLimits:
    """Whole-graph ceilings. ``None`` means that dimension is not capped."""

    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_tool_calls: int | None = None
    max_tool_bytes: int | None = None
    max_elapsed_ms: int | None = None
    max_billed_cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_input_tokens",
            "max_output_tokens",
            "max_tool_calls",
            "max_tool_bytes",
            "max_elapsed_ms",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if self.max_billed_cost_usd is not None and (
            not self.max_billed_cost_usd.is_finite() or self.max_billed_cost_usd < 0
        ):
            raise ValueError("max_billed_cost_usd must be finite and non-negative or None")


@dataclass(frozen=True, slots=True)
class GraphUsage:
    """Usage for one attempt or a graph aggregate.

    Cost is intentionally nullable.  Missing provider billing data must stay
    unknown rather than becoming a misleading zero-dollar total.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    tool_bytes: int = 0
    elapsed_ms: int = 0
    billed_cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "tool_calls", "tool_bytes", "elapsed_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.billed_cost_usd is not None and (
            not self.billed_cost_usd.is_finite() or self.billed_cost_usd < 0
        ):
            raise ValueError("billed_cost_usd must be finite and non-negative or None")

    def plus(self, other: "GraphUsage") -> "GraphUsage":
        cost = (
            None
            if self.billed_cost_usd is None or other.billed_cost_usd is None
            else self.billed_cost_usd + other.billed_cost_usd
        )
        return GraphUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            tool_bytes=self.tool_bytes + other.tool_bytes,
            elapsed_ms=self.elapsed_ms + other.elapsed_ms,
            billed_cost_usd=cost,
        )


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """A generic node. Required output names are logical artifact identifiers."""

    task_id: str
    dependencies: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ()
    max_attempts: int | None = None
    reservation: GraphUsage = field(default_factory=GraphUsage)

    def __post_init__(self) -> None:
        for label, values in (("dependencies", self.dependencies), ("required_outputs", self.required_outputs)):
            if any(not value or not value.strip() for value in values) or len(values) != len(set(values)):
                raise ValueError(f"{label} must be non-blank and unique")
        if not self.task_id or not self.task_id.strip():
            raise ValueError("task_id must be non-blank")
        if self.task_id in self.dependencies:
            raise ValueError("task cannot depend on itself")
        if self.max_attempts is not None and (
            isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class ScheduledGraph:
    graph_id: str
    tasks: tuple[ScheduledTask, ...]
    max_concurrency: int
    max_attempts: int
    budgets: GraphBudgetLimits = field(default_factory=GraphBudgetLimits)

    def __post_init__(self) -> None:
        if not self.graph_id or not self.graph_id.strip():
            raise ValueError("graph_id must be non-blank")
        if not self.tasks:
            raise ValueError("graph must contain at least one task")
        ids = tuple(task.task_id for task in self.tasks)
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        if isinstance(self.max_concurrency, bool) or not isinstance(self.max_concurrency, int) or self.max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        known = set(ids)
        if any(not set(task.dependencies).issubset(known) for task in self.tasks):
            raise ValueError("task dependencies must reference graph tasks")
        _validate_acyclic(self.tasks)


@dataclass(frozen=True, slots=True)
class TaskLease:
    graph_id: str
    task_id: str
    attempt_number: int
    attempt_id: str
    idempotency_key: str
    lease_id: str
    leased_by: str
    leased_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TaskExecutionOutcome:
    """Normalized terminal executor outcome; no raw provider payload is retained."""

    succeeded: bool
    outputs: tuple[str, ...] = ()
    usage: GraphUsage = field(default_factory=GraphUsage)
    failure_category: str | None = None

    def __post_init__(self) -> None:
        if self.succeeded and self.failure_category is not None:
            raise ValueError("successful outcome cannot have a failure category")
        if not self.succeeded and (not self.failure_category or not self.failure_category.strip()):
            raise ValueError("failed outcome requires a failure category")
        if any(not output or not output.strip() for output in self.outputs) or len(self.outputs) != len(set(self.outputs)):
            raise ValueError("outputs must be non-blank and unique")


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    task_id: str
    attempt_number: int
    attempt_id: str
    idempotency_key: str
    status: ScheduledTaskStatus
    leased_at: datetime
    completed_at: datetime | None = None
    usage: GraphUsage | None = None
    outputs: tuple[str, ...] = ()
    failure_category: str | None = None


@dataclass(frozen=True, slots=True)
class TaskState:
    task_id: str
    status: ScheduledTaskStatus = ScheduledTaskStatus.PENDING
    active_lease: TaskLease | None = None
    attempts: tuple[AttemptRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    graph_id: str
    task_states: tuple[TaskState, ...]
    committed_usage: GraphUsage


@runtime_checkable
class ScheduledTaskExecutor(Protocol):
    """The only execution seam. Production may adapt OpenCode; tests use fakes."""

    async def execute(self, lease: TaskLease) -> TaskExecutionOutcome: ...


def _validate_acyclic(tasks: tuple[ScheduledTask, ...]) -> None:
    by_id = {task.task_id: task for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            raise ValueError("graph dependencies must be acyclic")
        visiting.add(task_id)
        for dependency in by_id[task_id].dependencies:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task in tasks:
        visit(task.task_id)
