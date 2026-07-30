from __future__ import annotations

import hashlib

import pytest

from mcp_server.db.models import GenerateRun, Project
from mcp_server.experiments.application.snapshot_sources import SourceSnapshotPersistence
from mcp_server.experiments.domain.sources import (
    RedactionState,
    SourceSnapshot,
    SourceSnapshotContent,
    SourceSnapshotItem,
)
from mcp_server.experiments.infrastructure.persistence.sqlalchemy_repository import (
    SqlAlchemyExperimentRepository,
)


@pytest.mark.asyncio
async def test_repository_indexes_finalized_source_snapshot_once(db_session) -> None:
    project = Project(id="fixture-project", display_name="Fixture", local_path="/fixture")
    run = GenerateRun(project_id=project.id, triggered_by="api", status="completed")
    db_session.add_all((project, run))
    await db_session.flush()
    repository = SqlAlchemyExperimentRepository(db_session)
    experiment = repository.reserve(
        experiment_id="00000000-0000-4000-8000-000000000001",
        project_id=project.id,
        baseline_run_id=run.id,
        idempotency_key="snapshot",
        runtime_id="fake",
        provider_id=None,
        model_id=None,
        settings={},
    )
    await db_session.flush()
    payload = b"approved"
    digest = hashlib.sha256(payload).hexdigest()
    item = SourceSnapshotItem(
        id="b" * 64,
        source_descriptor_id="descriptor-a",
        logical_identity="fixture://item-a",
        source_kind="test",
        source_role="implementation",
        capabilities=frozenset({"searchable_text"}),
        locator="fixture://item-a",
        raw_sha256=digest,
        extracted_sha256=digest,
        media_type="text/plain",
        size_bytes=len(payload),
        extracted_size_bytes=len(payload),
        trust_level="trusted",
        redaction_state=RedactionState.APPROVED,
        adapter_id="test",
        adapter_version="1",
    )
    snapshot = SourceSnapshot.build(
        snapshot_id="snapshot-a",
        items=(item,),
        contents=(
            SourceSnapshotContent(
                item_id=item.id,
                extracted_bytes=payload,
                extracted_sha256=digest,
                media_type=item.media_type,
                size_bytes=len(payload),
            ),
        ),
    )
    persistence = SourceSnapshotPersistence(
        snapshot_id=snapshot.id,
        content_relative_paths={
            item.id: "shared/source_snapshots/a/content/b.bin",
        },
        manifest_relative_path="shared/source_snapshots/a/manifest.json",
    )

    row = await repository.finalize_source_snapshot(
        experiment_id=experiment.id,
        snapshot=snapshot,
        persistence=persistence,
    )

    assert row.manifest_sha256 == snapshot.manifest_sha256
    assert experiment.source_snapshot_id == snapshot.id
    with pytest.raises(ValueError, match="immutable"):
        await repository.finalize_source_snapshot(
            experiment_id=experiment.id,
            snapshot=snapshot,
            persistence=persistence,
        )
