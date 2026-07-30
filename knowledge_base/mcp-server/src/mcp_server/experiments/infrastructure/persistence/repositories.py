"""Framework-free persistence ports and a deterministic in-memory test adapter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from mcp_server.experiments.domain.models import (
    ExperimentArtifact,
    GenerationExperiment,
    LegacyBaselineRun,
)


class ExperimentRepository(Protocol):
    """Persistence boundary for the S1 application services.

    The root orchestrator supplies the SQLAlchemy adapter and migration.  Keeping
    the port here ensures baseline freezing has no accidental ORM dependency.
    """

    def find_by_idempotency_key(
        self, *, project_id: str, idempotency_key: str
    ) -> GenerationExperiment | None: ...

    def get_experiment(self, experiment_id: str) -> GenerationExperiment | None: ...

    def save_experiment(self, experiment: GenerationExperiment) -> None: ...

    def add_artifacts(self, artifacts: Iterable[ExperimentArtifact]) -> None: ...

    def list_artifacts(self, experiment_id: str) -> tuple[ExperimentArtifact, ...]: ...

    def latest_completed_baseline(self, project_id: str) -> LegacyBaselineRun | None: ...

    def get_completed_baseline(
        self, *, project_id: str, run_id: int
    ) -> LegacyBaselineRun | None: ...


class InMemoryExperimentRepository:
    """Small deterministic adapter used by S1 tests and local fake-runtime work."""

    def __init__(self, baselines: Iterable[LegacyBaselineRun] = ()) -> None:
        self._experiments: dict[str, GenerationExperiment] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._artifacts: dict[str, list[ExperimentArtifact]] = {}
        self._baselines: dict[tuple[str, int], LegacyBaselineRun] = {
            (baseline.project_id, baseline.id): baseline for baseline in baselines
        }

    def find_by_idempotency_key(
        self, *, project_id: str, idempotency_key: str
    ) -> GenerationExperiment | None:
        experiment_id = self._idempotency.get((project_id, idempotency_key))
        return self._experiments.get(experiment_id) if experiment_id else None

    def get_experiment(self, experiment_id: str) -> GenerationExperiment | None:
        return self._experiments.get(experiment_id)

    def save_experiment(self, experiment: GenerationExperiment) -> None:
        existing = self._experiments.get(experiment.id)
        idempotency_key = (experiment.project_id, experiment.idempotency_key)
        existing_id = self._idempotency.get(idempotency_key)
        if existing_id is not None and existing_id != experiment.id:
            raise ValueError("idempotency key is already owned by another experiment")
        if existing is not None and (
            existing.project_id != experiment.project_id
            or existing.baseline_run_id != experiment.baseline_run_id
            or existing.idempotency_key != experiment.idempotency_key
        ):
            raise ValueError("experiment identity and baseline selection are immutable")
        self._experiments[experiment.id] = experiment
        self._idempotency[idempotency_key] = experiment.id

    def add_artifacts(self, artifacts: Iterable[ExperimentArtifact]) -> None:
        for artifact in artifacts:
            stored = self._artifacts.setdefault(artifact.experiment_id, [])
            identity = (
                artifact.branch,
                artifact.logical_name,
                artifact.relative_path,
                artifact.sha256,
            )
            if any(
                (item.branch, item.logical_name, item.relative_path, item.sha256) == identity
                for item in stored
            ):
                continue
            if any(
                item.branch == artifact.branch and item.logical_name == artifact.logical_name
                for item in stored
            ):
                raise ValueError("artifact logical identity cannot be replaced")
            stored.append(artifact)

    def list_artifacts(self, experiment_id: str) -> tuple[ExperimentArtifact, ...]:
        return tuple(self._artifacts.get(experiment_id, ()))

    def latest_completed_baseline(self, project_id: str) -> LegacyBaselineRun | None:
        candidates = [
            baseline for (baseline_project, _), baseline in self._baselines.items()
            if baseline_project == project_id
        ]
        return max(candidates, key=lambda run: (run.completed_at, run.id), default=None)

    def get_completed_baseline(
        self, *, project_id: str, run_id: int
    ) -> LegacyBaselineRun | None:
        return self._baselines.get((project_id, run_id))
