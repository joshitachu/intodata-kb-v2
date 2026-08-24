"""Layered model/tool/deadline/cost budgets for the standard runtime.

Read depth is budgeted separately from consequential work. Before this split
there was one ``max_tool_calls`` ceiling for everything, and the deployed value
is 4 (``AGENT_CHAT_MAX_TOOL_CALLS``). A genuine structural drill-down --
resolve an edge, open the design that implements it, read the field lineage,
cross-check the documentation -- needs four to five reads before the model has
anything to answer with, so 4 stopped runs mid-investigation on
``tool_call_limit``. Raising the single ceiling would have widened the write
budget at the same time, which is not what a read-depth problem needs.

``max_read_only_tool_calls`` / ``max_read_only_model_calls`` therefore apply only
while a run has done read-only work and nothing else. The moment a run makes one
consequential tool call, the base ceilings apply again for the rest of the run,
so the number of consequential calls a run can make never increases.
"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict, Field

from src.harness.models import HarnessRiskTier
from src.harness.model_usage import model_cost_usd, token_usage_from_messages


class BudgetStopReason(StrEnum):
    MODEL_TIMEOUT = "model_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    SOFT_DEADLINE = "soft_deadline_exceeded"
    HARD_DEADLINE = "hard_deadline_exceeded"
    MODEL_CALL_LIMIT = "model_call_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    COST_LIMIT = "cost_budget_exceeded"


class AgentBudgetStop(RuntimeError):
    def __init__(self, reason: BudgetStopReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class LayeredBudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    # Upper bounds match `Settings`: `reconcile_turn_deadlines` raises the read
    # tool timeout to the hard deadline so a finished answer is never discarded
    # by a limit below the turn's own, and derives the soft deadline from it.
    read_tool_timeout_seconds: float = Field(default=20.0, gt=0, le=600)
    write_tool_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    workflow_handoff_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    soft_deadline_seconds: float = Field(default=45.0, gt=0, le=600)
    hard_deadline_seconds: float = Field(default=120.0, gt=0, le=600)
    max_model_calls: int = Field(default=8, ge=1, le=64)
    synthesis_reserve_calls: int = Field(default=2, ge=1, le=4)
    max_tool_calls: int = Field(default=4, ge=0, le=32)
    # Read-depth ceilings. Not simply larger defaults on the two fields above,
    # because those also bound consequential work; see the module docstring.
    max_read_only_tool_calls: int = Field(default=8, ge=0, le=32)
    max_read_only_model_calls: int = Field(default=12, ge=1, le=64)
    max_cost_usd: float = Field(default=0.25, ge=0, le=100)

    @property
    def read_only_tool_call_ceiling(self) -> int:
        """Tool-call ceiling for a run that has read only, and read something.

        Zero is a kill switch, not a small number: ``max_tool_calls == 0`` means
        this turn gets no tools at all, and a read-depth allowance must never
        re-enable them. Above zero the ceiling is the larger of the two values,
        so a deployment that configures a base ceiling above the read allowance
        is never given LESS depth for reads than it allows for writes.
        """

        if self.max_tool_calls == 0:
            return 0
        return max(self.max_tool_calls, self.max_read_only_tool_calls)

    @property
    def read_only_model_call_ceiling(self) -> int:
        """Model-call ceiling for a run that has read only, and read something.

        Raised in proportion to the tool ceiling: each extra read costs one model
        call to request and one to reason over, so a read budget that is twice as
        deep needs more than two extra model calls to be usable.
        ``synthesis_reserve_calls`` is subtracted from whichever ceiling is in
        force, so the reserved answer-only calls survive the raise unchanged.
        """

        return max(self.max_model_calls, self.max_read_only_model_calls)

    def model_dump_public(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class LayeredBudgetController:
    """Monotonic authority checked before every model, tool, and effect start."""

    def __init__(
        self,
        limits: LayeredBudgetLimits,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limits.synthesis_reserve_calls >= limits.max_model_calls:
            raise ValueError("At least one non-synthesis model call must remain")
        if limits.soft_deadline_seconds >= limits.hard_deadline_seconds:
            raise ValueError("Soft deadline must precede hard deadline")
        self.limits = limits
        self._clock = clock
        self._started = clock()
        self.model_calls = 0
        self.tool_calls = 0
        #: Consequential tool calls charged so far. One is enough to retire the
        #: read-depth ceilings for the rest of the run.
        self.consequential_tool_calls = 0
        self.cost_usd = 0.0
        self.stop_reason: BudgetStopReason | None = None
        self._post_stop_synthesis_calls = 0

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started)

    @property
    def stopped(self) -> bool:
        return self.stop_reason is not None

    @property
    def read_depth_active(self) -> bool:
        """Whether this run has earned the read-depth ceilings.

        Both conditions are load-bearing. ``consequential_tool_calls == 0``
        keeps write work on the base budget. ``tool_calls > 0`` means the raise
        is granted only once the run has actually read something: a turn that has
        called no tool at all is not a drill-down, and letting it spend the
        deeper model budget on pure generation is how a run talks itself past the
        base ceiling without reading anything.
        """

        return self.consequential_tool_calls == 0 and self.tool_calls > 0

    def _model_call_ceiling(self) -> int:
        if self.read_depth_active:
            return self.limits.read_only_model_call_ceiling
        return self.limits.max_model_calls

    def _tool_call_ceiling(self, risk_tier: HarnessRiskTier) -> int:
        if risk_tier is HarnessRiskTier.READ_ONLY and self.consequential_tool_calls == 0:
            return self.limits.read_only_tool_call_ceiling
        return self.limits.max_tool_calls

    def before_model(self, *, final_synthesis: bool) -> float:
        if self.stop_reason is not None:
            return self._before_post_stop_synthesis(final_synthesis=final_synthesis)
        self._require_open()
        self._check_hard_deadline()
        if self.cost_usd >= self.limits.max_cost_usd:
            self._stop(BudgetStopReason.COST_LIMIT)
        ceiling = self._model_call_ceiling()
        if self.model_calls >= ceiling:
            self._stop(BudgetStopReason.MODEL_CALL_LIMIT)
        non_synthesis_limit = ceiling - self.limits.synthesis_reserve_calls
        if not final_synthesis and self.model_calls >= non_synthesis_limit:
            self._stop(BudgetStopReason.MODEL_CALL_LIMIT)
        if self.elapsed_seconds >= self.limits.soft_deadline_seconds and not final_synthesis:
            self._stop(BudgetStopReason.SOFT_DEADLINE)
        self.model_calls += 1
        return min(self.limits.model_timeout_seconds, self.remaining_hard_seconds())

    def _before_post_stop_synthesis(self, *, final_synthesis: bool) -> float:
        """Allow the reserved answer-only call after a bounded tool-loop stop."""

        if (
            not final_synthesis
            or self.stop_reason
            not in {BudgetStopReason.TOOL_CALL_LIMIT, BudgetStopReason.SOFT_DEADLINE}
            or self._post_stop_synthesis_calls >= self.limits.synthesis_reserve_calls
        ):
            raise AgentBudgetStop(self.stop_reason)
        self._check_hard_deadline()
        if self.cost_usd >= self.limits.max_cost_usd:
            self._stop(BudgetStopReason.COST_LIMIT)
        if self.model_calls >= self._model_call_ceiling():
            self._stop(BudgetStopReason.MODEL_CALL_LIMIT)
        self._post_stop_synthesis_calls += 1
        self.model_calls += 1
        return min(self.limits.model_timeout_seconds, self.remaining_hard_seconds())

    def record_model_usage(self, *, cost_usd: float = 0.0) -> None:
        self.cost_usd += max(0.0, float(cost_usd))
        if self.cost_usd > self.limits.max_cost_usd:
            self.stop_reason = BudgetStopReason.COST_LIMIT

    def before_tool(
        self,
        *,
        risk_tier: HarnessRiskTier,
        estimated_cost_usd: float = 0.0,
        workflow_handoff: bool = False,
    ) -> float:
        self._require_open()
        self._check_hard_deadline()
        if self.elapsed_seconds >= self.limits.soft_deadline_seconds:
            self._stop(BudgetStopReason.SOFT_DEADLINE)
        # Charged against the ceiling for THIS call's risk tier. A run may take
        # more read calls than it may take write calls, and the first write call
        # is measured against the base ceiling including the reads already spent,
        # so read depth can never be converted into extra write capacity.
        if self.tool_calls >= self._tool_call_ceiling(risk_tier):
            self._stop(BudgetStopReason.TOOL_CALL_LIMIT)
        if self.cost_usd + max(0.0, estimated_cost_usd) > self.limits.max_cost_usd:
            self._stop(BudgetStopReason.COST_LIMIT)
        self.tool_calls += 1
        if risk_tier is not HarnessRiskTier.READ_ONLY:
            self.consequential_tool_calls += 1
        self.cost_usd += max(0.0, estimated_cost_usd)
        configured = (
            self.limits.workflow_handoff_timeout_seconds
            if workflow_handoff
            else self.limits.read_tool_timeout_seconds
            if risk_tier is HarnessRiskTier.READ_ONLY
            else self.limits.write_tool_timeout_seconds
        )
        return min(configured, self.remaining_hard_seconds())

    def timeout(self, reason: BudgetStopReason) -> None:
        self.stop_reason = reason

    def remaining_hard_seconds(self) -> float:
        remaining = self.limits.hard_deadline_seconds - self.elapsed_seconds
        if remaining <= 0:
            self._stop(BudgetStopReason.HARD_DEADLINE)
        return remaining

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.limits.model_dump_public(),
            "model_calls_used": self.model_calls,
            "tool_calls_used": self.tool_calls,
            "consequential_tool_calls_used": self.consequential_tool_calls,
            "read_depth_active": self.read_depth_active,
            "cost_usd_used": round(self.cost_usd, 6),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "stop_reason": self.stop_reason.value if self.stop_reason else None,
        }

    def _check_hard_deadline(self) -> None:
        if self.elapsed_seconds >= self.limits.hard_deadline_seconds:
            self._stop(BudgetStopReason.HARD_DEADLINE)

    def _require_open(self) -> None:
        if self.stop_reason is not None:
            raise AgentBudgetStop(self.stop_reason)

    def _stop(self, reason: BudgetStopReason) -> None:
        self.stop_reason = reason
        raise AgentBudgetStop(reason)


class LayeredModelBudgetMiddleware(AgentMiddleware):
    """Per-model timeout/count/cost enforcement for ``create_agent``."""

    def __init__(self, controller: LayeredBudgetController, *, model_name: str = "") -> None:
        self.controller = controller
        self.model_name = model_name.split("/", 1)[-1]

    async def awrap_model_call(self, request, handler):  # noqa: ANN001, ANN201
        final_synthesis = (
            bool(request.messages)
            and isinstance(request.messages[-1], ToolMessage)
        ) or self.controller.stop_reason in {
            BudgetStopReason.TOOL_CALL_LIMIT,
            BudgetStopReason.SOFT_DEADLINE,
        }
        timeout = self.controller.before_model(final_synthesis=final_synthesis)
        try:
            async with asyncio.timeout(timeout):
                response = await handler(request)
        except TimeoutError as exc:
            self.controller.timeout(BudgetStopReason.MODEL_TIMEOUT)
            raise AgentBudgetStop(BudgetStopReason.MODEL_TIMEOUT) from exc
        self.controller.record_model_usage(
            cost_usd=_model_cost(response, model_name=self.model_name)
        )
        return response


def _model_cost(response: Any, *, model_name: str) -> float:
    """Price one wrapped model call, discounting prompt tokens served from cache.

    Previously this reimplemented the input/output price formula and read only the
    two top-level token counts, so a provider with an automatic prefix cache
    (Moonshot/Kimi reports most of the prompt as ``cache_read``) was billed at the
    full input rate on every turn. Pricing now comes from
    ``src.generation.usage``, which is the single source of truth for rates and
    cache discounts.
    """

    result = getattr(response, "result", None)
    messages = result if isinstance(result, list) else [result] if result is not None else []
    explicit_total = 0.0
    for message in messages:
        metadata = getattr(message, "response_metadata", {}) or {}
        value = metadata.get("cost_usd")
        if isinstance(value, (int, float)):
            explicit_total += max(0.0, float(value))
    if explicit_total:
        # A provider that bills us an exact figure wins over any local table.
        return explicit_total
    return model_cost_usd(
        token_usage_from_messages(messages), model_name=model_name
    )


__all__ = [
    "AgentBudgetStop",
    "BudgetStopReason",
    "LayeredBudgetController",
    "LayeredBudgetLimits",
    "LayeredModelBudgetMiddleware",
]
