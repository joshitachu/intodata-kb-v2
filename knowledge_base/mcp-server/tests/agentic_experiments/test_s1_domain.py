from __future__ import annotations

from mcp_server.experiments.domain.models import GenerationExperiment
from mcp_server.experiments.domain.statuses import ExperimentStatus, can_transition


def test_experiment_id_is_canonical_uuid_and_baseline_is_immutable() -> None:
    experiment = GenerationExperiment.new(
        project_id="alitalend",
        baseline_run_id=44,
        idempotency_key="request-1",
        experiment_id="2A966E39-45B9-4D7D-A02B-C0AC12697B1B",
    )

    frozen = experiment.with_frozen_baseline(
        manifest_sha256="a" * 64,
        artifact_root="experiments/2a966e39-45b9-4d7d-a02b-c0ac12697b1b/baseline",
        document_count=10,
    )

    assert frozen.id == "2a966e39-45b9-4d7d-a02b-c0ac12697b1b"
    assert frozen.status is ExperimentStatus.QUEUED
    assert frozen.with_frozen_baseline(
        manifest_sha256="a" * 64,
        artifact_root=frozen.baseline_artifact_root or "",
        document_count=10,
    ) == frozen


def test_statuses_are_monotonic_and_terminal() -> None:
    assert can_transition(ExperimentStatus.QUEUED, ExperimentStatus.SNAPSHOTTING)
    assert can_transition(ExperimentStatus.RUNNING, ExperimentStatus.VALIDATING)
    assert not can_transition(ExperimentStatus.COMPLETED, ExperimentStatus.RUNNING)
    assert not can_transition(ExperimentStatus.FAILED, ExperimentStatus.QUEUED)
