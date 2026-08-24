from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import Conversation, ConversationMessage, QueryUsage, User
from src.db.models.harness import (
    HarnessAgentPublicEventRow,
    HarnessAgentRunRow,
    HarnessAgentTurnAuditRow,
)
from src.harness.agent_chat_runtime import execute_production_agent_chat_turn
from src.harness.agent_chat_runtime import (
    AgentChatIdempotencyConflict,
    _apply_interaction_grounding_failure,
    _claim_runtime_cohort,
    _prepared_action_finalization,
)
from src.harness.cohort import CohortDecision, RuntimeCohort
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecision,
    AgentLoopResult,
    AgentModelResult,
    AgentStopReason,
)
from src.harness.conversation_state import (
    ConversationPreparedAction,
    ConversationState,
)
from src.harness.readonly_runtime import grounding_failure_answer


pytestmark = pytest.mark.asyncio


class _OverlappingAnswerModel:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def decide(self, request):  # noqa: ANN001
        del request
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return AgentModelResult(
            decision=AgentDecision(
                action=AgentBoundaryAction.ANSWER,
                content="The authoritative agent answered once.",
            )
        )


async def test_invalid_interaction_uses_only_the_grounding_failure_response() -> None:
    turn = SimpleNamespace(
        result=AgentLoopResult(
            action=AgentBoundaryAction.CLARIFY,
            output="What additional detail should I use to complete this action?",
            stop_reason=AgentStopReason.CLARIFICATION_REQUIRED,
            state_version="state-v1",
        ),
        grounded=None,
    )

    _apply_interaction_grounding_failure(turn)

    expected = grounding_failure_answer()
    assert turn.result.action is AgentBoundaryAction.ANSWER
    assert turn.result.stop_reason is AgentStopReason.INVALID_MODEL_OUTPUT
    assert turn.result.output == expected.content
    assert turn.grounded == expected
    assert "additional detail" not in turn.result.output.casefold()


async def test_effect_turn_updates_and_consumes_prepared_action_context() -> None:
    previous = ConversationPreparedAction(
        capability_id="nexus.packs.create",
        semantic_name="create_pack",
        objective="Create a migration Pack",
        arguments={"name": "Migration"},
        source_message_ids=[10],
        revision=2,
    )
    state = ConversationState(
        id=1,
        conversation_id=2,
        owner_user_id=3,
        version=4,
        prepared_action=previous,
    )
    candidate = previous.model_copy(
        update={"arguments": {"name": "Talend Migration"}}
    )

    updated, clear = _prepared_action_finalization(
        state=state,
        candidate=candidate,
        completed_semantic_name=None,
        message_id=11,
    )

    assert clear is False
    assert updated is not None
    assert updated.revision == 3
    assert updated.source_message_ids == [10, 11]
    assert updated.arguments == {"name": "Talend Migration"}

    updated, clear = _prepared_action_finalization(
        state=state,
        candidate=candidate,
        completed_semantic_name="create_pack",
        message_id=11,
    )
    assert updated is None
    assert clear is True


async def test_concurrent_duplicate_client_turn_executes_model_and_persists_once(
    tmp_path,
) -> None:
    database_path = tmp_path / "agent-chat-concurrency.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as setup:
        user = User(username="agent-once", email="agent-once@example.com")
        setup.add(user)
        await setup.flush()
        conversation = Conversation(title="Exactly once", user_id=user.id)
        setup.add(conversation)
        await setup.commit()
        user_id = user.id
        conversation_id = conversation.id

    model = _OverlappingAnswerModel()
    async def execute(transport: str):
        async with sessions() as session:
            conversation = await session.get(Conversation, conversation_id)
            assert conversation is not None
            await session.refresh(conversation, ["messages"])
            return await execute_production_agent_chat_turn(
                db=session,
                qdrant=None,  # type: ignore[arg-type]
                conversation=conversation,
                query="Answer this once",
                images=None,
                client_turn_id="turn-concurrent-1",
                requested_project_id=None,
                user_id=user_id,
                user_groups=[],
                project_scope=None,
                permissions=None,
                access_token=None,
                transport=transport,
                model=model,
            )

    first = asyncio.create_task(execute("stream"))
    await asyncio.wait_for(model.started.wait(), timeout=5)
    second = asyncio.create_task(execute("nonstream"))
    await asyncio.sleep(0.1)
    model.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert model.calls == 1
    assert first_result.user_message.id == second_result.user_message.id
    assert first_result.assistant_message.id == second_result.assistant_message.id
    assert first_result.result == second_result.result
    async with sessions() as verification:
        user_rows = await verification.scalar(
            select(func.count()).select_from(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "user",
                ConversationMessage.client_turn_id == "turn-concurrent-1",
            )
        )
        assistant_rows = await verification.scalar(
            select(func.count()).select_from(ConversationMessage).where(
                ConversationMessage.in_reply_to_message_id
                == first_result.user_message.id
            )
        )
        audits = await verification.scalar(
            select(func.count()).select_from(HarnessAgentTurnAuditRow)
        )
        usage = await verification.scalar(
            select(func.count()).select_from(QueryUsage).where(
                QueryUsage.interface == "conversation_agent"
            )
        )
    assert (user_rows, assistant_rows, audits, usage) == (1, 1, 1, 1)
    await engine.dispose()


async def test_idempotency_constraints_allow_legacy_null_rows_and_reuse_exact_turn(
    db_session,
) -> None:
    user = User(username="legacy-null", email="legacy-null@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Legacy rows", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    db_session.add_all(
        [
            ConversationMessage(
                conversation_id=conversation.id,
                role="user",
                content="legacy one",
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                role="user",
                content="legacy two",
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="legacy answer one",
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="legacy answer two",
            ),
        ]
    )
    await db_session.flush()
    from src.harness.agent_chat_runtime import _get_or_create_user_message

    first = await _get_or_create_user_message(
        db=db_session,
        conversation_id=conversation.id,
        query="exact payload",
        images=None,
        client_turn_id="exact-turn",
        request_fingerprint="a" * 64,
    )
    second = await _get_or_create_user_message(
        db=db_session,
        conversation_id=conversation.id,
        query="exact payload",
        images=None,
        client_turn_id="exact-turn",
        request_fingerprint="a" * 64,
    )
    assert first.id == second.id
    assert second.content == "exact payload"
    assert await db_session.scalar(
        select(func.count()).select_from(ConversationMessage).where(
            ConversationMessage.conversation_id == conversation.id,
            ConversationMessage.client_turn_id.is_(None),
        )
    ) == 4
    with pytest.raises(AgentChatIdempotencyConflict):
        await _get_or_create_user_message(
            db=db_session,
            conversation_id=conversation.id,
            query="different payload",
            images=None,
            client_turn_id="exact-turn",
            request_fingerprint="b" * 64,
        )


async def test_runtime_cohort_is_claimed_once_and_survives_config_change(
    db_session,
) -> None:
    user = User(username="route-claim", email="route-claim@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Route claim", user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="stable turn",
        client_turn_id="stable-route-turn",
    )
    db_session.add(message)
    await db_session.commit()
    first = CohortDecision(
        cohort=RuntimeCohort.V2_READONLY_PRIMARY,
        cohort_source="flag_readonly_canary",
        cohort_bucket=7,
        reason_code="readonly_allowlist",
    )
    changed_config_candidate = CohortDecision(
        cohort=RuntimeCohort.LEGACY,
        cohort_source="legacy_default",
        cohort_bucket=7,
        reason_code="canary_stage_off",
    )
    assert await _claim_runtime_cohort(
        db=db_session, user_message=message, candidate=first, live_streaming=True
    ) == first
    assert await _claim_runtime_cohort(
        db=db_session,
        user_message=message,
        candidate=changed_config_candidate,
        live_streaming=False,
    ) == first
    assert message.turn_plan["schema"] == "runtime_route_v2"
    assert message.turn_plan["live_streaming"] is True


class _ImmediateAnswerModel:
    async def decide(self, request):  # noqa: ANN001
        del request
        return AgentModelResult(
            decision=AgentDecision(
                action=AgentBoundaryAction.ANSWER,
                content="The legacy runtime answered this turn.",
            )
        )


async def test_legacy_turn_records_real_cohort_not_the_old_literal(db_session) -> None:
    from src.harness.cohort import stable_user_bucket

    user = User(username="cohort-legacy", email="cohort-legacy@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Cohort audit", user_id=user.id)
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])

    # V2 is disabled by default, so every turn must route to the legacy runtime
    # and record the real legacy cohort, never the old single_path/0 literals.
    await execute_production_agent_chat_turn(
        db=db_session,
        qdrant=None,  # type: ignore[arg-type]
        conversation=conversation,
        query="What can you do?",
        images=None,
        client_turn_id="cohort-turn-1",
        requested_project_id=None,
        user_id=user.id,
        user_groups=[],
        project_scope=None,
        permissions=None,
        access_token=None,
        transport="nonstream",
        model=_ImmediateAnswerModel(),
    )
    audit = (
        await db_session.execute(select(HarnessAgentTurnAuditRow))
    ).scalar_one()
    assert audit.cohort_source == "legacy_default"
    assert audit.cohort_source != "single_path"
    assert audit.cohort_bucket == stable_user_bucket(user.id)


async def test_provider_failure_persists_one_typed_stop_for_nonstream(
    db_session, monkeypatch
) -> None:
    from src.config import settings

    user = User(username="provider-stop", email="provider-stop@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Provider stop", user_id=user.id)
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    monkeypatch.setattr(settings, "agent_runtime_v2_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_canary_stage", "workflow_approvals")
    monkeypatch.setattr(settings, "agent_runtime_v2_effects_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_effect_users", [user.id])
    monkeypatch.setattr(settings, "agent_runtime_v2_live_streaming_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_live_streaming_users", [user.id])

    async def fail_primary(**kwargs):  # noqa: ANN003, ANN202
        del kwargs
        raise RuntimeError("provider connection payload must not escape")

    monkeypatch.setattr(
        "src.harness.agent_chat_runtime._run_primary_or_replay", fail_primary
    )

    execution = await execute_production_agent_chat_turn(
        db=db_session,
        qdrant=None,  # type: ignore[arg-type]
        conversation=conversation,
        query="Answer safely",
        images=None,
        client_turn_id="provider-failure-turn",
        requested_project_id=None,
        user_id=user.id,
        user_groups=[],
        project_scope=None,
        permissions=None,
        access_token=None,
        transport="nonstream",
    )

    assert execution.result.stop_reason is AgentStopReason.PLANNER_UNAVAILABLE
    assert "provider connection payload" not in execution.assistant_message.content
    assert "stopped unexpectedly" in execution.assistant_message.content
    assert "No completed action was hidden" in execution.assistant_message.content
    run = (await db_session.execute(select(HarnessAgentRunRow))).scalar_one()
    assert run.status == "failed"
    assert run.stop_reason == AgentStopReason.PLANNER_UNAVAILABLE.value


class _StreamingAnswerModel(FakeMessagesListChatModel):
    calls: int = 0

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001, ANN201
        del tools, tool_choice, kwargs
        return self

    async def _agenerate(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        self.calls += 1
        return await super()._agenerate(*args, **kwargs)


async def test_live_run_persists_events_and_terminal_reconnect_does_not_reinvoke_model(
    db_session, monkeypatch
) -> None:
    from src.config import settings

    user = User(username="live-run", email="live-run@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(title="Live", user_id=user.id)
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation, ["messages"])
    grounding = (
        '```nexus-grounding\n{"answer_status":"grounded","citations":[],"disclosures":[]}\n```'
    )
    model = _StreamingAnswerModel(
        responses=[AIMessage(content=f"Safe live answer.\n{grounding}")]
    )
    monkeypatch.setattr(settings, "agent_runtime_v2_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_canary_stage", "workflow_approvals")
    monkeypatch.setattr(settings, "agent_runtime_v2_effects_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_effect_users", [user.id])
    monkeypatch.setattr(settings, "agent_runtime_v2_live_streaming_enabled", True)
    monkeypatch.setattr(settings, "agent_runtime_v2_live_streaming_users", [user.id])
    monkeypatch.setattr(
        "src.harness.agent_chat_runtime.build_readonly_primary_chat_model",
        lambda _settings: model,
    )
    events = []

    async def listener(event):  # noqa: ANN001
        events.append(event)

    first = await execute_production_agent_chat_turn(
        db=db_session,
        qdrant=None,  # type: ignore[arg-type]
        conversation=conversation,
        query="Hello",
        images=None,
        client_turn_id="live-reconnect-turn",
        requested_project_id=None,
        user_id=user.id,
        user_groups=[],
        project_scope=None,
        permissions=None,
        access_token=None,
        transport="stream",
        public_event_listener=listener,
    )
    first_events = list(events)
    events.clear()
    await db_session.refresh(conversation, ["messages"])
    replay = await execute_production_agent_chat_turn(
        db=db_session,
        qdrant=None,  # type: ignore[arg-type]
        conversation=conversation,
        query="Hello",
        images=None,
        client_turn_id="live-reconnect-turn",
        requested_project_id=None,
        user_id=user.id,
        user_groups=[],
        project_scope=None,
        permissions=None,
        access_token=None,
        transport="nonstream",
        public_event_listener=listener,
    )

    assert first.run_id == replay.run_id
    assert replay.replayed is True
    assert model.calls == 1
    assert [event.sequence for event in first_events] == list(range(1, len(first_events) + 1))
    # Live progress heartbeats are true only when emitted, so they are recorded
    # non-replayable: a reconnect must see every durable event and no stale
    # "Writing the answer... 4s" from minutes ago.
    assert any(event.type == "assistant.progress" for event in first_events)
    assert all(event.replayable is False for event in first_events if event.type == "assistant.progress")
    assert [event.event_id for event in events] == [
        event.event_id for event in first_events if event.replayable
    ]
    assert first_events[0].type == "run.started"
    assert first_events[-1].type == "run.completed"
    run = (await db_session.execute(select(HarnessAgentRunRow))).scalar_one()
    persisted = (
        await db_session.execute(
            select(HarnessAgentPublicEventRow).order_by(HarnessAgentPublicEventRow.sequence)
        )
    ).scalars().all()
    assert run.status == "completed"
    assert [row.sequence for row in persisted] == list(range(1, len(persisted) + 1))
