"""Authoritative, idempotent reconciliation of accepted documentation workflows."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
from dataclasses import dataclass
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from src.harness.models import WorkflowRun, WorkflowRunStatus, WorkflowRunUpdate
from src.harness.providers.mcp_docs import McpDocumentationProvider, McpDocumentationProviderError
from src.harness.repository import HarnessWorkflowConflictError, HarnessWorkflowRepository
from src.harness.workflow_ledger import (
    TERMINAL_CHILD_STATUSES,
    TERMINAL_PARENT_STATUSES,
    WorkflowChildCancellationState,
    WorkflowChildStatus,
    WorkflowObservationSource,
    parent_status_from_child,
)

_ACTIVE_CHILD_STATES = frozenset({"queued", "pending", "running", "triggered"})
_SUCCESS_CHILD_STATES = frozenset({"completed", "succeeded", "ready"})
_FAILURE_CHILD_STATES = frozenset({"failed", "error"})
_CANCELLED_CHILD_STATES = frozenset({"cancelled"})
_MAX_ATTEMPTS = 6
_BASE_BACKOFF_SECONDS = 15
_MAX_BACKOFF_SECONDS = 15 * 60
_DOCUMENTATION_GENERATION_CAPABILITY_ID = "mcp.docs.generate_project_documentation"
_DOCUMENTATION_CHILD_KIND = "documentation_generation"

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]
ProviderFactory = Callable[[], McpDocumentationProvider]


@dataclass(frozen=True)
class ReconciliationResult:
    run: WorkflowRun
    attempted: bool
    authoritative_status: str | None


@dataclass(frozen=True)
class ReconciliationCycleResult:
    scanned: int
    attempted: int


@dataclass(frozen=True)
class CallbackIngestionResult:
    run: WorkflowRun
    applied: bool
    child_status: str


class DocumentationWorkflowReconciler:
    """Maps MCP's durable generation receipt back onto one Nexus parent run.

    Polling performs only a read against MCP. The attempt ledger lives in the
    parent run metadata, making retries/restarts idempotent without retaining a
    user's cookie or any provider payload.
    """

    def __init__(
        self,
        repository: HarnessWorkflowRepository,
        provider: McpDocumentationProvider,
        *,
        now: Callable[[], datetime.datetime] | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._now = now or (lambda: datetime.datetime.now(datetime.UTC))
        self._max_attempts = max_attempts

    async def reconcile(
        self,
        run: WorkflowRun,
        *,
        access_token: str | None,
        force: bool = False,
        source: WorkflowObservationSource = WorkflowObservationSource.POLL,
    ) -> ReconciliationResult:
        receipt = await self._repository.get_child_receipt(
            [run.project_id] if run.project_id is not None else None,
            run.id,
            provider_id="mcp.docs",
            child_kind=_DOCUMENTATION_CHILD_KIND,
            owner_user_id=run.user_id,
        )
        if run.status in TERMINAL_PARENT_STATUSES and (
            receipt is None or WorkflowChildStatus(receipt.status) in TERMINAL_CHILD_STATUSES
        ):
            return ReconciliationResult(run=run, attempted=False, authoritative_status=None)
        metadata = dict(run.runtime_metadata)
        ledger = _ledger(metadata)
        now = self._now()
        if not force and _next_poll_after(ledger) > now:
            return ReconciliationResult(run=run, attempted=False, authoritative_status=None)

        attempt = int(ledger.get("attempt_count", 0)) + 1
        try:
            status_arguments = {
                "project_id": run.project_id,
                "access_token": access_token,
            }
            if receipt is not None:
                status_arguments["external_run_id"] = receipt.external_run_id
            child = await self._provider.get_generation_status(**status_arguments)
        except McpDocumentationProviderError:
            return await self._record_pending_or_indeterminate(
                run, metadata, attempt, child_status=None, reason="provider_unavailable"
            )

        child_status = child.status.lower()
        provider_timestamp = _parse_provider_timestamp(child.completed_at or child.started_at)
        if receipt is None and child.found and child.run_id is not None:
            receipt = await self._repository.bind_child_receipt(
                [run.project_id] if run.project_id is not None else None,
                workflow_run_id=run.id,
                provider_id="mcp.docs",
                child_kind=_DOCUMENTATION_CHILD_KIND,
                external_run_id=child.run_id,
                project_id=run.project_id,
                provider_status=child_status,
                source=source,
                observed_at=now,
                provider_started_at=_parse_provider_timestamp(child.started_at),
                provider_finished_at=_parse_provider_timestamp(child.completed_at),
                provider_timestamp=provider_timestamp,
                owner_user_id=run.user_id,
            )
        if receipt is not None:
            receipt, _ = await self._repository.record_child_observation(
                child_receipt_id=receipt.id,
                idempotency_key=_observation_key(
                    receipt.external_run_id,
                    child_status,
                    child.completed_at or child.started_at,
                    child.error_message,
                ),
                source=source,
                provider_status=child_status,
                observed_at=now,
                provider_timestamp=provider_timestamp,
                provider_started_at=_parse_provider_timestamp(child.started_at),
                provider_finished_at=_parse_provider_timestamp(child.completed_at),
                error_message=child.error_message,
            )
            child_status = receipt.provider_status
        external_generation = {
            "run_id": (
                _compat_external_id(receipt.external_run_id)
                if receipt is not None
                else child.run_id
            ),
            "status": child_status,
            "reconciled_at": now.isoformat(),
        }
        metadata["external_generation"] = external_generation
        if receipt is not None:
            canonical_child_status = WorkflowChildStatus(receipt.status)
            cancellation_state = WorkflowChildCancellationState(receipt.cancellation_state)
            target_parent_status = parent_status_from_child(
                parent_status=run.status,
                child_status=canonical_child_status,
                cancellation_state=cancellation_state,
            )
        else:
            canonical_child_status = WorkflowChildStatus.UNKNOWN
            target_parent_status = run.status
        if run.status in TERMINAL_PARENT_STATUSES:
            refreshed = await self._repository.get_run(
                [run.project_id] if run.project_id is not None else None,
                run.id,
                owner_user_id=run.user_id,
            )
            return ReconciliationResult(
                run=refreshed,
                attempted=True,
                authoritative_status=child_status,
            )
        if child.found and canonical_child_status is WorkflowChildStatus.SUCCEEDED:
            return await self._transition(
                run,
                metadata,
                status=target_parent_status,
                summary="Documentation generation completed.",
                child_status=child_status,
            )
        if child.found and canonical_child_status is WorkflowChildStatus.FAILED:
            message = (child.error_message or f"Documentation generation {child_status}.")[:500]
            return await self._transition(
                run,
                metadata,
                status=target_parent_status,
                error_message=message,
                child_status=child_status,
            )
        if child.found and canonical_child_status is WorkflowChildStatus.CANCELLED:
            return await self._transition(
                run,
                metadata,
                status=target_parent_status,
                summary="Documentation generation was cancelled.",
                child_status=child_status,
            )
        reason = "child_not_found" if not child.found else "child_still_active"
        return await self._record_pending_or_indeterminate(
            run, metadata, attempt, child_status=child_status, reason=reason
        )

    async def _record_pending_or_indeterminate(
        self,
        run: WorkflowRun,
        metadata: dict,
        attempt: int,
        *,
        child_status: str | None,
        reason: str,
    ) -> ReconciliationResult:
        if attempt >= self._max_attempts:
            return await self._transition(
                run,
                metadata,
                status=WorkflowRunStatus.INDETERMINATE,
                summary="Documentation generation outcome could not be confirmed.",
                child_status=child_status,
                attempt=attempt,
                reason=reason,
            )
        return await self._transition(
            run,
            metadata,
            status=run.status,
            child_status=child_status,
            attempt=attempt,
            reason=reason,
        )

    async def _transition(
        self,
        run: WorkflowRun,
        metadata: dict,
        *,
        status: WorkflowRunStatus,
        child_status: str | None,
        attempt: int | None = None,
        reason: str | None = None,
        summary: str | None = None,
        error_message: str | None = None,
    ) -> ReconciliationResult:
        now = self._now()
        ledger = _ledger(metadata)
        if attempt is not None:
            ledger.update(
                {
                    "state": "retry_scheduled",
                    "attempt_count": attempt,
                    "last_status": child_status,
                    "last_reason": reason,
                    "next_poll_after": (now + datetime.timedelta(seconds=_backoff_seconds(attempt))).isoformat(),
                }
            )
        else:
            ledger.update(
                {
                    "state": "resolved",
                    "last_status": child_status,
                    "resolved_at": now.isoformat(),
                }
            )
        metadata["reconciliation"] = ledger
        if status == WorkflowRunStatus.INDETERMINATE:
            ledger["state"] = "indeterminate"
            ledger.pop("next_poll_after", None)
        try:
            updated = await self._repository.update_run(
                [run.project_id],
                run.id,
                WorkflowRunUpdate(
                    status=status,
                    output_summary=summary,
                    error_message=error_message,
                    runtime_metadata=metadata,
                    finished_at=(
                        now
                        if status
                        in {
                            WorkflowRunStatus.SUCCEEDED,
                            WorkflowRunStatus.FAILED,
                            WorkflowRunStatus.CANCELLED,
                            WorkflowRunStatus.INDETERMINATE,
                        }
                        else None
                    ),
                    cancelled_at=(now if status == WorkflowRunStatus.CANCELLED else None),
                ),
                expected_version=run.version,
            )
        except HarnessWorkflowConflictError:
            # A concurrent poll/cancel won. The next caller reloads the source
            # of truth rather than overwriting it with stale child information.
            updated = await self._repository.get_run([run.project_id], run.id)
        return ReconciliationResult(run=updated, attempted=True, authoritative_status=child_status)


class DocumentationWorkflowCallbackIngestor:
    """Authenticated-boundary seam for exact, replay-safe provider callbacks.

    MCP has no callback contract today, so this is intentionally not mounted as
    a public route. A future authenticated adapter can call this seam without
    inventing another transition policy.
    """

    def __init__(self, repository: HarnessWorkflowRepository) -> None:
        self._repository = repository

    async def ingest(
        self,
        *,
        provider_id: str,
        external_run_id: str | int,
        idempotency_key: str,
        provider_status: str,
        source: WorkflowObservationSource = WorkflowObservationSource.CALLBACK,
        observed_at: datetime.datetime | None = None,
        provider_timestamp: datetime.datetime | None = None,
        provider_started_at: datetime.datetime | None = None,
        provider_finished_at: datetime.datetime | None = None,
        error_message: str | None = None,
    ) -> CallbackIngestionResult:
        receipt = await self._repository.get_child_receipt_by_external(
            provider_id=provider_id,
            external_run_id=external_run_id,
        )
        if receipt is None:
            raise ValueError("Unknown provider child receipt")
        receipt, applied = await self._repository.record_child_observation(
            child_receipt_id=receipt.id,
            idempotency_key=idempotency_key,
            source=source,
            provider_status=provider_status,
            observed_at=observed_at,
            provider_timestamp=provider_timestamp,
            provider_started_at=provider_started_at,
            provider_finished_at=provider_finished_at,
            error_message=error_message,
        )
        run = await self._repository.get_run(None, receipt.workflow_run_id)
        target = parent_status_from_child(
            parent_status=run.status,
            child_status=WorkflowChildStatus(receipt.status),
            cancellation_state=WorkflowChildCancellationState(receipt.cancellation_state),
        )
        if target != run.status:
            now = observed_at or datetime.datetime.now(datetime.UTC)
            run = await self._repository.update_run(
                [run.project_id] if run.project_id is not None else None,
                run.id,
                WorkflowRunUpdate(
                    status=target,
                    output_summary=(
                        "Documentation generation completed."
                        if target is WorkflowRunStatus.SUCCEEDED
                        else "Documentation generation was cancelled."
                        if target is WorkflowRunStatus.CANCELLED
                        else None
                    ),
                    error_message=(
                        (receipt.error_message or "Documentation generation failed.")[:500]
                        if target is WorkflowRunStatus.FAILED
                        else None
                    ),
                    finished_at=(now if target in TERMINAL_PARENT_STATUSES else None),
                    cancelled_at=(now if target is WorkflowRunStatus.CANCELLED else None),
                    runtime_metadata={
                        "external_generation": {
                            "run_id": _compat_external_id(receipt.external_run_id),
                            "status": receipt.provider_status,
                            "reconciled_at": now.isoformat(),
                        }
                    },
                ),
                expected_version=run.version,
            )
        else:
            run = await self._repository.get_run(None, run.id)
        return CallbackIngestionResult(
            run=run,
            applied=applied,
            child_status=receipt.status,
        )


async def reconcile_accepted_documentation_workflows(
    repository: HarnessWorkflowRepository,
    provider: McpDocumentationProvider,
    *,
    access_token: str,
    limit: int,
) -> ReconciliationCycleResult:
    """Reconcile a bounded set of accepted documentation workflow parents.

    The caller supplies a deployment service credential only for this cycle.
    It is deliberately not copied into workflow metadata or the result ledger.
    """
    remaining = max(1, limit)
    runs: list[WorkflowRun] = []
    seen: set[int] = set()
    receipts = await repository.list_nonterminal_child_receipts(
        provider_id="mcp.docs",
        child_kind=_DOCUMENTATION_CHILD_KIND,
        limit=remaining,
    )
    for receipt in receipts:
        run = await repository.get_run(None, receipt.workflow_run_id)
        if run.id not in seen:
            runs.append(run)
            seen.add(run.id)
            remaining -= 1
        if remaining == 0:
            break
    for status in (
        WorkflowRunStatus.TRIGGERED,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.INDETERMINATE,
    ):
        if remaining == 0:
            break
        candidates = await repository.list_runs(
            None,
            capability_id=_DOCUMENTATION_GENERATION_CAPABILITY_ID,
            status=status,
            limit=remaining,
        )
        for candidate in candidates:
            if candidate.id not in seen:
                runs.append(candidate)
                seen.add(candidate.id)
                remaining -= 1
                if remaining == 0:
                    break

    reconciler = DocumentationWorkflowReconciler(repository, provider)
    attempted = 0
    for run in runs:
        result = await reconciler.reconcile(
            run,
            access_token=access_token,
            source=WorkflowObservationSource.RECOVERY,
        )
        attempted += int(result.attempted)
    return ReconciliationCycleResult(scanned=len(runs), attempted=attempted)


class DocumentationWorkflowReconciliationScheduler:
    """Reconciles accepted MCP generations without retaining end-user credentials."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        service_access_token: str,
        provider_factory: ProviderFactory = McpDocumentationProvider,
        interval_seconds: int = 60,
        batch_size: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._service_access_token = service_access_token
        self._provider_factory = provider_factory
        self._interval_seconds = min(3600, max(1, interval_seconds))
        self._batch_size = min(500, max(1, batch_size))
        self._stop = asyncio.Event()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    async def run_once(self) -> ReconciliationCycleResult:
        """Run one non-overlapping, bounded reconciliation cycle."""
        if self._lock.locked():
            logger.info("Documentation reconciliation skipped; prior cycle is still running")
            return ReconciliationCycleResult(scanned=0, attempted=0)
        async with self._lock:
            async with self._session_factory() as session:
                try:
                    result = await reconcile_accepted_documentation_workflows(
                        HarnessWorkflowRepository(session),
                        self._provider_factory(),
                        access_token=self._service_access_token,
                        limit=self._batch_size,
                    )
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception("Documentation reconciliation cycle crashed")
                    return ReconciliationCycleResult(scanned=0, attempted=0)
            logger.info(
                "Documentation reconciliation cycle finished scanned=%s attempted=%s",
                result.scanned,
                result.attempted,
            )
            return result

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
        self._running = False

    def start_background(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.start())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task


def _ledger(metadata: dict) -> dict:
    existing = metadata.get("reconciliation")
    return dict(existing) if isinstance(existing, dict) else {}


def _next_poll_after(ledger: dict) -> datetime.datetime:
    raw = ledger.get("next_poll_after")
    if not isinstance(raw, str):
        return datetime.datetime.min.replace(tzinfo=datetime.UTC)
    try:
        value = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value.astimezone(datetime.UTC)


def _backoff_seconds(attempt: int) -> int:
    return min(_BASE_BACKOFF_SECONDS * (2 ** max(0, attempt - 1)), _MAX_BACKOFF_SECONDS)


def _parse_provider_timestamp(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.UTC) if parsed.tzinfo is None else parsed.astimezone(datetime.UTC)


def _observation_key(
    external_run_id: str,
    provider_status: str,
    provider_timestamp: str | None,
    error_message: str | None,
) -> str:
    payload = "|".join(
        (
            external_run_id,
            provider_status,
            provider_timestamp or "",
            (error_message or "")[:500],
        )
    )
    return f"mcp.docs:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _compat_external_id(value: str) -> str | int:
    return int(value) if value.isdigit() else value
