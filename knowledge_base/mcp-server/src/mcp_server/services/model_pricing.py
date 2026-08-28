"""Token-based model pricing helpers for generated-document usage tracking.

Two rules govern this module:

1. **A run's cost must resolve out of the box.** ``MCP_MODEL_PRICING_JSON`` was
   absent from the deployment environment and ``config.model_pricing_json``
   defaulted to ``""``, so ``load_model_pricing()`` returned ``{}`` and every
   ``GenerateRun.cost_usd`` was NULL. ``DEFAULT_MODEL_PRICING`` below is the
   built-in floor that makes cost observable with no configuration; the env var
   still overrides it, per model.
2. **A wrong number is worse than no number.** An unknown model resolves to
   ``None`` and is logged once with the config key that fixes it -- it is never
   silently priced at zero, and ``calculate_total_cost`` returns ``None`` for
   the whole run rather than under-reporting a partially-priced one. Rates are
   only listed below when they were verified against a source; models whose
   rate could not be verified (or cannot be expressed by a flat two-rate model,
   e.g. DeepSeek's peak/off-peak billing) are deliberately absent.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable

from mcp_server.config import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    """USD-per-million-token pricing for one model."""

    input_per_million: float
    output_per_million: float


# --------------------------------------------------------------------------- #
# Built-in default pricing table
# --------------------------------------------------------------------------- #
# Keyed by the BARE model name. ``model_pricing_candidates`` tries the full
# provider-prefixed id first and then the bare suffix, so one entry serves
# ``openai/gpt-5.6-luna``, ``opencode-go/gpt-5.6-luna`` and a bare
# ``gpt-5.6-luna`` alike.
#
# ENTRY CRITERION: a rate is listed only when it was checked against a source
# and that source is named on the line. Coverage is deliberately narrow --
# see MODELS DELIBERATELY OMITTED below. Correct a rate here, or override it
# at runtime with MCP_MODEL_PRICING_JSON; do not add an unsourced guess.
DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = {
    # -- OpenAI ------------------------------------------------------------ #
    # Verified 2026-08-28 against the OpenRouter model page for
    # openai/gpt-5.6-luna and corroborated by independent pricing aggregators
    # reporting the 2026-07-30 price cut. This is the model the live VPS runs.
    # CAVEAT: OpenAI meters gpt-5.6-luna at a higher long-context rate
    # (~$0.40/$1.80 per MTok) for very large single prompts. Nexus doc-gen
    # calls average ~54K input tokens (~2M input over ~37 calls), well under
    # any long-context threshold, so the standard rate is the right one here.
    "gpt-5.6-luna": ModelPricing(0.20, 1.20),
    # -- Anthropic --------------------------------------------------------- #
    # Anthropic first-party API list prices.
    "claude-sonnet-4-6": ModelPricing(3.00, 15.00),
    "claude-opus-4-7": ModelPricing(5.00, 25.00),
    # -- Local inference (Ollama) ------------------------------------------ #
    # Zero is the correct API-side cost for locally hosted inference, not a
    # missing rate: there is no vendor charge per token. It deliberately does
    # NOT account for hardware or electricity. These are the only entries in
    # this table allowed to be zero (pinned by a test).
    "gemma3:12b": ModelPricing(0.0, 0.0),
    "gemma3:4b": ModelPricing(0.0, 0.0),
    "qwen2.5:14b": ModelPricing(0.0, 0.0),
    "hermes3:8b": ModelPricing(0.0, 0.0),
}

# MODELS DELIBERATELY OMITTED (each resolves to None + a log line):
#
# * deepseek-v4-flash / deepseek-v4-pro -- DeepSeek moved to PEAK/OFF-PEAK
#   billing on 2026-08-16, where the peak rate is exactly 2x off-peak
#   (flash ~$0.22/$0.66 off-peak vs ~$0.44/$1.32 peak per MTok). A flat
#   two-rate model cannot express a time-of-day tariff, so any single number
#   here is wrong by up to 2x in one direction. Pick a convention and set it
#   explicitly via MCP_MODEL_PRICING_JSON rather than guessing here.
# * gpt-5.6 / gpt-5.6-sol / gpt-5.6-terra and the older gpt-5.x / gpt-4.x
#   ids, kimi-k2.5, and the gemini-* ids -- present in
#   data/model_catalogue.json but not in production use for doc generation,
#   and not independently verified. Add them with a source when needed.
#
# NOT A SOURCE OF TRUTH: RAG_pipeline/backend/src/generation/usage.py carries
# its own MODEL_PRICING table for chat usage. It is a separate deployable and
# is NOT imported here. It also disagrees with this table on claude-opus-4-7
# (it lists $15/$75, the old Opus-4.1-era rate); this table's $5/$25 is the
# current published rate. That divergence is flagged for the RAG owner.

# Bare model names for which a 0.0 rate is a real answer rather than a bug.
_KNOWN_FREE_MODELS = frozenset(
    name for name, rate in DEFAULT_MODEL_PRICING.items()
    if rate.input_per_million == 0.0 and rate.output_per_million == 0.0
)

# Bounded, process-wide dedupe so an unpriced model logs once instead of once
# per model call (a real run makes tens to hundreds of calls).
_UNPRICED_LOGGED: set[str] = set()
_UNPRICED_LOG_CAP = 256


def reset_unpriced_model_log() -> None:
    """Clear the once-per-model unpriced-model log guard (tests)."""

    _UNPRICED_LOGGED.clear()


def _log_unpriced_model(model_name: str) -> None:
    if model_name in _UNPRICED_LOGGED:
        return
    if len(_UNPRICED_LOGGED) < _UNPRICED_LOG_CAP:
        _UNPRICED_LOGGED.add(model_name)
    logger.warning(
        "[model_pricing] no USD rate for model %r -- cost will be reported as "
        "unknown (None) rather than estimated. Add it to "
        "services/model_pricing.DEFAULT_MODEL_PRICING or set "
        "MCP_MODEL_PRICING_JSON='{\"%s\":{\"input\":<usd_per_mtok>,"
        "\"output\":<usd_per_mtok>}}'",
        model_name,
        model_name,
    )


def _rate_value(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(rate) or rate < 0:
            return None
        return rate
    return None


def parse_model_pricing(raw_json: str | None) -> dict[str, ModelPricing | None]:
    """Parse a model-pricing JSON override into rates (and explicit removals).

    Accepted shapes per model:
    - {"input": 0.28, "output": 0.88}
    - {"input_per_million": 0.28, "output_per_million": 0.88}
    - {"input_usd_per_million": 0.28, "output_usd_per_million": 0.88}
    - null -- explicitly REMOVES a built-in default so that model reports
      unknown cost instead of using the shipped rate.

    A malformed or negative rate is skipped with a warning rather than being
    coerced: a silently-wrong rate is worse than an absent one.
    """

    if not raw_json:
        return {}
    parsed = json.loads(raw_json)
    if not isinstance(parsed, dict):
        raise ValueError("model pricing config must be a JSON object")

    pricing: dict[str, ModelPricing | None] = {}
    for model_name, rates in parsed.items():
        if not isinstance(model_name, str):
            continue
        if rates is None:
            pricing[model_name] = None
            continue
        if not isinstance(rates, dict):
            continue
        input_rate = _rate_value(rates, "input", "input_per_million", "input_usd_per_million")
        output_rate = _rate_value(rates, "output", "output_per_million", "output_usd_per_million")
        if input_rate is None or output_rate is None:
            logger.warning(
                "[model_pricing] ignoring override for %r: input/output rates "
                "must both be present, numeric and non-negative",
                model_name,
            )
            continue
        pricing[model_name] = ModelPricing(input_rate, output_rate)
    return pricing


def load_model_pricing(
    raw_json: str | None = None,
    *,
    include_defaults: bool = True,
) -> dict[str, ModelPricing]:
    """Return the EFFECTIVE pricing table: built-in defaults + JSON overrides.

    ``MCP_MODEL_PRICING_JSON`` (via ``config.model_pricing_json``) is merged
    over ``DEFAULT_MODEL_PRICING`` PER MODEL, so correcting one rate does not
    require restating every other. A ``null`` value removes a default. Pass
    ``include_defaults=False`` for the override table alone.
    """

    raw = raw_json if raw_json is not None else config.model_pricing_json
    pricing: dict[str, ModelPricing] = dict(DEFAULT_MODEL_PRICING) if include_defaults else {}
    for model_name, rate in parse_model_pricing(raw).items():
        if rate is None:
            pricing.pop(model_name, None)
        else:
            pricing[model_name] = rate
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
    """Find pricing by exact model key, falling back from provider/model to model.

    Returns ``None`` for an unknown model and logs it once. It never falls back
    to a zero or nearby-model rate: cost is either right or explicitly unknown.
    """

    pricing = pricing if pricing is not None else load_model_pricing()
    for candidate in model_pricing_candidates(model_name):
        rate = pricing.get(candidate)
        if rate is not None:
            if (
                rate.input_per_million == 0.0
                and rate.output_per_million == 0.0
                and candidate not in _KNOWN_FREE_MODELS
            ):
                # Never let a zero rate pass as a priced result without saying
                # so -- a $0 run and an unpriced run must not look identical.
                logger.info(
                    "[model_pricing] model %r is priced at zero USD per token "
                    "(configured explicitly); its usage will report $0.00",
                    candidate,
                )
            return rate
    if model_name:
        _log_unpriced_model(model_name)
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
