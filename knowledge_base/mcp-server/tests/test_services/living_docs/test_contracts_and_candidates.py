from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mcp_server.services.living_docs.candidate_store import CandidateConflict, FileCandidateStore
from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    CandidateState,
    ChangeKind,
    ChangeSet,
    DeterministicImpactFloor,
    PatchCandidate,
    PatchTarget,
    SemanticChange,
    SourceOrigin,
    SourceRevision,
    content_sha256,
)


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def _candidate(*, origin: SourceOrigin = SourceOrigin.GIT, key: str = "push:one", payload: bytes = b"after") -> PatchCandidate:
    source = SourceRevision(
        project_id="project-a",
        origin=origin,
        source_identity="github:org/repository" if origin is SourceOrigin.GIT else "upload:source",
        revision_id="commit-2" if origin is SourceOrigin.GIT else "revision-2",
        content_sha256=content_sha256(payload),
        observed_at=NOW,
    )
    change_set = ChangeSet(
        source_revision=source,
        base_source_revision_id="commit-1",
        idempotency_key=key,
        changes=(
            SemanticChange(
                kind=ChangeKind.MODIFIED,
                logical_path="jobs/customer.json",
                field_id="timeout",
                before_sha256=content_sha256(b"before"),
                after_sha256=content_sha256(payload),
            ),
        ),
    )
    return PatchCandidate.new(
        change_set=change_set,
        impact_floor=DeterministicImpactFloor(
            source_revision_key=source.key,
            targets=(PatchTarget("docs/customer.md", "timeout", ArtifactKind.CANONICAL_DOCUMENT),),
            evidence_ids=("evidence:timeout",),
        ),
        base_knowledge_revision_id="knowledge-r1",
        knowledge_revision_id="knowledge-r2",
    )


def test_source_neutral_candidates_accept_git_zip_and_chat_origins():
    origins = (SourceOrigin.GIT, SourceOrigin.ZIP, SourceOrigin.CHAT)
    candidates = tuple(_candidate(origin=origin, key=f"event:{origin}") for origin in origins)

    assert {candidate.change_set.source_revision.origin for candidate in candidates} == set(origins)
    assert len({candidate.id for candidate in candidates}) == 3
    assert all(candidate.impact_floor.source_revision_key == candidate.change_set.source_revision.key for candidate in candidates)


def test_store_idempotency_conflicts_when_same_key_has_different_candidate_bytes(tmp_path):
    store = FileCandidateStore(tmp_path / "candidate-store")
    first = _candidate(key="push:1", payload=b"first")
    same = _candidate(key="push:1", payload=b"first")
    conflicting = _candidate(key="push:1", payload=b"second")

    assert store.create(first) == first
    assert store.create(same) == first
    with pytest.raises(CandidateConflict, match="idempotency key"):
        store.create(conflicting)


def test_candidate_journal_cas_recovery_and_stale_activation_guard(tmp_path):
    store = FileCandidateStore(tmp_path / "candidate-store")
    candidate = store.create(_candidate())

    preparing = store.transition(candidate.id, expected_version=0, target=CandidateState.PREPARING, now=NOW)
    validating = store.transition(candidate.id, expected_version=preparing.version, target=CandidateState.VALIDATING, now=NOW)
    rag_prepared = store.transition(candidate.id, expected_version=validating.version, target=CandidateState.RAG_PREPARED, now=NOW)
    with pytest.raises(CandidateConflict, match="source revision is stale"):
        store.transition(
            candidate.id,
            expected_version=rag_prepared.version,
            target=CandidateState.ACTIVE,
            active_source_revision_key="0" * 64,
            active_knowledge_revision_id="knowledge-r1",
        )
    with pytest.raises(CandidateConflict, match="compare-and-swap"):
        store.transition(candidate.id, expected_version=0, target=CandidateState.HELD)

    recovered = FileCandidateStore(tmp_path / "candidate-store")
    assert recovered.status(candidate.id).state is CandidateState.RAG_PREPARED
    active = recovered.transition(
        candidate.id,
        expected_version=rag_prepared.version,
        target=CandidateState.ACTIVE,
        active_source_revision_key=candidate.change_set.source_revision.key,
        active_knowledge_revision_id="knowledge-r1",
        now=NOW,
    )
    assert active.state is CandidateState.ACTIVE
    assert [entry.version for entry in recovered.journal(candidate.id)] == [0, 1, 2, 3, 4]


def test_candidate_artifacts_are_content_addressed_write_once_and_never_touch_active_tree(tmp_path):
    active = tmp_path / "active-docs"
    active.mkdir()
    active_doc = active / "customer.md"
    active_doc.write_bytes(b"old, active bytes")
    store = FileCandidateStore(tmp_path / "candidate-store")
    candidate = store.create(_candidate())

    member = store.write_artifact(
        candidate.id,
        logical_id="docs/customer.md",
        artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
        media_type="text/markdown",
        content=b"candidate bytes",
    )
    assert store.write_artifact(
        candidate.id,
        logical_id="docs/customer.md",
        artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
        media_type="text/markdown",
        content=b"candidate bytes",
    ) == member
    assert active_doc.read_bytes() == b"old, active bytes"
    assert (store.candidate_root(candidate.id) / "artifacts" / member.content_sha256[:2] / member.content_sha256).read_bytes() == b"candidate bytes"
    with pytest.raises(CandidateConflict, match="logical id is immutable"):
        store.write_artifact(
            candidate.id,
            logical_id="docs/customer.md",
            artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
            media_type="text/markdown",
            content=b"different candidate bytes",
        )


def test_status_detail_is_read_only_and_candidate_artifact_ids_cannot_escape(tmp_path):
    store = FileCandidateStore(tmp_path / "candidate-store")
    candidate = store.create(_candidate())
    before = store.status(candidate.id)

    detail = store.detail(candidate.id)

    assert detail["state"] == "queued"
    assert store.status(candidate.id) == before
    with pytest.raises(ValueError, match="relative logical path"):
        store.write_artifact(
            candidate.id,
            logical_id="../outside.md",
            artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
            media_type="text/markdown",
            content=b"nope",
        )
