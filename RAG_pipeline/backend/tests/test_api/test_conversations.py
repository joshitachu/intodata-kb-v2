import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import User, Role, Conversation, ConversationMessage
from src.harness.agent_chat_runtime import (
    AgentChatIdempotencyConflict,
    AgentChatTurnExecution,
)
from src.harness.agent_loop import (
    AgentBoundaryAction,
    AgentLoopResult,
    AgentStopReason,
)
from src.harness.response_synthesis import render_typed_response
from src.harness.workflow_projection import WorkflowCard

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


def _mock_agent_execution(content: str = "Handled by the authoritative agent"):
    typed = render_typed_response(
        action="answer",
        content=content,
        plan=None,
        facts=[],
        state_version="agent-route-state",
        stop_reason="answered",
    )
    result = AgentLoopResult(
        action=AgentBoundaryAction.ANSWER,
        output=typed.content,
        stop_reason=AgentStopReason.ANSWERED,
        state_version="agent-route-state",
        typed_response=typed,
    )
    user_message = _mock_message(id=88, role="user", content="request")
    assistant = _mock_message(id=89, role="assistant", content=typed.content)
    assistant.latency_ms = 12
    assistant.structured_answer = typed.model_dump(mode="json")
    return AgentChatTurnExecution(
        user_message=user_message,
        assistant_message=assistant,
        result=result,
        latency_ms=12,
    )


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
        assert data["messages"][1]["structured_answer"] is None
        assert data["messages"][1]["content"] == "Answer"

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

    async def test_typed_response_history_is_value_exact_and_malformed_payload_fails_closed(
        self, mock_deps
    ):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=1, user_id=1)
        valid = _mock_message(id=2, role="assistant", content="A direct answer.")
        typed = render_typed_response(
            action="answer",
            content="A direct answer.",
            plan=None,
            facts=[],
            state_version="history",
            stop_reason="answered",
        ).model_dump(mode="json")
        valid.structured_answer = typed
        malformed = _mock_message(id=3, role="assistant", content="Stored fallback text.")
        malformed.structured_answer = {
            "response_type": "agent_response_v1",
            "kind": "status",
            "content": "corrupt",
        }
        conv.messages = [valid, malformed]
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/conversations/1")

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert messages[0]["structured_answer"] == typed
        assert messages[1]["structured_answer"] is None
        assert messages[1]["content"] == "Stored fallback text."

    async def test_history_reprojects_all_stored_typed_card_ids_without_legacy_run_id(
        self, mock_deps
    ):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=1, user_id=1)
        message = _mock_message(id=2, role="assistant", content="Two workflows changed.")

        def workflow_card(run_id: int, *, status: str, display_label: str) -> WorkflowCard:
            terminal = status == "failed"
            return WorkflowCard(
                workflow_run_id=run_id,
                project_id=f"project-{run_id}",
                capability_id="mcp.docs.generate_project_documentation",
                label=f"Documentation generation {run_id}",
                status=status,
                display_label=display_label,
                display_tone="danger" if terminal else "progress",
                is_active=not terminal,
                error_message="Provider child failed." if terminal else None,
                started_at="2026-07-19T01:00:00Z",
                finished_at="2026-07-19T01:01:00Z" if terminal else None,
                projected_at="2026-07-19T01:02:00Z",
                elapsed_ms=60_000,
                timing_complete=True,
            )

        stored_cards = [
            workflow_card(44, status="in_progress", display_label="Stale running"),
            workflow_card(45, status="in_progress", display_label="Stale running"),
        ]
        fresh_cards = [
            workflow_card(44, status="failed", display_label="Already failed"),
            workflow_card(45, status="in_progress", display_label="In progress"),
        ]
        typed = render_typed_response(
            action="answer",
            content=None,
            plan=None,
            facts=[],
            workflow_cards=stored_cards,
            state_version="history-cards",
            stop_reason="answered",
        ).model_dump(mode="json")
        message.content = typed["content"]
        message.structured_answer = typed
        assert message.workflow_run_id is None
        conv.messages = [message]
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        with patch(
            "src.api.routes.conversations.AuthoritativeWorkflowCardProjector.project_many",
            new_callable=AsyncMock,
            return_value=fresh_cards,
        ) as project_many:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/v1/conversations/1")

        assert response.status_code == 200
        payload = response.json()["messages"][0]
        assert payload["structured_answer"] == typed
        assert [card["display_label"] for card in payload["workflow_cards"]] == [
            "Already failed",
            "In progress",
        ]
        assert project_many.await_args.args[0] == [44, 45]
        assert project_many.await_args.kwargs["reconcile"] is True
        mock_db.commit.assert_awaited()


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
    @pytest.mark.parametrize(
        "query",
        [
            "which project is currently having documents made",
            "help me document this project",
            "make a security scanning pack",
            "onboard project IntodataStreams",
            "cancel IntodataStreams",
            "CHO-frojefe",
        ],
    )
    async def test_agent_route_handles_authenticated_paraphrases_without_legacy_dispatch(
        self, mock_deps, query
    ):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=166, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)
        execution = _mock_agent_execution()

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/166/messages",
                    json={
                        "query": query,
                        "client_turn_id": "route-paraphrase-1",
                    },
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 200
        assert response.json()["structured_answer"] == execution.result.typed_response.model_dump(
            mode="json"
        )
        assert agent.await_args.kwargs["query"] == query
        assert agent.await_args.kwargs["client_turn_id"] == "route-paraphrase-1"

    async def test_agent_stream_and_nonstream_project_the_same_typed_result(
        self, mock_deps
    ):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=163, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)
        execution = _mock_agent_execution("One exact typed response")
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                nonstream = await client.post(
                    "/api/v1/conversations/163/messages",
                    json={"query": "status", "client_turn_id": "transport-one-1"},
                    headers=CSRF_HEADERS,
                )
                stream = await client.post(
                    "/api/v1/conversations/163/messages/stream",
                    json={"query": "status", "client_turn_id": "transport-two-1"},
                    headers=CSRF_HEADERS,
                )

        assert nonstream.status_code == stream.status_code == 200
        response_data = next(
            json.loads(line.removeprefix("data: "))
            for previous, line in zip(stream.text.splitlines(), stream.text.splitlines()[1:])
            if previous == "event: agent_response" and line.startswith("data: ")
        )
        assert response_data == nonstream.json()["structured_answer"]
        assert [call.kwargs["transport"] for call in agent.await_args_list] == [
            "nonstream",
            "stream",
        ]

    async def test_more_than_four_images_is_rejected_before_route_execution(
        self, mock_deps
    ):
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/166/messages",
                    json={"query": "inspect", "images": ["a", "b", "c", "d", "e"]},
                    headers=CSRF_HEADERS,
                )
        assert response.status_code == 422
        agent.assert_not_awaited()

    @pytest.mark.parametrize("suffix", ["messages", "messages/stream"])
    async def test_client_turn_payload_mismatch_returns_409(
        self, mock_deps, suffix
    ):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=166, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            side_effect=AgentChatIdempotencyConflict(
                "client_turn_id is already bound to a different request"
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/v1/conversations/166/{suffix}",
                    json={"query": "different", "client_turn_id": "reused-turn-1"},
                    headers=CSRF_HEADERS,
                )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "idempotency_conflict"

    async def test_every_new_turn_uses_authoritative_agent_execution(self, mock_deps):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=166, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)
        execution = _mock_agent_execution("The authoritative agent handled the turn.")
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/166/messages",
                    json={"query": "cancel IntodataStreams"},
                    headers=CSRF_HEADERS,
                )
        assert response.status_code == 200
        assert response.json()["content"] == execution.result.typed_response.content
        agent.assert_awaited_once()

    async def test_agent_failure_never_falls_through_to_legacy_pipeline(self, mock_deps):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=166, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            side_effect=RuntimeError("agent dependency failed"),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/166/messages",
                    json={"query": "cancel IntodataStreams"},
                    headers=CSRF_HEADERS,
                )
        assert response.status_code == 500

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
        execution = _mock_agent_execution(response.content)

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                api_response = await client.post(
                    "/api/v1/conversations/163/messages",
                    json={"query": "which project is currently having documents made"},
                    headers=CSRF_HEADERS,
                )

        assert api_response.status_code == 200
        agent.assert_awaited_once()

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

        execution = _mock_agent_execution(response.content)
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                result = await client.post(
                    "/api/v1/conversations/1/messages",
                    json={"query": "/docs customer-api"},
                    headers=CSRF_HEADERS,
                )

        assert result.status_code == 200
        assert agent.await_args.kwargs["query"] == "/docs customer-api"

    async def test_send_message_success(self, mock_deps):
        mock_db = mock_deps["db"]

        conv = _mock_conversation(id=1, user_id=1)
        conv.messages = []
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        execution = _mock_agent_execution("The answer is 42.")
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
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

        execution = _mock_agent_execution("Answer")
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages",
                    json={"query": "Question", "top_k": 10, "threshold": 0.8},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        agent.assert_awaited_once()


class TestSendMessageStream:
    async def test_stream_uses_only_unified_agent_transport_events(self, mock_deps):
        mock_db = mock_deps["db"]
        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        execution = _mock_agent_execution("Open the Pack draft from the artifact link.")
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ) as agent:
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
        assert events == ["agent_response", "agent_final", "done"]
        assert "workspace" not in events
        agent.assert_awaited_once()

    async def test_stream_agent_failure_fails_closed_before_transport(self, mock_deps):
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

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Provider timed out"),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages/stream",
                    json={"query": "Slow question"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 500
        mock_db.commit.assert_not_awaited()

    async def test_agent_final_is_metadata_only(self, mock_deps):
        mock_db = mock_deps["db"]
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()

        conv = _mock_conversation(id=1, user_id=1)
        result = MagicMock()
        result.scalar_one_or_none.return_value = conv
        mock_db.execute = AsyncMock(return_value=result)

        execution = _mock_agent_execution("The nightly_sync job failed.")
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=execution,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/conversations/1/messages/stream",
                    json={"query": "Did the nightly sync fail?"},
                    headers=CSRF_HEADERS,
                )

        assert resp.status_code == 200
        lines = resp.text.splitlines()
        final_data = next(
            json.loads(line.removeprefix("data: "))
            for previous, line in zip(lines, lines[1:])
            if previous == "event: agent_final"
        )
        assert "typed_response" not in final_data
        assert "output" not in final_data


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
