"""Service helpers for creating and updating harness workflow receipts."""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from src.harness.models import (
    ActivityEventCreate,
    ActivityEventStatus,
    WorkflowRun,
    WorkflowRunCreate,
    WorkflowRunStatus,
    WorkflowRunUpdate,
    WorkflowInterrupt,
    WorkflowInterruptCreate,
    WorkflowInterruptType,
)
from src.harness.registry import CapabilityRegistry
from src.harness.repository import HarnessWorkflowRepository
from src.harness.repository import HarnessWorkflowConflictError

_MAX_ACTIVITY_LABEL = 255
_MAX_ACTIVITY_EVENT_TYPE = 80
_MAX_ACTIVITY_STATUS_SUMMARY = 2000
_MAX_RUNTIME_NODE = 120
_MAX_TOOL_CALL_ID = 255


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
        project_id: str | None,
        intent_mode: str,
        capability_id: str,
        input_summary: str | None = None,
    ) -> WorkflowRun:
        capability = self._capability_registry.require_available(capability_id)
        pack_snapshot = capability.metadata.get("nexus_pack_snapshot")
        pack_governance = _pack_governance_receipt(
            pack_snapshot,
            capability.metadata.get("nexus_pack_presentation"),
            capability.metadata.get("nexus_private_analysis_packs"),
        )
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
                runtime_metadata={
                    **({"pack_snapshot": pack_snapshot} if isinstance(pack_snapshot, dict) else {}),
                    **({"pack_governance": pack_governance} if pack_governance else {}),
                },
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
                event_type=_bounded(event_type, _MAX_ACTIVITY_EVENT_TYPE) or "workflow.event",
                label=_bounded(label, _MAX_ACTIVITY_LABEL) or "Workflow activity",
                status=status,
                provider_id=run.provider_id,
                capability_id=run.capability_id,
                summary=_bounded(summary, _MAX_ACTIVITY_STATUS_SUMMARY),
                safe_details=_with_pack_governance(safe_details or {}, run.runtime_metadata),
                runtime_node=_bounded(runtime_node, _MAX_RUNTIME_NODE),
                tool_call_id=_bounded(tool_call_id, _MAX_TOOL_CALL_ID),
                evidence_refs=evidence_refs or [],
                artifact_receipts=_with_pack_receipt(artifact_receipts, run.runtime_metadata),
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
            [run.project_id] if run.project_id is not None else None,
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.TRIGGERED,
                output_summary=output_summary,
                artifact_links=artifact_links,
                runtime_provider_id=runtime_provider_id,
                runtime_thread_id=runtime_thread_id,
                runtime_run_id=runtime_run_id,
                current_node=current_node,
                artifact_receipts=_with_pack_receipt(artifact_receipts, run.runtime_metadata),
                runtime_metadata=_merge_runtime_metadata(run.runtime_metadata, runtime_metadata),
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
        interrupt_type: WorkflowInterruptType | None = None,
        prompt: str | None = None,
        options: list[dict[str, Any]] | None = None,
        policy_snapshot: dict[str, Any] | None = None,
        scope_snapshot: dict[str, Any] | None = None,
        normalized_arguments: dict[str, Any] | None = None,
        expires_at: datetime.datetime | None = None,
    ) -> WorkflowRun:
        updated = await self._repository.update_run(
            [run.project_id] if run.project_id is not None else None,
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.INTERRUPTED,
                output_summary=output_summary,
                runtime_provider_id=runtime_provider_id,
                runtime_thread_id=runtime_thread_id,
                runtime_run_id=runtime_run_id,
                current_node=current_node,
                resume_token=resume_token,
                runtime_metadata=_merge_runtime_metadata(run.runtime_metadata, runtime_metadata),
            ),
        )
        if interrupt_type is not None:
            if prompt is None or normalized_arguments is None:
                raise ValueError("Typed interrupts require a prompt and normalized arguments")
            await self._repository.create_interrupt(
                WorkflowInterruptCreate(
                    workflow_run_id=run.id,
                    owner_user_id=run.user_id,
                    interrupt_type=interrupt_type,
                    prompt=prompt,
                    options=options or [],
                    policy_snapshot=policy_snapshot or {},
                    scope_snapshot=scope_snapshot or {},
                    argument_hash=_argument_hash(normalized_arguments),
                    expires_at=expires_at
                    or datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=30),
                )
            )
        return updated

    async def pending_interrupts(self, run_id: int, owner_user_id: int) -> list[WorkflowInterrupt]:
        return await self._repository.list_pending_interrupts_for_owner(run_id, owner_user_id)

    async def resume(
        self,
        *,
        run_id: int,
        owner_user_id: int,
        interrupt_id: int,
        expected_version: int,
        response: str,
    ) -> WorkflowRun:
        run = await self._repository.get_run(None, run_id, owner_user_id=owner_user_id)
        if run.user_id != owner_user_id or run.status != WorkflowRunStatus.INTERRUPTED:
            from src.harness import HarnessRunNotFoundError

            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")
        if run.version != expected_version:
            raise HarnessWorkflowConflictError("Workflow state changed; reload and retry")
        await self._repository.consume_interrupt(
            interrupt_id,
            run_id,
            owner_user_id,
            response_summary=_sanitize_response(response),
        )
        metadata = dict(run.runtime_metadata)
        metadata["continuation"] = {"state": "pending", "interrupt_id": interrupt_id}
        return await self._repository.update_run(
            None,
            run_id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.PENDING,
                runtime_metadata=_merge_runtime_metadata(run.runtime_metadata, metadata),
            ),
            expected_version=expected_version,
            owner_user_id=owner_user_id,
        )

    async def cancel(
        self,
        *,
        run_id: int,
        owner_user_id: int,
        expected_version: int,
        reason: str | None,
    ) -> WorkflowRun:
        run = await self._repository.get_run(None, run_id, owner_user_id=owner_user_id)
        if run.user_id != owner_user_id or run.status not in {
            WorkflowRunStatus.PENDING,
            WorkflowRunStatus.RUNNING,
            WorkflowRunStatus.INTERRUPTED,
        }:
            from src.harness import HarnessRunNotFoundError

            raise HarnessRunNotFoundError(f"Unknown workflow_run_id: {run_id}")
        now = datetime.datetime.now(datetime.UTC)
        updated = await self._repository.update_run(
            None,
            run_id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.CANCELLED,
                cancelled_at=now,
                cancelled_by_user_id=owner_user_id,
                cancellation_reason=_sanitize_response(reason) if reason else None,
                finished_at=now,
            ),
            expected_version=expected_version,
            owner_user_id=owner_user_id,
        )
        await self._repository.cancel_pending_interrupts(run_id, owner_user_id)
        return updated

    async def mark_succeeded(
        self,
        run: WorkflowRun,
        *,
        output_summary: str | None = None,
        artifact_links: list[dict[str, Any]] | None = None,
    ) -> WorkflowRun:
        return await self._repository.update_run(
            [run.project_id] if run.project_id is not None else None,
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.SUCCEEDED,
                output_summary=output_summary,
                artifact_links=artifact_links,
                artifact_receipts=_with_pack_receipt(None, run.runtime_metadata),
                finished_at=datetime.datetime.now(datetime.UTC),
            ),
        )

    async def mark_failed(
        self,
        run: WorkflowRun,
        *,
        error_message: str,
        runtime_provider_id: str | None = None,
        runtime_thread_id: str | None = None,
        runtime_run_id: str | None = None,
        current_node: str | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        return await self._repository.update_run(
            [run.project_id] if run.project_id is not None else None,
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.FAILED,
                error_message=error_message,
                runtime_provider_id=runtime_provider_id,
                runtime_thread_id=runtime_thread_id,
                runtime_run_id=runtime_run_id,
                current_node=current_node,
                runtime_metadata=_merge_runtime_metadata(run.runtime_metadata, runtime_metadata),
                finished_at=datetime.datetime.now(datetime.UTC),
            ),
        )

    async def mark_indeterminate(
        self,
        run: WorkflowRun,
        *,
        output_summary: str,
        runtime_provider_id: str | None = None,
        runtime_thread_id: str | None = None,
        runtime_run_id: str | None = None,
        current_node: str | None = None,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        """Record an unknown side-effect outcome without permitting a blind retry."""
        return await self._repository.update_run(
            [run.project_id],
            run.id,
            WorkflowRunUpdate(
                status=WorkflowRunStatus.INDETERMINATE,
                output_summary=output_summary,
                runtime_provider_id=runtime_provider_id,
                runtime_thread_id=runtime_thread_id,
                runtime_run_id=runtime_run_id,
                current_node=current_node,
                runtime_metadata=_merge_runtime_metadata(run.runtime_metadata, runtime_metadata),
            ),
        )


def _argument_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sanitize_response(value: str) -> str:
    return " ".join(value.strip().split())[:2000]


def _bounded(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).strip().split())[:limit]


def _pack_governance_receipt(
    snapshot: Any,
    presentation: Any,
    private_analysis_packs: Any = None,
) -> dict[str, Any] | None:
    """Extract immutable governed provenance from compiled capability metadata."""
    if not isinstance(snapshot, dict) or "policy_id" not in snapshot:
        return None
    private_receipts = [
        item["receipt"]
        for item in (private_analysis_packs if isinstance(private_analysis_packs, list) else [])
        if isinstance(item, dict) and isinstance(item.get("receipt"), dict)
    ]
    return {
        "policy_id": snapshot.get("policy_id"),
        "policy_hash": snapshot.get("policy_hash"),
        "compiled_snapshot_hash": snapshot.get("snapshot_hash"),
        "foundation_pack_id": snapshot.get("foundation_pack_id"),
        "functional_pack_ids": list(snapshot.get("functional_pack_ids") or []),
        "style_pack_ids": list(snapshot.get("style_pack_ids") or []),
        "packs": list(snapshot.get("resolved_packs") or []),
        "presentation": presentation if isinstance(presentation, dict) else None,
        "private_analysis_pack_receipts": private_receipts,
    }


def _merge_runtime_metadata(
    current: dict[str, Any], update: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preserve the immutable Pack receipt across every workflow transition."""
    merged = dict(current)
    if update:
        merged.update(update)
    if "pack_governance" in current:
        merged["pack_governance"] = current["pack_governance"]
    if "pack_snapshot" in current:
        merged["pack_snapshot"] = current["pack_snapshot"]
    return merged


def _with_pack_governance(details: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    receipt = metadata.get("pack_governance")
    return {**details, **({"pack_governance": receipt} if isinstance(receipt, dict) else {})}


def _with_pack_receipt(
    receipts: list[dict[str, Any]] | None,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    result = list(receipts or [])
    receipt = metadata.get("pack_governance")
    if isinstance(receipt, dict) and not any(item.get("kind") == "pack_governance" for item in result):
        result.append({"kind": "pack_governance", **receipt})
    return result
