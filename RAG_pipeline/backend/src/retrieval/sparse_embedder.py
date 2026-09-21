"""Hash-based sparse embedding for hybrid search.

Each token deterministically maps to a fixed index via a CONTENT hash of the
token. This eliminates the TF-IDF vocabulary mismatch bug where a vectorizer
fitted on ingestion documents produces different indices than one fitted on a
query.

The hash must be stable across processes, hosts and releases, because the two
sides of hybrid search are computed in different processes at different times:
ingestion (``src/ingestion/pipeline.py``) writes the sparse vector into Qdrant,
and query time (``src/retrieval/search.py``) builds the query vector that has to
land on the same indices. An earlier version used the builtin ``hash()``, which
Python salts per process unless ``PYTHONHASHSEED`` is set -- neither the backend
Dockerfile nor docker-compose set it, so ``hash('workorder') % 30000`` measured
255 / 196 / 10712 across three host processes and 29571 / 19031 inside the
backend container. Ingestion and query therefore only agreed within one process
lifetime, and every restart silently invalidated the entire sparse index.

``blake2b`` is used rather than the builtin hash for exactly that reason: it is
a stdlib content hash with no salt, so the mapping is a pure function of the
token bytes. No new dependency; still pure Python; still works on Windows.

Pure Python, no new dependencies, works on Windows.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SPARSE_DIM = 30_000

#: Bumped whenever the token -> index mapping changes. Any change here makes
#: every sparse vector already stored in Qdrant stale, and the stored vectors
#: have to be rebuilt (``python -m src.retrieval.reindex_sparse``) before hybrid
#: search is trustworthy again. Recorded as a constant so the backfill tool and
#: an operator can both name the version they are on.
SPARSE_HASH_VERSION = 2

#: Width of the blake2b digest consumed for the index, in bytes. 8 bytes gives a
#: 64-bit value, which is far above SPARSE_DIM so the modulo bias is negligible,
#: and it is cheaper to compute than the full 64-byte digest.
_HASH_DIGEST_BYTES = 8

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
    """Stable index for a token: same token → same index in every process.

    Content-addressed on purpose. The builtin ``hash()`` is randomised per
    process (PEP 456), so it cannot be used here: the index written at ingestion
    time and the index looked up at query time are produced by different
    processes and must agree. See the module docstring for the measured failure.
    """

    digest = hashlib.blake2b(
        token.encode("utf-8"), digest_size=_HASH_DIGEST_BYTES
    ).digest()
    return int.from_bytes(digest, "big") % SPARSE_DIM


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
