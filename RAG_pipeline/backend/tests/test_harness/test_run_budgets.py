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
    """Tight limits for the general budget tests.

    The read-depth ceilings default to the base ceilings here, so a test that
    says ``max_tool_calls=1`` still means "one tool call, of any kind". Read
    depth is exercised explicitly in ``TestReadDepthBudget`` instead of leaking
    into every other assertion in this file.
    """

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
    values.setdefault("max_read_only_tool_calls", values["max_tool_calls"])
    values.setdefault("max_read_only_model_calls", values["max_model_calls"])
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


class TestReadDepthBudget:
    """Read depth is budgeted apart from consequential work.

    The shipped defaults are the contract under test: a read-only drill-down gets
    eight tool calls, and a run that writes keeps the base four.
    """

    def test_the_shipped_read_only_ceiling_is_eight_tool_calls(self) -> None:
        limits = LayeredBudgetLimits()

        assert limits.max_tool_calls == 4
        assert limits.read_only_tool_call_ceiling == 8
        assert limits.max_model_calls == 8
        assert limits.read_only_model_call_ceiling == 12
        # The reserve is untouched by the raise, so the answer-only calls survive.
        assert limits.synthesis_reserve_calls == 2

    def test_the_deployed_base_of_four_still_yields_eight_reads(self) -> None:
        """AGENT_CHAT_MAX_TOOL_CALLS=4 is what production sets; reads get eight."""
        controller = LayeredBudgetController(LayeredBudgetLimits(max_tool_calls=4))

        for _ in range(8):
            assert controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY) > 0
        with pytest.raises(AgentBudgetStop) as stopped:
            controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)

        assert stopped.value.reason is BudgetStopReason.TOOL_CALL_LIMIT
        assert controller.tool_calls == 8

    def test_a_write_cohort_keeps_the_base_tool_ceiling(self) -> None:
        controller = LayeredBudgetController(LayeredBudgetLimits())

        for _ in range(4):
            controller.before_tool(risk_tier=HarnessRiskTier.REVERSIBLE_WRITE)
        with pytest.raises(AgentBudgetStop) as stopped:
            controller.before_tool(risk_tier=HarnessRiskTier.REVERSIBLE_WRITE)

        assert stopped.value.reason is BudgetStopReason.TOOL_CALL_LIMIT
        assert controller.tool_calls == 4
        assert controller.consequential_tool_calls == 4

    def test_read_depth_is_never_convertible_into_write_capacity(self) -> None:
        """Four reads spend the whole base ceiling, so the fifth write is refused."""
        controller = LayeredBudgetController(LayeredBudgetLimits())
        for _ in range(4):
            controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)

        with pytest.raises(AgentBudgetStop) as stopped:
            controller.before_tool(risk_tier=HarnessRiskTier.REVERSIBLE_WRITE)

        assert stopped.value.reason is BudgetStopReason.TOOL_CALL_LIMIT
        assert controller.consequential_tool_calls == 0

    def test_one_consequential_call_retires_the_read_depth_ceiling(self) -> None:
        controller = LayeredBudgetController(LayeredBudgetLimits())
        controller.before_tool(risk_tier=HarnessRiskTier.REVERSIBLE_WRITE)
        for _ in range(3):
            controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)

        assert controller.read_depth_active is False
        with pytest.raises(AgentBudgetStop) as stopped:
            controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
        assert stopped.value.reason is BudgetStopReason.TOOL_CALL_LIMIT

    def test_zero_tool_calls_stays_a_kill_switch(self) -> None:
        """A disabled-tools turn must not be re-enabled by the read allowance."""
        limits = LayeredBudgetLimits(max_tool_calls=0)
        controller = LayeredBudgetController(limits)

        assert limits.read_only_tool_call_ceiling == 0
        with pytest.raises(AgentBudgetStop) as stopped:
            controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
        assert stopped.value.reason is BudgetStopReason.TOOL_CALL_LIMIT

    def test_a_base_ceiling_above_the_read_allowance_is_not_lowered(self) -> None:
        """Reads must never get less depth than the deployment allows writes."""
        limits = LayeredBudgetLimits(max_tool_calls=20, max_model_calls=40)

        assert limits.read_only_tool_call_ceiling == 20
        assert limits.read_only_model_call_ceiling == 40

    def test_the_deeper_model_budget_needs_a_read_to_unlock_it(self) -> None:
        """No tool call means no drill-down, so the base model ceiling applies."""
        limits = LayeredBudgetLimits(max_model_calls=4, synthesis_reserve_calls=1)
        controller = LayeredBudgetController(limits)
        for _ in range(3):
            controller.before_model(final_synthesis=False)

        with pytest.raises(AgentBudgetStop) as stopped:
            controller.before_model(final_synthesis=False)
        assert stopped.value.reason is BudgetStopReason.MODEL_CALL_LIMIT

        reading = LayeredBudgetController(limits)
        reading.before_model(final_synthesis=False)
        reading.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)
        assert reading.read_depth_active is True
        # Ceiling is now 12 with one reserved, so calls 2..11 are all permitted.
        for _ in range(10):
            assert reading.before_model(final_synthesis=False) > 0

    def test_the_snapshot_reports_which_ceilings_were_in_force(self) -> None:
        controller = LayeredBudgetController(LayeredBudgetLimits())
        controller.before_tool(risk_tier=HarnessRiskTier.READ_ONLY)

        snapshot = controller.snapshot()

        assert snapshot["max_tool_calls"] == 4
        assert snapshot["max_read_only_tool_calls"] == 8
        assert snapshot["max_read_only_model_calls"] == 12
        assert snapshot["consequential_tool_calls_used"] == 0
        assert snapshot["read_depth_active"] is True


def test_model_token_cost_uses_the_shared_pricing_authority() -> None:
    response = SimpleNamespace(result=[AIMessage(
        content="answer",
        usage_metadata={"input_tokens": 1_000_000, "output_tokens": 500_000, "total_tokens": 1_500_000},
    )])
    assert _model_cost(response, model_name="kimi-k2.5") == pytest.approx(0.6)
