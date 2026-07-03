"""Adapter tests for the Ollama provider — per-model tool-support degradation."""
from unittest.mock import AsyncMock, MagicMock, patch

import ollama as ollama_lib
import pytest

from src.generation.providers.ollama_provider import OllamaProvider
from src.runtime.models import RuntimeToolDefinition

TOOLS = [RuntimeToolDefinition(name="f", description="d", input_schema={"type": "object"})]
MSGS = [{"role": "user", "content": "hi"}]


def _provider():
    return OllamaProvider(base_url="http://localhost:11434")


async def test_tool_call_unsupported_model_degrades_gracefully():
    provider = _provider()
    client = MagicMock()
    client.chat = AsyncMock(
        side_effect=ollama_lib.ResponseError("gemma3:12b does not support tools", 400)
    )
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        resp = await provider.generate_with_tools(model="gemma3:12b", messages=MSGS, runtime_tools=TOOLS)

    assert resp.no_tool_support is True
    assert resp.tool_call is None
    assert "gemma3:12b" in provider._no_tool_models


async def test_unsupported_model_is_not_retried():
    provider = _provider()
    client = MagicMock()
    client.chat = AsyncMock(
        side_effect=ollama_lib.ResponseError("gemma3:12b does not support tools", 400)
    )
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        await provider.generate_with_tools(model="gemma3:12b", messages=MSGS, runtime_tools=TOOLS)
        await provider.generate_with_tools(model="gemma3:12b", messages=MSGS, runtime_tools=TOOLS)

    # Second call short-circuits before hitting the client again.
    assert client.chat.await_count == 1


async def test_other_response_errors_still_raise():
    provider = _provider()
    client = MagicMock()
    client.chat = AsyncMock(side_effect=ollama_lib.ResponseError("connection refused", 500))
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        with pytest.raises(ollama_lib.ResponseError):
            await provider.generate_with_tools(model="llama3.1", messages=MSGS, runtime_tools=TOOLS)


async def test_tool_capable_model_returns_tool_call():
    provider = _provider()
    client = MagicMock()
    client.chat = AsyncMock(return_value={
        "message": {
            "content": "",
            "tool_calls": [{"function": {"name": "f", "arguments": {"a": 1}}}],
        },
        "prompt_eval_count": 3,
        "eval_count": 1,
    })
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        resp = await provider.generate_with_tools(model="llama3.1", messages=MSGS, runtime_tools=TOOLS)

    assert resp.tool_call.tool_name == "f"
    assert resp.tool_call.arguments == {"a": 1}
