"""Async SQLAlchemy composition for experiment domain records.

The application/domain ports remain framework-free. This adapter owns the durable
reservation used before filesystem work and the transaction that indexes a
successfully frozen branch.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.db.models import (
    GenerationExperiment as GenerationExperimentRow,
)
from mcp_server.db.models import (
    GenerationExperimentArtifact as GenerationExperimentArtifactRow,
)
from mcp_server.db.models import (
    GenerationSourceSnapshot as GenerationSourceSnapshotRow,
)
from mcp_server.db.models import (
    GenerationSourceSnapshotItem as GenerationSourceSnapshotItemRow,
)
from mcp_server.db.models import (
    GenerationRecipeSnapshot as GenerationRecipeSnapshotRow,
)
from mcp_server.db.models import GenerationTaskRun as GenerationTaskRunRow
from mcp_server.experiments.application.snapshot_recipe import RecipeSnapshotPersistence
from mcp_server.experiments.application.snapshot_golden import (
    GoldenSnapshotPersistence,
)
from mcp_server.experiments.application.snapshot_sources import SourceSnapshotPersistence
from mcp_server.experiments.domain.models import (
    ExperimentArtifact,
    GenerationExperiment,
)
from mcp_server.experiments.domain.parity import (
    GoldenParityReceipt,
    thaw_json,
)
from mcp_server.experiments.domain.recipes import AgenticRecipeManifest
from mcp_server.experiments.domain.sources import SourceSnapshot
from mcp_server.experiments.domain.statuses import ExperimentStatus
from mcp_server.experiments.domain.tasks import CompiledTaskGraph
from mcp_server.experiments.infrastructure.storage.artifact_store import StoredArtifact


class SqlAlchemyExperimentRepository:
    """Durable experiment index; artifact bytes remain filesystem-owned."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_idempotency_key(
        self,
        *,
        project_id: str,
        idempotency_key: str,
    ) -> GenerationExperimentRow | None:
        result = await self._session.execute(
            select(GenerationExperimentRow).where(
                GenerationExperimentRow.project_id == project_id,
                GenerationExperimentRow.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, experiment_id: str) -> GenerationExperimentRow | None:
        return await self._session.get(GenerationExperimentRow, experiment_id)

    async def list_for_project(
        self,
        project_id: str,
        *,
        limit: int = 100,
    ) -> tuple[GenerationExperimentRow, ...]:
        result = await self._session.execute(
            select(GenerationExperimentRow)
            .where(GenerationExperimentRow.project_id == project_id)
            .order_by(GenerationExperimentRow.created_at.desc())
            .limit(limit)
        )
        return tuple(result.scalars())

    async def list_artifacts(
        self,
        experiment_id: str,
    ) -> tuple[GenerationExperimentArtifactRow, ...]:
        result = await self._session.execute(
            select(GenerationExperimentArtifactRow)
            .where(GenerationExperimentArtifactRow.experiment_id == experiment_id)
            .order_by(
                GenerationExperimentArtifactRow.branch,
                GenerationExperimentArtifactRow.kind,
                GenerationExperimentArtifactRow.logical_name,
            )
        )
        return tuple(result.scalars())

    def reserve(
        self,
        *,
        experiment_id: str,
        project_id: str,
        baseline_run_id: int | None,
        idempotency_key: str,
        runtime_id: str,
        provider_id: str | None,
        model_id: str | None,
        settings: dict[str, object],
    ) -> GenerationExperimentRow:
        row = GenerationExperimentRow(
            id=experiment_id,
            project_id=project_id,
            baseline_run_id=baseline_run_id,
            idempotency_key=idempotency_key,
            status=ExperimentStatus.QUEUED.value,
            comparison_status="pending",
            candidate_verdict="not_evaluated",
            runtime_id=runtime_id,
            provider_id=provider_id,
            model_id=model_id,
            publishes=False,
            settings_json=settings,
        )
        self._session.add(row)
        return row

    async def finalize_baseline(
        self,
        *,
        experiment: GenerationExperiment,
        artifacts: Iterable[ExperimentArtifact],
    ) -> GenerationExperimentRow:
        row = await self.get(experiment.id)
        if row is None:
            raise LookupError("experiment reservation no longer exists")
        if row.baseline_manifest_sha256 not in {
            None,
            experiment.baseline_manifest_sha256,
        }:
            raise ValueError("frozen baseline manifest cannot be replaced")

        row.baseline_manifest_sha256 = experiment.baseline_manifest_sha256
        row.baseline_artifact_root = experiment.baseline_artifact_root
        indexed: set[tuple[str, str]] = set()
        for artifact in artifacts:
            logical_key = (artifact.branch, artifact.logical_name)
            if logical_key in indexed:
                raise ValueError("duplicate artifact logical identity")
            indexed.add(logical_key)
            self._session.add(
                GenerationExperimentArtifactRow(
                    experiment_id=experiment.id,
                    branch=artifact.branch,
                    kind=artifact.kind,
                    logical_name=artifact.logical_name,
                    relative_path=artifact.relative_path,
                    sha256=artifact.sha256,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    artifact_metadata=dict(artifact.metadata),
                    finalized_at=datetime.datetime.now(datetime.timezone.utc),
                )
            )
        await self._session.flush()
        return row

    async def finalize_source_snapshot(
        self,
        *,
        experiment_id: str,
        snapshot: SourceSnapshot,
        persistence: SourceSnapshotPersistence,
    ) -> GenerationSourceSnapshotRow:
        experiment = await self.get(experiment_id)
        if experiment is None:
            raise LookupError("experiment reservation no longer exists")
        if persistence.snapshot_id != snapshot.id:
            raise ValueError("source snapshot persistence identity mismatch")
        content_paths = persistence.content_relative_paths
        if set(content_paths) != {item.id for item in snapshot.items}:
            raise ValueError("source snapshot persistence content index mismatch")
        existing = await self._session.get(GenerationSourceSnapshotRow, snapshot.id)
        if existing is not None or experiment.source_snapshot_id is not None:
            raise ValueError("experiment source snapshot is immutable")

        finalized_at = datetime.datetime.now(datetime.timezone.utc)
        snapshot_row = GenerationSourceSnapshotRow(
            id=snapshot.id,
            experiment_id=experiment_id,
            schema_version=snapshot.schema_version,
            manifest_sha256=snapshot.manifest_sha256,
            security_state=snapshot.security_state.value,
            manifest_relative_path=persistence.manifest_relative_path,
            snapshot_metadata={"content_count": len(snapshot.items)},
            finalized_at=finalized_at,
        )
        self._session.add(snapshot_row)
        for item in snapshot.items:
            self._session.add(
                GenerationSourceSnapshotItemRow(
                    id=item.id,
                    snapshot_id=snapshot.id,
                    source_descriptor_id=item.source_descriptor_id,
                    logical_identity=item.logical_identity,
                    source_kind=item.source_kind,
                    source_role=item.source_role,
                    capabilities_json=sorted(item.capabilities),
                    locator=item.locator,
                    raw_sha256=item.raw_sha256,
                    extracted_sha256=item.extracted_sha256,
                    media_type=item.media_type,
                    size_bytes=item.size_bytes,
                    extracted_size_bytes=item.extracted_size_bytes,
                    content_relative_path=content_paths[item.id],
                    trust_level=item.trust_level,
                    redaction_state=item.redaction_state.value,
                    adapter_id=item.adapter_id,
                    adapter_version=item.adapter_version,
                    item_metadata=dict(item.metadata),
                )
            )
        # The experiment and source snapshot deliberately point at each other.
        # Materialize the owned snapshot first, then attach the convenience
        # reference so databases with immediate FK enforcement remain safe.
        await self._session.flush()
        experiment.source_snapshot_id = snapshot.id
        await self._session.flush()
        return snapshot_row

    async def finalize_recipe_snapshot(
        self,
        *,
        experiment_id: str,
        snapshot_id: str,
        manifest: AgenticRecipeManifest,
        graph: CompiledTaskGraph,
        persistence: RecipeSnapshotPersistence,
    ) -> GenerationRecipeSnapshotRow:
        experiment = await self.get(experiment_id)
        if experiment is None:
            raise LookupError("experiment reservation no longer exists")
        if persistence.snapshot_id != snapshot_id:
            raise ValueError("recipe snapshot persistence identity mismatch")
        existing = await self._session.get(GenerationRecipeSnapshotRow, snapshot_id)
        if existing is not None or experiment.recipe_snapshot_id is not None:
            raise ValueError("experiment recipe snapshot is immutable")
        required_receipts = {
            "manifest_sha256",
            "source_requirements_sha256",
            "effective_scope_sha256",
            "semantic_prompts_sha256",
            "output_contracts_sha256",
            "graph_sha256",
        }
        if not required_receipts.issubset(graph.receipt):
            raise ValueError("compiled graph receipt is incomplete")

        finalized_at = datetime.datetime.now(datetime.timezone.utc)
        recipe_row = GenerationRecipeSnapshotRow(
            id=snapshot_id,
            experiment_id=experiment_id,
            recipe_id=manifest.id,
            recipe_version=manifest.version,
            schema_version=manifest.schema_version,
            certification_id=manifest.certification_id,
            manifest_sha256=graph.receipt["manifest_sha256"],
            source_requirements_sha256=graph.receipt["source_requirements_sha256"],
            effective_scope_sha256=graph.receipt["effective_scope_sha256"],
            semantic_prompts_sha256=graph.receipt["semantic_prompts_sha256"],
            output_contracts_sha256=graph.receipt["output_contracts_sha256"],
            graph_sha256=graph.receipt["graph_sha256"],
            manifest_relative_path=persistence.manifest_relative_path,
            recipe_metadata={
                "compiled_graph_relative_path": persistence.compiled_graph_relative_path,
                "recipe_relative_path": persistence.recipe_relative_path,
            },
            finalized_at=finalized_at,
        )
        self._session.add(recipe_row)
        for node in graph.nodes:
            task_id = hashlib.sha256(
                f"{experiment_id}:{node.node_id}".encode("utf-8")
            ).hexdigest()
            self._session.add(
                GenerationTaskRunRow(
                    id=task_id,
                    experiment_id=experiment_id,
                    recipe_snapshot_id=snapshot_id,
                    node_id=node.node_id,
                    task_type=node.task_type,
                    role=node.role,
                    dependencies_json=list(node.dependencies),
                    dependency_output_refs_json=list(node.dependency_output_refs),
                    output_ids_json=list(node.output_ids),
                    status="pending",
                    attempt=0,
                    max_attempts=manifest.budgets.max_attempts,
                    semantic_prompt_sha256=hashlib.sha256(
                        node.semantic_prompt.encode("utf-8")
                    ).hexdigest(),
                    graph_sha256=graph.sha256,
                    runtime_id=experiment.runtime_id,
                    provider_id=experiment.provider_id,
                    model_id=experiment.model_id,
                    request_json={
                        "compiled_graph_relative_path": (
                            persistence.compiled_graph_relative_path
                        ),
                        "node_id": node.node_id,
                    },
                )
            )
        await self._session.flush()
        experiment.recipe_snapshot_id = snapshot_id
        await self._session.flush()
        return recipe_row

    async def attach_golden_parity(
        self,
        *,
        experiment_id: str,
        task_node_id: str,
        receipt: GoldenParityReceipt,
        persistence: GoldenSnapshotPersistence,
    ) -> GenerationTaskRunRow:
        """Index one immutable historical task bundle against a compiled task."""

        result = await self._session.execute(
            select(GenerationTaskRunRow)
            .where(
                GenerationTaskRunRow.experiment_id == experiment_id,
                GenerationTaskRunRow.node_id == task_node_id,
            )
            .with_for_update()
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise LookupError("compiled experiment task does not exist")
        if receipt.request.task_id not in task.output_ids_json:
            raise ValueError(
                "golden parity output is not declared by the selected task"
            )
        if (
            persistence.task_id != receipt.request.task_id
            or persistence.aggregate_receipt_sha256
            != receipt.aggregate_receipt_sha256
        ):
            raise ValueError("golden persistence receipt identity mismatch")

        request_json = dict(task.request_json or {})
        existing = request_json.get("golden_parity")
        if existing is not None:
            if (
                isinstance(existing, dict)
                and existing.get("aggregate_receipt_sha256")
                == receipt.aggregate_receipt_sha256
            ):
                return task
            raise ValueError("golden parity task request is immutable")

        finalized_at = datetime.datetime.now(datetime.timezone.utc)
        for artifact in persistence.artifacts:
            self._session.add(
                GenerationExperimentArtifactRow(
                    experiment_id=experiment_id,
                    branch=artifact.branch,
                    kind=artifact.kind,
                    logical_name=artifact.logical_name,
                    relative_path=artifact.relative_path,
                    sha256=artifact.sha256,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    artifact_metadata={
                        "aggregate_receipt_sha256": (
                            receipt.aggregate_receipt_sha256
                        ),
                        "task_id": task.id,
                        "task_node_id": task.node_id,
                    },
                    finalized_at=finalized_at,
                )
            )
        task.provider_id = receipt.request.provider_id
        task.model_id = receipt.request.model_id
        task.request_json = {
            **request_json,
            "golden_parity": {
                "aggregate_receipt_sha256": (
                    receipt.aggregate_receipt_sha256
                ),
                "artifact_relative_paths": dict(
                    persistence.artifact_relative_paths
                ),
                "manifest_relative_path": (
                    persistence.manifest_relative_path
                ),
                "model_options": thaw_json(
                    receipt.request.model_options
                ),
                "output_contract_sha256": (
                    receipt.request.output_contract_sha256
                ),
                "source_hashes": dict(receipt.source_hashes),
                "task_id": receipt.request.task_id,
            },
        }
        await self._session.flush()
        return task

    async def mark_failed(self, experiment_id: str, detail: str) -> None:
        row = await self.get(experiment_id)
        if row is None:
            return
        row.status = ExperimentStatus.FAILED.value
        row.error_message = detail[:4000]
        row.completed_at = datetime.datetime.now(datetime.timezone.utc)
        await self._session.flush()

    async def mark_candidate_running(self, experiment_id: str) -> GenerationExperimentRow:
        """Record the start of the explicit UI-triggered candidate call."""

        row = await self.get(experiment_id)
        if row is None:
            raise LookupError("experiment reservation no longer exists")
        if row.status not in {
            ExperimentStatus.QUEUED.value,
            ExperimentStatus.SNAPSHOTTING.value,
        }:
            raise ValueError("candidate can only start from a prepared experiment")
        now = datetime.datetime.now(datetime.timezone.utc)
        row.status = ExperimentStatus.RUNNING.value
        row.started_at = row.started_at or now
        row.runtime_version = "1.18.4"
        row.error_message = None
        await self._session.flush()
        return row

    async def mark_candidate_recovering(
        self,
        experiment_id: str,
    ) -> GenerationExperimentRow:
        """Reopen one failed, artifact-free candidate for no-dispatch recovery."""

        row = await self.get(experiment_id)
        if row is None:
            raise LookupError("experiment reservation no longer exists")
        if row.status != ExperimentStatus.FAILED.value:
            raise ValueError("only a failed experiment can be recovered")
        existing = await self.list_artifacts(experiment_id)
        if any(artifact.branch == "candidate" for artifact in existing):
            raise ValueError("candidate recovery requires an unindexed candidate branch")
        row.status = ExperimentStatus.RUNNING.value
        row.error_message = None
        settings = dict(row.settings_json or {})
        settings["candidate_recovery"] = {
            "mode": "existing-opencode-session",
            "provider_calls_performed": 0,
        }
        row.settings_json = settings
        await self._session.flush()
        return row

    async def finalize_quick_candidate(
        self,
        *,
        experiment_id: str,
        artifacts: Iterable[StoredArtifact],
        manifest_sha256: str,
        elapsed_ms: int,
        session_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        billed_cost_usd: str | None,
        safety_warnings: Iterable[dict[str, str]] = (),
    ) -> GenerationExperimentRow:
        """Index one immutable non-publishing candidate bundle."""

        row = await self.get(experiment_id)
        if row is None:
            raise LookupError("experiment reservation no longer exists")
        if row.status != ExperimentStatus.RUNNING.value:
            raise ValueError("candidate finalization requires a running experiment")
        finalized_at = datetime.datetime.now(datetime.timezone.utc)
        for artifact in artifacts:
            if artifact.branch != "candidate":
                raise ValueError("quick candidate artifacts must remain on the candidate branch")
            self._session.add(
                GenerationExperimentArtifactRow(
                    experiment_id=experiment_id,
                    branch=artifact.branch,
                    kind=artifact.kind,
                    logical_name=artifact.logical_name,
                    relative_path=artifact.relative_path,
                    sha256=artifact.sha256,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    artifact_metadata={
                        "runtime": "opencode",
                        "runtime_version": "1.18.4",
                        "session_id": session_id,
                    },
                    finalized_at=finalized_at,
                )
            )
        warnings = tuple(dict(item) for item in safety_warnings)
        row.status = (
            ExperimentStatus.COMPLETED_WARNINGS.value
            if warnings
            else ExperimentStatus.COMPLETED.value
        )
        row.comparison_status = "pending"
        row.candidate_verdict = "not_evaluated"
        row.candidate_artifact_root = f"experiments/{experiment_id}/candidate"
        row.completed_at = finalized_at
        row.wall_duration_ms = elapsed_ms
        row.metrics_json = {
            "candidate": {
                "manifest_sha256": manifest_sha256,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "billed_cost_usd": billed_cost_usd,
                "provider_latency_ms": elapsed_ms,
                "runtime_session_id": session_id,
                "safety_warnings": list(warnings),
            }
        }
        await self._session.flush()
        return row
