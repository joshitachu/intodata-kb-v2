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

    assert resp.tool_call.tool_name == "list_executions"
    assert resp.tool_call.arguments == {"limit": 3}
    # assistant_message is OpenAI-shaped for loop consistency
    assert resp.assistant_message["tool_calls"][0]["function"]["name"] == "list_executions"
