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

    assert resp.tool_call.tool_name == "list_executions"
    assert resp.tool_call.arguments == {"limit": 5}
    assert resp.no_tool_support is False
