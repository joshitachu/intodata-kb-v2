"""Runtime-neutral request-building seam for staged Grounded role execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mcp_server.experiments.domain.runtime import RuntimeTaskRequest


BOUNDED_BUDGET_MONITORING_CAPABILITY = "bounded-budget-monitoring"


@dataclass(frozen=True, slots=True)
class GroundedRoleRuntimeContext:
    """Nexus-owned inputs from which a runtime adapter builds one role request."""

    experiment_id: str
    role_id: str
    agent_id: str
    prompt: str
    packet_root: Path
    runtime_directory: Path
    owned_artifacts: tuple[str, ...]
    provider_id: str
    model_id: str
    attempt: int
    max_context_bytes: int
    max_input_tokens: int
    max_tool_calls: int
    timeout_seconds: float
    max_billed_cost_usd: float | None = None


class GroundedRuntimeTaskBuilder(Protocol):
    """Adapter seam for runtime-specific context and tool binding."""

    def build(self, context: GroundedRoleRuntimeContext) -> RuntimeTaskRequest: ...
