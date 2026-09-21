"""Voyage AI reranking for improved retrieval precision.

Two operator-visibility rules live here, and both exist because a reranker that
is not running looks exactly like one that is:

* A configuration where ``reranker_enabled`` is true and no API key is set is
  refused at startup (:func:`warmup`). It is not a degraded mode, it is a
  contradiction: every call would fail with 401 and fall back to raw RRF order,
  so the deployment would run permanently un-reranked with nothing in the logs
  to say so. An operator who genuinely wants no reranking sets
  ``RERANKER_ENABLED=false``, which makes that state explicit and intentional.
* A per-call failure is logged at ERROR and counted on :data:`_HEALTH`. The
  fallback to fusion order is kept -- degrading beats erroring out mid-turn --
  but it stops being invisible.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = [2.0, 5.0, 15.0]  # seconds — tuned for Voyage free tier (3 RPM)

#: Bound on the distinct failure reasons kept on the health record, which lives
#: for the whole process lifetime.
_MAX_TRACKED_REASONS = 10


class RerankerMisconfigured(RuntimeError):
    """``reranker_enabled`` is set without the credential the reranker needs."""


@dataclass(slots=True)
class RerankerHealth:
    """Process-level record of reranker degradation.

    Deliberately process-global rather than turn-scoped. The failure it has to
    make visible is a *standing* one -- a missing key, a revoked key, a provider
    outage -- which shows up as "every call in this process failed", not as one
    unlucky turn. A counter that survives the turn is what lets an operator, a
    health endpoint or a support conversation answer "is this deployment
    actually reranking?" without reading a log stream.

    ``misconfigured`` is set by :func:`warmup` and is the one field that means
    the answer is no and will stay no until the configuration changes.
    """

    misconfigured: bool = False
    failures: int = 0
    consecutive_failures: int = 0
    successes: int = 0
    last_error: str | None = None
    last_failure_monotonic: float | None = None
    #: Reason strings seen at least once, newest last, bounded. Enough to tell a
    #: rate limit apart from an auth failure without keeping an unbounded log.
    recent_reasons: list[str] = field(default_factory=list)

    def record_failure(self, reason: str) -> None:
        self.failures += 1
        self.consecutive_failures += 1
        self.last_error = reason
        self.last_failure_monotonic = time.monotonic()
        if reason not in self.recent_reasons:
            self.recent_reasons.append(reason)
            if len(self.recent_reasons) > _MAX_TRACKED_REASONS:
                del self.recent_reasons[: len(self.recent_reasons) - _MAX_TRACKED_REASONS]

    def record_success(self) -> None:
        self.successes += 1
        self.consecutive_failures = 0

    def as_dict(self) -> dict[str, object]:
        """A JSON-safe projection for diagnostics surfaces."""

        return {
            "misconfigured": self.misconfigured,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "successes": self.successes,
            "last_error": self.last_error,
            "recent_reasons": list(self.recent_reasons),
            # The one derived judgement worth publishing: the reranker is not
            # doing its job, whether because it cannot start or because every
            # call is failing.
            "degraded": self.misconfigured or self.consecutive_failures > 0,
        }


_HEALTH = RerankerHealth()


def reranker_health() -> RerankerHealth:
    """The process's reranker health record."""

    return _HEALTH


def record_rerank_failure(exc: BaseException) -> None:
    """Record one rerank call that fell back to fusion order.

    Called by the retrieval layer at the point where the fallback is taken, so
    the record covers every reason a rerank can be lost -- transport error, auth
    failure, exhausted retries -- not just the ones raised inside this module.
    """

    reason = f"{type(exc).__name__}: {exc}"
    _HEALTH.record_failure(reason)
    logger.error(
        "RERANKER DEGRADED: rerank call failed, falling back to fusion order "
        "(reason=%s, consecutive_failures=%d, total_failures=%d)",
        reason,
        _HEALTH.consecutive_failures,
        _HEALTH.failures,
        exc_info=exc,
    )


async def rerank(
    query: str,
    texts: list[str],
    top_k: int = 5,
) -> list[tuple[int, float]]:
    """Rerank texts using Voyage AI rerank API.

    Returns list of (original_index, score) tuples, sorted by score descending.
    Scores are in 0-1 range. Raises on API failure (caller should fall back and
    call :func:`record_rerank_failure`).
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
    _HEALTH.record_success()

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


def warmup() -> None:
    """Validate the reranker configuration at startup. No model to load.

    Called from the app lifespan, and only when ``reranker_enabled`` is true, so
    reaching here without a key means the deployment asked for reranking and did
    not supply the credential it needs. That combination is refused rather than
    logged, because logging it is exactly what failed before: the blank
    ``VOYAGE_API_KEY`` that ships in `.env.production.template` produced a
    deployment that answered every query from raw fusion order and looked
    entirely healthy doing it.

    The escape hatch is one line of configuration -- ``RERANKER_ENABLED=false``
    -- which is a statement an operator makes on purpose and an auditor can read.
    """

    if not settings.reranker_enabled:
        return
    if not (settings.voyage_api_key or "").strip():
        _HEALTH.misconfigured = True
        message = (
            "RERANKER MISCONFIGURED: reranker_enabled=true but VOYAGE_API_KEY is "
            "empty. Every rerank call would fail and silently fall back to raw "
            "fusion order, so retrieval quality would be degraded with no signal. "
            "Set VOYAGE_API_KEY, or set RERANKER_ENABLED=false to run without "
            "reranking deliberately."
        )
        logger.critical(message)
        raise RerankerMisconfigured(message)
    _HEALTH.misconfigured = False


__all__ = [
    "RerankerHealth",
    "RerankerMisconfigured",
    "record_rerank_failure",
    "rerank",
    "reranker_health",
    "warmup",
]
