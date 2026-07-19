"""Repository for persisted Nexus harness workflow runs and activity events."""

from __future__ import annotations

import datetime
import re

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.harness import (
    HarnessActivityEventRow,
    HarnessWorkflowChildObservationRow,
    HarnessWorkflowChildReceiptRow,
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
    WorkflowChildReceipt,
    WorkflowInterrupt,
    WorkflowInterruptCreate,
    WorkflowInterruptStatus,
)
from src.harness.workflow_ledger import (
    WorkflowChildCancellationState,
    WorkflowChildStatus,
    WorkflowObservationSource,
    decide_child_observation,
    normalize_child_status,
    validate_parent_transition,
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
            child_receipts=[],
        )
        self._session.add(row)
        await self._session.flush()
        return _to_workflow_run(row)

    @staticmethod
    def _apply_owner_visibility(stmt, scope: ProjectScope, owner_user_id: int | None):
        if owner_user_id is None:
            return HarnessWorkflowRepository._apply_scope(stmt, scope)
        owner = HarnessWorkflowRunRow.user_id == owner_user_id
        if scope is None:
            return stmt.where(owner)
        pending_or_unbound = or_(
            HarnessWorkflowRunRow.project_id == "__pending_project_scope__",
            HarnessWorkflowRunRow.project_id.is_(None),
        )
        authorized_project = HarnessWorkflowRunRow.project_id.in_(scope)
        return stmt.where(and_(owner, or_(authorized_project, pending_or_unbound)))

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

    async def list_owned_runs(
        self,
        owner_user_id: int,
        *,
        conversation_id: int | None = None,
        limit: int = 50,
    ) -> list[WorkflowRun]:
        """List only one owner's runs before callers apply current project scope."""
        stmt = select(HarnessWorkflowRunRow).where(
            HarnessWorkflowRunRow.user_id == owner_user_id
        )
        if conversation_id is not None:
            stmt = stmt.where(HarnessWorkflowRunRow.conversation_id == conversation_id)
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
        if update_payload.status is not None:
            validate_parent_transition(WorkflowRunStatus(row.status), update_payload.status)
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

    async def bind_child_receipt(
        self,
        scope: ProjectScope,
        *,
        workflow_run_id: int,
        provider_id: str,
        child_kind: str,
        external_run_id: str | int,
        project_id: str | None,
        provider_status: str,
        source: WorkflowObservationSource = WorkflowObservationSource.TRIGGER,
        observed_at: datetime.datetime | None = None,
        provider_started_at: datetime.datetime | None = None,
        provider_finished_at: datetime.datetime | None = None,
        provider_timestamp: datetime.datetime | None = None,
        owner_user_id: int | None = None,
    ) -> WorkflowChildReceipt:
        """Bind one exact provider child to one authorized parent idempotently."""

        await self.get_run(scope, workflow_run_id, owner_user_id=owner_user_id)
        external_id = str(external_run_id).strip()
        if not external_id:
            raise ValueError("external_run_id is required")
        existing = await self._get_child_row(
            workflow_run_id=workflow_run_id,
            provider_id=provider_id,
            child_kind=child_kind,
        )
        if existing is not None:
            if existing.external_run_id != external_id:
                raise HarnessWorkflowConflictError(
                    "Workflow is already bound to a different provider child"
                )
            return _to_child_receipt(existing)
        duplicate = (
            await self._session.execute(
                select(HarnessWorkflowChildReceiptRow).where(
                    HarnessWorkflowChildReceiptRow.provider_id == provider_id,
                    HarnessWorkflowChildReceiptRow.external_run_id == external_id,
                )
            )
        ).scalars().first()
        if duplicate is not None:
            raise HarnessWorkflowConflictError(
                "Provider child is already bound to another workflow"
            )

        now = observed_at or _now()
        normalized = normalize_child_status(provider_status)
        row = HarnessWorkflowChildReceiptRow(
            workflow_run_id=workflow_run_id,
            provider_id=provider_id,
            child_kind=child_kind,
            external_run_id=external_id,
            project_id=project_id,
            status=normalized.value,
            provider_status=_bounded(provider_status, 80) or "unknown",
            provider_started_at=provider_started_at,
            provider_finished_at=provider_finished_at,
            provider_timestamp=provider_timestamp,
            last_observed_at=now,
            last_observation_source=source.value,
            cancellation_state=WorkflowChildCancellationState.NONE.value,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            concurrent = await self._get_child_row(
                workflow_run_id=workflow_run_id,
                provider_id=provider_id,
                child_kind=child_kind,
            )
            if concurrent is None or concurrent.external_run_id != external_id:
                raise HarnessWorkflowConflictError(
                    "Provider child binding lost an idempotency race"
                ) from None
            return _to_child_receipt(concurrent)
        return _to_child_receipt(row)

    async def get_child_receipt(
        self,
        scope: ProjectScope,
        workflow_run_id: int,
        *,
        provider_id: str | None = None,
        child_kind: str | None = None,
        owner_user_id: int | None = None,
    ) -> WorkflowChildReceipt | None:
        await self.get_run(scope, workflow_run_id, owner_user_id=owner_user_id)
        stmt = select(HarnessWorkflowChildReceiptRow).where(
            HarnessWorkflowChildReceiptRow.workflow_run_id == workflow_run_id
        )
        if provider_id is not None:
            stmt = stmt.where(HarnessWorkflowChildReceiptRow.provider_id == provider_id)
        if child_kind is not None:
            stmt = stmt.where(HarnessWorkflowChildReceiptRow.child_kind == child_kind)
        rows = (await self._session.execute(stmt)).scalars().all()
        if len(rows) > 1:
            raise HarnessWorkflowConflictError("Workflow has more than one matching provider child")
        return _to_child_receipt(rows[0]) if rows else None

    async def get_child_receipt_by_external(
        self,
        *,
        provider_id: str,
        external_run_id: str | int,
    ) -> WorkflowChildReceipt | None:
        row = (
            await self._session.execute(
                select(HarnessWorkflowChildReceiptRow).where(
                    HarnessWorkflowChildReceiptRow.provider_id == provider_id,
                    HarnessWorkflowChildReceiptRow.external_run_id == str(external_run_id),
                )
            )
        ).scalars().first()
        return _to_child_receipt(row) if row is not None else None

    async def list_nonterminal_child_receipts(
        self,
        *,
        provider_id: str | None = None,
        child_kind: str | None = None,
        limit: int = 50,
    ) -> list[WorkflowChildReceipt]:
        stmt = select(HarnessWorkflowChildReceiptRow).where(
            HarnessWorkflowChildReceiptRow.status.not_in(
                [
                    WorkflowChildStatus.SUCCEEDED.value,
                    WorkflowChildStatus.FAILED.value,
                    WorkflowChildStatus.CANCELLED.value,
                ]
            )
        )
        if provider_id is not None:
            stmt = stmt.where(HarnessWorkflowChildReceiptRow.provider_id == provider_id)
        if child_kind is not None:
            stmt = stmt.where(HarnessWorkflowChildReceiptRow.child_kind == child_kind)
        stmt = stmt.order_by(HarnessWorkflowChildReceiptRow.last_observed_at.asc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_child_receipt(row) for row in rows]

    async def record_child_observation(
        self,
        *,
        child_receipt_id: int,
        idempotency_key: str,
        source: WorkflowObservationSource,
        provider_status: str,
        observed_at: datetime.datetime | None = None,
        provider_timestamp: datetime.datetime | None = None,
        provider_started_at: datetime.datetime | None = None,
        provider_finished_at: datetime.datetime | None = None,
        error_message: str | None = None,
    ) -> tuple[WorkflowChildReceipt, bool]:
        """Apply one provider fact exactly once and reject stale facts."""

        row = await self._child_row_by_id(child_receipt_id, for_update=True)
        existing_observation = (
            await self._session.execute(
                select(HarnessWorkflowChildObservationRow.id).where(
                    HarnessWorkflowChildObservationRow.idempotency_key == idempotency_key
                )
            )
        ).scalar_one_or_none()
        if existing_observation is not None:
            return _to_child_receipt(row), False

        normalized = normalize_child_status(provider_status)
        decision = decide_child_observation(
            current_status=WorkflowChildStatus(row.status),
            current_provider_timestamp=_as_utc(row.provider_timestamp),
            observed_status=normalized,
            observed_provider_timestamp=provider_timestamp,
        )
        now = observed_at or _now()
        observation = HarnessWorkflowChildObservationRow(
            child_receipt_id=row.id,
            idempotency_key=_bounded(idempotency_key, 255) or "invalid",
            source=source.value,
            provider_status=_bounded(provider_status, 80) or "unknown",
            normalized_status=normalized.value,
            provider_timestamp=provider_timestamp,
            observed_at=now,
            accepted=decision.accepted,
            decision_reason=decision.reason,
            error_message=_safe_error_text(error_message),
        )
        try:
            async with self._session.begin_nested():
                self._session.add(observation)
                await self._session.flush()
        except IntegrityError:
            row = await self._child_row_by_id(child_receipt_id)
            return _to_child_receipt(row), False

        values: dict = {
            "last_observed_at": now,
            "last_observation_source": source.value,
            "updated_at": now,
            "version": HarnessWorkflowChildReceiptRow.version + 1,
        }
        if decision.accepted:
            values.update(
                status=decision.status.value,
                provider_status=_bounded(provider_status, 80) or "unknown",
                provider_timestamp=provider_timestamp,
                error_message=_safe_error_text(error_message),
            )
            if provider_started_at is not None:
                values["provider_started_at"] = provider_started_at
            if provider_finished_at is not None:
                values["provider_finished_at"] = provider_finished_at
        result = await self._session.execute(
            update(HarnessWorkflowChildReceiptRow)
            .where(
                HarnessWorkflowChildReceiptRow.id == row.id,
                HarnessWorkflowChildReceiptRow.version == row.version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise HarnessWorkflowConflictError("Child observation state changed; reload and retry")
        await self._session.flush()
        return _to_child_receipt(await self._child_row_by_id(row.id)), decision.accepted

    async def record_child_cancellation(
        self,
        *,
        child_receipt_id: int,
        idempotency_key: str,
        state: WorkflowChildCancellationState,
        provider_status: str | None = None,
        requested_at: datetime.datetime | None = None,
    ) -> tuple[WorkflowChildReceipt, bool]:
        """Persist request/result provenance without claiming a provider fact."""

        row = await self._child_row_by_id(child_receipt_id, for_update=True)
        key = _bounded(idempotency_key, 255)
        if row.cancellation_idempotency_key is not None:
            if row.cancellation_idempotency_key != key:
                raise HarnessWorkflowConflictError("A different cancellation is already recorded")
            if row.cancellation_state == state.value:
                return _to_child_receipt(row), False
        now = requested_at or _now()
        values = {
            "cancellation_idempotency_key": key,
            "cancellation_state": state.value,
            "updated_at": now,
            "version": HarnessWorkflowChildReceiptRow.version + 1,
        }
        if row.cancellation_requested_at is None:
            values["cancellation_requested_at"] = now
        if state is not WorkflowChildCancellationState.REQUESTED:
            values["cancellation_provider_status"] = _bounded(provider_status, 80)
            values["cancellation_recorded_at"] = now
        result = await self._session.execute(
            update(HarnessWorkflowChildReceiptRow)
            .where(
                HarnessWorkflowChildReceiptRow.id == row.id,
                HarnessWorkflowChildReceiptRow.version == row.version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise HarnessWorkflowConflictError("Child cancellation state changed; reload and retry")
        await self._session.flush()
        return _to_child_receipt(await self._child_row_by_id(row.id)), True

    async def _get_child_row(
        self,
        *,
        workflow_run_id: int,
        provider_id: str,
        child_kind: str,
    ) -> HarnessWorkflowChildReceiptRow | None:
        return (
            await self._session.execute(
                select(HarnessWorkflowChildReceiptRow).where(
                    HarnessWorkflowChildReceiptRow.workflow_run_id == workflow_run_id,
                    HarnessWorkflowChildReceiptRow.provider_id == provider_id,
                    HarnessWorkflowChildReceiptRow.child_kind == child_kind,
                )
            )
        ).scalars().first()

    async def _child_row_by_id(
        self,
        child_receipt_id: int,
        *,
        for_update: bool = False,
    ) -> HarnessWorkflowChildReceiptRow:
        stmt = select(HarnessWorkflowChildReceiptRow).where(
            HarnessWorkflowChildReceiptRow.id == child_receipt_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise HarnessRunNotFoundError(f"Unknown workflow_child_receipt_id: {child_receipt_id}")
        return row

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
        children=[_to_child_receipt(child) for child in row.child_receipts],
    )


def _to_child_receipt(row: HarnessWorkflowChildReceiptRow) -> WorkflowChildReceipt:
    return WorkflowChildReceipt(
        id=row.id,
        workflow_run_id=row.workflow_run_id,
        provider_id=row.provider_id,
        child_kind=row.child_kind,
        external_run_id=row.external_run_id,
        project_id=row.project_id,
        status=row.status,
        provider_status=row.provider_status,
        version=row.version,
        provider_started_at=_as_utc(row.provider_started_at),
        provider_finished_at=_as_utc(row.provider_finished_at),
        provider_timestamp=_as_utc(row.provider_timestamp),
        last_observed_at=_as_utc(row.last_observed_at),
        last_observation_source=row.last_observation_source,
        error_message=row.error_message,
        cancellation_state=row.cancellation_state,
        cancellation_requested_at=_as_utc(row.cancellation_requested_at),
        cancellation_provider_status=row.cancellation_provider_status,
        cancellation_recorded_at=_as_utc(row.cancellation_recorded_at),
    )


def _run_update_values(payload: WorkflowRunUpdate) -> dict:
    values = payload.model_dump(exclude_unset=True)
    if "status" in values:
        values["status"] = payload.status.value
    for key in ("artifact_links", "budget", "evidence_refs", "artifact_receipts", "runtime_metadata"):
        if key in values and not values[key]:
            values[key] = None
    return values


def _bounded(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).strip().split())[:limit]


def _safe_error_text(value: str | None) -> str | None:
    bounded = _bounded(value, 500)
    if bounded is None:
        return None
    bounded = re.sub(
        r"(?i)\b(authorization\s*[:=]\s*bearer|bearer|basic)\s+[A-Za-z0-9._+/=\-]{8,}",
        "[REDACTED]",
        bounded,
    )
    bounded = re.sub(
        r"(?i)\b(access_token|refresh_token|api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        bounded,
    )
    return bounded


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
