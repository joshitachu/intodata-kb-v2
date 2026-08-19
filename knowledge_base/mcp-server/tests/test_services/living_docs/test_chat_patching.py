from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.services.evidence_doc_update import EvidenceDocUpdateError
from mcp_server.services.living_docs.candidate_store import FileCandidateStore
from mcp_server.services.living_docs.chat_patching import (
    ChatPatchService,
    FileChatProposalStore,
    mark_stale_editorial_overlays,
)
from mcp_server.services.living_docs.rag_revisions import InMemoryRevisionedRagStore
from mcp_server.services.rag_bridge import RagBridge


@pytest.fixture(autouse=True)
def _isolated_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "project_root", tmp_path / "project-root")


def _service(tmp_path: Path, *, now=None):
    rag_store = InMemoryRevisionedRagStore()
    service = ChatPatchService(
        proposal_store=FileChatProposalStore(tmp_path / "proposals"),
        candidate_store=FileCandidateStore(tmp_path / "candidate-store"),
        rag=RagBridge(revisioned_rag=rag_store),
        now=now,
    )
    return service, rag_store


def _doc(project_id: str = "p1") -> Path:
    docs = config.project_root / "output" / project_id / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    path = docs / "04_RUNBOOK.md"
    path.write_text("# Runbook\n\n## Recovery\n\nOld procedure.\n\n## Owner\n\nOps.\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_preview_is_side_effect_free_and_source_backed_activation_is_revisioned(tmp_path: Path) -> None:
    path = _doc()
    before = path.read_bytes()
    service, rag = _service(tmp_path)

    proposal = await service.preview(
        project_id="p1", filename=path.name, section="Recovery",
        replacement_text="New evidence-backed procedure.", evidence_refs=["scan:17"],
        assertion_kind="source_backed", requested_by_user_id=7,
    )

    assert path.read_bytes() == before
    assert "-Old procedure." in proposal.compact_diff
    assert "+New evidence-backed procedure." in proposal.compact_diff
    assert await rag.active_revision("p1") is None

    result = await service.activate(
        proposal_id=proposal.proposal_id,
        expected_patch_plan_hash=proposal.patch_plan_hash,
        approver_user_id=7,
        editorial_approval_authorized=False,
    )

    assert "New evidence-backed procedure." in path.read_text(encoding="utf-8")
    assert "Old procedure." not in path.read_text(encoding="utf-8")
    assert await rag.active_revision("p1") == result.knowledge_revision_id
    retrieved = await rag.retrieve("p1")
    canonical = next(item for item in retrieved if item.artifact.logical_id == "canonical/04_RUNBOOK.md")
    assert canonical.knowledge_revision_id == result.knowledge_revision_id


@pytest.mark.asyncio
async def test_editorial_assertion_requires_owner_and_is_attributed_overlay(tmp_path: Path) -> None:
    path = _doc()
    service, rag = _service(tmp_path)
    proposal = await service.preview(
        project_id="p1", filename=path.name, section="Recovery",
        replacement_text="The owner says to use a manual fallback.", evidence_refs=["chat:message:42"],
        assertion_kind="editorial", requested_by_user_id=8,
    )

    with pytest.raises(EvidenceDocUpdateError, match="project owner"):
        await service.activate(
            proposal_id=proposal.proposal_id,
            expected_patch_plan_hash=proposal.patch_plan_hash,
            approver_user_id=9,
            editorial_approval_authorized=False,
        )
    assert "manual fallback" not in path.read_text(encoding="utf-8")
    assert await rag.active_revision("p1") is None

    result = await service.activate(
        proposal_id=proposal.proposal_id,
        expected_patch_plan_hash=proposal.patch_plan_hash,
        approver_user_id=9,
        editorial_approval_authorized=True,
    )
    assert path.read_bytes().startswith(b"# Runbook")
    overlays = list((path.parent / "_rag_support/editorial_overlays").glob("*.md"))
    assert len(overlays) == 1
    content = overlays[0].read_text(encoding="utf-8")
    assert "author_user_id: 8" in content
    assert "approver_user_id: 9" in content
    assert any("editorial_overlays" in item for item in result.changed_logical_ids)


@pytest.mark.asyncio
async def test_hash_stale_expired_and_replayed_approvals_fail_closed(tmp_path: Path) -> None:
    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    path = _doc()
    clock = [now]
    service, _ = _service(tmp_path, now=lambda: clock[0])
    proposal = await service.preview(
        project_id="p1", filename=path.name, section="Recovery",
        replacement_text="New procedure.", evidence_refs=["scan:1"],
        assertion_kind="source_backed", requested_by_user_id=1,
    )
    with pytest.raises(EvidenceDocUpdateError, match="does not match"):
        await service.activate(
            proposal_id=proposal.proposal_id, expected_patch_plan_hash="f" * 64,
            approver_user_id=1, editorial_approval_authorized=False,
        )
    clock[0] = now + timedelta(hours=1)
    with pytest.raises(EvidenceDocUpdateError, match="expired"):
        await service.activate(
            proposal_id=proposal.proposal_id, expected_patch_plan_hash=proposal.patch_plan_hash,
            approver_user_id=1, editorial_approval_authorized=False,
        )

    clock[0] = now
    second = await service.preview(
        project_id="p1", filename=path.name, section="Recovery",
        replacement_text="Another procedure.", evidence_refs=["scan:2"],
        assertion_kind="source_backed", requested_by_user_id=1,
    )
    path.write_text(path.read_text(encoding="utf-8") + "changed", encoding="utf-8")
    with pytest.raises(EvidenceDocUpdateError, match="changed after"):
        await service.activate(
            proposal_id=second.proposal_id, expected_patch_plan_hash=second.patch_plan_hash,
            approver_user_id=1, editorial_approval_authorized=False,
        )


def test_overlay_becomes_stale_when_base_document_changes(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    overlays = docs / "_rag_support/editorial_overlays"
    overlays.mkdir(parents=True)
    doc = docs / "04_RUNBOOK.md"
    doc.write_text("current", encoding="utf-8")
    overlay = overlays / "p.md"
    overlay.write_text(
        "---\ntarget_document: 04_RUNBOOK.md\nbase_document_sha256: " + "a" * 64 +
        "\nstatus: active\n---\n\nAssertion\n", encoding="utf-8",
    )
    assert mark_stale_editorial_overlays(docs) == 1
    assert "status: stale" in overlay.read_text(encoding="utf-8")
