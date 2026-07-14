from __future__ import annotations

import json

import httpx

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.models import (
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    RUNTIME_EVIDENCE_CAPABILITY,
    EvidenceQueryResult,
)
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
            arguments={"project_id": "project-a", "resume": True, "plugin_pack_id": 7},
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
    payload = json.loads(seen["payload"])
    assert payload["plugin_pack_id"] == 7


async def test_executor_reads_structured_generation_status() -> None:
    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "runs": [
                        {
                            "id": 41,
                            "project_id": "project-a",
                            "status": "completed",
                            "doc_count": 10,
                            "quality_score": 3.2,
                            "run_verdict": "failed",
                            "gate_results": {"client_ready": False},
                        }
                    ],
                    "total": 1,
                },
            )
        ),
    )
    executor = HarnessCapabilityExecutor(
        default_capability_registry(), docs_provider=provider
    )

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id,
            arguments={"project_id": "project-a"},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )

    assert observation.status == "succeeded"
    assert observation.data["external_run_id"] == 41
    assert observation.data["doc_count"] == 10
    assert observation.data["client_ready"] is False


async def test_executor_preserves_failed_evidence_handler_status() -> None:
    async def failed_handler(_arguments, _context):
        return EvidenceQueryResult(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            status="failed",
            error_message="Runtime provider unavailable",
        )

    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        evidence_handlers={RUNTIME_EVIDENCE_CAPABILITY.id: failed_handler},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            arguments={"question": "Show current state", "project_id": "project-a"},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )

    assert observation.status == "failed"
    assert observation.error_message == "Runtime provider unavailable"


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


async def test_executor_enforces_turn_capability_policy_before_provider_call() -> None:
    seen: dict[str, object] = {}
    executor = HarnessCapabilityExecutor(
        default_capability_registry(), docs_provider=_provider(seen)
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
            allowed_capability_ids={DOCUMENTATION_GENERATION_STATUS_CAPABILITY.id},
        ),
    )

    assert observation.status == "denied"
    assert "outside the turn evidence policy" in (observation.error_message or "")
    assert seen == {}


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
