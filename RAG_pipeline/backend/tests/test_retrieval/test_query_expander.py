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
async def test_expand_query_returns_alternatives(mock_settings):
    mock_settings.query_expansion_enabled = True

    with patch(
        "src.generation.llm_client.generate_answer",
        new_callable=AsyncMock,
    ) as mock_gen:
        mock_gen.return_value = "Human resources guidelines\nWorkplace rules and regulations"

        result = await expand_query("HR policies")

    assert len(result) == 3
    assert result[0] == "HR policies"
    assert "Human resources guidelines" in result[1]


@patch("src.retrieval.query_expander.settings")
async def test_expand_query_fallback_on_error(mock_settings):
    mock_settings.query_expansion_enabled = True

    with patch(
        "src.generation.llm_client.generate_answer",
        new_callable=AsyncMock,
    ) as mock_gen:
        mock_gen.side_effect = RuntimeError("All providers failed")

        result = await expand_query("HR policies")

    assert result == ["HR policies"]


@patch("src.retrieval.query_expander.settings")
async def test_expand_query_fallback_on_empty_response(mock_settings):
    mock_settings.query_expansion_enabled = True

    with patch(
        "src.generation.llm_client.generate_answer",
        new_callable=AsyncMock,
    ) as mock_gen:
        mock_gen.return_value = ""

        result = await expand_query("HR policies")

    assert result == ["HR policies"]
