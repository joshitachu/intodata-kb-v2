"""Pure recovery-state derivation for isolated candidate experiment runs.

This module intentionally consumes only sanitized scheduler facts.  It has no
repository, provider, filesystem, publishing, or retry side effect.  A caller
must explicitly act on a returned manual-retry requirement.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

class RecoveryState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SanitizedTaskStatus(StrEnum):
    """Durable scheduler states, reduced to non-provider recovery facts."""

    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SanitizedLeaseSnapshot:
    """Only lease facts needed to recover state; no runtime session/payload."""

    expires_at: datetime
    external_call_may_have_started: bool

    def __post_init__(self) -> None:
        _utc(self.expires_at, "lease expires_at")


@dataclass(frozen=True, slots=True)
class SanitizedTaskSnapshot:
    """A redacted task view sufficient for status and output reconciliation."""

    task_id: str
    status: SanitizedTaskStatus
    # Every declared output is required for candidate graph completion. This
    # includes receipts and intermediate artifacts, not just rendered docs.
    declared_outputs: tuple[str, ...] = ()
    accepted_output_claims: tuple[str, ...] = ()
    lease: SanitizedLeaseSnapshot | None = None

    def __post_init__(self) -> None:
        _unique_nonblank((self.task_id,), "task id")
        _unique_nonblank(self.declared_outputs, "declared outputs")
        if any(not output or not output.strip() for output in self.accepted_output_claims):
            raise ValueError("accepted output claims must be non-blank")
        if self.status in {SanitizedTaskStatus.LEASED, SanitizedTaskStatus.RUNNING} and self.lease is None:
            raise ValueError("leased or running task requires a sanitized lease")
        if self.status not in {SanitizedTaskStatus.LEASED, SanitizedTaskStatus.RUNNING} and self.lease is not None:
            raise ValueError("only leased or running task may retain a sanitized lease")


@dataclass(frozen=True, slots=True)
class ExperimentRecoverySnapshot:
    experiment_id: str
    tasks: tuple[SanitizedTaskSnapshot, ...]

    def __post_init__(self) -> None:
        _unique_nonblank((self.experiment_id,), "experiment id")
        if not self.tasks:
            raise ValueError("recovery snapshot must contain at least one task")
        _unique_nonblank((task.task_id for task in self.tasks), "task ids")
        _unique_nonblank(
            (output for task in self.tasks for output in task.declared_outputs), "graph declared outputs"
        )


@dataclass(frozen=True, slots=True)
class TaskRecovery:
    task_id: str
    state: RecoveryState
    manual_retry_required: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExperimentRecoveryDecision:
    experiment_id: str
    state: RecoveryState
    task_recovery: tuple[TaskRecovery, ...]
    missing_required_outputs: tuple[str, ...]
    duplicate_required_outputs: tuple[str, ...]
    manual_retry_task_ids: tuple[str, ...]
    # Candidate artifacts remain isolated even when the graph completes.
    publish_permitted: bool = field(default=False, init=False)


def derive_recovery(*, snapshot: ExperimentRecoverySnapshot, now: datetime) -> ExperimentRecoveryDecision:
    """Derive state without claiming work, publishing, or triggering a retry."""

    _utc(now, "now")
    task_recovery = tuple(_recover_task(task, now=now) for task in snapshot.tasks)
    manual_retry = tuple(item.task_id for item in task_recovery if item.manual_retry_required)
    required_counts = _accepted_required_output_counts(snapshot.tasks)
    required = tuple(output for task in snapshot.tasks for output in task.declared_outputs)
    missing = tuple(sorted(output for output in required if required_counts[output] == 0))
    duplicate = tuple(sorted(output for output in required if required_counts[output] > 1))

    if (
        any(item.state == RecoveryState.FAILED for item in task_recovery)
        or missing
        or duplicate
    ):
        state = RecoveryState.FAILED
    elif all(item.state == RecoveryState.COMPLETED for item in task_recovery):
        state = RecoveryState.COMPLETED
    elif any(item.state == RecoveryState.RUNNING for item in task_recovery):
        state = RecoveryState.RUNNING
    else:
        state = RecoveryState.QUEUED

    return ExperimentRecoveryDecision(
        experiment_id=snapshot.experiment_id,
        state=state,
        task_recovery=task_recovery,
        missing_required_outputs=missing,
        duplicate_required_outputs=duplicate,
        manual_retry_task_ids=manual_retry,
    )


def _recover_task(task: SanitizedTaskSnapshot, *, now: datetime) -> TaskRecovery:
    if task.status == SanitizedTaskStatus.PENDING:
        return TaskRecovery(task_id=task.task_id, state=RecoveryState.QUEUED)
    if task.status == SanitizedTaskStatus.SUCCEEDED:
        return TaskRecovery(task_id=task.task_id, state=RecoveryState.COMPLETED)
    if task.status in {SanitizedTaskStatus.FAILED, SanitizedTaskStatus.SKIPPED, SanitizedTaskStatus.CANCELLED}:
        return TaskRecovery(task_id=task.task_id, state=RecoveryState.FAILED, reason=task.status.value)
    if task.status not in {SanitizedTaskStatus.LEASED, SanitizedTaskStatus.RUNNING} or task.lease is None:
        # The scheduler has no unknown state, but malformed/unsupported input
        # must never be interpreted as eligible work.
        return TaskRecovery(task_id=task.task_id, state=RecoveryState.FAILED, reason="unknown_task_state")
    if task.lease.expires_at > now:
        return TaskRecovery(task_id=task.task_id, state=RecoveryState.RUNNING)
    if task.lease.external_call_may_have_started:
        return TaskRecovery(
            task_id=task.task_id,
            state=RecoveryState.FAILED,
            manual_retry_required=True,
            reason="expired_lease_external_call_ambiguous",
        )
    # No external call was started, so the lost lease may be safely queued by a
    # caller. This function itself never claims it.
    return TaskRecovery(task_id=task.task_id, state=RecoveryState.QUEUED, reason="expired_lease_no_external_call")


def _accepted_required_output_counts(tasks: tuple[SanitizedTaskSnapshot, ...]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for task in tasks:
        # Claims from non-successful tasks cannot satisfy a completed candidate.
        if task.status != SanitizedTaskStatus.SUCCEEDED:
            continue
        counts.update(output for output in task.accepted_output_claims if output in task.declared_outputs)
    return counts


def _unique_nonblank(values: object, label: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if any(not isinstance(item, str) or not item.strip() for item in items) or len(items) != len(set(items)):
        raise ValueError(f"{label} must be non-blank and unique")


def _utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be UTC-aware")
