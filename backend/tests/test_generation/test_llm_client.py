from unittest.mock import AsyncMock, patch

from src.generation.llm_client import generate_answer


async def test_generate_answer_returns_content():
    mock_response = {"message": {"content": "The answer is 42."}}

    with patch("src.generation.llm_client.ollama_lib.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.chat.return_value = mock_response
        MockClient.return_value = instance

        result = await generate_answer([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is the meaning of life?"},
        ])

    assert result == "The answer is 42."
    instance.chat.assert_called_once()


async def test_generate_answer_passes_messages():
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "User question"},
    ]
    mock_response = {"message": {"content": "Response"}}

    with patch("src.generation.llm_client.ollama_lib.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.chat.return_value = mock_response
        MockClient.return_value = instance

        await generate_answer(messages)

    call_kwargs = instance.chat.call_args.kwargs
    assert call_kwargs["messages"] == messages
