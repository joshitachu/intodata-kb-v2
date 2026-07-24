from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select

import src.harness.capability_handlers as capability_handlers
from src.db.models import Conversation, ConversationMessage, User
from src.db.models.harness_pack import (
    HarnessPackDraftRow,
    HarnessPrivateAnalysisPackRow,
    HarnessPrivateAnalysisPackVersionRow,
)
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityExecutionRequest
from src.harness.conversation_state import ConversationStateRepository
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.models import PACK_CREATE_CAPABILITY
from src.harness.packs.builder_workflow import PackBuilderWorkflow, PackGenerationResult
from src.harness.packs.draft_service import PackDraftService
from src.harness.packs.models import PackArtifactV3
from src.harness.registry import base_capability_registry


def _creation_arguments(name: str = "Talend Migration Diagnostic") -> dict[str, Any]:
    return {
        "name": name,
        "purpose": "Plan a Talend-to-Azure migration.",
        "audience": {
            "label": "Engineering and SRE team leads",
            "roles": ["engineering leads", "SRE leads"],
            "decision_needs": ["migration sequencing and risk decisions"],
        },
        "focus_components": ["tRunJob"],
        "output_documents": [{"title": "Talend-to-Azure Migration Plan"}],
    }


def _rich_artifact() -> PackArtifactV3:
    return PackArtifactV3.model_validate_json(
        json.dumps(
            {
                "schema_version": 3,
                "purpose": "Plan a safe, evidence-backed Talend-to-Azure migration.",
                "analyses": [
                    {
                        "id": "trunjob-inventory",
                        "label": "tRunJob inventory",
                        "purpose": "Inventory every parent-child job invocation and execution mode.",
                    },
                    {
                        "id": "dependency-mapping",
                        "label": "Dependency mapping",
                        "purpose": "Map parameters, contexts, schedules, and failure propagation.",
                    },
                    {
                        "id": "azure-target",
                        "label": "Azure target design",
                        "purpose": "Select Azure orchestration patterns for each invocation path.",
                    },
                ],
                "evidence_requirements": [
                    {
                        "id": "talend-exports",
                        "label": "Talend job exports",
                        "required": True,
                        "weight": 45,
                    },
                    {
                        "id": "execution-logs",
                        "label": "Execution logs and schedules",
                        "required": True,
                        "weight": 30,
                    },
                    {
                        "id": "context-values",
                        "label": "Context and parameter definitions",
                        "required": True,
                        "weight": 25,
                    },
                ],
                "output_documents": [
                    {
                        "id": "migration-plan",
                        "title": "Talend-to-Azure Migration Plan",
                        "sections": [
                            {"id": "summary", "heading": "Executive summary", "order": 1},
                            {"id": "inventory", "heading": "tRunJob inventory", "order": 2},
                            {"id": "dependencies", "heading": "Dependency map", "order": 3},
                            {"id": "target", "heading": "Azure target architecture", "order": 4},
                            {"id": "waves", "heading": "Migration waves", "order": 5},
                            {"id": "validation", "heading": "Validation and rollback", "order": 6},
                        ],
                    }
                ],
                "audience": {
                    "label": "Engineering and SRE team leads",
                    "roles": ["engineering leads", "SRE leads"],
                    "decision_needs": ["migration sequencing", "risk acceptance"],
                    "desired_outcome": "Approve a safe migration sequence and Azure target patterns.",
                    "render": {
                        "seniority": "manager",
                        "domain_familiarity": "working",
                        "decision_horizon": "near_term",
                        "detail_level": "detailed",
                    },
                },
            }
        )
    )


async def _executor_context(db_session, *, suffix: str):
    owner = User(username=f"pack-create-{suffix}", email=f"pack-create-{suffix}@example.com")
    db_session.add(owner)
    await db_session.flush()
    conversation = Conversation(user_id=owner.id, title="Pack creation")
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Create the Talend migration Pack.",
    )
    db_session.add(message)
    await db_session.flush()
    states = ConversationStateRepository(db_session)
    state = await states.get_or_create(conversation_id=conversation.id, owner_user_id=owner.id)
    claim = await states.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key=f"pack-create:{message.id}",
    )
    registry = base_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry,
        pack_draft_service=PackDraftService(db_session, registry),
        conversation_state_repository=states,
    )
    context = CapabilityExecutionContext(
        user_id=owner.id,
        conversation_id=conversation.id,
        permissions={"manage_packs"},
        current_message_id=message.id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=claim.id,
        conversation_turn_claim_token=claim.lease_token,
    )
    return executor, context, states


async def _counts(db_session) -> tuple[int, int, int]:
    return (
        int(await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) or 0),
        int(await db_session.scalar(select(func.count(HarnessPrivateAnalysisPackRow.id))) or 0),
        int(
            await db_session.scalar(select(func.count(HarnessPrivateAnalysisPackVersionRow.id)))
            or 0
        ),
    )


async def test_workbench_and_chat_share_generator_and_chat_saves_builder_artifact(
    db_session, monkeypatch
) -> None:
    generated = _rich_artifact()
    calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs):  # noqa: ANN001, ANN202
        del self
        calls.append(kwargs)
        return PackGenerationResult(artifact=generated, name="Builder-proposed name")

    def forbidden_translator(_: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("chat creation must not use the legacy friendly translator")

    monkeypatch.setattr(PackBuilderWorkflow, "generate", generate)
    monkeypatch.setattr(
        capability_handlers, "friendly_pack_configuration_patch", forbidden_translator
    )

    workbench_owner = User(
        username="pack-workbench-shared", email="pack-workbench-shared@example.com"
    )
    db_session.add(workbench_owner)
    await db_session.flush()
    registry = base_capability_registry()
    draft_service = PackDraftService(db_session, registry)
    workbench_draft = await draft_service.create_draft(owner_user_id=workbench_owner.id)
    drafted, _ = await PackBuilderWorkflow().run_for_draft(
        service=draft_service,
        draft_id=workbench_draft.id,
        owner_user_id=workbench_owner.id,
        query="Build this Pack in the Workbench.",
    )

    executor, context, _ = await _executor_context(db_session, suffix="shared")
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-rich-pack",
        ),
        context,
    )

    version = await db_session.get(
        HarnessPrivateAnalysisPackVersionRow,
        observation.data["private_pack_version_id"],
    )
    assert observation.status == "succeeded"
    assert drafted.artifact == generated.model_dump(mode="json")
    assert version is not None
    assert version.artifact == generated.model_dump(mode="json")
    identity = await db_session.get(HarnessPrivateAnalysisPackRow, version.private_pack_id)
    assert identity is not None
    assert identity.name == _creation_arguments()["name"]
    assert len(calls) == 2
    assert calls[0]["current_artifact"] == {}
    assert calls[1].get("current_artifact") is None
    assert "tRunJob" in calls[1]["query"]
    assert "Talend-to-Azure Migration Plan" in calls[1]["query"]


async def test_chat_builder_question_persists_no_pack(db_session, monkeypatch) -> None:
    async def question(self, **kwargs):  # noqa: ANN001, ANN202
        del self, kwargs
        return PackGenerationResult(question="Which migration decision should this Pack support?")

    monkeypatch.setattr(PackBuilderWorkflow, "generate", question)
    executor, context, _ = await _executor_context(db_session, suffix="question")

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-question",
        ),
        context,
    )

    assert observation.status == "needs_input"
    assert observation.summary == "Which migration decision should this Pack support?"
    assert await _counts(db_session) == (0, 0, 0)


async def test_chat_invalid_builder_output_persists_no_pack(db_session, monkeypatch) -> None:
    async def invalid(self, **kwargs):  # noqa: ANN001, ANN202
        del self, kwargs
        return PackGenerationResult(
            error_code="invalid_output",
            error_message="The model returned an invalid Pack draft. The draft was not changed.",
        )

    monkeypatch.setattr(PackBuilderWorkflow, "generate", invalid)
    executor, context, _ = await _executor_context(db_session, suffix="invalid")

    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-invalid",
        ),
        context,
    )

    assert observation.status == "failed"
    assert observation.error_code == "pack_generation_invalid_output"
    assert "No Pack was created" in str(observation.summary)
    assert await _counts(db_session) == (0, 0, 0)


async def test_chat_save_and_receipt_roll_back_together(db_session, monkeypatch) -> None:
    async def generate(self, **kwargs):  # noqa: ANN001, ANN202
        del self, kwargs
        return PackGenerationResult(artifact=_rich_artifact())

    monkeypatch.setattr(PackBuilderWorkflow, "generate", generate)
    executor, context, states = await _executor_context(db_session, suffix="atomic")

    async def fail_receipt(**kwargs):  # noqa: ANN003, ANN202
        del kwargs
        raise RuntimeError("receipt write failed")

    monkeypatch.setattr(states, "record_effect", fail_receipt)
    observation = await executor.execute(
        CapabilityExecutionRequest(
            capability_id=PACK_CREATE_CAPABILITY.id,
            arguments=_creation_arguments(),
            idempotency_key="chat-atomic",
        ),
        context,
    )

    assert observation.status == "failed"
    assert await _counts(db_session) == (0, 0, 0)
