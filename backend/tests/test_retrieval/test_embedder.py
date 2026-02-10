from unittest.mock import AsyncMock, patch

import pytest

from src.retrieval.embedder import embed_text, embed_texts, EMBEDDING_DIM


async def test_embed_text_returns_vector():
    mock_response = {"embeddings": [[0.1] * EMBEDDING_DIM]}

    with patch("src.retrieval.embedder.ollama_lib.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.embed.return_value = mock_response
        MockClient.return_value = instance

        result = await embed_text("hello world")

    assert len(result) == EMBEDDING_DIM
    assert result == [0.1] * EMBEDDING_DIM


async def test_embed_texts_returns_vectors():
    mock_response = {
        "embeddings": [
            [0.1] * EMBEDDING_DIM,
            [0.2] * EMBEDDING_DIM,
        ]
    }

    with patch("src.retrieval.embedder.ollama_lib.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.embed.return_value = mock_response
        MockClient.return_value = instance

        results = await embed_texts(["hello", "world"])

    assert len(results) == 2
    assert len(results[0]) == EMBEDDING_DIM


def test_embedding_dim_constant():
    assert EMBEDDING_DIM == 768
