from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from src.db.models import Conversation, ConversationMessage, User
from src.harness.chat import (
    detect_harness_chat_intent,
    run_harness_chat_turn,
    run_harness_chat_turn_stream,
)
from src.harness.models import (
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    EXTERNAL_CONNECTOR_READ_CAPABILITY,
    RUNTIME_EVIDENCE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
    WorkflowRunStatus,
)
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.repository import HarnessWorkflowRepository


async def _seed_conversation(db_session):
    user = User(username="harness-chat", email="harness-chat@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Harness chat")
    db_session.add(conversation)
    await db_session.flush()
    return user, conversation


def _provider(status_code: int = 200, json_body: dict | None = None) -> McpDocumentationProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/mcp/projects":
            return httpx.Response(
                200,
                json={
                    "projects": [
                        {
                            "id": "project-a",
                            "display_name": "Project A",
                            "platform": "generic",
                            "source_type": "github",
                            "status": "ready",
                            "doc_count": 3,
                            "job_count": 1,
                        },
                        {
                            "id": "project-b",
                            "display_name": "Project B",
                            "platform": "generic",
                            "source_type": "github",
                            "status": "ready",
                            "doc_count": 2,
                            "job_count": 0,
                        },
                    ],
                    "total": 2,
                },
            )
        return httpx.Response(
            status_code,
            json=json_body
            if json_body is not None
            else {
                "status": "generation_triggered",
                "message": "Documentation generation started",
            },
        )

    return McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )


def _doc_update_provider(seen: dict[str, object] | None = None) -> McpDocumentationProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["path"] = request.url.path
            seen["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "project_id": "project-a",
                "filename": "04_RUNBOOK.md",
                "status": "updated",
                "message": "Updated 04_RUNBOOK.md from provided evidence.",
                "workflow_run_id": "workflow-1",
                "evidence_refs": ["chat_prompt"],
                "before_sha": "sha256:before",
                "after_sha": "sha256:after",
                "receipt_path": "_harness_doc_updates/receipt.json",
                "docs_path": "/projects/project-a/docs/04_RUNBOOK.md",
                "rag_result": {"pushed": 1, "skipped": 0, "errors": []},
            },
        )

    return McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )


def test_detect_harness_chat_intent_is_conservative() -> None:
    assert detect_harness_chat_intent("Generate documentation for this project")
    assert detect_harness_chat_intent("please run the docs generator")
    capability_intent = detect_harness_chat_intent("u can make documentation or not?")
    assert capability_intent is not None
    assert capability_intent.mode == "capability_summary"
    assert detect_harness_chat_intent("what can you do?") is not None
    update_intent = detect_harness_chat_intent("Update the runbook using this uploaded PDF")
    assert update_intent is not None
    assert update_intent.mode == "update_docs_from_evidence"
    investigation_intent = detect_harness_chat_intent("Investigate why this integration failed recently")
    assert investigation_intent is not None
    assert investigation_intent.mode == "operational_investigation"
    connector_intent = detect_harness_chat_intent("Which runtime sources are configured?")
    assert connector_intent is not None
    assert connector_intent.mode == "connector_inventory"

    assert detect_harness_chat_intent("What do the docs say about retries?") is None
    assert detect_harness_chat_intent("Summarize the generated documentation") is None


async def test_harness_chat_turn_triggers_docs_generation_and_persists_receipt(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Generate docs for this project",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        access_token="jwt-token",
        provider=_provider(),
    )

    assert response.role == "assistant"
    assert "Documentation generation started for `project-a`" in response.content

    repo = HarnessWorkflowRepository(db_session)
    runs = await repo.list_runs(None, project_id="project-a")
    assert len(runs) == 1
    assert runs[0].status == WorkflowRunStatus.TRIGGERED
    assert runs[0].artifact_links[0]["label"] == "Generation Status"
    assert runs[0].runtime_provider_id == "langgraph.nexus"
    assert runs[0].runtime_thread_id == f"harness-run-{runs[0].id}"
    assert runs[0].runtime_run_id is not None
    assert runs[0].current_node == "finalize"
    assert runs[0].runtime_metadata["graph"] == "docs_generation"
    assert runs[0].runtime_metadata["checkpoint_id"]
    assert runs[0].artifact_receipts[0]["label"] == "Generation Status"

    events = await repo.list_activity_events(None, runs[0].id)
    assert [event.event_type for event in events] == [
        "planner.decision",
        "capability.selected",
        "scope",
        "runtime.manifest_loaded",
        "runtime.capability_selected",
        "runtime.executor_request",
        "runtime.observation",
        "runtime.completed",
    ]
    assert events[3].runtime_node == "load_manifest"
    assert events[-1].tool_call_id == runs[0].runtime_run_id

    messages = (
        await db_session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.id)
        )
    ).scalars().all()
    assert [message.role for message in messages] == ["user", "assistant"]


async def test_harness_chat_stream_emits_activity_and_done(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    events = [
        event
        async for event in run_harness_chat_turn_stream(
            conversation=conversation,
            query="Run documentation generation",
            db=db_session,
            user_id=user.id,
            project_ids=["project-a"],
            provider=_provider(json_body={"status": "generation_triggered", "message": "started"}),
        )
    ]

    event_types = [event_type for event_type, _ in events]
    assert event_types == ["harness_activity"] * 8 + ["token", "usage", "done"]

    activities = [
        json.loads(data)
        for event_type, data in events
        if event_type == "harness_activity"
    ]
    assert activities[-1]["status"] == "completed"
    assert activities[-1]["workflow_run_id"]

    done = json.loads(events[-1][1])
    assert done["message_id"] > 0


async def test_missing_project_scope_creates_interrupted_resume_ready_run(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Generate documentation",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a", "project-b"],
        provider=_provider(),
    )

    assert "Which project should I use to generate documentation?" in response.content
    assert "`project-a`" in response.content
    assert "`project-b`" in response.content
    repo = HarnessWorkflowRepository(db_session)
    runs = await repo.list_runs(None)
    assert len(runs) == 1
    run = runs[0]
    assert run.status == WorkflowRunStatus.INTERRUPTED
    assert run.runtime_provider_id == "langgraph.nexus"
    assert run.runtime_thread_id == f"harness-run-{run.id}"
    assert run.runtime_run_id == f"lg-interrupt-{run.id}"
    assert run.resume_token == run.runtime_thread_id
    assert run.current_node == "await_project_scope"
    assert run.runtime_metadata["interrupt"]["type"] == "missing_project_scope"
    assert run.runtime_metadata["interrupt"]["missing_slots"] == ["project_id"]
    assert run.runtime_metadata["interrupt"]["available_projects"] == ["project-a", "project-b"]
    assert run.runtime_metadata["resume"]["ready"] is True
    events = await repo.list_activity_events(None, run.id)
    assert [event.event_type for event in events] == ["planner.decision", "clarification.requested"]


async def test_docs_generation_without_permission_is_denied_before_mcp_call(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={"status": "generation_triggered", "message": "should not start"},
        )

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Generate docs for this project",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        user_permissions=set(),
        provider=McpDocumentationProvider(
            base_url="http://mcp.internal/mcp",
            transport=httpx.MockTransport(handler),
        ),
    )

    assert "I could not start documentation generation" in response.content
    assert seen == {}
    runs = await HarnessWorkflowRepository(db_session).list_runs(None, project_id="project-a")
    assert runs[0].status == WorkflowRunStatus.INTERRUPTED
    assert runs[0].runtime_provider_id == "langgraph.nexus"
    assert runs[0].runtime_metadata["graph"] == "docs_generation"


async def test_capability_question_answers_from_registry_without_workflow(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="u can make documentation or not?",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        provider=_provider(status_code=500, json_body={"detail": "should not be called"}),
    )

    assert "I can generate project documentation" in response.content
    assert "mcp.docs.generate_project_documentation" not in response.content
    assert "triggers_workflow" not in response.content
    assert "read-only" not in response.content.lower()
    assert await HarnessWorkflowRepository(db_session).list_runs(None) == []


async def test_named_project_in_text_resolves_scope_and_triggers_generation(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="make docs for project-b",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a", "project-b"],
        provider=_provider(),
    )

    assert "Documentation generation started for `project-b`" in response.content
    runs = await HarnessWorkflowRepository(db_session).list_runs(None, project_id="project-b")
    assert len(runs) == 1
    assert runs[0].intent_mode == "generate_docs"


async def test_doc_update_from_evidence_requires_named_markdown_doc(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Update the architecture doc using this uploaded file",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        provider=_provider(status_code=500, json_body={"detail": "should not be called"}),
    )

    assert "target markdown file" in response.content
    repo = HarnessWorkflowRepository(db_session)
    runs = await repo.list_runs(None, project_id="project-a")
    assert runs[0].intent_mode == "update_docs_from_evidence"
    assert runs[0].status == WorkflowRunStatus.FAILED
    events = await repo.list_activity_events(None, runs[0].id)
    assert [event.event_type for event in events] == ["scope", "capability_skipped"]


async def test_doc_update_from_evidence_calls_mcp_provider_and_persists_receipt(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)
    seen: dict[str, object] = {}

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Update 04_RUNBOOK.md using evidence: the nightly sync now starts at 02:00 UTC.",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        provider=_doc_update_provider(seen),
    )

    assert "Updated `04_RUNBOOK.md` for `project-a`" in response.content
    assert "_harness_doc_updates/receipt.json" in response.content
    assert seen["path"] == "/mcp/projects/project-a/docs/04_RUNBOOK.md/evidence-update"
    assert b"nightly sync now starts" in seen["payload"]

    repo = HarnessWorkflowRepository(db_session)
    runs = await repo.list_runs(None, project_id="project-a")
    assert runs[0].intent_mode == "update_docs_from_evidence"
    assert runs[0].status == WorkflowRunStatus.SUCCEEDED
    assert runs[0].artifact_links[0]["label"] == "Updated Documentation"
    events = await repo.list_activity_events(None, runs[0].id)
    assert [event.event_type for event in events] == [
        "scope",
        "evidence.parsed",
        "provider_trigger",
        "provider_result",
        "workflow.completed",
    ]


async def test_operational_investigation_records_evidence_workflow(db_session, monkeypatch) -> None:
    user, conversation = await _seed_conversation(db_session)

    async def fake_search(**_kwargs):
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="doc:1:0",
                    capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
                    source_type="rag_document",
                    title="Failure Runbook",
                    summary="Check rejected rows and connection timeouts.",
                )
            ],
        )

    async def fake_docs(**_kwargs):
        return EvidenceQueryResult(
            capability_id=DOCUMENT_LIST_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="mcp-doc:project-a:runbook.md",
                    capability_id=DOCUMENT_LIST_CAPABILITY.id,
                    source_type="generated_doc",
                    title="runbook.md",
                    summary="Generated runbook.",
                    observed_at="2026-07-09T10:00:00+00:00",
                )
            ],
        )

    async def fake_runtime(**_kwargs):
        return EvidenceQueryResult(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="runtime-failure:exec-1",
                    capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                    source_type="runtime_failure",
                    title="Runtime failure: nightly_sync",
                    summary="connection timeout",
                    observed_at="2026-07-10T10:00:00+00:00",
                    metadata={"execution_id": "exec-1", "job_name": "nightly_sync"},
                )
            ],
        )

    monkeypatch.setattr("src.harness.chat.search_document_evidence", fake_search)
    monkeypatch.setattr("src.harness.chat.list_generated_doc_evidence", fake_docs)
    monkeypatch.setattr("src.harness.chat.query_runtime_evidence", fake_runtime)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Investigate why this integration failed recently",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        user_groups=["engineering"],
        qdrant=object(),
        provider=_provider(),
    )

    assert "Operational Investigation" in response.content
    assert "runtime-failure:exec-1" in response.content
    assert "potentially stale" in response.content

    repo = HarnessWorkflowRepository(db_session)
    runs = await repo.list_runs(None, project_id="project-a")
    assert runs[0].intent_mode == "operational_investigation"
    assert runs[0].status == WorkflowRunStatus.SUCCEEDED
    events = await repo.list_activity_events(None, runs[0].id)
    assert {event.event_type for event in events} >= {
        "evidence.docs_search",
        "evidence.docs_metadata",
        "evidence.runtime",
        "workflow.completed",
    }


async def test_connector_inventory_records_read_only_connector_evidence(db_session, monkeypatch) -> None:
    user, conversation = await _seed_conversation(db_session)

    async def fake_connector_evidence(**_kwargs):
        return EvidenceQueryResult(
            capability_id=EXTERNAL_CONNECTOR_READ_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="connector:1",
                    capability_id=EXTERNAL_CONNECTOR_READ_CAPABILITY.id,
                    source_type="connector_metadata",
                    title="Talend prod",
                    summary="Talend Management Console source is healthy.",
                    metadata={
                        "source_id": 1,
                        "source_type": "tmc",
                        "status": "healthy",
                        "enabled": True,
                    },
                )
            ],
        )

    monkeypatch.setattr("src.harness.chat.read_connector_evidence", fake_connector_evidence)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Which runtime sources are configured?",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
    )

    assert "Connector Inventory" in response.content
    assert "connector:1" in response.content
    runs = await HarnessWorkflowRepository(db_session).list_runs(None, project_id="project-a")
    assert runs[0].intent_mode == "connector_inventory"


async def test_provider_failure_marks_workflow_failed(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Generate documentation",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        provider=_provider(status_code=500, json_body={"detail": "provider exploded token=secret"}),
    )

    assert "I could not start documentation generation" in response.content
    runs = await HarnessWorkflowRepository(db_session).list_runs(None, project_id="project-a")
    assert runs[0].status == WorkflowRunStatus.FAILED
    assert "secret" not in (runs[0].error_message or "")
