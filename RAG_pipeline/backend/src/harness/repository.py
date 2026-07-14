"""Repository for persisted Nexus harness workflow runs and activity events."""

from __future__ import annotations

import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness import (
    HarnessActivityEventRow,
    HarnessWorkflowInterruptRow,
    HarnessWorkflowRunRow,
)
from src.harness import HarnessRunNotFoundError, ProjectScope
from src.harness.models import (
    ActivityEvent,
    ActivityEventCreate,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunStatus,
    WorkflowRunUpdate,
    WorkflowInterrupt,
    WorkflowInterruptCreate,
    WorkflowInterruptStatus,
)


class HarnessWorkflowConflictError(Exception):
    """Raised when an optimistic workflow transition loses a race."""


class HarnessInterruptUnavailableError(Exception):
    """Raised for stale, consumed, expired, or non-owner interrupts."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _as_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


class HarnessWorkflowRepository:
    """Async repository over harness workflow receipt tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _denies_all(scope: ProjectScope) -> bool:
        return scope is not None and len(scope) == 0

    @staticmethod
    def _apply_scope(stmt, scope: ProjectScope):
        if scope is None:
            return stmt
        return stmt.where(HarnessWorkflowRunRow.project_id.in_(scope))

    async def create_run(self, payload: WorkflowRunCreate) -> WorkflowRun:
        started_at = payload.started_at or _now()
        row = HarnessWorkflowRunRow(
            conversation_id=payload.conversation_id,
            user_id=payload.user_id,
            project_id=payload.project_id,
            intent_mode=payload.intent_mode,
            capability_id=payload.capability_id,
            provider_id=payload.provider_id,
            status=payload.status.value,
            input_summary=payload.input_summary,
            output_summary=payload.output_summary,
            artifact_links=payload.artifact_links or None,
            error_message=payload.error_message,
            runtime_provider_id=payload.runtime_provider_id,
            runtime_thread_id=payload.runtime_thread_id,
            runtime_run_id=payload.runtime_run_id,
            current_node=payload.current_node,
            resume_token=payload.resume_token,
            budget=payload.budget or None,
            evidence_refs=payload.evidence_refs or None,
            artifact_receipts=payload.artifact_receipts or None,
            runtime_metadata=payload.runtime_metadata or None,
            started_at=started_at,
            finished_at=payload.finished_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_workflow_run(row)

    @staticmethod
    def _apply_owner_visibility(stmt, scope: ProjectScope, owner_user_id: int | None):
        if owner_user_id is None:
            return HarnessWorkflowRepository._apply_scope(stmt, scope)
        owner_interrupt = HarnessWorkflowRunRow.user_id == owner_user_id
        if scope is None:
            return stmt
        return stmt.where(or_(HarnessWorkflowRunRow.project_id.in_(scope), owner_interrupt))

    async def get_run(
        self,
        scope: ProjectScope,
        run_id: int,
        *,
        owner_user_id: int | None = None,
    ) -> WorkflowRun:
        if self._denies_all(scope) and owner_user_id is None:
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")
        stmt = select(HarnessWorkflowRunRow).where(HarnessWorkflowRunRow.id == run_id)
        stmt = self._apply_owner_visibility(stmt, scope, owner_user_id)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")
        return _to_workflow_run(row)

    async def list_runs(
        self,
        scope: ProjectScope,
        *,
        conversation_id: int | None = None,
        project_id: str | None = None,
        capability_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        owner_user_id: int | None = None,
        limit: int = 50,
    ) -> list[WorkflowRun]:
        if self._denies_all(scope) and owner_user_id is None:
            return []
        stmt = select(HarnessWorkflowRunRow)
        stmt = self._apply_owner_visibility(stmt, scope, owner_user_id)
        if conversation_id is not None:
            stmt = stmt.where(HarnessWorkflowRunRow.conversation_id == conversation_id)
        if project_id is not None:
            stmt = stmt.where(HarnessWorkflowRunRow.project_id == project_id)
        if capability_id is not None:
            stmt = stmt.where(HarnessWorkflowRunRow.capability_id == capability_id)
        if status is not None:
            stmt = stmt.where(HarnessWorkflowRunRow.status == status.value)
        stmt = stmt.order_by(HarnessWorkflowRunRow.started_at.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_workflow_run(row) for row in rows]

    async def update_run(
        self,
        scope: ProjectScope,
        run_id: int,
        update_payload: WorkflowRunUpdate,
        *,
        expected_version: int | None = None,
        owner_user_id: int | None = None,
    ) -> WorkflowRun:
        if self._denies_all(scope) and owner_user_id is None:
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")
        stmt = select(HarnessWorkflowRunRow).where(HarnessWorkflowRunRow.id == run_id)
        stmt = self._apply_owner_visibility(stmt, scope, owner_user_id)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")

        values = _run_update_values(update_payload)
        if update_payload.runtime_metadata is not None:
            merged_metadata = dict(row.runtime_metadata or {})
            merged_metadata.update(update_payload.runtime_metadata)
            values["runtime_metadata"] = merged_metadata
        if expected_version is None:
            values["version"] = HarnessWorkflowRunRow.version + 1
            await self._session.execute(
                update(HarnessWorkflowRunRow)
                .where(HarnessWorkflowRunRow.id == row.id)
                .values(**values)
            )
        else:
            values["version"] = expected_version + 1
            result = await self._session.execute(
                update(HarnessWorkflowRunRow)
                .where(
                    HarnessWorkflowRunRow.id == row.id,
                    HarnessWorkflowRunRow.version == expected_version,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise HarnessWorkflowConflictError("Workflow state changed; reload and retry")
        await self._session.flush()
        refreshed = (
            await self._session.execute(
                select(HarnessWorkflowRunRow).where(HarnessWorkflowRunRow.id == row.id)
            )
        ).scalar_one()
        return _to_workflow_run(refreshed)

    async def create_interrupt(self, payload: WorkflowInterruptCreate) -> WorkflowInterrupt:
        row = HarnessWorkflowInterruptRow(
            workflow_run_id=payload.workflow_run_id,
            owner_user_id=payload.owner_user_id,
            interrupt_type=payload.interrupt_type.value,
            status=WorkflowInterruptStatus.PENDING.value,
            prompt=payload.prompt,
            options=payload.options or None,
            policy_snapshot=payload.policy_snapshot or None,
            scope_snapshot=payload.scope_snapshot or None,
            argument_hash=payload.argument_hash,
            expires_at=payload.expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_workflow_interrupt(row)

    async def get_interrupt_for_owner(
        self, interrupt_id: int, workflow_run_id: int, owner_user_id: int
    ) -> WorkflowInterrupt:
        stmt = select(HarnessWorkflowInterruptRow).where(
            HarnessWorkflowInterruptRow.id == interrupt_id,
            HarnessWorkflowInterruptRow.workflow_run_id == workflow_run_id,
            HarnessWorkflowInterruptRow.owner_user_id == owner_user_id,
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise HarnessRunNotFoundError("Workflow interrupt not found")
        return _to_workflow_interrupt(row)

    async def list_pending_interrupts_for_owner(
        self, workflow_run_id: int, owner_user_id: int
    ) -> list[WorkflowInterrupt]:
        stmt = (
            select(HarnessWorkflowInterruptRow)
            .where(
                HarnessWorkflowInterruptRow.workflow_run_id == workflow_run_id,
                HarnessWorkflowInterruptRow.owner_user_id == owner_user_id,
                HarnessWorkflowInterruptRow.status == WorkflowInterruptStatus.PENDING.value,
                HarnessWorkflowInterruptRow.expires_at > _now(),
            )
            .order_by(HarnessWorkflowInterruptRow.id.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_workflow_interrupt(row) for row in rows]

    async def consume_interrupt(
        self,
        interrupt_id: int,
        workflow_run_id: int,
        owner_user_id: int,
        *,
        response_summary: str,
    ) -> WorkflowInterrupt:
        now = _now()
        result = await self._session.execute(
            update(HarnessWorkflowInterruptRow)
            .where(
                HarnessWorkflowInterruptRow.id == interrupt_id,
                HarnessWorkflowInterruptRow.workflow_run_id == workflow_run_id,
                HarnessWorkflowInterruptRow.owner_user_id == owner_user_id,
                HarnessWorkflowInterruptRow.status == WorkflowInterruptStatus.PENDING.value,
                HarnessWorkflowInterruptRow.expires_at > now,
            )
            .values(
                status=WorkflowInterruptStatus.CONSUMED.value,
                response_summary=response_summary,
                consumed_at=now,
            )
        )
        if result.rowcount != 1:
            raise HarnessInterruptUnavailableError("Workflow interrupt is unavailable")
        await self._session.flush()
        row = (
            await self._session.execute(
                select(HarnessWorkflowInterruptRow).where(
                    HarnessWorkflowInterruptRow.id == interrupt_id
                )
            )
        ).scalar_one()
        return _to_workflow_interrupt(row)

    async def cancel_pending_interrupts(self, workflow_run_id: int, owner_user_id: int) -> None:
        await self._session.execute(
            update(HarnessWorkflowInterruptRow)
            .where(
                HarnessWorkflowInterruptRow.workflow_run_id == workflow_run_id,
                HarnessWorkflowInterruptRow.owner_user_id == owner_user_id,
                HarnessWorkflowInterruptRow.status == WorkflowInterruptStatus.PENDING.value,
            )
            .values(status=WorkflowInterruptStatus.CANCELLED.value)
        )
        await self._session.flush()

    async def add_activity_event(self, event: ActivityEventCreate) -> ActivityEvent:
        sequence_stmt = select(func.max(HarnessActivityEventRow.sequence)).where(
            HarnessActivityEventRow.workflow_run_id == event.workflow_run_id
        )
        max_sequence = (await self._session.execute(sequence_stmt)).scalar_one_or_none()
        row = HarnessActivityEventRow(
            workflow_run_id=event.workflow_run_id,
            sequence=(max_sequence or 0) + 1,
            event_type=event.event_type,
            label=event.label,
            status=event.status.value,
            provider_id=event.provider_id,
            capability_id=event.capability_id,
            summary=event.summary,
            safe_details=event.safe_details or None,
            runtime_node=event.runtime_node,
            tool_call_id=event.tool_call_id,
            evidence_refs=event.evidence_refs or None,
            artifact_receipts=event.artifact_receipts or None,
            occurred_at=event.occurred_at or _now(),
        )
        self._session.add(row)
        await self._session.flush()
        return _to_activity_event(row)

    async def list_activity_events(self, scope: ProjectScope, workflow_run_id: int) -> list[ActivityEvent]:
        if self._denies_all(scope):
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {workflow_run_id}")
        run_stmt = select(HarnessWorkflowRunRow.id).where(HarnessWorkflowRunRow.id == workflow_run_id)
        run_stmt = self._apply_scope(run_stmt, scope)
        run_id = (await self._session.execute(run_stmt)).scalar_one_or_none()
        if run_id is None:
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {workflow_run_id}")

        stmt = (
            select(HarnessActivityEventRow)
            .where(HarnessActivityEventRow.workflow_run_id == workflow_run_id)
            .order_by(HarnessActivityEventRow.sequence.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_activity_event(row) for row in rows]


def _to_workflow_run(row: HarnessWorkflowRunRow) -> WorkflowRun:
    return WorkflowRun(
        id=row.id,
        conversation_id=row.conversation_id,
        user_id=row.user_id,
        project_id=row.project_id,
        intent_mode=row.intent_mode,
        capability_id=row.capability_id,
        provider_id=row.provider_id,
        status=WorkflowRunStatus(row.status),
        version=row.version,
        input_summary=row.input_summary,
        output_summary=row.output_summary,
        artifact_links=row.artifact_links or [],
        error_message=row.error_message,
        runtime_provider_id=row.runtime_provider_id,
        runtime_thread_id=row.runtime_thread_id,
        runtime_run_id=row.runtime_run_id,
        current_node=row.current_node,
        resume_token=row.resume_token,
        budget=row.budget or {},
        evidence_refs=row.evidence_refs or [],
        artifact_receipts=row.artifact_receipts or [],
        runtime_metadata=row.runtime_metadata or {},
        started_at=_as_utc(row.started_at),
        finished_at=_as_utc(row.finished_at),
        cancelled_at=_as_utc(row.cancelled_at),
        cancelled_by_user_id=row.cancelled_by_user_id,
        cancellation_reason=row.cancellation_reason,
    )


def _run_update_values(payload: WorkflowRunUpdate) -> dict:
    values = payload.model_dump(exclude_unset=True)
    if "status" in values:
        values["status"] = payload.status.value
    for key in ("artifact_links", "budget", "evidence_refs", "artifact_receipts", "runtime_metadata"):
        if key in values and not values[key]:
            values[key] = None
    return values


def _to_workflow_interrupt(row: HarnessWorkflowInterruptRow) -> WorkflowInterrupt:
    return WorkflowInterrupt(
        id=row.id,
        workflow_run_id=row.workflow_run_id,
        owner_user_id=row.owner_user_id,
        interrupt_type=row.interrupt_type,
        status=row.status,
        prompt=row.prompt,
        options=row.options or [],
        policy_snapshot=row.policy_snapshot or {},
        scope_snapshot=row.scope_snapshot or {},
        argument_hash=row.argument_hash,
        response_summary=row.response_summary,
        expires_at=_as_utc(row.expires_at),
        consumed_at=_as_utc(row.consumed_at),
    )


def _to_activity_event(row: HarnessActivityEventRow) -> ActivityEvent:
    return ActivityEvent(
        id=row.id,
        workflow_run_id=row.workflow_run_id,
        sequence=row.sequence,
        event_type=row.event_type,
        label=row.label,
        status=row.status,
        provider_id=row.provider_id,
        capability_id=row.capability_id,
        summary=row.summary,
        safe_details=row.safe_details or {},
        runtime_node=row.runtime_node,
        tool_call_id=row.tool_call_id,
        evidence_refs=row.evidence_refs or [],
        artifact_receipts=row.artifact_receipts or [],
        occurred_at=_as_utc(row.occurred_at),
    )
