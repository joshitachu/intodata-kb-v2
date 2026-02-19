from unittest.mock import AsyncMock, patch

import pytest

from src.retrieval.query_expander import expand_query, should_expand_query


def test_should_expand_short_query():
    assert should_expand_query("HR policies") is True
    assert should_expand_query("WFH") is True


def test_should_expand_abbreviations():
    assert should_expand_query("What is the ISO 27001 compliance policy") is True


def test_should_not_expand_long_specific_query():
    assert should_expand_query(
        "What is the detailed policy for remote work arrangements in the engineering department"
    ) is False


@patch("src.retrieval.query_expander.settings")
async def test_expand_query_disabled(mock_settings):
    mock_settings.query_expansion_enabled = False
    result = await expand_query("HR policies")
    assert result == ["HR policies"]


@patch("src.retrieval.query_expander.settings")
@patch("src.retrieval.query_expander.ollama_lib")
async def test_expand_query_returns_alternatives(mock_ollama, mock_settings):
    mock_settings.query_expansion_enabled = True
    mock_settings.ollama_base_url = "http://localhost:11434"
    mock_settings.ollama_router_model = "gemma3:4b"

    mock_client = AsyncMock()
    mock_client.chat.return_value = {
        "message": {"content": "Human resources guidelines\nWorkplace rules and regulations"}
    }
    mock_ollama.AsyncClient.return_value = mock_client

    result = await expand_query("HR policies")

    assert len(result) == 3
    assert result[0] == "HR policies"
    assert "Human resources guidelines" in result[1]


@patch("src.retrieval.query_expander.settings")
@patch("src.retrieval.query_expander.ollama_lib")
async def test_expand_query_fallback_on_error(mock_ollama, mock_settings):
    mock_settings.query_expansion_enabled = True
    mock_settings.ollama_base_url = "http://localhost:11434"
    mock_settings.ollama_router_model = "gemma3:4b"

    mock_client = AsyncMock()
    mock_client.chat.side_effect = ConnectionError("Ollama down")
    mock_ollama.AsyncClient.return_value = mock_client

    result = await expand_query("HR policies")

    assert result == ["HR policies"]


@patch("src.retrieval.query_expander.settings")
@patch("src.retrieval.query_expander.ollama_lib")
async def test_expand_query_fallback_on_empty_response(mock_ollama, mock_settings):
    mock_settings.query_expansion_enabled = True
    mock_settings.ollama_base_url = "http://localhost:11434"
    mock_settings.ollama_router_model = "gemma3:4b"

    mock_client = AsyncMock()
    mock_client.chat.return_value = {"message": {"content": ""}}
    mock_ollama.AsyncClient.return_value = mock_client

    result = await expand_query("HR policies")

    assert result == ["HR policies"]
