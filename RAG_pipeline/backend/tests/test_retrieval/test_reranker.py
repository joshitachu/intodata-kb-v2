from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.retrieval.reranker import rerank


def _voyage_response(results: list[dict]) -> httpx.Response:
    """Build a fake Voyage rerank API response."""
    return httpx.Response(
        status_code=200,
        json={"data": results},
        request=httpx.Request("POST", "https://api.voyageai.com/v1/rerank"),
    )


@pytest.mark.asyncio
@patch("src.retrieval.reranker.settings")
async def test_rerank_returns_sorted_by_score(mock_settings):
    mock_settings.voyage_api_key = "test-key"
    mock_settings.reranker_model = "rerank-2.5"
    mock_settings.reranker_min_score = 0.0

    response = _voyage_response([
        {"index": 0, "relevance_score": 0.1},
        {"index": 1, "relevance_score": 0.9},
        {"index": 2, "relevance_score": 0.5},
    ])

    with patch("src.retrieval.reranker.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await rerank("query", ["low", "high", "mid"], top_k=3)

    assert len(result) == 3
    assert result[0] == (1, 0.9)   # "high" first
    assert result[1] == (2, 0.5)   # "mid" second
    assert result[2] == (0, 0.1)   # "low" third


@pytest.mark.asyncio
@patch("src.retrieval.reranker.settings")
async def test_rerank_respects_top_k_via_api(mock_settings):
    mock_settings.voyage_api_key = "test-key"
    mock_settings.reranker_model = "rerank-2.5"
    mock_settings.reranker_min_score = 0.0

    # Voyage API respects top_k and only returns 2 results
    response = _voyage_response([
        {"index": 1, "relevance_score": 0.9},
        {"index": 3, "relevance_score": 0.7},
    ])

    with patch("src.retrieval.reranker.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await rerank("query", ["a", "b", "c", "d"], top_k=2)

    assert len(result) == 2
    assert result[0][0] == 1  # highest score
    assert result[1][0] == 3  # second highest

    # Verify top_k was sent to the API
    call_json = mock_client.post.call_args[1]["json"]
    assert call_json["top_k"] == 2


@pytest.mark.asyncio
async def test_rerank_empty_texts():
    result = await rerank("query", [], top_k=5)
    assert result == []


@pytest.mark.asyncio
@patch("src.retrieval.reranker.settings")
async def test_rerank_sends_correct_payload(mock_settings):
    mock_settings.voyage_api_key = "test-key"
    mock_settings.reranker_model = "rerank-2.5"
    mock_settings.reranker_min_score = 0.0

    response = _voyage_response([
        {"index": 0, "relevance_score": 0.5},
        {"index": 1, "relevance_score": 0.8},
    ])

    with patch("src.retrieval.reranker.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        await rerank("security policy", ["text about security", "text about HR"], top_k=2)

    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://api.voyageai.com/v1/rerank"
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-key"
    assert call_args[1]["json"] == {
        "query": "security policy",
        "documents": ["text about security", "text about HR"],
        "model": "rerank-2.5",
        "top_k": 2,
    }


@pytest.mark.asyncio
@patch("src.retrieval.reranker.settings")
async def test_rerank_single_text(mock_settings):
    mock_settings.voyage_api_key = "test-key"
    mock_settings.reranker_model = "rerank-2.5"
    mock_settings.reranker_min_score = 0.0

    response = _voyage_response([
        {"index": 0, "relevance_score": 0.75},
    ])

    with patch("src.retrieval.reranker.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await rerank("query", ["only text"], top_k=1)

    assert len(result) == 1
    assert result[0] == (0, 0.75)


# --- Min-score filtering tests ---


@pytest.mark.asyncio
@patch("src.retrieval.reranker.settings")
async def test_rerank_filters_below_min_score(mock_settings):
    mock_settings.voyage_api_key = "test-key"
    mock_settings.reranker_model = "rerank-2.5"
    mock_settings.reranker_min_score = 0.3

    response = _voyage_response([
        {"index": 0, "relevance_score": 0.05},
        {"index": 1, "relevance_score": 0.85},
        {"index": 2, "relevance_score": 0.02},
    ])

    with patch("src.retrieval.reranker.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await rerank("query", ["bad", "good", "bad2"], top_k=5)

    assert len(result) == 1
    assert result[0] == (1, 0.85)


@pytest.mark.asyncio
@patch("src.retrieval.reranker.settings")
async def test_rerank_all_below_min_score_returns_empty(mock_settings):
    mock_settings.voyage_api_key = "test-key"
    mock_settings.reranker_model = "rerank-2.5"
    mock_settings.reranker_min_score = 0.5

    response = _voyage_response([
        {"index": 0, "relevance_score": 0.1},
        {"index": 1, "relevance_score": 0.2},
    ])

    with patch("src.retrieval.reranker.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await rerank("query", ["irrelevant1", "irrelevant2"], top_k=5)

    assert result == []


@pytest.mark.asyncio
@patch("src.retrieval.reranker.settings")
async def test_rerank_min_score_boundary(mock_settings):
    mock_settings.voyage_api_key = "test-key"
    mock_settings.reranker_model = "rerank-2.5"
    mock_settings.reranker_min_score = 0.05

    response = _voyage_response([
        {"index": 0, "relevance_score": 0.05},
    ])

    with patch("src.retrieval.reranker.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.return_value = response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await rerank("query", ["borderline"], top_k=1)

    # Score exactly at min_score (0.05 >= 0.05) should pass
    assert len(result) == 1
    assert result[0] == (0, 0.05)
