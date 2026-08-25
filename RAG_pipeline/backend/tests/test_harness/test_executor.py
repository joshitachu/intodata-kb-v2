from __future__ import annotations

import json

import httpx

from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.models import (
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    DOCUMENTATION_GENERATION_STATUS_CAPABILITY,
    PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY,
    STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY,
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


async def test_executor_rejects_retired_reusable_documentation_pack_creation() -> None:
    executor = HarnessCapabilityExecutor(default_capability_registry())
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id="mcp.docs.create_documentation_pack",
            arguments={"name": "Operations Pack", "documents": []},
        ),
        CapabilityExecutionContext(user_id=1, permissions={"manage_projects"}),
    )

    assert observation.status == "denied"
    assert "Unknown capability" in (observation.error_message or "")


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


async def test_executor_lists_active_workflows_with_server_owned_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/projects":
            return httpx.Response(
                200,
                json={
                    "projects": [
                        {"id": "allowed", "display_name": "Allowed", "status": "scanning"},
                        {"id": "other", "display_name": "Other", "status": "scanning"},
                    ]
                },
            )
        if request.url.path == "/mcp/generate-runs/allowed":
            return httpx.Response(200, json={"runs": []})
        raise AssertionError(f"scope leak: {request.url.path}")

    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        docs_provider=McpDocumentationProvider(
            base_url="http://mcp.internal/mcp",
            transport=httpx.MockTransport(handler),
        ),
    )

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PROJECT_ACTIVE_WORKFLOW_STATUS_CAPABILITY.id,
            arguments={},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["allowed"]),
    )

    assert observation.status == "succeeded"
    assert observation.data["workflows"][0]["project_id"] == "allowed"
    assert observation.safe_details == {"workflow_count": 1}


async def test_executor_preserves_failed_evidence_handler_status() -> None:
    async def failed_handler(_arguments, _context):
        return EvidenceQueryResult(
            capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
            status="failed",
            error_message="Runtime provider unavailable",
        )

    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        evidence_handlers={STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id: failed_handler},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=STRUCTURED_RUNTIME_EVIDENCE_CAPABILITY.id,
            arguments={
                "operation": "list_runtime_sources",
                "project_id": "project-a",
            },
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
    assert invalid.status == "needs_input"
    assert invalid.retryable is True
    assert invalid.error_code == "validation"
    assert invalid.safe_details["field_path"] == "project_id"

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
    assert out_of_scope.error_code == "scope"
    assert "Project access required" in out_of_scope.error_message


async def test_executor_restores_unique_case_insensitive_authorized_project_id() -> None:
    seen: dict[str, object] = {}

    async def evidence_handler(arguments, _context):  # noqa: ANN001
        seen.update(arguments)
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[],
        )

    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: evidence_handler},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            arguments={"query": "overview", "project_id": "intodatastreams"},
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["IntodataStreams"],
        ),
    )

    assert observation.status == "succeeded"
    assert seen["project_id"] == "IntodataStreams"


async def test_executor_pins_document_read_alias_to_the_single_authorized_project() -> None:
    seen: dict[str, object] = {}

    async def evidence_handler(arguments, _context):  # noqa: ANN001
        seen.update(arguments)
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[],
        )

    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: evidence_handler},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            arguments={
                "query": "overview",
                "project_id": "Scherpenhuizen Talend Estate",
            },
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["scherpenhuizen_talend"],
        ),
    )

    assert observation.status == "succeeded"
    assert seen["project_id"] == "scherpenhuizen_talend"


async def test_executor_does_not_guess_between_case_colliding_authorized_projects() -> None:
    calls = 0

    async def evidence_handler(_arguments, _context):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[],
        )

    executor = HarnessCapabilityExecutor(
        default_capability_registry(),
        evidence_handlers={DOCUMENT_SEARCH_CAPABILITY.id: evidence_handler},
    )
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            arguments={"query": "overview", "project_id": "PROJECTX"},
        ),
        CapabilityExecutionContext(
            user_id=1,
            project_scope=["ProjectX", "projectx"],
        ),
    )

    assert observation.status == "denied"
    assert observation.error_code == "scope"
    assert calls == 0


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
    assert observation.error_code == "allowlist"
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
