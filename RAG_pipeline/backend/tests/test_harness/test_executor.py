from __future__ import annotations

import httpx

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.models import DOCUMENTATION_GENERATION_CAPABILITY
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.registry import default_capability_registry


def _provider(seen: dict[str, object] | None = None) -> McpDocumentationProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["path"] = request.url.path
            seen["payload"] = request.read()
        return httpx.Response(
            200,
            json={"status": "generation_triggered", "message": "started safely"},
        )

    return McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )


async def test_executor_triggers_docs_generation_through_provider() -> None:
    seen: dict[str, object] = {}
    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        docs_provider=_provider(seen),
    )

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-a", "resume": True},
            idempotency_key="run-1",
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-a"],
            permissions={"manage_projects"},
        ),
    )

    assert observation.status == "triggered"
    assert observation.summary == "started safely"
    assert observation.artifact_links[0]["url"] == "/mcp/generate-runs/project-a"
    assert seen["path"] == "/mcp/generate"


async def test_executor_rejects_unknown_invalid_and_out_of_scope_calls() -> None:
    executor = HarnessCapabilityExecutor(default_capability_registry(), docs_provider=_provider())

    unknown = await executor.execute(
        CapabilityExecutionRequest(capability_id="missing", arguments={}),
        CapabilityExecutionContext(user_id=1),
    )
    assert unknown.status == "denied"
    assert "Unknown capability" in unknown.error_message

    invalid = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"resume": True},
        ),
        CapabilityExecutionContext(user_id=1, permissions={"manage_projects"}),
    )
    assert invalid.status == "denied"
    assert "Missing required input: project_id" in invalid.error_message

    out_of_scope = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-b"},
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-a"],
            permissions={"manage_projects"},
        ),
    )
    assert out_of_scope.status == "denied"
    assert "Project access required" in out_of_scope.error_message


async def test_executor_sanitizes_provider_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"detail": "failed with token=secret-value"},
        )

    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        docs_provider=McpDocumentationProvider(
            base_url="http://mcp.internal/mcp",
            transport=httpx.MockTransport(handler),
        ),
    )

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            arguments={"project_id": "project-a"},
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["project-a"],
            permissions={"manage_projects"},
        ),
    )

    assert observation.status == "failed"
    assert "secret-value" not in (observation.error_message or "")
