"""Layered model/tool/deadline/cost budgets for the standard runtime."""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict, Field

from src.harness.models import HarnessRiskTier
from src.generation.usage import MODEL_PRICING


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
    read_tool_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    write_tool_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    workflow_handoff_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    soft_deadline_seconds: float = Field(default=45.0, gt=0, le=300)
    hard_deadline_seconds: float = Field(default=120.0, gt=0, le=600)
    max_model_calls: int = Field(default=8, ge=1, le=64)
    synthesis_reserve_calls: int = Field(default=2, ge=1, le=4)
    max_tool_calls: int = Field(default=4, ge=0, le=32)
    max_cost_usd: float = Field(default=0.25, ge=0, le=100)

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
        self.cost_usd = 0.0
        self.stop_reason: BudgetStopReason | None = None
        self._post_stop_synthesis_calls = 0

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started)

    @property
    def stopped(self) -> bool:
        return self.stop_reason is not None

    def before_model(self, *, final_synthesis: bool) -> float:
        if self.stop_reason is not None:
            return self._before_post_stop_synthesis(final_synthesis=final_synthesis)
        self._require_open()
        self._check_hard_deadline()
        if self.cost_usd >= self.limits.max_cost_usd:
            self._stop(BudgetStopReason.COST_LIMIT)
        if self.model_calls >= self.limits.max_model_calls:
            self._stop(BudgetStopReason.MODEL_CALL_LIMIT)
        non_synthesis_limit = self.limits.max_model_calls - self.limits.synthesis_reserve_calls
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
        if self.model_calls >= self.limits.max_model_calls:
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
        if self.tool_calls >= self.limits.max_tool_calls:
            self._stop(BudgetStopReason.TOOL_CALL_LIMIT)
        if self.cost_usd + max(0.0, estimated_cost_usd) > self.limits.max_cost_usd:
            self._stop(BudgetStopReason.COST_LIMIT)
        self.tool_calls += 1
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
    result = getattr(response, "result", None)
    messages = result if isinstance(result, list) else [result] if result is not None else []
    explicit_total = 0.0
    input_tokens = 0
    output_tokens = 0
    for message in messages:
        metadata = getattr(message, "response_metadata", {}) or {}
        value = metadata.get("cost_usd")
        if isinstance(value, (int, float)):
            explicit_total += max(0.0, float(value))
        usage = getattr(message, "usage_metadata", {}) or {}
        input_tokens += max(0, int(usage.get("input_tokens", 0) or 0))
        output_tokens += max(0, int(usage.get("output_tokens", 0) or 0))
    if explicit_total:
        return explicit_total
    pricing = MODEL_PRICING.get(model_name)
    if pricing is None:
        return 0.0
    return (input_tokens / 1_000_000) * pricing[0] + (output_tokens / 1_000_000) * pricing[1]


__all__ = [
    "AgentBudgetStop",
    "BudgetStopReason",
    "LayeredBudgetController",
    "LayeredBudgetLimits",
    "LayeredModelBudgetMiddleware",
]
