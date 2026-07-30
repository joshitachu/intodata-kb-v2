"""Offline vertical proof: run-44 receipt -> fake OpenCode task -> parity report."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest

from mcp_server.experiments.application.evaluate_parity import (
    ParitySide,
    SingleDocumentParityRequest,
    TransientParityMaterial,
    evaluate_single_document_parity,
    fingerprint_output_contract,
)
from mcp_server.experiments.application.execute_task import TaskExecutionService
from mcp_server.experiments.application.import_golden import (
    GoldenImportCommand,
    GoldenParityImporter,
)
from mcp_server.experiments.application.parity_report import (
    render_canonical_json_report,
    render_markdown_report,
)
from mcp_server.experiments.application.preflight import (
    CredentialReferences,
    PaidTaskContract,
    PaidTaskPreflight,
    PinnedOpenCodeRoute,
    RunReservation,
    TaskTokenServerBinding,
    VerifiedRuntimeIdentity,
)
from mcp_server.experiments.domain.evaluation import (
    ArtifactIdentity,
    CostMetrics,
    TimingMetrics,
    UsageMetrics,
)
from mcp_server.experiments.domain.parity import (
    GoldenLineageInput,
    thaw_json,
)
from mcp_server.experiments.domain.runtime import (
    ArtifactReference,
    ModelCapability,
    OutputContract,
    RuntimeTaskStatus,
    RuntimeUsage,
)
from mcp_server.experiments.domain.sources import RedactionState
from mcp_server.experiments.domain.tasks import CompiledTask
from mcp_server.experiments.infrastructure.opencode.runtime import (
    PINNED_OPENCODE_VERSION,
)
from mcp_server.experiments.infrastructure.recipes.compiler import (
    NEXUS_MCP_TOOL_NAMES,
)
from mcp_server.experiments.infrastructure.runtime.fake import (
    FakeAgentRuntime,
    FakeRuntimeScenario,
)
from mcp_server.experiments.infrastructure.storage.canonical_json import (
    canonical_json_bytes,
)
from mcp_server.experiments.infrastructure.validation.cost import (
    parse_frozen_price_card,
)


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
EXPERIMENT_ID = "00000000-0000-4000-8000-000000000006"
TASK_NODE_ID = "document:09_EXECUTIVE_SUMMARY"
DOCUMENT_NAME = "09_EXECUTIVE_SUMMARY.md"
GOLDEN_ROOT = (
    Path(__file__).resolve().parents[3]
    / "output/alitalend/docs/_debug/model_inputs/phase3/016_09_EXECUTIVE_SUMMARY"
)
BASELINE_DOCUMENT = (
    Path(__file__).resolve().parents[3]
    / "output/alitalend/docs/09_EXECUTIVE_SUMMARY.md"
)
GOLDEN_HASHES = {
    "prompt": "3718fb5cbd6d7ecb3a77699c3f6efa536a95145bf9c03c8064830bc58fe12b09",
    "context": "ab7775311a6cec989a07313c16603909fca979dd187a616d4ed0bd677a5a12cf",
    "request": "3b02b0c75f5f222baeed4926a96832977656ec7a3769e8d9666ab84f5f95f1ec",
    "response": "c7c517fb9f906ca1552217942e6ddc996b91f4cc3d6612851facbbd9029195a9",
}


class OfflinePinnedOpenCodeRuntime(FakeAgentRuntime):
    runtime_id = "opencode"
    runtime_version = PINNED_OPENCODE_VERSION


class EchoTaskTokenServer:
    def bind_task(self, **scope: object) -> TaskTokenServerBinding:
        return TaskTokenServerBinding(
            mcp_server_url="http://nexus-internal/experiment-mcp/",
            task_token_ref=str(scope["task_token_ref"]),
            source_roles=scope["source_roles"],  # type: ignore[arg-type]
            subject_ids=scope["subject_ids"],  # type: ignore[arg-type]
            allowed_tools=scope["allowed_tools"],  # type: ignore[arg-type]
            reservation=scope["reservation"],  # type: ignore[arg-type]
            output_contract=scope["output_contract"],  # type: ignore[arg-type]
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(canonical_json_bytes(value))


@pytest.mark.asyncio
async def test_real_golden_task_runs_through_offline_runtime_and_parity_report() -> None:
    output_contract = OutputContract(
        DOCUMENT_NAME,
        "text/markdown",
        max_bytes=2 * 1024 * 1024,
    )
    golden = GoldenParityImporter().import_bundle(
        command=GoldenImportCommand(
            task_id="09_EXECUTIVE_SUMMARY",
            allowed_root=GOLDEN_ROOT,
            prompt_relative_path=Path("prompt.md"),
            context_relative_path=Path("context_bundle.md"),
            request_relative_path=Path("request.json"),
            response_relative_path=Path("response.json"),
            output_contract=asdict(output_contract),
            expected_sha256=GOLDEN_HASHES,
            lineage=GoldenLineageInput(
                baseline_run_id=44,
                run_model_route=(
                    "deepseek/deepseek-v4-pro @ https://api.deepseek.com/v1"
                ),
                resume_requested=True,
            ),
        )
    )
    baseline_document_bytes = BASELINE_DOCUMENT.read_bytes()
    baseline_document_sha256 = _sha256_bytes(baseline_document_bytes)
    output_ref = ArtifactReference(
        DOCUMENT_NAME,
        baseline_document_sha256,
        "text/markdown",
        len(baseline_document_bytes),
    )
    price_card = parse_frozen_price_card(
        {
            "schema_version": "1",
            "price_card_id": "offline-test-only",
            "version": "1",
            "currency": "USD",
            "rates": [
                {
                    "provider_id": "deepseek",
                    "model_id": "deepseek-v4-pro",
                    "input_usd_per_million": "1",
                    "output_usd_per_million": "1",
                    "cache_read_usd_per_million": "1",
                    "cache_write_usd_per_million": "1",
                }
            ],
        }
    )
    runtime = OfflinePinnedOpenCodeRuntime(
        scenarios={
            TASK_NODE_ID: FakeRuntimeScenario(
                outcome=RuntimeTaskStatus.SUCCEEDED,
                output_refs=(output_ref,),
                usage=RuntimeUsage(
                    input_tokens=70000,
                    output_tokens=5000,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                ),
                elapsed_ms=100000,
                provider_latency_ms=90000,
            )
        }
    )
    runtime_identity = VerifiedRuntimeIdentity.from_capabilities(
        runtime_id="opencode",
        runtime_version=PINNED_OPENCODE_VERSION,
        checked_at=NOW,
        models=(
            ModelCapability(
                "deepseek",
                "deepseek-v4-pro",
                frozenset({"tools"}),
            ),
        ),
    )
    tools = tuple(sorted(NEXUS_MCP_TOOL_NAMES))
    contract = PaidTaskContract(
        project_id="alitalend",
        experiment_id=EXPERIMENT_ID,
        task=CompiledTask(
            node_id=TASK_NODE_ID,
            task_type="synthesis",
            role="writer",
            dependencies=("analysis",),
            output_ids=("09_EXECUTIVE_SUMMARY",),
            semantic_prompt=golden.receipt.request.semantic_prompt,
            dependency_output_refs=("analysis",),
        ),
        output_contract=output_contract,
        context_refs=(
            ArtifactReference(
                "golden-context",
                GOLDEN_HASHES["context"],
                "text/markdown",
                len(golden.artifacts["context"]),
            ),
        ),
        source_roles=("implementation",),
        subject_ids=("all",),
        recipe_allowed_tools=tools,
        allowed_tools=tools,
        route=PinnedOpenCodeRoute(
            "opencode",
            PINNED_OPENCODE_VERSION,
            "deepseek",
            "deepseek-v4-pro",
        ),
        credentials=CredentialReferences(
            "offline-task-token-ref",
            "offline-opencode-password-ref",
            "offline-provider-credential-ref",
        ),
        runtime_identity=runtime_identity,
        price_card=price_card,
        reservation=RunReservation(
            max_input_tokens=100000,
            max_output_tokens=10000,
            max_elapsed_ms=120000,
            max_cost_usd=Decimal("1"),
            max_tool_calls=25,
            max_tool_read_bytes=1024 * 1024,
            max_tool_result_bytes=1024 * 1024,
            max_tool_write_bytes=2 * 1024 * 1024,
        ),
        deadline_at=NOW + timedelta(minutes=10),
        expected_mcp_server_url="http://nexus-internal/experiment-mcp/",
        max_runtime_identity_age=timedelta(minutes=1),
        experiment_enabled=True,
        paid_execution_enabled=True,
        redaction_state=RedactionState.APPROVED,
        security_approved=True,
    )
    executed = await TaskExecutionService(
        runtime=runtime,
        preflight=PaidTaskPreflight(
            task_token_server=EchoTaskTokenServer(),
            now=lambda: NOW,
        ),
    ).execute(contract=contract, execution_id="offline-e2e")

    context_sha256 = GOLDEN_HASHES["context"]
    source_sha256 = _sha256_json(
        {"context_sha256": context_sha256, "source_role": "implementation"}
    )
    model_options_sha256 = _sha256_json(
        thaw_json(golden.receipt.request.model_options)
    )
    baseline_runtime_envelope = {
        "runtime": "legacy-python",
        "model": golden.receipt.request.canonical_model_route,
    }
    candidate_runtime_envelope = {
        "runtime": f"opencode/{PINNED_OPENCODE_VERSION}",
        "model": golden.receipt.request.canonical_model_route,
    }
    result = evaluate_single_document_parity(
        SingleDocumentParityRequest(
            document_id=DOCUMENT_NAME,
            output_contract=output_contract,
            baseline=ParitySide(
                artifact=ArtifactIdentity(
                    "baseline",
                    "document",
                    DOCUMENT_NAME,
                    baseline_document_sha256,
                    len(baseline_document_bytes),
                ),
                context_manifest_sha256=context_sha256,
                source_snapshot_sha256=source_sha256,
                output_contract_sha256=golden.receipt.request.output_contract_sha256,
                model_route_options_sha256=model_options_sha256,
                timing=TimingMetrics(
                    experiment_wall_ms=golden.receipt.baseline_usage.latency_ms,
                    provider_latency_sum_ms=golden.receipt.baseline_usage.latency_ms,
                ),
                usage=UsageMetrics(
                    input_tokens=golden.receipt.baseline_usage.input_tokens,
                    output_tokens=golden.receipt.baseline_usage.output_tokens,
                    cache_read_tokens=(
                        golden.receipt.baseline_usage.input_cache_read_tokens
                    ),
                    provider_calls=1,
                    retries=golden.receipt.baseline_usage.retries,
                ),
            ),
            candidate=ParitySide(
                artifact=ArtifactIdentity(
                    "candidate",
                    "document",
                    DOCUMENT_NAME,
                    output_ref.sha256,
                    output_ref.byte_size or 0,
                ),
                context_manifest_sha256=context_sha256,
                source_snapshot_sha256=source_sha256,
                output_contract_sha256=fingerprint_output_contract(
                    output_contract
                ),
                model_route_options_sha256=model_options_sha256,
                timing=TimingMetrics(
                    experiment_wall_ms=executed.usage.elapsed_ms,
                    provider_latency_sum_ms=(
                        executed.result.timings.provider_latency_ms
                    ),
                ),
                usage=UsageMetrics(
                    input_tokens=executed.usage.input_tokens,
                    output_tokens=executed.usage.output_tokens,
                    cache_read_tokens=executed.usage.cache_read_tokens,
                    cache_write_tokens=executed.usage.cache_write_tokens,
                    provider_calls=1,
                    tool_calls=executed.result.tool_calls,
                    tool_bytes=executed.result.tool_bytes,
                    retries=executed.result.retry_count,
                ),
                cost=CostMetrics(
                    billed_cost_usd=executed.usage.billed_cost_usd,
                    normalized_cost_usd=executed.usage.normalized_cost_usd,
                    price_card=executed.preflight.price_card,
                ),
            ),
            baseline_transient=TransientParityMaterial(
                semantic_prompt=golden.receipt.request.semantic_prompt,
                runtime_envelope=baseline_runtime_envelope,
            ),
            candidate_transient=TransientParityMaterial(
                semantic_prompt=golden.receipt.request.semantic_prompt,
                runtime_envelope=candidate_runtime_envelope,
            ),
        )
    )
    json_report = render_canonical_json_report(result)
    markdown_report = render_markdown_report(result)

    assert len(runtime.started_requests) == 1
    assert result.baseline_contract.status == "passed"
    assert result.candidate_contract.status == "passed"
    assert result.comparability.semantic_parity_confirmed is True
    assert result.comparability.runtime_status == "different"
    assert result.candidate.artifact is not None
    assert result.candidate.artifact.sha256 == result.baseline.artifact.sha256  # type: ignore[union-attr]
    assert result.candidate.cost.normalized_cost_usd == Decimal("0.075")
    assert result.baseline.cost.normalized_cost_usd is None
    assert "legacy-python" not in json_report
    assert "opencode/" not in json_report
    assert "Semantic parity confirmed: **yes**" in markdown_report
