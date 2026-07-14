from __future__ import annotations

from src.db.models import Conversation, HarnessConversationContinuationRow, User
from src.generation.agent_loop import RuntimeLoopOutcome
from src.harness.chat import (
    HarnessPlannerDecision,
    _runtime_scope_selection,
    _with_turn_plan,
    plan_model_harness_chat_turn,
)
from src.harness.evidence import query_runtime_evidence
from src.harness.executor import (
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    HarnessCapabilityExecutor,
)
from src.harness.models import RUNTIME_EVIDENCE_CAPABILITY
from src.harness.planner import (
    ContinuationEnvelope,
    ContinuationSlot,
    EvidencePolicy,
    RuntimeScope,
    RuntimeScopeKind,
    TurnExecutionMode,
    TurnPlan,
    TurnTaskType,
)
from src.harness.registry import CapabilityRegistry


def _runtime_decision(arguments: dict) -> HarnessPlannerDecision:
    return HarnessPlannerDecision(
        action="call_capability",
        mode="runtime_answer",
        capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
        arguments=arguments,
    )


def test_global_engine_inventory_uses_global_runtime_scope_without_clarification() -> None:
    decision = _with_turn_plan(
        _runtime_decision({"question": "List runtime engines"}),
        query="List runtime engines",
        project_ids=["project-a", "project-b"],
    )

    assert decision.turn_plan is not None
    assert decision.turn_plan.resolved_scope["project_id"] is None
    assert decision.turn_plan.resolved_scope["runtime_scope"] == {
        "kind": "global",
        "project_ids": [],
    }
    assert decision.turn_plan.missing_slots == []
    assert decision.turn_plan.evidence_policy.allowed == ["runtime_evidence"]
    assert "indexed_documentation" in decision.turn_plan.evidence_policy.denied


async def test_one_project_job_query_restricts_runtime_execution_to_selected_project(
    db_session, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    async def runtime_loop(_messages, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return RuntimeLoopOutcome(final_answer="No recent failures.", used_runtime_data=True)

    monkeypatch.setattr("src.harness.evidence.run_runtime_tool_loop", runtime_loop)
    decision = _with_turn_plan(
        _runtime_decision({"question": "Show failed jobs", "project_id": "project-a"}),
        query="Show failed jobs for project-a over the last 2 days",
        project_ids=["project-a", "project-b"],
    )

    assert decision.turn_plan is not None
    assert decision.turn_plan.resolved_scope["runtime_scope"]["kind"] == "single_project"
    assert decision.turn_plan.missing_slots == []
    result = await query_runtime_evidence(
        question="Show failed jobs",
        project_id="project-a",
        project_scope=["project-a", "project-b"],
        project_ids=["project-a"],
        db=db_session,
    )

    assert result.status == "succeeded"
    assert captured["project_scope"] == ["project-a"]


async def test_explicit_multi_project_query_restricts_runtime_execution_to_selection(
    db_session, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    async def runtime_loop(_messages, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return RuntimeLoopOutcome(final_answer="Two projects selected.", used_runtime_data=True)

    monkeypatch.setattr("src.harness.evidence.run_runtime_tool_loop", runtime_loop)
    decision = _with_turn_plan(
        _runtime_decision(
            {"question": "Compare failed jobs", "project_ids": ["project-a", "project-b"]}
        ),
        query="Compare failed jobs for project-a and project-b over the last 2 days",
        project_ids=["project-a", "project-b", "project-c"],
    )

    assert decision.turn_plan is not None
    assert decision.turn_plan.resolved_scope["project_id"] is None
    assert decision.turn_plan.resolved_scope["runtime_scope"] == {
        "kind": "multi_project",
        "project_ids": ["project-a", "project-b"],
    }
    await query_runtime_evidence(
        question="Compare failed jobs",
        project_id=None,
        project_scope=["project-a", "project-b", "project-c"],
        project_ids=["project-a", "project-b"],
        db=db_session,
    )

    assert captured["project_scope"] == ["project-a", "project-b"]


async def test_unauthorized_runtime_project_is_denied_without_scope_widening() -> None:
    scope, unauthorized = _runtime_scope_selection(
        query="Show failed jobs for project-b",
        arguments={"project_id": "project-b"},
        allowed_project_ids=["project-a"],
    )
    executor = HarnessCapabilityExecutor(CapabilityRegistry([RUNTIME_EVIDENCE_CAPABILITY]))
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=RUNTIME_EVIDENCE_CAPABILITY.id,
            arguments={"question": "Show failed jobs", "project_ids": ["project-b"]},
        ),
        CapabilityExecutionContext(user_id=1, project_scope=["project-a"]),
    )

    assert scope is None
    assert unauthorized == ("project-b",)
    assert observation.status == "denied"
    assert observation.error_message == "Project access required"


async def test_runtime_scope_follow_up_preserves_typed_selection(db_session) -> None:
    user = User(username="runtime-scope", email="runtime-scope@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Runtime scope")
    db_session.add(conversation)
    await db_session.flush()
    scope = RuntimeScope(
        kind=RuntimeScopeKind.SINGLE_PROJECT,
        project_ids=("project-a",),
    )
    plan = TurnPlan(
        objective="Show failed jobs for project-a.",
        task_type=TurnTaskType.RUNTIME_ANSWER,
        resolved_scope={
            "project_id": "project-a",
            "project_ids": ["project-a"],
            "runtime_scope": scope.model_dump(mode="json"),
            "time_window": None,
        },
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
    envelope = ContinuationEnvelope(
        continuation_id="runtime-scope-follow-up",
        version=1,
        status="active",
        plan=plan,
        slots=[ContinuationSlot(name="time_window", value=None)],
        missing_slots=["time_window"],
    )
    db_session.add(
        HarnessConversationContinuationRow(
            conversation_id=conversation.id,
            owner_user_id=user.id,
            status="active",
            version=1,
            envelope=envelope.model_dump(mode="json"),
        )
    )
    await db_session.flush()

    decision = await plan_model_harness_chat_turn(
        query="last 2 days",
        conversation=conversation,
        db=db_session,
        project_ids=["project-a"],
        user_permissions=set(),
        access_token=None,
    )

    assert decision.mode == "runtime_answer"
    assert decision.arguments == {
        "question": "Show failed jobs for project-a.",
        "project_id": "project-a",
    }
    assert decision.turn_plan is not None
    assert decision.turn_plan.resolved_scope["runtime_scope"] == scope.model_dump(mode="json")
    assert decision.turn_plan.evidence_policy.allowed == ["runtime_evidence"]
    assert decision.turn_plan.missing_slots == []
