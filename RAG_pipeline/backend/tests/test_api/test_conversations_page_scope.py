"""Page-scope contract on the three message-persisting conversation routes.

`page_scope` is client-supplied. The security property under test is that it is a
prompt hint and nothing else: it is validated, persisted with the turn, and never
translated into project authorization or a retrieval scope.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.sql.dml import Update

from src.api.main import app
from src.db.models import Conversation, ConversationMessage, Role, User
from src.harness.agent_chat_runtime import AgentChatTurnExecution
from src.harness.agent_loop import AgentBoundaryAction, AgentLoopResult, AgentStopReason
from src.harness.response_synthesis import render_typed_response

CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}

PAGE_SCOPE = {
    "surface": "topology",
    "project_id": "werkorder",
    "level": {"kind": "job", "jobName": "vw_load_werkorder", "sourceRoot": "src/main"},
}


def _mock_user(id: int = 1) -> MagicMock:
    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = id
    user.roles = [role]
    return user


def _mock_conversation(id: int = 1, user_id: int = 1) -> MagicMock:
    conv = MagicMock(spec=Conversation)
    conv.id = id
    conv.user_id = user_id
    conv.title = "Topology chat"
    conv.messages = []
    conv.origin_surface = "topology"
    conv.visible_in_chat = False
    conv.created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    conv.updated_at = conv.created_at
    return conv


def _mock_message(id: int, role: str, content: str) -> MagicMock:
    msg = MagicMock(spec=ConversationMessage)
    msg.id = id
    msg.role = role
    msg.content = content
    msg.sources = None
    msg.image_data = None
    msg.latency_ms = 5
    msg.status = "completed"
    msg.error_message = None
    msg.primary_source_layer = None
    msg.fallback_used = False
    msg.doc_gap = False
    msg.workflow_run_id = None
    msg.turn_plan = None
    msg.structured_answer = None
    msg.page_scope = None
    msg.created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    return msg


def _mock_execution() -> AgentChatTurnExecution:
    typed = render_typed_response(
        action="answer",
        content="tPostgresqlInput_1 feeds it.",
        plan=None,
        facts=[],
        state_version="page-scope-state",
        stop_reason="answered",
    )
    assistant = _mock_message(89, "assistant", typed.content)
    assistant.structured_answer = typed.model_dump(mode="json")
    return AgentChatTurnExecution(
        user_message=_mock_message(88, "user", "what feeds this?"),
        assistant_message=assistant,
        result=AgentLoopResult(
            action=AgentBoundaryAction.ANSWER,
            output=typed.content,
            stop_reason=AgentStopReason.ANSWERED,
            state_version="page-scope-state",
            typed_response=typed,
        ),
        latency_ms=5,
    )


@pytest.fixture
def mock_deps():
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    mock_db = AsyncMock()
    user = _mock_user()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    yield {"db": mock_db, "user": user}
    app.dependency_overrides.clear()


def _bind_conversation(mock_db: AsyncMock, conv: MagicMock) -> list:
    """Return every statement passed to db.execute while answering with `conv`."""
    executed: list = []
    result = MagicMock()
    result.scalar_one_or_none.return_value = conv

    async def _execute(statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        executed.append(statement)
        return result

    mock_db.execute = AsyncMock(side_effect=_execute)
    return executed


def _page_scope_updates(executed: list) -> list[dict]:
    """Extract the persisted page_scope values from captured UPDATE statements."""
    values: list[dict] = []
    for statement in executed:
        if isinstance(statement, Update):
            params = statement.compile().params
            if "page_scope" in params:
                values.append(params["page_scope"])
    return values


class TestPageScopeAcceptedAndPersisted:
    @pytest.mark.parametrize("suffix", ["messages", "messages/stream"])
    async def test_agent_routes_persist_the_scope_on_the_turn(self, mock_deps, suffix):
        executed = _bind_conversation(mock_deps["db"], _mock_conversation(id=7))

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=_mock_execution(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/v1/conversations/7/{suffix}",
                    json={"query": "what feeds this?", "page_scope": PAGE_SCOPE},
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 200
        assert _page_scope_updates(executed) == [PAGE_SCOPE]

    @pytest.mark.parametrize("suffix", ["messages", "messages/stream"])
    async def test_no_page_scope_writes_nothing(self, mock_deps, suffix):
        executed = _bind_conversation(mock_deps["db"], _mock_conversation(id=7))

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=_mock_execution(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/v1/conversations/7/{suffix}",
                    json={"query": "what feeds this?"},
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 200
        assert _page_scope_updates(executed) == []

    async def test_save_route_persists_the_scope_on_the_user_message(self, db_session):
        """The save path builds its own rows, so this one is asserted against a real DB."""
        from src.api.dependencies import get_current_user, get_db
        from src.auth.security import hash_password

        user = User(
            username="page-scope-user",
            email="page-scope@example.com",
            password_hash=hash_password("pw"),
            is_superadmin=True,
        )
        db_session.add(user)
        await db_session.flush()
        conversation = Conversation(title=None, user_id=user.id)
        db_session.add(conversation)
        await db_session.commit()

        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/v1/conversations/{conversation.id}/messages/save",
                    json={
                        "user_message": "what feeds this?",
                        "assistant_message": "tPostgresqlInput_1 feeds it.",
                        "page_scope": PAGE_SCOPE,
                    },
                    headers=CSRF_HEADERS,
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        rows = (
            (
                await db_session.execute(
                    select(ConversationMessage).where(
                        ConversationMessage.conversation_id == conversation.id,
                        ConversationMessage.role == "user",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [row.page_scope for row in rows] == [PAGE_SCOPE]


class TestPageScopeValidation:
    @pytest.mark.parametrize(
        "page_scope",
        [
            {"surface": "topology", "project_id": "werkorder"},  # no level
            {"surface": "topology", "level": {"kind": "project"}},  # no project
            {"surface": "runtime", "project_id": "p", "level": {"kind": "project"}},
            {"surface": "topology", "project_id": "p", "level": {"kind": "galaxy"}},
            {"surface": "topology", "project_id": "p", "level": {"kind": "job"}},  # no name
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "job", "jobName": "j", "extra": "x"},
            },
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "job", "jobName": "x" * 501},
            },
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "integration", "selection": {"kind": "system"}},
            },
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "project"},
                "selected_entity": {"kind": "planet", "label": "Mars"},
            },
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "project"},
                "selected_entity": {"kind": "system"},
            },
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "project"},
                "active_detail_tab": "x" * 101,
            },
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "project"},
                "filters": [{"name": "kind", "value": "all"}] * 13,
            },
            {
                "surface": "topology",
                "project_id": "p",
                "level": {"kind": "project"},
                "filters": [{"name": "kind", "value": "x" * 201}],
            },
            {"surface": "topology", "project_id": "p", "level": {"kind": "project"}, "x": 1},
        ],
    )
    async def test_malformed_scope_is_rejected_before_the_agent_runs(
        self, mock_deps, page_scope
    ):
        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/7/messages",
                    json={"query": "what feeds this?", "page_scope": page_scope},
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 422
        agent.assert_not_awaited()

    @pytest.mark.parametrize(
        "level",
        [
            {"kind": "project"},
            {"kind": "integration", "selection": {"kind": "application", "applicationId": "a1"}},
            {"kind": "integration", "selection": {"kind": "system", "systemId": "s1"}},
            {"kind": "integration", "selection": {"kind": "edge", "edgeKey": "e1"}},
            {
                "kind": "integration",
                "selection": {
                    "kind": "relationship",
                    "source": "a",
                    "target": "b",
                    "streamKeys": ["s1", "s2"],
                },
            },
            {"kind": "group", "groupName": "werkorder"},
            {"kind": "job", "jobName": "vw_load", "sourceRoot": "src/main"},
            {"kind": "component", "jobName": "vw_load", "componentName": "tMap_1"},
        ],
    )
    async def test_every_frontend_level_variant_is_accepted_and_persisted_verbatim(
        self, mock_deps, level
    ):
        """The persisted shape must round-trip unchanged: the topology tools emit the
        same union back as navigation targets, so a renamed field navigates nowhere."""
        executed = _bind_conversation(mock_deps["db"], _mock_conversation(id=7))
        scope = {"surface": "topology", "project_id": "werkorder", "level": level}

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=_mock_execution(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/7/messages",
                    json={"query": "what feeds this?", "page_scope": scope},
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 200
        assert _page_scope_updates(executed) == [scope]

    async def test_rich_context_is_accepted_and_persisted_verbatim(self, mock_deps):
        executed = _bind_conversation(mock_deps["db"], _mock_conversation(id=7))
        scope = {
            **PAGE_SCOPE,
            "selected_entity": {
                "kind": "column",
                "label": "WorkOrder.Status",
                "entity_id": "dblogs::Status",
            },
            "active_detail_tab": "mapping",
            "filters": [
                {"name": "connection visibility", "value": "main flows"},
                {"name": "artifact kind", "value": "jobs"},
            ],
        }

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=_mock_execution(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/7/messages",
                    json={"query": "what maps this?", "page_scope": scope},
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 200
        assert _page_scope_updates(executed) == [scope]

    async def test_old_payload_does_not_gain_default_rich_context_fields(self, mock_deps):
        executed = _bind_conversation(mock_deps["db"], _mock_conversation(id=7))

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=_mock_execution(),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/7/messages",
                    json={"query": "what feeds this?", "page_scope": PAGE_SCOPE},
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 200
        assert _page_scope_updates(executed) == [PAGE_SCOPE]


class TestPageScopeGrantsNoAccess:
    async def test_scope_naming_an_inaccessible_project_does_not_widen_the_scope(
        self, mock_deps
    ):
        """A page_scope is a hint: it must not add its project to the RBAC scope, and
        must not be substituted for the authorized request.project_id."""
        _bind_conversation(mock_deps["db"], _mock_conversation(id=7))
        scope = {
            "surface": "topology",
            "project_id": "secret-project",
            "level": {"kind": "project"},
        }

        with (
            patch(
                "src.api.routes.conversations.get_user_project_ids",
                new_callable=AsyncMock,
                return_value=["allowed-project"],
            ),
            patch(
                "src.api.routes.conversations.execute_production_agent_chat_turn",
                new_callable=AsyncMock,
                return_value=_mock_execution(),
            ) as agent,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/7/messages",
                    json={"query": "what feeds this?", "page_scope": scope},
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 200
        kwargs = agent.await_args.kwargs
        assert kwargs["project_scope"] == ["allowed-project"]
        assert kwargs["requested_project_id"] is None

    async def test_scope_cannot_rescue_a_forbidden_project_id(self, mock_deps):
        _bind_conversation(mock_deps["db"], _mock_conversation(id=7))
        scope = {
            "surface": "topology",
            "project_id": "secret-project",
            "level": {"kind": "project"},
        }

        with (
            patch(
                "src.api.routes.conversations.get_user_project_ids",
                new_callable=AsyncMock,
                return_value=["allowed-project"],
            ),
            patch(
                "src.api.routes.conversations.execute_production_agent_chat_turn",
                new_callable=AsyncMock,
            ) as agent,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/conversations/7/messages",
                    json={
                        "query": "what feeds this?",
                        "project_id": "secret-project",
                        "page_scope": scope,
                    },
                    headers=CSRF_HEADERS,
                )

        assert response.status_code == 403
        agent.assert_not_awaited()


class TestAccessTokenForwarding:
    @pytest.mark.parametrize("suffix", ["messages", "messages/stream"])
    async def test_cookie_token_reaches_the_turn_runtime(self, mock_deps, suffix):
        """The token comes from authenticated request state (the cookie), never the
        body; if the forward is dropped the topology dispatcher loses its MCP identity."""
        _bind_conversation(mock_deps["db"], _mock_conversation(id=7))

        with patch(
            "src.api.routes.conversations.execute_production_agent_chat_turn",
            new_callable=AsyncMock,
            return_value=_mock_execution(),
        ) as agent:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/v1/conversations/7/{suffix}",
                    json={"query": "what feeds this?", "page_scope": PAGE_SCOPE},
                    headers={
                        **CSRF_HEADERS,
                        "cookie": "csrf_token=test_csrf; access_token=tok-abc",
                    },
                )

        assert response.status_code == 200
        assert agent.await_args.kwargs["access_token"] == "tok-abc"
