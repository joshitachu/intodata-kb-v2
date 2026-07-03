"""Token usage tracking dataclass — lightweight, no LangChain dependency."""

from __future__ import annotations

from dataclasses import dataclass, field


# Pricing per million tokens (USD). Keyed by the bare provider-native model name
# (the value passed to the client), matching UsageStats.model. Unknown models
# resolve to 0 cost (recorded but not billed).
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_price_per_M, output_price_per_M)
    # Kimi / Moonshot
    "kimi-k2.5": (0.30, 0.60),
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


@dataclass
class UsageStats:
    """Accumulated token usage for a single LLM or embedding call."""

    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def calculate_cost(self) -> float:
        """Calculate cost based on model pricing."""
        pricing = MODEL_PRICING.get(self.model)
        if not pricing:
            return 0.0
        input_cost = (self.input_tokens / 1_000_000) * pricing[0]
        output_cost = (self.output_tokens / 1_000_000) * pricing[1]
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

    def finalize(self) -> UsageStats:
        stats = UsageStats(
            provider=self.provider,
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=self.latency_ms,
        )
        stats.calculate_cost()
        return stats
