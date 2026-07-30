"""Create an immutable legacy baseline without entering legacy generation paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from mcp_server.experiments.domain.models import (
    ExperimentArtifact,
    GenerationExperiment,
    LegacyBaselineRun,
    require_safe_identifier,
)
from mcp_server.experiments.infrastructure.persistence.repositories import ExperimentRepository
from mcp_server.experiments.infrastructure.storage.artifact_store import ArtifactStore, StoredArtifact
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes


class BaselineFreezeError(RuntimeError):
    """A baseline is missing, unsafe, or cannot be made immutable."""


@dataclass(frozen=True)
class BaselineFreezeRequest:
    project_id: str
    idempotency_key: str
    baseline_run_id: int | None = None
    experiment_id: str | None = None


@dataclass(frozen=True)
class BaselineFreezeResult:
    experiment: GenerationExperiment
    artifacts: tuple[ExperimentArtifact, ...]
    reused: bool


ArtifactStoreFactory = Callable[[str, str], ArtifactStore]


class BaselineFreezer:
    """Freeze an allowed legacy run into a dedicated experiment artifact tree."""

    _SUPPORTING_SUFFIXES = frozenset({".json", ".md"})

    def __init__(
        self,
        *,
        repository: ExperimentRepository,
        artifact_store_factory: ArtifactStoreFactory,
    ) -> None:
        self._repository = repository
        self._artifact_store_factory = artifact_store_factory

    def freeze(self, request: BaselineFreezeRequest) -> BaselineFreezeResult:
        project_id = require_safe_identifier(request.project_id, field_name="project_id")
        if not request.idempotency_key.strip():
            raise BaselineFreezeError("idempotency_key must not be blank")

        existing = self._repository.find_by_idempotency_key(
            project_id=project_id, idempotency_key=request.idempotency_key
        )
        if existing is not None:
            return BaselineFreezeResult(
                experiment=existing,
                artifacts=self._repository.list_artifacts(existing.id),
                reused=True,
            )

        baseline = self._select_baseline(project_id=project_id, run_id=request.baseline_run_id)
        experiment = GenerationExperiment.new(
            project_id=project_id,
            baseline_run_id=baseline.id,
            idempotency_key=request.idempotency_key,
            experiment_id=request.experiment_id,
        )
        # Persist identity before writing artifacts so a process interruption has an
        # auditable owner.  The root's later ORM adapter supplies transaction policy.
        self._repository.save_experiment(experiment)
        store = self._artifact_store_factory(project_id, experiment.id)
        try:
            artifacts = self._copy_baseline(store=store, experiment=experiment, baseline=baseline)
            receipt = self._build_receipt(experiment=experiment, baseline=baseline, artifacts=artifacts)
            finalized = store.finalize_branch(branch="baseline", manifest=receipt)
            manifest_artifact = ExperimentArtifact(
                experiment_id=experiment.id,
                branch="baseline",
                kind="receipt",
                logical_name="baseline/manifest.json",
                relative_path=finalized.manifest_relative_path,
                sha256=finalized.manifest_sha256,
                size_bytes=len(canonical_json_bytes(receipt)),
                media_type="application/json",
            )
            frozen = experiment.with_frozen_baseline(
                manifest_sha256=finalized.manifest_sha256,
                artifact_root=f"{store.experiment_relative_root}/baseline",
                document_count=sum(artifact.kind == "document" for artifact in artifacts),
            )
            all_artifacts = (*artifacts, manifest_artifact)
            self._repository.add_artifacts(all_artifacts)
            self._repository.save_experiment(frozen)
            return BaselineFreezeResult(experiment=frozen, artifacts=all_artifacts, reused=False)
        except Exception:
            # The partial tree is intentionally retained for later safe reconciliation
            # rather than deleting an unresolved path from an error handler.
            raise

    def _select_baseline(self, *, project_id: str, run_id: int | None) -> LegacyBaselineRun:
        baseline = (
            self._repository.get_completed_baseline(project_id=project_id, run_id=run_id)
            if run_id is not None
            else self._repository.latest_completed_baseline(project_id)
        )
        if baseline is None:
            qualifier = f"run {run_id}" if run_id is not None else "latest completed run"
            raise BaselineFreezeError(f"no completed baseline available for {project_id}: {qualifier}")
        if baseline.project_id != project_id:
            raise BaselineFreezeError("baseline project ownership mismatch")
        return baseline

    def _copy_baseline(
        self,
        *,
        store: ArtifactStore,
        experiment: GenerationExperiment,
        baseline: LegacyBaselineRun,
    ) -> tuple[ExperimentArtifact, ...]:
        docs = self._top_level_documents(baseline.documents_directory)
        copied: list[ExperimentArtifact] = []
        for source in docs:
            stored = store.copy_file(
                branch="baseline",
                logical_name=f"docs/{source.name}",
                source=source,
                relative_path=f"docs/{source.name}",
                media_type="text/markdown",
                kind="document",
            )
            copied.append(self._to_domain_artifact(experiment.id, stored))

        seen_supporting_names: set[str] = set()
        for support in sorted(baseline.supporting_artifacts, key=lambda item: (item.kind, item.logical_name)):
            suffix = support.source_path.suffix.lower()
            safe_name = self._safe_supporting_name(support.logical_name)
            if suffix not in self._SUPPORTING_SUFFIXES:
                raise BaselineFreezeError(f"unsupported baseline supporting receipt suffix: {suffix}")
            if safe_name in seen_supporting_names:
                raise BaselineFreezeError(f"duplicate baseline supporting receipt: {safe_name}")
            seen_supporting_names.add(safe_name)
            stored = store.copy_file(
                branch="baseline",
                logical_name=f"{support.kind}/{safe_name}",
                source=support.source_path,
                relative_path=f"{support.kind}/{safe_name}",
                media_type=support.media_type,
                kind=support.kind,
            )
            copied.append(self._to_domain_artifact(experiment.id, stored))
        return tuple(copied)

    @staticmethod
    def _top_level_documents(directory: Path) -> tuple[Path, ...]:
        directory = Path(directory)
        if directory.is_symlink() or not directory.is_dir():
            raise BaselineFreezeError("baseline documents directory must be a non-symlink directory")
        documents = tuple(
            sorted(
                (
                    child for child in directory.iterdir()
                    if child.suffix.lower() == ".md" and child.is_file() and not child.is_symlink()
                ),
                key=lambda child: child.name,
            )
        )
        if not documents:
            raise BaselineFreezeError("baseline documents directory contains no top-level Markdown files")
        if any(document.stat().st_size == 0 for document in documents):
            raise BaselineFreezeError("baseline documents must not be empty")
        return documents

    @staticmethod
    def _safe_supporting_name(value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in value
            or "\x00" in value
        ):
            raise BaselineFreezeError("supporting receipt name must be a safe relative POSIX path")
        return path.as_posix()

    @staticmethod
    def _to_domain_artifact(experiment_id: str, stored: StoredArtifact) -> ExperimentArtifact:
        return ExperimentArtifact(
            experiment_id=experiment_id,
            branch=stored.branch,
            logical_name=stored.logical_name,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type=stored.media_type,
            kind=stored.kind,
        )

    @staticmethod
    def _build_receipt(
        *,
        experiment: GenerationExperiment,
        baseline: LegacyBaselineRun,
        artifacts: tuple[ExperimentArtifact, ...],
    ) -> dict[str, object]:
        entries = [
            {
                "branch": artifact.branch,
                "kind": artifact.kind,
                "logical_name": artifact.logical_name,
                "media_type": artifact.media_type,
                "relative_path": artifact.relative_path,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
            }
            for artifact in sorted(artifacts, key=lambda item: (item.kind, item.logical_name))
        ]
        return {
            "artifacts": entries,
            "baseline_metrics": dict(baseline.metrics),
            "baseline_run_id": baseline.id,
            "experiment_id": experiment.id,
            "project_id": experiment.project_id,
            "schema_version": 1,
        }
