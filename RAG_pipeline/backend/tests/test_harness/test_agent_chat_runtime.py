from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import Conversation, ConversationMessage, QueryUsage, User
from src.db.models.harness import HarnessAgentTurnAuditRow
from src.harness.agent_chat_runtime import execute_production_agent_chat_turn
from src.harness.agent_chat_runtime import AgentChatIdempotencyConflict
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentDecision,
    AgentModelResult,
)


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
