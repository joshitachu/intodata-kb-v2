"""Adapter-level tests for the OpenAI-compatible provider (mocked SDK).

Covers openai/deepseek/kimi (all share this adapter): correct model name and
params reach the client, streaming assembles tokens, and tool calls round-trip.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.providers.openai_compat import OpenAICompatibleProvider
from src.runtime.models import RuntimeToolDefinition

MESSAGES = [{"role": "user", "content": "hi"}]


def _provider():
    return OpenAICompatibleProvider(
        provider_id="openai", base_url="https://api.openai.com/v1", api_key="sk-x"
    )


def _usage(prompt=10, completion=5):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


async def test_generate_sends_bare_model_and_returns_usage():
    provider = _provider()
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="answer", tool_calls=None))],
            usage=_usage(),
        )
    )
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        text, usage = await provider.generate(model="gpt-4o", messages=MESSAGES)

    assert text == "answer"
    assert usage.provider == "openai"
    assert usage.model == "gpt-4o"
    assert usage.input_tokens == 10 and usage.output_tokens == 5
    kwargs = fake_client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "gpt-4o"  # bare, no 'openai/' prefix


async def test_stream_assembles_tokens():
    provider = _provider()

    async def _fake_stream(*a, **k):
        for c in ["Hel", "lo"]:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=c))], usage=None
            )
        yield SimpleNamespace(choices=[], usage=_usage())

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=lambda *a, **k: _fake_stream())
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        out = [item async for item in provider.stream(model="gpt-4o", messages=MESSAGES)]

    tokens = [x for x in out if isinstance(x, str)]
    assert tokens == ["Hel", "lo"]


async def test_generate_with_tools_returns_tool_call():
    provider = _provider()
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="list_executions", arguments='{"limit": 5}'),
    )
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call]))],
            usage=_usage(),
        )
    )
    tools = [RuntimeToolDefinition(name="list_executions", description="d", input_schema={"type": "object"})]
    with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
        resp = await provider.generate_with_tools(model="gpt-4o", messages=MESSAGES, runtime_tools=tools)

    assert resp.tool_calls[0].tool_name == "list_executions"
    assert resp.tool_calls[0].arguments == {"limit": 5}
    assert resp.no_tool_support is False


def _usage_cached(prompt=6266, completion=32, cached=6144):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached, cache_write_tokens=None),
    )


class TestPromptCacheAccounting:
    """Moonshot/Kimi caches the prefix automatically; the adapter must read the
    hit count back and discount it, or the saving is invisible and unbilled."""

    async def test_generate_records_cached_tokens_and_discounts_cost(self):
        provider = OpenAICompatibleProvider(
            provider_id="kimi", base_url="https://api.moonshot.ai/v1", api_key="sk-x"
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="a", tool_calls=None))],
                usage=_usage_cached(),
            )
        )
        with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
            _, usage = await provider.generate(model="kimi-k2.6", messages=MESSAGES)

        assert usage.input_tokens == 6266
        assert usage.cached_input_tokens == 6144
        assert usage.uncached_input_tokens == 122
        expected = (122 * 0.30 + 6144 * 0.30 * 0.175 + 32 * 0.60) / 1_000_000
        assert usage.cost_usd == round(expected, 6)

    async def test_cold_call_with_null_details_is_safe(self):
        """Moonshot's first call returns prompt_tokens_details: null."""
        provider = _provider()
        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="a", tool_calls=None))],
                usage=SimpleNamespace(
                    prompt_tokens=6266, completion_tokens=32, prompt_tokens_details=None
                ),
            )
        )
        with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
            _, usage = await provider.generate(model="gpt-4o", messages=MESSAGES)
        assert usage.cached_input_tokens == 0
        assert usage.input_tokens == 6266

    async def test_generate_with_tools_records_cached_tokens(self):
        provider = _provider()
        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="a", tool_calls=None))],
                usage=_usage_cached(),
            )
        )
        tools = [RuntimeToolDefinition(name="t", description="d", input_schema={"type": "object"})]
        with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
            resp = await provider.generate_with_tools(
                model="gpt-4o", messages=MESSAGES, runtime_tools=tools
            )
        assert resp.usage.cached_input_tokens == 6144

    async def test_stream_records_cached_tokens(self):
        provider = _provider()

        async def _fake_stream(*a, **k):
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"))], usage=None
            )
            yield SimpleNamespace(choices=[], usage=_usage_cached())

        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(side_effect=lambda *a, **k: _fake_stream())
        with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
            out = [x async for x in provider.stream(model="gpt-4o", messages=MESSAGES)]

        stats = [x for x in out if not isinstance(x, str)]
        assert stats[-1].cached_input_tokens == 6144

    async def test_tool_schema_keys_are_canonically_ordered_on_the_wire(self):
        """The tool block heads the cacheable prefix, so its bytes must be stable
        regardless of how the caller happened to build the schema dict."""
        provider = _provider()
        fake_client = MagicMock()
        fake_client.chat.completions.create = AsyncMock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="a", tool_calls=None))],
                usage=_usage(),
            )
        )
        tools = [RuntimeToolDefinition(
            name="t",
            description="d",
            input_schema={"required": ["b"], "type": "object",
                          "properties": {"b": {"type": "string"}, "a": {"type": "string"}}},
        )]
        with patch.object(provider, "_get_client", AsyncMock(return_value=fake_client)):
            await provider.generate_with_tools(
                model="gpt-4o", messages=MESSAGES, runtime_tools=tools
            )
        sent = fake_client.chat.completions.create.await_args.kwargs["tools"][0]
        params = sent["function"]["parameters"]
        assert list(params.keys()) == ["properties", "required", "type"]
        assert list(params["properties"].keys()) == ["a", "b"]
