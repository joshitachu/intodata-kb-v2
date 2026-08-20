"""End-to-end local S4 fixture: Git candidate -> patch -> coherent activation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server.services.living_docs.candidate_publisher import CandidatePublisher
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.git_patch_coordinator import GitPatchCoordinator
from mcp_server.services.living_docs.rag_revisions import InMemoryRevisionedRagStore
from mcp_server.services.rag_bridge import RagBridge
from mcp_server.services.true_patch_executor import PatchResult


class _PatchInCandidate:
    async def run(self, **kwargs: object) -> PatchResult:
        output_dir = kwargs["output_dir"]
        assert isinstance(output_dir, Path)
        doc = output_dir / "docs" / "01_ARCHITECTURE_MAP.md"
        doc.write_text("new database host", encoding="utf-8")
        return PatchResult(
            action="true_patch",
            patched_groups=["payments"],
            patched_docs=["01_ARCHITECTURE_MAP.md"],
            summary={"reason_code": "true_patch_succeeded"},
            advance_commit=True,
        )


def _active_output(root: Path) -> Path:
    output = root / "output"
    docs = output / "docs"
    (docs / "_rag_support").mkdir(parents=True)
    (docs / "01_ARCHITECTURE_MAP.md").write_text("old database host", encoding="utf-8")
    (docs / "_rag_support" / "_doc_pack_manifest.json").write_text(
        '{"doc_packs":{"01_ARCHITECTURE_MAP":{"included":{"payments.md":["DATA_FLOWS"]}}}}',
        encoding="utf-8",
    )
    return output


@pytest.mark.asyncio
async def test_git_candidate_patch_never_writes_active_docs_before_revision_activation(tmp_path: Path) -> None:
    active_output = _active_output(tmp_path)
    store = FileCandidateStore((tmp_path / "candidate-store").resolve())
    rag_store = InMemoryRevisionedRagStore()
    coordinator = GitPatchCoordinator(
        store=store,
        publisher=CandidatePublisher(store=store, rag=RagBridge(revisioned_rag=rag_store)),
        executor=_PatchInCandidate(),
    )
    candidate = coordinator.create_candidate(
        project_id="project-a",
        source_identity="owner/repo",
        before_sha="a" * 40,
        after_sha="b" * 40,
        changed_groups=["payments"],
        active_docs_dir=active_output / "docs",
        base_knowledge_revision_id=None,
    )

    outcome = await coordinator.run(
        candidate,
        active_output_dir=active_output,
        candidate_scan_output_dir=tmp_path / "candidate-scan",
        changed_groups=["payments"],
        platform="talend",
        log_mgr=MagicMock(emit=AsyncMock()),
        before_sha="a" * 40,
        after_sha="b" * 40,
        active_source_revision_key=candidate.change_set.source_revision.key,
    )

    assert outcome.action == "active"
    assert outcome.patched_docs == ("01_ARCHITECTURE_MAP.md",)
    assert (active_output / "docs" / "01_ARCHITECTURE_MAP.md").read_text() == "new database host"
    active = await rag_store.retrieve("project-a")
    assert active and active[0].knowledge_revision_id == candidate.knowledge_revision_id
    assert active[0].artifact.content == "new database host"


@pytest.mark.asyncio
async def test_git_candidate_holds_instead_of_falling_back_to_generation(tmp_path: Path) -> None:
    active_output = _active_output(tmp_path)
    store = FileCandidateStore((tmp_path / "candidate-store").resolve())
    rag_store = InMemoryRevisionedRagStore()

    class _RejectedPatch:
        async def run(self, **kwargs: object) -> PatchResult:
            return PatchResult(
                action="scoped_regen_fallback",
                patched_groups=[],
                patched_docs=[],
                summary={},
                advance_commit=False,
                error="invalid patch",
            )

    coordinator = GitPatchCoordinator(
        store=store,
        publisher=CandidatePublisher(store=store, rag=RagBridge(revisioned_rag=rag_store)),
        executor=_RejectedPatch(),
    )
    candidate = coordinator.create_candidate(
        project_id="project-a",
        source_identity="owner/repo",
        before_sha="a" * 40,
        after_sha="c" * 40,
        changed_groups=["payments"],
        active_docs_dir=active_output / "docs",
        base_knowledge_revision_id=None,
    )

    outcome = await coordinator.run(
        candidate,
        active_output_dir=active_output,
        candidate_scan_output_dir=tmp_path / "candidate-scan",
        changed_groups=["payments"],
        platform="talend",
        log_mgr=MagicMock(emit=AsyncMock()),
        before_sha="a" * 40,
        after_sha="c" * 40,
        active_source_revision_key=candidate.change_set.source_revision.key,
    )

    assert outcome.action == "held"
    assert outcome.reason_code == "patch_validation_failed"
    assert (active_output / "docs" / "01_ARCHITECTURE_MAP.md").read_text() == "old database host"
    assert await rag_store.retrieve("project-a") == []
