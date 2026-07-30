from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from mcp_server.db.models import (
    GenerateRun,
    GenerationTaskRun,
    Project,
)
from mcp_server.experiments.application.snapshot_recipe import (
    RecipeSnapshotArtifactSink,
)
from mcp_server.experiments.infrastructure.persistence.sqlalchemy_repository import (
    SqlAlchemyExperimentRepository,
)
from mcp_server.experiments.infrastructure.recipes.compiler import compile_recipe
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore


@pytest.mark.asyncio
async def test_repository_indexes_recipe_graph_and_pending_tasks(
    db_session,
    tmp_path: Path,
) -> None:
    project = Project(id="fixture-project", display_name="Fixture", local_path="/fixture")
    run = GenerateRun(project_id=project.id, triggered_by="api", status="completed")
    db_session.add_all((project, run))
    await db_session.flush()
    repository = SqlAlchemyExperimentRepository(db_session)
    experiment_id = str(uuid4())
    experiment = repository.reserve(
        experiment_id=experiment_id,
        project_id=project.id,
        baseline_run_id=run.id,
        idempotency_key="recipe",
        runtime_id="fake",
        provider_id="fake",
        model_id="tool-model",
        settings={},
    )
    await db_session.flush()
    recipe_root = (
        Path(__file__).parents[2]
        / "data"
        / "agentic_recipes"
        / "logistics_operations"
    )
    manifest = load_recipe(recipe_root / "recipe.yaml")
    graph = compile_recipe(
        manifest,
        recipe_root=recipe_root,
        model_capabilities=frozenset({"tools"}),
    )
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id=project.id,
        experiment_id=experiment_id,
    )
    snapshot_id = str(uuid4())
    persistence = RecipeSnapshotArtifactSink(store).save(
        experiment_id=experiment_id,
        snapshot_id=snapshot_id,
        manifest=manifest,
        graph=graph,
    )

    row = await repository.finalize_recipe_snapshot(
        experiment_id=experiment_id,
        snapshot_id=snapshot_id,
        manifest=manifest,
        graph=graph,
        persistence=persistence,
    )

    assert row.graph_sha256 == graph.sha256
    assert experiment.recipe_snapshot_id == snapshot_id
    assert (
        await db_session.scalar(
            select(func.count(GenerationTaskRun.id)).where(
                GenerationTaskRun.experiment_id == experiment_id
            )
        )
        == len(graph.nodes)
    )
    task_statuses = (
        await db_session.execute(
            select(GenerationTaskRun.status).where(
                GenerationTaskRun.experiment_id == experiment_id
            )
        )
    ).scalars()
    assert set(task_statuses) == {"pending"}
