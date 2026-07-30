from __future__ import annotations

import pytest

from mcp_server.experiments.domain.evaluation import ArtifactIdentity
from mcp_server.experiments.infrastructure.validation.alignment import align_required_documents


def _artifact(branch: str, name: str) -> ArtifactIdentity:
    return ArtifactIdentity(branch, "document", name, "a" * 64, 1)  # type: ignore[arg-type]


def test_aligns_required_documents_by_logical_identity_in_stable_order() -> None:
    alignment = align_required_documents(
        ["b.md", "a.md"],
        [_artifact("baseline", "a.md"), _artifact("baseline", "b.md")],
        [_artifact("candidate", "b.md"), _artifact("candidate", "a.md")],
    )

    assert alignment.required_document_ids == ("a.md", "b.md")
    assert [pair.status for pair in alignment.pairs] == ["aligned", "aligned"]


def test_alignment_keeps_missing_documents_explicit() -> None:
    alignment = align_required_documents(
        ["a.md", "b.md"],
        [_artifact("baseline", "a.md")],
        [_artifact("candidate", "b.md")],
    )

    assert [(pair.document_id, pair.status) for pair in alignment.pairs] == [
        ("a.md", "missing_candidate"),
        ("b.md", "missing_baseline"),
    ]


def test_duplicate_logical_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        align_required_documents(
            ["a.md"],
            [_artifact("baseline", "a.md"), _artifact("baseline", "a.md")],
            [_artifact("candidate", "a.md")],
        )
