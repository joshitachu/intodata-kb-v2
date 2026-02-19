import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import User, Conversation, ConversationMessage


class TestConversationModel:
    async def test_create_conversation(self, db_session: AsyncSession) -> None:
        user = User(username="chatuser", email="chat@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id, title="Test Chat")
        db_session.add(conversation)
        await db_session.flush()

        result = await db_session.execute(
            select(Conversation).where(Conversation.id == conversation.id)
        )
        fetched = result.scalar_one()
        assert fetched.title == "Test Chat"
        assert fetched.user_id == user.id
        assert fetched.created_at is not None

    async def test_conversation_optional_title(self, db_session: AsyncSession) -> None:
        user = User(username="notitle", email="notitle@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id)
        db_session.add(conversation)
        await db_session.flush()

        assert conversation.title is None

    async def test_user_has_conversations(self, db_session: AsyncSession) -> None:
        user = User(username="multiconv", email="multi@example.com")
        db_session.add(user)
        await db_session.flush()

        conv1 = Conversation(user_id=user.id, title="First")
        conv2 = Conversation(user_id=user.id, title="Second")
        db_session.add_all([conv1, conv2])
        await db_session.flush()

        result = await db_session.execute(
            select(User).where(User.id == user.id).options(selectinload(User.conversations))
        )
        fetched = result.scalar_one()
        assert len(fetched.conversations) == 2

    async def test_conversation_cascade_delete_on_user(self, db_session: AsyncSession) -> None:
        user = User(username="deleteuser", email="del@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id, title="Will be deleted")
        db_session.add(conversation)
        await db_session.flush()
        conv_id = conversation.id

        await db_session.delete(user)
        await db_session.flush()

        result = await db_session.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        assert result.scalar_one_or_none() is None


class TestConversationMessageModel:
    async def test_create_message(self, db_session: AsyncSession) -> None:
        user = User(username="msguser", email="msg@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id)
        db_session.add(conversation)
        await db_session.flush()

        message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content="Hello, what is our revenue?",
        )
        db_session.add(message)
        await db_session.flush()

        result = await db_session.execute(
            select(ConversationMessage).where(ConversationMessage.id == message.id)
        )
        fetched = result.scalar_one()
        assert fetched.role == "user"
        assert fetched.content == "Hello, what is our revenue?"
        assert fetched.sources is None
        assert fetched.latency_ms is None

    async def test_assistant_message_with_sources(self, db_session: AsyncSession) -> None:
        user = User(username="srcuser", email="src@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id)
        db_session.add(conversation)
        await db_session.flush()

        sources_data = [
            {"document_id": 1, "filename": "report.pdf", "chunk_index": 0, "score": 0.95, "text": "Revenue was $1M"}
        ]
        message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="Revenue was $1M in Q4.",
            sources=sources_data,
            latency_ms=450,
        )
        db_session.add(message)
        await db_session.flush()

        result = await db_session.execute(
            select(ConversationMessage).where(ConversationMessage.id == message.id)
        )
        fetched = result.scalar_one()
        assert fetched.role == "assistant"
        assert fetched.sources[0]["filename"] == "report.pdf"
        assert fetched.latency_ms == 450

    async def test_conversation_has_messages(self, db_session: AsyncSession) -> None:
        user = User(username="multimsg", email="multimsg@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id)
        db_session.add(conversation)
        await db_session.flush()

        msg1 = ConversationMessage(conversation_id=conversation.id, role="user", content="Question 1")
        msg2 = ConversationMessage(conversation_id=conversation.id, role="assistant", content="Answer 1")
        msg3 = ConversationMessage(conversation_id=conversation.id, role="user", content="Question 2")
        db_session.add_all([msg1, msg2, msg3])
        await db_session.flush()

        result = await db_session.execute(
            select(Conversation)
            .where(Conversation.id == conversation.id)
            .options(selectinload(Conversation.messages))
        )
        fetched = result.scalar_one()
        assert len(fetched.messages) == 3

    async def test_messages_cascade_delete_on_conversation(self, db_session: AsyncSession) -> None:
        user = User(username="cascdel", email="cascdel@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id)
        db_session.add(conversation)
        await db_session.flush()

        message = ConversationMessage(conversation_id=conversation.id, role="user", content="Test")
        db_session.add(message)
        await db_session.flush()
        msg_id = message.id

        await db_session.delete(conversation)
        await db_session.flush()

        result = await db_session.execute(
            select(ConversationMessage).where(ConversationMessage.id == msg_id)
        )
        assert result.scalar_one_or_none() is None

    async def test_messages_ordered_by_id(self, db_session: AsyncSession) -> None:
        user = User(username="orderuser", email="order@example.com")
        db_session.add(user)
        await db_session.flush()

        conversation = Conversation(user_id=user.id)
        db_session.add(conversation)
        await db_session.flush()

        msg1 = ConversationMessage(conversation_id=conversation.id, role="user", content="First")
        msg2 = ConversationMessage(conversation_id=conversation.id, role="assistant", content="Second")
        msg3 = ConversationMessage(conversation_id=conversation.id, role="user", content="Third")
        db_session.add_all([msg1, msg2, msg3])
        await db_session.flush()

        result = await db_session.execute(
            select(Conversation)
            .where(Conversation.id == conversation.id)
            .options(selectinload(Conversation.messages))
        )
        fetched = result.scalar_one()
        contents = [m.content for m in fetched.messages]
        assert contents == ["First", "Second", "Third"]
