from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.generation.llm_client import generate_answer, generate_answer_stream


MESSAGES = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "What is 2+2?"},
]


# ---------------------------------------------------------------------------
# generate_answer tests
# ---------------------------------------------------------------------------


async def test_first_provider_success():
    """Groq works → others not called."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._groq_generate", new_callable=AsyncMock) as mock_groq,
        patch("src.generation.llm_client._openrouter_generate", new_callable=AsyncMock) as mock_or,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["groq", "openrouter", "ollama"]
        mock_groq.return_value = "Groq answer"

        result = await generate_answer(MESSAGES)

        assert result == "Groq answer"
        mock_groq.assert_awaited_once_with(MESSAGES)
        mock_or.assert_not_awaited()
        mock_ollama.assert_not_awaited()


async def test_fallback_to_second():
    """Groq fails → OpenRouter succeeds."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._groq_generate", new_callable=AsyncMock) as mock_groq,
        patch(
            "src.generation.llm_client._openrouter_generate", new_callable=AsyncMock
        ) as mock_or,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["groq", "openrouter", "ollama"]
        mock_groq.side_effect = Exception("Groq rate limited")
        mock_or.return_value = "OpenRouter answer"

        result = await generate_answer(MESSAGES)

        assert result == "OpenRouter answer"
        mock_groq.assert_awaited_once()
        mock_or.assert_awaited_once_with(MESSAGES)
        mock_ollama.assert_not_awaited()


async def test_fallback_to_third():
    """Groq + OpenRouter fail → Ollama succeeds."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._groq_generate", new_callable=AsyncMock) as mock_groq,
        patch(
            "src.generation.llm_client._openrouter_generate", new_callable=AsyncMock
        ) as mock_or,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["groq", "openrouter", "ollama"]
        mock_groq.side_effect = Exception("Groq down")
        mock_or.side_effect = Exception("OpenRouter down")
        mock_ollama.return_value = "Ollama answer"

        result = await generate_answer(MESSAGES)

        assert result == "Ollama answer"
        mock_groq.assert_awaited_once()
        mock_or.assert_awaited_once()
        mock_ollama.assert_awaited_once_with(MESSAGES)


async def test_all_providers_fail():
    """All three fail → RuntimeError."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._groq_generate", new_callable=AsyncMock) as mock_groq,
        patch(
            "src.generation.llm_client._openrouter_generate", new_callable=AsyncMock
        ) as mock_or,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
    ):
        mock_settings.llm_providers = ["groq", "openrouter", "ollama"]
        mock_groq.side_effect = Exception("Groq error")
        mock_or.side_effect = Exception("OpenRouter error")
        mock_ollama.side_effect = Exception("Ollama error")

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            await generate_answer(MESSAGES)


async def test_custom_provider_order():
    """Respects custom provider list — ollama first."""
    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._ollama_generate", new_callable=AsyncMock) as mock_ollama,
        patch("src.generation.llm_client._groq_generate", new_callable=AsyncMock) as mock_groq,
    ):
        mock_settings.llm_providers = ["ollama"]
        mock_ollama.return_value = "Ollama first"

        result = await generate_answer(MESSAGES)

        assert result == "Ollama first"
        mock_ollama.assert_awaited_once()
        mock_groq.assert_not_awaited()


# ---------------------------------------------------------------------------
# generate_answer_stream tests
# ---------------------------------------------------------------------------


async def test_stream_first_provider_success():
    """Streaming: Groq works → others not called."""

    async def _fake_groq_stream(messages):
        yield "Hello "
        yield "world"

    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._groq_stream", side_effect=_fake_groq_stream),
        patch("src.generation.llm_client._openrouter_stream", new_callable=AsyncMock) as mock_or,
    ):
        mock_settings.llm_providers = ["groq", "openrouter"]

        tokens = []
        async for token in generate_answer_stream(MESSAGES):
            tokens.append(token)

        assert tokens == ["Hello ", "world"]
        mock_or.assert_not_awaited()


async def test_stream_fallback():
    """Streaming: Groq fails → OpenRouter streams successfully."""

    async def _failing_stream(messages):
        raise Exception("Groq stream error")
        yield  # noqa: unreachable — makes this an async generator

    async def _working_stream(messages):
        yield "Fallback "
        yield "works"

    with (
        patch("src.generation.llm_client.settings") as mock_settings,
        patch("src.generation.llm_client._groq_stream", side_effect=_failing_stream),
        patch("src.generation.llm_client._openrouter_stream", side_effect=_working_stream),
    ):
        mock_settings.llm_providers = ["groq", "openrouter"]

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
        patch("src.generation.llm_client._groq_stream", side_effect=_failing),
        patch("src.generation.llm_client._openrouter_stream", side_effect=_failing),
        patch("src.generation.llm_client._ollama_stream", side_effect=_failing),
    ):
        mock_settings.llm_providers = ["groq", "openrouter", "ollama"]

        with pytest.raises(RuntimeError, match="All LLM providers failed"):
            async for _ in generate_answer_stream(MESSAGES):
                pass
