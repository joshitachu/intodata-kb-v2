"""Full offline logistics A/B proof using governed fixture sources only."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from mcp_server.experiments.application.schedule_graph import ExperimentGraphScheduler
from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    EvaluationReceipt,
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
from mcp_server.experiments.domain.sources import SourceDescriptor, SourceLimits, SourceSnapshot
from mcp_server.experiments.infrastructure.recipes.compiler import compile_recipe
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe
from mcp_server.experiments.infrastructure.sources.governed_directory import (
    DirectoryEvidenceAdapter,
)
from mcp_server.experiments.infrastructure.sources.registry import SourceAdapterRegistry
from mcp_server.experiments.infrastructure.sources.runtime_export import RuntimeExportAdapter
from mcp_server.experiments.infrastructure.sources.tabular import TabularFileAdapter
from mcp_server.experiments.infrastructure.storage.artifact_store import (
    ArtifactStore,
    StoredArtifact,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import canonical_json_bytes
from mcp_server.experiments.infrastructure.validation.structural import measure_markdown


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
EXPERIMENT_ID = "00000000-0000-4000-8000-000000000011"
PROJECT_ID = "logistics-operations"
MCP_ROOT = Path(__file__).parents[2]
RECIPE_ROOT = MCP_ROOT / "data" / "agentic_recipes" / "logistics_operations"
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "agentic_experiments" / "logistics_operations"
LEGACY_DOCS = MCP_ROOT / "output" / PROJECT_ID / "docs"
LEGACY_STAGING = MCP_ROOT / "output" / PROJECT_ID / "docs_staging"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): _sha256(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _source_snapshot():
    """Exercise every certified logistics source role through trusted adapters."""

    descriptors = (
        SourceDescriptor(
            id="operational-policy",
            source_kind="directory_evidence",
            source_role="operational_policy",
            source_path=FIXTURE_ROOT / "warehouse_transport_sop.md",
            provenance_locator="fixture://logistics/sop",
            capabilities=frozenset({"searchable_text"}),
            trust_level="governed_fixture",
        ),
        SourceDescriptor(
            id="shipment-events",
            source_kind="tabular_file",
            source_role="shipment_events",
            source_path=FIXTURE_ROOT / "shipments.csv",
            provenance_locator="fixture://logistics/shipments",
            capabilities=frozenset({"structured_records", "tabular_data"}),
            trust_level="governed_fixture",
        ),
        SourceDescriptor(
            id="mapping-reference",
            source_kind="tabular_file",
            source_role="mapping_reference",
            source_path=FIXTURE_ROOT / "status_mapping.csv",
            provenance_locator="fixture://logistics/status-mapping",
            capabilities=frozenset({"mapping_reference", "tabular_data"}),
            trust_level="governed_fixture",
        ),
        SourceDescriptor(
            id="runtime-observation",
            source_kind="runtime_export",
            source_role="runtime_observation",
            source_path=FIXTURE_ROOT / "runtime_events.jsonl",
            provenance_locator="fixture://logistics/runtime",
            capabilities=frozenset({"runtime_events", "timestamped_records"}),
            trust_level="governed_fixture",
        ),
    )
    registry = SourceAdapterRegistry(
        (
            DirectoryEvidenceAdapter(allowed_roots=(FIXTURE_ROOT,)),
            TabularFileAdapter(),
            RuntimeExportAdapter(),
        )
    )
    # Tabular and runtime adapters intentionally extract row/record JSON from their
    # CSV/JSONL containers.  Build the immutable snapshot from that approved
    # extracted representation, preserving the actual extracted media type.
    results = [
        registry.resolve(descriptor).snapshot(descriptor, SourceLimits())
        for descriptor in descriptors
    ]
    contents = tuple(content for result in results for content in result.contents)
    content_media_types = {content.item_id: content.media_type for content in contents}
    return SourceSnapshot.build(
        snapshot_id="logistics-full-fake",
        items=tuple(
            replace(item, media_type=content_media_types[item.id])
            for result in results
            for item in result.items
        ),
        contents=contents,
    )


class FullLogisticsFakeExecutor:
    """Offline runtime that writes only deterministic candidate artifacts."""

    def __init__(self, *, graph, manifest, snapshot, store: ArtifactStore) -> None:
        self._nodes = {node.node_id: node for node in graph.nodes}
        self._contracts = {contract.id: contract for contract in manifest.output_contracts}
        self._snapshot = snapshot
        self._store = store
        self.calls: list[TaskLease] = []
        self.documents: dict[str, StoredArtifact] = {}

    async def execute(self, lease: TaskLease) -> TaskExecutionOutcome:
        self.calls.append(lease)
        node = self._nodes[lease.task_id]
        for output_id in node.output_ids:
            contract = self._contracts[output_id]
            content = self._content(contract.id)
            stored = self._store.write_bytes(
                branch="candidate",
                logical_name=contract.path,
                content=content,
                relative_path=contract.path,
                media_type=contract.media_type,
                kind="document" if contract.media_type == "text/markdown" else "task_receipt",
            )
            if contract.media_type == "text/markdown":
                self.documents[output_id] = stored
        return TaskExecutionOutcome(
            succeeded=True,
            outputs=node.output_ids,
            usage=GraphUsage(
                input_tokens=1_000,
                output_tokens=100,
                tool_calls=2,
                tool_bytes=1_024,
                elapsed_ms=100,
            ),
        )

    def _content(self, output_id: str) -> bytes:
        if output_id == "operations_runbook":
            return (
                "# Operations\n\n"
                "Transport operations owns dispatch and carrier handoff.\n\n"
                f"Evidence snapshot: `{self._snapshot.manifest_sha256}`.\n"
            ).encode()
        if output_id == "exception_inventory":
            return (
                "# Exceptions\n\n"
                "SHP-101 has an address-mismatch exception; warehouse operations owns closure.\n\n"
                f"Evidence snapshot: `{self._snapshot.manifest_sha256}`.\n"
            ).encode()
        return canonical_json_bytes(
            {
                "node_output": output_id,
                "offline_fake": True,
                "snapshot_sha256": self._snapshot.manifest_sha256,
            }
        )


@pytest.mark.asyncio
async def test_logistics_full_fake_ab_proof_is_isolated_and_aggregate_evaluable(tmp_path: Path) -> None:
    docs_existed = LEGACY_DOCS.exists()
    staging_existed = LEGACY_STAGING.exists()
    legacy_docs_before = _tree_hashes(LEGACY_DOCS)
    legacy_staging_before = _tree_hashes(LEGACY_STAGING)
    snapshot = _source_snapshot()
    assert {item.source_role for item in snapshot.items} == {
        "operational_policy", "shipment_events", "mapping_reference", "runtime_observation"
    }
    assert all(item.redaction_state.value == "approved" for item in snapshot.items)

    manifest = load_recipe(RECIPE_ROOT / "recipe.yaml")
    graph = compile_recipe(manifest, recipe_root=RECIPE_ROOT, model_capabilities=frozenset({"tools"}))
    assert len(graph.nodes) == 4
    scheduled = ScheduledGraph(
        graph_id=graph.sha256,
        tasks=tuple(
            ScheduledTask(
                task_id=node.node_id,
                dependencies=node.dependencies,
                required_outputs=node.output_ids,
                max_attempts=manifest.budgets.max_attempts,
                reservation=GraphUsage(input_tokens=2_000, output_tokens=200, tool_calls=4, tool_bytes=2_048, elapsed_ms=200),
            )
            for node in graph.nodes
        ),
        max_concurrency=manifest.budgets.max_concurrency,
        max_attempts=manifest.budgets.max_attempts,
        budgets=GraphBudgetLimits(
            max_input_tokens=100_000, max_output_tokens=20_000, max_tool_calls=100,
            max_tool_bytes=1_000_000, max_elapsed_ms=10_000,
        ),
    )
    store = ArtifactStore(output_root=tmp_path / "output", project_id=PROJECT_ID, experiment_id=EXPERIMENT_ID)
    scheduler = ExperimentGraphScheduler(scheduled)
    executor = FullLogisticsFakeExecutor(graph=graph, manifest=manifest, snapshot=snapshot, store=store)
    batches = 0
    while scheduler.run_status() not in {SchedulerRunStatus.SUCCEEDED, SchedulerRunStatus.FAILED}:
        completed = await scheduler.execute_ready(
            executor=executor,
            worker_id="offline-logistics-fake",
            now=NOW + timedelta(seconds=batches),
            lease_ttl=timedelta(minutes=1),
        )
        assert completed
        batches += 1

    assert scheduler.run_status() == SchedulerRunStatus.SUCCEEDED
    assert scheduler.required_outputs_present()
    assert len(executor.calls) == len(graph.nodes) == 4
    assert batches == 3
    candidate_final = store.finalize_branch(
        branch="candidate",
        manifest={"document_count": len(executor.documents), "graph_sha256": graph.sha256, "publishes": False, "schema_version": 1},
    )

    document_contracts = {contract.id: contract for contract in manifest.output_contracts if contract.media_type == "text/markdown"}
    graph_nodes = {node.node_id: node for node in graph.nodes}
    document_inputs: list[DocumentEvaluationInput] = []
    for output_id, contract in sorted(document_contracts.items()):
        candidate = executor.documents[output_id]
        content = (store.experiment_root / candidate.relative_path).read_bytes()
        runtime_contract = OutputContract(contract.path, contract.media_type, max_bytes=len(content))
        node = next(node for node in graph.nodes if output_id in node.output_ids)
        common = {
            "context_manifest_sha256": graph.receipt["effective_scope_sha256"],
            "source_snapshot_sha256": snapshot.manifest_sha256,
            "output_contract_sha256": fingerprint_output_contract(runtime_contract),
            "model_route_options_sha256": _sha256(b"offline-logistics-fake-options"),
        }
        identity = ArtifactIdentity("candidate", "document", contract.path, candidate.sha256, candidate.size_bytes)
        document_inputs.append(
            DocumentEvaluationInput(
                parity=SingleDocumentParityRequest(
                    document_id=contract.path,
                    output_contract=runtime_contract,
                    baseline=ParitySide(artifact=ArtifactIdentity("baseline", "document", contract.path, candidate.sha256, candidate.size_bytes), **common),
                    candidate=ParitySide(artifact=identity, **common),
                    baseline_transient=TransientParityMaterial(semantic_prompt=node.semantic_prompt, runtime_envelope={"runtime": "offline-baseline"}),
                    candidate_transient=TransientParityMaterial(semantic_prompt=node.semantic_prompt, runtime_envelope={"runtime": "offline-fake"}),
                ),
                baseline_structure=measure_markdown(content.decode(), required_sections=contract.required_sections),
                candidate_structure=measure_markdown(content.decode(), required_sections=contract.required_sections),
            )
        )

    attempts: list[TaskAttemptObservation] = []
    for node_id in graph.topological_order:
        node = graph_nodes[node_id]
        attempt = scheduler.task_state(node_id).attempts[-1]
        start_ms = 0 if node_id == "evidence" else 100 if node_id == "analysis" else 200
        attempts.append(
            TaskAttemptObservation(
                branch="candidate", task_id=node_id, attempt_id=attempt.attempt_id, state="accepted",
                dependency_task_ids=node.dependencies,
                accepted_output_document_ids=tuple(document_contracts[output_id].path for output_id in node.output_ids if output_id in document_contracts),
                started_ms=start_ms, completed_ms=start_ms + 100, provider_latency_ms=80,
                usage=UsageMetrics(
                    input_tokens=attempt.usage.input_tokens if attempt.usage else None,
                    output_tokens=attempt.usage.output_tokens if attempt.usage else None,
                    cache_read_tokens=0, cache_write_tokens=0, provider_calls=1,
                    tool_calls=attempt.usage.tool_calls if attempt.usage else None,
                    tool_bytes=attempt.usage.tool_bytes if attempt.usage else None, repairs=0,
                ),
            )
        )

    result = evaluate_experiment(
        ExperimentEvaluationRequest(
            receipt=EvaluationReceipt("nexus-agentic-aggregate", "1", _sha256(b"logistics-test-evaluator"), _sha256(canonical_json_bytes(dict(graph.receipt)))),
            required_document_ids=tuple(contract.path for contract in document_contracts.values()),
            documents=tuple(document_inputs),
            baseline_run=BranchRunObservation(experiment_wall_ms=600),
            candidate_run=BranchRunObservation(attempts=tuple(attempts), experiment_wall_ms=300, queue_ms=0, snapshot_ms=0, validation_ms=0),
        )
    )
    json_report = render_canonical_json_report(result)
    markdown_report = render_markdown_report(result)
    comparison_json = store.write_bytes(branch="comparison", logical_name="aggregate-evaluation", content=json_report.encode(), relative_path="evaluation.json", media_type="application/json", kind="evaluation")
    comparison_markdown = store.write_bytes(branch="comparison", logical_name="aggregate-evaluation-report", content=markdown_report.encode(), relative_path="evaluation.md", media_type="text/markdown", kind="evaluation_report")
    store.finalize_branch(
        branch="comparison",
        manifest={"candidate_manifest_sha256": candidate_final.manifest_sha256, "evaluation_sha256": comparison_json.sha256, "report_sha256": comparison_markdown.sha256, "schema_version": 1},
    )

    expected_documents = tuple(sorted(contract.path for contract in document_contracts.values()))
    assert tuple(sorted(path for attempt in attempts for path in attempt.accepted_output_document_ids)) == expected_documents
    assert len({attempt.task_id for attempt in attempts}) == len(attempts) == 4
    assert result.candidate_output_claims.status == "passed"
    assert result.candidate_attempts.accepted == 4
    assert result.candidate_timing.generation_wall_ms == 300
    assert result.candidate_timing.critical_path_ms == 300
    assert result.candidate_timing.provider_latency_sum_ms == 320
    assert result.candidate_usage.input_tokens == 4_000
    assert result.candidate_usage.output_tokens == 400
    assert result.candidate_usage.provider_calls == 4
    assert result.candidate_usage.tool_calls == 8
    assert result.candidate_usage.tool_bytes == 4_096
    assert result.comparability.semantic_parity_confirmed is True
    assert all(pair.status == "aligned" for pair in result.alignment.pairs)
    assert {artifact.branch for artifact in executor.documents.values()} == {"candidate"}
    assert (store.experiment_root / "candidate" / ".finalized.json").is_file()
    assert (store.experiment_root / "comparison" / ".finalized.json").is_file()
    assert "offline-fake" not in json_report
    assert "offline-fake" not in markdown_report
    assert LEGACY_DOCS.exists() is docs_existed
    assert LEGACY_STAGING.exists() is staging_existed
    assert _tree_hashes(LEGACY_DOCS) == legacy_docs_before
    assert _tree_hashes(LEGACY_STAGING) == legacy_staging_before
    assert store.experiment_root.is_relative_to(tmp_path)
