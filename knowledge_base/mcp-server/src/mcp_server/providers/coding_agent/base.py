"""Provider-neutral runtime and injected transport seams."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from mcp_server.providers.coding_agent.models import (
    AgentCapabilities,
    InvestigationCheckpoint,
    InvestigationRequest,
    InvestigationResult,
    InvestigationStatus,
    ResourceCeiling,
    TerminalInvestigationStatus,
    UsageReceipt,
)

LogCallback = Callable[[str, str, str], Awaitable[None]]


class IncompatibleCheckpointError(ValueError):
    """Raised before dispatch when durable state belongs to another investigation."""


class UnsupportedCodingAgentError(LookupError):
    """Raised when no real transport is registered for a requested provider."""


class ProviderExecutionRequest(BaseModel):
    """Provider adapter to transport request; contains no domain workflow concepts."""

    model_config = ConfigDict(extra="forbid")

    investigation_id: str
    project_id: str
    goal: str
    expected_outcome: str
    workspace: Path
    fact_graph: Path
    method_files: tuple[Path, ...] = ()
    tool_names: tuple[str, ...] = ()
    session_id: str | None = None
    checkpoint_state: dict[str, Any] = Field(default_factory=dict)
    initial_scope_seed: dict[str, Any] = Field(default_factory=dict)
    provider_options: dict[str, Any] = Field(default_factory=dict)
    resource_ceiling: ResourceCeiling = Field(default_factory=ResourceCeiling)


class ProviderExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TerminalInvestigationStatus
    session_id: str | None = None
    output: str = ""
    checkpoint_state: dict[str, Any] = Field(default_factory=dict)
    usage: UsageReceipt = Field(default_factory=UsageReceipt)
    stop_reason: str | None = None
    error: str | None = None


class ExecutionObserver:
    """Persists live progress so provider interruption cannot erase useful work."""

    def __init__(self, checkpoint: InvestigationCheckpoint, path: Path) -> None:
        self.checkpoint = checkpoint
        self.path = path
        self._baseline_usage = checkpoint.usage.model_copy(deep=True)
        self._observed_usage = UsageReceipt()
        self.persist()

    def session_started(self, session_id: str) -> None:
        self.checkpoint.session_id = session_id
        self.checkpoint.in_flight = {"session_id": session_id, "started_at": time.time()}
        self._advance()

    def record_checkpoint(
        self,
        state: dict[str, Any],
        *,
        discovered_scope: list[dict[str, Any]] | None = None,
        decisions: list[dict[str, Any]] | None = None,
        open_questions: list[dict[str, Any]] | None = None,
        accepted_artifacts: list[str] | None = None,
    ) -> None:
        self.checkpoint.state = dict(state)
        if discovered_scope is not None:
            self.checkpoint.discovered_scope = list(discovered_scope)
        if decisions is not None:
            self.checkpoint.decisions = list(decisions)
        if open_questions is not None:
            self.checkpoint.open_questions = list(open_questions)
        if accepted_artifacts is not None:
            self.checkpoint.accepted_artifacts = list(accepted_artifacts)
        self._advance()

    def record_usage(self, usage_delta: UsageReceipt) -> None:
        self._observed_usage = self._observed_usage.merged(usage_delta)
        self.checkpoint.usage = self._baseline_usage.merged(self._observed_usage)
        self._advance()

    def finalize_usage(self, authoritative_invocation_usage: UsageReceipt) -> None:
        """Replace provisional event deltas with the provider's final invocation total."""

        self._observed_usage = authoritative_invocation_usage.model_copy(deep=True)
        self.checkpoint.usage = self._baseline_usage.merged(self._observed_usage)
        self._advance()

    def finish(
        self, status: InvestigationStatus, *, in_flight: dict[str, Any] | None = None
    ) -> None:
        self.checkpoint.status = status
        self.checkpoint.in_flight = in_flight
        self._advance()

    def _advance(self) -> None:
        self.checkpoint.revision += 1
        self.checkpoint.updated_at = time.time()
        self.persist()

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(self.checkpoint.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


@runtime_checkable
class ExecutionTransport(Protocol):
    async def __call__(
        self,
        request: ProviderExecutionRequest,
        observer: ExecutionObserver,
    ) -> ProviderExecutionResult: ...


@runtime_checkable
class CodingAgentRuntime(Protocol):
    provider_id: str

    def capabilities(self) -> AgentCapabilities: ...

    async def start_or_resume(
        self,
        request: InvestigationRequest,
        *,
        checkpoint: InvestigationCheckpoint | None = None,
        log_cb: LogCallback | None = None,
    ) -> InvestigationResult: ...


def load_checkpoint(path: Path) -> InvestigationCheckpoint | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return InvestigationCheckpoint.model_validate(payload)
    except FileNotFoundError:
        return None


def assert_checkpoint_compatible(
    request: InvestigationRequest,
    checkpoint: InvestigationCheckpoint,
    *,
    provider_id: str,
    model: str,
) -> None:
    errors: list[str] = []
    if checkpoint.investigation_id != request.investigation_id:
        errors.append("investigation_id")
    if checkpoint.provider_id != provider_id:
        errors.append("provider")
    if checkpoint.model != model:
        errors.append("model")
    errors.extend(checkpoint.fingerprints.differences(request.fingerprints))
    if errors:
        raise IncompatibleCheckpointError(
            "checkpoint is incompatible with the current investigation: "
            + ", ".join(dict.fromkeys(errors))
        )
