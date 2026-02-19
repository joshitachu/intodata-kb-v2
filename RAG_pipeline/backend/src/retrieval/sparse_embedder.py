"""Sparse (TF-IDF) embedding for hybrid search.

Uses sklearn TfidfVectorizer to generate sparse vectors for BM25-like
keyword matching. The vectorizer is fitted on the query text itself
(single-document mode), producing term frequency vectors that capture
keyword importance.

For production, this could be replaced with fastembed's Qdrant/bm25 model
when the Rust toolchain is available.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)

# Shared vectorizer instance — fitted on each batch
# This provides a simple sparse representation for hybrid search
_vectorizer = TfidfVectorizer(
    max_features=30000,
    sublinear_tf=True,
    stop_words="english",
)


@dataclass
class SparseVector:
    """Sparse vector representation with indices and values."""
    indices: list[int]
    values: list[float]


def embed_text_sparse(text: str) -> SparseVector:
    """Generate a sparse TF-IDF vector for a single text.

    Fits a TF-IDF vectorizer on the text and extracts non-zero entries.
    """
    vectorizer = TfidfVectorizer(
        max_features=30000,
        sublinear_tf=True,
        stop_words="english",
    )
    tfidf_matrix = vectorizer.fit_transform([text])
    sparse = tfidf_matrix[0]
    indices = sparse.indices.tolist()
    values = sparse.data.tolist()

    return SparseVector(indices=indices, values=values)


def embed_texts_sparse(texts: list[str]) -> list[SparseVector]:
    """Generate sparse TF-IDF vectors for multiple texts.

    Fits a shared TF-IDF vectorizer on all texts and extracts
    non-zero entries for each.
    """
    if not texts:
        return []

    vectorizer = TfidfVectorizer(
        max_features=30000,
        sublinear_tf=True,
        stop_words="english",
    )
    tfidf_matrix = vectorizer.fit_transform(texts)

    results: list[SparseVector] = []
    for i in range(tfidf_matrix.shape[0]):
        row = tfidf_matrix[i]
        results.append(
            SparseVector(
                indices=row.indices.tolist(),
                values=row.data.tolist(),
            )
        )

    logger.info(f"Generated {len(results)} sparse embeddings (vocab size: {len(vectorizer.vocabulary_)})")
    return results
