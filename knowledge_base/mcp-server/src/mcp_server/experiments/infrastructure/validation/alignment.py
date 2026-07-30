"""Document alignment by immutable logical identity."""

from __future__ import annotations

from collections.abc import Iterable

from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    DocumentAlignment,
    DocumentPair,
)


def _document_index(
    artifacts: Iterable[ArtifactIdentity], *, expected_branch: str
) -> dict[str, ArtifactIdentity]:
    index: dict[str, ArtifactIdentity] = {}
    for artifact in artifacts:
        if artifact.kind != "document":
            continue
        if artifact.branch != expected_branch:
            raise ValueError(f"expected {expected_branch} artifact, got {artifact.branch}")
        if artifact.logical_name in index:
            raise ValueError(f"duplicate document logical identity: {artifact.logical_name}")
        index[artifact.logical_name] = artifact
    return index


def align_required_documents(
    required_document_ids: Iterable[str],
    baseline_artifacts: Iterable[ArtifactIdentity],
    candidate_artifacts: Iterable[ArtifactIdentity],
) -> DocumentAlignment:
    """Align only declared outputs; unknown extras never become implied requirements."""
    required = tuple(sorted(required_document_ids))
    baseline = _document_index(baseline_artifacts, expected_branch="baseline")
    candidate = _document_index(candidate_artifacts, expected_branch="candidate")
    pairs: list[DocumentPair] = []
    for document_id in required:
        baseline_document = baseline.get(document_id)
        candidate_document = candidate.get(document_id)
        if baseline_document is None:
            status = "missing_baseline"
            limitations = ("baseline document is unavailable",)
        elif candidate_document is None:
            status = "missing_candidate"
            limitations = ("candidate document is unavailable",)
        else:
            status = "aligned"
            limitations = ()
        pairs.append(
            DocumentPair(
                document_id=document_id,
                baseline=baseline_document,
                candidate=candidate_document,
                status=status,
                limitations=limitations,
            )
        )
    return DocumentAlignment(required_document_ids=required, pairs=tuple(pairs))
