"""Authoritative, idempotent reconciliation of accepted documentation workflows."""

from __future__ import annotations

import asyncio
import datetime
import logging
from dataclasses import dataclass
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from src.harness.models import WorkflowRun, WorkflowRunStatus, WorkflowRunUpdate
from src.harness.providers.mcp_docs import McpDocumentationProvider, McpDocumentationProviderError
from src.harness.repository import HarnessWorkflowConflictError, HarnessWorkflowRepository

_ACTIVE_CHILD_STATES = frozenset({"queued", "pending", "running", "triggered"})
_SUCCESS_CHILD_STATES = frozenset({"completed", "succeeded", "ready"})
_FAILURE_CHILD_STATES = frozenset({"failed", "error", "cancelled"})
_MAX_ATTEMPTS = 6
_BASE_BACKOFF_SECONDS = 15
_MAX_BACKOFF_SECONDS = 15 * 60
_DOCUMENTATION_GENERATION_CAPABILITY_ID = "mcp.docs.generate_project_documentation"

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
    ) -> ReconciliationResult:
        if run.status in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }:
            return ReconciliationResult(run=run, attempted=False, authoritative_status=None)
        metadata = dict(run.runtime_metadata)
        ledger = _ledger(metadata)
        now = self._now()
        if not force and _next_poll_after(ledger) > now:
            return ReconciliationResult(run=run, attempted=False, authoritative_status=None)

        attempt = int(ledger.get("attempt_count", 0)) + 1
        try:
            child = await self._provider.get_generation_status(
                project_id=run.project_id,
                access_token=access_token,
            )
        except McpDocumentationProviderError:
            return await self._record_pending_or_indeterminate(
                run, metadata, attempt, child_status=None, reason="provider_unavailable"
            )

        child_status = child.status.lower()
        external_generation = {
            "run_id": child.run_id,
            "status": child_status,
            "reconciled_at": now.isoformat(),
        }
        metadata["external_generation"] = external_generation
        if child.found and child_status in _SUCCESS_CHILD_STATES:
            return await self._transition(
                run,
                metadata,
                status=WorkflowRunStatus.SUCCEEDED,
                summary="Documentation generation completed.",
                child_status=child_status,
            )
        if child.found and child_status in _FAILURE_CHILD_STATES:
            message = (child.error_message or f"Documentation generation {child_status}.")[:500]
            return await self._transition(
                run,
                metadata,
                status=WorkflowRunStatus.FAILED,
                error_message=message,
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
                    finished_at=(now if status in {WorkflowRunStatus.SUCCEEDED, WorkflowRunStatus.FAILED, WorkflowRunStatus.INDETERMINATE} else None),
                ),
                expected_version=run.version,
            )
        except HarnessWorkflowConflictError:
            # A concurrent poll/cancel won. The next caller reloads the source
            # of truth rather than overwriting it with stale child information.
            updated = await self._repository.get_run([run.project_id], run.id)
        return ReconciliationResult(run=updated, attempted=True, authoritative_status=child_status)


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
    for status in (WorkflowRunStatus.TRIGGERED, WorkflowRunStatus.RUNNING):
        if remaining == 0:
            break
        candidates = await repository.list_runs(
            None,
            capability_id=_DOCUMENTATION_GENERATION_CAPABILITY_ID,
            status=status,
            limit=remaining,
        )
        runs.extend(candidates)
        remaining -= len(candidates)

    reconciler = DocumentationWorkflowReconciler(repository, provider)
    attempted = 0
    for run in runs:
        result = await reconciler.reconcile(run, access_token=access_token)
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
