"""S1 creation command: freeze a baseline and leave the candidate unstarted."""

from __future__ import annotations

from dataclasses import dataclass

from mcp_server.experiments.application.snapshot_baseline import (
    BaselineFreezeResult,
    BaselineFreezeRequest,
    BaselineFreezer,
)


@dataclass(frozen=True)
class CreateExperimentRequest:
    project_id: str
    idempotency_key: str
    baseline_run_id: int | None = None
    experiment_id: str | None = None


class ExperimentCreationService:
    """Application-facing wrapper kept free of FastAPI and ORM request types."""

    def __init__(self, *, baseline_freezer: BaselineFreezer) -> None:
        self._baseline_freezer = baseline_freezer

    def create(self, request: CreateExperimentRequest) -> BaselineFreezeResult:
        return self._baseline_freezer.freeze(
            BaselineFreezeRequest(
                project_id=request.project_id,
                idempotency_key=request.idempotency_key,
                baseline_run_id=request.baseline_run_id,
                experiment_id=request.experiment_id,
            )
        )
