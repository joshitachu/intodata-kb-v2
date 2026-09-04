"""Token usage tracking dataclass — lightweight, no LangChain dependency."""

from __future__ import annotations

from dataclasses import dataclass, field


# Pricing per million tokens (USD). Keyed by the bare provider-native model name
# (the value passed to the client), matching UsageStats.model. Unknown models
# resolve to 0 cost (recorded but not billed).
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_price_per_M, output_price_per_M)
    # Kimi / Moonshot
    # UNVERIFIED: carried over from the retired kimi-k2.5 entry when that model
    # was removed. Not confirmed against Moonshot's current price list -- check
    # before trusting spend figures for this model.
    "kimi-k2.6": (0.30, 0.60),
    "moonshot-v1-128k": (0.30, 0.60),
    # OpenAI
    "gpt-5.5": (5.00, 15.00),
    "gpt-5.4": (5.00, 15.00),
    "gpt-5.4-mini": (0.60, 2.40),
    "gpt-5": (5.00, 15.00),
    "gpt-4.5": (5.00, 15.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o": (2.50, 10.00),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v4-pro": (0.55, 2.19),
    "deepseek-v4-flash": (0.27, 1.10),
    # Anthropic
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    # Google Gemini
    "gemini-3.1-pro-preview": (1.25, 10.00),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    # Local (Ollama) / embeddings
    "gemma3:4b": (0.0, 0.0),  # Local
    "gemma3:12b": (0.0, 0.0),  # Local
    "nomic-embed-text": (0.0, 0.0),  # Local
    "voyage-4-large": (0.12, 0.0),  # Embedding only
    "voyage-3": (0.06, 0.0),
}


# Fraction of the normal input price charged for a prompt token served from the
# provider's prefix cache. Expressed as a RATIO rather than an absolute per-M
# price so it stays correct if a base rate in MODEL_PRICING is renegotiated or
# corrected — the two numbers can never drift apart.
#
# A model absent from this table falls back to _DEFAULT_CACHE_READ_DISCOUNT (1.0,
# i.e. "assume cached tokens cost full price"). That deliberately OVERSTATES cost
# for a provider whose discount we have not confirmed: an unverified saving must
# never show up as revenue in the usage dashboard.
MODEL_CACHE_READ_DISCOUNT: dict[str, float] = {
    # Moonshot/Kimi published cached input at CNY 0.70/M against CNY 4.00/M
    # uncached for the retired k2.5 => 0.175. Automatic prefix cache, no
    # cache-write fee. UNVERIFIED for k2.6: ratio assumed unchanged.
    "kimi-k2.6": 0.175,
    # moonshot-v1-* has no cache discount, so it is intentionally absent.
    # Anthropic: cache read is 0.1x base input, cache write 1.25x base input.
    "claude-sonnet-4-6": 0.10,
    "claude-opus-4-7": 0.10,
    # DeepSeek context caching: cache hit is 0.1x the cache-miss input price.
    "deepseek-chat": 0.10,
    "deepseek-reasoner": 0.10,
    "deepseek-v4-pro": 0.10,
    "deepseek-v4-flash": 0.10,
    # Google implicit caching on Gemini 2.5+: cached input is 0.25x base.
    "gemini-3.1-pro-preview": 0.25,
    "gemini-2.5-pro": 0.25,
    "gemini-2.5-flash": 0.25,
    "gemini-2.5-flash-lite": 0.25,
    # OpenAI models are intentionally absent: their cached-input discount varies
    # by model generation and has not been verified for the ids above.
}

_DEFAULT_CACHE_READ_DISCOUNT = 1.0

# Multiple of the base input price charged for tokens WRITTEN into the cache.
# Only providers with explicit, billed cache creation appear here; everyone else
# reports cache_write_tokens == 0 so the multiplier is never applied.
MODEL_CACHE_WRITE_MULTIPLIER: dict[str, float] = {
    "claude-sonnet-4-6": 1.25,
    "claude-opus-4-7": 1.25,
}

_DEFAULT_CACHE_WRITE_MULTIPLIER = 1.0


@dataclass
class UsageStats:
    """Accumulated token usage for a single LLM or embedding call.

    Cache accounting invariant (IMPORTANT — providers must normalize to it):
    ``input_tokens`` is always the TOTAL prompt size, and
    ``cached_input_tokens`` + ``cache_write_tokens`` are SUBSETS of it. The two
    upstream wire formats disagree, so each adapter converts on the way in:

    * OpenAI-compatible (Kimi/Moonshot, DeepSeek, OpenAI) already reports
      ``prompt_tokens`` inclusive of ``prompt_tokens_details.cached_tokens`` —
      pass both through unchanged.
    * Anthropic reports ``input_tokens`` EXCLUSIVE of
      ``cache_read_input_tokens`` and ``cache_creation_input_tokens`` — the
      adapter must sum all three into ``input_tokens``.

    Keeping one invariant means every existing consumer that reads
    ``input_tokens`` as "how big was the prompt" stays correct, and cost is
    computed identically for all providers.
    """

    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    cached_input_tokens: int = 0
    """Prompt tokens served from the provider's prefix cache (billed cheaper)."""
    cache_write_tokens: int = 0
    """Prompt tokens written into an explicit cache (billed at a premium).
    Stays 0 on providers with an automatic prefix cache and no write fee."""
    truncated: bool = False
    """The provider stopped because the output ceiling was reached, not because
    the model was finished. The accompanying text is a fragment.

    Every provider reports this (OpenAI-compatible ``finish_reason == "length"``,
    Anthropic ``stop_reason == "max_tokens"``, Gemini ``MAX_TOKENS``) and it was
    previously discarded at the adapter, so a stream that stopped early was
    indistinguishable from one that finished and was persisted as a complete
    answer."""

    @property
    def uncached_input_tokens(self) -> int:
        """Prompt tokens billed at the full input rate."""
        return max(0, self.input_tokens - self.cached_input_tokens - self.cache_write_tokens)

    @property
    def cache_hit_ratio(self) -> float:
        """Share of the prompt served from cache, 0.0-1.0. 0.0 when no prompt."""
        if self.input_tokens <= 0:
            return 0.0
        return min(1.0, self.cached_input_tokens / self.input_tokens)

    def calculate_cost(self) -> float:
        """Calculate cost based on model pricing, discounting cached prompt tokens.

        With ``cached_input_tokens`` and ``cache_write_tokens`` at their 0 defaults
        this reduces exactly to the previous flat input-price formula, so providers
        that report no cache data bill identically to before.
        """
        pricing = MODEL_PRICING.get(self.model)
        if not pricing:
            return 0.0
        input_price, output_price = pricing
        read_discount = MODEL_CACHE_READ_DISCOUNT.get(
            self.model, _DEFAULT_CACHE_READ_DISCOUNT
        )
        write_multiplier = MODEL_CACHE_WRITE_MULTIPLIER.get(
            self.model, _DEFAULT_CACHE_WRITE_MULTIPLIER
        )
        input_cost = (
            self.uncached_input_tokens * input_price
            + self.cached_input_tokens * input_price * read_discount
            + self.cache_write_tokens * input_price * write_multiplier
        ) / 1_000_000
        output_cost = (self.output_tokens / 1_000_000) * output_price
        self.cost_usd = round(input_cost + output_cost, 6)
        return self.cost_usd


@dataclass
class StreamUsageStats:
    """Accumulates usage across a streaming response."""

    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    truncated: bool = False

    def finalize(self) -> UsageStats:
        stats = UsageStats(
            provider=self.provider,
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=self.latency_ms,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens,
            truncated=self.truncated,
        )
        stats.calculate_cost()
        return stats


TRUNCATION_NOTICE = (
    "\n\n---\n\n"
    "**⚠ This answer was cut off before it finished.** The model reached its "
    "maximum response length, so the text above stops mid-thought and the "
    "remaining content is missing. Ask a narrower question, or ask for the "
    "remainder, to get the rest."
)


def append_truncation_notice(answer: str) -> str:
    """Append the visible truncation notice, once.

    The marker goes into the answer TEXT rather than onto a separate flag because
    the text is the one thing every surface renders and persists. A boolean on
    the message row can be ignored by a UI that has not been taught about it, and
    the failure this guards against is precisely one the reader cannot see: the
    output stops mid-sentence but is otherwise well-formed, so a truncated answer
    reads as a finished one.

    Idempotent, so paths that mark at more than one layer cannot double-append.
    """
    if TRUNCATION_NOTICE.strip() in answer:
        return answer
    return answer.rstrip() + TRUNCATION_NOTICE


def mark_if_truncated(answer: str, usage: UsageStats | None) -> str:
    """Append the truncation notice when ``usage`` says the ceiling was hit."""
    if usage is None or not usage.truncated:
        return answer
    return append_truncation_notice(answer)
