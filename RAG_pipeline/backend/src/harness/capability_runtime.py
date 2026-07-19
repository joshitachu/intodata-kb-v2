"""Typed execution contracts shared by capability registries and handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from src.harness.conversation_state import ConversationTaskReference
from src.harness.models import CapabilityDefinition


class CapabilityObservationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    TRIGGERED = "triggered"
    FAILED = "failed"
    DENIED = "denied"


class ConversationTaskInputError(ValueError):
    """A workflow task reference/input is invalid for the current authority."""


class ConversationTaskConflictError(ValueError):
    """A workflow task version or reservation lost an optimistic race."""


class ConversationTaskExecutionError(RuntimeError):
    """A reserved task failed after deterministic orchestration began."""


class CapabilityExecutionContext(BaseModel):
    """Server-owned policy and scope supplied for one capability call."""

    user_id: int
    conversation_id: int | None = None
    project_scope: list[str] | None = None
    domain: str | None = None
    permissions: set[str] | None = None
    approvals: set[str] = Field(default_factory=set)
    access_token: str | None = None
    allow_writes_content: bool = False
    allow_external_side_effects: bool = False
    allowed_capability_ids: set[str] | None = None
    denied_capability_ids: set[str] = Field(default_factory=set)
    remaining_tool_calls: int | None = Field(default=None, ge=0)
    remaining_cost_usd: float | None = Field(default=None, ge=0)
    current_message_id: int | None = Field(default=None, ge=1)
    conversation_state_version: int | None = Field(default=None, ge=1)
    conversation_turn_claim_id: int | None = Field(default=None, ge=1)
    conversation_turn_claim_token: str | None = Field(default=None, exclude=True)


class CapabilityExecutionRequest(BaseModel):
    capability_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class CapabilityObservation(BaseModel):
    """Sanitized typed observation returned to every runtime transport."""

    capability_id: str
    status: CapabilityObservationStatus
    output_type: str | None = None
    summary: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    artifact_links: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    workflow_ref: dict[str, Any] | None = None
    receipt_ref: dict[str, Any] | None = None
    safe_details: dict[str, Any] = Field(default_factory=dict)
    task_ref: ConversationTaskReference | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {
            CapabilityObservationStatus.SUCCEEDED,
            CapabilityObservationStatus.TRIGGERED,
        }


EvidenceHandler = Callable[
    [dict[str, Any], CapabilityExecutionContext],
    Awaitable[Any],
]


@dataclass(frozen=True, slots=True)
class CapabilityHandlerServices:
    """Governed service adapters available to registered handlers."""

    docs_provider: Any = None
    projects_provider: Any = None
    connector_service: Any = None
    workflow_cancellation_controller: Any = None
    evidence_handlers: Mapping[str, EvidenceHandler] = field(default_factory=dict)
    pack_draft_service: Any = None
    conversation_state_repository: Any = None
    conversation_task_service: Any = None


class CapabilityHandler(Protocol):
    """A handler registration validated against one capability definition."""

    capability_id: str
    input_schema: Mapping[str, Any]
    output_type: str
    task_types: tuple[str, ...]

    async def __call__(
        self,
        capability: CapabilityDefinition,
        request: CapabilityExecutionRequest,
        context: CapabilityExecutionContext,
        services: CapabilityHandlerServices,
    ) -> CapabilityObservation: ...
