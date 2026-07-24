from __future__ import annotations

import datetime

import pytest

from src.db.models import Conversation, ConversationMessage, User
from src.harness.approvals import (
    ApprovalConflict,
    ApprovalDecision,
    ApprovalDenied,
    ApprovalService,
)
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityObservation
from src.harness.conversation_state import ConversationStateRepository
from src.harness.effect_operations import EffectOperationService
from src.harness.models import PACK_DRAFT_SAVE_CAPABILITY


async def _authority(db_session):
    user = User(username="effect-owner", email="effect-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Effects")
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
        idempotency_key="effect-authority-test",
    )
    context = CapabilityExecutionContext(
        user_id=user.id,
        conversation_id=conversation.id,
        project_scope=["fixture-project"],
        permissions=set(PACK_DRAFT_SAVE_CAPABILITY.required_permissions),
        current_message_id=message.id,
        conversation_state_version=state.version,
        conversation_turn_claim_id=claim.id,
        conversation_turn_claim_token=claim.lease_token,
    )
    return user, conversation, message, claim, context


async def test_exact_proposal_replays_and_rejects_changed_arguments(db_session) -> None:
    user, _, _, _, context = await _authority(db_session)
    service = ApprovalService(db_session)
    first = await service.propose(
        capability=PACK_DRAFT_SAVE_CAPABILITY,
        semantic_name="save_pack",
        arguments={"draft_id": 9, "expected_revision": 3},
        context=context,
        call_id="call-1",
    )
    replay = await service.propose(
        capability=PACK_DRAFT_SAVE_CAPABILITY,
        semantic_name="save_pack",
        arguments={"expected_revision": 3, "draft_id": 9},
        context=context,
        call_id="call-1",
    )
    assert replay.proposal_id == first.proposal_id
    assert replay.arguments_hash == first.arguments_hash

    with pytest.raises(ApprovalConflict):
        await service.propose(
            capability=PACK_DRAFT_SAVE_CAPABILITY,
            semantic_name="save_pack",
            arguments={"draft_id": 9, "expected_revision": 4},
            context=context,
            call_id="call-1",
        )

    rejected = await service.decide(
        proposal_id=first.proposal_id,
        owner_user_id=user.id,
        expected_version=1,
        decision=ApprovalDecision.REJECT,
        project_scope=["fixture-project"],
        permissions=set(),
    )
    assert rejected.status == "rejected"
    with pytest.raises(ApprovalDenied):
        await service.approved_row(proposal_id=first.proposal_id, owner_user_id=user.id)


async def test_approval_revalidates_scope_and_is_single_use(db_session) -> None:
    user, _, _, _, context = await _authority(db_session)
    service = ApprovalService(db_session)
    proposal = await service.propose(
        capability=PACK_DRAFT_SAVE_CAPABILITY,
        semantic_name="save_pack",
        arguments={"draft_id": 4, "expected_revision": 2},
        context=context,
        call_id="call-2",
    )
    with pytest.raises(ApprovalDenied):
        await service.decide(
            proposal_id=proposal.proposal_id,
            owner_user_id=user.id,
            expected_version=1,
            decision=ApprovalDecision.APPROVE,
            project_scope=[],
            permissions=None,
        )
    approved = await service.decide(
        proposal_id=proposal.proposal_id,
        owner_user_id=user.id,
        expected_version=1,
        decision=ApprovalDecision.APPROVE,
        project_scope=["fixture-project"],
        permissions=None,
    )
    assert approved.status == "approved"
    with pytest.raises(ApprovalConflict):
        await service.decide(
            proposal_id=proposal.proposal_id,
            owner_user_id=user.id,
            expected_version=1,
            decision=ApprovalDecision.REJECT,
            project_scope=["fixture-project"],
            permissions=None,
        )


async def test_committed_effect_replays_without_second_execution(db_session) -> None:
    _, _, _, claim, _ = await _authority(db_session)
    service = EffectOperationService(db_session)
    prepared = await service.prepare(
        turn_claim_id=claim.id,
        call_id="call-effect",
        capability_id="nexus.test.reversible",
        arguments_hash="a" * 64,
    )
    assert prepared.replay is None
    observation = CapabilityObservation(
        capability_id="nexus.test.reversible",
        status="succeeded",
        summary="Changed once.",
        receipt_ref={"idempotency_key": prepared.operation_id},
    )
    await service.commit(prepared, observation)
    replay = await service.prepare(
        turn_claim_id=claim.id,
        call_id="call-effect",
        capability_id="nexus.test.reversible",
        arguments_hash="a" * 64,
    )
    assert replay.operation_id == prepared.operation_id
    assert replay.replay == observation


async def test_expired_proposal_cannot_be_approved(db_session) -> None:
    user, _, _, _, context = await _authority(db_session)
    service = ApprovalService(db_session)
    projection = await service.propose(
        capability=PACK_DRAFT_SAVE_CAPABILITY,
        semantic_name="save_pack",
        arguments={"draft_id": 2, "expected_revision": 1},
        context=context,
        call_id="call-expired",
    )
    row = await service._owned(projection.proposal_id, user.id)  # noqa: SLF001
    row.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    await db_session.flush()
    with pytest.raises(ApprovalConflict, match="expired"):
        await service.decide(
            proposal_id=projection.proposal_id,
            owner_user_id=user.id,
            expected_version=1,
            decision=ApprovalDecision.APPROVE,
            project_scope=["fixture-project"],
            permissions=None,
        )
