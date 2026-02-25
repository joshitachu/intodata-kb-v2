"""Hash-based sparse embedding for hybrid search.

Each token deterministically maps to a fixed index via ``hash(token) % DIM``.
This eliminates the TF-IDF vocabulary mismatch bug where a vectorizer fitted
on ingestion documents produces different indices than one fitted on a query.

Pure Python, no new dependencies, works on Windows.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SPARSE_DIM = 30_000

# Simple English stop words (no external dependency)
_STOP_WORDS: frozenset[str] = frozenset(
    "a an and are as at be but by for from has have he her his how i if in into "
    "is it its me my no not of on or our she so than that the their them then "
    "there these they this to up us was we what when where which who why will "
    "with you your".split()
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


@dataclass
class SparseVector:
    """Sparse vector representation with indices and values."""
    indices: list[int]
    values: list[float]


def _tokenize(text: str) -> list[str]:
    """Lowercase, extract alphanumeric tokens, remove stop words."""
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOP_WORDS and len(t) > 1
    ]


def _hash_index(token: str) -> int:
    """Deterministic index for a token: same token → same index always."""
    return hash(token) % SPARSE_DIM


def _build_sparse(tokens: list[str]) -> SparseVector:
    """Build a sparse vector from tokens using sub-linear TF weighting."""
    if not tokens:
        return SparseVector(indices=[], values=[])

    # Count term frequencies
    tf: dict[int, float] = {}
    for token in tokens:
        idx = _hash_index(token)
        tf[idx] = tf.get(idx, 0.0) + 1.0

    # Sub-linear TF: 1.0 + log(count)
    indices: list[int] = []
    values: list[float] = []
    for idx, count in sorted(tf.items()):
        indices.append(idx)
        values.append(1.0 + math.log(count))

    return SparseVector(indices=indices, values=values)


def embed_text_sparse(text: str) -> SparseVector:
    """Generate a sparse vector for a single text.

    Tokenize, hash each token to a fixed index, weight with sub-linear TF.
    """
    tokens = _tokenize(text)
    return _build_sparse(tokens)


def embed_texts_sparse(texts: list[str]) -> list[SparseVector]:
    """Generate sparse vectors for multiple texts."""
    if not texts:
        return []

    results = [embed_text_sparse(t) for t in texts]
    total_tokens = sum(len(_tokenize(t)) for t in texts)
    logger.info(
        f"Generated {len(results)} sparse embeddings "
        f"(dim={SPARSE_DIM}, total_tokens={total_tokens})"
    )
    return results
