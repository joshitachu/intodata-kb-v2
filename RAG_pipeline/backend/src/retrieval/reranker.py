"""Voyage AI reranking for improved retrieval precision."""
from __future__ import annotations

import asyncio
import httpx
import logging
import time

from src.config import settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = [2.0, 5.0, 15.0]  # seconds — tuned for Voyage free tier (3 RPM)


async def rerank(
    query: str,
    texts: list[str],
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Rerank texts using Voyage AI rerank API.

    Returns list of (original_index, score) tuples, sorted by score descending.
    Scores are in 0-1 range. Returns None on API failure (caller should fallback).
    """
    if not texts:
        return []

    start = time.perf_counter()

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.voyageai.com/v1/rerank",
                    headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
                    json={
                        "query": query,
                        "documents": texts,
                        "model": settings.reranker_model,
                        "top_k": top_k,
                    },
                )
                if response.status_code == 429:
                    # Use Retry-After header if provided, otherwise use backoff schedule
                    retry_after = response.headers.get("retry-after")
                    if retry_after:
                        wait = min(float(retry_after), 30.0)
                    else:
                        wait = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 15.0
                    logger.warning(
                        f"Voyage rerank 429 rate-limited (attempt {attempt + 1}/{_MAX_RETRIES}), "
                        f"retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()
                break
        except httpx.HTTPStatusError:
            raise  # non-429 HTTP errors — don't retry
        except Exception as e:
            last_exc = e
            logger.warning(f"Voyage rerank attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BACKOFF[attempt])
            continue
    else:
        # All retries exhausted
        raise last_exc or httpx.HTTPStatusError(
            "Voyage rerank failed after retries",
            request=httpx.Request("POST", "https://api.voyageai.com/v1/rerank"),
            response=response,
        )

    data = response.json()
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    logger.info(f"Reranked {len(texts)} candidates in {elapsed_ms}ms (Voyage)")

    # Extract (index, relevance_score) from response
    results = data.get("data", [])
    indexed_scores = [(r["index"], r["relevance_score"]) for r in results]
    indexed_scores.sort(key=lambda x: x[1], reverse=True)

    # Filter by min_score
    min_score = settings.reranker_min_score
    pre_filter_count = len(indexed_scores)
    indexed_scores = [(i, s) for i, s in indexed_scores if s >= min_score]

    dropped = pre_filter_count - len(indexed_scores)
    if indexed_scores:
        logger.info(
            "Reranker top scores: "
            + ", ".join(f"#{idx}={score:.4f}" for idx, score in indexed_scores)
            + (f" | {dropped} dropped below {min_score}" if dropped else "")
        )
    elif dropped:
        logger.info(f"Reranker: all {pre_filter_count} candidates below min_score={min_score}")

    return indexed_scores


# No warmup needed for API-based reranker
def warmup() -> None:
    pass
