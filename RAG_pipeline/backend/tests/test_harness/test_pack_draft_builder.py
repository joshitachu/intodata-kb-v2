from __future__ import annotations

import pytest

from src.db.models import Conversation, User
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
