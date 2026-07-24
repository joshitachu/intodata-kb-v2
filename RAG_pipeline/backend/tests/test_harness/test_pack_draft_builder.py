from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.db.models import Conversation, User
from src.db.models.harness_pack import (
    HarnessPackDraftRow,
    HarnessPrivateAnalysisPackRow,
    HarnessPrivateAnalysisPackVersionRow,
)
from src.harness.packs.draft_service import DraftConflictError, PackDraftService
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.registry import base_capability_registry


def supply_chain_artifact() -> dict:
    return {
        "purpose": "Assess supply-chain disruption and support 48-hour allocation decisions.",
        "analyses": [
            {"id": "exposure", "label": "Exposure", "purpose": "Map supplier and node exposure."},
            {"id": "time-to-impact", "label": "Time to impact", "purpose": "Estimate time to interruption."},
            {"id": "alternate-sources", "label": "Alternate sources", "purpose": "Assess qualified alternatives."},
            {"id": "mitigation", "label": "Mitigation", "purpose": "Prioritize mitigations."},
        ],
        "evidence_requirements": [{"id": "supplier-data", "label": "Supplier evidence", "required": True, "weight": 70}],
        "output_documents": [
            {"id": "executive-brief", "title": "Executive Disruption Brief", "sections": [{"id": "summary", "heading": "Summary", "order": 1}]},
            {"id": "risk-scorecard", "title": "Supplier and Node Risk Scorecard", "sections": [{"id": "scores", "heading": "Scores", "order": 1}]},
            {"id": "action-plan", "title": "Mitigation Action Plan", "sections": [{"id": "actions", "heading": "Actions", "order": 1}]},
        ],
        "scoring_dimensions": [{"id": "impact", "label": "Patient impact", "weight": 70}, {"id": "urgency", "label": "Urgency", "weight": 30}],
        "capability_requirements": [
            {"capability_id": "rag.docs.search_evidence", "required": True, "purpose": "Supplier evidence"},
            {"capability_id": "port.congestion", "required": False, "purpose": "Port congestion context"},
        ],
        "audience": {
            "label": "Regional procurement directors", "roles": ["regional procurement directors"],
            "decision_needs": ["48-hour allocation decisions"], "domain_familiarity": "regulated cold-chain medicines",
            "desired_outcome": "protect patient supply", "render": {"seniority": "director", "domain_familiarity": "expert", "decision_horizon": "immediate", "detail_level": "brief"},
        },
    }


async def test_private_supply_chain_pack_saves_without_project_and_discloses_optional_capability(db_session) -> None:
    owner = User(username="pack-owner", email="pack-owner@example.com")
    db_session.add(owner)
    await db_session.flush()
    conversation = Conversation(user_id=owner.id, title="Supply chain Pack")
    db_session.add(conversation)
    await db_session.flush()
    service = PackDraftService(db_session, base_capability_registry())
    draft = await service.create_draft(conversation_id=conversation.id, owner_user_id=owner.id, name="Supply Chain Disruption")
    draft = await service.mutate(draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision, patch=supply_chain_artifact())
    saved = await service.save(draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision)
    compatibility = await service.compatibility(saved.id, owner.id, "logistics-project")
    preview = await service.preview(
        version_id=saved.id, owner_user_id=owner.id, project_id="logistics-project",
        executor=HarnessCapabilityExecutor(base_capability_registry()),
    )

    assert saved.content_hash
    assert len(saved.artifact.output_documents) == 3
    assert compatibility["compatible"] is True
    assert compatibility["missing_optional"] == ["port.congestion"]
    assert preview["receipt"]["missing_optional"] == ["port.congestion"]
    assert {item["capability_id"] for item in preview["receipt"]["capability_observations"]} == {"rag.docs.search_evidence"}
    assert saved.receipt["audience_render"] == {"seniority": "director", "domain_familiarity": "expert", "decision_horizon": "immediate", "detail_level": "brief"}


async def test_atomic_complete_pack_creation_rolls_back_every_row_on_failure(db_session) -> None:
    owner = User(username="atomic-pack-owner", email="atomic-pack-owner@example.com")
    db_session.add(owner)
    await db_session.flush()
    service = PackDraftService(db_session, base_capability_registry())

    with pytest.raises(RuntimeError, match="simulated receipt failure"):
        async with service.atomic():
            await service.create_saved_pack(
                conversation_id=None,
                owner_user_id=owner.id,
                name="Atomic Supply Chain Pack",
                artifact=supply_chain_artifact(),
            )
            raise RuntimeError("simulated receipt failure")

    assert await db_session.scalar(select(func.count(HarnessPackDraftRow.id))) == 0
    assert await db_session.scalar(select(func.count(HarnessPrivateAnalysisPackRow.id))) == 0
    assert await db_session.scalar(
        select(func.count(HarnessPrivateAnalysisPackVersionRow.id))
    ) == 0


async def test_draft_revisions_are_optimistic_and_audience_cannot_suppress_evidence(db_session) -> None:
    owner = User(username="pack-revision", email="pack-revision@example.com")
    db_session.add(owner)
    await db_session.flush()
    conversation = Conversation(user_id=owner.id, title="Draft")
    db_session.add(conversation)
    await db_session.flush()
    service = PackDraftService(db_session, base_capability_registry())
    draft = await service.create_draft(conversation_id=conversation.id, owner_user_id=owner.id)
    with pytest.raises(DraftConflictError):
        await service.mutate(draft_id=draft.id, owner_user_id=owner.id, expected_revision=99, patch={})
    draft = await service.mutate(draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision, patch=supply_chain_artifact())
    unsafe = supply_chain_artifact()
    unsafe["audience"]["desired_outcome"] = "hide evidence from directors"
    changed = await service.mutate(draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision, patch=unsafe)
    assert changed.validation["valid"] is False
    assert "suppress" in " ".join(changed.validation["errors"]).lower()


async def test_private_pack_identity_and_content_hash_are_owner_scoped(db_session) -> None:
    owners = [
        User(username="pack-owner-a", email="pack-owner-a@example.com"),
        User(username="pack-owner-b", email="pack-owner-b@example.com"),
    ]
    db_session.add_all(owners)
    await db_session.flush()

    saved = []
    for owner in owners:
        conversation = Conversation(user_id=owner.id, title="Independent Pack")
        db_session.add(conversation)
        await db_session.flush()
        service = PackDraftService(db_session, base_capability_registry())
        draft = await service.create_draft(
            conversation_id=conversation.id,
            owner_user_id=owner.id,
            name="Shared Name",
        )
        draft = await service.mutate(
            draft_id=draft.id,
            owner_user_id=owner.id,
            expected_revision=draft.revision,
            patch=supply_chain_artifact(),
        )
        saved.append(await service.save(
            draft_id=draft.id,
            owner_user_id=owner.id,
            expected_revision=draft.revision,
            pack_id="analysis.shared-name",
        ))

    assert saved[0].pack_id == saved[1].pack_id
    assert saved[0].content_hash == saved[1].content_hash
    assert saved[0].owner_user_id != saved[1].owner_user_id


async def test_detached_draft_can_save_and_fork_without_a_conversation(db_session) -> None:
    owner = User(username="detached-pack-owner", email="detached-pack-owner@example.com")
    db_session.add(owner)
    await db_session.flush()
    service = PackDraftService(db_session, base_capability_registry())

    draft = await service.create_draft(owner_user_id=owner.id, name="Detached Supply Chain Pack")
    assert draft.conversation_id is None
    draft = await service.mutate(
        draft_id=draft.id,
        owner_user_id=owner.id,
        expected_revision=draft.revision,
        patch=supply_chain_artifact(),
    )
    saved = await service.save(
        draft_id=draft.id, owner_user_id=owner.id, expected_revision=draft.revision
    )
    forked = await service.fork_version(version_id=saved.id, owner_user_id=owner.id)

    assert forked.conversation_id is None
    assert forked.status == "active"
    assert forked.artifact == saved.artifact.model_dump(mode="json")


async def test_discovery_context_is_bounded_and_retains_original_brief(db_session) -> None:
    owner = User(username="discovery-pack-owner", email="discovery-pack-owner@example.com")
    db_session.add(owner)
    await db_session.flush()
    service = PackDraftService(db_session, base_capability_registry())
    draft = await service.create_draft(owner_user_id=owner.id)

    for index in range(4):
        draft = await service.retain_discovery_turn(
            draft_id=draft.id,
            owner_user_id=owner.id,
            instruction=f"Original supply-chain brief {index}" if index == 0 else f"Follow-up {index}",
            question=f"Clarification {index}?",
        )

    turns = draft.discovery_context["turns"]
    assert len(turns) == 3
    assert turns[0] == {
        "instruction": "Original supply-chain brief 0",
        "question": "Clarification 0?",
    }
    assert turns[-1] == {"instruction": "Follow-up 3", "question": "Clarification 3?"}
