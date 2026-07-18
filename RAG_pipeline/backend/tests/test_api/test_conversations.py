import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import User, Role, Conversation, ConversationMessage
from src.harness.repository import HarnessWorkflowRepository
from src.harness.providers.mcp_docs import McpDocumentationProvider
from src.harness.models import WorkflowRunStatus
from src.harness.chat import HarnessPlannerDecision

CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}


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
    msg.primary_source_layer = None
    msg.fallback_used = False
    msg.doc_gap = False
    msg.workflow_run_id = None
    msg.turn_plan = None
    msg.structured_answer = None
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

    # Most route tests use a single canned execute() result for the conversation.
    # Keep the independent Pack-draft lookup out of those legacy mocks; builder
    # routing has dedicated tests with a real database/service.
    with patch(
        "src.api.routes.conversations.PackDraftService.active_for_conversation",
        new_callable=AsyncMock,
        return_value=None,
    ):
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


class TestSlashCommands:
    async def test_lists_public_command_metadata(self, mock_deps):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/conversations/commands")

        assert response.status_code == 200
        commands = response.json()["commands"]
        assert [command["name"] for command in commands] == [
            "onboard", "docs", "investigate", "pack", "status", "capabilities",
        ]
        assert all("planner_template" not in command for command in commands)


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
                headers=CSRF_HEADERS,
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
            resp = await client.post("/api/v1/conversations", headers=CSRF_HEADERS)

        assert resp.status_code == 201


class TestGetConversation:
    async def test_get_success(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=1)
        msg1 = _mock_message(id=1, role="user", content="Question")
        msg2 = _mock_message(id=2, role="assistant", content="Answer")
        msg2.sources = [
            {
                "document_id": 1,
                "filename": "policy.pdf",
                "score": 0.8,
                "text": "Policy context",
                "reference_number": 1,
                "document_role": "policy",
            }
        ]
        msg2.structured_answer = {
            "output_contract": "component_inventory",
            "headline": "Component inventory",
            "summary": "One documented job.",
            "sections": [],
            "limitations": [],
            "citations": [],
            "evidence_refs": [],
            "answer_status": "complete",
            "execution_status": "completed",
            "domain_status": "unknown",
            "artifacts": [],
        }
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
        assert data["messages"][1]["sources"][0]["document_role"] == "policy"
        assert data["messages"][1]["structured_answer"]["headline"] == "Component inventory"

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
                headers=CSRF_HEADERS,
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
                headers=CSRF_HEADERS,
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
            resp = await client.delete("/api/v1/conversations/1", headers=CSRF_HEADERS)

        assert resp.status_code == 204

    async def test_delete_not_found(self, mock_deps):
        mock_db = mock_deps["db"]

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/conversations/999", headers=CSRF_HEADERS)

        assert resp.status_code == 404

    async def test_delete_forbidden(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=999)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/v1/conversations/1", headers=CSRF_HEADERS)

        assert resp.status_code == 403


class TestSendMessage:
    async def test_workflow_status_intent_uses_harness_public_route(self, mock_deps):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=163, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        response = MagicMock()
        response.id = 9
        response.role = "assistant"
        response.content = "Active project workflows: project-a is generating documentation."
        response.sources = None
        response.latency_ms = 10
        response.status = "completed"
        response.error_message = None
        response.primary_source_layer = None
        response.fallback_used = False
        response.doc_gap = False
        response.workflow_run_id = None
        response.harness_activities = []
        response.pending_action = None
        response.created_at = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
        decision = HarnessPlannerDecision(
            action="call_capability",
            mode="project_workflow_status",
            capability_id="mcp.projects.list_active_workflows",
            arguments={},
            decision_source="model",
        )

        with patch(
            "src.api.routes.conversations.plan_model_harness_chat_turn",
            new_callable=AsyncMock,
            return_value=decision,
        ), patch(
            "src.api.routes.conversations.run_harness_chat_turn",
            new_callable=AsyncMock,
            return_value=response,
        ) as harness, patch(
            "src.api.routes.conversations.run_conversational_rag",
            new_callable=AsyncMock,
        ) as rag:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                api_response = await client.post(
                    "/api/v1/conversations/163/messages",
                    json={"query": "which project is currently having documents made"},
                    headers=CSRF_HEADERS,
                )

        assert api_response.status_code == 200
        harness.assert_awaited_once()
        rag.assert_not_awaited()

    async def test_slash_command_is_resolved_before_the_normal_planner(self, mock_deps):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        response = MagicMock()
        response.id = 1
        response.role = "assistant"
        response.content = "Documentation started."
        response.sources = None
        response.latency_ms = 10
        response.status = "completed"
        response.error_message = None
        response.primary_source_layer = None
        response.fallback_used = False
        response.doc_gap = False
        response.workflow_run_id = None
        response.harness_activities = []
        response.pending_action = None
        response.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        decision = HarnessPlannerDecision(action="use_rag", mode="rag")
        with patch(
            "src.api.routes.conversations.plan_model_harness_chat_turn",
            new_callable=AsyncMock,
            return_value=decision,
        ) as planner, patch(
            "src.api.routes.conversations.run_conversational_rag",
            new_callable=AsyncMock,
            return_value=response,
        ) as rag:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                result = await client.post(
                    "/api/v1/conversations/1/messages",
                    json={"query": "/docs customer-api"},
                    headers=CSRF_HEADERS,
                )

        assert result.status_code == 200
        assert planner.await_args.kwargs["query"] == "Generate documentation customer-api"
        assert rag.await_args.kwargs["query"] == "Generate documentation customer-api"

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
        mock_response.primary_source_layer = None
        mock_response.fallback_used = False
        mock_response.doc_gap = False
        mock_response.workflow_run_id = None
        mock_response.harness_activities = []
        mock_response.pending_action = None
        mock_response.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        with patch(
            "src.api.routes.conversations.plan_model_harness_chat_turn",
            new_callable=AsyncMock,
            return_value=HarnessPlannerDecision(action="use_rag", mode="rag"),
        ), patch(
            "src.api.routes.conversations.run_conversational_rag",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages",
                    json={"query": "What is the meaning of life?"},
                    headers=CSRF_HEADERS,
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
                headers=CSRF_HEADERS,
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
                headers=CSRF_HEADERS,
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
        mock_response.primary_source_layer = None
        mock_response.fallback_used = False
        mock_response.doc_gap = False
        mock_response.workflow_run_id = None
        mock_response.harness_activities = []
        mock_response.pending_action = None
        mock_response.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

        with patch(
            "src.api.routes.conversations.plan_model_harness_chat_turn",
            new_callable=AsyncMock,
            return_value=HarnessPlannerDecision(action="use_rag", mode="rag"),
        ), patch(
            "src.api.routes.conversations.run_conversational_rag",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_rag:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages",
                    json={"query": "Question", "top_k": 10, "threshold": 0.8},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        # Verify the options were passed
        call_kwargs = mock_rag.call_args.kwargs
        assert call_kwargs["top_k"] == 10
        assert call_kwargs["threshold"] == 0.8


class TestSendMessageStream:
    async def test_pack_builder_stream_emits_workspace_hint_before_builder_turn(self, mock_deps):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        builder_response = SimpleNamespace(content="What analysis goal should this Pack serve?", id=17)
        with patch(
            "src.api.routes.conversations._run_pack_builder_turn",
            new_callable=AsyncMock,
            return_value=builder_response,
        ) as builder_turn:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages/stream",
                    json={"query": "I want to create a pack"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        events = [
            line.removeprefix("event: ")
            for line in resp.text.splitlines()
            if line.startswith("event: ")
        ]
        assert events == ["workspace", "token", "done"]
        assert 'data: {"workspace": "pack", "status": "running"}' in resp.text
        builder_turn.assert_awaited_once()

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
            "src.api.routes.conversations.plan_model_harness_chat_turn",
            new_callable=AsyncMock,
            return_value=HarnessPlannerDecision(action="use_rag", mode="rag"),
        ), patch(
            "src.api.routes.conversations.run_conversational_rag_stream",
            side_effect=failing_stream,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages/stream",
                    json={"query": "Slow question"},
                    headers=CSRF_HEADERS,
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

    async def test_stream_relays_runtime_tool_events_in_order(self, mock_deps):
        """Backend runtime tool-use loop events (EP-004 slice 3) are relayed
        as additive SSE events, ending with the existing usage/done pair."""
        mock_db = mock_deps["db"]
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        async def fake_stream(**_kwargs):
            yield ("sources", "[]")
            yield ("runtime_tool_call", '{"tool_name": "detect_failures", "arguments": {}}')
            yield ("runtime_tool_result", '{"tool_name": "detect_failures", "success": true}')
            yield ("token", '"The nightly_sync job failed."')
            yield ("usage", '{"input_tokens": 10, "output_tokens": 5}')
            yield ("done", '{"message_id": 99, "latency_ms": 120}')

        with patch(
            "src.api.routes.conversations.plan_model_harness_chat_turn",
            new_callable=AsyncMock,
            return_value=HarnessPlannerDecision(action="use_rag", mode="rag"),
        ), patch(
            "src.api.routes.conversations.run_conversational_rag_stream",
            side_effect=fake_stream,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages/stream",
                    json={"query": "Did the nightly sync fail?"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        events_in_order = [
            line.removeprefix("event: ")
            for line in resp.text.splitlines()
            if line.startswith("event: ")
        ]
        assert events_in_order == [
            "sources", "runtime_tool_call", "runtime_tool_result", "token", "usage", "done",
        ]


async def test_stream_docs_generation_runs_through_langgraph_and_persists_runtime_metadata(
    db_session,
    monkeypatch,
) -> None:
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    user = User(
        username="graph-stream-user",
        email="graph-stream@example.com",
        is_superadmin=True,
    )
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id, title="Graph stream")
    db_session.add(conversation)
    await db_session.flush()

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["generate_path"] = request.url.path
        seen["generate_payload"] = request.read()
        return httpx.Response(
            200,
            json={"status": "generation_triggered", "message": "docs started"},
        )

    provider = McpDocumentationProvider(
        base_url="http://mcp.internal/mcp",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr("src.harness.chat.McpDocumentationProvider", lambda: provider)
    monkeypatch.setattr(
        "src.api.routes.conversations.plan_model_harness_chat_turn",
        AsyncMock(
            return_value=HarnessPlannerDecision(
                action="call_capability",
                mode="generate_docs",
                capability_id="mcp.docs.generate_project_documentation",
                arguments={"project_id": "project-a"},
                decision_source="model",
            )
        ),
    )

    current_user = SimpleNamespace(
        id=user.id,
        username=user.username,
        email=user.email,
        is_superadmin=True,
        roles=[],
    )
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_qdrant] = lambda: AsyncMock()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/conversations/{conversation.id}/messages/stream",
                json={"query": "Generate docs for this project", "project_id": "project-a"},
                headers=CSRF_HEADERS,
            )
            assert response.status_code == 200
            assert "event: harness_activity" in response.text
            assert "Documentation generation started for `project-a`" in response.text
            assert seen["generate_path"] == "/mcp/generate"
            assert b'"project_id":"project-a"' in seen["generate_payload"]

            runs = await HarnessWorkflowRepository(db_session).list_runs(
                None,
                project_id="project-a",
            )
            assert len(runs) == 1
            run = runs[0]
            assert run.status == WorkflowRunStatus.TRIGGERED
            assert run.runtime_provider_id == "langgraph.nexus"
            assert run.runtime_thread_id == f"harness-run-{run.id}"
            assert run.runtime_run_id
            assert run.current_node == "finalize"
            assert run.runtime_metadata["graph"] == "docs_generation"
            assert run.runtime_metadata["checkpoint_id"]

            detail = await client.get(f"/api/v1/harness/workflows/{run.id}")
            body = detail.json()
            assert body["run"]["runtime_provider_id"] == "langgraph.nexus"
            assert body["run"]["runtime_metadata"]["graph"] == "docs_generation"
            assert any(
                activity["runtime_node"] == "execute_capability"
                for activity in body["activities"]
            )
    finally:
        app.dependency_overrides.clear()


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
            resp = await client.delete(f"/api/v1/conversations/{conv.id}", headers=CSRF_HEADERS)
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
