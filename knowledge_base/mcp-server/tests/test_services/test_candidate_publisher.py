"""Tests for isolated candidate docs + revision-aware RAG publication."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.contracts import (
    ArtifactKind,
    ChangeKind,
    ChangeSet,
    DeterministicImpactFloor,
    PatchCandidate,
    PatchTarget,
    SemanticChange,
    SourceOrigin,
    SourceRevision,
)
from mcp_server.services.living_docs.rag_revisions import InMemoryRevisionedRagStore
from mcp_server.services.rag_bridge import RagBridge


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate(*, revision: str, content: str, base: str | None) -> PatchCandidate:
    source = SourceRevision(
        project_id="project-a",
        origin=SourceOrigin.GIT,
        source_identity="owner/repo",
        revision_id=revision,
        content_sha256=_digest(content),
    )
    changes = ChangeSet(
        source_revision=source,
        base_source_revision_id=None,
        idempotency_key=f"git:{revision}",
        changes=(
            SemanticChange(
                kind=ChangeKind.SEMANTIC,
                logical_path="jobs/payroll.json",
                field_id="db.host",
                before_sha256=_digest("old"),
                after_sha256=_digest(content),
            ),
        ),
    )
    floor = DeterministicImpactFloor(
        source_revision_key=source.key,
        targets=(
            PatchTarget(
                artifact_id="01_ARCHITECTURE_MAP.md",
                section_id="Overview",
                artifact_kind=ArtifactKind.CANONICAL_DOCUMENT,
            ),
        ),
        evidence_ids=("scan:jobs/payroll.json",),
    )
    return PatchCandidate.new(
        change_set=changes,
        impact_floor=floor,
        base_knowledge_revision_id=base,
        knowledge_revision_id=f"knowledge-{revision}",
    )


def _docs(root: Path, body: str, *, extra: bool = False) -> Path:
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text(body, encoding="utf-8")
    if extra:
        (docs / "02_OPERATIONS.md").write_text("obsolete", encoding="utf-8")
    return docs


@pytest.mark.asyncio
async def test_candidate_prepare_is_invisible_until_activation_and_reuses_base(tmp_path: Path) -> None:
    store = FileCandidateStore((tmp_path / "candidate-store").resolve())
    rag_store = InMemoryRevisionedRagStore()
    publisher = CandidatePublisher(store=store, rag=RagBridge(revisioned_rag=rag_store))
    active_output = tmp_path / "active"

    first = _candidate(revision="sha-one", content="old", base=None)
    store.create(first)
    first_docs = _docs(tmp_path / "first", "old docs")
    await publisher.prepare(first, candidate_docs_dir=first_docs, active_docs_dir=active_output / "docs")
    assert await rag_store.retrieve("project-a") == []
    await publisher.activate(
        first,
        candidate_docs_dir=first_docs,
        active_output_dir=active_output,
        active_source_revision_key=first.change_set.source_revision.key,
    )
    assert [item.artifact.content for item in await rag_store.retrieve("project-a")] == ["old docs"]

    second = _candidate(revision="sha-two", content="new", base=first.knowledge_revision_id)
    store.create(second)
    second_docs = _docs(tmp_path / "second", "new docs")
    receipt = await publisher.prepare(
        second,
        candidate_docs_dir=second_docs,
        active_docs_dir=active_output / "docs",
    )
    # Preparation cannot leak a mixture of old/new retrieval results.
    assert [item.artifact.content for item in await rag_store.retrieve("project-a")] == ["old docs"]
    assert receipt.changed_logical_ids == ("canonical/01_ARCHITECTURE_MAP.md",)
    assert receipt.embedded_content_count == 1

    await publisher.activate(
        second,
        candidate_docs_dir=second_docs,
        active_output_dir=active_output,
        active_source_revision_key=second.change_set.source_revision.key,
    )
    assert [item.artifact.content for item in await rag_store.retrieve("project-a")] == ["new docs"]
    assert (active_output / "docs" / "01_ARCHITECTURE_MAP.md").read_text() == "new docs"


@pytest.mark.asyncio
async def test_candidate_prepare_records_targeted_deletions(tmp_path: Path) -> None:
    store = FileCandidateStore((tmp_path / "candidate-store").resolve())
    rag_store = InMemoryRevisionedRagStore()
    publisher = CandidatePublisher(store=store, rag=RagBridge(revisioned_rag=rag_store))
    active_output = tmp_path / "active"
    first = _candidate(revision="base", content="base", base=None)
    store.create(first)
    first_docs = _docs(tmp_path / "first", "base docs", extra=True)
    await publisher.prepare(first, candidate_docs_dir=first_docs, active_docs_dir=active_output / "docs")
    await publisher.activate(first, candidate_docs_dir=first_docs, active_output_dir=active_output, active_source_revision_key=first.change_set.source_revision.key)

    second = _candidate(revision="removed", content="changed", base=first.knowledge_revision_id)
    store.create(second)
    second_docs = _docs(tmp_path / "second", "changed docs")
    receipt = await publisher.prepare(second, candidate_docs_dir=second_docs, active_docs_dir=active_output / "docs")

    assert receipt.deleted_logical_ids == ("canonical/02_OPERATIONS.md",)
