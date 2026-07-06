"""Adapter tests for the Google Gemini provider (mocked SDK) + translation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.providers.google_provider import GoogleProvider, _to_gemini
from src.runtime.models import RuntimeToolDefinition


def _provider():
    return GoogleProvider(api_key="g-key")


def _resp(text=None, parts=None, prompt=6, out=4):
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts or []))
    return SimpleNamespace(
        text=text,
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=prompt, candidates_token_count=out),
    )


# --- translation -----------------------------------------------------------


def test_translation_system_and_roles():
    system, contents = _to_gemini(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ],
        None,
    )
    assert system == "sys"
    assert contents[0] == {"role": "user", "parts": [{"text": "hi"}]}
    assert contents[1] == {"role": "model", "parts": [{"text": "yo"}]}


def test_translation_tool_result_uses_recorded_function_name():
    _system, contents = _to_gemini(
        [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "{\"rows\": 2}"},
        ],
        None,
    )
    fr = contents[1]["parts"][0]["function_response"]
    assert fr["name"] == "lookup"          # resolved from the prior tool_call id
    assert fr["response"] == {"rows": 2}


# --- generate / tools ------------------------------------------------------


async def test_generate_returns_text_and_usage():
    provider = _provider()
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=_resp(text="hello"))
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        text, usage = await provider.generate(model="gemini-2.5-flash", messages=[{"role": "user", "content": "hi"}])

    assert text == "hello"
    assert usage.provider == "google" and usage.input_tokens == 6
    kwargs = client.aio.models.generate_content.await_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash"


async def test_generate_with_tools_returns_tool_call():
    provider = _provider()
    fc_part = SimpleNamespace(function_call=SimpleNamespace(name="list_executions", args={"limit": 2}), text=None)
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=_resp(parts=[fc_part]))
    tools = [RuntimeToolDefinition(name="list_executions", description="d", input_schema={"type": "object"})]
    with patch.object(provider, "_get_client", AsyncMock(return_value=client)):
        resp = await provider.generate_with_tools(
            model="gemini-2.5-flash", messages=[{"role": "user", "content": "go"}], runtime_tools=tools
        )

    assert resp.tool_calls[0].tool_name == "list_executions"
    assert resp.tool_calls[0].arguments == {"limit": 2}
    assert resp.assistant_message["tool_calls"][0]["function"]["name"] == "list_executions"
