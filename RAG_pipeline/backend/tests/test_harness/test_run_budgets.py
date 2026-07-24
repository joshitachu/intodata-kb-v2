from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from types import SimpleNamespace

from src.harness.models import HarnessRiskTier
from src.harness.run_budgets import (
    AgentBudgetStop,
    BudgetStopReason,
    LayeredBudgetController,
    LayeredBudgetLimits,
    _model_cost,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _limits(**updates) -> LayeredBudgetLimits:
    values = {
        "model_timeout_seconds": 5,
        "read_tool_timeout_seconds": 3,
        "write_tool_timeout_seconds": 4,
        "workflow_handoff_timeout_seconds": 2,
        "soft_deadline_seconds": 10,
        "hard_deadline_seconds": 20,
        "max_model_calls": 4,
        "synthesis_reserve_calls": 1,
        "max_tool_calls": 2,
        "max_cost_usd": 1,
    }
    values.update(updates)
    return LayeredBudgetLimits(**values)


def test_synthesis_call_is_reserved_after_normal_model_budget() -> None:
    controller = LayeredBudgetController(_limits())
    controller.before_model(final_synthesis=False)
    controller.before_model(final_synthesis=False)
    controller.before_model(final_synthesis=False)
    with pytest.raises(AgentBudgetStop) as stopped:
        controller.before_model(final_synthesis=False)
    assert stopped.value.reason is BudgetStopReason.MODEL_CALL_LIMIT

    fresh = LayeredBudgetController(_limits())
    for _ in range(3):
        fresh.before_model(final_synthesis=False)
    assert fresh.before_model(final_synthesis=True) > 0


def test_synthesis_call_is_reserved_after_tool_call_limit() -> None:
    controller = LayeredBudgetController(_limits(max_tool_calls=1))
    controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
    with pytest.raises(AgentBudgetStop) as stopped:
        controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
    assert stopped.value.reason is BudgetStopReason.TOOL_CALL_LIMIT
    assert controller.before_model(final_synthesis=True) > 0
    with pytest.raises(AgentBudgetStop):
        controller.before_model(final_synthesis=True)


def test_hard_stop_prevents_every_later_effect_start() -> None:
    clock = Clock()
    controller = LayeredBudgetController(_limits(), clock=clock)
    clock.now = 21
    with pytest.raises(AgentBudgetStop) as stopped:
        controller.before_tool(risk_tier=HarnessRiskTier.REVERSIBLE_WRITE)
    assert stopped.value.reason is BudgetStopReason.HARD_DEADLINE
    with pytest.raises(AgentBudgetStop) as repeated:
        controller.before_tool(risk_tier=HarnessRiskTier.REVERSIBLE_WRITE)
    assert repeated.value.reason is BudgetStopReason.HARD_DEADLINE
    assert controller.tool_calls == 0


def test_model_tool_cost_and_deadlines_have_distinct_reasons() -> None:
    handoff = LayeredBudgetController(_limits())
    assert handoff.before_tool(
        risk_tier=HarnessRiskTier.REVERSIBLE_WRITE, workflow_handoff=True
    ) == 2

    controller = LayeredBudgetController(_limits(max_tool_calls=1))
    controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
    with pytest.raises(AgentBudgetStop) as tool_limit:
        controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
    assert tool_limit.value.reason is BudgetStopReason.TOOL_CALL_LIMIT

    clock = Clock()
    soft = LayeredBudgetController(_limits(), clock=clock)
    clock.now = 11
    with pytest.raises(AgentBudgetStop) as soft_stop:
        soft.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
    assert soft_stop.value.reason is BudgetStopReason.SOFT_DEADLINE

    cost = LayeredBudgetController(_limits(max_cost_usd=0.1))
    with pytest.raises(AgentBudgetStop) as cost_stop:
        cost.before_tool(
            risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
            estimated_cost_usd=0.2,
        )
    assert cost_stop.value.reason is BudgetStopReason.COST_LIMIT


def test_model_token_cost_uses_the_shared_pricing_authority() -> None:
    response = SimpleNamespace(result=[AIMessage(
        content="answer",
        usage_metadata={"input_tokens": 1_000_000, "output_tokens": 500_000, "total_tokens": 1_500_000},
    )])
    assert _model_cost(response, model_name="kimi-k2.5") == pytest.approx(0.6)
