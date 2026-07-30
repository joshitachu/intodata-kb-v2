from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mcp_server.experiments.application.create_experiment import (
    CreateExperimentRequest,
    ExperimentCreationService,
)
from mcp_server.experiments.application.snapshot_baseline import BaselineFreezeError, BaselineFreezer
from mcp_server.experiments.domain.models import BaselineSupportingArtifact, LegacyBaselineRun
from mcp_server.experiments.infrastructure.persistence.repositories import InMemoryExperimentRepository
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore


def _baseline(tmp_path: Path, *, run_id: int = 44, project_id: str = "alitalend") -> LegacyBaselineRun:
    docs = tmp_path / f"docs-{run_id}"
    docs.mkdir()
    (docs / "00_AGENT_BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    (docs / "09_EXECUTIVE_SUMMARY.md").write_text("# Summary\n", encoding="utf-8")
    (docs / "ignored.txt").write_text("not a document", encoding="utf-8")
    metrics = tmp_path / f"run_metrics_{run_id}.json"
    metrics.write_text('{"calls": 16}', encoding="utf-8")
    return LegacyBaselineRun(
        id=run_id,
        project_id=project_id,
        completed_at=datetime(2026, 7, run_id % 28 + 1, tzinfo=timezone.utc),
        documents_directory=docs,
        supporting_artifacts=(
            BaselineSupportingArtifact(
                logical_name=f"run_metrics_{run_id}.json",
                source_path=metrics,
                kind="metrics",
            ),
        ),
        metrics={"input_tokens": 1008625},
    )


def _service(tmp_path: Path, *baselines: LegacyBaselineRun) -> tuple[ExperimentCreationService, InMemoryExperimentRepository]:
    repository = InMemoryExperimentRepository(baselines)
    freezer = BaselineFreezer(
        repository=repository,
        artifact_store_factory=lambda project_id, experiment_id: ArtifactStore(
            output_root=tmp_path / "output",
            project_id=project_id,
            experiment_id=experiment_id,
        ),
    )
    return ExperimentCreationService(baseline_freezer=freezer), repository


def test_create_freezes_latest_baseline_without_mutating_source(tmp_path: Path) -> None:
    older = _baseline(tmp_path, run_id=43)
    newest = _baseline(tmp_path, run_id=44)
    service, repository = _service(tmp_path, older, newest)

    result = service.create(
        CreateExperimentRequest(project_id="alitalend", idempotency_key="create-44")
    )

    assert result.reused is False
    assert result.experiment.baseline_run_id == 44
    assert result.experiment.baseline_document_count == 2
    assert result.experiment.baseline_manifest_sha256 is not None
    copied_docs = [artifact for artifact in result.artifacts if artifact.kind == "document"]
    assert [artifact.logical_name for artifact in copied_docs] == [
        "docs/00_AGENT_BRIEF.md",
        "docs/09_EXECUTIVE_SUMMARY.md",
    ]
    frozen = (
        tmp_path
        / "output"
        / "alitalend"
        / "experiments"
        / result.experiment.id
        / "baseline"
        / "docs"
        / "00_AGENT_BRIEF.md"
    )
    newest.documents_directory.joinpath("00_AGENT_BRIEF.md").write_text("# changed", encoding="utf-8")
    assert frozen.read_text(encoding="utf-8") == "# Brief\n"
    assert repository.get_experiment(result.experiment.id) == result.experiment


def test_create_reuses_idempotency_result_without_copying_again(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    service, _ = _service(tmp_path, baseline)
    request = CreateExperimentRequest(project_id="alitalend", idempotency_key="same-key")

    first = service.create(request)
    baseline.documents_directory.joinpath("00_AGENT_BRIEF.md").write_text("# changed", encoding="utf-8")
    second = service.create(request)

    assert second.reused is True
    assert second.experiment.id == first.experiment.id
    assert second.artifacts == first.artifacts


def test_create_rejects_missing_or_empty_legacy_docs(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    baseline.documents_directory.joinpath("00_AGENT_BRIEF.md").write_text("", encoding="utf-8")
    service, _ = _service(tmp_path, baseline)

    with pytest.raises(BaselineFreezeError, match="must not be empty"):
        service.create(CreateExperimentRequest(project_id="alitalend", idempotency_key="empty"))


def test_explicit_baseline_must_belong_to_project(tmp_path: Path) -> None:
    foreign = _baseline(tmp_path, project_id="other-project")
    service, _ = _service(tmp_path, foreign)

    with pytest.raises(BaselineFreezeError, match="no completed baseline"):
        service.create(
            CreateExperimentRequest(
                project_id="alitalend", baseline_run_id=44, idempotency_key="foreign"
            )
        )
