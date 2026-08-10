from __future__ import annotations

import pytest

from mcp_server.db.models import GenerationExperiment, Project
from mcp_server.experiments.infrastructure.persistence.sqlalchemy_repository import (
    SqlAlchemyExperimentRepository,
)


async def _project(db_session, project_id: str) -> Project:
    project = Project(
        id=project_id,
        display_name=project_id,
        local_path=f"/tmp/{project_id}",
        status="ready",
        job_count=1,
        doc_count=0,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _experiment(
    db_session,
    *,
    experiment_id: str,
    project_id: str,
    status: str,
    runtime_id: str,
) -> GenerationExperiment:
    experiment = GenerationExperiment(
        id=experiment_id,
        project_id=project_id,
        idempotency_key=f"key-{experiment_id}",
        status=status,
        runtime_id=runtime_id,
    )
    db_session.add(experiment)
    await db_session.flush()
    return experiment


@pytest.mark.asyncio
async def test_fails_a_running_grounded_experiment_left_over_from_a_restart(
    db_session,
) -> None:
    await _project(db_session, "proj-a")
    await _experiment(
        db_session,
        experiment_id="orphaned-opencode",
        project_id="proj-a",
        status="running",
        runtime_id="opencode-grounded",
    )
    await _experiment(
        db_session,
        experiment_id="orphaned-openai-agents",
        project_id="proj-a",
        status="running",
        runtime_id="openai-agents-grounded",
    )
    await db_session.commit()

    repo = SqlAlchemyExperimentRepository(db_session)
    orphaned = await repo.fail_orphaned_grounded_experiments("server restarted")
    await db_session.commit()

    assert set(orphaned) == {"orphaned-opencode", "orphaned-openai-agents"}
    for experiment_id in orphaned:
        row = await repo.get(experiment_id)
        assert row is not None
        assert row.status == "failed"
        assert row.error_message == "server restarted"
        assert row.completed_at is not None


@pytest.mark.asyncio
async def test_leaves_non_running_and_non_grounded_experiments_untouched(
    db_session,
) -> None:
    await _project(db_session, "proj-b")
    await _experiment(
        db_session,
        experiment_id="already-completed",
        project_id="proj-b",
        status="completed",
        runtime_id="opencode-grounded",
    )
    await _experiment(
        db_session,
        experiment_id="legacy-single-agent",
        project_id="proj-b",
        status="running",
        runtime_id="opencode",
    )
    await db_session.commit()

    repo = SqlAlchemyExperimentRepository(db_session)
    orphaned = await repo.fail_orphaned_grounded_experiments("server restarted")
    await db_session.commit()

    assert orphaned == ()
    completed = await repo.get("already-completed")
    assert completed is not None and completed.status == "completed"
    legacy = await repo.get("legacy-single-agent")
    assert legacy is not None and legacy.status == "running"
