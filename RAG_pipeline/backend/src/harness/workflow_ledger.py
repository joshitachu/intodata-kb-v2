"""Deterministic workflow-ledger transition and observation policy.

The model may reason over these facts, but it never creates or changes them.
Provider adapters normalize observations here and repositories enforce the
same terminal rules for every caller.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum

from src.harness.models import WorkflowRunStatus


class WorkflowChildStatus(StrEnum):
    UNKNOWN = "unknown"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowObservationSource(StrEnum):
    TRIGGER = "trigger"
    POLL = "poll"
    RECOVERY = "recovery"
    CALLBACK = "callback"
    CANCELLATION = "cancellation"
    BACKFILL = "backfill"


class WorkflowChildCancellationState(StrEnum):
    NONE = "none"
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


TERMINAL_PARENT_STATUSES = frozenset(
    {
        WorkflowRunStatus.SUCCEEDED,
        WorkflowRunStatus.FAILED,
        WorkflowRunStatus.CANCELLED,
    }
)
TERMINAL_CHILD_STATUSES = frozenset(
    {
        WorkflowChildStatus.SUCCEEDED,
        WorkflowChildStatus.FAILED,
        WorkflowChildStatus.CANCELLED,
    }
)

# Explicit inventory of the currently legitimate aggregate lifecycle. Later
# slices may narrow the non-terminal edges as legacy orchestration is removed,
# but adding an edge always requires changing this single policy and its tests.
_LEGAL_PARENT_TRANSITIONS: dict[WorkflowRunStatus, frozenset[WorkflowRunStatus]] = {
    WorkflowRunStatus.PENDING: frozenset(
        {
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.TRIGGERED,
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.INTERRUPTED,
            WorkflowRunStatus.INDETERMINATE,
        }
    ),
    WorkflowRunStatus.RUNNING: frozenset(
        {
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.TRIGGERED,
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.INTERRUPTED,
            WorkflowRunStatus.INDETERMINATE,
        }
    ),
    WorkflowRunStatus.TRIGGERED: frozenset(
        {
            WorkflowRunStatus.TRIGGERED,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.INDETERMINATE,
        }
    ),
    WorkflowRunStatus.INTERRUPTED: frozenset(
        {
            WorkflowRunStatus.INTERRUPTED,
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.TRIGGERED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.INDETERMINATE,
        }
    ),
    WorkflowRunStatus.INDETERMINATE: frozenset(
        {
            WorkflowRunStatus.INDETERMINATE,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.TRIGGERED,
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }
    ),
    WorkflowRunStatus.SUCCEEDED: frozenset({WorkflowRunStatus.SUCCEEDED}),
    WorkflowRunStatus.FAILED: frozenset({WorkflowRunStatus.FAILED}),
    WorkflowRunStatus.CANCELLED: frozenset({WorkflowRunStatus.CANCELLED}),
}

_CHILD_STATUS_ALIASES = {
    "queued": WorkflowChildStatus.QUEUED,
    "pending": WorkflowChildStatus.QUEUED,
    "triggered": WorkflowChildStatus.QUEUED,
    "generation_triggered": WorkflowChildStatus.QUEUED,
    "running": WorkflowChildStatus.RUNNING,
    "generating": WorkflowChildStatus.RUNNING,
    "completed": WorkflowChildStatus.SUCCEEDED,
    "completed_warnings": WorkflowChildStatus.SUCCEEDED,
    "succeeded": WorkflowChildStatus.SUCCEEDED,
    "ready": WorkflowChildStatus.SUCCEEDED,
    "failed": WorkflowChildStatus.FAILED,
    "error": WorkflowChildStatus.FAILED,
    "cancelled": WorkflowChildStatus.CANCELLED,
    "canceled": WorkflowChildStatus.CANCELLED,
}


class IllegalWorkflowTransition(ValueError):
    """Raised when a caller attempts to reopen or rewrite a terminal parent."""


def normalize_child_status(value: str | None) -> WorkflowChildStatus:
    return _CHILD_STATUS_ALIASES.get((value or "").strip().lower(), WorkflowChildStatus.UNKNOWN)


def validate_parent_transition(
    current: WorkflowRunStatus,
    target: WorkflowRunStatus,
) -> None:
    """Enforce terminal immutability at the aggregate boundary.

    Existing non-terminal orchestration remains deliberately permissive while
    the later agent-loop slices replace its execution paths. Terminal facts,
    however, can never be reopened or rewritten by a stale poll/callback.
    """

    if target not in _LEGAL_PARENT_TRANSITIONS[current]:
        raise IllegalWorkflowTransition(
            f"Illegal workflow transition: {current.value} -> {target.value}"
        )


@dataclass(frozen=True)
class ChildObservationDecision:
    accepted: bool
    status: WorkflowChildStatus
    reason: str


def decide_child_observation(
    *,
    current_status: WorkflowChildStatus,
    current_provider_timestamp: datetime.datetime | None,
    observed_status: WorkflowChildStatus,
    observed_provider_timestamp: datetime.datetime | None,
) -> ChildObservationDecision:
    """Reject stale/out-of-order provider facts deterministically."""

    current_time = _utc(current_provider_timestamp)
    observed_time = _utc(observed_provider_timestamp)
    if current_time is not None and observed_time is not None and observed_time < current_time:
        return ChildObservationDecision(False, current_status, "stale_provider_timestamp")
    if current_status in TERMINAL_CHILD_STATUSES:
        if observed_status == current_status:
            return ChildObservationDecision(True, current_status, "duplicate_terminal_fact")
        return ChildObservationDecision(False, current_status, "terminal_child_precedence")
    if observed_status is WorkflowChildStatus.UNKNOWN:
        return ChildObservationDecision(True, current_status, "unknown_status_preserved")
    return ChildObservationDecision(True, observed_status, "accepted")


def parent_status_from_child(
    *,
    parent_status: WorkflowRunStatus,
    child_status: WorkflowChildStatus,
    cancellation_state: WorkflowChildCancellationState,
) -> WorkflowRunStatus:
    """Resolve a parent without conflating provider and local cancellation facts."""

    if parent_status in TERMINAL_PARENT_STATUSES:
        return parent_status
    if cancellation_state is WorkflowChildCancellationState.ACCEPTED:
        return WorkflowRunStatus.CANCELLED
    if child_status is WorkflowChildStatus.SUCCEEDED:
        return WorkflowRunStatus.SUCCEEDED
    if child_status is WorkflowChildStatus.FAILED:
        return WorkflowRunStatus.FAILED
    if child_status is WorkflowChildStatus.CANCELLED:
        return WorkflowRunStatus.CANCELLED
    return parent_status


def _utc(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)
