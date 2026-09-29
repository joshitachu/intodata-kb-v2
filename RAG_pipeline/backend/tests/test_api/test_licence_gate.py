"""Licence gate on the RAG backend: 402 before any LLM work, reads stay open.

Tokens are signed here with the golden-vector TEST key, and the policy is
swapped in with ``set_policy`` so nothing depends on a mounted licence file.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.db.models import Conversation, ConversationMessage, Role, User
from src.harness.agent_chat_runtime import AgentChatTurnExecution
from src.harness.agent_loop import AgentBoundaryAction, AgentLoopResult, AgentStopReason
from src.harness.response_synthesis import render_typed_response
from src.licensing.core import ALGORITHM, ISSUER, EntitlementPolicy
from src.licensing.runtime import get_policy, set_policy

CSRF_HEADERS = {"cookie": "csrf_token=test_csrf", "X-CSRF-Token": "test_csrf"}
STREAM_PATH = "/api/v1/conversations/1/messages/stream"
_VECTORS = json.loads(
    (Path(__file__).parents[1] / "test_licensing" / "golden_vectors.json").read_text()
)
_KID = "test-1"


class _StaticSource:
    def __init__(self, token: str | None) -> None:
        self._token = token

    def read_token(self) -> str | None:
        return self._token


def _token(*, start_offset_days: float, end_offset_days: float,
           features: list[str] | None = None) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "iss": ISSUER,
        "sub": _VECTORS["expected_subject"],
        "lid": "00000000-0000-0000-0000-00000000abcd",
        "plan": "basic",
        "features": features if features is not None else ["chat", "generation"],
        "limits": {"generations_per_period": 3},
        "nbf": int((now + datetime.timedelta(days=start_offset_days)).timestamp()),
        "exp": int((now + datetime.timedelta(days=end_offset_days)).timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode(claims, _VECTORS["test_private_key_pem"], algorithm=ALGORITHM,
                      headers={"kid": _KID})


def _install(token: str | None, *, enforce: bool = True) -> None:
    set_policy(EntitlementPolicy(
        _StaticSource(token),
        public_keys=_VECTORS["public_keys"],
        expected_subject=_VECTORS["expected_subject"],
        enforce=enforce,
    ))


def _expired() -> str:
    return _token(start_offset_days=-31, end_offset_days=-1)


def _valid() -> str:
    return _token(start_offset_days=-1, end_offset_days=29)


@pytest.fixture(autouse=True)
def _reset_policy():
    yield
    set_policy(None)


@pytest.fixture
def mock_deps():
    from src.api.dependencies import get_current_user, get_db, get_qdrant

    role = MagicMock(spec=Role)
    role.name = "engineering"
    user = MagicMock(spec=User)
    user.id = 1
    user.roles = [role]
    mock_db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_qdrant] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    yield {"db": mock_db, "user": user}
    app.dependency_overrides.clear()


def _conversation() -> MagicMock:
    conv = MagicMock(spec=Conversation)
    conv.id = 1
    conv.user_id = 1
    conv.title = "Chat"
    conv.messages = []
    conv.origin_surface = "chat"
    conv.visible_in_chat = True
    conv.lane = "v1"
    conv.created_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    conv.updated_at = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    return conv


def _message(id: int, role: str, content: str) -> MagicMock:
    msg = MagicMock(spec=ConversationMessage)
    msg.id = id
    msg.role = role
    msg.content = content
    msg.sources = None
    msg.image_data = None
    msg.latency_ms = 12
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


def _execution() -> AgentChatTurnExecution:
    typed = render_typed_response(action="answer", content="ok", plan=None, facts=[],
                                  state_version="s", stop_reason="answered")
    result = AgentLoopResult(action=AgentBoundaryAction.ANSWER, output=typed.content,
                             stop_reason=AgentStopReason.ANSWERED, state_version="s",
                             typed_response=typed)
    assistant = _message(89, "assistant", typed.content)
    assistant.structured_answer = typed.model_dump(mode="json")
    return AgentChatTurnExecution(user_message=_message(88, "user", "hi"),
                                  assistant_message=assistant, result=result, latency_ms=12)


async def _post_stream() -> tuple[object, AsyncMock]:
    with patch(
        "src.api.routes.conversations.execute_production_agent_chat_turn",
        new_callable=AsyncMock,
        return_value=_execution(),
    ) as agent:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(STREAM_PATH, json={"query": "Hello"}, headers=CSRF_HEADERS)
    return resp, agent


def _conversation_found(mock_db: AsyncMock) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = _conversation()
    mock_db.execute = AsyncMock(return_value=result)


class TestChatGate:
    async def test_expired_licence_refuses_stream_before_it_opens(self, mock_deps):
        _install(_expired())
        _conversation_found(mock_deps["db"])

        resp, agent = await _post_stream()

        assert resp.status_code == 402
        assert not resp.headers["content-type"].startswith("text/event-stream")
        detail = resp.json()["detail"]
        assert detail["code"] == "licence_expired"
        assert detail["capability"] == "chat"
        assert detail["message"]
        # The handler never ran: no conversation lookup, no agent turn.
        mock_deps["db"].execute.assert_not_awaited()
        agent.assert_not_awaited()

    async def test_missing_licence_refuses_with_licence_missing(self, mock_deps):
        _install(None)
        _conversation_found(mock_deps["db"])

        resp, agent = await _post_stream()

        assert resp.status_code == 402
        assert resp.json()["detail"]["code"] == "licence_missing"
        agent.assert_not_awaited()

    async def test_plan_without_chat_refuses_with_feature_not_in_plan(self, mock_deps):
        _install(_token(start_offset_days=-1, end_offset_days=29, features=["generation"]))
        _conversation_found(mock_deps["db"])

        resp, _agent = await _post_stream()

        assert resp.status_code == 402
        assert resp.json()["detail"]["code"] == "feature_not_in_plan"

    async def test_reads_stay_open_on_an_expired_licence(self, mock_deps):
        _install(_expired())
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        select_result = MagicMock()
        select_result.scalars.return_value.all.return_value = []
        mock_deps["db"].execute = AsyncMock(side_effect=[count_result, select_result])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/conversations")

        assert resp.status_code == 200

    async def test_valid_licence_streams(self, mock_deps):
        _install(_valid())
        _conversation_found(mock_deps["db"])

        resp, agent = await _post_stream()

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        agent.assert_awaited_once()

    async def test_enforcement_off_never_refuses(self, mock_deps):
        _install(_expired(), enforce=False)
        _conversation_found(mock_deps["db"])

        resp, agent = await _post_stream()

        assert resp.status_code == 200
        agent.assert_awaited_once()

    async def test_unauthenticated_caller_gets_401_not_402(self):
        # Authentication resolves before the licence, so an expired session is
        # still a logout (401) and never masked as a licence problem.
        _install(_expired())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(STREAM_PATH, json={"query": "Hello"}, headers=CSRF_HEADERS)

        assert resp.status_code == 401


class TestLabsMcpBridgeGate:
    async def test_bridge_checks_its_bearer_before_the_licence(self):
        _install(_expired())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/labs/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers=CSRF_HEADERS,
            )
        assert resp.status_code == 403

    async def test_bridge_refuses_a_valid_turn_token_on_an_expired_licence(self):
        from src.labs.contracts import labs_token_registry

        _install(_expired())
        scope = MagicMock()
        with patch.object(labs_token_registry, "resolve", return_value=scope):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/labs/mcp/turn-token",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "x", "arguments": {}}},
                    headers=CSRF_HEADERS,
                )
        assert resp.status_code == 402
        assert resp.json()["detail"]["code"] == "licence_expired"


class TestLicenceStatus:
    async def test_status_shape_for_a_valid_licence(self, mock_deps):
        _install(_valid())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/licence/status")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "state", "reason", "plan", "features", "limits", "period_start",
            "period_end", "days_remaining", "near_expiry", "enforcement", "next_licence",
        }
        assert body["next_licence"] is None  # no renewal queued
        assert body["state"] == "valid"
        assert body["enforcement"] == "enforce"
        assert body["plan"] == "basic"
        assert body["features"] == ["chat", "generation"]
        assert body["limits"] == {"generations_per_period": 3}
        assert body["days_remaining"] in (28, 29)
        assert body["near_expiry"] is False
        assert "token" not in json.dumps(body).lower()

    async def test_status_reports_a_queued_renewal(self, mock_deps):
        """A pushed renewal sits in the next slot; the banner and Fleet must see it."""
        set_policy(EntitlementPolicy(
            _StaticSource(_valid()),
            public_keys=_VECTORS["public_keys"],
            expected_subject=_VECTORS["expected_subject"],
            enforce=True,
            next_source=_StaticSource(_token(start_offset_days=29, end_offset_days=59)),
        ))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/licence/status")

        body = resp.json()
        assert body["state"] == "valid"
        assert body["next_licence"]["state"] == "not_yet_valid"
        assert "token" not in json.dumps(body).lower()

    async def test_status_is_readable_while_expired(self, mock_deps):
        _install(_expired())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/licence/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "expired"
        assert body["days_remaining"] == 0
        assert body["reason"]

    async def test_status_reports_enforcement_off_and_missing(self, mock_deps):
        _install(None, enforce=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/licence/status")

        body = resp.json()
        assert body["state"] == "missing"
        assert body["enforcement"] == "off"
        assert body["plan"] is None

    async def test_status_requires_authentication(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/licence/status")
        assert resp.status_code == 401


def test_set_policy_none_rebuilds_from_settings():
    _install(_valid())
    set_policy(None)
    from src.config import settings

    assert get_policy().enforce is (settings.licence_enforcement == "enforce")


class TestMcpDocsLicenceRefusal:
    """The MCP server answers 402 when a generation is refused under its licence."""

    @staticmethod
    def _provider(status: int, body: object):
        import httpx

        from src.harness.providers.mcp_docs import McpDocumentationProvider

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body)

        return McpDocumentationProvider(base_url="http://mcp.test",
                                        transport=httpx.MockTransport(handler))

    async def test_402_is_a_typed_refusal_with_the_mcp_detail(self):
        from src.harness.providers.mcp_docs import (
            McpDocumentationProviderError,
            McpLicenceRefusedError,
        )

        provider = self._provider(402, {"detail": {
            "code": "quota_exhausted",
            "message": "The plan's generation allowance for this period is used up.",
            "capability": "generation", "used": 3, "limit": 3,
            "period_end": "2026-10-31T00:00:00+00:00",
        }})
        with pytest.raises(McpLicenceRefusedError) as caught:
            await provider.trigger_generation(project_id="acme-proj")

        err = caught.value
        assert isinstance(err, McpDocumentationProviderError)
        assert err.code == "quota_exhausted"
        assert err.safe_message == "The plan's generation allowance for this period is used up."
        assert err.detail["used"] == 3 and err.detail["limit"] == 3
        assert "HTTP 402" not in err.safe_message

    async def test_402_on_other_mcp_calls_is_typed_too(self):
        from src.harness.providers.mcp_docs import McpLicenceRefusedError

        provider = self._provider(402, {"detail": {"code": "licence_expired",
                                                   "message": "expired"}})
        with pytest.raises(McpLicenceRefusedError) as caught:
            await provider.update_doc_from_evidence(
                project_id="acme-proj", filename="a.md", evidence_text="x")
        assert caught.value.code == "licence_expired"

    async def test_unparseable_402_still_yields_a_typed_refusal(self):
        import httpx

        from src.harness.providers.mcp_docs import McpDocumentationProvider, McpLicenceRefusedError

        provider = McpDocumentationProvider(
            base_url="http://mcp.test",
            transport=httpx.MockTransport(lambda _r: httpx.Response(402, text="nope")),
        )
        with pytest.raises(McpLicenceRefusedError) as caught:
            await provider.trigger_generation(project_id="acme-proj")
        assert caught.value.code == "licence_refused"
        assert caught.value.safe_message

    async def test_other_errors_keep_the_generic_provider_error(self):
        from src.harness.providers.mcp_docs import (
            McpDocumentationProviderError,
            McpLicenceRefusedError,
        )

        provider = self._provider(500, {"detail": "boom"})
        with pytest.raises(McpDocumentationProviderError) as caught:
            await provider.trigger_generation(project_id="acme-proj")
        assert not isinstance(caught.value, McpLicenceRefusedError)
        assert "HTTP 500" in caught.value.safe_message
