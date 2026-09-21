"""Sparse embedding behaviour, including the cross-process stability contract.

The stability tests here are the ones that matter. Hybrid search only works
while the index written at ingestion time and the vector built at query time
land on the same sparse indices, and those two are produced by different
processes -- often on different days. A test that only checks stability *within*
one interpreter cannot see the failure this module was rewritten to fix, because
Python's builtin ``hash()`` is stable inside a process and randomised between
them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from src.retrieval.sparse_embedder import (
    SPARSE_DIM,
    SparseVector,
    embed_text_sparse,
    embed_texts_sparse,
)

#: The backend package root (the directory holding `src/`), so a child
#: interpreter can import `src.retrieval.sparse_embedder` without an install.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_TOKENS = ("workorder", "talend", "koppelvlak", "invoice", "tMap")

#: Run in a child interpreter: emit this module's index for each token, and the
#: builtin hash of the same tokens. The builtin hashes are the control -- if they
#: come back identical across two children the interpreter is not salting, and
#: the stability assertion below would be vacuous.
_CHILD_PROGRAM = textwrap.dedent(
    """
    import json, sys
    sys.path.insert(0, sys.argv[1])
    from src.retrieval.sparse_embedder import _hash_index
    tokens = sys.argv[2:]
    print(json.dumps({
        "stable": [_hash_index(t) for t in tokens],
        "builtin": [hash(t) for t in tokens],
    }))
    """
)


def _child_indexes() -> dict[str, list[int]]:
    """Compute the sparse indexes for `_TOKENS` in a fresh interpreter."""

    env = {**os.environ}
    # Explicitly ask for a randomised salt so the control below is meaningful
    # even if the surrounding environment pinned PYTHONHASHSEED.
    env["PYTHONHASHSEED"] = "random"
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM, str(_BACKEND_ROOT), *_TOKENS],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=str(_BACKEND_ROOT),
    )
    return json.loads(completed.stdout)


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


def test_indexes_stay_inside_the_declared_dimension():
    vector = embed_text_sparse(" ".join(_TOKENS))
    assert vector.indices
    assert all(0 <= index < SPARSE_DIM for index in vector.indices)


def test_token_index_is_stable_across_process_boundaries():
    """Two fresh interpreters must agree, and agree with this one.

    This is the regression guard for the defect that invalidated the whole
    sparse index on every restart: ``hash(token) % SPARSE_DIM`` returned a
    different index in each process, so a query could never match a vector
    written by an earlier one.
    """

    from src.retrieval.sparse_embedder import _hash_index

    first = _child_indexes()
    second = _child_indexes()

    # Control: the builtin hash really is salted between these two children, so
    # the assertion below is testing something.
    assert first["builtin"] != second["builtin"], (
        "PYTHONHASHSEED appears to be pinned in this environment; the "
        "cross-process assertion would pass trivially"
    )

    in_process = [_hash_index(token) for token in _TOKENS]
    assert first["stable"] == second["stable"] == in_process
