from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityObservation
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.models import (
    DOCUMENT_LIST_CAPABILITY,
    DOCUMENT_READ_CAPABILITY,
    DOCUMENT_SEARCH_CAPABILITY,
    GENERATED_DOCUMENT_READ_CAPABILITY,
)
from src.harness.planning_runtime import PLAN_PRIMARY_SYSTEM_PROMPT, build_planning_primary_runtime
from src.harness.registry import base_capability_registry
from src.harness.semantic_tools import project_capability_observation
from src.harness.semantic_tools import GatewayMode


def test_planning_runtime_exposes_required_objective_projection() -> None:
    registry = base_capability_registry()
    runtime = build_planning_primary_runtime(
        model=FakeMessagesListChatModel(responses=[]),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(
            user_id=1, conversation_id=1, project_scope=[], permissions=set(),
            current_message_id=1, conversation_state_version=1,
            conversation_turn_claim_id=1, conversation_turn_claim_token="test",
        ),
    )
    assert "project_objective" in runtime.snapshot.semantic_to_canonical
    assert "answer greetings" not in PLAN_PRIMARY_SYSTEM_PROMPT.lower()
    assert runtime.gateway._mode is GatewayMode.PLAN_CONTROL_PRIMARY
    assert runtime.middleware._mode is GatewayMode.PLAN_CONTROL_PRIMARY


def test_objective_projection_is_not_classified_as_evidence_lookup() -> None:
    registry = base_capability_registry()
    runtime = build_planning_primary_runtime(
        model=FakeMessagesListChatModel(responses=[]),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(
            user_id=1,
            conversation_id=1,
            project_scope=[],
            permissions=set(),
            current_message_id=1,
            conversation_state_version=1,
            conversation_turn_claim_id=1,
            conversation_turn_claim_token="test",
        ),
    )
    record = runtime.snapshot.tool_for_name("project_objective")
    assert record is not None
    envelope = project_capability_observation(
        CapabilityObservation(
            capability_id=record.canonical_id,
            status="succeeded",
            output_type=record.output_type,
            summary="Objective topology projected without persistence.",
            data={"plan_required": False},
        ),
        capability=record,
    )
    assert record.evidence_state_relevant is False
    assert envelope.evidence_state_relevant is False


def test_planning_runtime_honors_chat_rag_only_document_policy() -> None:
    registry = base_capability_registry()
    runtime = build_planning_primary_runtime(
        model=FakeMessagesListChatModel(responses=[]),
        registry=registry,
        executor=HarnessCapabilityExecutor(registry),
        context=CapabilityExecutionContext(
            user_id=1,
            conversation_id=1,
            project_scope=["fixture-project"],
            permissions=set(),
            denied_capability_ids={
                DOCUMENT_LIST_CAPABILITY.id,
                DOCUMENT_READ_CAPABILITY.id,
                GENERATED_DOCUMENT_READ_CAPABILITY.id,
            },
        ),
    )

    visible = {item.canonical_id for item in runtime.snapshot.tools}
    assert DOCUMENT_SEARCH_CAPABILITY.id in visible
    assert DOCUMENT_LIST_CAPABILITY.id not in visible
    assert DOCUMENT_READ_CAPABILITY.id not in visible
    assert GENERATED_DOCUMENT_READ_CAPABILITY.id not in visible
