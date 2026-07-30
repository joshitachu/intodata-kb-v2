"""Durable, provider-neutral task scheduler persistence.

This adapter owns only database state for compiled experiment tasks.  It neither
starts runtimes nor knows about OpenCode, credentials, scanners, or publication.
Every retry gets a new attempt row, and events are an ordered idempotent ledger so
crash recovery can be audited without rewriting execution history.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import datetime
import json
from typing import Literal
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import GenerationTaskAttempt, GenerationTaskEvent, GenerationTaskRun


TaskOutcome = Literal["succeeded", "failed", "cancelled", "skipped"]
_LEASEABLE_STATUSES = ("pending",)
_ACTIVE_STATUSES = ("leased", "running")
_BLOCKING_DEPENDENCY_STATUSES = ("failed", "skipped", "cancelled")


@dataclass(frozen=True)
class TaskLease:
    """The one-time execution authority issued to a worker for a task attempt."""

    task_run_id: str
    experiment_id: str
    node_id: str
    attempt_id: str
    attempt_number: int
    execution_idempotency_key: str
    lease_owner: str
    lease_expires_at: datetime.datetime


class TaskSchedulerRepository:
    """Lease/retry/event persistence for any compiled recipe graph.

    Callers own the surrounding transaction.  Lease acquisition uses a guarded
    SQL update after dependency inspection, so even backends without row-level
    locking cannot issue two active leases for the same task state.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lease_next(
        self,
        *,
        experiment_id: str,
        lease_owner: str,
        lease_seconds: int,
        now: datetime.datetime | None = None,
    ) -> TaskLease | None:
        """Atomically lease the next dependency-ready task, if any.

        Expired leases become terminal failures before selection;
        dependency-blocked work is visibly skipped instead of waiting forever.
        """

        now = _utc_now(now)
        if not lease_owner.strip():
            raise ValueError("lease owner is required")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")

        await self.recover_expired_leases(experiment_id=experiment_id, now=now)
        await self.skip_blocked_tasks(experiment_id=experiment_id, now=now)

        candidates = (
            await self._session.execute(
                select(GenerationTaskRun)
                .where(
                    GenerationTaskRun.experiment_id == experiment_id,
                    GenerationTaskRun.status.in_(_LEASEABLE_STATUSES),
                    GenerationTaskRun.attempt < GenerationTaskRun.max_attempts,
                )
                .order_by(GenerationTaskRun.created_at, GenerationTaskRun.id)
            )
        ).scalars().all()
        expires_at = now + datetime.timedelta(seconds=lease_seconds)
        for candidate in candidates:
            if not await self._dependencies_succeeded(candidate):
                continue
            next_attempt = candidate.attempt + 1
            # The state-and-counter predicate is the duplicate-lease defence.
            # On PostgreSQL this is also protected by normal transaction locks;
            # on SQLite it remains an optimistic compare-and-swap.
            claimed = await self._session.execute(
                update(GenerationTaskRun)
                .where(
                    GenerationTaskRun.id == candidate.id,
                    GenerationTaskRun.status.in_(_LEASEABLE_STATUSES),
                    GenerationTaskRun.attempt == candidate.attempt,
                    GenerationTaskRun.attempt < GenerationTaskRun.max_attempts,
                )
                .values(
                    status="leased",
                    attempt=next_attempt,
                    lease_owner=lease_owner,
                    lease_expires_at=expires_at,
                    started_at=now,
                    completed_at=None,
                    error_message=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                continue

            await self._session.refresh(candidate)
            attempt = GenerationTaskAttempt(
                id=str(uuid4()),
                task_run_id=candidate.id,
                attempt_number=next_attempt,
                execution_idempotency_key=_execution_idempotency_key(
                    candidate.id,
                    next_attempt,
                ),
                status="leased",
                lease_owner=lease_owner,
                lease_expires_at=expires_at,
                started_at=now,
            )
            self._session.add(attempt)
            await self._append_event(
                task=candidate,
                attempt_id=attempt.id,
                idempotency_key=f"lease-acquired:{attempt.id}",
                event_type="lease.acquired",
                payload={
                    "attempt_number": next_attempt,
                    "lease_owner": lease_owner,
                    "lease_expires_at": expires_at.isoformat(),
                },
                occurred_at=now,
            )
            await self._session.flush()
            return TaskLease(
                task_run_id=candidate.id,
                experiment_id=candidate.experiment_id,
                node_id=candidate.node_id,
                attempt_id=attempt.id,
                attempt_number=next_attempt,
                execution_idempotency_key=attempt.execution_idempotency_key,
                lease_owner=lease_owner,
                lease_expires_at=expires_at,
            )
        return None

    async def recover_expired_leases(
        self,
        *,
        experiment_id: str,
        now: datetime.datetime | None = None,
    ) -> tuple[str, ...]:
        """End expired active attempts and make eligible tasks restart-ready."""

        now = _utc_now(now)
        expired = (
            await self._session.execute(
                select(GenerationTaskRun)
                .where(
                    GenerationTaskRun.experiment_id == experiment_id,
                    GenerationTaskRun.status.in_(_ACTIVE_STATUSES),
                    GenerationTaskRun.lease_expires_at.is_not(None),
                    GenerationTaskRun.lease_expires_at <= now,
                )
                .with_for_update()
            )
        ).scalars().all()
        recovered: list[str] = []
        for task in expired:
            active_attempt = await self._active_attempt(task.id)
            if active_attempt is not None:
                active_attempt.status = "abandoned"
                active_attempt.completed_at = now
                active_attempt.error_message = "worker lease expired before terminal outcome"
                active_attempt.lease_expires_at = task.lease_expires_at
            # An expired dispatch is terminal until an operator/scheduler explicitly
            # authorizes a retry.  This prevents ambiguous provider submissions from
            # silently running twice after a crash.
            next_status = "failed"
            task.status = next_status
            task.lease_owner = None
            task.lease_expires_at = None
            task.completed_at = now if next_status == "failed" else None
            task.error_message = "worker lease expired before terminal outcome"
            await self._append_event(
                task=task,
                attempt_id=active_attempt.id if active_attempt is not None else None,
                idempotency_key=(
                    f"lease-expired:{active_attempt.id}"
                    if active_attempt is not None
                    else f"lease-expired:task:{task.attempt}"
                ),
                event_type="lease.expired",
                payload={"next_status": next_status},
                occurred_at=now,
            )
            recovered.append(task.id)
        if expired:
            await self._session.flush()
        return tuple(recovered)

    async def skip_blocked_tasks(
        self,
        *,
        experiment_id: str,
        now: datetime.datetime | None = None,
    ) -> tuple[str, ...]:
        """Persist visible skip outcomes once an upstream task cannot succeed."""

        now = _utc_now(now)
        candidates = (
            await self._session.execute(
                select(GenerationTaskRun)
                .where(
                    GenerationTaskRun.experiment_id == experiment_id,
                    GenerationTaskRun.status.in_(_LEASEABLE_STATUSES),
                )
                .with_for_update()
            )
        ).scalars().all()
        skipped: list[str] = []
        for task in candidates:
            dependency_states = await self._dependency_states(task)
            blockers = sorted(
                node_id
                for node_id, status in dependency_states.items()
                if status in _BLOCKING_DEPENDENCY_STATUSES or status is None
            )
            if not blockers:
                continue
            task.status = "skipped"
            task.completed_at = now
            task.lease_owner = None
            task.lease_expires_at = None
            task.error_message = "blocked by dependency: " + ", ".join(blockers)
            await self._append_event(
                task=task,
                attempt_id=None,
                idempotency_key="dependency-blocked:" + ",".join(blockers),
                event_type="task.skipped",
                payload={"blocked_dependencies": blockers},
                occurred_at=now,
            )
            skipped.append(task.id)
        if skipped:
            await self._session.flush()
        return tuple(skipped)

    async def list_restart_ready(
        self,
        *,
        experiment_id: str,
        now: datetime.datetime | None = None,
    ) -> tuple[GenerationTaskRun, ...]:
        """Return pending and expired active tasks for a restarted worker."""

        now = _utc_now(now)
        result = await self._session.execute(
            select(GenerationTaskRun)
            .where(
                GenerationTaskRun.experiment_id == experiment_id,
                (
                    GenerationTaskRun.status.in_(_LEASEABLE_STATUSES)
                    | (
                        GenerationTaskRun.status.in_(_ACTIVE_STATUSES)
                        & GenerationTaskRun.lease_expires_at.is_not(None)
                        & (GenerationTaskRun.lease_expires_at <= now)
                    )
                ),
            )
            .order_by(GenerationTaskRun.created_at, GenerationTaskRun.id)
        )
        return tuple(result.scalars())

    async def append_event(
        self,
        *,
        task_run_id: str,
        attempt_id: str | None,
        idempotency_key: str,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        occurred_at: datetime.datetime | None = None,
    ) -> GenerationTaskEvent:
        """Append an event or return the exact prior event for an idempotent replay."""

        task = await self._task_for_update(task_run_id)
        if task is None:
            raise LookupError("generation task does not exist")
        if attempt_id is not None:
            attempt = await self._session.get(GenerationTaskAttempt, attempt_id)
            if attempt is None or attempt.task_run_id != task_run_id:
                raise ValueError("event attempt does not belong to task")
        event = await self._append_event(
            task=task,
            attempt_id=attempt_id,
            idempotency_key=idempotency_key,
            event_type=event_type,
            payload=payload,
            occurred_at=_utc_now(occurred_at),
        )
        await self._session.flush()
        return event

    async def request_retry(
        self,
        *,
        task_run_id: str,
        requested_by: str,
        now: datetime.datetime | None = None,
    ) -> GenerationTaskRun:
        """Explicitly authorize another attempt and reopen unblocked descendants.

        Failed and expired tasks remain terminal until this method is called.  A
        repeat request for the same already-pending retry is idempotent.
        """

        now = _utc_now(now)
        if not requested_by.strip():
            raise ValueError("retry requester is required")
        task = await self._task_for_update(task_run_id)
        if task is None:
            raise LookupError("generation task does not exist")
        retry_key = f"retry-authorized:{task.attempt}"
        if task.status == "pending":
            existing = (
                await self._session.execute(
                    select(GenerationTaskEvent).where(
                        GenerationTaskEvent.task_run_id == task.id,
                        GenerationTaskEvent.idempotency_key == retry_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return task
            raise ValueError("task is pending but was not opened by an explicit retry")
        if task.status != "failed":
            raise ValueError("only failed tasks can be explicitly retried")
        if task.attempt >= task.max_attempts:
            raise ValueError("task has exhausted its maximum attempts")
        task.status = "pending"
        task.completed_at = None
        task.error_message = None
        await self._append_event(
            task=task,
            attempt_id=None,
            idempotency_key=retry_key,
            event_type="retry.authorized",
            payload={"requested_by": requested_by, "next_attempt_number": task.attempt + 1},
            occurred_at=now,
        )
        await self._reopen_unblocked_descendants(
            experiment_id=task.experiment_id,
            root_node_id=task.node_id,
            root_attempt=task.attempt,
            now=now,
        )
        await self._session.flush()
        return task

    async def finalize_attempt(
        self,
        *,
        task_run_id: str,
        attempt_id: str,
        lease_owner: str,
        outcome: TaskOutcome,
        completed_at: datetime.datetime | None = None,
        wall_duration_ms: int | None = None,
        provider_latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        provider_cost_usd: str | None = None,
        runtime_session_id: str | None = None,
        runtime_message_id: str | None = None,
        result: Mapping[str, object] | None = None,
        metrics: Mapping[str, object] | None = None,
        error_message: str | None = None,
    ) -> GenerationTaskRun:
        """Finalize exactly the current worker lease without erasing old attempts."""

        if outcome not in {"succeeded", "failed", "cancelled", "skipped"}:
            raise ValueError("unsupported task outcome")
        completed_at = _utc_now(completed_at)
        _require_nonnegative(wall_duration_ms, "wall_duration_ms")
        _require_nonnegative(provider_latency_ms, "provider_latency_ms")
        _require_nonnegative(input_tokens, "input_tokens")
        _require_nonnegative(output_tokens, "output_tokens")
        _require_nonnegative(cache_read_tokens, "cache_read_tokens")
        provider_cost_usd = _validate_cost(provider_cost_usd)

        task = await self._task_for_update(task_run_id)
        if task is None:
            raise LookupError("generation task does not exist")
        attempt = await self._session.get(GenerationTaskAttempt, attempt_id)
        if attempt is None or attempt.task_run_id != task.id:
            raise LookupError("generation task attempt does not exist")
        if task.status not in _ACTIVE_STATUSES or attempt.status not in _ACTIVE_STATUSES:
            raise ValueError("attempt is already terminal")
        if task.lease_owner != lease_owner or attempt.lease_owner != lease_owner:
            raise PermissionError("lease owner does not own this task attempt")
        if task.lease_expires_at is not None and _database_utc(task.lease_expires_at) <= completed_at:
            raise ValueError("lease expired; recover before finalizing")

        attempt.status = outcome
        attempt.completed_at = completed_at
        attempt.wall_duration_ms = wall_duration_ms
        attempt.provider_latency_ms = provider_latency_ms
        attempt.input_tokens = input_tokens
        attempt.output_tokens = output_tokens
        attempt.cache_read_tokens = cache_read_tokens
        attempt.provider_cost_usd = provider_cost_usd
        attempt.runtime_session_id = runtime_session_id
        attempt.runtime_message_id = runtime_message_id
        attempt.result_json = dict(result) if result is not None else None
        attempt.metrics_json = dict(metrics) if metrics is not None else None
        attempt.error_message = _limited_error(error_message)

        task.status = self._next_task_status(task=task, outcome=outcome)
        task.lease_owner = None
        task.lease_expires_at = None
        task.completed_at = completed_at
        task.wall_duration_ms = wall_duration_ms
        task.provider_latency_ms = provider_latency_ms
        task.input_tokens = input_tokens
        task.output_tokens = output_tokens
        task.cache_read_tokens = cache_read_tokens
        task.provider_cost_usd = provider_cost_usd
        task.runtime_session_id = runtime_session_id
        task.runtime_message_id = runtime_message_id
        task.result_json = dict(result) if result is not None else None
        task.metrics_json = dict(metrics) if metrics is not None else None
        task.error_message = _limited_error(error_message)
        await self._append_event(
            task=task,
            attempt_id=attempt.id,
            idempotency_key=f"attempt-finished:{attempt.id}",
            event_type="attempt.finished",
            payload={"outcome": outcome, "next_status": task.status},
            occurred_at=completed_at,
        )
        await self._session.flush()
        return task

    async def _dependencies_succeeded(self, task: GenerationTaskRun) -> bool:
        states = await self._dependency_states(task)
        return bool(states is not None) and all(status == "succeeded" for status in states.values())

    async def _dependency_states(self, task: GenerationTaskRun) -> dict[str, str | None]:
        dependencies = tuple(str(node_id) for node_id in (task.dependencies_json or ()))
        if not dependencies:
            return {}
        rows = (
            await self._session.execute(
                select(GenerationTaskRun.node_id, GenerationTaskRun.status).where(
                    GenerationTaskRun.experiment_id == task.experiment_id,
                    GenerationTaskRun.node_id.in_(dependencies),
                )
            )
        ).all()
        found = {node_id: status for node_id, status in rows}
        return {node_id: found.get(node_id) for node_id in dependencies}

    async def _reopen_unblocked_descendants(
        self,
        *,
        experiment_id: str,
        root_node_id: str,
        root_attempt: int,
        now: datetime.datetime,
    ) -> None:
        """Reopen only descendants whose every dependency is now non-terminal."""

        changed = True
        while changed:
            changed = False
            skipped = (
                await self._session.execute(
                    select(GenerationTaskRun)
                    .where(
                        GenerationTaskRun.experiment_id == experiment_id,
                        GenerationTaskRun.status == "skipped",
                    )
                    .with_for_update()
                )
            ).scalars().all()
            for descendant in skipped:
                states = await self._dependency_states(descendant)
                if any(status is None or status in _BLOCKING_DEPENDENCY_STATUSES for status in states.values()):
                    continue
                descendant.status = "pending"
                descendant.completed_at = None
                descendant.error_message = None
                await self._append_event(
                    task=descendant,
                    attempt_id=None,
                    idempotency_key=(
                        f"dependency-reopened:{root_node_id}:{root_attempt}"
                    ),
                    event_type="task.reopened",
                    payload={
                        "retry_root_node_id": root_node_id,
                        "retry_root_attempt": root_attempt,
                    },
                    occurred_at=now,
                )
                changed = True

    async def _active_attempt(self, task_run_id: str) -> GenerationTaskAttempt | None:
        return (
            await self._session.execute(
                select(GenerationTaskAttempt)
                .where(
                    GenerationTaskAttempt.task_run_id == task_run_id,
                    GenerationTaskAttempt.status.in_(_ACTIVE_STATUSES),
                )
                .order_by(GenerationTaskAttempt.attempt_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _task_for_update(self, task_run_id: str) -> GenerationTaskRun | None:
        return (
            await self._session.execute(
                select(GenerationTaskRun)
                .where(GenerationTaskRun.id == task_run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _append_event(
        self,
        *,
        task: GenerationTaskRun,
        attempt_id: str | None,
        idempotency_key: str,
        event_type: str,
        payload: Mapping[str, object] | None,
        occurred_at: datetime.datetime,
    ) -> GenerationTaskEvent:
        if not idempotency_key.strip() or not event_type.strip():
            raise ValueError("event idempotency key and type are required")
        existing = (
            await self._session.execute(
                select(GenerationTaskEvent).where(
                    GenerationTaskEvent.task_run_id == task.id,
                    GenerationTaskEvent.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        normalized_payload = dict(payload) if payload is not None else None
        if existing is not None:
            if (
                existing.attempt_id != attempt_id
                or existing.event_type != event_type
                or _canonical_json(existing.payload_json) != _canonical_json(normalized_payload)
            ):
                raise ValueError("event idempotency key was replayed with different content")
            return existing
        sequence = (
            await self._session.scalar(
                select(func.max(GenerationTaskEvent.sequence_number)).where(
                    GenerationTaskEvent.task_run_id == task.id
                )
            )
            or 0
        ) + 1
        event = GenerationTaskEvent(
            task_run_id=task.id,
            attempt_id=attempt_id,
            sequence_number=sequence,
            idempotency_key=idempotency_key,
            event_type=event_type,
            payload_json=normalized_payload,
            occurred_at=occurred_at,
        )
        self._session.add(event)
        return event

    @staticmethod
    def _next_task_status(*, task: GenerationTaskRun, outcome: TaskOutcome) -> str:
        if outcome == "succeeded":
            return "succeeded"
        if outcome in {"cancelled", "skipped"}:
            return outcome
        # Failure is deliberately terminal until ``request_retry`` authorizes a
        # new dispatch.  Provider submission is not inherently idempotent.
        return "failed"


def _utc_now(value: datetime.datetime | None) -> datetime.datetime:
    now = value or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return now.astimezone(datetime.timezone.utc)


def _database_utc(value: datetime.datetime) -> datetime.datetime:
    """Normalize a persisted timestamp for SQLite, which drops tzinfo."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _require_nonnegative(value: int | None, field: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"{field} must be a non-negative integer")


def _limited_error(value: str | None) -> str | None:
    return value[:4000] if value else None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _execution_idempotency_key(task_run_id: str, attempt_number: int) -> str:
    """Stable retry-safe runtime dispatch identity, recoverable from durable state."""

    return f"{task_run_id}:{attempt_number}"


def _validate_cost(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("provider_cost_usd must be a decimal string")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("provider_cost_usd must be a finite non-negative decimal") from exc
    if not decimal.is_finite() or decimal < 0:
        raise ValueError("provider_cost_usd must be a finite non-negative decimal")
    return value
