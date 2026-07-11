"""Service helpers for creating and updating harness workflow receipts."""

from __future__ import annotations

import datetime
from typing import Any

from src.harness.models import (
    ActivityEventCreate,
    ActivityEventStatus,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)
from src.harness.registry import CapabilityRegistry
from src.harness.repository import HarnessWorkflowRepository


class HarnessWorkflowService:
    """Thin workflow-run service that validates capability metadata before writes."""

    def __init__(
        self,
        repository: HarnessWorkflowRepository,
        capability_registry: CapabilityRegistry,
    ) -> None:
        self._repository = repository
        self._capability_registry = capability_registry

    async def start_run(
        self,
        *,
        conversation_id: int,
        user_id: int,
        project_id: str,
        intent_mode: str,
        capability_id: str,
        input_summary: str | None = None,
    ) -> WorkflowRun:
        capability = self._capability_registry.require_available(capability_id)
        return await self._repository.create_run(
            WorkflowRunCreate(
                conversation_id=conversation_id,
                user_id=user_id,
                project_id=project_id,
                intent_mode=intent_mode,
                capability_id=capability.id,
                provider_id=capability.provider_id,
                status=WorkflowRunStatus.RUNNING,
                input_summary=input_summary,
            )
        )

    async def record_activity(
        self,
        run: WorkflowRun,
        *,
        event_type: str,
        label: str,
        status: ActivityEventStatus,
        summary: str | None = None,
        safe_details: dict[str, Any] | None = None,
        runtime_node: str | None = None,
        tool_call_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        artifact_receipts: list[dict[str, Any]] | None = None,
    ):
        return await self._repository.add_activity_event(
            ActivityEventCreate(
                workflow_run_id=run.id,
                event_type=event_type,
                label=label,
                status=status,
                provider_id=run.provider_id,
                capability_id=run.capability_id,
                summary=summary,
                safe_details=safe_details or {},
                runtime_node=runtime_node,
                tool_call_id=tool_call_id,
                evidence_refs=evidence_refs or [],
                artifact_receipts=artifact_receipts or [],
            )
        )

    async def mark_triggered(
        self,
        run: WorkflowRun,
        *,
        output_summary: str | None = None,
        artifact_links: list[dict[str, Any]] | None = None,
        runtime_provider_id: str | None = None,
        runtime_thread_id: str | None = None,
        runtime_run_id: str | None = None,
        current_node: str | None = None,
        artifact_receipts: list[dict[str, Any]] | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        return await self._repository.update_run(
            [run.project_id],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.TRIGGERED,
                output_summary=output_summary,
                artifact_links=artifact_links,
                runtime_provider_id=runtime_provider_id,
                runtime_thread_id=runtime_thread_id,
                runtime_run_id=runtime_run_id,
                current_node=current_node,
                artifact_receipts=artifact_receipts,
                runtime_metadata=runtime_metadata,
            ),
        )

    async def mark_interrupted(
        self,
        run: WorkflowRun,
        *,
        output_summary: str | None = None,
        runtime_provider_id: str | None = None,
        runtime_thread_id: str | None = None,
        runtime_run_id: str | None = None,
        current_node: str | None = None,
        resume_token: str | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        return await self._repository.update_run(
            [run.project_id],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.INTERRUPTED,
                output_summary=output_summary,
                runtime_provider_id=runtime_provider_id,
                runtime_thread_id=runtime_thread_id,
                runtime_run_id=runtime_run_id,
                current_node=current_node,
                resume_token=resume_token,
                runtime_metadata=runtime_metadata,
            ),
        )

    async def mark_succeeded(
        self,
        run: WorkflowRun,
        *,
        output_summary: str | None = None,
        artifact_links: list[dict[str, Any]] | None = None,
    ) -> WorkflowRun:
        return await self._repository.update_run(
            [run.project_id],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.SUCCEEDED,
                output_summary=output_summary,
                artifact_links=artifact_links,
                finished_at=datetime.datetime.now(datetime.UTC),
            ),
        )

    async def mark_failed(self, run: WorkflowRun, *, error_message: str) -> WorkflowRun:
        return await self._repository.update_run(
            [run.project_id],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.FAILED,
                error_message=error_message,
                finished_at=datetime.datetime.now(datetime.UTC),
            ),
        )
