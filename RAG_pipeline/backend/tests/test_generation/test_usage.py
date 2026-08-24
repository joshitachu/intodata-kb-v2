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


class TestCachedTokenPricing:
    """Cached prompt tokens must be billed at the provider's cache-read rate."""

    def test_no_cache_data_bills_exactly_as_before(self):
        """Regression guard: 0 cached tokens reduces to the flat input formula."""
        stats = UsageStats(model="kimi-k2.5", input_tokens=10_000, output_tokens=500)
        cost = stats.calculate_cost()
        expected = (10_000 / 1_000_000) * 0.30 + (500 / 1_000_000) * 0.60
        assert cost == round(expected, 6)

    def test_full_cache_hit_costs_the_discounted_input_rate(self):
        """kimi-k2.5 cache read is 0.175x the input price."""
        stats = UsageStats(
            model="kimi-k2.5",
            input_tokens=10_000,
            cached_input_tokens=10_000,
            output_tokens=500,
        )
        cost = stats.calculate_cost()
        expected = (10_000 / 1_000_000) * 0.30 * 0.175 + (500 / 1_000_000) * 0.60
        assert cost == round(expected, 6)
        assert cost < UsageStats(
            model="kimi-k2.5", input_tokens=10_000, output_tokens=500
        ).calculate_cost()

    def test_partial_cache_hit_splits_the_input_bill(self):
        stats = UsageStats(
            model="kimi-k2.5", input_tokens=22_407, cached_input_tokens=15_000
        )
        cost = stats.calculate_cost()
        expected = (7_407 * 0.30 + 15_000 * 0.30 * 0.175) / 1_000_000
        assert cost == round(expected, 6)
        assert stats.uncached_input_tokens == 7_407

    def test_unknown_discount_assumes_full_price(self):
        """Never book an unverified saving: gpt-4o has no confirmed cache rate."""
        cached = UsageStats(model="gpt-4o", input_tokens=1000, cached_input_tokens=1000)
        plain = UsageStats(model="gpt-4o", input_tokens=1000)
        assert cached.calculate_cost() == plain.calculate_cost()

    def test_anthropic_cache_write_costs_a_premium(self):
        """Cache creation is 1.25x input; cache read is 0.10x."""
        write = UsageStats(
            model="claude-sonnet-4-6", input_tokens=1000, cache_write_tokens=1000
        )
        read = UsageStats(
            model="claude-sonnet-4-6", input_tokens=1000, cached_input_tokens=1000
        )
        plain = UsageStats(model="claude-sonnet-4-6", input_tokens=1000)
        assert write.calculate_cost() == round((1000 * 3.00 * 1.25) / 1_000_000, 6)
        assert read.calculate_cost() == round((1000 * 3.00 * 0.10) / 1_000_000, 6)
        assert write.cost_usd > plain.calculate_cost() > read.cost_usd

    def test_unknown_model_still_free(self):
        stats = UsageStats(model="nope", input_tokens=1000, cached_input_tokens=1000)
        assert stats.calculate_cost() == 0.0

    def test_cache_hit_ratio(self):
        assert UsageStats(input_tokens=1000, cached_input_tokens=250).cache_hit_ratio == 0.25
        assert UsageStats(input_tokens=0, cached_input_tokens=100).cache_hit_ratio == 0.0
        assert UsageStats(input_tokens=100, cached_input_tokens=999).cache_hit_ratio == 1.0

    def test_uncached_never_negative(self):
        stats = UsageStats(input_tokens=100, cached_input_tokens=80, cache_write_tokens=80)
        assert stats.uncached_input_tokens == 0

    def test_stream_finalize_carries_cache_counts(self):
        stream = StreamUsageStats(
            model="kimi-k2.5",
            input_tokens=10_000,
            cached_input_tokens=9_000,
            output_tokens=100,
        )
        result = stream.finalize()
        assert result.cached_input_tokens == 9_000
        assert result.cost_usd == round(
            (1_000 * 0.30 + 9_000 * 0.30 * 0.175 + 100 * 0.60) / 1_000_000, 6
        )
