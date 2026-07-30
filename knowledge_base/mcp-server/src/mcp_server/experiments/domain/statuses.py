"""Legal lifecycle transitions for an isolated generation experiment."""

from __future__ import annotations

from enum import StrEnum


class ExperimentStatus(StrEnum):
    """Durable experiment lifecycle states.

    ``queued`` means that all creation-time receipts (including the legacy
    baseline) are immutable and the candidate has not started.  Source snapshotting
    belongs to the later candidate-execution transition, so a frozen S1 baseline is
    intentionally still queued.
    """

    QUEUED = "queued"
    SNAPSHOTTING = "snapshotting"
    RUNNING = "running"
    VALIDATING = "validating"
    COMPLETED = "completed"
    COMPLETED_WARNINGS = "completed_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


LEGAL_TRANSITIONS: dict[ExperimentStatus, frozenset[ExperimentStatus]] = {
    ExperimentStatus.QUEUED: frozenset(
        {ExperimentStatus.SNAPSHOTTING, ExperimentStatus.FAILED, ExperimentStatus.CANCELLED}
    ),
    ExperimentStatus.SNAPSHOTTING: frozenset(
        {ExperimentStatus.RUNNING, ExperimentStatus.FAILED, ExperimentStatus.CANCELLED}
    ),
    ExperimentStatus.RUNNING: frozenset(
        {
            ExperimentStatus.VALIDATING,
            ExperimentStatus.FAILED,
            ExperimentStatus.CANCELLED,
        }
    ),
    ExperimentStatus.VALIDATING: frozenset(
        {
            ExperimentStatus.COMPLETED,
            ExperimentStatus.COMPLETED_WARNINGS,
            ExperimentStatus.FAILED,
        }
    ),
    ExperimentStatus.COMPLETED: frozenset(),
    ExperimentStatus.COMPLETED_WARNINGS: frozenset(),
    ExperimentStatus.FAILED: frozenset(),
    ExperimentStatus.CANCELLED: frozenset(),
}


def can_transition(current: ExperimentStatus, target: ExperimentStatus) -> bool:
    """Return whether a state change is legal without mutating any state."""

    return target in LEGAL_TRANSITIONS[current]


def require_transition(current: ExperimentStatus, target: ExperimentStatus) -> None:
    """Reject non-monotonic lifecycle changes with a domain-only exception."""

    if not can_transition(current, target):
        raise ValueError(f"illegal experiment status transition: {current} -> {target}")
