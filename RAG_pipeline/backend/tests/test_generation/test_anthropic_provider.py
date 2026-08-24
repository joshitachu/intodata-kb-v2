"""Adapter tests for the Anthropic provider (mocked SDK) + message translation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.providers.anthropic_provider import AnthropicProvider, _to_anthropic
from src.runtime.models import RuntimeToolDefinition


def _block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def _provider():
    return AnthropicProvider(api_key="sk-ant")


# --- message translation ---------------------------------------------------


def test_translation_splits_system_and_keeps_turns():
    system, msgs = _to_anthropic(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
        None,
    )
    assert system == "be terse"
    assert msgs == [{"role": "user", "content": "hi"}]


def test_translation_maps_openai_tool_result_to_tool_result_block():
    _system, msgs = _to_anthropic(
        [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "f", "arguments": '{"a":1}'}}
            ]},
            {"role": "tool", "tool_call_id": "t1", "content": "result-json"},
        ],
        None,
    )
    assert msgs[0]["content"][0]["type"] == "tool_use"
    assert msgs[0]["content"][0]["input"] == {"a": 1}
    assert msgs[1]["content"][0] == {
        "type": "tool_result", "tool_use_id": "t1", "content": "result-json"
    }


# --- generate / tools ------------------------------------------------------


async def test_generate_returns_text_and_usage():
    provider = _provider()
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=SimpleNamespace(
        content=[_block("text", text="hello")],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3),
    ))
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        text, usage = await provider.generate(model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hi"}])

    assert text == "hello"
    assert usage.provider == "anthropic" and usage.input_tokens == 7
    kwargs = client.messages.create.await_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"  # bare name


async def test_generate_with_tools_returns_tool_call():
    provider = _provider()
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=SimpleNamespace(
        content=[_block("tool_use", id="tu1", name="list_executions", input={"limit": 3})],
        usage=SimpleNamespace(input_tokens=5, output_tokens=2),
    ))
    tools = [RuntimeToolDefinition(name="list_executions", description="d", input_schema={"type": "object"})]
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        resp = await provider.generate_with_tools(
            model="claude-sonnet-4-6", messages=[{"role": "user", "content": "go"}], runtime_tools=tools
        )

    assert resp.tool_calls[0].tool_name == "list_executions"
    assert resp.tool_calls[0].arguments == {"limit": 3}
    # assistant_message is OpenAI-shaped for loop consistency
    assert resp.assistant_message["tool_calls"][0]["function"]["name"] == "list_executions"


# --- prompt caching --------------------------------------------------------


class TestPromptCaching:
    """Anthropic caching is opt-in: without a cache_control breakpoint nothing is
    cached at all, so the breakpoint placement is the whole feature."""

    async def test_last_tool_carries_the_cache_breakpoint(self):
        provider = _provider()
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[_block("text", text="ok")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        ))
        tools = [
            RuntimeToolDefinition(name="a", description="d", input_schema={"type": "object"}),
            RuntimeToolDefinition(name="b", description="d", input_schema={"type": "object"}),
        ]
        with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
            await provider.generate_with_tools(
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": "go"}],
                runtime_tools=tools,
            )
        sent = client.messages.create.await_args.kwargs["tools"]
        assert "cache_control" not in sent[0]
        assert sent[-1]["cache_control"] == {"type": "ephemeral"}

    async def test_system_block_gets_no_breakpoint(self):
        """A breakpoint on the per-turn system message would pay the 1.25x write
        premium every turn and almost never hit."""
        provider = _provider()
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[_block("text", text="ok")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        ))
        tools = [RuntimeToolDefinition(name="a", description="d", input_schema={"type": "object"})]
        with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
            await provider.generate_with_tools(
                model="claude-sonnet-4-6",
                messages=[{"role": "system", "content": "stable prose"},
                          {"role": "user", "content": "go"}],
                runtime_tools=tools,
            )
        assert client.messages.create.await_args.kwargs["system"] == "stable prose"

    async def test_no_tools_means_no_breakpoint(self):
        provider = _provider()
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[_block("text", text="ok")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        ))
        with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
            await provider.generate_with_tools(
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": "go"}],
                runtime_tools=[],
            )
        assert client.messages.create.await_args.kwargs["tools"] is None

    async def test_cache_read_and_write_are_summed_into_total_input(self):
        """Anthropic reports input_tokens EXCLUSIVE of cache reads/writes; the
        UsageStats invariant is that input_tokens is the total prompt size."""
        provider = _provider()
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[_block("text", text="ok")],
            usage=SimpleNamespace(
                input_tokens=200,
                output_tokens=50,
                cache_read_input_tokens=5000,
                cache_creation_input_tokens=100,
            ),
        ))
        with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
            _text, usage = await provider.generate(
                model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hi"}]
            )
        assert usage.input_tokens == 5300
        assert usage.cached_input_tokens == 5000
        assert usage.cache_write_tokens == 100
        assert usage.uncached_input_tokens == 200
        expected = (
            200 * 3.00 + 5000 * 3.00 * 0.10 + 100 * 3.00 * 1.25 + 50 * 15.00
        ) / 1_000_000
        assert usage.cost_usd == round(expected, 6)

    async def test_missing_cache_fields_degrade_to_no_cache(self):
        provider = _provider()
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[_block("text", text="ok")],
            usage=SimpleNamespace(input_tokens=200, output_tokens=50),
        ))
        with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
            _text, usage = await provider.generate(
                model="claude-sonnet-4-6", messages=[{"role": "user", "content": "hi"}]
            )
        assert usage.input_tokens == 200 and usage.cached_input_tokens == 0

    async def test_tool_schema_keys_are_canonically_ordered(self):
        provider = _provider()
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=SimpleNamespace(
            content=[_block("text", text="ok")],
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        ))
        tools = [RuntimeToolDefinition(
            name="a", description="d",
            input_schema={"required": ["b"], "type": "object",
                          "properties": {"b": {"type": "string"}, "a": {"type": "string"}}},
        )]
        with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
            await provider.generate_with_tools(
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": "go"}],
                runtime_tools=tools,
            )
        schema = client.messages.create.await_args.kwargs["tools"][0]["input_schema"]
        assert list(schema.keys()) == ["properties", "required", "type"]
