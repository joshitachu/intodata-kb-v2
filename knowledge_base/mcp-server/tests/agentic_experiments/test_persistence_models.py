"""Root-owned persistence checks for the isolated experiment tables."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mcp_server.db.models import (
    GenerateRun,
    GenerationExperiment,
    GenerationExperimentArtifact,
    GenerationSourceSnapshot,
    GenerationSourceSnapshotItem,
    Project,
)


async def _legacy_rows(db_session):
    project = Project(
        id="fixture-project",
        display_name="Fixture Project",
        local_path="/safe/fixture",
    )
    run = GenerateRun(
        project_id=project.id,
        triggered_by="api",
        status="completed",
        doc_count=1,
    )
    db_session.add_all((project, run))
    await db_session.flush()
    return project, run


@pytest.mark.asyncio
async def test_experiment_is_distinct_from_legacy_generate_run(db_session) -> None:
    project, run = await _legacy_rows(db_session)
    experiment = GenerationExperiment(
        id="00000000-0000-4000-8000-000000000001",
        project_id=project.id,
        baseline_run_id=run.id,
        idempotency_key="freeze-run-1",
    )
    db_session.add(experiment)
    await db_session.flush()

    assert experiment.status == "queued"
    assert experiment.publishes is False
    assert run.status == "completed"
    assert run.doc_count == 1
    assert await db_session.scalar(select(func.count(GenerateRun.id))) == 1


@pytest.mark.asyncio
async def test_project_scoped_idempotency_key_is_unique(db_session) -> None:
    project, run = await _legacy_rows(db_session)
    db_session.add_all(
        (
            GenerationExperiment(
                id="00000000-0000-4000-8000-000000000001",
                project_id=project.id,
                baseline_run_id=run.id,
                idempotency_key="same-key",
            ),
            GenerationExperiment(
                id="00000000-0000-4000-8000-000000000002",
                project_id=project.id,
                baseline_run_id=run.id,
                idempotency_key="same-key",
            ),
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_artifact_path_is_unique_inside_experiment(db_session) -> None:
    project, run = await _legacy_rows(db_session)
    experiment = GenerationExperiment(
        id="00000000-0000-4000-8000-000000000001",
        project_id=project.id,
        baseline_run_id=run.id,
        idempotency_key="artifact-test",
    )
    db_session.add(experiment)
    await db_session.flush()
    common = {
        "experiment_id": experiment.id,
        "branch": "baseline",
        "kind": "document",
        "relative_path": "baseline/docs/a.md",
        "sha256": "a" * 64,
        "media_type": "text/markdown",
        "size_bytes": 1,
    }
    db_session.add_all(
        (
            GenerationExperimentArtifact(logical_name="a", **common),
            GenerationExperimentArtifact(logical_name="duplicate-path", **common),
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_source_snapshot_indexes_immutable_content_path(db_session) -> None:
    project, run = await _legacy_rows(db_session)
    experiment = GenerationExperiment(
        id="00000000-0000-4000-8000-000000000001",
        project_id=project.id,
        baseline_run_id=run.id,
        idempotency_key="source-snapshot",
    )
    snapshot = GenerationSourceSnapshot(
        id="snapshot-a",
        experiment_id=experiment.id,
        schema_version=1,
        manifest_sha256="a" * 64,
        security_state="approved",
        manifest_relative_path="shared/source_snapshots/a/manifest.json",
    )
    item = GenerationSourceSnapshotItem(
        id="b" * 64,
        snapshot_id=snapshot.id,
        source_descriptor_id="descriptor-a",
        logical_identity="fixture://item-a",
        source_kind="test",
        source_role="implementation",
        capabilities_json=["searchable_text"],
        locator="fixture://item-a",
        raw_sha256="c" * 64,
        extracted_sha256="d" * 64,
        media_type="text/plain",
        size_bytes=4,
        extracted_size_bytes=4,
        content_relative_path="shared/source_snapshots/a/content/b.bin",
        trust_level="trusted",
        redaction_state="approved",
        adapter_id="test",
        adapter_version="1",
    )
    db_session.add_all((experiment, snapshot, item))
    await db_session.flush()

    assert item.content_relative_path.endswith("/content/b.bin")
