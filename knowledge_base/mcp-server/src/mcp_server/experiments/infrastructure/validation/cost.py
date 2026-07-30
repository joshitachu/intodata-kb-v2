"""Strict frozen price-card parsing and Decimal-only cost normalization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp_server.experiments.domain.evaluation import CostMetrics, PriceCardReceipt, UsageMetrics


_CARD_KEYS = {"schema_version", "price_card_id", "version", "currency", "rates"}
_RATE_KEYS = {
    "provider_id",
    "model_id",
    "input_usd_per_million",
    "output_usd_per_million",
    "cache_read_usd_per_million",
    "cache_write_usd_per_million",
}
_MILLION = Decimal("1000000")


def _strict_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{context} keys mismatch; missing={missing}, extra={extra}")


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


def _decimal(value: Any, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field_name} must be a valid decimal") from error
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field_name} must be a finite non-negative decimal")
    return parsed


@dataclass(frozen=True, slots=True)
class ProviderModelRate:
    provider_id: str
    model_id: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cache_read_usd_per_million: Decimal
    cache_write_usd_per_million: Decimal


@dataclass(frozen=True, slots=True)
class FrozenPriceCard:
    schema_version: str
    receipt: PriceCardReceipt
    rates: tuple[ProviderModelRate, ...]

    def rate_for(self, provider_id: str, model_id: str) -> ProviderModelRate | None:
        for rate in self.rates:
            if rate.provider_id == provider_id and rate.model_id == model_id:
                return rate
        return None


@dataclass(frozen=True, slots=True)
class CostCalculation:
    metrics: CostMetrics
    limitations: tuple[str, ...] = ()


def _canonical_payload(schema_version: str, receipt: PriceCardReceipt, rates: tuple[ProviderModelRate, ...]) -> str:
    serialised_rates = [
        {
            "provider_id": rate.provider_id,
            "model_id": rate.model_id,
            "input_usd_per_million": str(rate.input_usd_per_million),
            "output_usd_per_million": str(rate.output_usd_per_million),
            "cache_read_usd_per_million": str(rate.cache_read_usd_per_million),
            "cache_write_usd_per_million": str(rate.cache_write_usd_per_million),
        }
        for rate in rates
    ]
    return json.dumps(
        {
            "schema_version": schema_version,
            "price_card_id": receipt.price_card_id,
            "version": receipt.version,
            "currency": receipt.currency,
            "rates": serialised_rates,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def parse_frozen_price_card(payload: str | bytes | Mapping[str, Any]) -> FrozenPriceCard:
    """Parse a complete price card; no fallback rate or provider special case exists."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("price card must be valid JSON") from error
    else:
        decoded = dict(payload)
    if not isinstance(decoded, dict):
        raise ValueError("price card must be a JSON object")
    _strict_keys(decoded, _CARD_KEYS, "price card")
    schema_version = _required_string(decoded["schema_version"], "schema_version")
    price_card_id = _required_string(decoded["price_card_id"], "price_card_id")
    version = _required_string(decoded["version"], "version")
    currency = _required_string(decoded["currency"], "currency")
    if currency != "USD":
        raise ValueError("price card currency must be USD for USD cost metrics")
    raw_rates = decoded["rates"]
    if not isinstance(raw_rates, list):
        raise ValueError("rates must be a JSON array")
    rates: list[ProviderModelRate] = []
    for index, raw_rate in enumerate(raw_rates):
        if not isinstance(raw_rate, dict):
            raise ValueError(f"rates[{index}] must be an object")
        _strict_keys(raw_rate, _RATE_KEYS, f"rates[{index}]")
        rates.append(
            ProviderModelRate(
                provider_id=_required_string(raw_rate["provider_id"], "provider_id"),
                model_id=_required_string(raw_rate["model_id"], "model_id"),
                input_usd_per_million=_decimal(
                    raw_rate["input_usd_per_million"], "input_usd_per_million"
                ),
                output_usd_per_million=_decimal(
                    raw_rate["output_usd_per_million"], "output_usd_per_million"
                ),
                cache_read_usd_per_million=_decimal(
                    raw_rate["cache_read_usd_per_million"], "cache_read_usd_per_million"
                ),
                cache_write_usd_per_million=_decimal(
                    raw_rate["cache_write_usd_per_million"], "cache_write_usd_per_million"
                ),
            )
        )
    ordered_rates = tuple(sorted(rates, key=lambda rate: (rate.provider_id, rate.model_id)))
    routes = [(rate.provider_id, rate.model_id) for rate in ordered_rates]
    if len(set(routes)) != len(routes):
        raise ValueError("price card must not contain duplicate provider/model routes")
    provisional_receipt = PriceCardReceipt(price_card_id, version, "0" * 64, currency)
    canonical = _canonical_payload(schema_version, provisional_receipt, ordered_rates)
    receipt = PriceCardReceipt(
        price_card_id,
        version,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        currency,
    )
    return FrozenPriceCard(schema_version=schema_version, receipt=receipt, rates=ordered_rates)


def calculate_costs(
    usage: UsageMetrics,
    *,
    provider_id: str | None,
    model_id: str | None,
    price_card: FrozenPriceCard | None,
    billed_cost_usd: Decimal | None = None,
) -> CostCalculation:
    """Calculate only with a known explicit route, full usage, and frozen card."""
    if price_card is None:
        return CostCalculation(
            metrics=CostMetrics(billed_cost_usd=billed_cost_usd),
            limitations=("normalized cost unavailable: no frozen price card",),
        )
    if not provider_id or not model_id:
        return CostCalculation(
            metrics=CostMetrics(billed_cost_usd=billed_cost_usd, price_card=price_card.receipt),
            limitations=("normalized cost unavailable: provider/model route is unknown",),
        )
    rate = price_card.rate_for(provider_id, model_id)
    if rate is None:
        return CostCalculation(
            metrics=CostMetrics(billed_cost_usd=billed_cost_usd, price_card=price_card.receipt),
            limitations=("normalized cost unavailable: route is absent from frozen price card",),
        )
    token_counts = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
    )
    if any(value is None for value in token_counts):
        return CostCalculation(
            metrics=CostMetrics(billed_cost_usd=billed_cost_usd, price_card=price_card.receipt),
            limitations=("normalized cost unavailable: token usage is incomplete",),
        )
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens = [
        Decimal(int(value)) for value in token_counts
    ]
    normalized = (
        input_tokens * rate.input_usd_per_million
        + output_tokens * rate.output_usd_per_million
        + cache_read_tokens * rate.cache_read_usd_per_million
        + cache_write_tokens * rate.cache_write_usd_per_million
    ) / _MILLION
    return CostCalculation(
        metrics=CostMetrics(
            billed_cost_usd=billed_cost_usd,
            normalized_cost_usd=normalized,
            price_card=price_card.receipt,
        )
    )
