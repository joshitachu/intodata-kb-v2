"""Durable active-revision authority tests (no vector backend required)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.retrieval.knowledge_revisions import (
    KnowledgeRevisionConflictError,
    activate_knowledge_revision,
    active_knowledge_revisions,
    prepare_knowledge_revision,
)
from src.retrieval.vector_store import clone_revision_artifact_points


async def test_prepared_revision_is_inactive_until_atomic_activation(db_session) -> None:
    await prepare_knowledge_revision(
        db_session,
        project_id="project-a",
        knowledge_revision_id="R1",
        base_knowledge_revision_id=None,
        membership_hashes={"01_OVERVIEW.md": "a" * 64},
        deleted_logical_ids=[],
        aliases={},
    )
    await db_session.commit()

    assert await active_knowledge_revisions(db_session, ["project-a"]) == {}
    row, previous = await activate_knowledge_revision(
        db_session, project_id="project-a", knowledge_revision_id="R1"
    )
    await db_session.commit()
    assert (row.state, previous) == ("active", None)
    assert await active_knowledge_revisions(db_session, ["project-a"]) == {"project-a": "R1"}


async def test_stale_or_conflicting_prepare_cannot_replace_active_revision(db_session) -> None:
    first = await prepare_knowledge_revision(
        db_session,
        project_id="project-a",
        knowledge_revision_id="R1",
        base_knowledge_revision_id=None,
        membership_hashes={"01.md": "a" * 64},
        deleted_logical_ids=[],
        aliases={},
    )
    await activate_knowledge_revision(db_session, project_id="project-a", knowledge_revision_id=first.knowledge_revision_id)
    await db_session.commit()

    with pytest.raises(KnowledgeRevisionConflictError, match="stale"):
        await prepare_knowledge_revision(
            db_session,
            project_id="project-a",
            knowledge_revision_id="stale",
            base_knowledge_revision_id=None,
            membership_hashes={"01.md": "b" * 64},
            deleted_logical_ids=[],
            aliases={},
        )

    prepared = await prepare_knowledge_revision(
        db_session,
        project_id="project-a",
        knowledge_revision_id="R2",
        base_knowledge_revision_id="R1",
        membership_hashes={"01.md": "b" * 64},
        deleted_logical_ids=[],
        aliases={},
    )
    replay = await prepare_knowledge_revision(
        db_session,
        project_id="project-a",
        knowledge_revision_id="R2",
        base_knowledge_revision_id="R1",
        membership_hashes={"01.md": "b" * 64},
        deleted_logical_ids=[],
        aliases={},
    )
    assert replay.id == prepared.id

    with pytest.raises(KnowledgeRevisionConflictError, match="conflicts"):
        await prepare_knowledge_revision(
            db_session,
            project_id="project-a",
            knowledge_revision_id="R2",
            base_knowledge_revision_id="R1",
            membership_hashes={"01.md": "c" * 64},
            deleted_logical_ids=[],
            aliases={},
        )


async def test_unchanged_r1_artifact_is_cloned_under_r2_without_embedding() -> None:
    client = AsyncMock()
    source_point = MagicMock()
    source_point.id = "source-point"
    source_point.vector = {"dense": [0.1, 0.2]}
    source_point.payload = {
        "project_id": "project-a",
        "artifact_id": "02_UNCHANGED.md",
        "knowledge_revision_id": "R1",
        "filename": "project-a/02_UNCHANGED.md",
    }
    client.scroll.return_value = ([source_point], None)

    copied = await clone_revision_artifact_points(
        client,
        project_id="project-a",
        source_revision_id="R1",
        target_revision_id="R2",
        artifact_id="02_UNCHANGED.md",
    )

    assert copied == 1
    clone = client.upsert.await_args.kwargs["points"][0]
    assert clone.payload["knowledge_revision_id"] == "R2"
    assert clone.payload["artifact_id"] == "02_UNCHANGED.md"
    assert clone.vector == source_point.vector
