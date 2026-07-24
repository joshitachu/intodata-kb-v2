from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import get_current_user, get_db
from src.api.main import app
from src.db.models import User
from src.harness.agent_trace import AgentTraceDetail, AgentTraceList, AgentTraceSummary


def _user(*, audit: bool) -> MagicMock:
    user = MagicMock(spec=User)
    user.id = 1
    user.is_active = True
    user.is_superadmin = audit
    user.roles = []
    return user


@pytest.fixture
def trace_dependencies():
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: _user(audit=True)
    yield
    app.dependency_overrides.clear()


def _summary() -> AgentTraceSummary:
    now = datetime.datetime.now(datetime.UTC)
    return AgentTraceSummary(
        run_id="run-safe", conversation_id=42, owner_user_id=1,
        runtime_cohort="v2_effect_primary", transport="stream",
        status="completed", stop_reason="answered", objective_summary=None,
        model_calls=2, tool_calls=1, input_tokens=100, output_tokens=20,
        cost_usd=0.01, started_at=now, finished_at=now,
    )


@pytest.mark.asyncio
async def test_trace_list_requires_audit_permission() -> None:
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: _user(audit=False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/admin/agent-traces")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_trace_list_and_detail_are_operator_safe(trace_dependencies) -> None:
    summary = _summary()
    detail = AgentTraceDetail(
        **summary.model_dump(), manifest_snapshot_hash=None,
        budget={}, usage={}, timing={}, steps=[],
    )
    with (
        patch("src.api.routes.agent_traces.AgentTraceService.list_traces", new=AsyncMock(return_value=AgentTraceList(traces=[summary], total=1))),
        patch("src.api.routes.agent_traces.AgentTraceService.get_trace", new=AsyncMock(return_value=detail)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listing = await client.get("/api/v1/admin/agent-traces?status=completed")
            result = await client.get("/api/v1/admin/agent-traces/run-safe")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert result.status_code == 200
    serialized = result.text.casefold()
    assert "prompt" not in serialized
    assert "reasoning" not in serialized
