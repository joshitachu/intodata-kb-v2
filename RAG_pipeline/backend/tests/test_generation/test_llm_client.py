from unittest.mock import AsyncMock, patch

import pytest

from src.generation.llm_client import (
    RuntimeToolCallRequest,
    ToolAwareLLMResponse,
    generate_answer,
    generate_answer_stream,
    generate_answer_with_runtime_tools,
)
from src.generation.usage import UsageStats
from src.runtime.models import RuntimeToolDefinition


MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What is 2+2?"},
]


# ---------------------------------------------------------------------------
# generate_answer tests
# ---------------------------------------------------------------------------


async def test_first_provider_success():
    """Kimi works → Ollama not called."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_generate", new_callable=AsyncMock) as mock_kimi,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]
        mock_kimi.return_value = ("Kimi answer", UsageStats(provider="kimi", model="kimi-k2.5"))

        result = await generate_answer(MESSAGES)

        assert result == "Kimi answer"
        mock_kimi.assert_awaited_once_with(MESSAGES, images=None)
        mock_ollama.assert_not_awaited()


async def test_fallback_to_ollama():
    """Kimi fails → Ollama succeeds."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_generate", new_callable=AsyncMock) as mock_kimi,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]
        mock_kimi.side_effect = Exception("Kimi rate limited")
        mock_ollama.return_value = ("Ollama answer", UsageStats(provider="ollama", model="gemma3:4b"))

        result = await generate_answer(MESSAGES)

        assert result == "Ollama answer"
        mock_kimi.assert_awaited_once()
        mock_ollama.assert_awaited_once_with(MESSAGES, images=None)


async def test_all_providers_fail():
    """Both fail → RuntimeError."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_generate", new_callable=AsyncMock) as mock_kimi,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]
        mock_kimi.side_effect = Exception("Kimi error")
        mock_ollama.side_effect = Exception("Ollama error")

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await generate_answer(MESSAGES)


async def test_custom_provider_order():
    """Respects custom provider list — ollama first."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
        patch("src.generation.llm_client._kimi_generate", new_callable=AsyncMock) as mock_kimi,
    ):
        mock_settings.llm_providers = ["ollama"]
        mock_ollama.return_value = ("Ollama first", UsageStats(provider="ollama", model="gemma3:4b"))

        result = await generate_answer(MESSAGES)

        assert result == "Ollama first"
        mock_ollama.assert_awaited_once()
        mock_kimi.assert_not_awaited()


# ---------------------------------------------------------------------------
# generate_answer_stream tests
# ---------------------------------------------------------------------------


async def test_stream_first_provider_success():
    """Streaming: Kimi works → Ollama not called."""

    async def _fake_kimi_stream(messages, images=None):
        yield "Hello "
        yield "world"

    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_stream", side_effect=_fake_kimi_stream),
        patch("src.generation.llm_client._ollama_stream", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]

        tokens = []
        async for token in generate_answer_stream(MESSAGES):
            tokens.append(token)

        assert tokens == ["Hello ", "world"]
        mock_ollama.assert_not_awaited()


async def test_stream_fallback():
    """Streaming: Kimi fails → Ollama streams successfully."""

    async def _failing_stream(messages, images=None):
        raise Exception("Kimi stream error")
        yield  # noqa: unreachable — makes this an async generator

    async def _working_stream(messages, images=None):
        yield "Fallback "
        yield "works"

    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_stream", side_effect=_failing_stream),
        patch("src.generation.llm_client._ollama_stream", side_effect=_working_stream),
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]

        tokens = []
        async for token in generate_answer_stream(MESSAGES):
            tokens.append(token)

        assert tokens == ["Fallback ", "works"]


async def test_stream_all_fail():
    """Streaming: all providers fail → RuntimeError."""

    async def _failing(messages, images=None):
        raise Exception("fail")
        yield  # noqa: unreachable

    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_stream", side_effect=_failing),
        patch("src.generation.llm_client._ollama_stream", side_effect=_failing),
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            async for _ in generate_answer_stream(MESSAGES):
                pass


# ---------------------------------------------------------------------------
# generate_answer_with_runtime_tools tests (EP-004 slice 3)
# ---------------------------------------------------------------------------

RUNTIME_TOOLS = [
    RuntimeToolDefinition(name="list_executions", description="List executions", input_schema={"type": "object"}),
]


async def test_tool_aware_returns_tool_call():
    """Kimi requests a tool → tool_call is populated, no text."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_generate_with_tools", new_callable=AsyncMock) as mock_kimi,
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]
        mock_kimi.return_value = ToolAwareLLMResponse(
            provider="kimi",
            tool_call=RuntimeToolCallRequest(call_id="call_1", tool_name="list_executions", arguments={}),
            assistant_message={"role": "assistant", "content": "", "tool_calls": []},
            usage=UsageStats(provider="kimi", model="kimi-k2.5"),
        )

        response = await generate_answer_with_runtime_tools(MESSAGES, RUNTIME_TOOLS)

        assert response.tool_call.tool_name == "list_executions"
        assert response.no_tool_support is False
        mock_kimi.assert_awaited_once_with(MESSAGES, RUNTIME_TOOLS, images=None)


async def test_tool_aware_returns_text():
    """Kimi answers directly → text is populated, no tool_call."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_generate_with_tools", new_callable=AsyncMock) as mock_kimi,
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]
        mock_kimi.return_value = ToolAwareLLMResponse(
            provider="kimi", text="Plain answer", usage=UsageStats(provider="kimi", model="kimi-k2.5")
        )

        response = await generate_answer_with_runtime_tools(MESSAGES, RUNTIME_TOOLS)

        assert response.text == "Plain answer"
        assert response.tool_call is None


async def test_tool_aware_no_provider_supports_tools():
    """Only ollama configured (no tool-aware function) → no_tool_support."""
    with patch("src.generation.llm_client.settings") as mock_settings:
        mock_settings.llm_providers = ["ollama"]

        response = await generate_answer_with_runtime_tools(MESSAGES, RUNTIME_TOOLS)

        assert response.no_tool_support is True
        assert response.tool_call is None
        assert response.text is None


async def test_tool_aware_provider_error_falls_through_to_no_tool_support():
    """Kimi raises → chain exhausted (ollama has no tool support) → no_tool_support."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._kimi_generate_with_tools", new_callable=AsyncMock) as mock_kimi,
    ):
        mock_settings.llm_providers = ["kimi", "ollama"]
        mock_kimi.side_effect = Exception("Kimi rate limited")

        response = await generate_answer_with_runtime_tools(MESSAGES, RUNTIME_TOOLS)

        assert response.no_tool_support is True
        mock_kimi.assert_awaited_once()
