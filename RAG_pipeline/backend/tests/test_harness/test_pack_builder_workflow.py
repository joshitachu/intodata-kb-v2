from __future__ import annotations

import copy
import json

import pytest

import src.harness.packs.builder_workflow as builder_workflow
from src.db.models import Conversation, User
from src.generation.providers.types import RuntimeToolCallRequest, ToolAwareLLMResponse
from src.harness.packs.builder_workflow import PackBuilderWorkflow
from src.harness.packs.draft_service import PackDraftService
from src.harness.registry import base_capability_registry


def supply_chain_artifact() -> dict:
    return {
        "schema_version": 3,
        "purpose": "Assess cold-chain supply disruption and support 48-hour allocation decisions.",
        "analyses": [
            {"id": "exposure", "label": "Exposure", "purpose": "Map supplier and lane exposure."},
            {"id": "time-to-impact", "label": "Time to impact", "purpose": "Estimate interruption timing."},
            {"id": "alternatives", "label": "Alternatives", "purpose": "Assess qualified alternate sources."},
        ],
        "evidence_requirements": [
            {"id": "supplier-status", "label": "Supplier status", "required": True, "weight": 70},
            {"id": "temperature-risk", "label": "Temperature-control risk", "required": True, "weight": 30},
        ],
        "output_documents": [
            {
                "id": "executive-brief",
                "title": "Executive Disruption Brief",
                "sections": [
                    {"id": "summary", "heading": "Decision summary", "order": 1},
                    {"id": "exposure", "heading": "Exposure", "order": 2},
                ],
            },
            {
                "id": "risk-scorecard",
                "title": "Cold-Chain Risk Scorecard",
                "sections": [{"id": "risks", "heading": "Prioritized risks", "order": 1}],
            },
            {
                "id": "allocation-plan",
                "title": "Allocation and Mitigation Plan",
                "sections": [{"id": "actions", "heading": "Allocation actions", "order": 1}],
            },
        ],
        "scoring_dimensions": [
            {"id": "patient-impact", "label": "Patient impact", "weight": 70},
            {"id": "urgency", "label": "Urgency", "weight": 30},
        ],
        "capability_requirements": [],
        "compatibility": {
            "supported_project_kinds": ["supply-chain"],
            "required_domains": ["cold-chain procurement"],
        },
        "audience": {
            "label": "Cold-chain procurement leaders",
            "roles": ["regional procurement directors", "cold-chain sourcing leads"],
            "decision_needs": ["48-hour allocation decisions", "supplier substitution decisions"],
            "domain_familiarity": "regulated cold-chain procurement",
            "desired_outcome": "protect patient supply while preserving temperature controls",
            "render": {
                "seniority": "director",
                "domain_familiarity": "expert",
                "decision_horizon": "immediate",
                "detail_level": "brief",
            },
        },
    }


async def _service_and_conversation(db_session) -> tuple[PackDraftService, Conversation, User]:
    owner = User(username="pack-builder-owner", email="pack-builder-owner@example.com")
    db_session.add(owner)
    await db_session.flush()
    conversation = Conversation(user_id=owner.id, title="Supply-chain Pack")
    db_session.add(conversation)
    await db_session.flush()
    return PackDraftService(db_session, base_capability_registry()), conversation, owner


async def test_builder_drafts_and_revises_complete_supply_chain_pack_without_capability_ids(db_session) -> None:
    service, conversation, owner = await _service_and_conversation(db_session)
    initial = supply_chain_artifact()
    revised = copy.deepcopy(initial)
    revised["purpose"] = "Assess cold-chain supply disruption and prioritize allocation decisions within 24 hours."
    revised["output_documents"][0]["sections"].append(
        {"id": "assumptions", "heading": "Assumptions and evidence gaps", "order": 3}
    )
    calls: list[list[dict[str, str]]] = []

    async def model(messages: list[dict[str, str]]) -> str:
        calls.append(messages)
        artifact = initial if len(calls) == 1 else revised
        return f"```json\n{json.dumps({'name': 'Supply-Chain Disruption Assessment', 'artifact': artifact})}\n```"

    workflow = PackBuilderWorkflow(model_callable=model)
    draft, answer = await workflow.run(
        service=service,
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        query="Create a Supply-Chain Disruption Assessment Pack for cold-chain procurement leaders.",
    )

    assert draft.validation["valid"] is True
    assert draft.name == "Supply-Chain Disruption Assessment"
    assert len(draft.artifact["output_documents"]) == 3
    assert draft.artifact["audience"]["domain_familiarity"] == "regulated cold-chain procurement"
    assert draft.artifact["audience"]["render"]["detail_level"] == "brief"
    assert "3 output documents" in answer
    assert "capability" not in answer.casefold()

    changed, revised_answer = await workflow.run(
        service=service,
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        query="Revise it for a 24-hour decision horizon and add an assumptions section to the executive brief.",
    )

    assert changed.revision == draft.revision + 1
    assert changed.artifact["purpose"] == revised["purpose"]
    assert changed.artifact["output_documents"][0]["sections"][-1]["id"] == "assumptions"
    assert initial["purpose"] in calls[1][1]["content"]
    assert "preview it against a project" in revised_answer


async def test_builder_invalid_model_output_leaves_draft_unchanged(db_session) -> None:
    service, conversation, owner = await _service_and_conversation(db_session)

    async def model(_: list[dict[str, str]]) -> str:
        return "I drafted the Pack, but forgot to return JSON."

    draft, answer = await PackBuilderWorkflow(model_callable=model).run(
        service=service, conversation_id=conversation.id, owner_user_id=owner.id, query="Create a Pack."
    )

    assert draft.revision == 1
    assert draft.artifact == {}
    assert "invalid Pack draft" in answer


async def test_builder_protected_model_output_leaves_draft_unchanged(db_session) -> None:
    service, conversation, owner = await _service_and_conversation(db_session)
    unsafe = supply_chain_artifact()
    unsafe["actions"] = [{"command": "change permissions"}]

    async def model(_: list[dict[str, str]]) -> str:
        return json.dumps({"artifact": unsafe})

    draft, answer = await PackBuilderWorkflow(model_callable=model).run(
        service=service, conversation_id=conversation.id, owner_user_id=owner.id, query="Create a Pack."
    )

    assert draft.revision == 1
    assert draft.artifact == {}
    assert "protected or executable" in answer


async def test_builder_provider_failure_leaves_draft_unchanged(db_session) -> None:
    service, conversation, owner = await _service_and_conversation(db_session)

    async def unavailable(_: list[dict[str, str]]) -> str:
        raise RuntimeError("provider unavailable")

    draft, answer = await PackBuilderWorkflow(model_callable=unavailable).run(
        service=service, conversation_id=conversation.id, owner_user_id=owner.id, query="Create a Pack."
    )

    assert draft.revision == 1
    assert draft.artifact == {}
    assert "selected model could not draft" in answer


async def test_builder_incomplete_request_asks_one_question_without_mutating(db_session) -> None:
    service, conversation, owner = await _service_and_conversation(db_session)

    async def model(_: list[dict[str, str]]) -> str:
        return json.dumps({"question": "What decision should the audience make?"})

    draft, answer = await PackBuilderWorkflow(model_callable=model).run(
        service=service, conversation_id=conversation.id, owner_user_id=owner.id, query="Build a Pack for us."
    )

    assert draft.revision == 1
    assert draft.artifact == {}
    assert answer == "What decision should the audience make?"


async def test_builder_save_and_cancel_commands_do_not_call_the_model(db_session) -> None:
    service, conversation, owner = await _service_and_conversation(db_session)
    existing = await service.create_draft(conversation_id=conversation.id, owner_user_id=owner.id)
    await service.mutate(
        draft_id=existing.id,
        owner_user_id=owner.id,
        expected_revision=existing.revision,
        patch=supply_chain_artifact(),
    )

    async def model(_: list[dict[str, str]]) -> str:
        raise AssertionError("deterministic command must not invoke the model")

    workflow = PackBuilderWorkflow(model_callable=model)
    saved, save_answer = await workflow.run(
        service=service, conversation_id=conversation.id, owner_user_id=owner.id, query="/pack save"
    )

    cancellation = Conversation(user_id=owner.id, title="Cancellation")
    db_session.add(cancellation)
    await db_session.flush()
    abandoned, cancel_answer = await workflow.run(
        service=service, conversation_id=cancellation.id, owner_user_id=owner.id, query="/pack cancel"
    )

    assert saved.status == "saved"
    assert save_answer == "Saved private Analysis Pack version 1.0.0."
    assert abandoned.status == "abandoned"
    assert cancel_answer == "This Pack draft has been abandoned."


@pytest.mark.asyncio
async def test_default_builder_uses_schema_constrained_provider_output(monkeypatch) -> None:
    proposal = {"name": "Supply-Chain Disruption Assessment", "artifact": supply_chain_artifact()}

    async def structured(messages, tools, images=None):
        del messages, images
        assert len(tools) == 1
        assert tools[0].name == "propose_private_analysis_pack"
        assert "artifact" in tools[0].input_schema["properties"]
        return ToolAwareLLMResponse(
            provider="test",
            tool_calls=[
                RuntimeToolCallRequest(
                    call_id="proposal-1",
                    tool_name="propose_private_analysis_pack",
                    arguments=proposal,
                )
            ],
        )

    monkeypatch.setattr(builder_workflow, "generate_answer_with_runtime_tools", structured)
    raw = await builder_workflow._generate_structured_proposal(
        [{"role": "user", "content": "Build a supply-chain Pack"}]
    )

    artifact, question, name = builder_workflow._parse_model_proposal(raw)
    assert artifact is not None
    assert question is None
    assert name == "Supply-Chain Disruption Assessment"


@pytest.mark.asyncio
async def test_default_builder_falls_back_when_provider_has_no_tool_support(monkeypatch) -> None:
    async def unsupported(messages, tools, images=None):
        del messages, tools, images
        return ToolAwareLLMResponse(provider="test", no_tool_support=True)

    async def text_fallback(messages, images=None):
        del messages, images
        return json.dumps({"name": "Fallback Pack", "artifact": supply_chain_artifact()})

    monkeypatch.setattr(builder_workflow, "generate_answer_with_runtime_tools", unsupported)
    monkeypatch.setattr(builder_workflow, "generate_answer", text_fallback)

    raw = await builder_workflow._generate_structured_proposal(
        [{"role": "user", "content": "Build a supply-chain Pack"}]
    )
    artifact, _, name = builder_workflow._parse_model_proposal(raw)
    assert artifact is not None
    assert name == "Fallback Pack"
