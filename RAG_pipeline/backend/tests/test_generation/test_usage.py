"""Tests for UsageStats and StreamUsageStats (src/generation/usage.py)."""

from src.generation.usage import MODEL_PRICING, StreamUsageStats, UsageStats


class TestUsageStats:
    def test_calculate_cost_known_model(self):
        """kimi-k2.5: $0.30/M input, $0.60/M output."""
        stats = UsageStats(
            provider="kimi",
            model="kimi-k2.5",
            input_tokens=1000,
            output_tokens=500,
        )
        cost = stats.calculate_cost()
        expected = (1000 / 1_000_000) * 0.30 + (500 / 1_000_000) * 0.60
        assert cost == round(expected, 6)
        assert stats.cost_usd == cost

    def test_calculate_cost_unknown_model(self):
        """Unknown model returns $0.00."""
        stats = UsageStats(model="unknown-model-xyz", input_tokens=5000, output_tokens=2000)
        cost = stats.calculate_cost()
        assert cost == 0.0

    def test_calculate_cost_local_model(self):
        """Local models (gemma3) have $0.00 pricing."""
        stats = UsageStats(model="gemma3:12b", input_tokens=10000, output_tokens=5000)
        cost = stats.calculate_cost()
        assert cost == 0.0

    def test_calculate_cost_zero_tokens(self):
        """0 tokens = $0.00 regardless of model."""
        stats = UsageStats(model="kimi-k2.5", input_tokens=0, output_tokens=0)
        cost = stats.calculate_cost()
        assert cost == 0.0

    def test_usage_stats_defaults(self):
        """Default values are empty/zero."""
        stats = UsageStats()
        assert stats.provider == ""
        assert stats.model == ""
        assert stats.input_tokens == 0
        assert stats.output_tokens == 0
        assert stats.cost_usd == 0.0
        assert stats.latency_ms == 0

    def test_model_pricing_table(self):
        """All expected models present in pricing table."""
        expected_models = {"kimi-k2.5", "gemma3:4b", "gemma3:12b", "nomic-embed-text"}
        assert expected_models.issubset(set(MODEL_PRICING.keys()))

    def test_cost_rounding(self):
        """Cost is rounded to 6 decimal places."""
        stats = UsageStats(model="kimi-k2.5", input_tokens=1, output_tokens=1)
        cost = stats.calculate_cost()
        # Very small cost — should be rounded to 6 places
        assert cost == round(cost, 6)
        # Verify it's a float with at most 6 decimal places
        cost_str = f"{cost:.10f}"
        significant = cost_str.rstrip("0")
        decimal_places = len(significant.split(".")[-1]) if "." in significant else 0
        assert decimal_places <= 6


class TestStreamUsageStats:
    def test_stream_usage_finalize(self):
        """finalize() returns UsageStats with calculated cost."""
        stream = StreamUsageStats(
            provider="kimi",
            model="kimi-k2.5",
            input_tokens=2000,
            output_tokens=1000,
            latency_ms=350,
        )
        result = stream.finalize()
        assert isinstance(result, UsageStats)
        assert result.provider == "kimi"
        assert result.model == "kimi-k2.5"
        assert result.input_tokens == 2000
        assert result.output_tokens == 1000
        assert result.latency_ms == 350
        # Cost should be calculated
        expected_cost = (2000 / 1_000_000) * 0.30 + (1000 / 1_000_000) * 0.60
        assert result.cost_usd == round(expected_cost, 6)

    def test_stream_usage_finalize_local_model(self):
        """finalize() with local model = $0.00 cost."""
        stream = StreamUsageStats(
            provider="ollama",
            model="gemma3:4b",
            input_tokens=5000,
            output_tokens=2000,
            latency_ms=1200,
        )
        result = stream.finalize()
        assert result.cost_usd == 0.0
