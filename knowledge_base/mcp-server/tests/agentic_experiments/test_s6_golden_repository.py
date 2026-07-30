from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from mcp_server.db.models import (
    GenerateRun,
    GenerationExperimentArtifact,
    GenerationTaskRun,
    Project,
)
from mcp_server.experiments.application.import_golden import (
    GoldenImportCommand,
    GoldenParityImporter,
)
from mcp_server.experiments.application.snapshot_golden import (
    GoldenParityArtifactSink,
)
from mcp_server.experiments.application.snapshot_recipe import (
    RecipeSnapshotArtifactSink,
)
from mcp_server.experiments.domain.parity import GoldenLineageInput
from mcp_server.experiments.infrastructure.persistence.sqlalchemy_repository import (
    SqlAlchemyExperimentRepository,
)
from mcp_server.experiments.infrastructure.recipes.compiler import compile_recipe
from mcp_server.experiments.infrastructure.recipes.loader import (
    load_recipe,
    parse_scope,
)
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore


REAL_GOLDEN_ROOT = (
    Path(__file__).resolve().parents[3]
    / "output/alitalend/docs/_debug/model_inputs/phase3/016_09_EXECUTIVE_SUMMARY"
)
REAL_GOLDEN_HASHES = {
    "prompt": "3718fb5cbd6d7ecb3a77699c3f6efa536a95145bf9c03c8064830bc58fe12b09",
    "context": "ab7775311a6cec989a07313c16603909fca979dd187a616d4ed0bd677a5a12cf",
    "request": "3b02b0c75f5f222baeed4926a96832977656ec7a3769e8d9666ab84f5f95f1ec",
    "response": "c7c517fb9f906ca1552217942e6ddc996b91f4cc3d6612851facbbd9029195a9",
}


def _golden_bundle():
    return GoldenParityImporter().import_bundle(
        command=GoldenImportCommand(
            task_id="09_EXECUTIVE_SUMMARY",
            allowed_root=REAL_GOLDEN_ROOT,
            prompt_relative_path=Path("prompt.md"),
            context_relative_path=Path("context_bundle.md"),
            request_relative_path=Path("request.json"),
            response_relative_path=Path("response.json"),
            output_contract={
                "artifact_name": "09_EXECUTIVE_SUMMARY.md",
                "media_type": "text/markdown",
            },
            expected_sha256=REAL_GOLDEN_HASHES,
            lineage=GoldenLineageInput(
                baseline_run_id=44,
                run_model_route=(
                    "deepseek/deepseek-v4-pro @ https://api.deepseek.com/v1"
                ),
                resume_requested=True,
            ),
        )
    )


@pytest.mark.asyncio
async def test_repository_attaches_real_golden_bundle_to_declared_task_only(
    db_session,
    tmp_path: Path,
) -> None:
    project = Project(id="alitalend", display_name="AliTalend", local_path="/fixture")
    run = GenerateRun(project_id=project.id, triggered_by="api", status="completed")
    db_session.add_all((project, run))
    await db_session.flush()

    repository = SqlAlchemyExperimentRepository(db_session)
    experiment_id = str(uuid4())
    repository.reserve(
        experiment_id=experiment_id,
        project_id=project.id,
        baseline_run_id=run.id,
        idempotency_key="golden-repository",
        runtime_id="fake",
        provider_id="fake",
        model_id="fake-model",
        settings={},
    )
    await db_session.flush()

    recipe_root = (
        Path(__file__).parents[2]
        / "data"
        / "agentic_recipes"
        / "alitalend_legacy_equivalence"
    )
    manifest = load_recipe(recipe_root / "recipe.yaml")
    caller_scope = parse_scope(
        {
            "required_capabilities": ["searchable_text"],
            "requested_output_ids": ["09_EXECUTIVE_SUMMARY"],
            "budgets": {
                "max_source_items": 1000,
                "max_evidence_items": 10000,
                "max_bytes": 104857600,
                "max_input_tokens": 900000,
                "max_tool_calls": 25,
                "max_elapsed_seconds": 3600,
            },
            "unknown_policy": "disclose",
        }
    )
    graph = compile_recipe(
        manifest,
        recipe_root=recipe_root,
        model_capabilities=frozenset({"tools"}),
        available_source_capabilities={
            "implementation": frozenset({"searchable_text", "structured_records"}),
        },
        caller_scope=caller_scope,
    )
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id=project.id,
        experiment_id=experiment_id,
    )
    snapshot_id = str(uuid4())
    recipe_persistence = RecipeSnapshotArtifactSink(store).save(
        experiment_id=experiment_id,
        snapshot_id=snapshot_id,
        manifest=manifest,
        graph=graph,
    )
    await repository.finalize_recipe_snapshot(
        experiment_id=experiment_id,
        snapshot_id=snapshot_id,
        manifest=manifest,
        graph=graph,
        persistence=recipe_persistence,
    )

    bundle = _golden_bundle()
    golden_persistence = GoldenParityArtifactSink(store).save(bundle=bundle)
    task = await repository.attach_golden_parity(
        experiment_id=experiment_id,
        task_node_id="document:09_EXECUTIVE_SUMMARY",
        receipt=bundle.receipt,
        persistence=golden_persistence,
    )
    await repository.attach_golden_parity(
        experiment_id=experiment_id,
        task_node_id="document:09_EXECUTIVE_SUMMARY",
        receipt=bundle.receipt,
        persistence=golden_persistence,
    )

    assert task.provider_id == "deepseek"
    assert task.model_id == "deepseek-v4-pro"
    assert task.request_json["golden_parity"]["aggregate_receipt_sha256"] == (
        bundle.receipt.aggregate_receipt_sha256
    )
    assert (
        await db_session.scalar(
            select(func.count(GenerationExperimentArtifact.id)).where(
                GenerationExperimentArtifact.experiment_id == experiment_id,
                GenerationExperimentArtifact.kind.like("golden_%"),
            )
        )
        == 5
    )
    assert (
        await db_session.scalar(
            select(func.count(GenerationTaskRun.id)).where(
                GenerationTaskRun.experiment_id == experiment_id
            )
        )
        == 3
    )

    with pytest.raises(ValueError, match="not declared"):
        await repository.attach_golden_parity(
            experiment_id=experiment_id,
            task_node_id="analysis",
            receipt=bundle.receipt,
            persistence=golden_persistence,
        )
