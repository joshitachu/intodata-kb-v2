from src.retrieval.sparse_embedder import (
    SparseVector,
    embed_text_sparse,
    embed_texts_sparse,
)


def test_embed_text_sparse_returns_sparse_vector():
    result = embed_text_sparse("machine learning algorithms for classification")

    assert isinstance(result, SparseVector)
    assert len(result.indices) > 0
    assert len(result.values) > 0
    assert len(result.indices) == len(result.values)
    # All values should be positive (TF-IDF weights)
    assert all(v > 0 for v in result.values)


def test_embed_texts_sparse_returns_list():
    results = embed_texts_sparse(["text about security", "text about HR policies"])

    assert len(results) == 2
    assert isinstance(results[0], SparseVector)
    assert isinstance(results[1], SparseVector)
    assert len(results[0].indices) > 0
    assert len(results[1].indices) > 0


def test_embed_texts_sparse_empty_list():
    results = embed_texts_sparse([])
    assert results == []


def test_sparse_vector_dataclass():
    sv = SparseVector(indices=[1, 2, 3], values=[0.1, 0.2, 0.3])
    assert sv.indices == [1, 2, 3]
    assert sv.values == [0.1, 0.2, 0.3]


def test_different_texts_produce_different_vectors():
    v1 = embed_text_sparse("machine learning algorithms")
    v2 = embed_text_sparse("human resources department policies")
    # Different texts should have different sparse representations
    assert v1.indices != v2.indices or v1.values != v2.values
