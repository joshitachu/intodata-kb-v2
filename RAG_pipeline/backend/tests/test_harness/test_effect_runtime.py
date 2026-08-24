from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import func, select

from src.db.models import Conversation, ConversationMessage, HarnessApprovalProposalRow, User
from src.db.models.harness_pack import HarnessPackDraftRow
from src.harness.approvals import ApprovalService
from src.harness.capability_runtime import CapabilityExecutionContext
from src.harness.capability_runtime import CapabilityObservation
from src.harness.conversation_state import ConversationStateRepository
from src.harness.effect_operations import EffectOperationService
from src.harness.effect_runtime import build_effect_primary_runtime
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.agent_loop import AgentStopReason
from src.harness.readonly_runtime import run_readonly_primary_turn
from src.harness.models import (
    CapabilityDefinition,
    CapabilityHealth,
    HarnessProviderType,
    HarnessRiskTier,
)
from src.harness.packs.draft_service import PackDraftService
from src.harness.packs.builder_workflow import PackBuilderWorkflow, PackGenerationResult
from src.harness.packs.draft_artifact import PackDraftArtifact
from src.harness.registry import CapabilityRegistry, base_capability_registry


class ScriptedEffectModel(FakeMessagesListChatModel):
    bound_names: list[str] = []

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del kwargs
        assert tool_choice in {"auto", "none"}
        self.bound_names = [tool.name for tool in tools]
        assert "nexus_turn_assess" not in self.bound_names
        return self


_PACK_MANIFEST = """\
name: talend-migration
version: 0.1.0
description: Analyze and guide a Talend-to-Azure migration.
docs:
  - name: TALEND_MIGRATION_GUIDE
"""


def _complete_pack_arguments(name: str = "Talend Migration") -> dict[str, Any]:
    return {
        "name": name,
        "brief": (
            "Analyze and guide a Talend-to-Azure migration for platform engineers, "
            "covering job dependencies and migration risks."
        ),
    }


async def _fake_save(self, *, draft_id, owner_user_id, expected_revision, pack_id=None, access_token=None):  # noqa: ANN001, ANN202
    """Stand in for the MCP round-trip while keeping the draft transition real."""
    from src.harness.packs.draft_service import SavedPackRecord

    row = await self._owned_active_draft(draft_id, owner_user_id)
    validation = self._validation(dict(row.artifact or {}))
    assert validation["valid"] is True, validation["errors"]
    artifact = PackDraftArtifact.model_validate(row.artifact)
    row.status = "saved"
    row.revision += 1
    await self._db.flush()
    return SavedPackRecord(
        mcp_pack_id=7,
        name=row.name,
        version="1.0.0",
        documents=artifact.doc_names,
        content_hash="a" * 64,
    )


async def _runtime(
    db_session,
    responses: list[AIMessage],
):
    user = User(username="effect-runtime", email="effect-runtime@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Effect runtime")
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(conversation_id=conversation.id, role="user", content="Save it")
    db_session.add(message)
    await db_session.flush()
    states = ConversationStateRepository(db_session)
    state = await states.get_or_create(conversation_id=conversation.id, owner_user_id=user.id)
    claim = await states.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key=f"effect-runtime:{message.id}",
    )
    registry = base_capability_registry()
    context = CapabilityExecutionContext(
        user_id=user.id,
        conversation_id=conversation.id,
        project_scope=[],
        permissions=None,
        current_message_id=message.id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=claim.id,
        conversation_turn_claim_token=claim.lease_token,
    )
    runtime = build_effect_primary_runtime(
        model=ScriptedEffectModel(responses=responses),
        registry=registry,
        executor=HarnessCapabilityExecutor(
            registry,
            pack_draft_service=PackDraftService(db_session, registry),
            conversation_state_repository=states,
        ),
        context=context,
        approval_service=ApprovalService(db_session),
        effect_operation_service=EffectOperationService(db_session),
    )
    return runtime


async def test_complete_pack_request_executes_one_governed_creation(
    db_session, monkeypatch
) -> None:
    complete = _complete_pack_arguments()
    generated = PackDraftArtifact(manifest_yaml=_PACK_MANIFEST)
    briefs: list[str] = []

    async def generate(self, *, query, **kwargs):  # noqa: ANN001, ANN202
        del self, kwargs
        briefs.append(query)
        return PackGenerationResult(artifact=generated, name="talend-migration")

    monkeypatch.setattr(PackBuilderWorkflow, "generate", generate)
    monkeypatch.setattr(
        PackDraftService, "save", _fake_save, raising=True
    )
    runtime = await _runtime(
        db_session,
        [
            AIMessage(
                content="",
                tool_calls=[_call("create_pack", complete, "create-talend-pack")],
            ),
            AIMessage(
                content=(
                    "Created the Talend Migration private Pack.\n"
                    "```nexus-grounding\n"
                    '{"answer_status":"grounded","citations":[],"disclosures":[]}'
                    "\n```"
                )
            ),
        ],
    )

    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Create the Talend Migration Pack.",
        conversation_seed=[],
        state_version="complete-pack-effect",
    )

    drafts = list(
        (
            await db_session.scalars(
                select(HarnessPackDraftRow).where(HarnessPackDraftRow.name == "Talend Migration")
            )
        ).all()
    )
    assert len(drafts) == 1
    assert drafts[0].status == "saved"
    assert drafts[0].artifact
    assert drafts[0].validation["valid"] is True
    # The brief reaches the author verbatim; no structured artifact is invented.
    assert briefs == [complete["brief"]]
    assert turn.correction_rounds == 0
    assert turn.used_fallback is False
    assert turn.consequential_executions == 1
    assert "Created the Talend Migration private Pack" in turn.result.output
    assert sum(event.semantic_name == "create_pack" for event in runtime.ledger.events) > 0
    assert not any(event.semantic_name == "nexus_turn_assess" for event in runtime.ledger.events)


async def test_incomplete_pack_request_asks_before_persisting_any_pack(db_session) -> None:
    question = (
        "I have the name Incident Review. What should this Pack analyze, "
        "who is it for, and what document should it produce?"
    )
    runtime = await _runtime(
        db_session,
        [
            AIMessage(
                content=(
                    f"{question}\n"
                    "```nexus-grounding\n"
                    '{"answer_status":"no_evidence","citations":[],"disclosures":[]}'
                    "\n```"
                )
            )
        ],
    )

    result = await runtime.graph.ainvoke(
        {"messages": [HumanMessage(content="Create a Pack named Incident Review.")]}
    )

    assert question in str(result["messages"][-1].content)
    assert not any(isinstance(message, ToolMessage) for message in result["messages"])
    assert not any(event.semantic_name == "nexus_turn_assess" for event in runtime.ledger.events)
    assert await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) == 0


async def test_unspecified_pack_request_asks_for_specific_information_without_a_tool(
    db_session,
) -> None:
    question = "What should the Pack be called, and what should it help its audience decide?"
    runtime = await _runtime(
        db_session,
        [
            AIMessage(
                content=(
                    f"{question}\n"
                    "```nexus-grounding\n"
                    '{"answer_status":"no_evidence","citations":[],"disclosures":[]}'
                    "\n```"
                )
            )
        ],
    )

    result = await runtime.graph.ainvoke(
        {"messages": [HumanMessage(content="Create a pack for me.")]}
    )

    assert question in str(result["messages"][-1].content)
    assert not any(isinstance(message, ToolMessage) for message in result["messages"])
    assert runtime.middleware.prepared_action_candidate is None
    assert not any(event.semantic_name == "nexus_turn_assess" for event in runtime.ledger.events)
    assert await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) == 0


def _call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


async def test_approval_required_tool_persists_before_interrupt_observation(db_session) -> None:
    runtime = await _runtime(
        db_session,
        [
            AIMessage(
                content="",
                tool_calls=[_call("save_pack", {"draft_id": 3, "expected_revision": 2}, "save-1")],
            ),
            AIMessage(
                content='Waiting for approval.\n```nexus-grounding\n{"answer_status":"grounded","citations":[],"disclosures":[]}\n```'
            ),
        ],
    )
    result = await runtime.graph.ainvoke(
        {"messages": [HumanMessage(content="Save the current Pack.")]}
    )
    tool = next(
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == "save_pack"
    )
    payload = json.loads(str(tool.content))
    assert payload["status"] == "approval_required"
    assert payload["data"]["proposal_id"].startswith("apr-")
    assert await db_session.scalar(select(func.count(HarnessApprovalProposalRow.id))) == 1


async def test_multiple_consequential_calls_create_zero_proposals(db_session) -> None:
    runtime = await _runtime(
        db_session,
        [
            AIMessage(
                content="",
                tool_calls=[
                    _call("save_pack", {"draft_id": 3, "expected_revision": 2}, "save-2"),
                    _call(
                        "abandon_pack_draft", {"draft_id": 3, "expected_revision": 2}, "abandon-2"
                    ),
                ],
            ),
            AIMessage(
                content='I must handle one action at a time.\n```nexus-grounding\n{"answer_status":"grounded","citations":[],"disclosures":[]}\n```'
            ),
        ],
    )
    await runtime.graph.ainvoke(
        {"messages": [HumanMessage(content="Save and abandon it.")]}
    )
    rejected = [
        event
        for event in runtime.ledger.events
        if event.kind == "middleware.proposed"
        and event.policy_class == "call_set_rejected"
    ]
    assert {event.semantic_name for event in rejected} == {
        "save_pack",
        "abandon_pack_draft",
    }
    assert await db_session.scalar(select(func.count(HarnessApprovalProposalRow.id))) == 0


async def test_invalid_effect_arguments_end_as_one_clarification_without_retry(
    db_session,
) -> None:
    runtime = await _runtime(
        db_session,
        [
            AIMessage(
                content="",
                tool_calls=[_call("save_pack", {"draft_id": 3}, "invalid-save")],
            ),
            AIMessage(
                content=(
                    "Which draft revision should I save?\n"
                    "```nexus-grounding\n"
                    '{"answer_status":"no_evidence","citations":[],"disclosures":[]}'
                    "\n```"
                )
            ),
        ],
    )

    turn = await run_readonly_primary_turn(
        graph=runtime.graph,
        ledger=runtime.ledger,
        snapshot=runtime.snapshot,
        user_message="Save the Pack.",
        conversation_seed=[],
        state_version="needs-input-effect",
        max_corrections=0,
    )

    assert turn.result.stop_reason is AgentStopReason.CLARIFICATION_REQUIRED
    assert [item.status.value for item in turn.result.observations] == ["needs_input"]
    assert "Which draft revision" in turn.result.output
    assert await db_session.scalar(select(func.count(HarnessApprovalProposalRow.id))) == 0


async def test_reversible_effect_replays_stable_operation_without_second_handler_call(
    db_session,
) -> None:
    calls = 0
    capability = CapabilityDefinition(
        id="nexus.test.reversible",
        label="Change sandbox value",
        semantic_name="change_sandbox_value",
        purpose="Change one reversible sandbox value.",
        preconditions=["A live turn claim exists."],
        failure_recovery={
            "validation_failed": "Correct the value.",
            "denied": "Respect the boundary.",
            "failed": "Inspect the safe failure.",
        },
        provider_type=HarnessProviderType.WORKFLOW,
        provider_id="nexus.test",
        action="change",
        input_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
        output_type="sandbox_change",
        risk_tier=HarnessRiskTier.REVERSIBLE_WRITE,
        enabled=True,
        health=CapabilityHealth.AVAILABLE,
        packs=["core"],
        task_types=[],
        use_when=["The user explicitly asks to change the sandbox value."],
        do_not_use_when=["The user only discusses possible values."],
        output_contracts=["sandbox_change"],
        metadata={"bounded_loop_only": True, "requires_turn_claim": True},
    )

    class Handler:
        capability_id = capability.id
        input_schema = capability.input_schema
        output_type = capability.output_type
        task_types = ()

        async def __call__(self, definition, request, context, services):  # noqa: ANN001, ANN201
            nonlocal calls
            del definition, context, services
            calls += 1
            return CapabilityObservation(
                capability_id=capability.id,
                status="succeeded",
                summary=f"Changed to {request.arguments['value']}.",
                receipt_ref={"idempotency_key": request.idempotency_key},
            )

    user = User(username="replay-owner", email="replay-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Replay")
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(conversation_id=conversation.id, role="user", content="Change it")
    db_session.add(message)
    await db_session.flush()
    states = ConversationStateRepository(db_session)
    state = await states.get_or_create(conversation_id=conversation.id, owner_user_id=user.id)
    claim = await states.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="effect-replay",
    )
    context = CapabilityExecutionContext(
        user_id=user.id,
        conversation_id=conversation.id,
        current_message_id=message.id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=claim.id,
        conversation_turn_claim_token=claim.lease_token,
    )
    registry = CapabilityRegistry([capability], handlers=[Handler()])
    operations = EffectOperationService(db_session)

    async def invoke_once():
        runtime = build_effect_primary_runtime(
            model=ScriptedEffectModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[_call("change_sandbox_value", {"value": "green"}, "same-call")],
                    ),
                    AIMessage(
                        content='Done.\n```nexus-grounding\n{"answer_status":"grounded","citations":[],"disclosures":[]}\n```'
                    ),
                ]
            ),
            registry=registry,
            executor=HarnessCapabilityExecutor(registry),
            context=context,
            approval_service=ApprovalService(db_session),
            effect_operation_service=operations,
        )
        return await runtime.graph.ainvoke(
            {"messages": [HumanMessage(content="Change it to green.")]}
        )

    await invoke_once()
    await invoke_once()
    assert calls == 1
