"""Owner- and scope-bound workflow resolution and truthful cancellation outcomes."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum

from src.harness.models import (
    DOCUMENTATION_GENERATION_CAPABILITY,
    WorkflowRun,
    WorkflowRunStatus,
    WorkflowRunUpdate,
)
from src.harness.providers.mcp_docs import (
    McpDocumentationProvider,
    McpDocumentationProviderError,
)
from src.harness.reconciliation import DocumentationWorkflowReconciler
from src.harness.repository import HarnessWorkflowConflictError, HarnessWorkflowRepository
from src.harness.workflow_ledger import (
    WorkflowChildCancellationState,
    WorkflowObservationSource,
)
from src.harness.resolution import (
    EntityCandidate,
    EntityType,
    ResolutionStatus,
    WorkflowKind,
    normalize_reference_text,
    resolve_entity,
)

_ACTIVE_STATUSES = frozenset(
    {
        WorkflowRunStatus.PENDING,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.TRIGGERED,
        WorkflowRunStatus.INTERRUPTED,
        WorkflowRunStatus.INDETERMINATE,
    }
)
_PENDING_PROJECT_SCOPE_ID = "__pending_project_scope__"


class WorkflowCancellationOutcome(StrEnum):
    CANCELLED = "cancelled"
    ALREADY_FAILED = "already_failed"
    ALREADY_COMPLETED = "already_completed"
    ALREADY_CANCELLED = "already_cancelled"
    NOT_CANCELLABLE = "not_cancellable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class WorkflowCancellationResult:
    outcome: WorkflowCancellationOutcome
    message: str
    run: WorkflowRun | None = None
    options: tuple[WorkflowRun, ...] = ()


class WorkflowCancellationController:
    """Resolve caller-visible workflow references and cancel through provider truth."""

    def __init__(
        self,
        repository: HarnessWorkflowRepository,
        provider: McpDocumentationProvider,
    ) -> None:
        self._repository = repository
        self._provider = provider

    async def cancel(
        self,
        *,
        owner_user_id: int,
        project_scope: list[str] | None,
        conversation_id: int | None,
        workflow_reference: str | None,
        access_token: str | None,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> WorkflowCancellationResult:
        runs = await self._repository.list_owned_runs(
            owner_user_id,
            conversation_id=conversation_id,
            limit=50,
        )
        # Apply ownership and current project scope before resolution. The
        # pending-scope sentinel has no project resource yet and is authorized
        # by ownership alone.
        candidates = [
            run
            for run in runs
            if (
                project_scope is None
                or run.project_id in project_scope
                or run.project_id == _PENDING_PROJECT_SCOPE_ID
            )
        ]
        selected = _resolve_candidate(candidates, workflow_reference)
        if isinstance(selected, WorkflowCancellationResult):
            return selected
        run = selected
        if expected_version is not None and run.version != expected_version:
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.CONFLICT,
                "The workflow changed before cancellation could be applied. Reload and try again.",
                run=run,
            )

        terminal = _terminal_result(run)
        if terminal is not None:
            return terminal
        if run.status in {WorkflowRunStatus.PENDING, WorkflowRunStatus.INTERRUPTED}:
            return await self._cancel_local(
                run,
                owner_user_id=owner_user_id,
                reason=reason,
            )
        if run.capability_id != DOCUMENTATION_GENERATION_CAPABILITY.id:
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.NOT_CANCELLABLE,
                f"The {run.intent_mode.replace('_', ' ')} workflow for `{run.project_id or 'this conversation'}` cannot be cancelled through its provider.",
                run=run,
            )
        if run.project_id is None:
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.NOT_CANCELLABLE,
                "This workflow has no project-bound provider job to cancel.",
                run=run,
            )

        reconciled = await DocumentationWorkflowReconciler(
            self._repository, self._provider
        ).reconcile(run, access_token=access_token, force=True)
        run = reconciled.run
        terminal = _terminal_result(run)
        if terminal is not None:
            return terminal
        reconciliation = run.runtime_metadata.get("reconciliation")
        if (
            isinstance(reconciliation, dict)
            and reconciliation.get("last_reason") == "provider_unavailable"
        ):
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.PROVIDER_UNAVAILABLE,
                f"I could not reach the documentation provider, so I did not claim that workflow {run.id} was cancelled.",
                run=run,
            )

        child_receipt = await self._repository.get_child_receipt(
            [run.project_id],
            run.id,
            provider_id="mcp.docs",
            child_kind="documentation_generation",
            owner_user_id=owner_user_id,
        )
        if child_receipt is None:
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.NOT_CANCELLABLE,
                f"Workflow {run.id} has no exact provider child receipt to cancel safely.",
                run=run,
            )
        cancellation_key = f"workflow:{run.id}:provider-child:{child_receipt.id}:cancel"
        child_receipt, _ = await self._repository.record_child_cancellation(
            child_receipt_id=child_receipt.id,
            idempotency_key=cancellation_key,
            state=WorkflowChildCancellationState.REQUESTED,
        )

        try:
            provider_result = await self._provider.cancel_generation(
                project_id=run.project_id,
                access_token=access_token,
            )
        except McpDocumentationProviderError:
            await self._repository.record_child_cancellation(
                child_receipt_id=child_receipt.id,
                idempotency_key=cancellation_key,
                state=WorkflowChildCancellationState.PROVIDER_UNAVAILABLE,
                provider_status="provider_unavailable",
            )
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.PROVIDER_UNAVAILABLE,
                f"I could not reach the documentation provider, so workflow {run.id} remains {run.status.value}.",
                run=run,
            )
        if not provider_result.accepted:
            await self._repository.record_child_cancellation(
                child_receipt_id=child_receipt.id,
                idempotency_key=cancellation_key,
                state=WorkflowChildCancellationState.REJECTED,
                provider_status=provider_result.status,
            )
            # A provider-side race may have made the child terminal between the
            # initial read and cancellation. Re-read before reporting failure.
            refreshed = await DocumentationWorkflowReconciler(
                self._repository, self._provider
            ).reconcile(run, access_token=access_token, force=True)
            terminal = _terminal_result(refreshed.run)
            if terminal is not None:
                return terminal
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.NOT_CANCELLABLE,
                provider_result.message,
                run=refreshed.run,
            )

        child_receipt, _ = await self._repository.record_child_cancellation(
            child_receipt_id=child_receipt.id,
            idempotency_key=cancellation_key,
            state=WorkflowChildCancellationState.ACCEPTED,
            provider_status=provider_result.status,
        )
        await self._repository.record_child_observation(
            child_receipt_id=child_receipt.id,
            idempotency_key=f"{cancellation_key}:provider-response:{provider_result.status}",
            source=WorkflowObservationSource.CANCELLATION,
            provider_status=provider_result.status,
        )

        now = datetime.datetime.now(datetime.UTC)
        metadata = dict(run.runtime_metadata)
        child = metadata.get("external_generation")
        external_generation = dict(child) if isinstance(child, dict) else {}
        external_generation.update(
            {
                "status": "cancelled",
                "reconciled_at": now.isoformat(),
            }
        )
        metadata["external_generation"] = external_generation
        metadata["cancellation"] = {
            "outcome": "cancelled",
            "provider_status": provider_result.status,
            "child_receipt_id": child_receipt.id,
            "recorded_at": now.isoformat(),
        }
        try:
            cancelled = await self._repository.update_run(
                [run.project_id],
                run.id,
                WorkflowRunUpdate(
                    status=WorkflowRunStatus.CANCELLED,
                    output_summary=provider_result.message,
                    error_message=None,
                    runtime_metadata=metadata,
                    finished_at=now,
                    cancelled_at=now,
                    cancelled_by_user_id=owner_user_id,
                    cancellation_reason="Cancelled by user request.",
                ),
                expected_version=run.version,
            )
        except HarnessWorkflowConflictError:
            current = await self._repository.get_run([run.project_id], run.id)
            terminal = _terminal_result(current)
            if terminal is not None:
                return terminal
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.CONFLICT,
                "The workflow changed while cancellation was being applied. Its current state was preserved.",
                run=current,
            )
        await self._repository.cancel_pending_interrupts(run.id, owner_user_id)
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.CANCELLED,
            f"Cancelled documentation generation for `{cancelled.project_id}` (workflow {cancelled.id}).",
            run=cancelled,
        )

    async def _cancel_local(
        self,
        run: WorkflowRun,
        *,
        owner_user_id: int,
        reason: str | None,
    ) -> WorkflowCancellationResult:
        now = datetime.datetime.now(datetime.UTC)
        try:
            cancelled = await self._repository.update_run(
                [run.project_id] if run.project_id is not None else None,
                run.id,
                WorkflowRunUpdate(
                    status=WorkflowRunStatus.CANCELLED,
                    output_summary="Workflow cancelled before provider execution.",
                    finished_at=now,
                    cancelled_at=now,
                    cancelled_by_user_id=owner_user_id,
                    cancellation_reason=(reason[:500] if reason else "Cancelled by user request."),
                ),
                expected_version=run.version,
            )
        except HarnessWorkflowConflictError:
            current = await self._repository.get_run(
                [run.project_id] if run.project_id is not None else None,
                run.id,
            )
            terminal = _terminal_result(current)
            if terminal is not None:
                return terminal
            return WorkflowCancellationResult(
                WorkflowCancellationOutcome.CONFLICT,
                "The workflow changed while cancellation was being applied. Its current state was preserved.",
                run=current,
            )
        await self._repository.cancel_pending_interrupts(run.id, owner_user_id)
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.CANCELLED,
            f"Cancelled workflow {cancelled.id} before provider execution.",
            run=cancelled,
        )


def _resolve_candidate(
    candidates: list[WorkflowRun],
    workflow_reference: str | None,
) -> WorkflowRun | WorkflowCancellationResult:
    if not candidates:
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.NO_MATCH,
            "I could not find an authorized workflow in this conversation to cancel.",
        )
    ordered = sorted(candidates, key=lambda run: (run.started_at, run.id), reverse=True)
    if not workflow_reference or not workflow_reference.strip():
        active = [run for run in ordered if run.status in _ACTIVE_STATUSES]
        return active[0] if active else ordered[0]
    if not normalize_reference_text(workflow_reference):
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.NO_MATCH,
            "I could not match that reference to an authorized workflow in this conversation.",
        )

    entity_candidates = [_workflow_candidate(run) for run in ordered]
    resolution = resolve_entity(
        workflow_reference,
        EntityType.WORKFLOW_RUN,
        entity_candidates,
    )
    if resolution.status is ResolutionStatus.RESOLVED and resolution.reference is not None:
        return next(run for run in ordered if str(run.id) == resolution.reference.stable_id)

    active = [run for run in ordered if run.status in _ACTIVE_STATUSES]
    if active:
        active_resolution = resolve_entity(
            workflow_reference,
            EntityType.WORKFLOW_RUN,
            [_workflow_candidate(run) for run in active],
        )
        if (
            active_resolution.status is ResolutionStatus.RESOLVED
            and active_resolution.reference is not None
        ):
            return next(
                run for run in active if str(run.id) == active_resolution.reference.stable_id
            )
        resolution = active_resolution

    if resolution.status is ResolutionStatus.AMBIGUOUS:
        ids = {
            option.value
            for option in (resolution.ambiguity.options if resolution.ambiguity else ())
        }
        options = tuple(run for run in ordered if str(run.id) in ids)
        labels = ", ".join(
            f"workflow {run.id} for {run.project_id or 'this conversation'}"
            for run in options[:5]
        )
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.AMBIGUOUS,
            f"More than one authorized workflow matches that reference: {labels}. Which workflow should I cancel?",
            options=options,
        )
    return WorkflowCancellationResult(
        WorkflowCancellationOutcome.NO_MATCH,
        f"I could not match `{workflow_reference}` to an authorized workflow in this conversation.",
    )


def _workflow_candidate(run: WorkflowRun) -> EntityCandidate:
    project = run.project_id or "conversation workflow"
    is_documentation = run.capability_id == DOCUMENTATION_GENERATION_CAPABILITY.id
    return EntityCandidate(
        entity_type=EntityType.WORKFLOW_RUN,
        stable_id=str(run.id),
        canonical_name=f"workflow {run.id} for {project}",
        aliases=(
            f"run {run.id}",
            project,
            run.intent_mode.replace("_", " "),
            *(("documentation generation",) if is_documentation else ()),
        ),
        workflow_kind=(
            WorkflowKind.DOCUMENTATION_GENERATION
            if is_documentation
            else None
        ),
    )


def _terminal_result(run: WorkflowRun) -> WorkflowCancellationResult | None:
    project = run.project_id or "this conversation"
    if run.status is WorkflowRunStatus.FAILED:
        detail = f" {run.error_message}" if run.error_message else ""
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.ALREADY_FAILED,
            f"Workflow {run.id} for `{project}` has already failed; there is nothing left to cancel.{detail}",
            run=run,
        )
    if run.status is WorkflowRunStatus.SUCCEEDED:
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.ALREADY_COMPLETED,
            f"Workflow {run.id} for `{project}` has already completed; there is nothing left to cancel.",
            run=run,
        )
    if run.status is WorkflowRunStatus.CANCELLED:
        return WorkflowCancellationResult(
            WorkflowCancellationOutcome.ALREADY_CANCELLED,
            f"Workflow {run.id} for `{project}` is already cancelled.",
            run=run,
        )
    return None
