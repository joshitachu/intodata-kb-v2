from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import Conversation, ConversationMessage, User
from src.harness.agent_runs import AgentRunConflict, AgentRunService

pytestmark = pytest.mark.asyncio


async def _identity(db_session, suffix: str = "one"):
    user = User(username=f"run-{suffix}", email=f"run-{suffix}@example.com")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Streaming")
    db_session.add(conversation)
    await db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id, role="user", content="Run safely"
    )
    db_session.add(message)
    await db_session.flush()
    return user, conversation, message


async def test_run_identity_is_idempotent_per_user_message(db_session) -> None:
    user, conversation, message = await _identity(db_session)
    service = AgentRunService(db_session)
    first, created = await service.start_or_load(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        user_message_id=message.id,
        runtime_cohort="v2_readonly_primary",
        transport="stream",
        budget={"max_model_calls": 8},
        objective_summary="Explain the project",
    )
    replay, replay_created = await service.start_or_load(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        user_message_id=message.id,
        runtime_cohort="v2_readonly_primary",
        transport="nonstream",
        budget={"max_model_calls": 8},
    )
    assert created and not replay_created
    assert replay.run_id == first.run_id
    assert replay.transport == "stream"


async def test_events_are_ordered_replayable_and_sanitized(db_session) -> None:
    user, conversation, message = await _identity(db_session, "events")
    service = AgentRunService(db_session)
    run, _ = await service.start_or_load(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        user_message_id=message.id,
        runtime_cohort="v2_effect_primary",
        transport="stream",
        budget={},
    )
    first = await service.append_public_event(
        run_id=run.run_id,
        event_type="run.started",
        public_payload={
            "status": "running",
            "objective": "Authorization: Bearer private-canary",
            "prompt": "PRIVATE_REASONING_CANARY",
        },
    )
    second = await service.append_public_event(
        run_id=run.run_id,
        event_type="tool.completed",
        public_payload={
            "tool_label": "Search documentation",
            "status": "succeeded",
            "summary": "Found safe evidence",
            "arguments": {"secret": "must-not-persist"},
            "evidence_refs": [{"evidence_id": "evidence:safe"}],
        },
    )
    replay = await service.replay(
        run_id=run.run_id,
        conversation_id=conversation.id,
        owner_user_id=user.id,
        after_sequence=1,
    )
    assert [first.sequence, second.sequence] == [1, 2]
    assert [item.sequence for item in replay] == [2]
    persisted = json.dumps([first.model_dump(mode="json"), second.model_dump(mode="json")])
    assert "private-canary" not in persisted
    assert "PRIVATE_REASONING_CANARY" not in persisted
    assert "must-not-persist" not in persisted
    assert "evidence:safe" in persisted


async def test_terminal_run_is_immutable_and_owner_scoped(db_session) -> None:
    user, conversation, message = await _identity(db_session, "terminal")
    other, _, _ = await _identity(db_session, "other")
    service = AgentRunService(db_session)
    run, _ = await service.start_or_load(
        conversation_id=conversation.id,
        owner_user_id=user.id,
        user_message_id=message.id,
        runtime_cohort="v2_readonly_primary",
        transport="stream",
        budget={},
    )
    await service.finish(
        run_id=run.run_id,
        status="completed",
        stop_reason="answered",
        terminal_payload={"status": "completed", "stop_reason": "answered"},
    )
    with pytest.raises(AgentRunConflict, match="immutable"):
        await service.append_public_event(
            run_id=run.run_id,
            event_type="assistant.delta",
            public_payload={"content": "late"},
        )
    assert await service.replay(
        run_id=run.run_id,
        conversation_id=conversation.id,
        owner_user_id=other.id,
    ) == []


async def test_concurrent_event_sequence_allocation_is_atomic(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as setup:
        user = User(username="run-concurrent", email="run-concurrent@example.com")
        setup.add(user)
        await setup.flush()
        conversation = Conversation(user_id=user.id, title="Concurrent")
        setup.add(conversation)
        await setup.flush()
        message = ConversationMessage(
            conversation_id=conversation.id, role="user", content="Run"
        )
        setup.add(message)
        await setup.flush()
        run, _ = await AgentRunService(setup).start_or_load(
            conversation_id=conversation.id,
            owner_user_id=user.id,
            user_message_id=message.id,
            runtime_cohort="v2_readonly_primary",
            transport="stream",
            budget={},
        )
        ids = (run.run_id, conversation.id, user.id)

    async def append(label: str):
        async with sessions() as session:
            return await AgentRunService(session).append_public_event(
                run_id=ids[0],
                event_type="runtime.update",
                public_payload={"status": "running", "phase": label},
            )

    import asyncio

    event_count = 32
    events = await asyncio.gather(*(append(str(index)) for index in range(event_count)))
    assert sorted(event.sequence for event in events) == list(range(1, event_count + 1))
    async with sessions() as verification:
        replay = await AgentRunService(verification).replay(
            run_id=ids[0],
            conversation_id=ids[1],
            owner_user_id=ids[2],
        )
        assert [event.sequence for event in replay] == list(range(1, event_count + 1))
    await engine.dispose()
