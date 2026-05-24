import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import User, Role, Conversation, ConversationMessage


def _mock_user(id: int = 1):
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = id
    user.roles = [role]
    return user


def _mock_conversation(id: int = 1, user_id: int = 1, title: str = "Test Chat"):
    conv = MagicMock(spec=Conversation)
    conv.id = id
    conv.user_id = user_id
    conv.title = title
    conv.messages = []
    conv.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    conv.updated_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    return conv


def _mock_message(id: int = 1, role: str = "user", content: str = "Hello"):
    msg = MagicMock(spec=ConversationMessage)
    msg.id = id
    msg.role = role
    msg.content = content
    msg.sources = None
    msg.image_data = None
    msg.latency_ms = None
    msg.status = "completed"
    msg.error_message = None
    msg.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    return msg


@pytest.fixture
def mock_deps():
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    mock_qdrant = AsyncMock()
    user = _mock_user()

    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: mock_qdrant
    app.dependency_overrides[get_current_user] = lambda: user

    yield {"db": mock_db, "qdrant": mock_qdrant, "user": user}
    app.dependency_overrides.clear()


class TestListConversations:
    async def test_list_empty(self, mock_deps):
        mock_db = mock_deps["db"]

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        select_result = MagicMock()
        select_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(side_effect=[count_result, select_result])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/conversations")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["conversations"] == []

    async def test_list_with_conversations(self, mock_deps):
        mock_db = mock_deps["db"]

        count_result = MagicMock()
        count_result.scalar_one.return_value = 2

        conv1 = _mock_conversation(id=1, title="First")
        conv2 = _mock_conversation(id=2, title="Second")
        conv1.messages = [_mock_message()]
        conv2.messages = [_mock_message(), _mock_message(id=2)]

        select_result = MagicMock()
        select_result.scalars.return_value.all.return_value = [conv1, conv2]

        mock_db.execute = AsyncMock(side_effect=[count_result, select_result])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/conversations")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["conversations"]) == 2
        assert data["conversations"][0]["message_count"] == 1
        assert data["conversations"][1]["message_count"] == 2


class TestCreateConversation:
    async def test_create_with_title(self, mock_deps):
        mock_db = mock_deps["db"]
        created_conversation = None

        def capture_add(obj):
            nonlocal created_conversation
            if isinstance(obj, Conversation) or hasattr(obj, 'user_id'):
                created_conversation = obj
                obj.id = 1
                obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
                obj.updated_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        mock_db.add = capture_add
        mock_db.flush = AsyncMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/conversations",
                json={"title": "New Chat"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["message_count"] == 0

    async def test_create_without_title(self, mock_deps):
        mock_db = mock_deps["db"]

        def capture_add(obj):
            if hasattr(obj, 'user_id'):
                obj.id = 1
                obj.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
                obj.updated_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        mock_db.add = capture_add
        mock_db.flush = AsyncMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/conversations")

        assert resp.status_code == 201


class TestGetConversation:
    async def test_get_success(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=1)
        msg1 = _mock_message(id=1, role="user", content="Question")
        msg2 = _mock_message(id=2, role="assistant", content="Answer")
        conv.messages = [msg1, msg2]

        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/conversations/1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert len(data["messages"]) == 2

    async def test_get_not_found(self, mock_deps):
        mock_db = mock_deps["db"]

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/conversations/999")

        assert resp.status_code == 404

    async def test_get_forbidden(self, mock_deps):
        mock_db = mock_deps["db"]

        # Conversation belongs to different user
        conv = _mock_conversation(id=1, user_id=999)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/conversations/1")

        assert resp.status_code == 403


class TestUpdateConversation:
    async def test_update_title(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=1, title="Old Title")
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/conversations/1",
                json={"title": "New Title"},
            )

        assert resp.status_code == 200
        assert conv.title == "New Title"

    async def test_update_not_found(self, mock_deps):
        mock_db = mock_deps["db"]

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                "/api/v1/conversations/999",
                json={"title": "New"},
            )

        assert resp.status_code == 404


class TestDeleteConversation:
    async def test_delete_success(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)
        mock_db.delete = AsyncMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/conversations/1")

        assert resp.status_code == 204

    async def test_delete_not_found(self, mock_deps):
        mock_db = mock_deps["db"]

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/conversations/999")

        assert resp.status_code == 404

    async def test_delete_forbidden(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=999)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/conversations/1")

        assert resp.status_code == 403


class TestSendMessage:
    async def test_send_message_success(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=1)
        conv.messages = []
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        mock_response = MagicMock()
        mock_response.id = 1
        mock_response.role = "assistant"
        mock_response.content = "The answer is 42."
        mock_response.sources = None
        mock_response.latency_ms = 100
        mock_response.status = "completed"
        mock_response.error_message = None
        mock_response.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        with patch(
            "src.api.routes.conversations.run_conversational_rag",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages",
                    json={"query": "What is the meaning of life?"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "assistant"
        assert data["content"] == "The answer is 42."

    async def test_send_message_not_found(self, mock_deps):
        mock_db = mock_deps["db"]

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/conversations/999/messages",
                json={"query": "Hello"},
            )

        assert resp.status_code == 404

    async def test_send_message_forbidden(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=999)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/conversations/1/messages",
                json={"query": "Hello"},
            )

        assert resp.status_code == 403

    async def test_send_message_with_options(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        mock_response = MagicMock()
        mock_response.id = 1
        mock_response.role = "assistant"
        mock_response.content = "Answer"
        mock_response.sources = None
        mock_response.latency_ms = 50
        mock_response.status = "completed"
        mock_response.error_message = None
        mock_response.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        with patch(
            "src.api.routes.conversations.run_conversational_rag",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_rag:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages",
                    json={"query": "Question", "top_k": 10, "threshold": 0.8},
                )

        assert resp.status_code == 200
        # Verify the options were passed
        call_kwargs = mock_rag.call_args.kwargs
        assert call_kwargs["top_k"] == 10
        assert call_kwargs["threshold"] == 0.8


class TestSendMessageStream:
    async def test_stream_failure_persists_failed_assistant_message(self, mock_deps):
        mock_db = mock_deps["db"]
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        async def assign_failed_id():
            mock_db.add.call_args.args[0].id = 42

        mock_db.flush = AsyncMock(side_effect=assign_failed_id)

        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        async def failing_stream(**_kwargs):
            raise RuntimeError("Provider timed out")
            yield  # noqa: unreachable - keeps this an async generator

        with patch(
            "src.api.routes.conversations.run_conversational_rag_stream",
            side_effect=failing_stream,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages/stream",
                    json={"query": "Slow question"},
                    headers={"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"},
                )

        assert resp.status_code == 200
        assert "event: error" in resp.text
        assert "event: done" in resp.text
        assert '"message_id": 42' in resp.text
        mock_db.commit.assert_awaited_once()

        failed_msg = mock_db.add.call_args.args[0]
        assert isinstance(failed_msg, ConversationMessage)
        assert failed_msg.role == "assistant"
        assert failed_msg.status == "failed"
        assert failed_msg.error_message == "Chat response failed. Please try again."


# --- Soft delete tests (real SQLite DB via db_session) ---

async def test_soft_deleted_conversation_not_in_list(db_session):
    """A conversation with deleted_at set must not appear in GET /conversations."""
    from datetime import datetime, timezone

    from src.api.dependencies import get_current_user, get_db
    from src.auth.security import hash_password
    from src.db.models import User

    user = User(
        username="conv-softdel-user",
        email="conv-softdel@example.com",
        password_hash=hash_password("pw"),
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    live_conv = Conversation(title="Live", user_id=user.id)
    dead_conv = Conversation(
        title="Dead",
        user_id=user.id,
        deleted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([live_conv, dead_conv])
    await db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/conversations")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    titles = [c["title"] for c in resp.json()["conversations"]]
    assert "Live" in titles
    assert "Dead" not in titles
    assert resp.json()["total"] == 1


async def test_conversation_messages_soft_deleted_on_cascade(db_session):
    """Deleting a conversation via the API must also soft-delete its messages."""
    from sqlalchemy import select as sa_select

    from src.api.dependencies import get_current_user, get_db
    from src.auth.security import hash_password
    from src.db.models import User

    user = User(
        username="conv-cascade-user",
        email="conv-cascade@example.com",
        password_hash=hash_password("pw"),
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()

    conv = Conversation(title="To delete", user_id=user.id)
    db_session.add(conv)
    await db_session.flush()

    msg1 = ConversationMessage(role="user", content="hello", conversation_id=conv.id)
    msg2 = ConversationMessage(role="assistant", content="hi", conversation_id=conv.id)
    db_session.add_all([msg1, msg2])
    await db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/api/v1/conversations/{conv.id}")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 204

    # Conversation row preserved with deleted_at set
    assert conv.deleted_at is not None

    # Messages also soft-deleted
    result = await db_session.execute(
        sa_select(ConversationMessage).where(
            ConversationMessage.conversation_id == conv.id
        )
    )
    messages = result.scalars().all()
    assert all(m.deleted_at is not None for m in messages)
