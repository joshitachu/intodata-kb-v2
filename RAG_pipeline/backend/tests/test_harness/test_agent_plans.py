from __future__ import annotations

import datetime

import pytest

from src.db.models import Conversation, ConversationMessage, HarnessWorkflowRunRow, User
from src.harness.agent_plans import AgentPlanConflict, AgentPlanInput, AgentPlanService, AgentPlanStepInput, topology_requires_plan
from src.harness.conversation_state import ConversationStateRepository
from src.harness.registry import base_capability_registry


async def _claim(db_session):
    user = User(username="plan-owner", email="plan-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Plan")
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(conversation_id=conversation.id, role="user", content="Plan this")
    db_session.add(message)
    await db_session.flush()
    states = ConversationStateRepository(db_session)
    state = await states.get_or_create(conversation_id=conversation.id, owner_user_id=user.id)
    claim = await states.claim_turn(conversation_id=conversation.id, owner_user_id=user.id, message_id=message.id, expected_version=state.version, idempotency_key="plan-test")
    return user, conversation, message, claim


def _payload() -> AgentPlanInput:
    return AgentPlanInput(goal="Onboard and document", scoped_entities=["fixture-project"], steps=[AgentPlanStepInput(key="attach", objective="Attach source", kind="effect", capability_id="mcp.projects.attach_git_source"), AgentPlanStepInput(key="docs", objective="Generate docs", kind="workflow", depends_on=["attach"])])


def test_topology_policy_never_receives_words_and_requires_boundaries() -> None:
    assert not topology_requires_plan([AgentPlanStepInput(key="read-a", objective="x", kind="read"), AgentPlanStepInput(key="read-b", objective="y", kind="read")])
    assert not topology_requires_plan(
        [
            AgentPlanStepInput(key="list", objective="List documents", kind="read"),
            AgentPlanStepInput(
                key="read",
                objective="Read the selected documents",
                kind="read",
                depends_on=["list"],
            ),
        ]
    )
    assert topology_requires_plan(_payload().steps)


async def test_create_pause_and_stale_version_are_owner_scoped(db_session) -> None:
    user, conversation, message, claim = await _claim(db_session)
    service = AgentPlanService(db_session, base_capability_registry())
    created = await service.create(conversation_id=conversation.id, owner_user_id=user.id, message_id=message.id, claim=claim, payload=_payload(), project_scope=["fixture-project"])
    assert created.status == "active" and created.version == 1
    assert [step.status for step in created.steps] == ["pending", "pending"]
    paused = await service.transition(conversation_id=conversation.id, owner_user_id=user.id, message_id=message.id, claim=claim, plan_id=created.plan_id, expected_version=1, action="pause")
    assert paused.status == "paused" and paused.version == 2
    with pytest.raises(AgentPlanConflict):
        await service.transition(conversation_id=conversation.id, owner_user_id=user.id, message_id=message.id, claim=claim, plan_id=created.plan_id, expected_version=1, action="resume")


async def test_executable_steps_advance_only_from_ready_authoritative_outcomes(db_session) -> None:
    user, conversation, message, claim = await _claim(db_session)
    service = AgentPlanService(db_session, base_capability_registry())
    payload = AgentPlanInput(
        goal="Attach then generate",
        scoped_entities=["fixture-project"],
        steps=[
            AgentPlanStepInput(
                key="attach",
                objective="Attach source",
                kind="effect",
                capability_id="mcp.projects.attach_git_source",
            ),
            AgentPlanStepInput(
                key="generate",
                objective="Generate documentation",
                kind="workflow",
                capability_id="mcp.docs.generate_project_documentation",
                depends_on=["attach"],
            ),
        ],
    )
    created = await service.create(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        claim=claim,
        payload=payload,
        project_scope=["fixture-project"],
    )
    blocked = await service.record_capability_outcome(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        capability_id="mcp.docs.generate_project_documentation",
        operation_id="eff-too-early",
        status="triggered",
        workflow_ref={"workflow_run_id": 9},
    )
    assert blocked and [step.status for step in blocked.steps] == ["pending", "pending"]

    attached = await service.record_capability_outcome(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        capability_id="mcp.projects.attach_git_source",
        operation_id="eff-attach",
        status="succeeded",
        receipt_ref={"idempotency_key": "eff-attach"},
    )
    assert attached and attached.steps[0].status == "completed"
    assert attached.steps[0].capability_receipt_ref == {"idempotency_key": "eff-attach"}

    workflow = HarnessWorkflowRunRow(
        conversation_id=conversation.id,
        user_id=user.id,
        project_id="fixture-project",
        intent_mode="generate_docs",
        capability_id="mcp.docs.generate_project_documentation",
        provider_id="mcp.docs",
        status="running",
        version=1,
        started_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(workflow)
    await db_session.flush()

    waiting = await service.record_capability_outcome(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        capability_id="mcp.docs.generate_project_documentation",
        operation_id="eff-generate",
        status="triggered",
        workflow_ref={"workflow_run_id": workflow.id},
    )
    assert waiting and waiting.steps[1].status == "waiting"
    assert waiting.steps[1].workflow_run_id == workflow.id
    assert waiting.version == created.version + 2

    workflow.status = "succeeded"
    workflow.finished_at = datetime.datetime.now(datetime.UTC)
    await db_session.flush()
    reconciled = await service.reconcile_workflow_outcomes(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
    )
    assert reconciled and reconciled.steps[1].status == "completed"
    assert reconciled.last_change_reason == "step_terminal_from_workflow_ledger"


async def test_approved_effect_replaces_proposal_link_without_self_completing(db_session) -> None:
    user, conversation, message, claim = await _claim(db_session)
    service = AgentPlanService(db_session, base_capability_registry())
    payload = AgentPlanInput(
        goal="Save then generate",
        scoped_entities=["fixture-project"],
        steps=[
            AgentPlanStepInput(
                key="save",
                objective="Save Pack",
                kind="approval",
                capability_id="nexus.packs.drafts.save",
            ),
            AgentPlanStepInput(key="wait", objective="Wait for next work", kind="wait", depends_on=["save"]),
        ],
    )
    await service.create(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        claim=claim,
        payload=payload,
        project_scope=["fixture-project"],
    )
    proposed = await service.record_capability_outcome(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        capability_id="nexus.packs.drafts.save",
        operation_id="approval:apr-one",
        status="approval_required",
    )
    assert proposed and proposed.steps[0].status == "awaiting_approval"
    completed = await service.record_capability_outcome(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        capability_id="nexus.packs.drafts.save",
        operation_id="eff-approved",
        status="succeeded",
        receipt_ref={"idempotency_key": "eff-approved"},
    )
    assert completed and completed.steps[0].status == "completed"
    assert completed.steps[0].effect_operation_id == "eff-approved"
