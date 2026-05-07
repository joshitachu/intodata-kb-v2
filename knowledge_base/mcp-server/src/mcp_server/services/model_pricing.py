"""Token-based model pricing helpers for generated-document usage tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from mcp_server.config import config


@dataclass(frozen=True)
class ModelPricing:
    """USD-per-million-token pricing for one model."""

    input_per_million: float
    output_per_million: float


def _rate_value(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return float(value)
    return None


def load_model_pricing(raw_json: str | None = None) -> dict[str, ModelPricing]:
    """Parse configured model pricing from JSON.

    Accepted shapes per model:
    - {"input": 0.28, "output": 0.88}
    - {"input_per_million": 0.28, "output_per_million": 0.88}
    - {"input_usd_per_million": 0.28, "output_usd_per_million": 0.88}
    """

    raw = raw_json if raw_json is not None else config.model_pricing_json
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("model pricing config must be a JSON object")

    pricing: dict[str, ModelPricing] = {}
    for model_name, rates in parsed.items():
        if not isinstance(model_name, str) or not isinstance(rates, dict):
            continue
        input_rate = _rate_value(rates, "input", "input_per_million", "input_usd_per_million")
        output_rate = _rate_value(rates, "output", "output_per_million", "output_usd_per_million")
        if input_rate is None or output_rate is None:
            continue
        pricing[model_name] = ModelPricing(input_rate, output_rate)
    return pricing


def model_pricing_candidates(model_name: str | None) -> Iterable[str]:
    """Yield supported lookup keys from full and bare model names."""

    if not model_name:
        return
    yield model_name
    if "/" in model_name:
        yield model_name.rsplit("/", 1)[1]


def find_model_pricing(
    model_name: str | None,
    pricing: dict[str, ModelPricing] | None = None,
) -> ModelPricing | None:
    """Find pricing by exact model key, falling back from provider/model to model."""

    pricing = pricing if pricing is not None else load_model_pricing()
    for candidate in model_pricing_candidates(model_name):
        rate = pricing.get(candidate)
        if rate is not None:
            return rate
    return None


def calculate_model_cost(
    model_name: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    pricing: dict[str, ModelPricing] | None = None,
) -> float | None:
    """Calculate USD cost for one model usage record."""

    rate = find_model_pricing(model_name, pricing)
    if rate is None or input_tokens is None or output_tokens is None:
        return None
    cost = (input_tokens / 1_000_000) * rate.input_per_million
    cost += (output_tokens / 1_000_000) * rate.output_per_million
    return round(cost, 6)


def calculate_total_cost(
    calls: Iterable[tuple[str | None, int | None, int | None]],
    pricing: dict[str, ModelPricing] | None = None,
) -> float | None:
    """Calculate total USD cost across calls, returning None if any priced call is unknown."""

    pricing = pricing if pricing is not None else load_model_pricing()
    total = 0.0
    saw_usage = False
    for model_name, input_tokens, output_tokens in calls:
        if (input_tokens or 0) == 0 and (output_tokens or 0) == 0:
            continue
        call_cost = calculate_model_cost(model_name, input_tokens, output_tokens, pricing)
        if call_cost is None:
            return None
        total += call_cost
        saw_usage = True
    if not saw_usage:
        return None
    return round(total, 6)
