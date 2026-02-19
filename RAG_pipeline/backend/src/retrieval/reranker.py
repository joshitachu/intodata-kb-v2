"""Cross-encoder reranking for improved retrieval precision."""
from __future__ import annotations

import logging
import time

from src.config import settings

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load the cross-encoder model (singleton)."""
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(settings.reranker_model)
        logger.info(f"Loaded cross-encoder model: {settings.reranker_model}")
    return _model


def rerank(
    query: str,
    texts: list[str],
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Rerank texts by relevance to query using cross-encoder.

    Args:
        query: The user's query.
        texts: List of candidate texts to rerank.
        top_k: Number of top results to return.

    Returns:
        List of (original_index, score) tuples, sorted by score descending.
    """
    if not texts:
        return []

    model = _get_model()
    start = time.perf_counter()

    pairs = [[query, text] for text in texts]
    scores = model.predict(pairs)

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(f"Reranked {len(texts)} candidates in {elapsed_ms}ms")

    # Create (index, score) pairs and sort by score descending
    indexed_scores = [(i, float(s)) for i, s in enumerate(scores)]
    indexed_scores.sort(key=lambda x: x[1], reverse=True)

    result = indexed_scores[:top_k]

    if result:
        logger.info(
            f"Reranker top scores: "
            + ", ".join(f"#{idx}={score:.4f}" for idx, score in result)
        )

    return result
