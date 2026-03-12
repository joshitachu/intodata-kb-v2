from unittest.mock import AsyncMock, patch

import pytest

from src.generation.llm_client import generate_answer, generate_answer_stream
from src.generation.usage import UsageStats


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
        mock_kimi.assert_awaited_once_with(MESSAGES)
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
        mock_ollama.assert_awaited_once_with(MESSAGES)


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

    async def _fake_kimi_stream(messages):
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

    async def _failing_stream(messages):
        raise Exception("Kimi stream error")
        yield  # noqa: unreachable — makes this an async generator

    async def _working_stream(messages):
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

    async def _failing(messages):
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
