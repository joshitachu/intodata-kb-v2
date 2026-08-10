from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from mcp_server.experiments.application.analysis_batch_planner import (
    AnalysisBatchPlanner,
)
from mcp_server.experiments.application.fact_ledger import compile_fact_ledger
from mcp_server.experiments.application.grounded_pack import (
    GroundedExecutionPack,
    GroundedRole,
    expand_grounded_fanout,
    load_grounded_execution_pack,
)
from mcp_server.experiments.application.grounded_runtime import (
    BOUNDED_BUDGET_MONITORING_CAPABILITY,
    GroundedRoleRuntimeContext,
)
from mcp_server.experiments.application.run_quick_candidate import (
    QuickCandidateError,
)
from mcp_server.experiments.application.run_staged_grounded_candidate import (
    RESUME_STATE_NAME,
    GroundedOpenCodeRunner,
    _UnitEvidencePlan,
)
from mcp_server.experiments.infrastructure.opencode.client import (
    OpenCodeHttpClient,
)
from mcp_server.experiments.infrastructure.opencode.runtime import (
    OpenCodeAgentRuntime,
    OpenCodeGroundedTaskBuilder,
)
from mcp_server.experiments.infrastructure.runtime.fake import (
    FakeAgentRuntime,
    FakeRuntimeScenario,
    FakeToolCall,
)
from mcp_server.experiments.domain.runtime import (
    ArtifactReference,
    CancelResult,
    ModelCapability,
    OutputContract,
    RuntimeBudgets,
    RuntimeEventCategory,
    RuntimeTaskEvent,
    RuntimeTaskRequest,
    RuntimeTaskStatus,
    RuntimeUsage,
)
from mcp_server.experiments.infrastructure.recipes.loader import load_recipe


def _pack() -> GroundedExecutionPack:
    root = Path(__file__).parents[2] / "data" / "agentic_recipes"
    return load_grounded_execution_pack(load_recipe(root / "logistics_operations" / "recipe.yaml"))


def _pack_with_topology() -> GroundedExecutionPack:
    pack = _pack()
    topology = GroundedRole(
        id="business-topology",
        agent="nexus-doc-topology",
        stage="topology",
        owns=("candidate/topology/business_topology.json",),
        depends_on=("operations-analysis",),
        source_globs=(
            "role_inputs/business-topology/**",
            "candidate/intermediates/**",
            "candidate/orchestration/evidence_packets/**",
        ),
        max_context_bytes=3 * 1024 * 1024,
        failure_policy="warn",
    )
    return replace(
        pack,
        roles=(*pack.roles[:-1], topology, pack.roles[-1]),
        max_parallelism=2,
        max_context_bytes=16 * 1024 * 1024,
    )


def _complete_message() -> list[dict[str, object]]:
    return [
        {
            "info": {
                "role": "assistant",
                "time": {"completed": 1},
                "tokens": {"input": 10, "output": 5},
                "cost": 0.001,
            },
            "parts": [
                {"type": "tool", "tool": "read"},
                {"type": "step-finish", "reason": "stop"},
            ],
        }
    ]


def _write_role_outputs(
    root: Path,
    role_id: str,
    *,
    unsafe_evidence: bool = False,
) -> None:
    if role_id == "shipment-evidence":
        path = root / "candidate/orchestration/evidence_packets/shipments.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            (
                '{"claims":[],"clientSecret":"unsafe-test-marker"}'
                if unsafe_evidence
                else '{"claims":[]}'
            ),
            encoding="utf-8",
        )
    elif role_id == "operations-analysis":
        path = root / "candidate/intermediates/operations_analysis.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Analysis\n\nGrounded static analysis.\n", encoding="utf-8")
    elif role_id == "operations-writer":
        for name, title in (
            ("operations_runbook.md", "Operations"),
            ("exception_inventory.md", "Exceptions"),
        ):
            path = root / "candidate/docs" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"# {title}\n\nGrounded in the frozen source set.\n",
                encoding="utf-8",
            )
    elif role_id == "operations-reviewer":
        path = root / "candidate/orchestration/review_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                (
                    "## Factual review",
                    "Passed.",
                    "## Coverage review",
                    "Passed.",
                    "## Cross-document consistency",
                    "Passed.",
                    "## Secret and evidence-path review",
                    "Passed.",
                    "## Resolved findings",
                    "None.",
                    "## Final verdict",
                    "Pass.",
                )
            ),
            encoding="utf-8",
        )


def _scanner_sources(tmp_path: Path) -> tuple[tuple[str, Path], ...]:
    def write(name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    job = write(
        "shipment-planner.json",
        {
            "name": "ShipmentPlanner",
            "group": "Logistics",
            "source_root": "LOGISTICS",
            "path": "ShipmentPlanner_0.1.item",
            "source_design": "routes",
            "is_esb_route": True,
            "components": [{"component_type": "cJMS"}],
            "connections": [{}],
        },
    )
    files_index = write(
        "files-index.json",
        [{"relative_path": "LOGISTICS/ShipmentPlanner_0.1.item"}],
    )
    meta = write(
        "meta.json",
        {
            "platform": "talend",
            "stats": {"job_count": 0, "route_count": 1},
        },
    )
    redaction = write(
        "redaction.json",
        {
            "redaction_level": "strict",
            "total_redactions": 0,
            "summary": {},
        },
    )
    return (
        ("scan:scan/jobs/Logistics/ShipmentPlanner.json", job),
        ("scan:scan/files_index.json", files_index),
        ("scan:scan/meta.json", meta),
        ("scan:redaction_report.json", redaction),
    )


def _topology_sources(tmp_path: Path) -> tuple[tuple[str, Path], ...]:
    root = tmp_path / "frozen-scan"
    (root / "scan").mkdir(parents=True)
    scan_result = root / "scan_result.json"
    scan_result.write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    {
                        "name": "FrozenShipmentPlanner",
                        "job_id": "FrozenShipmentPlanner",
                        "source_root": "LOGISTICS",
                        "path": "Routes/FrozenShipmentPlanner.item",
                        "artifact_kind": "route",
                        "artifact_role": "execution",
                        "components": [
                            {
                                "unique_name": "cJMS_1",
                                "component_type": "cJMS",
                                "params": [],
                            }
                        ],
                    }
                ],
                "metadata_connections": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = root / "scan" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_roots": [{"id": "LOGISTICS", "project_id": "logistics"}],
                "artifact_relations": [],
                "artifact_resources": [],
                "artifact_endpoints": [
                    {
                        "artifact": "FrozenShipmentPlanner",
                        "endpoint_type": "jms",
                        "direction": "output",
                        "component_name": "cJMS_1",
                    }
                ],
                "artifact_channels": [],
            }
        ),
        encoding="utf-8",
    )
    lineage = root / "scan" / "lineage.json"
    lineage.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "job_name": "FrozenShipmentPlanner",
                        "source_table": "Shipment",
                        "source_column": "ShipmentId",
                        "target_table": "OutboundShipment",
                        "target_column": "ShipmentId",
                        "confidence": "certain",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    landscape = root / "scan" / "landscape.json"
    landscape.write_text(
        json.dumps(
            {
                "systems": [
                    {
                        "id": "system:shipment-source",
                        "name": "Shipment source",
                        "kind": "data_system",
                        "source_roots": ["LOGISTICS"],
                    },
                    {
                        "id": "system:shipment-destination",
                        "name": "Shipment destination",
                        "kind": "external_service",
                        "source_roots": ["LOGISTICS"],
                    },
                ],
                "applications": [],
                "edges": [
                    {
                        "source": "system:shipment-source",
                        "target": "system:shipment-destination",
                        "dependency_type": "data",
                        "mechanism": "jms",
                        "direction": "out",
                        "provenance": [{"artifact_id": "FrozenShipmentPlanner"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    redaction = root / "redaction_report.json"
    redaction.write_text(
        json.dumps(
            {
                "redaction_level": "strict",
                "total_redactions": 0,
                "summary": {},
            }
        ),
        encoding="utf-8",
    )
    return (
        ("current-intodoc-scan:scan_result.json", scan_result),
        ("current-intodoc-scan:scan/manifest.json", manifest),
        ("current-intodoc-scan:scan/lineage.json", lineage),
        ("current-intodoc-scan:scan/landscape.json", landscape),
        ("current-intodoc-scan:redaction_report.json", redaction),
    )


def _write_valid_topology_output(role_root: Path) -> None:
    topology_root = role_root / "topology"
    packet_manifest = json.loads(
        (topology_root / "packet_manifest.json").read_text(encoding="utf-8")
    )
    artifact = json.loads(
        (topology_root / "evidence" / "artifacts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    identities = [
        json.loads(line)
        for line in (topology_root / "evidence" / "systems.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    edge = json.loads(
        (topology_root / "evidence" / "edges.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    phase2 = next(
        json.loads(line)
        for line in (topology_root / "evidence" / "supporting_artifacts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["type"] == "phase2_analysis"
    )
    payload = {
        "schema_version": "business-topology-v1",
        "scan_fingerprint": packet_manifest["scan_fingerprint"],
        "metadata": {
            "project_id": "logistics",
            "scan_fingerprint": packet_manifest["scan_fingerprint"],
        },
        "systems": [
            {
                "id": "system:source",
                "name": "Shipment source",
                "status": "observed",
                "confidence": 0.9,
                "evidence_ids": [artifact["id"], phase2["id"], identities[0]["id"]],
            },
            {
                "id": "system:destination",
                "name": "Shipment destination",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [artifact["id"], identities[1]["id"]],
            },
        ],
        "data_streams": [
            {
                "id": "stream:shipments",
                "name": "Shipment transfer",
                "status": "inferred",
                "confidence": 0.7,
                "evidence_ids": [edge["id"]],
                "source_system_id": "system:source",
                "destination_system_id": "system:destination",
                "business_summary": "Transfers shipment information to its destination.",
                "artifact_ids": [artifact["id"]],
            }
        ],
        "coverage": {
            "total_artifacts": 0,
            "cited_artifacts": 0,
            "total_evidence": 0,
            "cited_evidence": 0,
            "coverage_ratio": 0,
        },
    }
    output = role_root / "candidate" / "topology" / "business_topology.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload), encoding="utf-8")


def _client(
    handler: httpx.MockTransport,
) -> tuple[OpenCodeAgentRuntime, httpx.AsyncClient]:
    async def dispatch(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/provider":
            return httpx.Response(
                200,
                json={
                    "connected": ["provider-a"],
                    "all": [{"id": "provider-a", "models": {"model-b": {}}}],
                },
            )
        if request.url.path == "/event":
            return httpx.Response(503, json={"error": "event stream unavailable"})
        return await handler.handle_async_request(request)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
    return (
        OpenCodeAgentRuntime(
            OpenCodeHttpClient(
                base_url="http://opencode.test",
                username="opencode",
                password="test-password",
                client=http_client,
            )
        ),
        http_client,
    )


def _wait_request(*, timeout_seconds: float) -> RuntimeTaskRequest:
    return RuntimeTaskRequest(
        experiment_id="wait-test",
        task_id="grounded-role:evidence:attempt:1",
        idempotency_key="wait-test:evidence:1",
        provider_id="provider-a",
        model_id="model-b",
        semantic_prompt="bounded role",
        semantic_prompt_sha256="a" * 64,
        context_refs=(),
        output_contract=OutputContract("role-bundle", "application/octet-stream"),
        allowed_tool_names=("read",),
        budgets=RuntimeBudgets(max_tool_calls=10, max_elapsed_ms=1000),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds),
    )


def _wait_runner(tmp_path: Path, runtime: FakeAgentRuntime, *, timeout_seconds: float):
    return GroundedOpenCodeRunner(
        runtime=runtime,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=tmp_path / "workspaces",
        execution_pack=_pack(),
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_terminal_result_does_not_wait_for_an_open_event_stream(tmp_path: Path) -> None:
    class OpenStreamRuntime(FakeAgentRuntime):
        observer_cancelled = False

        def observe(self, handle, *, after_sequence=0):
            return self._open_stream(handle, after_sequence=after_sequence)

        async def _open_stream(self, handle, *, after_sequence):
            try:
                async for event in super().observe(handle, after_sequence=after_sequence):
                    yield event
                await asyncio.Event().wait()
            finally:
                self.observer_cancelled = True

    runtime = OpenStreamRuntime()
    request = _wait_request(timeout_seconds=1)
    handle = await runtime.start_task(request)
    result, events = await asyncio.wait_for(
        _wait_runner(tmp_path, runtime, timeout_seconds=1)._wait(handle, request=request),
        timeout=0.25,
    )

    assert result.status == RuntimeTaskStatus.SUCCEEDED
    assert events
    assert runtime.observer_cancelled is True


@pytest.mark.asyncio
async def test_terminal_opencode_result_closes_a_hanging_global_sse_stream(
    tmp_path: Path,
) -> None:
    class HangingSSE(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            await asyncio.Event().wait()
            if False:  # pragma: no cover - makes this an async byte iterator
                yield b""

        async def aclose(self) -> None:
            self.closed = True

    stream = HangingSSE()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            return httpx.Response(
                200,
                json={
                    "connected": ["provider-a"],
                    "all": [{"id": "provider-a", "models": {"model-b": {}}}],
                },
            )
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "session-open-stream"})
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        if request.url.path == "/event":
            return httpx.Response(200, stream=stream)
        raise AssertionError(request.url.path)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runtime = OpenCodeAgentRuntime(
        OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-password",
            client=http_client,
        )
    )
    request = _wait_request(timeout_seconds=1)
    handle = await runtime.start_task(request)
    result, _ = await asyncio.wait_for(
        _wait_runner(tmp_path, runtime, timeout_seconds=1)._wait(handle, request=request),
        timeout=0.25,
    )
    await http_client.aclose()

    assert result.status == RuntimeTaskStatus.SUCCEEDED
    assert stream.closed is True


@pytest.mark.asyncio
async def test_live_opencode_usage_breach_cancels_before_sse_closure(
    tmp_path: Path,
) -> None:
    class UsageThenHangingSSE(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            yield (
                b'data: {"type":"usage","properties":'
                b'{"sessionID":"session-live-budget","id":"usage-over",'
                b'"tokens":{"input":11,"output":1}}}\n\n'
            )
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.closed = True

    stream = UsageThenHangingSSE()
    aborted = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal aborted
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/provider":
            return httpx.Response(
                200,
                json={
                    "connected": ["provider-a"],
                    "all": [{"id": "provider-a", "models": {"model-b": {}}}],
                },
            )
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "session-live-budget"})
        if request.url.path.endswith("/prompt_async"):
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=[])
        if request.url.path == "/session/status":
            return httpx.Response(
                200,
                json={"session-live-budget": {"type": "busy"}},
            )
        if request.url.path == "/event":
            return httpx.Response(200, stream=stream)
        if request.url.path.endswith("/abort"):
            aborted = True
            return httpx.Response(200, json={"status": "aborted"})
        raise AssertionError(request.url.path)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runtime = OpenCodeAgentRuntime(
        OpenCodeHttpClient(
            base_url="http://opencode.test",
            username="opencode",
            password="test-password",
            client=http_client,
        )
    )
    request = replace(
        _wait_request(timeout_seconds=1),
        budgets=RuntimeBudgets(max_input_tokens=10, max_elapsed_ms=1000),
    )
    handle = await runtime.start_task(request)
    result, _tally = await asyncio.wait_for(
        _wait_runner(tmp_path, runtime, timeout_seconds=1)._wait(handle, request=request),
        timeout=0.25,
    )
    await http_client.aclose()

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_detail == {"budget": "input_tokens"}
    assert aborted is True
    assert stream.closed is True


@pytest.mark.asyncio
async def test_timeout_reconciles_completion_after_rejected_cancel(tmp_path: Path) -> None:
    class CompletionRaceRuntime(FakeAgentRuntime):
        result_calls = 0

        async def result(self, handle):
            self.result_calls += 1
            result = await super().result(handle)
            if self.result_calls < 4:
                return replace(result, status=RuntimeTaskStatus.RUNNING)
            return result

        async def cancel(self, request):
            self.cancel_requests.append(request)
            return CancelResult(
                accepted=False,
                status=RuntimeTaskStatus.SUCCEEDED,
                runtime_id=self.runtime_id,
                session_id=request.session_id,
                requested_at=datetime.now(timezone.utc),
            )

    runtime = CompletionRaceRuntime(
        scenarios={
            "grounded-role:evidence:attempt:1": FakeRuntimeScenario(
                usage=RuntimeUsage(input_tokens=7, output_tokens=3)
            )
        }
    )
    request = _wait_request(timeout_seconds=0.01)
    handle = await runtime.start_task(request)
    result, _ = await _wait_runner(
        tmp_path,
        runtime,
        timeout_seconds=0.01,
    )._wait(handle, request=request)

    assert len(runtime.cancel_requests) == 1
    assert result.status == RuntimeTaskStatus.SUCCEEDED
    assert result.usage.input_tokens == 7


@pytest.mark.asyncio
async def test_timeout_reconciled_success_still_applies_terminal_budget_guard(
    tmp_path: Path,
) -> None:
    class CompletionRaceRuntime(FakeAgentRuntime):
        result_calls = 0

        async def result(self, handle):
            self.result_calls += 1
            result = await super().result(handle)
            if self.result_calls < 4:
                return replace(result, status=RuntimeTaskStatus.RUNNING)
            return result

        async def cancel(self, request):
            self.cancel_requests.append(request)
            return CancelResult(
                accepted=False,
                status=RuntimeTaskStatus.SUCCEEDED,
                runtime_id=self.runtime_id,
                session_id=request.session_id,
                requested_at=datetime.now(timezone.utc),
            )

    runtime = CompletionRaceRuntime(
        scenarios={
            "grounded-role:evidence:attempt:1": FakeRuntimeScenario(
                usage=RuntimeUsage(input_tokens=11, output_tokens=3)
            )
        }
    )
    request = replace(
        _wait_request(timeout_seconds=0.01),
        budgets=RuntimeBudgets(max_input_tokens=10, max_elapsed_ms=1000),
    )
    handle = await runtime.start_task(request)
    result, _ = await _wait_runner(
        tmp_path,
        runtime,
        timeout_seconds=0.01,
    )._wait(handle, request=request)

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "budget_exceeded"
    assert result.failure_detail == {"budget": "input_tokens"}


@pytest.mark.asyncio
async def test_timeout_reconciled_success_applies_live_observer_budget_violation(
    tmp_path: Path,
) -> None:
    class CompletionRaceRuntime(FakeAgentRuntime):
        result_calls = 0

        async def result(self, handle):
            self.result_calls += 1
            result = await super().result(handle)
            if self.result_calls < 4:
                return replace(result, status=RuntimeTaskStatus.RUNNING)
            return result

        async def cancel(self, request):
            self.cancel_requests.append(request)
            return CancelResult(
                accepted=False,
                status=RuntimeTaskStatus.SUCCEEDED,
                runtime_id=self.runtime_id,
                session_id=request.session_id,
                requested_at=datetime.now(timezone.utc),
            )

    usage_event = RuntimeTaskEvent(
        event_id="usage-placeholder",
        sequence=1,
        category=RuntimeEventCategory.USAGE,
        occurred_at=datetime.now(timezone.utc),
        payload={"input_tokens": 11},
    )
    runtime = CompletionRaceRuntime(
        scenarios={
            "grounded-role:evidence:attempt:1": FakeRuntimeScenario(
                usage=RuntimeUsage(input_tokens=7, output_tokens=3),
                extra_events=(usage_event,),
            )
        }
    )
    request = replace(
        _wait_request(timeout_seconds=0.01),
        budgets=RuntimeBudgets(max_input_tokens=10, max_elapsed_ms=1000),
    )
    handle = await runtime.start_task(request)
    result, _tally = await _wait_runner(
        tmp_path,
        runtime,
        timeout_seconds=0.01,
    )._wait(handle, request=request)

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "budget_exceeded"
    assert result.failure_detail == {"budget": "input_tokens"}
    assert len(runtime.cancel_requests) == 1
    assert runtime.cancel_requests[0].reason == "grounded_role_timeout"


@pytest.mark.asyncio
async def test_runtime_tool_budget_breach_cancels_before_timeout(tmp_path: Path) -> None:
    runtime = FakeAgentRuntime(
        scenarios={
            "grounded-role:evidence:attempt:1": FakeRuntimeScenario(
                outcome=RuntimeTaskStatus.RUNNING,
                tool_calls=(FakeToolCall("read"), FakeToolCall("read")),
            )
        }
    )
    request = replace(
        _wait_request(timeout_seconds=1),
        budgets=RuntimeBudgets(max_tool_calls=1, max_elapsed_ms=1000),
    )
    handle = await runtime.start_task(request)
    result, tally = await _wait_runner(
        tmp_path,
        runtime,
        timeout_seconds=1,
    )._wait(handle, request=request)

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "budget_exceeded"
    assert result.failure_detail == {"budget": "tool_calls"}
    assert tally.observed == 2
    assert tally.by_tool == {"read": 2}
    assert runtime.cancel_requests[0].reason == "grounded_budget_tool_calls"


@pytest.mark.asyncio
async def test_runtime_usage_event_over_budget_cancels_before_timeout(tmp_path: Path) -> None:
    usage_event = RuntimeTaskEvent(
        event_id="usage-placeholder",
        sequence=1,
        category=RuntimeEventCategory.USAGE,
        occurred_at=datetime.now(timezone.utc),
        payload={"input_tokens": 11},
    )
    runtime = FakeAgentRuntime(
        scenarios={
            "grounded-role:evidence:attempt:1": FakeRuntimeScenario(
                outcome=RuntimeTaskStatus.RUNNING,
                extra_events=(usage_event,),
            )
        }
    )
    request = replace(
        _wait_request(timeout_seconds=1),
        budgets=RuntimeBudgets(max_input_tokens=10, max_elapsed_ms=1000),
    )
    handle = await runtime.start_task(request)
    result, _ = await _wait_runner(tmp_path, runtime, timeout_seconds=1)._wait(
        handle,
        request=request,
    )

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_detail == {"budget": "input_tokens"}
    assert runtime.cancel_requests[0].reason == "grounded_budget_input_tokens"


@pytest.mark.asyncio
async def test_terminal_result_preserves_live_observer_budget_violation(
    tmp_path: Path,
) -> None:
    usage_event = RuntimeTaskEvent(
        event_id="usage-placeholder",
        sequence=1,
        category=RuntimeEventCategory.USAGE,
        occurred_at=datetime.now(timezone.utc),
        payload={"input_tokens": 11},
    )
    runtime = FakeAgentRuntime(
        scenarios={
            "grounded-role:evidence:attempt:1": FakeRuntimeScenario(
                usage=RuntimeUsage(input_tokens=7, output_tokens=3),
                extra_events=(usage_event,),
            )
        }
    )
    request = replace(
        _wait_request(timeout_seconds=1),
        budgets=RuntimeBudgets(max_input_tokens=10, max_elapsed_ms=1000),
    )
    handle = await runtime.start_task(request)
    result, _tally = await _wait_runner(tmp_path, runtime, timeout_seconds=1)._wait(
        handle,
        request=request,
    )

    assert result.status == RuntimeTaskStatus.FAILED
    assert result.failure_category == "budget_exceeded"
    assert result.failure_detail == {"budget": "input_tokens"}
    assert runtime.cancel_requests == []


@pytest.mark.asyncio
async def test_staged_grounded_rejects_runtime_without_budget_monitoring(
    tmp_path: Path,
) -> None:
    source = tmp_path / "shipment-events.txt"
    source.write_text("shipment A arrived\n", encoding="utf-8")
    runtime = FakeAgentRuntime(
        models=(ModelCapability("provider-a", "model-b", frozenset()),)
    )

    with pytest.raises(QuickCandidateError, match="bounded budget monitoring"):
        await GroundedOpenCodeRunner(
            runtime=runtime,
            task_builder=OpenCodeGroundedTaskBuilder(),
            output_root=tmp_path / "output",
            project_root=tmp_path,
            workspace_root=tmp_path / "workspaces",
            execution_pack=_pack(),
        ).run(
            project_id="logistics",
            experiment_id="00000000-0000-4000-8000-000000000213",
            source_items=(("upload:events/shipment-events.txt", source),),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )

    assert runtime.started_requests == []


@pytest.mark.asyncio
async def test_staged_grounded_contains_packets_and_dispatches_pack_roles(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000201"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "shipment-events.txt"
    source.write_text("shipment A arrived\n", encoding="utf-8")
    sessions: dict[str, str] = {}
    prompts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{role_id}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompts.append(json.loads(request.content))
            _write_role_outputs(
                workspace_root / experiment_id / "roles" / role_id,
                role_id,
            )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=(("upload:events/shipment-events.txt", source),),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    assert len(prompts) == 4
    assert all(prompt["tools"]["task"] is False for prompt in prompts)
    assert all(
        prompt["model"] == {"providerID": "provider-a", "modelID": "model-b"} for prompt in prompts
    )
    roles_root = workspace_root / experiment_id / "roles"
    assert (
        roles_root / "shipment-evidence" / "input" / "sources" / "events" / "shipment-events.txt"
    ).is_file()
    assert not list((roles_root / "operations-analysis" / "input").rglob("shipment-events.txt"))
    for role in (
        "shipment-evidence",
        "operations-analysis",
        "operations-writer",
        "operations-reviewer",
    ):
        manifest_path = roles_root / role / "input" / "SOURCE_MANIFEST.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["complete_frozen_source_inventory"] is True
        assert manifest["source_count"] == 1
        assert manifest["sources"][0]["present_in_frozen_snapshot"] is True
    assert (
        roles_root
        / "operations-analysis"
        / "input"
        / "accepted"
        / "candidate"
        / "orchestration"
        / "evidence_packets"
        / "shipments.json"
    ).is_file()
    reviewer_accepted = roles_root / "operations-reviewer" / "input" / "accepted"
    assert (
        reviewer_accepted / "candidate" / "orchestration" / "evidence_packets" / "shipments.json"
    ).is_file()
    assert (reviewer_accepted / "candidate" / "intermediates" / "operations_analysis.md").is_file()
    assert (reviewer_accepted / "orchestration" / "deterministic_claim_report.json").is_file()
    assert all(
        "SOURCE_MANIFEST.json are deterministic and authoritative" in prompt["parts"][0]["text"]
        for prompt in prompts
    )
    receipt = next(
        artifact
        for artifact in result.artifacts
        if artifact.logical_name == "receipts/grounded_execution.json"
    )
    payload = json.loads(
        (
            tmp_path
            / "output"
            / "logistics"
            / "experiments"
            / experiment_id
            / receipt.relative_path
        ).read_text(encoding="utf-8")
    )
    assert payload["execution_mode"] == "grounded-nexus-staged-pack"
    assert len(payload["role_sessions"]) == 4


@pytest.mark.asyncio
async def test_staged_grounded_orchestrates_through_provider_neutral_runtime(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000211"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "shipment-events.txt"
    source.write_text("shipment A arrived\n", encoding="utf-8")

    class ScopedPacketBuilder:
        def __init__(self) -> None:
            self.contexts: dict[str, GroundedRoleRuntimeContext] = {}
            self.paths: dict[str, dict[str, Path]] = {}

        def build(self, context: GroundedRoleRuntimeContext) -> RuntimeTaskRequest:
            task_id = f"grounded-role:{context.role_id}:attempt:{context.attempt}"
            packet_paths = tuple(
                path
                for path in sorted(context.packet_root.rglob("*"))
                if path.is_file() and not path.is_symlink()
            )
            logical_paths = {
                path.relative_to(context.packet_root).as_posix(): path for path in packet_paths
            }
            self.contexts[task_id] = context
            self.paths[task_id] = logical_paths
            return RuntimeTaskRequest(
                experiment_id=context.experiment_id,
                task_id=task_id,
                idempotency_key=f"{context.experiment_id}:{task_id}",
                provider_id=context.provider_id,
                model_id=context.model_id,
                semantic_prompt=context.prompt,
                semantic_prompt_sha256=sha256(context.prompt.encode()).hexdigest(),
                context_refs=tuple(
                    ArtifactReference(
                        logical_name=logical,
                        sha256=sha256(path.read_bytes()).hexdigest(),
                        media_type="application/octet-stream",
                        byte_size=path.stat().st_size,
                    )
                    for logical, path in logical_paths.items()
                ),
                output_contract=OutputContract(
                    f"roles/{context.role_id}/candidate-bundle",
                    "application/vnd.nexus.grounded-role-bundle",
                    max_bytes=context.max_context_bytes,
                ),
                allowed_tool_names=("nexus_grounded_read_context", "nexus_grounded_write_artifact"),
                budgets=RuntimeBudgets(
                    max_input_tokens=context.max_input_tokens,
                    max_tool_calls=context.max_tool_calls,
                    max_elapsed_ms=int(context.timeout_seconds * 1000),
                ),
                deadline_at=datetime.now(timezone.utc)
                + timedelta(seconds=context.timeout_seconds),
            )

    class PacketWritingRuntime(FakeAgentRuntime):
        def __init__(self, builder: ScopedPacketBuilder) -> None:
            super().__init__(
                models=(
                    ModelCapability(
                        "provider-a",
                        "model-b",
                        frozenset({BOUNDED_BUDGET_MONITORING_CAPABILITY}),
                    ),
                )
            )
            self.builder = builder

        async def start_task(self, request):
            assert request.allowed_tool_names == (
                "nexus_grounded_read_context",
                "nexus_grounded_write_artifact",
            )
            for reference in request.context_refs:
                source_path = self.builder.paths[request.task_id][reference.logical_name]
                assert sha256(source_path.read_bytes()).hexdigest() == reference.sha256
            handle = await super().start_task(request)
            context = self.builder.contexts[request.task_id]
            _write_role_outputs(context.packet_root, context.role_id)
            return handle

    task_builder = ScopedPacketBuilder()
    runtime = PacketWritingRuntime(task_builder)
    result = await GroundedOpenCodeRunner(
        runtime=runtime,
        task_builder=task_builder,
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=(("upload:events/shipment-events.txt", source),),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )

    assert len(runtime.started_requests) == 4
    assert all(
        request.allowed_tool_names
        == ("nexus_grounded_read_context", "nexus_grounded_write_artifact")
        for request in runtime.started_requests
    )
    assert all(request.output_contract.artifact_name.endswith("/candidate-bundle") for request in runtime.started_requests)
    assert all(request.context_refs for request in runtime.started_requests)
    assert all(not request.runtime_options for request in runtime.started_requests)
    dispatched_contexts = tuple(task_builder.contexts.values())
    assert sum(context.max_input_tokens for context in dispatched_contexts) == _pack().max_input_tokens
    assert sum(context.max_tool_calls for context in dispatched_contexts) == _pack().max_tool_calls
    assert all(context.max_input_tokens < _pack().max_input_tokens for context in dispatched_contexts)
    assert all(context.max_tool_calls < _pack().max_tool_calls for context in dispatched_contexts)
    receipt = next(item for item in result.artifacts if item.logical_name == "receipts/grounded_execution.json")
    payload = json.loads(
        (tmp_path / "output" / "logistics" / "experiments" / experiment_id / receipt.relative_path).read_text()
    )
    assert payload["runtime"] == "fake/1.0"
    assert payload["role_sessions"][0]["session_id"].startswith("fake-session-")


@pytest.mark.asyncio
async def test_staged_grounded_cancels_a_timed_out_runtime_task(tmp_path: Path) -> None:
    source = tmp_path / "shipment-events.txt"
    source.write_text("shipment A arrived\n", encoding="utf-8")
    runtime = FakeAgentRuntime(
        models=(
            ModelCapability(
                "provider-a",
                "model-b",
                frozenset({BOUNDED_BUDGET_MONITORING_CAPABILITY}),
            ),
        ),
        scenarios={
            "grounded-role:shipment-evidence:attempt:1": FakeRuntimeScenario(
                outcome=RuntimeTaskStatus.RUNNING
            )
        }
    )
    runner = GroundedOpenCodeRunner(
        runtime=runtime,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=tmp_path / "workspaces",
        execution_pack=replace(_pack(), repair_limit=0),
        timeout_seconds=1,
    )

    with pytest.raises(QuickCandidateError, match="runtime was cancelled"):
        await runner.run(
            project_id="logistics",
            experiment_id="00000000-0000-4000-8000-000000000212",
            source_items=(("upload:events/shipment-events.txt", source),),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )

    assert len(runtime.cancel_requests) == 1
    assert runtime.cancel_requests[0].reason == "grounded_role_timeout"


@pytest.mark.asyncio
async def test_staged_grounded_never_exposes_unsafe_intermediate_downstream(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000202"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "safe.txt"
    source.write_text("safe source\n", encoding="utf-8")
    pack = replace(_pack(), repair_limit=0)
    created_roles: list[str] = []
    sessions: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            created_roles.append(role_id)
            sessions[f"session-{role_id}"] = role_id
            return httpx.Response(200, json={"id": f"session-{role_id}"})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            _write_role_outputs(
                workspace_root / experiment_id / "roles" / role_id,
                role_id,
                unsafe_evidence=True,
            )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    with pytest.raises(QuickCandidateError, match="secret-safety"):
        await GroundedOpenCodeRunner(
            runtime=client,
            task_builder=OpenCodeGroundedTaskBuilder(),
            output_root=tmp_path / "output",
            project_root=tmp_path,
            workspace_root=workspace_root,
            execution_pack=pack,
            timeout_seconds=2,
        ).run(
            project_id="logistics",
            experiment_id=experiment_id,
            source_items=(("upload:safe.txt", source),),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )
    await http_client.aclose()

    assert created_roles == ["shipment-evidence"]
    downstream = (
        workspace_root / experiment_id / "roles" / "operations-analysis" / "input" / "accepted"
    )
    assert not downstream.exists()


@pytest.mark.asyncio
async def test_staged_grounded_enforces_physical_role_packet_byte_budget(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000203"
    source = tmp_path / "large.txt"
    source.write_text("x" * 1024, encoding="utf-8")
    pack = _pack()
    evidence = replace(pack.roles[0], max_context_bytes=32)
    constrained = replace(pack, roles=(evidence, *pack.roles[1:]))
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    with pytest.raises(QuickCandidateError, match="physical role packet budget"):
        await GroundedOpenCodeRunner(
            runtime=client,
            task_builder=OpenCodeGroundedTaskBuilder(),
            output_root=tmp_path / "output",
            project_root=tmp_path,
            workspace_root=tmp_path / "workspaces",
            execution_pack=constrained,
            timeout_seconds=2,
        ).run(
            project_id="logistics",
            experiment_id=experiment_id,
            source_items=(("upload:large.txt", source),),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )
    await http_client.aclose()

    assert requests == ["/global/health"]


@pytest.mark.asyncio
async def test_staged_grounded_retries_only_the_rejected_role_within_pack_limit(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000204"
    workspace_root = tmp_path / "workspaces"
    source = tmp_path / "safe.txt"
    source.write_text("safe source\n", encoding="utf-8")
    sessions: dict[str, str] = {}
    prompt_roles: list[str] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompt_roles.append(role_id)
            # The first evidence attempt is deliberately incomplete. Nexus
            # retries only that isolated role; no dependent can start yet.
            if not (role_id == "shipment-evidence" and prompt_roles.count(role_id) == 1):
                _write_role_outputs(
                    workspace_root / experiment_id / "roles" / role_id,
                    role_id,
                )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=(("upload:safe.txt", source),),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    assert prompt_roles == [
        "shipment-evidence",
        "shipment-evidence",
        "operations-analysis",
        "operations-writer",
        "operations-reviewer",
    ]


@pytest.mark.asyncio
async def test_staged_grounded_accepts_bad_evidence_as_warning(
    tmp_path: Path,
) -> None:
    """A protected-fact mismatch is a warning, not a repair-triggering failure.

    The scanner-derived fact ledger is itself imperfect (known gaps), so a
    single-role output disagreeing with it should not cost a retry or block
    the run -- it should be accepted and the mismatch surfaced for review.
    """

    experiment_id = "00000000-0000-4000-8000-000000000205"
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    prompt_roles: list[str] = []
    prompt_texts: list[str] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompt_roles.append(role_id)
            payload = json.loads(request.content)
            prompt_texts.append(payload["parts"][0]["text"])
            role_root = workspace_root / experiment_id / "roles" / role_id
            if role_id == "shipment-evidence":
                output = (
                    role_root
                    / "candidate"
                    / "orchestration"
                    / "evidence_packets"
                    / "shipments.json"
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(
                        {
                            "canonical_units": [
                                {
                                    "name": "ShipmentPlanner",
                                    "kind": "route",
                                    "component_count": 9,
                                    "connection_count": 1,
                                    "source_present": True,
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=_scanner_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    assert prompt_roles == [
        "shipment-evidence",
        "operations-analysis",
        "operations-writer",
        "operations-reviewer",
    ]
    mismatch_warnings = [
        warning
        for warning in result.safety_warnings
        if warning.get("category") == "protected_fact_mismatch"
    ]
    assert mismatch_warnings, result.safety_warnings
    assert any('"claimed":9' in warning["detail"] for warning in mismatch_warnings)
    assert any('"expected":1' in warning["detail"] for warning in mismatch_warnings)


@pytest.mark.asyncio
async def test_staged_grounded_accepts_persistently_bad_evidence_without_retry(
    tmp_path: Path,
) -> None:
    """A role that would fail the fact check on every attempt still only runs once.

    Before this was softened to a warning, this same fixture burned every
    repair attempt and then hard-failed the whole run. Confirms no attempt is
    wasted retrying a mismatch that will never resolve, since it no longer
    blocks acceptance in the first place.
    """

    experiment_id = "00000000-0000-4000-8000-000000000206"
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    prompt_roles: list[str] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompt_roles.append(role_id)
            role_root = workspace_root / experiment_id / "roles" / role_id
            if role_id == "shipment-evidence":
                output = (
                    role_root
                    / "candidate"
                    / "orchestration"
                    / "evidence_packets"
                    / "shipments.json"
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(
                        {
                            "canonical_units": [
                                {
                                    "name": "ShipmentPlanner",
                                    "kind": "route",
                                    "component_count": 9,
                                    "connection_count": 1,
                                    "source_present": True,
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=_scanner_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    assert prompt_roles == [
        "shipment-evidence",
        "operations-analysis",
        "operations-writer",
        "operations-reviewer",
    ]
    assert any(
        warning.get("category") == "protected_fact_mismatch"
        for warning in result.safety_warnings
    )


@pytest.mark.asyncio
async def test_staged_grounded_accepts_bad_writer_kind_summary_as_warning(
    tmp_path: Path,
) -> None:
    """A writer doc disagreeing with scanner job/route totals is a warning, not a repair."""

    experiment_id = "00000000-0000-4000-8000-000000000207"
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    prompt_roles: list[str] = []
    prompt_texts: list[str] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompt_roles.append(role_id)
            payload = json.loads(request.content)
            prompt_texts.append(payload["parts"][0]["text"])
            role_root = workspace_root / experiment_id / "roles" / role_id
            if role_id != "operations-writer":
                _write_role_outputs(role_root, role_id)
                return httpx.Response(204)
            kind_summary = "The project comprises 0 Routes and 1 Job."
            for name, title in (
                ("operations_runbook.md", "Operations"),
                ("exception_inventory.md", "Exceptions"),
            ):
                path = role_root / "candidate" / "docs" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    f"# {title}\n\n{kind_summary}\n",
                    encoding="utf-8",
                )
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=_scanner_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    assert prompt_roles == [
        "shipment-evidence",
        "operations-analysis",
        "operations-writer",
        "operations-reviewer",
    ]
    mismatch_warnings = [
        warning
        for warning in result.safety_warnings
        if warning.get("category") == "protected_fact_mismatch"
    ]
    assert mismatch_warnings, result.safety_warnings
    assert any("project_job_count_mismatch" in warning["detail"] for warning in mismatch_warnings)
    assert any(
        "project_route_count_mismatch" in warning["detail"] for warning in mismatch_warnings
    )


@pytest.mark.asyncio
async def test_staged_grounded_persists_valid_optional_business_topology(
    tmp_path: Path,
) -> None:
    experiment_id = "00000000-0000-4000-8000-000000000208"
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    prompted_roles: list[str] = []
    prompt_texts: dict[str, str] = {}
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            role_id = parse_qs(request.url.query.decode())["directory"][0].rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            role_id = sessions[request.url.path.split("/")[2]]
            prompted_roles.append(role_id)
            prompt_texts[role_id] = json.loads(request.content)["parts"][0]["text"]
            role_root = workspace_root / experiment_id / "roles" / role_id
            if role_id == "business-topology":
                assert (role_root / "ROLE_PROMPT.txt").is_file()
                assert "FrozenShipmentPlanner" in (
                    role_root / "topology" / "evidence" / "artifacts.jsonl"
                ).read_text(encoding="utf-8")
                assert "ChangedLiveJob" not in (
                    role_root / "topology" / "evidence" / "artifacts.jsonl"
                ).read_text(encoding="utf-8")
                _write_valid_topology_output(role_root)
            else:
                _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    live = tmp_path / "current-live-scan.json"
    live.write_text(json.dumps({"jobs": [{"name": "ChangedLiveJob"}]}), encoding="utf-8")
    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack_with_topology(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=_topology_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    logical_names = {artifact.logical_name for artifact in result.artifacts}
    assert {
        "docs/operations_runbook.md",
        "docs/exception_inventory.md",
        "topology/business_topology.json",
        "topology/validation_report.json",
        "topology/state.json",
    }.issubset(logical_names)
    assert prompted_roles.index("business-topology") > prompted_roles.index("operations-analysis")
    assert prompted_roles.index("business-topology") < prompted_roles.index("operations-reviewer")
    assert "Read ROLE_PROMPT.txt completely" in prompt_texts["business-topology"]
    candidate_root = tmp_path / "output" / "logistics" / "experiments" / experiment_id / "candidate"
    assert json.loads((candidate_root / "topology" / "validation_report.json").read_text())["validated"]
    assert json.loads((candidate_root / "topology" / "state.json").read_text())["available"]
    receipt = json.loads((candidate_root / "receipts" / "grounded_execution.json").read_text())
    topology_receipt = next(
        item for item in receipt["role_sessions"] if item["role_id"] == "business-topology"
    )
    assert receipt["publishes"] is False
    assert topology_receipt["status"] == "completed"
    assert topology_receipt["session_id"]
    assert topology_receipt["usage"]["input_tokens"] == 10
    assert topology_receipt["tool_summary"]["total_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ("invalid", "runtime"))
async def test_staged_grounded_optional_topology_failure_does_not_fail_docs(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    experiment_id = (
        "00000000-0000-4000-8000-000000000209"
        if failure_mode == "invalid"
        else "00000000-0000-4000-8000-000000000210"
    )
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    prompt_roles: list[str] = []
    prompt_texts: list[str] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            role_id = parse_qs(request.url.query.decode())["directory"][0].rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            role_id = sessions[request.url.path.split("/")[2]]
            prompt_roles.append(role_id)
            prompt_texts.append(json.loads(request.content)["parts"][0]["text"])
            role_root = workspace_root / experiment_id / "roles" / role_id
            if role_id == "business-topology":
                if failure_mode == "runtime":
                    return httpx.Response(500, json={"error": "topology runtime unavailable"})
                output = role_root / "candidate" / "topology" / "business_topology.json"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("{}", encoding="utf-8")
            else:
                _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack_with_topology(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=_topology_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    logical_names = {artifact.logical_name for artifact in result.artifacts}
    assert "docs/operations_runbook.md" in logical_names
    assert "docs/exception_inventory.md" in logical_names
    assert "topology/business_topology.json" not in logical_names
    assert "topology/validation_report.json" in logical_names
    assert "topology/state.json" in logical_names
    assert result.safety_warnings
    candidate_root = tmp_path / "output" / "logistics" / "experiments" / experiment_id / "candidate"
    assert not (candidate_root / "topology" / "business_topology.json").exists()
    assert not json.loads((candidate_root / "topology" / "state.json").read_text())["available"]
    receipt = json.loads((candidate_root / "receipts" / "grounded_execution.json").read_text())
    topology_receipt = next(
        item for item in receipt["role_sessions"] if item["role_id"] == "business-topology"
    )
    assert topology_receipt["status"] == "warning"
    assert topology_receipt["session_id"]
    if failure_mode == "invalid":
        assert prompt_roles.count("business-topology") == 2
        topology_prompts = [
            prompt
            for role_id, prompt in zip(prompt_roles, prompt_texts, strict=True)
            if role_id == "business-topology"
        ]
        assert "Business-topology validation feedback" in topology_prompts[-1]


@pytest.mark.asyncio
async def test_staged_grounded_topology_preparation_failure_is_diagnosable(
    tmp_path: Path,
) -> None:
    """A topology PREPARATION failure (before any runtime session) must be diagnosable.

    Verified defect: a real run's ``business-topology`` role finished with
    status "warning", no session, and zero tool calls -- it failed inside
    Python packet preparation, never reaching the runtime. The only
    recorded reason was the generic bucket
    ``business_topology_packet_failure``, identical for every possible
    preparation error, so the run gave no way to tell WHY it failed. This
    drops a required frozen scanner artifact (``scan/lineage.json``) so
    preparation raises a concrete, known error, and asserts that the exact
    reason now reaches the receipt/state -- not just the bucket name.
    """

    experiment_id = "00000000-0000-4000-8000-000000000211"
    workspace_root = tmp_path / "workspaces"
    prompt_roles: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            role_id = parse_qs(request.url.query.decode())["directory"][0].rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": f"session-{role_id}"})
        if request.url.path.endswith("/prompt_async"):
            role_id = request.url.path.split("/")[2].removeprefix("session-")
            prompt_roles.append(role_id)
            role_root = workspace_root / experiment_id / "roles" / role_id
            _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    sources_without_lineage = tuple(
        item for item in _topology_sources(tmp_path) if not item[0].endswith("scan/lineage.json")
    )
    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack_with_topology(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=sources_without_lineage,
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    # The topology role never reached the runtime -- no session was ever
    # opened for it.
    assert "business-topology" not in prompt_roles

    logical_names = {artifact.logical_name for artifact in result.artifacts}
    assert "docs/operations_runbook.md" in logical_names
    assert "topology/business_topology.json" not in logical_names
    assert "topology/validation_report.json" in logical_names
    assert "topology/state.json" in logical_names

    candidate_root = tmp_path / "output" / "logistics" / "experiments" / experiment_id / "candidate"
    state = json.loads((candidate_root / "topology" / "state.json").read_text())
    validation = json.loads((candidate_root / "topology" / "validation_report.json").read_text())
    assert state["available"] is False
    # The bucketed category alone used to be the ONLY thing recorded. The
    # concrete reason must now also be present, and it must name the actual
    # missing artifact, not merely restate the bucket name.
    assert any("business_topology_packet_failure" in error for error in state["errors"])
    assert any("missing required scanner artifacts" in error for error in state["errors"])
    assert any("scan/lineage.json" in error for error in state["errors"])
    assert state["errors"] == validation["errors"]

    receipt = json.loads((candidate_root / "receipts" / "grounded_execution.json").read_text())
    topology_receipt = next(
        item for item in receipt["role_sessions"] if item["role_id"] == "business-topology"
    )
    assert topology_receipt["status"] == "warning"
    assert topology_receipt["session_id"] is None
    assert topology_receipt["tool_summary"]["total_calls"] == 0

    topology_warning = next(
        warning
        for warning in receipt["safety_warnings"]
        if warning["path"] == "candidate/topology/business_topology.json"
    )
    assert topology_warning["category"] == "optional_role_business_topology_packet_failure"
    assert "missing required scanner artifacts" in topology_warning["detail"]
    assert "scan/lineage.json" in topology_warning["detail"]


@pytest.mark.asyncio
async def test_staged_grounded_resume_skips_already_accepted_roles(
    tmp_path: Path,
) -> None:
    """A second attempt can resume a first attempt's already-accepted roles.

    The first run fails at the reviewer (the last role). Evidence, analysis,
    and the writer role all succeeded and were accepted before that. A second
    run pointed at the first run's workspace via resume_from_experiment_id
    must not re-prompt any of those three roles -- only the reviewer, which
    never got far enough to be accepted, should run.
    """

    first_experiment_id = "00000000-0000-4000-8000-000000000301"
    second_experiment_id = "00000000-0000-4000-8000-000000000302"
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    prompt_roles: list[str] = []
    create_count = 0

    def failing_reviewer_handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompt_roles.append(role_id)
            if role_id == "operations-reviewer":
                # Produce nothing -- a genuine hard failure (missing required
                # artifact), exhausting the role's one retry.
                return httpx.Response(204)
            role_root = workspace_root / first_experiment_id / "roles" / role_id
            _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(failing_reviewer_handler))
    with pytest.raises(QuickCandidateError):
        await GroundedOpenCodeRunner(
            runtime=client,
            task_builder=OpenCodeGroundedTaskBuilder(),
            output_root=tmp_path / "output",
            project_root=tmp_path,
            workspace_root=workspace_root,
            execution_pack=_pack(),
            timeout_seconds=2,
        ).run(
            project_id="logistics",
            experiment_id=first_experiment_id,
            source_items=_scanner_sources(tmp_path),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )
    await http_client.aclose()

    first_attempt_roles = list(prompt_roles)
    assert "shipment-evidence" in first_attempt_roles
    assert "operations-analysis" in first_attempt_roles
    assert "operations-writer" in first_attempt_roles
    assert first_attempt_roles.count("operations-reviewer") == 2  # exhausted its retry

    prompt_roles.clear()
    sessions.clear()

    def resumed_handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            if role_id != "operations-reviewer":
                raise AssertionError(f"resumed role should not restart: {role_id}")
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            role_id = sessions[session_id]
            prompt_roles.append(role_id)
            role_root = workspace_root / second_experiment_id / "roles" / role_id
            _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client2, http_client2 = _client(httpx.MockTransport(resumed_handler))
    result = await GroundedOpenCodeRunner(
        runtime=client2,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=second_experiment_id,
        source_items=_scanner_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
        resume_from_experiment_id=first_experiment_id,
    )
    await http_client2.aclose()

    assert prompt_roles == ["operations-reviewer"]
    logical_names = {artifact.logical_name for artifact in result.artifacts}
    assert "docs/operations_runbook.md" in logical_names
    assert "docs/exception_inventory.md" in logical_names


@pytest.mark.asyncio
async def test_staged_grounded_resume_reruns_every_role_on_changed_fact_ledger(
    tmp_path: Path,
) -> None:
    """A changed project reuses nothing stale -- every role reruns fresh.

    Resume keys each role on a content hash: a static role is pinned to the
    whole fact ledger, so a different scan (different ledger) gives every role
    a different hash. The resumed run must therefore reuse NO prior output and
    re-prompt every role against the new evidence -- never silently serve a
    document built from the old scan. (Previously this hard-failed the resume;
    graceful full rerun is the safer, more useful behaviour.)
    """

    first_experiment_id = "00000000-0000-4000-8000-000000000303"
    second_experiment_id = "00000000-0000-4000-8000-000000000304"
    workspace_root = tmp_path / "workspaces"
    # Map each opened session to the (experiment_id, role_id) parsed from its
    # runtime directory so BOTH runs write outputs into their own workspace.
    sessions: dict[str, tuple[str, str]] = {}
    prompted: list[tuple[str, str]] = []
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            experiment_id = directory.split("/roles/")[0].rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = (experiment_id, role_id)
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            session_id = request.url.path.split("/")[2]
            experiment_id, role_id = sessions[session_id]
            prompted.append((experiment_id, role_id))
            role_root = workspace_root / experiment_id / "roles" / role_id
            _write_role_outputs(role_root, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    # First attempt succeeds outright -- it only needs to leave behind a
    # completed workspace (with its own fact ledger and resume state).
    client, http_client = _client(httpx.MockTransport(handler))
    await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=first_experiment_id,
        source_items=_scanner_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    # Different scan content -- a different fact ledger -- for the resume attempt.
    other_root = tmp_path / "other"
    other_root.mkdir()
    other_sources = _scanner_sources(other_root)
    job_path = next(path for logical, path in other_sources if logical.endswith("ShipmentPlanner.json"))
    job_payload = json.loads(job_path.read_text(encoding="utf-8"))
    job_payload["name"] = "ADifferentJobEntirely"
    job_path.write_text(json.dumps(job_payload), encoding="utf-8")

    prompted.clear()
    client2, http_client2 = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client2,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
    ).run(
        project_id="logistics",
        experiment_id=second_experiment_id,
        source_items=other_sources,
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
        resume_from_experiment_id=first_experiment_id,
    )
    await http_client2.aclose()

    # Nothing was reused: every role of the second run was re-prompted against
    # the new scan, and none of the first run's roles were touched again.
    resumed_roles = {role_id for exp_id, role_id in prompted if exp_id == second_experiment_id}
    assert resumed_roles == {
        "shipment-evidence",
        "operations-analysis",
        "operations-writer",
        "operations-reviewer",
    }
    assert all(exp_id == second_experiment_id for exp_id, _role in prompted)
    logical_names = {artifact.logical_name for artifact in result.artifacts}
    assert "docs/operations_runbook.md" in logical_names


# ==========================================================================
# Slice 7 -- resume keys each dynamic (batch) role on a content hash, and
# runner reliability (transient retry vs. deterministic failure).
# ==========================================================================


_RECIPE_ROOT = Path(__file__).parents[2] / "data" / "agentic_recipes"


def _fanout_pack() -> GroundedExecutionPack:
    return load_grounded_execution_pack(
        load_recipe(_RECIPE_ROOT / "alitalend_legacy_equivalence" / "recipe.yaml")
    )


def _fanout_sources(root: Path, *, beta_code: str) -> tuple[tuple[str, Path], ...]:
    root.mkdir(parents=True, exist_ok=True)

    def component(unique: str, code: str) -> dict:
        return {"unique_name": unique, "component_type": "tJava", "code": code, "params": []}

    def job(name: str, group: str, code: str) -> dict:
        return {
            "name": name,
            "group": group,
            "source_root": "ALI",
            "path": f"{name}.item",
            "source_design": "process",
            "components": [component(f"tJava_{name}", code)],
            "connections": [],
        }

    # Fixed-length body so Alpha/Gamma evidence is byte-identical across scenarios
    # (stable bin-packing / batch ids). Beta swaps to a same-length body so only
    # its own batch's content hash changes.
    stable = "x = 1; // " + ("payload " * 60)
    scan = root / "scan_result.json"
    scan.write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": [
                    job("AlphaJob", "GroupA", stable),
                    job("BetaJob", "GroupB", beta_code),
                    job("GammaJob", "GroupC", stable),
                ],
                "stats": {"total_jobs": 3},
            }
        ),
        encoding="utf-8",
    )
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"job_calls": {}, "connection_used_by": {}}), encoding="utf-8")
    meta = root / "meta.json"
    meta.write_text(
        json.dumps({"platform": "talend", "stats": {"job_count": 3, "route_count": 0}}),
        encoding="utf-8",
    )
    redaction = root / "redaction.json"
    redaction.write_text(
        json.dumps({"redaction_level": "strict", "total_redactions": 0, "summary": {}}),
        encoding="utf-8",
    )
    return (
        ("scan:scan_result.json", scan),
        ("scan:scan/manifest.json", manifest),
        ("scan:scan/meta.json", meta),
        ("scan:redaction_report.json", redaction),
    )


def _prepare_fanout(root: Path, *, beta_code: str, workspace_root: Path | None = None):
    """Build ledger -> plan -> expanded pack -> a runner + its role hashes."""

    sources = _fanout_sources(root, beta_code=beta_code)
    ledger = compile_fact_ledger(sources, project_id="ali")
    unit_plan = _UnitEvidencePlan.from_frozen_sources(sources, ledger)
    _planner, batch_plan = AnalysisBatchPlanner.from_fact_ledger(
        ledger,
        evidence_bytes=unit_plan.evidence_bytes(),
        # Tiny budget forces one unit per batch: analysis_batch_001..003.
        max_batch_bytes=100,
    )
    pack = expand_grounded_fanout(_fanout_pack(), batch_plan.batches)
    runner = GroundedOpenCodeRunner(
        runtime=FakeAgentRuntime(),
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=root / "output",
        project_root=root,
        workspace_root=workspace_root if workspace_root is not None else root / "workspaces",
        execution_pack=pack,
        timeout_seconds=2,
    )
    hashes = runner._role_content_hashes(
        ledger=ledger,
        unit_evidence_plan=unit_plan,
        batch_plan=batch_plan,
        provider_id="provider-a",
        model_id="model-b",
    )
    return runner, pack, hashes


def test_resume_reuses_unchanged_batch_and_reruns_changed_batch(tmp_path: Path) -> None:
    """An unchanged batch is reused on resume; a content-changed batch reruns."""

    stable_beta = "x = 1; // " + ("payload " * 60)
    changed_beta = "y = 2; // " + ("altered " * 60)  # same length, different content

    workspace_root = tmp_path / "shared-workspaces"
    workspace_root.mkdir()

    runner_a, pack_a, hashes_a = _prepare_fanout(
        tmp_path / "a", beta_code=stable_beta, workspace_root=workspace_root
    )
    batch_ids = tuple(
        role.id for role in pack_a.roles if role.stage == "analysis" and role.units
    )
    assert batch_ids == ("analysis_batch_001", "analysis_batch_002", "analysis_batch_003")

    # A prior workspace that accepted all three analysis batches: its canonical
    # owned outputs plus a resume state recording each batch's content hash.
    source_root = workspace_root / "source-exp"
    source_root.mkdir()
    accepted_hashes = {}
    for role in pack_a.roles:
        if role.id not in batch_ids:
            continue
        owned = source_root.joinpath(*Path(role.owns[0]).parts)
        owned.parent.mkdir(parents=True, exist_ok=True)
        owned.write_text(f"# {role.id}\n\nPrior accepted analysis.\n", encoding="utf-8")
        accepted_hashes[role.id] = hashes_a[role.id]
    (source_root / RESUME_STATE_NAME).write_text(
        json.dumps({"schema_version": 1, "accepted_role_hashes": accepted_hashes}),
        encoding="utf-8",
    )

    # Point runner A (identical composition) at the source: every batch is reused.
    dest_a = workspace_root / "dest-a"
    dest_a.mkdir()
    reused_a = {
        role_id
        for role_id, _paths in runner_a._resume_accepted_roles(
            workspace=dest_a,
            resume_from_experiment_id="source-exp",
            role_content_hashes=hashes_a,
        )
    }
    assert set(batch_ids) <= reused_a

    # Scenario B: BetaJob's body changed (same length -> same batch ids). Only
    # analysis_batch_002's content hash moves; the other two are byte-stable.
    runner_b, _pack_b, hashes_b = _prepare_fanout(
        tmp_path / "b", beta_code=changed_beta, workspace_root=workspace_root
    )
    assert hashes_b["analysis_batch_002"] != hashes_a["analysis_batch_002"]
    assert hashes_b["analysis_batch_001"] == hashes_a["analysis_batch_001"]
    assert hashes_b["analysis_batch_003"] == hashes_a["analysis_batch_003"]

    # Resuming scenario B from the scenario-A workspace reuses ONLY the unchanged
    # batches; the changed batch is not reused and must rerun.
    dest_b = workspace_root / "dest-b"
    dest_b.mkdir()
    reused_b = {
        role_id
        for role_id, _paths in runner_b._resume_accepted_roles(
            workspace=dest_b,
            resume_from_experiment_id="source-exp",
            role_content_hashes=hashes_b,
        )
    }
    assert "analysis_batch_001" in reused_b
    assert "analysis_batch_003" in reused_b
    assert "analysis_batch_002" not in reused_b


def test_resume_reruns_batch_when_composition_changes(tmp_path: Path) -> None:
    """Adding/removing a unit from a batch changes its hash (so it reruns)."""

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    def _single_batch(root: Path, *, drop_gamma: bool):
        sources_root = root
        sources = _fanout_sources(sources_root, beta_code="x = 1; // " + ("payload " * 60))
        if drop_gamma:
            scan_path = sources_root / "scan_result.json"
            payload = json.loads(scan_path.read_text(encoding="utf-8"))
            payload["jobs"] = [j for j in payload["jobs"] if j["name"] != "GammaJob"]
            scan_path.write_text(json.dumps(payload), encoding="utf-8")
        ledger = compile_fact_ledger(sources, project_id="ali")
        unit_plan = _UnitEvidencePlan.from_frozen_sources(sources, ledger)
        # Large budget: every unit collapses into ONE batch (analysis_batch_001),
        # so dropping a unit is a pure composition change to that one batch.
        _planner, batch_plan = AnalysisBatchPlanner.from_fact_ledger(
            ledger, evidence_bytes=unit_plan.evidence_bytes(), max_batch_bytes=10_000_000
        )
        pack = expand_grounded_fanout(_fanout_pack(), batch_plan.batches)
        runner = GroundedOpenCodeRunner(
            runtime=FakeAgentRuntime(),
            task_builder=OpenCodeGroundedTaskBuilder(),
            output_root=root / "output",
            project_root=root,
            workspace_root=workspace_root,
            execution_pack=pack,
            timeout_seconds=2,
        )
        return runner._role_content_hashes(
            ledger=ledger,
            unit_evidence_plan=unit_plan,
            batch_plan=batch_plan,
            provider_id="provider-a",
            model_id="model-b",
        )

    full = _single_batch(tmp_path / "full", drop_gamma=False)
    reduced = _single_batch(tmp_path / "reduced", drop_gamma=True)
    assert "analysis_batch_001" in full and "analysis_batch_001" in reduced
    assert full["analysis_batch_001"] != reduced["analysis_batch_001"]


@pytest.mark.asyncio
async def test_staged_grounded_retries_transient_runtime_error_then_succeeds(
    tmp_path: Path,
) -> None:
    """A transient runtime fault (OpenCode 5xx) on a required role is retried."""

    experiment_id = "00000000-0000-4000-8000-000000000401"
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    create_count = 0
    evidence_session_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count, evidence_session_attempts
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            if role_id == "shipment-evidence":
                evidence_session_attempts += 1
                if evidence_session_attempts == 1:
                    # First attempt: a 5xx -> OpenCodeUnavailable (retryable).
                    return httpx.Response(503, json={"error": "runtime unavailable"})
            create_count += 1
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            role_id = sessions[request.url.path.split("/")[2]]
            _write_role_outputs(workspace_root / experiment_id / "roles" / role_id, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack(),
        timeout_seconds=2,
        transient_retry_limit=2,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=_scanner_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    # The evidence role opened a session twice: the 503 attempt and its retry.
    assert evidence_session_attempts == 2
    logical_names = {artifact.logical_name for artifact in result.artifacts}
    assert "docs/operations_runbook.md" in logical_names
    assert "docs/exception_inventory.md" in logical_names


@pytest.mark.asyncio
async def test_staged_grounded_does_not_retry_deterministic_runtime_error(
    tmp_path: Path,
) -> None:
    """A deterministic (non-retryable) runtime fault fails loudly, no retry."""

    experiment_id = "00000000-0000-4000-8000-000000000402"
    workspace_root = tmp_path / "workspaces"
    evidence_session_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal evidence_session_attempts
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            directory = parse_qs(request.url.query.decode())["directory"][0]
            role_id = directory.rsplit("/", 1)[-1]
            if role_id == "shipment-evidence":
                evidence_session_attempts += 1
                # 401 -> OpenCodeClientError (retryable = False): never retried.
                return httpx.Response(401, json={"error": "authentication rejected"})
            return httpx.Response(200, json={"id": f"session-{role_id}"})
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    with pytest.raises(QuickCandidateError, match="required role shipment-evidence failed"):
        await GroundedOpenCodeRunner(
            runtime=client,
            task_builder=OpenCodeGroundedTaskBuilder(),
            output_root=tmp_path / "output",
            project_root=tmp_path,
            workspace_root=workspace_root,
            execution_pack=_pack(),
            timeout_seconds=2,
            transient_retry_limit=2,
        ).run(
            project_id="logistics",
            experiment_id=experiment_id,
            source_items=_scanner_sources(tmp_path),
            provider_id="provider-a",
            model_id="model-b",
            output_names=("operations_runbook.md", "exception_inventory.md"),
        )
    await http_client.aclose()

    # Exactly one session attempt: the deterministic error was not retried.
    assert evidence_session_attempts == 1


@pytest.mark.asyncio
async def test_staged_grounded_optional_role_failure_records_reason_and_keeps_docs(
    tmp_path: Path,
) -> None:
    """An optional role failing degrades with a recorded reason; docs still ship."""

    experiment_id = "00000000-0000-4000-8000-000000000403"
    workspace_root = tmp_path / "workspaces"
    sessions: dict[str, str] = {}
    create_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_count
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"version": "1.18.4"})
        if request.url.path == "/session":
            create_count += 1
            role_id = parse_qs(request.url.query.decode())["directory"][0].rsplit("/", 1)[-1]
            session_id = f"session-{create_count}"
            sessions[session_id] = role_id
            return httpx.Response(200, json={"id": session_id})
        if request.url.path.endswith("/prompt_async"):
            role_id = sessions[request.url.path.split("/")[2]]
            if role_id == "business-topology":
                # Optional role: the runtime is persistently unavailable.
                return httpx.Response(500, json={"error": "topology runtime unavailable"})
            _write_role_outputs(workspace_root / experiment_id / "roles" / role_id, role_id)
            return httpx.Response(204)
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=_complete_message())
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        raise AssertionError(request.url.path)

    client, http_client = _client(httpx.MockTransport(handler))
    result = await GroundedOpenCodeRunner(
        runtime=client,
        task_builder=OpenCodeGroundedTaskBuilder(),
        output_root=tmp_path / "output",
        project_root=tmp_path,
        workspace_root=workspace_root,
        execution_pack=_pack_with_topology(),
        timeout_seconds=2,
        transient_retry_limit=1,
    ).run(
        project_id="logistics",
        experiment_id=experiment_id,
        source_items=_topology_sources(tmp_path),
        provider_id="provider-a",
        model_id="model-b",
        output_names=("operations_runbook.md", "exception_inventory.md"),
    )
    await http_client.aclose()

    logical_names = {artifact.logical_name for artifact in result.artifacts}
    # Required outputs are not zeroed by the optional-role failure.
    assert "docs/operations_runbook.md" in logical_names
    assert "docs/exception_inventory.md" in logical_names
    assert "topology/business_topology.json" not in logical_names
    # The optional failure is recorded with a concrete, non-empty reason.
    optional_warnings = [
        warning
        for warning in result.safety_warnings
        if str(warning.get("category", "")).startswith("optional_role_")
    ]
    assert optional_warnings
    assert any(warning.get("detail") for warning in optional_warnings)
