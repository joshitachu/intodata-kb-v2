"""Token usage and cost for harness model calls, read from LangChain metadata.

One extractor and one cost function for the whole harness. The v2 chat cohorts
build their own ``langchain_openai.ChatOpenAI`` (see
``build_readonly_primary_chat_model``) and therefore never pass through
``src/generation/providers/**``, so the provider layer's cache accounting cannot
reach them. This module is the bridge: it normalizes LangChain's usage metadata
into the *same* invariant ``src.generation.usage.UsageStats`` already documents,
then delegates pricing to ``UsageStats.calculate_cost``.

Pricing is deliberately NOT duplicated here. ``MODEL_PRICING``,
``MODEL_CACHE_READ_DISCOUNT`` and ``MODEL_CACHE_WRITE_MULTIPLIER`` live in
``src/generation/usage.py`` and stay the single source of truth, so a renegotiated
rate cannot drift between the provider layer and the harness.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.messages import AIMessage

from src.generation.usage import UsageStats


@dataclass(frozen=True, slots=True)
class ModelTokenUsage:
    """Token counts for one or more model calls.

    Same invariant as ``UsageStats``: ``input_tokens`` is the TOTAL prompt count
    and the two cache counts are SUBSETS of it. LangChain already normalizes
    OpenAI-compatible and Anthropic wire formats to that shape, so no
    provider-specific arithmetic is needed here.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "ModelTokenUsage") -> "ModelTokenUsage":
        return ModelTokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


def token_usage_from_message(message: object) -> ModelTokenUsage:
    """Read one message's usage metadata, including its cache breakdown.

    ``input_token_details.cache_read`` is what a provider with an automatic prefix
    cache (Moonshot/Kimi, DeepSeek, Gemini) reports, and it was previously
    discarded, so every prompt token was billed at the full input rate.
    """

    metadata = getattr(message, "usage_metadata", None) or {}
    if not isinstance(metadata, dict):
        return ModelTokenUsage()
    input_tokens = _non_negative_int(metadata.get("input_tokens"))
    output_tokens = _non_negative_int(metadata.get("output_tokens"))
    details = metadata.get("input_token_details") or {}
    if not isinstance(details, dict):
        details = {}
    cached = _non_negative_int(details.get("cache_read"))
    written = _non_negative_int(details.get("cache_creation"))
    # Clamp to the prompt total. A provider that ever reports a cache count larger
    # than its own prompt count must not be able to drive the billed cost negative
    # through `uncached_input_tokens`.
    if cached + written > input_tokens:
        cached = min(cached, input_tokens)
        written = max(0, input_tokens - cached)
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        cache_write_tokens=written,
    )


def token_usage_from_messages(messages: Iterable[object]) -> ModelTokenUsage:
    """Sum usage over the AI messages in ``messages``; others are ignored."""

    total = ModelTokenUsage()
    for message in messages:
        if isinstance(message, AIMessage):
            total = total + token_usage_from_message(message)
    return total


def model_cost_usd(usage: ModelTokenUsage, *, model_name: str) -> float:
    """Price ``usage`` for ``model_name`` using the shared pricing tables.

    An unknown model resolves to 0.0 and a model with no confirmed cache discount
    bills cached tokens at full price, so cost is never understated on a guess.
    """

    stats = UsageStats(
        model=model_name,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
    )
    return stats.calculate_cost()


def _non_negative_int(value: object) -> int:
    return max(0, int(value)) if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = [
    "ModelTokenUsage",
    "model_cost_usd",
    "token_usage_from_message",
    "token_usage_from_messages",
]
