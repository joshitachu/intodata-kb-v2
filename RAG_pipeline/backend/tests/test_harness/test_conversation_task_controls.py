from __future__ import annotations

from src.db.models import Conversation, ConversationMessage, User
from src.harness.capability_runtime import CapabilityExecutionContext, CapabilityExecutionRequest
from src.harness.conversation_state import (
    ConversationStateRepository,
    ConversationTaskEffect,
    ConversationTaskFocus,
    ConversationTaskKind,
    ConversationTaskReference,
)
from src.harness.executor import HarnessCapabilityExecutor
from src.harness.registry import base_capability_registry


async def test_background_and_resume_change_focus_without_domain_lifecycle(db_session) -> None:
    user = User(username="focus-owner", email="focus-owner@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Focus")
    db_session.add(conversation)
    await db_session.flush()
    first = ConversationMessage(conversation_id=conversation.id, role="user", content="Create draft")
    db_session.add(first)
    await db_session.flush()
    states = ConversationStateRepository(db_session)
    state = await states.get_or_create(conversation_id=conversation.id, owner_user_id=user.id)
    setup = await states.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=first.id,
        expected_version=state.version,
        idempotency_key="focus-setup",
    )
    await states.start_effect(
        claim_id=setup.id,
        lease_token=setup.lease_token or "",
        call_key="draft-setup",
        capability_id="nexus.packs.drafts.create",
    )
    task = ConversationTaskReference(
        task_ref_id="pack:41",
        kind=ConversationTaskKind.PACK_DRAFT,
        capability_id="nexus.packs.drafts.create",
        draft_id=41,
        focused=True,
    )
    await states.record_effect(
        claim_id=setup.id,
        lease_token=setup.lease_token or "",
        effect=ConversationTaskEffect(
            call_key="draft-setup",
            capability_id="nexus.packs.drafts.create",
            task_ref=task,
        ),
    )
    state = await states.finalize_claim(
        claim_id=setup.id,
        lease_token=setup.lease_token or "",
        action="setup",
        final_result={"status": "ok"},
    )

    registry = base_capability_registry()
    executor = HarnessCapabilityExecutor(
        registry, conversation_state_repository=states
    )

    async def control(action: str, version: int, message_text: str):
        message = ConversationMessage(conversation_id=conversation.id, role="user", content=message_text)
        db_session.add(message)
        await db_session.flush()
        claim = await states.claim_turn(
            conversation_id=conversation.id,
            owner_user_id=user.id,
            message_id=message.id,
            expected_version=version,
            idempotency_key=f"focus:{action}:{message.id}",
        )
        observation = await executor.execute(
            CapabilityExecutionRequest(
                capability_id=f"nexus.conversation_tasks.{action}_conversation_task",
                arguments={"task_ref_id": "pack:41"},
                idempotency_key=f"focus-effect:{action}",
            ),
            CapabilityExecutionContext(
                user_id=user.id,
                conversation_id=conversation.id,
                current_message_id=message.id,
                conversation_state_version=version,
                conversation_turn_claim_id=claim.id,
                conversation_turn_claim_token=claim.lease_token,
            ),
        )
        assert observation.succeeded
        return await states.finalize_claim(
            claim_id=claim.id,
            lease_token=claim.lease_token or "",
            action=f"{action}_task",
            final_result={"observation": observation.model_dump(mode="json")},
        )

    background = await control("background", state.version, "Stop asking me about the Pack")
    assert len(background.active_tasks) == 1
    assert background.active_tasks[0].draft_id == 41
    assert background.active_tasks[0].focus_state is ConversationTaskFocus.BACKGROUND
    assert not background.active_tasks[0].focused

    resumed = await control("resume", background.version, "Continue the Pack")
    assert len(resumed.active_tasks) == 1
    assert resumed.active_tasks[0].focus_state is ConversationTaskFocus.FOCUSED
    assert resumed.active_tasks[0].focused
