from unittest.mock import MagicMock, patch
import numpy as np

from src.retrieval.reranker import rerank


@patch("src.retrieval.reranker.settings")
@patch("src.retrieval.reranker._get_model")
def test_rerank_returns_sorted_by_score(mock_get_model, mock_settings):
    mock_settings.reranker_min_score = -999
    mock_model = MagicMock()
    # Scores: text[0]=0.1, text[1]=0.9, text[2]=0.5
    mock_model.predict.return_value = np.array([0.1, 0.9, 0.5])
    mock_get_model.return_value = mock_model

    result = rerank("query", ["low", "high", "mid"], top_k=3)

    assert len(result) == 3
    assert result[0] == (1, 0.9)   # "high" first
    assert result[1] == (2, 0.5)   # "mid" second
    assert result[2] == (0, 0.1)   # "low" third


@patch("src.retrieval.reranker.settings")
@patch("src.retrieval.reranker._get_model")
def test_rerank_respects_top_k(mock_get_model, mock_settings):
    mock_settings.reranker_min_score = -999
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.1, 0.9, 0.5, 0.7])
    mock_get_model.return_value = mock_model

    result = rerank("query", ["a", "b", "c", "d"], top_k=2)

    assert len(result) == 2
    assert result[0][0] == 1  # highest score
    assert result[1][0] == 3  # second highest


@patch("src.retrieval.reranker._get_model")
def test_rerank_empty_texts(mock_get_model):
    result = rerank("query", [], top_k=5)
    assert result == []
    mock_get_model.assert_not_called()


@patch("src.retrieval.reranker.settings")
@patch("src.retrieval.reranker._get_model")
def test_rerank_creates_correct_pairs(mock_get_model, mock_settings):
    mock_settings.reranker_min_score = -999
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.5, 0.8])
    mock_get_model.return_value = mock_model

    rerank("security policy", ["text about security", "text about HR"], top_k=2)

    call_args = mock_model.predict.call_args[0][0]
    assert call_args == [
        ["security policy", "text about security"],
        ["security policy", "text about HR"],
    ]


@patch("src.retrieval.reranker.settings")
@patch("src.retrieval.reranker._get_model")
def test_rerank_single_text(mock_get_model, mock_settings):
    mock_settings.reranker_min_score = -999
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.75])
    mock_get_model.return_value = mock_model

    result = rerank("query", ["only text"], top_k=1)

    assert len(result) == 1
    assert result[0] == (0, 0.75)


# --- Min-score filtering tests ---


@patch("src.retrieval.reranker.settings")
@patch("src.retrieval.reranker._get_model")
def test_rerank_filters_below_min_score(mock_get_model, mock_settings):
    mock_settings.reranker_min_score = 0.0
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([-9.1, 2.5, -0.5])
    mock_get_model.return_value = mock_model

    result = rerank("query", ["bad", "good", "bad2"], top_k=5)

    assert len(result) == 1
    assert result[0] == (1, 2.5)


@patch("src.retrieval.reranker.settings")
@patch("src.retrieval.reranker._get_model")
def test_rerank_all_below_min_score_returns_empty(mock_get_model, mock_settings):
    mock_settings.reranker_min_score = 0.0
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([-9.1, -11.2])
    mock_get_model.return_value = mock_model

    result = rerank("query", ["irrelevant1", "irrelevant2"], top_k=5)

    assert result == []


@patch("src.retrieval.reranker.settings")
@patch("src.retrieval.reranker._get_model")
def test_rerank_min_score_boundary(mock_get_model, mock_settings):
    mock_settings.reranker_min_score = 0.0
    mock_model = MagicMock()
    mock_model.predict.return_value = np.array([0.0])
    mock_get_model.return_value = mock_model

    result = rerank("query", ["borderline"], top_k=1)

    # Score exactly at min_score (0.0 >= 0.0) should pass
    assert len(result) == 1
    assert result[0] == (0, 0.0)
