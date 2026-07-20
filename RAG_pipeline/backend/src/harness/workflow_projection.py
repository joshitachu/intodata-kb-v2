"""Public, authoritative workflow-card projections for agent responses and chat UI."""

from __future__ import annotations

import datetime
import re
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from src.harness import HarnessRunNotFoundError
from src.harness.models import WorkflowRun, WorkflowRunStatus
from src.harness.providers.mcp_docs import (
    McpDocumentationProvider,
    McpDocumentationProviderError,
)
from src.harness.reconciliation import DocumentationWorkflowReconciler
from src.harness.repository import HarnessWorkflowConflictError, HarnessWorkflowRepository
from src.harness.workflow_ledger import WorkflowChildStatus


_UNSAFE_TEXT = re.compile(
    r"(?i)(authorization\s*[:=]|bearer\s+|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|token\s*[:=]|password\s*[:=]|secret\s*[:=]|\bsk-[a-z0-9_-]{8,})"
)


class WorkflowCardStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    NEEDS_INPUT = "needs_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"


class WorkflowCardTone(StrEnum):
    NEUTRAL = "neutral"
    PROGRESS = "progress"
    SUCCESS = "success"
    DANGER = "danger"
    MUTED = "muted"
    WARNING = "warning"


class WorkflowChildCard(BaseModel):
    """Bounded child outcome with no provider/external identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1, max_length=120)
    status: WorkflowChildStatus
    error_message: str | None = Field(default=None, max_length=500)


class WorkflowCard(BaseModel):
    """Finite server-owned workflow display contract consumed without UI inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_run_id: int = Field(ge=1)
    project_id: str | None = Field(default=None, max_length=255)
    capability_id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=200)
    status: WorkflowCardStatus
    display_label: str = Field(min_length=1, max_length=80)
    display_tone: WorkflowCardTone
    is_active: bool
    summary: str | None = Field(default=None, max_length=2_000)
    error_message: str | None = Field(default=None, max_length=500)
    started_at: datetime.datetime
    finished_at: datetime.datetime | None = None
    projected_at: datetime.datetime
    elapsed_ms: int = Field(ge=0)
    timing_complete: bool
    children: list[WorkflowChildCard] = Field(default_factory=list, max_length=20)
    artifact_links: list[dict[str, str]] = Field(default_factory=list, max_length=50)


_DISPLAY: dict[
    WorkflowRunStatus,
    tuple[WorkflowCardStatus, str, WorkflowCardTone, bool],
] = {
    WorkflowRunStatus.PENDING: (
        WorkflowCardStatus.QUEUED,
        "Queued",
        WorkflowCardTone.NEUTRAL,
        True,
    ),
    WorkflowRunStatus.RUNNING: (
        WorkflowCardStatus.IN_PROGRESS,
        "In progress",
        WorkflowCardTone.PROGRESS,
        True,
    ),
    WorkflowRunStatus.TRIGGERED: (
        WorkflowCardStatus.IN_PROGRESS,
        "In progress",
        WorkflowCardTone.PROGRESS,
        True,
    ),
    WorkflowRunStatus.INTERRUPTED: (
        WorkflowCardStatus.NEEDS_INPUT,
        "Needs input",
        WorkflowCardTone.WARNING,
        True,
    ),
    WorkflowRunStatus.SUCCEEDED: (
        WorkflowCardStatus.SUCCEEDED,
        "Complete",
        WorkflowCardTone.SUCCESS,
        False,
    ),
    WorkflowRunStatus.FAILED: (
        WorkflowCardStatus.FAILED,
        "Failed",
        WorkflowCardTone.DANGER,
        False,
    ),
    WorkflowRunStatus.CANCELLED: (
        WorkflowCardStatus.CANCELLED,
        "Cancelled",
        WorkflowCardTone.MUTED,
        False,
    ),
    WorkflowRunStatus.INDETERMINATE: (
        WorkflowCardStatus.INDETERMINATE,
        "Outcome unconfirmed",
        WorkflowCardTone.WARNING,
        False,
    ),
}

_DOCUMENTATION_CAPABILITY = "mcp.docs.generate_project_documentation"
_RECONCILABLE = frozenset(
    {
        WorkflowRunStatus.PENDING,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.TRIGGERED,
    }
)

ReconcileRun = Callable[[WorkflowRun, str | None], Awaitable[WorkflowRun]]


class AuthoritativeWorkflowCardProjector:
    """Owner/scope-bound card loader with optional exact-child reconciliation."""

    def __init__(
        self,
        repository: HarnessWorkflowRepository,
        provider: McpDocumentationProvider | None = None,
        *,
        reconcile_run: ReconcileRun | None = None,
        now: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider or McpDocumentationProvider()
        self._reconcile_run = reconcile_run
        self._now = now or (lambda: datetime.datetime.now(datetime.UTC))

    async def project_many(
        self,
        workflow_run_ids: Sequence[int],
        *,
        owner_user_id: int,
        conversation_id: int,
        project_scope: list[str] | None,
        access_token: str | None,
        reconcile: bool,
    ) -> list[WorkflowCard]:
        cards: list[WorkflowCard] = []
        for run_id in list(dict.fromkeys(workflow_run_ids))[:20]:
            try:
                run = await self._repository.get_run(
                    project_scope, run_id, owner_user_id=owner_user_id
                )
            except HarnessRunNotFoundError:
                continue
            if run.conversation_id != conversation_id:
                continue
            if reconcile and _eligible_for_reconciliation(run):
                try:
                    await self._reconcile_exact(run, access_token)
                except (
                    TimeoutError,
                    McpDocumentationProviderError,
                    HarnessWorkflowConflictError,
                ):
                    # A card read fails closed to the last canonical Ledger fact.
                    # The provider error never becomes a model/UI status guess.
                    pass
                run = await self._repository.get_run(
                    project_scope, run_id, owner_user_id=owner_user_id
                )
            cards.append(project_workflow_card(run, projected_at=self._now()))
        return cards

    async def _reconcile_exact(
        self, run: WorkflowRun, access_token: str | None
    ) -> WorkflowRun:
        if self._reconcile_run is not None:
            return await self._reconcile_run(run, access_token)
        result = await DocumentationWorkflowReconciler(
            self._repository, self._provider
        ).reconcile(run, access_token=access_token, force=False)
        return result.run


def project_workflow_card(
    run: WorkflowRun,
    *,
    label: str | None = None,
    projected_at: datetime.datetime | None = None,
) -> WorkflowCard:
    """Project one canonical Ledger aggregate without consulting activity history."""

    now = _aware(projected_at or datetime.datetime.now(datetime.UTC))
    started = _aware(run.started_at)
    status, display_label, tone, is_active = _DISPLAY[run.status]
    terminal_at = _terminal_at(run) if not is_active else None
    if is_active:
        elapsed_ms = _elapsed_ms(started, now)
        timing_complete = True
    elif terminal_at is not None:
        elapsed_ms = _elapsed_ms(started, terminal_at)
        timing_complete = True
    else:
        elapsed_ms = 0
        timing_complete = False
    return WorkflowCard(
        workflow_run_id=run.id,
        project_id=run.project_id,
        capability_id=run.capability_id,
        label=(label or _capability_label(run.capability_id))[:200],
        status=status,
        display_label=display_label,
        display_tone=tone,
        is_active=is_active,
        summary=_bounded(run.output_summary, 2_000),
        error_message=_bounded(run.error_message, 500),
        started_at=started,
        finished_at=terminal_at,
        projected_at=now,
        elapsed_ms=elapsed_ms,
        timing_complete=timing_complete,
        children=[
            WorkflowChildCard(
                kind=_safe_label(child.child_kind, 120, fallback="workflow child"),
                status=_child_status(child.status),
                error_message=_bounded(child.error_message, 500),
            )
            for child in run.children[:20]
        ],
        artifact_links=_safe_artifact_links(run.artifact_links),
    )


def _terminal_at(run: WorkflowRun) -> datetime.datetime | None:
    candidates: list[datetime.datetime | None] = [run.finished_at, run.cancelled_at]
    for child in run.children:
        if str(child.status).lower() not in {"succeeded", "failed", "cancelled"}:
            continue
        candidates.extend(
            [
                child.provider_finished_at,
                child.provider_timestamp,
                child.last_observed_at,
            ]
        )
    return next((_aware(item) for item in candidates if item is not None), None)


def _safe_artifact_links(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("path")
        label = item.get("label") or item.get("name")
        if (
            not isinstance(url, str)
            or not isinstance(label, str)
            or _UNSAFE_TEXT.search(url)
        ):
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"", "http", "https"}:
            continue
        if parsed.scheme == "" and not url.startswith("/"):
            continue
        links.append(
            {
                "label": _safe_label(label, 200, fallback="Artifact"),
                "url": url[:2_000],
                "kind": _safe_label(
                    str(item.get("kind") or "artifact"), 80, fallback="artifact"
                ),
            }
        )
    return links


def _capability_label(capability_id: str) -> str:
    return capability_id.rsplit(".", 1)[-1].replace("_", " ").strip().title() or "Workflow"


def _elapsed_ms(started: datetime.datetime, stopped: datetime.datetime) -> int:
    return max(0, int((_aware(stopped) - _aware(started)).total_seconds() * 1_000))


def _aware(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)


def _bounded(value: str | None, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return "[redacted]" if _UNSAFE_TEXT.search(value) else value[:limit]


def _child_status(value: Any) -> WorkflowChildStatus:
    try:
        return WorkflowChildStatus(str(value))
    except ValueError:
        return WorkflowChildStatus.UNKNOWN


def _safe_label(value: Any, limit: int, *, fallback: str) -> str:
    text = str(value).strip()
    if not text or _UNSAFE_TEXT.search(text):
        return fallback
    return text[:limit]


def _eligible_for_reconciliation(run: WorkflowRun) -> bool:
    return run.capability_id == _DOCUMENTATION_CAPABILITY and run.status in _RECONCILABLE


__all__ = [
    "WorkflowCard",
    "WorkflowCardStatus",
    "WorkflowCardTone",
    "WorkflowChildCard",
    "AuthoritativeWorkflowCardProjector",
    "project_workflow_card",
]
