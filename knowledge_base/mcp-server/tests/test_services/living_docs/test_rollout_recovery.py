from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mcp_server.services.living_docs.audit import FilePatchAuditStore, PatchAuditReceipt, PatchUsage
from mcp_server.services.living_docs.baseline import prime_project_baseline
from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
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
)
from mcp_server.services.living_docs.rag_revisions import InMemoryRevisionedRagStore
from mcp_server.services.living_docs.recovery import CandidateRecoveryCoordinator
from mcp_server.services.living_docs.rollout import (
    RolloutStage,
    activation_decision,
    inspect_project_patch_readiness,
)
from mcp_server.services.rag_bridge import RagBridge


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate(revision: str, base: str | None) -> PatchCandidate:
    source = SourceRevision(
        project_id="project-a",
        origin=SourceOrigin.GIT,
        source_identity="owner/repo",
        revision_id=revision,
        content_sha256=_hash(revision),
    )
    changes = ChangeSet(
        source_revision=source,
        base_source_revision_id=None,
        idempotency_key=f"git:{revision}",
        changes=(SemanticChange(ChangeKind.SEMANTIC, "scan/a.json", "a", _hash("x"), _hash(revision)),),
    )
    return PatchCandidate.new(
        change_set=changes,
        impact_floor=DeterministicImpactFloor(
            source_revision_key=source.key,
            targets=(PatchTarget("01_ARCHITECTURE.md", "overview", ArtifactKind.CANONICAL_DOCUMENT),),
            evidence_ids=("scan:a",),
        ),
        base_knowledge_revision_id=base,
        knowledge_revision_id=f"kr-{revision}",
    )


def _workspace_docs(store: FileCandidateStore, candidate: PatchCandidate, body: str) -> Path:
    docs = store.candidate_root(candidate.id) / "workspace" / "output" / "docs"
    docs.mkdir(parents=True)
    (docs / "01_ARCHITECTURE.md").write_text(body, encoding="utf-8")
    return docs


def _ready_output(root: Path) -> Path:
    output = root / "output"
    support = output / "docs" / "_rag_support" / "phase2_analysis"
    cache = output / "analysis_cache"
    support.mkdir(parents=True)
    cache.mkdir(parents=True)
    (output / "docs" / "01_ARCHITECTURE.md").write_text("docs", encoding="utf-8")
    (support / "a.md").write_text("analysis", encoding="utf-8")
    (cache / "a.md").write_text("analysis", encoding="utf-8")
    (support.parent / "_doc_pack_manifest.json").write_text("{}", encoding="utf-8")
    return output


@pytest.mark.asyncio
async def test_automatic_recovery_requires_activation_intent(tmp_path):
    from mcp_server.services.living_docs.git_recovery import recover_git_activations
    from mcp_server.services.living_docs.run_journal import atomic_json

    store = FileCandidateStore((tmp_path / "store").resolve())
    rag = RagBridge(revisioned_rag=InMemoryRevisionedRagStore())
    publisher = CandidatePublisher(store=store, rag=rag)
    candidate = store.create(_candidate("pending", None))
    docs = _workspace_docs(store, candidate, "# Recovered docs")
    output = tmp_path / "active"
    await publisher.prepare(candidate, candidate_docs_dir=docs, active_docs_dir=output / "docs")
    # Prepared alone is not authorization: approval-only drafts stay private.
    assert await recover_git_activations(store=store, rag=rag, output=output, source_baseline=None) == []
    assert not (output / "docs").exists()
    # Simulate a crash after RAG activation but before the document swap.
    atomic_json(store.candidate_root(candidate.id) / "activation_intent.json", {"candidate_id": candidate.id})
    await rag.activate_revision("project-a", candidate.knowledge_revision_id)
    receipts = await recover_git_activations(store=store, rag=rag, output=output, source_baseline=None)
    assert receipts[0]["after"] == "pending"
    assert (output / "docs/01_ARCHITECTURE.md").read_text() == "# Recovered docs"
    assert store.status(candidate.id).state is CandidateState.ACTIVE


def test_readiness_and_staged_activation_policy(tmp_path: Path) -> None:
    output = _ready_output(tmp_path)
    readiness = inspect_project_patch_readiness(
        project_id="project-a",
        output_dir=output,
        feature_enabled=True,
        stage=RolloutStage.AUTO_LOW_RISK,
        source_revision_id="sha-1",
        active_knowledge_revision_id="kr-1",
    )
    assert readiness.ready
    assert activation_decision(
        readiness=readiness,
        approved=False,
        deterministic=True,
        changed_artifact_count=2,
    ).allowed
    broad = activation_decision(
        readiness=readiness,
        approved=False,
        deterministic=False,
        changed_artifact_count=4,
    )
    assert not broad.allowed
    assert broad.reason_code == "human_approval_required"

    unprimed = inspect_project_patch_readiness(
        project_id="project-b",
        output_dir=tmp_path / "absent",
        feature_enabled=True,
        stage=RolloutStage.AUTO_SCOPED,
        source_revision_id=None,
        active_knowledge_revision_id=None,
    )
    assert not unprimed.ready
    assert "source_baseline_missing" in unprimed.reason_codes
    assert "knowledge_revision_baseline_missing" in unprimed.reason_codes


def test_audit_receipts_are_append_only_and_queryable(tmp_path: Path) -> None:
    store = FilePatchAuditStore((tmp_path / "audit").resolve())
    store.append(PatchAuditReceipt(
        candidate_id="candidate-1",
        project_id="project-a",
        source_revision_id="sha-1",
        knowledge_revision_id="kr-1",
        state="active",
        changed_artifacts=("canonical/a.md",),
        embedded_content_count=1,
        reused_content_count=9,
        deterministic_scope=True,
        usage=PatchUsage(provider_calls=0),
    ))
    store.append(PatchAuditReceipt(
        candidate_id="candidate-1",
        project_id="project-a",
        source_revision_id="sha-1",
        knowledge_revision_id="kr-1",
        state="active",
        recovery_action="already_active",
    ))
    assert len(store.list_project("project-a")) == 2
    assert store.latest_candidate("project-a", "candidate-1")["recovery_action"] == "already_active"


@pytest.mark.asyncio
async def test_baseline_priming_is_idempotent_and_does_not_generate(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "01_ARCHITECTURE.md").write_text("existing truth", encoding="utf-8")
    rag_store = InMemoryRevisionedRagStore()
    bridge = RagBridge(revisioned_rag=rag_store)

    first = await prime_project_baseline(
        project_id="project-a",
        source_revision_id="sha-1",
        docs_dir=docs,
        rag=bridge,
    )
    replay = await prime_project_baseline(
        project_id="project-a",
        source_revision_id="sha-1",
        docs_dir=docs,
        rag=bridge,
    )
    assert first.embedded_content_count == 1
    assert replay.already_active
    assert replay.embedded_content_count == 0


@pytest.mark.asyncio
async def test_reconcile_finishes_docs_after_rag_activation_without_repatch(tmp_path: Path) -> None:
    store = FileCandidateStore((tmp_path / "candidates").resolve())
    rag_store = InMemoryRevisionedRagStore()
    bridge = RagBridge(revisioned_rag=rag_store)
    publisher = CandidatePublisher(store=store, rag=bridge)
    recovery = CandidateRecoveryCoordinator(store=store, rag=bridge, publisher=publisher)
    candidate = store.create(_candidate("one", None))
    docs = _workspace_docs(store, candidate, "new docs")
    active_output = tmp_path / "active"
    (active_output / "docs").mkdir(parents=True)
    (active_output / "docs" / "01_ARCHITECTURE.md").write_text("old docs", encoding="utf-8")

    await publisher.prepare(candidate, candidate_docs_dir=docs, active_docs_dir=active_output / "docs")
    await bridge.activate_revision("project-a", candidate.knowledge_revision_id)

    receipt = await recovery.reconcile(
        candidate.id,
        active_output_dir=active_output,
        active_source_revision_key=candidate.change_set.source_revision.key,
    )
    assert receipt.action == "completed_docs_activation"
    assert store.status(candidate.id).state is CandidateState.ACTIVE
    assert (active_output / "docs" / "01_ARCHITECTURE.md").read_text() == "new docs"


@pytest.mark.asyncio
async def test_docs_swap_failure_remains_recoverable(tmp_path: Path, monkeypatch) -> None:
    store = FileCandidateStore((tmp_path / "candidates").resolve())
    rag_store = InMemoryRevisionedRagStore()
    bridge = RagBridge(revisioned_rag=rag_store)
    publisher = CandidatePublisher(store=store, rag=bridge)
    candidate = store.create(_candidate("interrupted", None))
    docs = _workspace_docs(store, candidate, "candidate docs")
    active_output = tmp_path / "active"
    (active_output / "docs").mkdir(parents=True)
    (active_output / "docs" / "01_ARCHITECTURE.md").write_text("old docs", encoding="utf-8")
    await publisher.prepare(candidate, candidate_docs_dir=docs, active_docs_dir=active_output / "docs")

    def _interrupt(_: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr("mcp_server.services.living_docs.candidate_publisher.swap_docs", _interrupt)
    with pytest.raises(Exception, match="recovery required"):
        await publisher.activate(
            candidate,
            candidate_docs_dir=docs,
            active_output_dir=active_output,
            active_source_revision_key=candidate.change_set.source_revision.key,
        )
    status = store.status(candidate.id)
    assert status.state is CandidateState.RECOVERY_REQUIRED
    assert status.reason_code == "docs_activation_recovery_required"


@pytest.mark.asyncio
async def test_rollback_restores_retained_docs_and_rag_without_embeddings(tmp_path: Path) -> None:
    store = FileCandidateStore((tmp_path / "candidates").resolve())
    rag_store = InMemoryRevisionedRagStore()
    bridge = RagBridge(revisioned_rag=rag_store)
    publisher = CandidatePublisher(store=store, rag=bridge)
    recovery = CandidateRecoveryCoordinator(store=store, rag=bridge, publisher=publisher)
    active_output = tmp_path / "active"

    first = store.create(_candidate("one", None))
    first_docs = _workspace_docs(store, first, "revision one")
    await publisher.prepare(first, candidate_docs_dir=first_docs, active_docs_dir=active_output / "docs")
    await publisher.activate(first, candidate_docs_dir=first_docs, active_output_dir=active_output, active_source_revision_key=first.change_set.source_revision.key)

    second = store.create(_candidate("two", first.knowledge_revision_id))
    second_docs = _workspace_docs(store, second, "revision two")
    await publisher.prepare(second, candidate_docs_dir=second_docs, active_docs_dir=active_output / "docs")
    await publisher.activate(second, candidate_docs_dir=second_docs, active_output_dir=active_output, active_source_revision_key=second.change_set.source_revision.key)
    embedded_before_rollback = len(rag_store._content_blobs)

    receipt = await recovery.rollback(
        first,
        expected_active_revision_id=second.knowledge_revision_id,
        active_output_dir=active_output,
    )
    assert receipt.action == "rolled_back"
    assert await bridge.active_knowledge_revision("project-a") == first.knowledge_revision_id
    assert (active_output / "docs" / "01_ARCHITECTURE.md").read_text() == "revision one"
    assert len(rag_store._content_blobs) == embedded_before_rollback
