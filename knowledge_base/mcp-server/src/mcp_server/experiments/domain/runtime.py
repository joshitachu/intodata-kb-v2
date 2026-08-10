"""Runtime-neutral contracts for bounded agentic experiment tasks.

This module intentionally has no HTTP, persistence, provider SDK, or OpenCode
dependency.  Application code can use these immutable DTOs with either the
offline fake runtime or a future runtime adapter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class RuntimeAvailability(StrEnum):
    """Whether a runtime health check can accept work."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class RuntimeTaskStatus(StrEnum):
    """Terminal and non-terminal normalized task states."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RuntimeEventCategory(StrEnum):
    """Stable categories for append-only runtime events.

    The payload owns provider-specific detail.  New categories can be added
    without exposing a provider payload type through the runtime port.
    """

    LIFECYCLE = "lifecycle"
    MESSAGE = "message"
    TOOL = "tool"
    USAGE = "usage"
    WARNING = "warning"
    ERROR = "error"


_SECRET_TOKENS = frozenset({"authorization", "credential", "password", "secret", "token", "api_key"})
_SAFE_TOKEN_METRIC_KEYS = frozenset(
    {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
)


def redact_payload(value: Any) -> Any:
    """Return a recursively redacted, serialization-safe event payload.

    Runtime adapters must pass payloads through this function before creating
    an event or persisting an error.  It deliberately keys off field *names*,
    not provider payload shapes, so the invariant is portable.
    """

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if (
                normalized in _SAFE_TOKEN_METRIC_KEYS
                and isinstance(nested, (int, float))
                and not isinstance(nested, bool)
            ):
                redacted[key_text] = nested
            elif any(token in normalized for token in _SECRET_TOKENS):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_payload(nested)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_payload(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    runtime_id: str
    runtime_version: str | None
    availability: RuntimeAvailability
    checked_at: datetime
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelCapability:
    """A discovered, provider/model-specific capability record.

    Provider and model identifiers are deliberately open strings.  A recipe
    can request generic capabilities without making this port vendor-aware.
    """

    provider_id: str
    model_id: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    available: bool = True
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    runtime_id: str
    runtime_version: str | None
    checked_at: datetime
    models: tuple[ModelCapability, ...] = ()
    supported_capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """A hash-addressed reference to data owned by the experiment artifact store."""

    logical_name: str
    sha256: str
    media_type: str
    byte_size: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeBudgets:
    """Hard ceilings enforced by the orchestrator/tool boundary, not a prompt."""

    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_tool_calls: int | None = None
    max_tool_bytes: int | None = None
    max_elapsed_ms: int | None = None
    max_billed_cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class OutputContract:
    """Runtime-neutral declaration of the artifact expected from one task."""

    artifact_name: str
    media_type: str
    required: bool = True
    max_bytes: int | None = None
    contract_ref: ArtifactReference | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTaskRequest:
    """Immutable, bounded work handed to an ``AgentRuntime``.

    ``semantic_prompt`` is Nexus-owned task text.  Context is passed as
    hash-addressed references so runtime adapters do not acquire unbounded
    filesystem access merely to execute a task.
    """

    experiment_id: str
    task_id: str
    idempotency_key: str
    provider_id: str
    model_id: str
    semantic_prompt: str
    semantic_prompt_sha256: str
    context_refs: tuple[ArtifactReference, ...]
    output_contract: OutputContract
    allowed_tool_names: tuple[str, ...]
    budgets: RuntimeBudgets
    deadline_at: datetime
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    agent_id: str | None = None
    runtime_options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeTaskHandle:
    """Opaque runtime task identity retained by the orchestrator."""

    runtime_id: str
    session_id: str
    message_id: str | None
    experiment_id: str
    task_id: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RuntimeTaskEvent:
    """A stable append-only event suitable for an event ledger."""

    event_id: str
    sequence: int
    category: RuntimeEventCategory
    occurred_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    task_status: RuntimeTaskStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", redact_payload(self.payload))


@dataclass(frozen=True, slots=True)
class RuntimeUsage:
    """Usage fields remain ``None`` when a runtime/provider does not report them."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    billed_cost_usd: float | None = None
    normalized_cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTimings:
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_ms: int | None = None
    provider_latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTaskResult:
    """Normalized terminal result; failure detail is categorized and redacted."""

    status: RuntimeTaskStatus
    runtime_id: str
    runtime_version: str | None
    provider_id: str
    model_id: str
    session_id: str | None
    message_id: str | None
    output_refs: tuple[ArtifactReference, ...] = ()
    usage: RuntimeUsage = field(default_factory=RuntimeUsage)
    timings: RuntimeTimings = field(default_factory=RuntimeTimings)
    retry_count: int = 0
    tool_calls: int = 0
    tool_bytes: int = 0
    failure_category: str | None = None
    failure_detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "failure_detail", redact_payload(self.failure_detail))


@dataclass(frozen=True, slots=True)
class CancelRequest:
    experiment_id: str
    task_id: str
    session_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class CancelResult:
    accepted: bool
    status: RuntimeTaskStatus
    runtime_id: str
    session_id: str
    requested_at: datetime
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", redact_payload(self.detail))


@runtime_checkable
class AgentRuntime(Protocol):
    """Replaceable, async runtime port used by experiment orchestration."""

    async def health(self) -> RuntimeHealth: ...

    async def capabilities(self) -> RuntimeCapabilities: ...

    async def start_task(self, request: RuntimeTaskRequest) -> RuntimeTaskHandle: ...

    def observe(
        self,
        handle: RuntimeTaskHandle,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[RuntimeTaskEvent]: ...

    async def poll_status(self, handle: RuntimeTaskHandle) -> RuntimeTaskStatus:
        """Cheap, frequent liveness check -- must not read a full transcript.

        Callers poll this in a tight loop for the entire life of a task, so an
        implementation backed by a growing conversation must answer from a
        constant-size status signal, not by re-fetching and re-parsing
        everything said so far. Reserve that expensive work for ``result()``,
        which is called once, on completion.
        """
        ...

    async def result(self, handle: RuntimeTaskHandle) -> RuntimeTaskResult: ...

    async def cancel(self, request: CancelRequest) -> CancelResult: ...
