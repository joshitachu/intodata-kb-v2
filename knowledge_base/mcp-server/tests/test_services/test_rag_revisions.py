"""Fake-backed tests for revision-scoped RAG preparation and activation."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from mcp_server.services.living_docs.rag_revisions import (
    InMemoryRevisionedRagStore,
    RagArtifact,
    RagMutation,
    RevisionValidationError,
    StaleRagRevisionError,
)
from mcp_server.services.rag_bridge import HttpRevisionedRagAdapter, RagBridge


def _artifact(
    logical_id: str,
    content: str,
    *,
    layer: str = "canonical_docs",
) -> RagArtifact:
    return RagArtifact(
        logical_id=logical_id,
        content=content,
        source_layer=layer,
        artifact_type="final_doc" if layer == "canonical_docs" else "phase2_analysis",
    )


async def _activate_r1(store: InMemoryRevisionedRagStore) -> None:
    prepared = await store.prepare(
        "project-a",
        "R1",
        [
            RagMutation.upsert(_artifact("01_OVERVIEW.md", "original overview")),
            RagMutation.upsert(_artifact("_internal/orders.md", "original analysis", layer="technical_analysis")),
        ],
        base_knowledge_revision_id=None,
    )
    await store.validate(
        "project-a", "R1", required_artifact_hashes=prepared.membership_hashes
    )
    await store.activate("project-a", "R1")


async def test_preparation_is_invisible_until_the_candidate_activates() -> None:
    store = InMemoryRevisionedRagStore()
    await _activate_r1(store)

    prepared = await store.prepare(
        "project-a",
        "R2",
        [RagMutation.upsert(_artifact("01_OVERVIEW.md", "patched overview"))],
        base_knowledge_revision_id="R1",
    )

    during_prepare = await store.retrieve("project-a")
    assert {item.knowledge_revision_id for item in during_prepare} == {"R1"}
    assert {item.artifact.content for item in during_prepare} == {
        "original overview", "original analysis"
    }
    assert prepared.new_embedding_count == 1
    assert prepared.reused_content_count == 2

    await store.validate(
        "project-a", "R2", required_artifact_hashes=prepared.membership_hashes
    )
    receipt = await store.activate("project-a", "R2")
    assert receipt.previous_knowledge_revision_id == "R1"
    after_activation = await store.retrieve("project-a")
    assert {item.knowledge_revision_id for item in after_activation} == {"R2"}
    assert {item.artifact.content for item in after_activation} == {
        "patched overview", "original analysis"
    }


async def test_validation_requires_expected_canonical_internal_delete_and_alias_membership() -> None:
    store = InMemoryRevisionedRagStore()
    await _activate_r1(store)
    changed_analysis = _artifact("_internal/orders.md", "new analysis", layer="technical_analysis")
    prepared = await store.prepare(
        "project-a",
        "R2",
        [
            RagMutation.upsert(changed_analysis),
            RagMutation.delete("01_OVERVIEW.md"),
            RagMutation.alias("orders", "_internal/orders.md"),
        ],
        base_knowledge_revision_id="R1",
    )

    with pytest.raises(RevisionValidationError):
        await store.validate("project-a", "R2", required_deletes=["missing.md"])

    await store.validate(
        "project-a",
        "R2",
        required_artifact_hashes={"_internal/orders.md": changed_analysis.content_hash},
        required_deletes=["01_OVERVIEW.md"],
        required_aliases={"orders": "_internal/orders.md"},
    )
    await store.activate("project-a", "R2")
    results = await store.retrieve("project-a", source_layers={"technical_analysis"})
    assert [(r.artifact.logical_id, r.knowledge_revision_id) for r in results] == [
        ("_internal/orders.md", "R2")
    ]
    assert prepared.new_embedding_count == 1


async def test_unchanged_content_is_reused_once_per_hash_and_failed_candidate_keeps_r1() -> None:
    store = InMemoryRevisionedRagStore()
    await _activate_r1(store)
    prepared = await store.prepare(
        "project-a",
        "R2",
        [
            RagMutation.upsert(_artifact("02_COPY.md", "original overview")),
            RagMutation.upsert(_artifact("03_NEW.md", "new canonical")),
            RagMutation.upsert(_artifact("_internal/new.md", "new analysis", layer="technical_analysis")),
        ],
        base_knowledge_revision_id="R1",
    )

    assert prepared.new_embedding_count == 2
    assert len(prepared.reused_content_hashes) == 2
    with pytest.raises(RevisionValidationError):
        await store.activate("project-a", "R2")
    assert await store.active_revision("project-a") == "R1"
    assert {r.knowledge_revision_id for r in await store.retrieve("project-a")} == {"R1"}


async def test_stale_candidate_cannot_replace_newer_active_revision() -> None:
    store = InMemoryRevisionedRagStore()
    await _activate_r1(store)
    r2 = await store.prepare(
        "project-a", "R2", [RagMutation.upsert(_artifact("01_OVERVIEW.md", "r2"))],
        base_knowledge_revision_id="R1",
    )
    await store.validate("project-a", "R2", required_artifact_hashes=r2.membership_hashes)
    await store.activate("project-a", "R2")

    # A replay of a R1-based candidate is rejected before it can become active.
    with pytest.raises(StaleRagRevisionError):
        await store.prepare(
            "project-a", "stale", [RagMutation.upsert(_artifact("01_OVERVIEW.md", "stale"))],
            base_knowledge_revision_id="R1",
        )


async def test_rag_bridge_exposes_revision_prepare_validate_activate_port() -> None:
    store = InMemoryRevisionedRagStore()
    bridge = RagBridge(revisioned_rag=store)
    artifact = _artifact("01_OVERVIEW.md", "new overview")

    prepared = await bridge.prepare_revision(
        "project-a",
        "R1",
        [RagMutation.upsert(artifact)],
        base_knowledge_revision_id=None,
    )
    await bridge.validate_revision(
        "project-a", "R1", required_artifact_hashes=prepared.membership_hashes
    )
    await bridge.activate_revision("project-a", "R1")

    assert await bridge.active_knowledge_revision("project-a") == "R1"


async def test_http_adapter_stages_only_changed_artifacts_and_preserves_inherited_membership() -> None:
    bridge = RagBridge()
    bridge.push_markdown = AsyncMock(return_value={"status": "ok"})
    adapter = HttpRevisionedRagAdapter(bridge)
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_request(method: str, path: str, *, payload: dict | None = None) -> dict:
        calls.append((method, path, payload))
        if method == "GET":
            return {
                "membership_hashes": {
                    "01_CHANGED.md": _artifact("01_CHANGED.md", "old").content_hash,
                    "02_UNCHANGED.md": _artifact("02_UNCHANGED.md", "same").content_hash,
                },
                "aliases": {},
                "deleted_logical_ids": [],
            }
        return {"state": "prepared"}

    adapter._request = fake_request  # type: ignore[method-assign]
    changed = _artifact("01_CHANGED.md", "new")
    unchanged = _artifact("02_UNCHANGED.md", "same")
    prepared = await adapter.prepare(
        "project-a",
        "R2",
        [RagMutation.upsert(changed), RagMutation.upsert(unchanged)],
        base_knowledge_revision_id="R1",
    )

    bridge.push_markdown.assert_awaited_once()
    assert bridge.push_markdown.await_args.kwargs["filename"] == "01_CHANGED.md"
    prepare_payload = calls[-1][2]
    assert prepare_payload is not None
    assert prepare_payload["membership_hashes"] == {
        "01_CHANGED.md": changed.content_hash,
        "02_UNCHANGED.md": unchanged.content_hash,
    }
    assert prepared.new_embedding_count == 1
    assert unchanged.content_hash in prepared.reused_content_hashes
