"""Tests for configurable model pricing helpers."""

from mcp_server.services.model_pricing import (
    calculate_model_cost,
    calculate_total_cost,
    find_model_pricing,
    load_model_pricing,
)


def test_load_model_pricing_accepts_supported_rate_keys():
    pricing = load_model_pricing(
        """
        {
          "deepseek-v4-pro": {"input": 0.28, "output": 0.88},
          "moonshot/kimi-k2.6": {
            "input_usd_per_million": 0.15,
            "output_usd_per_million": 2.50
          }
        }
        """
    )

    assert pricing["deepseek-v4-pro"].input_per_million == 0.28
    assert pricing["deepseek-v4-pro"].output_per_million == 0.88
    assert pricing["moonshot/kimi-k2.6"].input_per_million == 0.15
    assert pricing["moonshot/kimi-k2.6"].output_per_million == 2.50


def test_find_model_pricing_falls_back_to_bare_model_name():
    pricing = load_model_pricing('{"deepseek-v4-pro":{"input":0.28,"output":0.88}}')

    assert find_model_pricing("openai/deepseek-v4-pro", pricing) == pricing["deepseek-v4-pro"]


def test_calculate_model_cost_returns_none_for_unknown_or_incomplete_usage():
    pricing = load_model_pricing('{"known":{"input":1,"output":2}}')

    assert calculate_model_cost("unknown", 100, 200, pricing) is None
    assert calculate_model_cost("known", None, 200, pricing) is None
    assert calculate_model_cost("known", 100, None, pricing) is None


def test_calculate_total_cost_requires_all_priced_usage():
    pricing = load_model_pricing(
        '{"analysis":{"input":1,"output":2},"writer":{"input":3,"output":4}}'
    )

    assert calculate_total_cost(
        [
            ("analysis", 1_000_000, 500_000),
            ("writer", 100_000, 200_000),
        ],
        pricing,
    ) == 3.1
    assert calculate_total_cost([("analysis", 10, 20), ("unknown", 10, 20)], pricing) is None
