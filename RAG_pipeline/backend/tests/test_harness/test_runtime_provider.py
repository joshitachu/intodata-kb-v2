from __future__ import annotations

import asyncio

import httpx

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityObservation,
    HarnessCapabilityExecutor,
)
from src.harness.manifest import ManifestScope, build_runtime_capability_manifest
from src.harness.models import DOCUMENTATION_GENERATION_CAPABILITY
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.registry import default_capability_registry
from src.harness.runtime import LangGraphRuntimeProvider, RuntimeTurnRequest


async def test_langgraph_runtime_boundary_uses_manifest_and_executor_only() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={"status": "generation_triggered", "message": "docs started"},
        )

    registry = default_capability_registry()
    manifest = build_runtime_capability_manifest(
        registry,
        scope=ManifestScope(permissions={"manage_projects"}),
    )
    executor = HarnessCapabilityExecutor(
        registry,
        docs_provider=McpDocumentationProvider(
            base_url="http://mcp.internal/mcp",
            transport=httpx.MockTransport(handler),
        ),
    )
    runtime = LangGraphRuntimeProvider()

    result = await runtime.run_turn(
        RuntimeTurnRequest(
            message="generate docs",
            manifest=manifest,
            planned_capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-a"},
            thread_id="thread-1",
        ),
        executor=executor,
        context=CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-a"],
            permissions={"manage_projects"},
        ),
    )

    assert result.runtime_provider == "langgraph.nexus"
    assert result.status == "triggered"
    assert result.selected_capability_id == DOCUMENTATION_GENERATION_CAPABILITY.id
    assert result.observation is not None
    assert result.observation.summary == "docs started"
    assert seen["path"] == "/mcp/generate"
    assert [item["event_type"] for item in result.activity] == [
        "runtime.manifest_loaded",
        "runtime.capability_selected",
        "runtime.executor_request",
        "runtime.observation",
        "runtime.completed",
    ]
    assert result.thread_id == "thread-1"
    assert result.runtime_run_id is not None
    assert result.current_node == "finalize"
    assert result.runtime_metadata["graph"] == "docs_generation"
    assert result.runtime_metadata["checkpoint_id"]
    assert result.runtime_metadata["budget"]["step_limit"] == 10
    assert result.runtime_metadata["budget"]["timeout_seconds"] == 30.0


async def test_langgraph_runtime_does_not_select_unlisted_capability() -> None:
    runtime = LangGraphRuntimeProvider()

    result = await runtime.run_turn(
        RuntimeTurnRequest(
            message="generate docs",
            manifest=build_runtime_capability_manifest(
                default_capability_registry(),
                scope=ManifestScope(permissions=set(), packs={"operations"}),
            ),
            planned_capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-a"},
        ),
        executor=HarnessCapabilityExecutor(default_capability_registry()),
        context=CapabilityExecutionContext(user_id=1, permissions=set()),
    )

    assert result.status == "interrupted"
    assert result.observation is None
    assert result.runtime_metadata["checkpoint_id"]
    assert [item["event_type"] for item in result.activity] == [
        "runtime.manifest_loaded",
        "runtime.interrupted",
        "runtime.paused",
    ]


async def test_langgraph_runtime_timeout_returns_safe_failure() -> None:
    class SlowExecutor:
        async def execute(self, request, context):  # noqa: ANN001
            await asyncio.sleep(0.1)
            return CapabilityObservation(
                capability_id=request.capability_id,
                status="triggered",
                summary="too late",
            )

    registry = default_capability_registry()
    runtime = LangGraphRuntimeProvider()

    result = await runtime.run_turn(
        RuntimeTurnRequest(
            message="generate docs",
            manifest=build_runtime_capability_manifest(
                registry,
                scope=ManifestScope(permissions={"manage_projects"}),
            ),
            planned_capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-a"},
            thread_id="thread-timeout",
            timeout_seconds=0.01,
            step_limit=6,
        ),
        executor=SlowExecutor(),
        context=CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-a"],
            permissions={"manage_projects"},
        ),
    )

    assert result.status == "failed"
    assert result.thread_id == "thread-timeout"
    assert result.current_node == "timeout"
    assert result.runtime_metadata == {
        "graph": "docs_generation",
        "error": {"type": "timeout"},
        "budget": {"step_limit": 6, "timeout_seconds": 0.01},
    }
    assert [item["event_type"] for item in result.activity] == ["runtime.failed"]
    assert result.activity[0]["safe_details"] == {"error_type": "timeout"}
