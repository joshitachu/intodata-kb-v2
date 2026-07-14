from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from src.db.models import Conversation, ConversationMessage, HarnessConversationContinuationRow, User
from src.api.routes.conversations import get_conversation
from src.harness.chat import (
    HarnessPlannerDecision,
    _recompose_persisted_evidence,
    _scope_safe_fallback_decision,
    _with_turn_plan,
    detect_harness_chat_intent,
    plan_model_harness_chat_turn,
    resume_documentation_update_workflow,
    plan_harness_chat_turn,
    run_harness_chat_turn,
    run_harness_chat_turn_stream,
)
from src.harness.resolution import (
    ConversationalFocus,
    EntityReference,
    EntityType,
    WorkflowKind,
)
from src.harness.models import (
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    DOCUMENTATION_GENERATION_CAPABILITY,
    EXTERNAL_CONNECTOR_READ_CAPABILITY,
    OPERATIONAL_INVESTIGATION_CAPABILITY,
    PROJECT_ONBOARDING_CAPABILITY,
    RUNTIME_EVIDENCE_CAPABILITY,
    EvidenceItem,
    EvidenceQueryResult,
    WorkflowRunStatus,
    WorkflowRunCreate,
)
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.repository import HarnessWorkflowRepository
from src.harness.planner import (
    EvidencePolicy,
    ModelPlannerDecision,
    PlannedToolCall,
    PlannerAction,
    PlannerTurnResult,
    TurnExecutionMode,
    TurnPlan,
    TurnTaskType,
)


async def _seed_conversation(db_session):
    user = User(username="harness-chat", email="harness-chat@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Harness chat")
    db_session.add(conversation)
    await db_session.flush()
    return user, conversation


def test_fully_scoped_runtime_plan_persists_time_without_missing_slots() -> None:
    decision = _with_turn_plan(
        HarnessPlannerDecision(
            action="call_capability",
            mode="runtime_answer",
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            arguments={"project_id": "cdc", "question": "Show ESB_PROD"},
            confidence=1,
        ),
        query="In project cdc show ESB_PROD over the last 2 days",
        project_ids=None,
    )

    assert decision.turn_plan is not None
    assert decision.turn_plan.missing_slots == []
    assert decision.turn_plan.resolved_scope["project_id"] == "cdc"
    assert decision.turn_plan.resolved_scope["time_window"]["expression"] == "last 2 days"


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
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "filename": "04_RUNBOOK.md",
                    "content": "# Runbook\n\n## Recovery\n\nOld procedure.\n",
                },
            )
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
                "version_id": "sha256:after",
                "section": None,
                "revert_path": "/projects/project-a/docs/04_RUNBOOK.md/evidence-revert",
                "reconciliation_status": "indexed",
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


def test_fallback_preserves_focused_operational_scope() -> None:
    fallback = _scope_safe_fallback_decision(
        query="Check the same project over the last 30 days instead.",
        fallback=plan_harness_chat_turn(
            "Check the same project over the last 30 days instead."
        ),
        focus=ConversationalFocus(
            project=EntityReference(
                entity_type=EntityType.PROJECT,
                stable_id="reingest-drill",
                canonical_name="reingest-drill",
            ),
            workflow_kind=WorkflowKind.RUNTIME_HEALTH,
        ),
    )

    assert fallback.mode == "operational_investigation"
    assert fallback.arguments == {"project_id": "reingest-drill"}


async def test_zip_onboarding_routes_to_the_onboarding_workflow(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)

    decision = await plan_model_harness_chat_turn(
        query="Create acceptance_zip from a ZIP file instead of GitHub.",
        conversation=conversation,
        db=db_session,
        project_ids=["project-a"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.mode == "project_onboarding"
    assert decision.action == "call_capability"
    assert decision.arguments["project_id"] == "acceptance_zip"
    assert decision.capability_id == PROJECT_ONBOARDING_CAPABILITY.id


async def test_unavailable_named_connector_cannot_be_answered_as_if_searched(
    db_session,
) -> None:
    _, conversation = await _seed_conversation(db_session)

    decision = await plan_model_harness_chat_turn(
        query="Search Slack for messages about the reingest-drill database timeout.",
        conversation=conversation,
        db=db_session,
        project_ids=["reingest-drill"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.mode == "operational_investigation"
    assert decision.action == "call_capability"
    assert decision.decision_source == "capability_guard"


async def test_conversation_74_docs_inventory_plan_denies_runtime(db_session, monkeypatch) -> None:
    _, conversation = await _seed_conversation(db_session)

    async def fake_plan(**_kwargs):
        return PlannerTurnResult(
            decision=ModelPlannerDecision(
                action=PlannerAction.EXECUTE,
                confidence=0.95,
                tool_calls=[
                    PlannedToolCall(
                        capability_id=DOCUMENT_LIST_CAPABILITY.id,
                        arguments={"project_id": "alitalend"},
                    )
                ],
            )
        )

    monkeypatch.setattr("src.harness.chat.plan_harness_turn_detailed", fake_plan)
    decision = await plan_model_harness_chat_turn(
        query=(
            "give me a list of all the jobs in this project and what they do, "
            "and what they connect to; do not use runtime logs, use the documentation"
        ),
        conversation=conversation,
        db=db_session,
        project_ids=["alitalend"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.action == "use_rag"
    assert decision.mode == "document_answer"
    assert decision.turn_plan is not None
    assert decision.turn_plan.task_type.value == "document_answer"
    assert decision.turn_plan.allows("indexed_documentation")
    assert not decision.turn_plan.allows("runtime_evidence")


async def test_conversation_81_migration_followup_stays_document_scoped(
    db_session, monkeypatch
) -> None:
    _, conversation = await _seed_conversation(db_session)
    db_session.add(
        ConversationMessage(
            conversation=conversation,
            role="user",
            content="Help me migrate alitalend to Azure Data Factory.",
            turn_plan={
                "version": 1,
                "objective": "Help me migrate alitalend to Azure Data Factory.",
                "task_type": "document_answer",
                "resolved_scope": {
                    "project_id": "alitalend",
                    "project_ids": ["alitalend"],
                },
                "evidence_policy": {
                    "allowed": ["indexed_documentation", "generated_documentation"],
                    "denied": ["runtime_evidence", "connector_evidence"],
                    "preferred": ["indexed_documentation"],
                    "required": [],
                },
                "execution_mode": "evidence",
                "output_contract": "migration_proposal",
                "confidence": 0.9,
                "missing_slots": [],
                "continuation": {"state": "none"},
                "budget": {
                    "max_tool_calls": 3,
                    "max_evidence_items": 30,
                    "max_duration_ms": 30000,
                },
                "capability_ids": [],
                "decision_source": "model",
            },
        )
    )
    await db_session.flush()

    async def fake_plan(**_kwargs):
        return PlannerTurnResult(
            decision=ModelPlannerDecision(
                action=PlannerAction.CLARIFY,
                confidence=0.8,
                clarification="Which approach should I use?",
            )
        )

    monkeypatch.setattr("src.harness.chat.plan_harness_turn_detailed", fake_plan)
    decision = await plan_model_harness_chat_turn(
        query="option 1 and 2",
        conversation=conversation,
        db=db_session,
        project_ids=["alitalend"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.action == "use_rag"
    assert decision.mode == "document_answer"
    assert decision.turn_plan is not None
    assert decision.turn_plan.output_contract == "migration_proposal"
    assert decision.turn_plan.objective == "Help me migrate alitalend to Azure Data Factory."
    assert decision.turn_plan.continuation["state"] == "continued"
    assert "runtime_evidence" in decision.turn_plan.evidence_policy.denied


async def test_recomposition_guard_overrides_model_continue_without_new_evidence(
    db_session, monkeypatch
) -> None:
    _, conversation = await _seed_conversation(db_session)
    prior_plan = TurnPlan(
        objective="List documented jobs and connections.",
        task_type=TurnTaskType.DOCUMENT_ANSWER,
        resolved_scope={"project_id": "alitalend", "project_ids": ["alitalend"]},
        evidence_policy=EvidencePolicy(
            allowed=["indexed_documentation", "generated_documentation"],
            denied=["runtime_evidence", "connector_evidence"],
            preferred=["indexed_documentation"],
            required=[],
        ),
        execution_mode=TurnExecutionMode.EVIDENCE,
        output_contract="component_inventory",
        confidence=0.9,
    )
    db_session.add(
        ConversationMessage(
            conversation=conversation,
            role="assistant",
            content="Prior inventory",
            turn_plan=prior_plan.model_dump(mode="json"),
        )
    )
    await db_session.flush()

    async def fake_plan(**_kwargs):
        return PlannerTurnResult(
            decision=ModelPlannerDecision(
                action=PlannerAction.CONTINUE,
                confidence=0.8,
            )
        )

    monkeypatch.setattr("src.harness.chat.plan_harness_turn_detailed", fake_plan)
    decision = await plan_model_harness_chat_turn(
        query="Give me better output",
        conversation=conversation,
        db=db_session,
        project_ids=["alitalend"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.action == "use_rag"
    assert decision.mode == "document_answer"
    assert decision.matched_rule == "evidence_recomposition"
    assert decision.turn_plan is not None
    assert decision.turn_plan.objective == prior_plan.objective
    assert decision.turn_plan.output_contract == "component_inventory"
    assert decision.turn_plan.continuation["state"] == "continued"
    assert "runtime_evidence" in decision.turn_plan.evidence_policy.denied


async def test_runtime_show_results_reuses_prior_run_and_policy(
    db_session, monkeypatch
) -> None:
    user, conversation = await _seed_conversation(db_session)
    run = await HarnessWorkflowRepository(db_session).create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="cdc",
            intent_mode="evidence_investigation",
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            provider_id=RUNTIME_EVIDENCE_CAPABILITY.provider_id,
            status=WorkflowRunStatus.SUCCEEDED,
        )
    )
    prior_plan = TurnPlan(
        objective="Show ESB_PROD executions and health for the last two days.",
        task_type=TurnTaskType.RUNTIME_ANSWER,
        resolved_scope={"project_id": "cdc", "time_window": {"start": "2026-07-09T00:00:00Z", "end": "2026-07-11T00:00:00Z"}},
        evidence_policy=EvidencePolicy(
            allowed=["runtime_evidence"],
            denied=["indexed_documentation", "generated_documentation", "connector_evidence"],
            preferred=["runtime_evidence"],
            required=["runtime_evidence"],
        ),
        execution_mode=TurnExecutionMode.EVIDENCE,
        output_contract="runtime_answer",
        confidence=1,
    )
    db_session.add(
        ConversationMessage(
            conversation=conversation,
            role="assistant",
            content="Prior runtime result",
            workflow_run_id=run.id,
            turn_plan=prior_plan.model_dump(mode="json"),
        )
    )
    db_session.add(
        ConversationMessage(
            conversation=conversation,
            role="assistant",
            content="See above.",
            workflow_run_id=run.id,
            turn_plan=prior_plan.model_dump(mode="json"),
        )
    )
    await db_session.flush()

    async def latest_run(*_args, **_kwargs):
        return run.id

    async def fake_plan(**_kwargs):
        return PlannerTurnResult(
            decision=ModelPlannerDecision(action=PlannerAction.CONTINUE, confidence=0.8)
        )

    monkeypatch.setattr("src.harness.chat._latest_evidence_run_id", latest_run)
    monkeypatch.setattr("src.harness.chat.plan_harness_turn_detailed", fake_plan)
    decision = await plan_model_harness_chat_turn(
        query="show me the results then?",
        conversation=conversation,
        db=db_session,
        project_ids=["cdc"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.mode == "evidence_recomposition"
    assert decision.arguments["recompose_from_run_id"] == run.id
    assert decision.turn_plan is not None
    assert decision.turn_plan.task_type is TurnTaskType.RUNTIME_ANSWER
    assert decision.turn_plan.evidence_policy.allowed == ["runtime_evidence"]
    assert decision.turn_plan.objective == prior_plan.objective
    assert (
        await _recompose_persisted_evidence(db_session, run.id, prior_plan)
        == "Prior runtime result"
    )


async def test_runtime_ranking_executes_runtime_evidence_before_synthesis(
    db_session, monkeypatch
) -> None:
    _, conversation = await _seed_conversation(db_session)

    async def fake_plan(**_kwargs):
        return PlannerTurnResult(
            decision=ModelPlannerDecision(
                action=PlannerAction.EXECUTE,
                confidence=0.9,
                tool_calls=[
                    PlannedToolCall(
                        capability_id=OPERATIONAL_INVESTIGATION_CAPABILITY.id,
                        arguments={"question": "which jobs fail most", "project_id": "alitalend"},
                    )
                ],
            )
        )

    monkeypatch.setattr("src.harness.chat.plan_harness_turn_detailed", fake_plan)
    decision = await plan_model_harness_chat_turn(
        query="Which jobs fail the most according to runtime logs?",
        conversation=conversation,
        db=db_session,
        project_ids=["alitalend"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.action == "call_capability"
    assert decision.mode == "runtime_answer"
    assert decision.turn_plan is not None
    assert decision.turn_plan.allows("runtime_evidence")
    assert not decision.turn_plan.allows("indexed_documentation")
    assert decision.turn_plan.output_contract == "runtime_ranking"


async def test_model_clarification_state_is_typed_and_durable(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)
    response = await run_harness_chat_turn(
        conversation=conversation,
        query="Help me with the project",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a", "project-b"],
        planner_decision=HarnessPlannerDecision(
            action="ask_clarification",
            mode="model_clarification",
            confidence=0.7,
            direct_answer="Which project should I use?",
            missing_slots=("project_id",),
            decision_source="model",
        ),
    )

    assert response.turn_plan is not None
    assert response.turn_plan["task_type"] == "clarification"
    assert response.turn_plan["missing_slots"] == ["project_id"]
    messages = (
        await db_session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.id)
        )
    ).scalars().all()
    assert messages[0].turn_plan == messages[1].turn_plan == response.turn_plan


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
    assert response.workflow_run_id == runs[0].id
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
    assert messages[0].turn_plan["task_type"] == "document_generation"
    assert messages[0].turn_plan["execution_mode"] == "workflow"
    assert messages[1].turn_plan == messages[0].turn_plan


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
    assert response.workflow_run_id == run.id
    assert run.status == WorkflowRunStatus.INTERRUPTED
    assert run.runtime_provider_id == "langgraph.nexus"
    assert run.runtime_thread_id == f"harness-run-{run.id}"
    assert run.runtime_run_id is not None
    assert run.runtime_run_id.startswith("lg-run-")
    assert run.resume_token is None
    assert run.current_node == "await_project_scope"
    assert run.runtime_metadata["interrupt"]["type"] == "missing_project_scope"
    assert run.runtime_metadata["interrupt"]["missing_slots"] == ["project_id"]
    assert run.runtime_metadata["interrupt"]["available_projects"] == ["project-a", "project-b"]
    assert run.runtime_metadata["resume"]["ready"] is True
    events = await repo.list_activity_events(None, run.id)
    assert [event.event_type for event in events] == ["planner.decision", "clarification.requested"]
    detail = await get_conversation(conversation.id, db_session, user)
    assistant = detail.messages[-1]
    assert assistant.workflow_run_id == run.id
    assert [item.event_type for item in assistant.harness_activities] == [
        "planner.decision",
        "clarification.requested",
    ]
    assert assistant.pending_action is not None
    assert assistant.pending_action.workflow_run_id == run.id
    assert {choice.value for choice in assistant.pending_action.choices} == {
        "project-a",
        "project-b",
    }


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
    assert runs[0].status == WorkflowRunStatus.FAILED
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

    assert "Please approve the change" in response.content
    assert seen == {}

    repo = HarnessWorkflowRepository(db_session)
    runs = await repo.list_runs(None, project_id="project-a")
    assert runs[0].intent_mode == "update_docs_from_evidence"
    assert runs[0].status == WorkflowRunStatus.INTERRUPTED
    assert runs[0].runtime_metadata["proposal"]["expected_base_sha"].startswith("sha256:")
    pending = await repo.list_pending_interrupts_for_owner(runs[0].id, user.id)
    execution_arguments = pending[0].scope_snapshot["execution_arguments"]
    assert execution_arguments["expected_base_sha"].startswith("sha256:")
    assert execution_arguments["target_doc"] == "04_RUNBOOK.md"
    events = await repo.list_activity_events(None, runs[0].id)
    assert "evidence.parsed" in [event.event_type for event in events]
    assert "documentation_update.proposal_created" in [
        event.event_type for event in events
    ]


async def test_stale_document_approval_finishes_failed_without_activity_overflow(
    db_session, monkeypatch
) -> None:
    from src.harness.executor import CapabilityObservation
    from src.harness.models import DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY
    from src.harness.workflows.documentation_update import DocumentationUpdateResult

    user, conversation = await _seed_conversation(db_session)
    repo = HarnessWorkflowRepository(db_session)
    run = await repo.create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="project-a",
            intent_mode="update_docs_from_evidence",
            capability_id=DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id,
            provider_id=DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.provider_id,
            status=WorkflowRunStatus.PENDING,
            runtime_thread_id="doc-update-stale",
        )
    )
    provider_error = (
        "Documentation provider returned HTTP 409: Documentation changed since approval "
        + "sha256:" + "a" * 500
    )

    class StaleWorkflow:
        async def resume(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return DocumentationUpdateResult(
                status="failed",
                thread_id="doc-update-stale",
                current_node="finalize",
                activity=[
                    {
                        "event_type": "documentation_update.failed",
                        "label": provider_error,
                        "summary": provider_error,
                        "status": "failed",
                        "runtime_node": "publish",
                    }
                ],
                observation=CapabilityObservation(
                    capability_id=DOCUMENT_UPDATE_FROM_EVIDENCE_CAPABILITY.id,
                    status="failed",
                    summary=provider_error,
                    error_message=provider_error,
                ),
            )

    monkeypatch.setattr(
        "src.harness.chat.default_documentation_update_workflow",
        lambda: StaleWorkflow(),
    )
    updated, activities = await resume_documentation_update_workflow(
        run=run,
        approved=True,
        db=db_session,
        user_id=user.id,
        project_scope=["project-a"],
        user_permissions={"docs.generate"},
        access_token=None,
        arguments={
            "project_id": "project-a",
            "target_doc": "04_RUNBOOK.md",
            "evidence_refs": ["chat_prompt"],
            "evidence_text": "new evidence",
            "expected_base_sha": "sha256:" + "b" * 64,
        },
    )

    assert updated.status == WorkflowRunStatus.FAILED
    assert updated.finished_at is not None
    assert activities[-1].label == "Documentation update failed"
    assert len(activities[-1].summary or "") <= 2000


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
        query="Search Slack and investigate why this integration failed recently",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        user_groups=["engineering"],
        qdrant=object(),
        provider=_provider(),
    )

    assert "Likely cause:" in response.content
    assert "connection timeout" in response.content
    assert "Slack connector" in response.content
    assert "Unavailable evidence: Slack connector." in response.content
    assert "Sources:" not in response.content

    repo = HarnessWorkflowRepository(db_session)
    runs = await repo.list_runs(None, project_id="project-a")
    assert runs[0].intent_mode == "evidence_investigation"
    # Evidence collection completed. Missing Slack evidence makes the answer
    # partial, not the read-only workflow execution failed.
    assert runs[0].status == WorkflowRunStatus.SUCCEEDED
    assert response.structured_answer is not None
    assert response.structured_answer["execution_status"] == "completed"
    assert response.structured_answer["answer_status"] == "partial"
    structured_sections = json.dumps(response.structured_answer["sections"])
    assert "Sources:" not in structured_sections
    assert "Missing required sources:" not in structured_sections
    citation_labels = [
        citation["label"] for citation in response.structured_answer["citations"]
    ]
    assert len(citation_labels) == len(set(citation_labels))
    events = await repo.list_activity_events(None, runs[0].id)
    assert "investigation.completed" in {event.event_type for event in events}


async def test_conversation_48_generation_status_uses_receipt_and_reconciles_parent(
    db_session,
) -> None:
    user, conversation = await _seed_conversation(db_session)
    repo = HarnessWorkflowRepository(db_session)
    parent = await repo.create_run(
        WorkflowRunCreate(
            conversation_id=conversation.id,
            user_id=user.id,
            project_id="ml-sleep",
            intent_mode="generate_docs",
            capability_id=DOCUMENTATION_GENERATION_CAPABILITY.id,
            provider_id=DOCUMENTATION_GENERATION_CAPABILITY.provider_id,
            status=WorkflowRunStatus.TRIGGERED,
            input_summary="Generate the ml-sleep documentation",
        )
    )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "runs": [
                        {
                            "id": 41,
                            "project_id": "ml-sleep",
                            "status": "completed",
                            "doc_count": 10,
                            "quality_score": 3.2,
                            "run_verdict": "failed",
                            "gate_results": {
                                "client_ready": False,
                                "gates": {
                                    "evidence_quality": {"passed": False},
                                    "coverage": {"passed": True},
                                },
                            },
                        }
                    ],
                    "total": 1,
                },
            )
        ),
    )

    response = await run_harness_chat_turn(
        conversation=conversation,
        query="We previously triggered the ml-sleep documentation generation. Is it done?",
        db=db_session,
        user_id=user.id,
        project_ids=["ml-sleep"],
        provider=provider,
    )

    assert "finished and produced 10 documents" in response.content
    assert "needs review before it is client-ready" in response.content
    assert "Quality score: 3.2" in response.content
    assert "evidence quality" in response.content
    assert "mcp.docs" not in response.content
    assert "bytes" not in response.content

    reconciled = await repo.get_run(["ml-sleep"], parent.id)
    assert reconciled.status == WorkflowRunStatus.SUCCEEDED
    assert reconciled.runtime_metadata["external_generation"]["run_id"] == 41
    assert reconciled.runtime_metadata["external_generation"]["status"] == "completed"
    assert reconciled.runtime_metadata["external_generation"]["reconciled_at"]
    status_runs = await repo.list_runs(
        ["ml-sleep"], capability_id="mcp.docs.get_generation_status"
    )
    assert status_runs[0].runtime_metadata["pack_snapshot"]["active_pack_ids"] == [
        "nexus.data-integration"
    ]


async def test_model_selected_runtime_call_cannot_bypass_requested_time_window(
    db_session, monkeypatch
) -> None:
    user, conversation = await _seed_conversation(db_session)
    captured = {}
    touched: list[str] = []

    async def runtime(**kwargs):
        touched.append("runtime")
        captured.update(kwargs)
        return EvidenceQueryResult(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            status="succeeded",
        )

    async def docs(**_kwargs):
        touched.append("generated_docs")
        return EvidenceQueryResult(
            capability_id=DOCUMENT_LIST_CAPABILITY.id,
            status="succeeded",
        )

    async def search(**_kwargs):
        touched.append("indexed_docs")
        return EvidenceQueryResult(
            capability_id=DOCUMENT_SEARCH_CAPABILITY.id,
            status="succeeded",
        )

    monkeypatch.setattr("src.harness.chat.query_runtime_evidence", runtime)
    monkeypatch.setattr("src.harness.chat.list_generated_doc_evidence", docs)
    monkeypatch.setattr("src.harness.chat.search_document_evidence", search)
    await run_harness_chat_turn(
        conversation=conversation,
        query="Check failures in the past 24 hours for project-a",
        db=db_session,
        user_id=user.id,
        project_ids=["project-a"],
        qdrant=object(),
        planner_decision=HarnessPlannerDecision(
            action="call_capability",
            mode="evidence_investigation",
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            tool_calls=(
                {
                    "capability_id": RUNTIME_EVIDENCE_CAPABILITY.id,
                    "arguments": {"query": "check failures", "project_id": "project-a"},
                },
            ),
        ),
    )

    assert captured["since"] is not None
    assert captured["question"] == "check failures"
    assert captured["until"] is not None
    assert (captured["until"] - captured["since"]).total_seconds() == 86400
    assert touched == ["indexed_docs", "generated_docs", "runtime"]


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


async def test_runtime_continuation_resolves_slots_before_model_planning(db_session, monkeypatch) -> None:
    user, conversation = await _seed_conversation(db_session)
    plan = TurnPlan(
        objective="Show runtime results for ESB_PROD.",
        task_type=TurnTaskType.RUNTIME_ANSWER,
        resolved_scope={"engine": "ESB_PROD", "project_id": None},
        evidence_policy=EvidencePolicy(
            allowed=["runtime_evidence"], denied=["indexed_documentation"],
            preferred=["runtime_evidence"], required=["runtime_evidence"],
        ),
        execution_mode=TurnExecutionMode.EVIDENCE,
        output_contract="runtime_answer", confidence=1,
        missing_slots=["project_id", "time_window"], capability_ids=[RUNTIME_EVIDENCE_CAPABILITY.id],
    )
    db_session.add(HarnessConversationContinuationRow(
        conversation_id=conversation.id, owner_user_id=user.id, status="active", version=1,
        envelope={
            "continuation_id": "c108", "version": 1, "status": "active",
            "plan": plan.model_dump(mode="json"), "missing_slots": ["project_id", "time_window"],
            "slots": [
                {"name": "project_id", "value": None, "required": True, "options": []},
                {"name": "time_window", "value": None, "required": True, "options": []},
            ],
        },
    ))
    await db_session.flush()

    async def must_not_plan(**_kwargs):
        raise AssertionError("active continuation must resolve before model planning")

    monkeypatch.setattr("src.harness.chat.plan_harness_turn_detailed", must_not_plan)
    first = await plan_model_harness_chat_turn(
        query="cdc", conversation=conversation, db=db_session, project_ids=["cdc"],
        user_permissions=set(), access_token=None,
    )
    assert first.action == "ask_clarification"
    assert first.turn_plan.resolved_scope["engine"] == "ESB_PROD"
    assert first.turn_plan.resolved_scope["project_id"] == "cdc"
    assert first.turn_plan.evidence_policy.required == ["runtime_evidence"]
    await run_harness_chat_turn(
        conversation=conversation, query="cdc", db=db_session, user_id=user.id,
        project_ids=["cdc"], planner_decision=first,
    )

    second = await plan_model_harness_chat_turn(
        query="last 2 days", conversation=conversation, db=db_session, project_ids=["cdc"],
        user_permissions=set(), access_token=None,
    )
    assert second.action == "call_capability"
    assert second.mode == "runtime_answer"
    assert second.turn_plan.resolved_scope["engine"] == "ESB_PROD"
    assert second.turn_plan.resolved_scope["time_window"] is not None


@pytest.mark.parametrize("stream", [False, True])
async def test_runtime_continuation_executes_persisted_objective_without_docs(
    db_session, monkeypatch, stream: bool
) -> None:
    user, conversation = await _seed_conversation(db_session)
    objective = (
        "For ESB_PROD, show recent executions, health, events, failures, and performance."
    )
    plan = TurnPlan(
        objective=objective,
        task_type=TurnTaskType.RUNTIME_ANSWER,
        resolved_scope={"engine": "ESB_PROD", "project_id": "cdc", "project_ids": ["cdc"]},
        evidence_policy=EvidencePolicy(
            allowed=["runtime_evidence"],
            denied=["indexed_documentation", "generated_documentation", "connector_evidence"],
            preferred=["runtime_evidence"],
            required=["runtime_evidence"],
        ),
        execution_mode=TurnExecutionMode.EVIDENCE,
        output_contract="runtime_answer",
        confidence=1,
        missing_slots=["time_window"],
        capability_ids=[RUNTIME_EVIDENCE_CAPABILITY.id],
    )
    db_session.add(
        HarnessConversationContinuationRow(
            conversation_id=conversation.id,
            owner_user_id=user.id,
            status="active",
            version=1,
            envelope={
                "continuation_id": "runtime-slots",
                "version": 1,
                "status": "active",
                "plan": plan.model_dump(mode="json"),
                "slots": [
                    {"name": "project_id", "value": "cdc", "required": True, "options": []},
                    {"name": "time_window", "value": None, "required": True, "options": []},
                ],
                "missing_slots": ["time_window"],
            },
        )
    )
    await db_session.flush()
    captured: dict[str, object] = {}

    async def runtime(**kwargs):
        captured.update(kwargs)
        return EvidenceQueryResult(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            status="succeeded",
            items=[
                EvidenceItem(
                    id="runtime:1",
                    capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
                    source_type="runtime_execution",
                    title="Recent execution",
                    summary="ESB_PROD execution completed.",
                    observed_at="2026-07-13T12:00:00+00:00",
                )
            ],
        )

    async def docs_must_not_run(**_kwargs):
        raise AssertionError("runtime-only continuation must not query documentation")

    monkeypatch.setattr("src.harness.chat.query_runtime_evidence", runtime)
    monkeypatch.setattr("src.harness.chat.search_document_evidence", docs_must_not_run)
    monkeypatch.setattr("src.harness.chat.list_generated_doc_evidence", docs_must_not_run)
    decision = await plan_model_harness_chat_turn(
        query="last 2 days",
        conversation=conversation,
        db=db_session,
        project_ids=["cdc"],
        user_permissions=set(),
        access_token=None,
    )

    if stream:
        events = [
            event
            async for event in run_harness_chat_turn_stream(
                conversation=conversation,
                query="last 2 days",
                db=db_session,
                user_id=user.id,
                project_ids=["cdc"],
                provider=_provider(),
                planner_decision=decision,
            )
        ]
        assert any(event_type == "done" for event_type, _ in events)
    else:
        await run_harness_chat_turn(
            conversation=conversation,
            query="last 2 days",
            db=db_session,
            user_id=user.id,
            project_ids=["cdc"],
            provider=_provider(),
            planner_decision=decision,
        )

    assert captured["question"] == objective
    assert captured["project_id"] == "cdc"
    assert captured["since"] is not None
    assert captured["until"] is not None
    continuation = (
        await db_session.execute(
            select(HarnessConversationContinuationRow).where(
                HarnessConversationContinuationRow.conversation_id == conversation.id
            )
        )
    ).scalar_one()
    assert continuation.status == "completed"
    assert continuation.version == 3


async def test_explicit_docs_switch_supersedes_runtime_continuation(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)
    plan = TurnPlan(
        objective="runtime", task_type=TurnTaskType.RUNTIME_ANSWER,
        evidence_policy=EvidencePolicy(allowed=["runtime_evidence"], required=["runtime_evidence"]),
        execution_mode=TurnExecutionMode.EVIDENCE, output_contract="runtime_answer", confidence=1,
    )
    row = HarnessConversationContinuationRow(
        conversation_id=conversation.id, owner_user_id=user.id, status="active", version=1,
        envelope={"continuation_id": "switch", "version": 1, "status": "active", "plan": plan.model_dump(mode="json"), "slots": [], "missing_slots": []},
    )
    db_session.add(row)
    await db_session.flush()
    decision = await plan_model_harness_chat_turn(
        query="use docs instead", conversation=conversation, db=db_session, project_ids=["cdc"],
        user_permissions=set(), access_token=None,
    )
    await db_session.refresh(row)
    assert row.status == "superseded"
    assert decision.mode == "document_answer"


async def test_new_runtime_objective_supersedes_unrelated_continuation(db_session) -> None:
    user, conversation = await _seed_conversation(db_session)
    plan = TurnPlan(
        objective="Which runtime engines are available?",
        task_type=TurnTaskType.RUNTIME_ANSWER,
        evidence_policy=EvidencePolicy(allowed=["runtime_evidence"], required=["runtime_evidence"]),
        execution_mode=TurnExecutionMode.EVIDENCE,
        output_contract="runtime_answer",
        confidence=1,
    )
    row = HarnessConversationContinuationRow(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        status="active",
        version=1,
        envelope={
            "continuation_id": "old-runtime",
            "version": 1,
            "status": "active",
            "plan": plan.model_dump(mode="json"),
            "slots": [],
            "missing_slots": [],
        },
    )
    db_session.add(row)
    await db_session.flush()

    await plan_model_harness_chat_turn(
        query="For ESB_PROD, show recent executions, health, events, failures, and performance.",
        conversation=conversation,
        db=db_session,
        project_ids=None,
        user_permissions=set(),
        access_token=None,
        provider=_provider(),
    )

    await db_session.refresh(row)
    assert row.status == "superseded"
    assert row.version == 2
