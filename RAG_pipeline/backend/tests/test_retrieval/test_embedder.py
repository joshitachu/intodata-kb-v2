from unittest.mock import AsyncMock, patch

import pytest

from src.retrieval.embedder import embed_text, embed_texts, get_embedding_dim


async def test_embed_text_returns_vector():
    mock_response = {"embeddings": [[0.1] * 768]}

    with patch("src.retrieval.embedder._ollama_embed_single", new_callable=AsyncMock) as mock_fn:
        mock_fn.return_value = [0.1] * 768
        # Force Ollama provider (no voyage key)
        with patch("src.retrieval.embedder.settings") as mock_settings:
            mock_settings.embedding_providers = ["ollama"]
            mock_settings.voyage_api_key = ""
            mock_settings.ollama_base_url = "http://localhost:11434"
            mock_settings.ollama_embed_model = "nomic-embed-text"

            result = await embed_text("hello world")

    assert len(result) == 768
    assert result == [0.1] * 768


async def test_embed_texts_returns_vectors():
    with patch("src.retrieval.embedder._ollama_embed_batch", new_callable=AsyncMock) as mock_fn:
        mock_fn.return_value = [[0.1] * 768, [0.2] * 768]
        with patch("src.retrieval.embedder.settings") as mock_settings:
            mock_settings.embedding_providers = ["ollama"]
            mock_settings.voyage_api_key = ""
            mock_settings.ollama_base_url = "http://localhost:11434"
            mock_settings.ollama_embed_model = "nomic-embed-text"

            results = await embed_texts(["hello", "world"])

    assert len(results) == 2
    assert len(results[0]) == 768


def test_get_embedding_dim_ollama_default():
    with patch("src.retrieval.embedder.settings") as mock_settings:
        mock_settings.embedding_providers = ["ollama"]
        mock_settings.voyage_api_key = ""
        assert get_embedding_dim() == 768


def test_get_embedding_dim_voyage():
    with patch("src.retrieval.embedder.settings") as mock_settings:
        mock_settings.embedding_providers = ["voyage", "ollama"]
        mock_settings.voyage_api_key = "test-key"
        mock_settings.voyage_embed_dim = 1024
        assert get_embedding_dim() == 1024
