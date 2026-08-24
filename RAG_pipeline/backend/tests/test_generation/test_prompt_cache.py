"""Tests for the shared prompt-cache plumbing (src/generation/providers/prompt_cache.py).

Covers the two behaviours the cost saving depends on: byte-stable schema
serialization, and correct normalization of the two upstream cache-usage shapes
onto the single UsageStats invariant.
"""
from types import SimpleNamespace

import pytest

from src.generation.providers.prompt_cache import (
    apply_cache_tokens,
    canonical_schema,
    check_tool_prefix_stability,
    read_openai_cache_tokens,
    reset_prefix_tracking,
)
from src.generation.usage import StreamUsageStats, UsageStats


@pytest.fixture(autouse=True)
def _fresh_prefix_tracking():
    """Tests share one context; production scopes this per request task."""
    reset_prefix_tracking()
    yield
    reset_prefix_tracking()


class TestCanonicalSchema:
    def test_sorts_nested_object_keys(self):
        a = {"b": 1, "a": {"z": 2, "y": 3}}
        b = {"a": {"y": 3, "z": 2}, "b": 1}
        assert list(canonical_schema(a).keys()) == ["a", "b"]
        assert canonical_schema(a) == canonical_schema(b)

    def test_identical_schemas_serialize_identically(self):
        """The property the prefix cache actually keys on."""
        import json

        built_one_way = {
            "type": "object",
            "properties": {"limit": {"type": "integer"}, "id": {"type": "string"}},
            "required": ["id"],
        }
        built_another_way = {
            "required": ["id"],
            "properties": {"id": {"type": "string"}, "limit": {"type": "integer"}},
            "type": "object",
        }
        assert json.dumps(built_one_way) != json.dumps(built_another_way)
        assert json.dumps(canonical_schema(built_one_way)) == json.dumps(
            canonical_schema(built_another_way)
        )

    def test_preserves_list_order(self):
        """enum/required order can carry meaning, so lists are never reordered."""
        schema = {"enum": ["z", "a", "m"], "prefixItems": [{"b": 1}, {"a": 2}]}
        out = canonical_schema(schema)
        assert out["enum"] == ["z", "a", "m"]
        assert list(out["prefixItems"][0].keys()) == ["b"]

    def test_recurses_into_lists_of_objects(self):
        schema = {"anyOf": [{"b": 1, "a": 2}]}
        assert list(canonical_schema(schema)["anyOf"][0].keys()) == ["a", "b"]

    def test_passes_through_scalars_and_none(self):
        assert canonical_schema(None) is None
        assert canonical_schema(5) == 5
        assert canonical_schema("x") == "x"


class TestReadOpenAICacheTokens:
    def test_reads_prompt_tokens_details(self):
        usage = SimpleNamespace(
            prompt_tokens=6266,
            prompt_tokens_details=SimpleNamespace(cached_tokens=6144, cache_write_tokens=None),
        )
        assert read_openai_cache_tokens(usage) == (6144, 0)

    def test_cold_call_reports_none_details(self):
        """Moonshot's first call returns prompt_tokens_details: null."""
        usage = SimpleNamespace(prompt_tokens=6266, prompt_tokens_details=None)
        assert read_openai_cache_tokens(usage) == (0, 0)

    def test_falls_back_to_top_level_cached_tokens(self):
        """Moonshot also mirrors the count at the top level of `usage`."""
        usage = SimpleNamespace(
            prompt_tokens=100, prompt_tokens_details=None, cached_tokens=64
        )
        assert read_openai_cache_tokens(usage) == (64, 0)

    def test_falls_back_to_model_extra(self):
        usage = SimpleNamespace(
            prompt_tokens=100,
            prompt_tokens_details=None,
            model_extra={"cached_tokens": 64},
        )
        assert read_openai_cache_tokens(usage) == (64, 0)

    def test_reads_cache_write_from_model_extra(self):
        usage = SimpleNamespace(
            prompt_tokens=100,
            prompt_tokens_details=SimpleNamespace(
                cached_tokens=10, model_extra={"cache_write_tokens": 20}
            ),
        )
        assert read_openai_cache_tokens(usage) == (10, 20)

    def test_none_usage_is_safe(self):
        assert read_openai_cache_tokens(None) == (0, 0)

    def test_string_counts_are_coerced_not_raised(self):
        usage = SimpleNamespace(
            prompt_tokens=100, prompt_tokens_details=SimpleNamespace(cached_tokens="64")
        )
        assert read_openai_cache_tokens(usage) == (64, 0)

    def test_garbage_count_degrades_to_zero(self):
        usage = SimpleNamespace(
            prompt_tokens=100, prompt_tokens_details=SimpleNamespace(cached_tokens="lots")
        )
        assert read_openai_cache_tokens(usage) == (0, 0)


class TestApplyCacheTokens:
    def test_clamps_cached_to_prompt_size(self):
        """An over-reporting upstream must never make cost read as negative."""
        stats = UsageStats(model="kimi-k2.5", input_tokens=100)
        apply_cache_tokens(stats, 500, 0)
        assert stats.cached_input_tokens == 100
        assert stats.uncached_input_tokens == 0

    def test_negative_counts_floor_at_zero(self):
        stats = UsageStats(model="kimi-k2.5", input_tokens=100)
        apply_cache_tokens(stats, -5, -5)
        assert stats.cached_input_tokens == 0
        assert stats.cache_write_tokens == 0

    def test_write_shares_the_prompt_budget_with_reads(self):
        stats = UsageStats(model="claude-sonnet-4-6", input_tokens=100)
        apply_cache_tokens(stats, 60, 80)
        assert stats.cached_input_tokens == 60
        assert stats.cache_write_tokens == 40
        assert stats.uncached_input_tokens == 0

    def test_works_on_stream_usage_stats(self):
        acc = StreamUsageStats(model="kimi-k2.5", input_tokens=1000)
        apply_cache_tokens(acc, 900, 0)
        assert acc.finalize().cached_input_tokens == 900


class TestToolPrefixStability:
    def test_warns_when_tool_block_changes_mid_turn(self, caplog):
        tools_a = [{"type": "function", "function": {"name": "a"}}]
        tools_b = [{"type": "function", "function": {"name": "b"}}]
        check_tool_prefix_stability("kimi", tools_a)
        with caplog.at_level("WARNING"):
            check_tool_prefix_stability("kimi", tools_b)
        assert "tool schema block changed" in caplog.text

    def test_silent_when_tool_block_is_stable(self, caplog):
        tools = [{"type": "function", "function": {"name": "a"}}]
        check_tool_prefix_stability("kimi", tools)
        with caplog.at_level("WARNING"):
            check_tool_prefix_stability("kimi", tools)
        assert "tool schema block changed" not in caplog.text

    def test_key_reorder_alone_does_not_trip_the_warning(self, caplog):
        """Fingerprinting is order-insensitive, so only real changes warn."""
        check_tool_prefix_stability("kimi", [{"a": 1, "b": 2}])
        with caplog.at_level("WARNING"):
            check_tool_prefix_stability("kimi", [{"b": 2, "a": 1}])
        assert "tool schema block changed" not in caplog.text

    def test_empty_tools_is_a_noop(self, caplog):
        with caplog.at_level("WARNING"):
            check_tool_prefix_stability("kimi", None)
            check_tool_prefix_stability("kimi", [])
        assert caplog.text == ""
