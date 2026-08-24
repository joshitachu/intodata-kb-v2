import datetime

import pytest
from sqlalchemy import select

from src.db.models import Conversation, ConversationMessage, User
from src.db.models.harness import (
    HarnessConversationStateRow,
    HarnessConversationTurnClaimRow,
)
from src.db.models.harness_pack import HarnessPackDraftRow
from src.harness.conversation_state import (
    ConversationPreparedAction,
    ConversationRequestedInput,
    ConversationState,
    ConversationStateConflict,
    ConversationStateRepository,
    ConversationTaskEffect,
    ConversationTaskKind,
    ConversationTaskReference,
    TurnClaimStatus,
)
from src.harness.packs.draft_artifact import PackDraftArtifact
from src.harness.packs.draft_service import PackDraftService
from src.harness.registry import base_capability_registry
from src.harness.repository import HarnessWorkflowRepository
from src.harness.service import HarnessWorkflowService


async def _seed(db_session, suffix: str = "base"):
    user = User(
        username=f"state-{suffix}",
        email=f"state-{suffix}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="State", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    return user, conversation


async def _message(db_session, conversation: Conversation, content: str):
    row = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=content,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_state_is_owner_bound_bounded_and_allows_only_one_focus(db_session) -> None:
    owner, conversation = await _seed(db_session, "owner")
    state = await ConversationStateRepository(db_session).get_or_create(
        conversation_id=conversation.id, owner_user_id=owner.id
    )
    assert state.version == 1
    assert state.model_projection() == {
        "conversation_id": conversation.id,
        "version": 1,
        "objective": None,
        "referenced_entities": [],
        "requested_inputs": [],
        "active_tasks": [],
        "prepared_action": None,
        "last_applied_message_id": None,
    }
    other, _ = await _seed(db_session, "other")
    with pytest.raises(Exception, match="unavailable"):
        await ConversationStateRepository(db_session).load(
            conversation_id=conversation.id, owner_user_id=other.id
        )


async def test_taskless_requested_input_preserves_standalone_objective(db_session) -> None:
    owner, conversation = await _seed(db_session, "standalone-objective")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=owner.id
    )
    row = await db_session.get(HarnessConversationStateRow, state.id)
    assert row is not None
    row.objective = "Clarify the reporting period"
    row.requested_inputs = [
        {
            "task_ref_id": None,
            "field": "reporting_period",
            "prompt": "Which reporting period?",
            "options": [],
        }
    ]
    await db_session.flush()
    reconciled = await repo.reconcile_authorities(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
    )
    assert reconciled.objective == "Clarify the reporting period"
    assert reconciled.requested_inputs[0].task_ref_id is None

    message = await _message(db_session, conversation, "January")
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        message_id=message.id,
        expected_version=reconciled.version,
        idempotency_key="turn:complete-standalone-objective",
    )
    assert claim.lease_token
    completed = await repo.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        action="complete_objective",
        final_result={"stop_reason": "answered"},
        complete_objective=True,
    )
    assert completed.objective is None
    assert completed.requested_inputs == []
    with pytest.raises(ValueError, match="at most one focused"):
        ConversationState.model_validate(
            {
                **state.model_dump(mode="json"),
                "active_tasks": [
                    {
                        "task_ref_id": "pack:1",
                        "kind": "pack_draft",
                        "capability_id": "nexus.packs.drafts.mutate",
                        "draft_id": 1,
                        "focused": True,
                    },
                    {
                        "task_ref_id": "workflow:2",
                        "kind": "onboarding_workflow",
                        "capability_id": "nexus.workflow.onboard_project",
                        "workflow_run_id": 2,
                        "focused": True,
                    },
                ],
            }
        )


async def test_prepared_action_persists_and_clears_under_turn_versioning(
    db_session,
) -> None:
    owner, conversation = await _seed(db_session, "prepared-action")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=owner.id
    )
    first = await _message(db_session, conversation, "Prepare a Talend Pack")
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        message_id=first.id,
        expected_version=state.version,
        idempotency_key="turn:prepare-pack",
    )
    assert claim.lease_token
    prepared = ConversationPreparedAction(
        capability_id="nexus.packs.create",
        semantic_name="create_pack",
        objective="Create the Talend migration Pack",
        arguments={"name": "Talend Migration"},
        source_message_ids=[first.id],
    )
    stored = await repo.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        action="prepare_action",
        final_result={"stop_reason": "clarification_required"},
        prepared_action=prepared,
    )
    assert stored.prepared_action == prepared
    assert stored.model_projection()["prepared_action"]["semantic_name"] == (
        "create_pack"
    )

    second = await _message(db_session, conversation, "Okay, do it")
    second_claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=owner.id,
        message_id=second.id,
        expected_version=stored.version,
        idempotency_key="turn:execute-pack",
    )
    assert second_claim.lease_token
    cleared = await repo.finalize_claim(
        claim_id=second_claim.id,
        lease_token=second_claim.lease_token,
        action="execute_action",
        final_result={"stop_reason": "answered"},
        clear_prepared_action=True,
    )
    assert cleared.prepared_action is None


async def test_claim_blocks_competing_message_before_effect_and_failure_releases(db_session) -> None:
    user, conversation = await _seed(db_session, "claim")
    first = await _message(db_session, conversation, "start a pack")
    second = await _message(db_session, conversation, "unrelated question")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=first.id,
        expected_version=state.version,
        idempotency_key="turn:first",
    )
    with pytest.raises(ConversationStateConflict, match="Another message"):
        await repo.claim_turn(
            conversation_id=conversation.id,
            owner_user_id=user.id,
            message_id=second.id,
            expected_version=state.version,
            idempotency_key="turn:second",
        )
    assert claim.lease_token is not None
    await repo.fail_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        final_result={"stop_reason": "planner_unavailable"},
    )
    second_claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=second.id,
        expected_version=state.version,
        idempotency_key="turn:second",
    )
    assert second_claim.status is TurnClaimStatus.CLAIMED


async def test_same_applied_message_replays_without_a_new_claim_or_version(db_session) -> None:
    user, conversation = await _seed(db_session, "replay")
    message = await _message(db_session, conversation, "answer this")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:replay",
    )
    assert claim.lease_token
    applied = await repo.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        action="answer",
        final_result={"stop_reason": "answered", "output": "done"},
        response_fingerprint="f" * 64,
    )
    replay = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:replay",
    )
    assert replay.replayed
    assert replay.final_result == {"stop_reason": "answered", "output": "done"}
    assert (await repo.load(conversation_id=conversation.id, owner_user_id=user.id)).version == applied.version


async def test_stale_claim_cannot_start_effect_after_state_version_advance(db_session) -> None:
    user, conversation = await _seed(db_session, "stale-effect")
    message = await _message(db_session, conversation, "mutate")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:stale-effect",
    )
    state_row = await db_session.get(HarnessConversationStateRow, state.id)
    assert state_row is not None
    state_row.version += 1
    await db_session.flush()
    provider_called = False

    async def stateful_backend() -> None:
        nonlocal provider_called
        assert claim.lease_token
        await repo.start_effect(
            claim_id=claim.id,
            lease_token=claim.lease_token,
            call_key="pack:mutate",
            capability_id="nexus.packs.drafts.refine",
        )
        provider_called = True

    with pytest.raises(ConversationStateConflict, match="stale"):
        await stateful_backend()
    assert provider_called is False
    row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert row is not None and row.status == TurnClaimStatus.FAILED.value


async def test_expired_claim_reclaims_only_if_no_effect_and_version_is_current(db_session) -> None:
    user, conversation = await _seed(db_session, "expired")
    first = await _message(db_session, conversation, "first")
    second = await _message(db_session, conversation, "second")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=2)
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=first.id,
        expected_version=state.version,
        idempotency_key="turn:expired",
        lease_seconds=1,
        now=past,
    )
    reclaimed = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=first.id,
        expected_version=state.version,
        idempotency_key="turn:expired",
    )
    assert reclaimed.id == claim.id and reclaimed.lease_token
    await repo.fail_claim(
        claim_id=reclaimed.id,
        lease_token=reclaimed.lease_token,
        final_result={"stop_reason": "failed"},
    )
    next_claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=second.id,
        expected_version=state.version,
        idempotency_key="turn:expired-next",
    )
    assert next_claim.status is TurnClaimStatus.CLAIMED


async def test_abandoned_same_message_reclaims_without_recovery_hold(db_session) -> None:
    user, conversation = await _seed(db_session, "abandoned-replay")
    message = await _message(db_session, conversation, "retry me")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=2)
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:abandoned-replay",
        lease_seconds=1,
        now=past,
    )
    row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert row is not None
    row.status = TurnClaimStatus.ABANDONED.value
    row.finalized_at = datetime.datetime.now(datetime.UTC)
    await db_session.flush()

    reclaimed = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:abandoned-replay",
    )
    assert reclaimed.id == claim.id
    assert reclaimed.status is TurnClaimStatus.CLAIMED
    assert reclaimed.lease_token


async def test_multiple_effects_finalize_once_with_one_focused_task(db_session) -> None:
    user, conversation = await _seed(db_session, "effects")
    message = await _message(db_session, conversation, "start both")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:effects",
    )
    assert claim.lease_token
    pack = ConversationTaskReference(
        task_ref_id="pack:11",
        kind=ConversationTaskKind.PACK_DRAFT,
        capability_id="nexus.packs.drafts.create",
        draft_id=11,
        focused=True,
    )
    workflow = ConversationTaskReference(
        task_ref_id="workflow:22",
        kind=ConversationTaskKind.ONBOARDING_WORKFLOW,
        capability_id="nexus.workflow.onboard_project",
        workflow_run_id=22,
        project_id="project-a",
        focused=True,
    )
    for call_key, capability_id, task in (
        ("pack:create", pack.capability_id, pack),
        ("onboard:start", workflow.capability_id, workflow),
    ):
        await repo.start_effect(
            claim_id=claim.id,
            lease_token=claim.lease_token,
            call_key=call_key,
            capability_id=capability_id,
        )
        await repo.record_effect(
            claim_id=claim.id,
            lease_token=claim.lease_token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=capability_id,
                task_ref=task,
                requested_inputs=(
                    [
                        ConversationRequestedInput(
                            task_ref_id=task.task_ref_id,
                            field="source_type",
                            prompt="Choose a source.",
                        )
                    ]
                    if task.kind is ConversationTaskKind.ONBOARDING_WORKFLOW
                    else []
                ),
            ),
        )
    updated = await repo.finalize_claim(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        action="answer",
        final_result={"stop_reason": "answered"},
    )
    assert updated.version == state.version + 1
    assert len(updated.active_tasks) == 2
    assert [item.task_ref_id for item in updated.active_tasks if item.focused] == [
        "workflow:22"
    ]
    assert updated.requested_inputs[0].task_ref_id == "workflow:22"


async def test_removing_focused_task_refocuses_newest_remaining_task(db_session) -> None:
    user, conversation = await _seed(db_session, "refocus")
    first = await _message(db_session, conversation, "start both")
    second = await _message(db_session, conversation, "finish workflow")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    first_claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=first.id,
        expected_version=state.version,
        idempotency_key="turn:refocus:first",
    )
    assert first_claim.lease_token
    for call_key, task in (
        (
            "pack",
            ConversationTaskReference(
                task_ref_id="pack:1",
                kind=ConversationTaskKind.PACK_DRAFT,
                capability_id="nexus.packs.drafts.create",
                draft_id=1,
                focused=True,
            ),
        ),
        (
            "workflow",
            ConversationTaskReference(
                task_ref_id="workflow:2",
                kind=ConversationTaskKind.DOCUMENTATION_WORKFLOW,
                capability_id="mcp.docs.generate_project_documentation",
                workflow_run_id=2,
                project_id="p",
                focused=True,
            ),
        ),
    ):
        await repo.start_effect(
            claim_id=first_claim.id,
            lease_token=first_claim.lease_token,
            call_key=call_key,
            capability_id=task.capability_id,
        )
        await repo.record_effect(
            claim_id=first_claim.id,
            lease_token=first_claim.lease_token,
            effect=ConversationTaskEffect(
                call_key=call_key,
                capability_id=task.capability_id,
                task_ref=task,
            ),
        )
    with_tasks = await repo.finalize_claim(
        claim_id=first_claim.id,
        lease_token=first_claim.lease_token,
        action="tools",
        final_result={"stop_reason": "answered"},
    )
    second_claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=second.id,
        expected_version=with_tasks.version,
        idempotency_key="turn:refocus:second",
    )
    assert second_claim.lease_token
    await repo.start_effect(
        claim_id=second_claim.id,
        lease_token=second_claim.lease_token,
        call_key="finish-workflow",
        capability_id="nexus.workflows.cancel",
    )
    await repo.record_effect(
        claim_id=second_claim.id,
        lease_token=second_claim.lease_token,
        effect=ConversationTaskEffect(
            call_key="finish-workflow",
            capability_id="nexus.workflows.cancel",
            remove_task_ref_id="workflow:2",
        ),
    )
    refocused = await repo.finalize_claim(
        claim_id=second_claim.id,
        lease_token=second_claim.lease_token,
        action="workflow_terminal",
        final_result={"stop_reason": "answered"},
    )
    assert [item.task_ref_id for item in refocused.active_tasks] == ["pack:1"]
    assert refocused.active_tasks[0].focused is True


async def test_exact_pack_revision_recovery_finalizes_without_redispatch(db_session) -> None:
    user, conversation = await _seed(db_session, "recover-pack")
    message = await _message(db_session, conversation, "mutate")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:recover-pack",
    )
    assert claim.lease_token
    service = PackDraftService(db_session, base_capability_registry())
    draft = await service.create_draft(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        name="Recover",
    )
    await repo.start_effect(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        call_key="mutate",
        capability_id="nexus.packs.drafts.refine",
        authority_hint={
            "kind": "pack_revision",
            "draft_id": draft.id,
            "expected_revision": draft.revision,
            "target_revision": draft.revision + 1,
        },
    )
    await service.replace_artifact(
        draft_id=draft.id,
        owner_user_id=user.id,
        expected_revision=draft.revision,
        artifact=PackDraftArtifact(
            manifest_yaml=(
                "name: committed-once\n"
                "version: 0.1.0\n"
                "description: Committed exactly once by the recovery path.\n"
                "docs:\n"
                "  - name: REPORT\n"
            )
        ),
    )
    row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert row is not None
    row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
    row.lease_expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    await db_session.commit()

    recovered = await repo.recover_claim_authority(
        claim_id=claim.id,
        owner_user_id=user.id,
    )

    assert recovered is not None
    assert recovered.version == state.version + 1
    assert recovered.active_tasks[0].draft_id == draft.id
    assert recovered.active_tasks[0].focused is True
    claim_row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert claim_row is not None and claim_row.status == TurnClaimStatus.APPLIED.value


async def test_uncommitted_create_intent_recovers_as_no_effect(db_session) -> None:
    user, conversation = await _seed(db_session, "recover-no-effect")
    message = await _message(db_session, conversation, "create")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:recover-no-effect",
    )
    assert claim.lease_token
    await repo.start_effect(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        call_key="create",
        capability_id="nexus.packs.drafts.create",
        authority_hint={"kind": "pack_create"},
    )
    row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert row is not None
    row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
    row.lease_expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    recovered = await repo.recover_claim_authority(
        claim_id=claim.id,
        owner_user_id=user.id,
    )
    assert recovered is not None and recovered.version == state.version
    assert recovered.active_tasks == []
    assert row.status == TurnClaimStatus.FAILED.value


async def test_exact_workflow_recovery_adds_active_then_removes_terminal_task(
    db_session,
) -> None:
    user, conversation = await _seed(db_session, "recover-workflow")
    first = await _message(db_session, conversation, "start docs")
    second = await _message(db_session, conversation, "observe terminal")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    workflow_service = HarnessWorkflowService(
        HarnessWorkflowRepository(db_session), base_capability_registry()
    )
    run = await workflow_service.start_run(
        conversation_id=conversation.id,
        user_id=user.id,
        project_id="project-a",
        intent_mode="generate_docs",
        capability_id="mcp.docs.generate_project_documentation",
        input_summary="Generate docs",
    )
    first_claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=first.id,
        expected_version=state.version,
        idempotency_key="turn:recover-workflow-active",
    )
    assert first_claim.lease_token
    await repo.start_effect(
        claim_id=first_claim.id,
        lease_token=first_claim.lease_token,
        call_key="docs",
        capability_id=run.capability_id,
        authority_hint={
            "kind": "workflow_run",
            "workflow_run_id": run.id,
            "project_id": run.project_id,
        },
    )
    first_row = await db_session.get(HarnessConversationTurnClaimRow, first_claim.id)
    assert first_row is not None
    first_row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
    first_row.lease_expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    await db_session.commit()
    active = await repo.recover_claim_authority(
        claim_id=first_claim.id,
        owner_user_id=user.id,
    )
    assert active is not None
    assert [item.workflow_run_id for item in active.active_tasks] == [run.id]

    run = await workflow_service.mark_failed(run, error_message="provider failed")
    second_claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=second.id,
        expected_version=active.version,
        idempotency_key="turn:recover-workflow-terminal",
    )
    assert second_claim.lease_token
    await repo.start_effect(
        claim_id=second_claim.id,
        lease_token=second_claim.lease_token,
        call_key="terminal",
        capability_id="nexus.workflows.cancel",
        authority_hint={
            "kind": "workflow_run",
            "workflow_run_id": run.id,
            "project_id": run.project_id,
        },
    )
    second_row = await db_session.get(HarnessConversationTurnClaimRow, second_claim.id)
    assert second_row is not None
    second_row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
    second_row.lease_expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    await db_session.commit()
    terminal = await repo.recover_claim_authority(
        claim_id=second_claim.id,
        owner_user_id=user.id,
    )
    assert terminal is not None
    assert terminal.active_tasks == []


async def test_recovery_rejects_same_owner_cross_conversation_pack_authority(
    db_session,
) -> None:
    user, conversation = await _seed(db_session, "recover-cross")
    other = Conversation(title="Other", user_id=user.id)
    db_session.add(other)
    await db_session.flush()
    message = await _message(db_session, conversation, "recover forged")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    draft = await PackDraftService(
        db_session, base_capability_registry()
    ).create_draft(
        conversation_id=other.id,
        owner_user_id=user.id,
        name="Other",
    )
    claim = await repo.claim_turn(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        message_id=message.id,
        expected_version=state.version,
        idempotency_key="turn:recover-cross",
    )
    assert claim.lease_token
    await repo.start_effect(
        claim_id=claim.id,
        lease_token=claim.lease_token,
        call_key="forged",
        capability_id="nexus.packs.drafts.validate",
        authority_hint={
            "kind": "pack_revision",
            "draft_id": draft.id,
            "expected_revision": draft.revision,
            "target_revision": draft.revision,
        },
    )
    row = await db_session.get(HarnessConversationTurnClaimRow, claim.id)
    assert row is not None
    row.status = TurnClaimStatus.RECOVERY_REQUIRED.value
    row.lease_expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    assert (
        await repo.recover_claim_authority(claim_id=claim.id, owner_user_id=user.id)
        is None
    )
    assert row.status == TurnClaimStatus.RECOVERY_REQUIRED.value


async def test_pack_requested_input_comes_from_validation_not_legacy_discovery(db_session) -> None:
    user, conversation = await _seed(db_session, "pack-input")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    now = datetime.datetime.now(datetime.UTC)
    draft = HarnessPackDraftRow(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        status="active",
        revision=1,
        name="Incomplete",
        artifact={},
        validation={"valid": False, "errors": ["purpose is required"]},
        discovery_context={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(draft)
    await db_session.flush()
    state_row = await db_session.get(HarnessConversationStateRow, state.id)
    assert state_row is not None
    state_row.active_tasks = [
        ConversationTaskReference(
            task_ref_id=f"pack:{draft.id}",
            kind=ConversationTaskKind.PACK_DRAFT,
            capability_id="nexus.packs.drafts.create",
            draft_id=draft.id,
            focused=True,
        ).model_dump(mode="json")
    ]
    await db_session.flush()
    hydrated = await repo.reconcile_authorities(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert hydrated.requested_inputs[0].field == "pack_details"
    assert "purpose is required" in hydrated.requested_inputs[0].prompt


async def test_terminal_pack_authority_removes_task_and_requested_input(db_session) -> None:
    user, conversation = await _seed(db_session, "terminal-pack")
    repo = ConversationStateRepository(db_session)
    state = await repo.get_or_create(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    now = datetime.datetime.now(datetime.UTC)
    draft = HarnessPackDraftRow(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        status="abandoned",
        revision=2,
        name="Done",
        artifact={},
        validation={"valid": False, "errors": []},
        discovery_context={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(draft)
    await db_session.flush()
    task = ConversationTaskReference(
        task_ref_id=f"pack:{draft.id}",
        kind=ConversationTaskKind.PACK_DRAFT,
        capability_id="nexus.packs.drafts.abandon",
        draft_id=draft.id,
        focused=True,
    )
    state_row = await db_session.get(HarnessConversationStateRow, state.id)
    assert state_row is not None
    state_row.active_tasks = [task.model_dump(mode="json")]
    state_row.requested_inputs = [
        ConversationRequestedInput(
            task_ref_id=task.task_ref_id,
            field="pack_details",
            prompt="More details",
        ).model_dump(mode="json")
    ]
    await db_session.flush()
    hydrated = await repo.reconcile_authorities(
        conversation_id=conversation.id, owner_user_id=user.id
    )
    assert hydrated.active_tasks == []
    assert hydrated.requested_inputs == []
    rows = (
        await db_session.execute(
            select(HarnessConversationTurnClaimRow).where(
                HarnessConversationTurnClaimRow.conversation_state_id == state.id
            )
        )
    ).scalars().all()
    assert rows == []
