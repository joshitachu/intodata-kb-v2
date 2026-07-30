"""Full offline AliTalend run: 12-node recipe, 10 isolated docs, aggregate A/B."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest

from mcp_server.experiments.application.evaluate_experiment import (
    BranchRunObservation,
    DocumentEvaluationInput,
    ExperimentEvaluationRequest,
    TaskAttemptObservation,
    evaluate_experiment,
)
from mcp_server.experiments.application.evaluate_parity import (
    ParitySide,
    SingleDocumentParityRequest,
    TransientParityMaterial,
    fingerprint_output_contract,
)
from mcp_server.experiments.application.experiment_report import (
    render_canonical_json_report,
    render_markdown_report,
)
from mcp_server.experiments.application.schedule_graph import (
    ExperimentGraphScheduler,
)
from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    EvaluationReceipt,
    QualityMetrics,
    UsageMetrics,
)
from mcp_server.experiments.domain.runtime import OutputContract
from mcp_server.experiments.domain.scheduling import (
    GraphBudgetLimits,
    GraphUsage,
    ScheduledGraph,
    ScheduledTask,
    SchedulerRunStatus,
    TaskExecutionOutcome,
    TaskLease,
)
from mcp_server.experiments.infrastructure.recipes.compiler import compile_recipe
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)
from mcp_server.experiments.infrastructure.validation.structural import (
    measure_markdown,
)


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
EXPERIMENT_ID = "00000000-0000-4000-8000-000000000008"
PROJECT_ID = "alitalend"
KNOWLEDGE_BASE_ROOT = Path(__file__).resolve().parents[3]
RECIPE_ROOT = (
    Path(__file__).parents[2]
    / "data"
    / "agentic_recipes"
    / "alitalend_legacy_equivalence"
)
LEGACY_DOCS = KNOWLEDGE_BASE_ROOT / "output" / PROJECT_ID / "docs"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FullAliFakeExecutor:
    """Writes deterministic task outputs only inside the experiment store."""

    def __init__(self, *, graph, manifest, store: ArtifactStore) -> None:
        self._nodes = {node.node_id: node for node in graph.nodes}
        self._contracts = {
            contract.id: contract for contract in manifest.output_contracts
        }
        self._store = store
        self.calls: list[TaskLease] = []
        self.documents: dict[str, StoredArtifact] = {}

    async def execute(self, lease: TaskLease) -> TaskExecutionOutcome:
        self.calls.append(lease)
        node = self._nodes[lease.task_id]
        for output_id in node.output_ids:
            contract = self._contracts[output_id]
            if contract.media_type == "text/markdown":
                content = (LEGACY_DOCS / contract.path).read_bytes()
                stored = self._store.write_bytes(
                    branch="candidate",
                    logical_name=contract.path,
                    content=content,
                    relative_path=contract.path,
                    media_type=contract.media_type,
                    kind="document",
                )
                self.documents[output_id] = stored
            else:
                content = canonical_json_bytes(
                    {
                        "node_id": node.node_id,
                        "offline_fake": True,
                        "output_id": output_id,
                        "schema_version": 1,
                    }
                )
                self._store.write_bytes(
                    branch="tasks",
                    logical_name=output_id,
                    content=content,
                    relative_path=f"{node.node_id}/{contract.path}",
                    media_type=contract.media_type,
                    kind="task_receipt",
                )
        return TaskExecutionOutcome(
            succeeded=True,
            outputs=node.output_ids,
            usage=GraphUsage(
                input_tokens=1000,
                output_tokens=100,
                tool_calls=2,
                tool_bytes=1024,
                elapsed_ms=100,
            ),
        )


@pytest.mark.asyncio
async def test_full_alitalend_fake_run_is_isolated_and_aggregate_evaluable(
    tmp_path: Path,
) -> None:
    legacy_hashes_before = {
        path.name: _sha256(path.read_bytes())
        for path in sorted(LEGACY_DOCS.glob("[0-9][0-9]_*.md"))
    }
    manifest = load_recipe(RECIPE_ROOT / "recipe.yaml")
    graph = compile_recipe(
        manifest,
        recipe_root=RECIPE_ROOT,
        model_capabilities=frozenset({"tools"}),
    )
    scheduled = ScheduledGraph(
        graph_id=graph.sha256,
        tasks=tuple(
            ScheduledTask(
                task_id=node.node_id,
                dependencies=node.dependencies,
                required_outputs=node.output_ids,
                max_attempts=manifest.budgets.max_attempts,
                reservation=GraphUsage(
                    input_tokens=2000,
                    output_tokens=200,
                    tool_calls=4,
                    tool_bytes=2048,
                    elapsed_ms=200,
                ),
            )
            for node in graph.nodes
        ),
        max_concurrency=manifest.budgets.max_concurrency,
        max_attempts=manifest.budgets.max_attempts,
        budgets=GraphBudgetLimits(
            max_input_tokens=100_000,
            max_output_tokens=20_000,
            max_tool_calls=100,
            max_tool_bytes=1_000_000,
            max_elapsed_ms=10_000,
        ),
    )
    store = ArtifactStore(
        output_root=tmp_path / "output",
        project_id=PROJECT_ID,
        experiment_id=EXPERIMENT_ID,
    )
    scheduler = ExperimentGraphScheduler(scheduled)
    executor = FullAliFakeExecutor(graph=graph, manifest=manifest, store=store)
    batch_count = 0
    while scheduler.run_status() not in {
        SchedulerRunStatus.SUCCEEDED,
        SchedulerRunStatus.FAILED,
    }:
        completed = await scheduler.execute_ready(
            executor=executor,
            worker_id="offline-full-ali",
            now=NOW + timedelta(seconds=batch_count),
            lease_ttl=timedelta(minutes=1),
        )
        assert completed
        batch_count += 1

    assert scheduler.run_status() == SchedulerRunStatus.SUCCEEDED
    assert scheduler.required_outputs_present()
    assert len(executor.calls) == 12
    assert len(executor.documents) == 10
    assert batch_count == 5
    candidate_final = store.finalize_branch(
        branch="candidate",
        manifest={
            "document_count": len(executor.documents),
            "graph_sha256": graph.sha256,
            "publishes": False,
            "schema_version": 1,
        },
    )

    output_contracts = {
        contract.id: contract
        for contract in manifest.output_contracts
        if contract.media_type == "text/markdown"
    }
    graph_nodes = {node.node_id: node for node in graph.nodes}
    document_inputs: list[DocumentEvaluationInput] = []
    for output_id, contract in sorted(output_contracts.items()):
        legacy_bytes = (LEGACY_DOCS / contract.path).read_bytes()
        legacy_text = legacy_bytes.decode("utf-8")
        candidate = executor.documents[output_id]
        runtime_contract = OutputContract(
            contract.path,
            contract.media_type,
            max_bytes=max(len(legacy_bytes), 1),
        )
        node = next(
            current for current in graph.nodes if output_id in current.output_ids
        )
        common = {
            "context_manifest_sha256": graph.receipt[
                "effective_scope_sha256"
            ],
            "source_snapshot_sha256": graph.receipt[
                "source_requirements_sha256"
            ],
            "output_contract_sha256": fingerprint_output_contract(
                runtime_contract
            ),
            "model_route_options_sha256": _sha256(
                b"offline-fake-model-options"
            ),
        }
        document_inputs.append(
            DocumentEvaluationInput(
                parity=SingleDocumentParityRequest(
                    document_id=contract.path,
                    output_contract=runtime_contract,
                    baseline=ParitySide(
                        artifact=ArtifactIdentity(
                            "baseline",
                            "document",
                            contract.path,
                            _sha256(legacy_bytes),
                            len(legacy_bytes),
                        ),
                        **common,
                    ),
                    candidate=ParitySide(
                        artifact=ArtifactIdentity(
                            "candidate",
                            "document",
                            contract.path,
                            candidate.sha256,
                            candidate.size_bytes,
                        ),
                        **common,
                    ),
                    baseline_transient=TransientParityMaterial(
                        semantic_prompt=node.semantic_prompt,
                        runtime_envelope={"runtime": "legacy-python"},
                    ),
                    candidate_transient=TransientParityMaterial(
                        semantic_prompt=node.semantic_prompt,
                        runtime_envelope={"runtime": "fake-opencode"},
                    ),
                ),
                baseline_structure=measure_markdown(
                    legacy_text,
                    required_sections=contract.required_sections,
                ),
                candidate_structure=measure_markdown(
                    legacy_text,
                    required_sections=contract.required_sections,
                ),
            )
        )

    candidate_attempts: list[TaskAttemptObservation] = []
    for node_id in graph.topological_order:
        node = graph_nodes[node_id]
        state = scheduler.task_state(node_id)
        attempt = state.attempts[-1]
        if node.node_id == "evidence":
            start_ms = 0
        elif node.node_id == "analysis":
            start_ms = 100
        else:
            document_index = sorted(
                current.node_id
                for current in graph.nodes
                if current.task_type == "synthesis"
            ).index(node.node_id)
            start_ms = 200 + (document_index // manifest.budgets.max_concurrency) * 100
        accepted_documents = tuple(
            output_contracts[output_id].path
            for output_id in node.output_ids
            if output_id in output_contracts
        )
        candidate_attempts.append(
            TaskAttemptObservation(
                branch="candidate",
                task_id=node.node_id,
                attempt_id=attempt.attempt_id,
                state="accepted",
                dependency_task_ids=node.dependencies,
                accepted_output_document_ids=accepted_documents,
                started_ms=start_ms,
                completed_ms=start_ms + 100,
                provider_latency_ms=80,
                usage=UsageMetrics(
                    input_tokens=attempt.usage.input_tokens
                    if attempt.usage is not None
                    else None,
                    output_tokens=attempt.usage.output_tokens
                    if attempt.usage is not None
                    else None,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    provider_calls=1,
                    tool_calls=attempt.usage.tool_calls
                    if attempt.usage is not None
                    else None,
                    tool_bytes=attempt.usage.tool_bytes
                    if attempt.usage is not None
                    else None,
                    repairs=0,
                ),
            )
        )

    result = evaluate_experiment(
        ExperimentEvaluationRequest(
            receipt=EvaluationReceipt(
                "nexus-agentic-aggregate",
                "1",
                _sha256(b"test-evaluator-code"),
                _sha256(canonical_json_bytes(dict(graph.receipt))),
            ),
            required_document_ids=tuple(
                contract.path for contract in output_contracts.values()
            ),
            documents=tuple(document_inputs),
            baseline_run=BranchRunObservation(
                experiment_wall_ms=1_438_756,
                quality=QualityMetrics(
                    historical_score=Decimal("3.84"),
                    scorer_id="legacy-doc-quality",
                    scorer_version="run-44",
                ),
            ),
            candidate_run=BranchRunObservation(
                attempts=tuple(candidate_attempts),
                experiment_wall_ms=500,
                queue_ms=0,
                snapshot_ms=0,
                validation_ms=0,
            ),
        )
    )
    json_report = render_canonical_json_report(result)
    markdown_report = render_markdown_report(result)
    comparison_json = store.write_bytes(
        branch="comparison",
        logical_name="aggregate-evaluation",
        content=json_report.encode("utf-8"),
        relative_path="evaluation.json",
        media_type="application/json",
        kind="evaluation",
    )
    comparison_markdown = store.write_bytes(
        branch="comparison",
        logical_name="aggregate-evaluation-report",
        content=markdown_report.encode("utf-8"),
        relative_path="evaluation.md",
        media_type="text/markdown",
        kind="evaluation_report",
    )
    store.finalize_branch(
        branch="comparison",
        manifest={
            "candidate_manifest_sha256": candidate_final.manifest_sha256,
            "evaluation_sha256": comparison_json.sha256,
            "report_sha256": comparison_markdown.sha256,
            "schema_version": 1,
        },
    )

    assert result.alignment.required_document_ids == tuple(
        sorted(legacy_hashes_before)
    )
    assert all(pair.status == "aligned" for pair in result.alignment.pairs)
    assert result.candidate_output_claims.status == "passed"
    assert result.candidate_attempts.accepted == 12
    assert result.candidate_timing.generation_wall_ms == 500
    assert result.candidate_timing.critical_path_ms == 300
    assert result.candidate_timing.provider_latency_sum_ms == 960
    assert result.comparability.semantic_parity_confirmed is True
    assert result.comparability.semantic_status == "matched"
    assert result.verdict.candidate_verdict == "not_evaluated"
    assert all(
        document.parity.baseline.artifact.sha256
        == document.parity.candidate.artifact.sha256
        for document in result.documents
        if document.parity.baseline.artifact is not None
        and document.parity.candidate.artifact is not None
    )
    assert "legacy-python" not in json_report
    assert "fake-opencode" not in markdown_report
    assert {
        path.name: _sha256(path.read_bytes())
        for path in sorted(LEGACY_DOCS.glob("[0-9][0-9]_*.md"))
    } == legacy_hashes_before
    assert not (KNOWLEDGE_BASE_ROOT / "output" / PROJECT_ID / "docs_staging").exists()
    assert store.experiment_root.is_relative_to(tmp_path)
