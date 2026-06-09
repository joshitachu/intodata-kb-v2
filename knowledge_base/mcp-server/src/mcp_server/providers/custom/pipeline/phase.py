"""Phase ABC, PhaseResult, and PhaseEstimate — the contract every pipeline phase must satisfy."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp_server.providers.base import DocGenContext, LogCallback
    from mcp_server.providers.custom.models import ExecutionPlan, HarnessRunState


@dataclass
class PhaseEstimate:
    phase_name: str
    calls_min: int
    calls_max: int
    skipped: bool = False
    notes: str = ""


@dataclass
class PhaseResult:
    phase_name: str
    success: bool
    was_cached: bool = False
    skipped: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Phase(ABC):
    """Contract every pipeline phase must satisfy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and checkpoints, e.g. 'phase1'."""
        ...

    @abstractmethod
    def should_skip(self, plan: "ExecutionPlan") -> bool:
        """Return True if this phase should be omitted for the given execution plan."""
        ...

    @abstractmethod
    async def run(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        """Execute the phase from scratch."""
        ...

    @abstractmethod
    async def resume(
        self,
        ctx: "DocGenContext",
        state: "HarnessRunState",
        log_cb: "LogCallback",
    ) -> PhaseResult:
        """Resume a partially-completed phase. Check artifacts; fall back to run() if incomplete."""
        ...

    @abstractmethod
    def estimate(
        self,
        groups: list[str],
        docs: list[str],
        facts_unit_count: int,
    ) -> PhaseEstimate:
        """Return a deterministic call-count estimate without executing anything."""
        ...
