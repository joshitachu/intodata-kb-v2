from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from mcp_server.experiments.domain.evaluation import UsageMetrics
from mcp_server.experiments.infrastructure.validation.cost import (
    calculate_costs,
    parse_frozen_price_card,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "agentic_experiments" / "evaluation" / "price_card_v1.json"


def test_price_card_is_strict_deterministic_and_decimal_precise() -> None:
    card = parse_frozen_price_card(FIXTURE.read_text(encoding="utf-8"))
    same_card = parse_frozen_price_card(FIXTURE.read_text(encoding="utf-8"))
    calculation = calculate_costs(
        UsageMetrics(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        ),
        provider_id="test-provider",
        model_id="test-model",
        price_card=card,
        billed_cost_usd=Decimal("2.50"),
    )

    assert card.receipt.sha256 == same_card.receipt.sha256
    assert calculation.metrics.billed_cost_usd == Decimal("2.50")
    assert calculation.metrics.normalized_cost_usd == Decimal("1.141111110")


def test_unknown_route_and_incomplete_usage_are_incomparable_not_imputed() -> None:
    card = parse_frozen_price_card(FIXTURE.read_text(encoding="utf-8"))
    unknown_route = calculate_costs(
        UsageMetrics(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0),
        provider_id="unknown",
        model_id="unknown",
        price_card=card,
    )
    incomplete = calculate_costs(
        UsageMetrics(input_tokens=0, output_tokens=None, cache_read_tokens=0, cache_write_tokens=0),
        provider_id="test-provider",
        model_id="test-model",
        price_card=card,
    )

    assert unknown_route.metrics.normalized_cost_usd is None
    assert "absent" in unknown_route.limitations[0]
    assert incomplete.metrics.normalized_cost_usd is None
    assert "incomplete" in incomplete.limitations[0]


def test_price_card_rejects_unknown_fields_and_non_decimal_rates() -> None:
    invalid = {
        "schema_version": "1.0",
        "price_card_id": "card",
        "version": "1",
        "currency": "USD",
        "rates": [],
        "unexpected": True,
    }
    with pytest.raises(ValueError, match="keys mismatch"):
        parse_frozen_price_card(invalid)
