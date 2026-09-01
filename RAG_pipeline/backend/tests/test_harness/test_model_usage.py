"""Prompt-cache accounting on the harness model path.

The v2 chat cohorts build their own ``ChatOpenAI`` and never pass through
``src/generation/providers/**``, so the provider layer's cache accounting cannot
reach them. These tests pin the harness-side equivalent: cached prompt tokens are
read from LangChain metadata, priced with the shared tables, and never allowed to
understate cost.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.generation.usage import MODEL_CACHE_READ_DISCOUNT, MODEL_PRICING
from src.harness.agent_loop import AgentUsage
from src.harness.model_usage import (
    ModelTokenUsage,
    model_cost_usd,
    token_usage_from_message,
    token_usage_from_messages,
)
from src.harness.readonly_runtime import _accumulate_usage
from src.harness.run_budgets import _model_cost


def _kimi_message(
    *, input_tokens: int, output_tokens: int, cache_read: int | None = None
) -> AIMessage:
    metadata: dict[str, object] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if cache_read is not None:
        metadata["input_token_details"] = {"cache_read": cache_read}
    return AIMessage(content="answer", usage_metadata=metadata)


def test_cache_read_is_extracted_as_a_subset_of_the_prompt_total() -> None:
    usage = token_usage_from_message(
        _kimi_message(input_tokens=3_023, output_tokens=100, cache_read=2_816)
    )
    # LangChain reports `input_tokens` inclusive of the cache counts, and that
    # invariant is preserved rather than re-derived.
    assert usage.input_tokens == 3_023
    assert usage.cached_input_tokens == 2_816
    assert usage.cache_write_tokens == 0


def test_a_message_without_cache_details_reports_no_cached_tokens() -> None:
    usage = token_usage_from_message(_kimi_message(input_tokens=500, output_tokens=10))
    assert usage == ModelTokenUsage(input_tokens=500, output_tokens=10)


def test_a_cache_count_larger_than_the_prompt_cannot_reduce_billable_tokens() -> None:
    usage = token_usage_from_message(
        _kimi_message(input_tokens=100, output_tokens=10, cache_read=9_999)
    )
    assert usage.cached_input_tokens == 100
    assert usage.cache_write_tokens == 0
    # Never negative: `uncached_input_tokens` must not be able to credit us.
    assert model_cost_usd(usage, model_name="kimi-k2.6") > 0


def test_non_ai_messages_and_missing_metadata_are_ignored() -> None:
    total = token_usage_from_messages(
        [
            HumanMessage(content="question"),
            AIMessage(content="no metadata"),
            _kimi_message(input_tokens=10, output_tokens=2, cache_read=5),
        ]
    )
    assert total == ModelTokenUsage(
        input_tokens=10, output_tokens=2, cached_input_tokens=5
    )


def test_cached_prompt_tokens_are_billed_at_the_published_discount() -> None:
    input_price, output_price = MODEL_PRICING["kimi-k2.6"]
    discount = MODEL_CACHE_READ_DISCOUNT["kimi-k2.6"]
    usage = ModelTokenUsage(
        input_tokens=1_000_000, output_tokens=0, cached_input_tokens=900_000
    )
    expected = (100_000 * input_price + 900_000 * input_price * discount) / 1_000_000
    assert model_cost_usd(usage, model_name="kimi-k2.6") == pytest.approx(expected)
    del output_price


def test_a_model_with_no_confirmed_discount_still_bills_cache_at_full_price() -> None:
    """An unverified saving must never surface as revenue in the dashboard."""

    usage = ModelTokenUsage(
        input_tokens=1_000_000, output_tokens=0, cached_input_tokens=900_000
    )
    assert "moonshot-v1-128k" not in MODEL_CACHE_READ_DISCOUNT
    flat = ModelTokenUsage(input_tokens=1_000_000, output_tokens=0)
    assert model_cost_usd(usage, model_name="moonshot-v1-128k") == pytest.approx(
        model_cost_usd(flat, model_name="moonshot-v1-128k")
    )


def test_an_unknown_model_is_recorded_but_not_billed() -> None:
    usage = ModelTokenUsage(input_tokens=1_000, output_tokens=100)
    assert model_cost_usd(usage, model_name="not-a-real-model") == 0.0


def test_the_budget_controller_prices_a_cached_call_below_a_flat_one() -> None:
    cached = SimpleNamespace(
        result=[_kimi_message(input_tokens=3_023, output_tokens=100, cache_read=2_816)]
    )
    flat = SimpleNamespace(
        result=[_kimi_message(input_tokens=3_023, output_tokens=100)]
    )
    cached_cost = _model_cost(cached, model_name="kimi-k2.6")
    flat_cost = _model_cost(flat, model_name="kimi-k2.6")
    assert cached_cost < flat_cost
    # 93% of this prompt is cached at 0.175x, so the saving is large, not marginal.
    assert cached_cost == pytest.approx(0.00027, abs=1e-6)
    assert flat_cost == pytest.approx(0.000967, abs=1e-6)


def test_a_provider_billed_figure_still_wins_over_the_local_table() -> None:
    response = SimpleNamespace(
        result=[
            AIMessage(
                content="answer",
                usage_metadata={
                    "input_tokens": 3_023,
                    "output_tokens": 100,
                    "total_tokens": 3_123,
                    "input_token_details": {"cache_read": 2_816},
                },
                response_metadata={"cost_usd": 0.5},
            )
        ]
    )
    assert _model_cost(response, model_name="kimi-k2.6") == pytest.approx(0.5)


def test_turn_usage_accumulates_the_cache_breakdown_across_model_calls() -> None:
    usage = _accumulate_usage(
        AgentUsage(),
        [_kimi_message(input_tokens=1_000, output_tokens=50, cache_read=800)],
    )
    usage = _accumulate_usage(
        usage,
        [_kimi_message(input_tokens=2_000, output_tokens=60, cache_read=1_900)],
    )
    assert usage.input_tokens == 3_000
    assert usage.output_tokens == 110
    assert usage.cached_input_tokens == 2_700
    assert usage.cache_hit_ratio == pytest.approx(0.9)


def test_agent_usage_cache_ratio_is_safe_on_an_empty_prompt() -> None:
    assert AgentUsage().cache_hit_ratio == 0.0
