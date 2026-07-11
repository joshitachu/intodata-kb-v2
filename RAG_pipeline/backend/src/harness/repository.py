"""Repository for persisted Nexus harness workflow runs and activity events."""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness import HarnessActivityEventRow, HarnessWorkflowRunRow
from src.harness import HarnessRunNotFoundError, ProjectScope
from src.harness.models import (
    ActivityEvent,
    ActivityEventCreate,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)


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

    async def get_run(self, scope: ProjectScope, run_id: int) -> WorkflowRun:
        if self._denies_all(scope):
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")
        stmt = select(HarnessWorkflowRunRow).where(HarnessWorkflowRunRow.id == run_id)
        stmt = self._apply_scope(stmt, scope)
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
        limit: int = 50,
    ) -> list[WorkflowRun]:
        if self._denies_all(scope):
            return []
        stmt = select(HarnessWorkflowRunRow)
        stmt = self._apply_scope(stmt, scope)
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

    async def update_run(self, scope: ProjectScope, run_id: int, update: WorkflowRunUpdate) -> WorkflowRun:
        if self._denies_all(scope):
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")
        stmt = select(HarnessWorkflowRunRow).where(HarnessWorkflowRunRow.id == run_id)
        stmt = self._apply_scope(stmt, scope)
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")

        if update.status is not None:
            row.status = update.status.value
        if update.output_summary is not None:
            row.output_summary = update.output_summary
        if update.artifact_links is not None:
            row.artifact_links = update.artifact_links or None
        if update.error_message is not None:
            row.error_message = update.error_message
        if update.runtime_provider_id is not None:
            row.runtime_provider_id = update.runtime_provider_id
        if update.runtime_thread_id is not None:
            row.runtime_thread_id = update.runtime_thread_id
        if update.runtime_run_id is not None:
            row.runtime_run_id = update.runtime_run_id
        if update.current_node is not None:
            row.current_node = update.current_node
        if update.resume_token is not None:
            row.resume_token = update.resume_token
        if update.budget is not None:
            row.budget = update.budget or None
        if update.evidence_refs is not None:
            row.evidence_refs = update.evidence_refs or None
        if update.artifact_receipts is not None:
            row.artifact_receipts = update.artifact_receipts or None
        if update.runtime_metadata is not None:
            row.runtime_metadata = update.runtime_metadata or None
        if update.finished_at is not None:
            row.finished_at = update.finished_at

        await self._session.flush()
        return _to_workflow_run(row)

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
