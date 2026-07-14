from __future__ import annotations

from types import SimpleNamespace
import json

import httpx
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import Conversation, User
from src.harness.chat import plan_model_harness_chat_turn, run_harness_chat_turn
from src.harness.connectors import (
    ConnectorRepository,
    ConnectorService,
    default_connector_provider_registry,
)
from src.harness.connectors.capabilities import build_request_capability_registry
from src.harness.connectors.fixture import FIXTURE_CAPABILITY_ID
from src.harness.connectors.models import (
    CapabilityBindingCreate,
    ConnectorInstanceCreate,
    ConnectorInstanceUpdate,
)
from src.harness.evidence_store import HarnessEvidenceRepository
from src.harness.executor import CapabilityExecutionContext, CapabilityExecutionRequest, HarnessCapabilityExecutor
from src.harness.models import WorkflowRunStatus
from src.harness.models import DOCUMENTATION_GENERATION_CAPABILITY
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.providers.mcp_projects import McpProjectsProvider
from src.harness.repository import HarnessWorkflowRepository


CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


async def _seed(db_session, *, suffix: str):
    user = User(
        username=f"harness-e2e-{suffix}",
        email=f"harness-e2e-{suffix}@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Harness E2E")
    db_session.add(conversation)
    await db_session.flush()
    return user, conversation


def _auth(user):
    return SimpleNamespace(
        id=user.id,
        username=user.username,
        email=user.email,
        is_superadmin=True,
        roles=[],
    )


async def test_unscoped_docs_resume_executes_same_workflow_once(db_session, monkeypatch) -> None:
    user, conversation = await _seed(db_session, suffix="resume-docs")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "generation_triggered", "message": "started"})

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )
    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Generate documentation",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a", "project-b"],
        user_permissions={"manage_projects"},
        provider=provider,
    )
    assert "Which project" in response.content
    repo = HarnessWorkflowRepository(db_session)
    run = (await repo.list_runs(None))[0]
    interrupt = (await repo.list_pending_interrupts_for_owner(run.id, user.id))[0]
    monkeypatch.setattr("src.harness.chat.McpDocumentationProvider", lambda: provider)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _auth(user)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resumed = await client.post(
                f"/api/v1/harness/workflows/{run.id}/resume",
                json={
                    "interrupt_id": interrupt.id,
                    "expected_version": run.version,
                    "response": "project-a",
                },
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run"]["id"] == run.id
    assert resumed.json()["run"]["project_id"] == "project-a"
    assert resumed.json()["run"]["status"] == "triggered", resumed.json()
    assert seen == ["/mcp/generate"]


async def test_model_planner_is_primary_for_non_regex_paraphrase(db_session, monkeypatch) -> None:
    _, conversation = await _seed(db_session, suffix="model-router")

    async def fake_model(_messages, _tools):
        return ToolAwareLLMResponse(
            provider="fixture",
            tool_calls=[
                RuntimeToolCallRequest(
                    call_id="call-1",
                    tool_name=DOCUMENTATION_GENERATION_CAPABILITY.id,
                    arguments={"project_id": "project-a"},
                )
            ],
        )

    monkeypatch.setattr(
        "src.harness.planner.generate_answer_with_runtime_tools", fake_model
    )
    decision = await plan_model_harness_chat_turn(
        query="Please prepare the complete technical material for our selected workspace",
        conversation=conversation,
        db=db_session,
        project_ids=["project-a"],
        user_permissions={"manage_projects"},
        access_token=None,
        provider=McpDocumentationProvider(base_url="http://unused"),
    )

    assert decision.mode == "generate_docs"
    assert decision.capability_id == DOCUMENTATION_GENERATION_CAPABILITY.id
    assert decision.arguments == {"project_id": "project-a"}


async def test_document_update_requires_approval_and_resumes_once(db_session, monkeypatch) -> None:
    user, conversation = await _seed(db_session, suffix="approval")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"filename": "04_RUNBOOK.md", "content": "# Runbook\n"},
            )
        return httpx.Response(
            200,
            json={
                "project_id": "project-a",
                "filename": "04_RUNBOOK.md",
                "status": "updated",
                "message": "Updated runbook.",
                "workflow_run_id": "1",
                "evidence_refs": ["chat_prompt"],
                "before_sha": "before",
                "after_sha": "after",
                "receipt_path": "/receipts/update-1",
                "docs_path": "/projects/project-a/docs/04_RUNBOOK.md",
                "rag_result": {"pushed": 1},
            },
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(handler)
    )
    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Update 04_RUNBOOK.md using evidence: schedule is now 03:00 UTC.",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        user_permissions={"manage_projects"},
        provider=provider,
    )
    assert "approve" in response.content.lower()
    assert seen == ["/mcp/projects/project-a/docs/04_RUNBOOK.md"]
    repo = HarnessWorkflowRepository(db_session)
    run = (await repo.list_runs(None))[0]
    assert "schedule is now" not in json.dumps(run.model_dump(mode="json"))
    interrupt = (await repo.list_pending_interrupts_for_owner(run.id, user.id))[0]
    monkeypatch.setattr("src.harness.chat.McpDocumentationProvider", lambda: provider)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _auth(user)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            approved = await client.post(
                f"/api/v1/harness/workflows/{run.id}/resume",
                json={
                    "interrupt_id": interrupt.id,
                    "expected_version": run.version,
                    "response": "approve",
                },
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert approved.status_code == 200, approved.text
    assert approved.json()["run"]["status"] == "succeeded"
    assert seen == [
        "/mcp/projects/project-a/docs/04_RUNBOOK.md",
        "/mcp/projects/project-a/docs/04_RUNBOOK.md/evidence-update",
    ]


async def test_project_onboarding_runs_attach_scan_docs_and_index(db_session, monkeypatch) -> None:
    user, conversation = await _seed(db_session, suffix="onboarding")
    project_calls: list[str] = []
    docs_calls: list[str] = []
    all_calls: list[str] = []
    status_calls = 0

    def project_handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        project_calls.append(request.url.path)
        all_calls.append(request.url.path)
        if request.url.path.endswith("/add-repo"):
            return httpx.Response(
                200,
                json={"id": "test_env", "status": "ready", "platform": "generic", "source_type": "git"},
            )
        if request.url.path.endswith("/scan"):
            return httpx.Response(200, json={"status": "scan_triggered", "message": "scan started"})
        if request.url.path.endswith("/status/test_env"):
            status_calls += 1
            if status_calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "project_id": "test_env",
                        "status": "scanning",
                        "job_count": 0,
                        "doc_count": 0,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "project_id": "test_env",
                    "status": "ready",
                    "last_scan_at": "2026-07-11T10:00:00+00:00",
                    "job_count": 3,
                    "doc_count": 2,
                },
            )
        if request.url.path.endswith("/push-to-rag"):
            return httpx.Response(200, json={"status": "indexed", "message": "indexed"})
        raise AssertionError(request.url.path)

    def docs_handler(request: httpx.Request) -> httpx.Response:
        docs_calls.append(request.url.path)
        all_calls.append(request.url.path)
        if request.url.path.endswith("/generate-runs/test_env"):
            return httpx.Response(
                200,
                json={"runs": [{"id": 1, "project_id": "test_env", "status": "completed"}]},
            )
        return httpx.Response(200, json={"status": "generation_triggered", "message": "docs started"})

    projects_provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(project_handler),
    )
    docs_provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(docs_handler),
    )
    monkeypatch.setattr("src.harness.chat.McpProjectsProvider", lambda: projects_provider)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Create project called test_env from https://github.com/example/test-env.git",
        db=db_session,
        user_id=user.id,
        project_ids=[],
        user_permissions={"manage_projects"},
        provider=docs_provider,
    )

    assert "test_env" in response.content
    assert project_calls == [
        "/mcp/add-repo",
        "/mcp/scan",
        "/mcp/status/test_env",
        "/mcp/status/test_env",
        "/mcp/push-to-rag",
    ]
    assert docs_calls == ["/mcp/generate", "/mcp/generate-runs/test_env"]
    assert all_calls.index("/mcp/generate") > all_calls.index("/mcp/status/test_env")
    run = (await HarnessWorkflowRepository(db_session).list_runs(None))[0]
    assert run.intent_mode == "project_onboarding"
    assert run.status == WorkflowRunStatus.TRIGGERED
    assert {item["kind"] for item in run.artifact_links} >= {"project", "scan", "docs", "index"}


async def test_project_onboarding_missing_source_resumes_same_run(db_session, monkeypatch) -> None:
    user, conversation = await _seed(db_session, suffix="onboarding-resume")
    calls: list[str] = []

    def project_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/add-repo"):
            return httpx.Response(200, json={"id": "test_env", "status": "ready", "platform": "generic", "source_type": "git"})
        if request.url.path.endswith("/scan"):
            return httpx.Response(200, json={"status": "scan_triggered", "message": "scan started"})
        if request.url.path.endswith("/status/test_env"):
            return httpx.Response(200, json={"project_id": "test_env", "status": "ready", "job_count": 1, "doc_count": 1})
        if request.url.path.endswith("/push-to-rag"):
            return httpx.Response(200, json={"status": "indexed", "message": "indexed"})
        raise AssertionError(request.url.path)

    def docs_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/generate-runs/test_env"):
            return httpx.Response(
                200,
                json={"runs": [{"id": 1, "project_id": "test_env", "status": "completed"}]},
            )
        return httpx.Response(200, json={"status": "generation_triggered", "message": "docs started"})

    projects_provider = McpProjectsProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(project_handler)
    )
    docs_provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp", transport=httpx.MockTransport(docs_handler)
    )
    initial = await run_harness_chat_turn(
        conversation=conversation,
        query="Create project called test_env",
        db=db_session,
        user_id=user.id,
        project_ids=[],
        user_permissions={"manage_projects"},
        provider=docs_provider,
    )
    assert "Git repository or upload a ZIP" in initial.content
    repo = HarnessWorkflowRepository(db_session)
    run = (await repo.list_runs(None))[0]
    interrupt = (await repo.list_pending_interrupts_for_owner(run.id, user.id))[0]
    monkeypatch.setattr("src.harness.chat.McpProjectsProvider", lambda: projects_provider)
    monkeypatch.setattr("src.harness.chat.McpDocumentationProvider", lambda: docs_provider)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: _auth(user)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            source_selected = await client.post(
                f"/api/v1/harness/workflows/{run.id}/resume",
                json={
                    "interrupt_id": interrupt.id,
                    "expected_version": run.version,
                    "response": "git",
                },
                headers=CSRF_HEADERS,
            )
            assert source_selected.status_code == 200, source_selected.text
            selected_run = source_selected.json()["run"]
            next_interrupt = (
                await repo.list_pending_interrupts_for_owner(run.id, user.id)
            )[0]
            resumed = await client.post(
                f"/api/v1/harness/workflows/{run.id}/resume",
                json={
                    "interrupt_id": next_interrupt.id,
                    "expected_version": selected_run["version"],
                    "response": "https://github.com/example/test-env.git",
                },
                headers=CSRF_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run"]["id"] == run.id
    remaining_interrupts = await repo.list_pending_interrupts_for_owner(run.id, user.id)
    assert not remaining_interrupts, [
        (item.prompt, item.scope_snapshot) for item in remaining_interrupts
    ]
    assert resumed.json()["run"]["status"] == "triggered", resumed.json()
    assert calls == [
        "/mcp/add-repo",
        "/mcp/scan",
        "/mcp/status/test_env",
        "/mcp/generate",
        "/mcp/generate-runs/test_env",
        "/mcp/push-to-rag",
    ]


async def test_connector_enable_health_manifest_and_stale_execution_guard(db_session) -> None:
    repo = ConnectorRepository(db_session)
    service = ConnectorService(repo, default_connector_provider_registry())
    row = await service.create(
        ConnectorInstanceCreate(
            name="Fixture KB",
            provider_key="fixture_knowledge",
            config={"dataset": "default"},
        )
    )
    await service.bind(
        row.id,
        CapabilityBindingCreate(
            capability_id=FIXTURE_CAPABILITY_ID,
            project_ids=["project-a"],
            domains=["operations"],
        ),
    )
    await service.check_health(row.id)
    registry = await build_request_capability_registry(repo, project_ids=["project-a"])
    dynamic = next(item for item in registry.list() if item.provider_id == "nexus.connector")
    executor = HarnessCapabilityExecutor(registry, connector_service=service)
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=dynamic.id,
            arguments={"query": "payments", "project_id": "project-a"},
        ),
        CapabilityExecutionContext(
            user_id=1, project_scope=["project-a"], domain="operations"
        ),
    )
    assert observation.succeeded
    assert observation.data["items"][0]["title"] == "Payments API runbook"

    domain_denied = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=dynamic.id,
            arguments={"query": "payments", "project_id": "project-a"},
        ),
        CapabilityExecutionContext(
            user_id=1, project_scope=["project-a"], domain="documentation"
        ),
    )
    assert domain_denied.status == "denied"

    await service.update(row.id, ConnectorInstanceUpdate(enabled=False))
    hidden = await build_request_capability_registry(repo, project_ids=["project-a"])
    assert dynamic.id not in {item.id for item in hidden.list()}
    denied = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=dynamic.id,
            arguments={"query": "payments", "project_id": "project-a"},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )
    assert denied.status == "denied"


async def test_conflicting_docs_and_runtime_are_persisted_and_disclosed(db_session, monkeypatch) -> None:
    from src.harness.models import (
        DOCUMENT_LIST_CAPABILITY,
        DOCUMENT_SEARCH_CAPABILITY,
        RUNTIME_EVIDENCE_CAPABILITY,
        EvidenceItem,
        EvidenceQueryResult,
    )

    user, conversation = await _seed(db_session, suffix="conflict")

    async def docs_search(**_kwargs):
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="docs-schedule",
                    capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
                    source_type="rag_document",
                    title="Schedule runbook",
                    summary="The nightly job starts at 02:00 UTC.",
                )
            ],
        )

    async def docs_list(**_kwargs):
        return EvidenceQueryResult(capability_id=DOCUMENT_LIST_CAPABILITY.id, status="succeeded")

    async def runtime(**_kwargs):
        return EvidenceQueryResult(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="runtime-schedule",
                    capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                    source_type="runtime_status",
                    title="Current schedule",
                    summary="The current runtime schedule starts at 03:00 UTC.",
                )
            ],
        )

    monkeypatch.setattr("src.harness.chat.search_document_evidence", docs_search)
    monkeypatch.setattr("src.harness.chat.list_generated_doc_evidence", docs_list)
    monkeypatch.setattr("src.harness.chat.query_runtime_evidence", runtime)
    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Investigate why the nightly schedule failed and is different",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        qdrant=object(),
        provider=McpDocumentationProvider(base_url="http://unused"),
    )
    assert "Sources conflict about schedule" in response.content
    assert "02:00" in response.content
    assert "03:00" in response.content
    run = (await HarnessWorkflowRepository(db_session).list_runs(None))[0]
    evidence = await HarnessEvidenceRepository(db_session).list_collected(run.id)
    assert len(evidence) == 2
    assert len({item.conflict_group for item in evidence}) == 1
    assert evidence[0].conflict_group is not None
