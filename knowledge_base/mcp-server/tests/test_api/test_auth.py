"""Tests for authentication enforcement."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def unauth_client(session_factory, monkeypatch, tmp_path):
    """Client WITHOUT auth override -- tests that endpoints reject unauthenticated requests."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config, "project_root", tmp_path / "project_root")
    (tmp_path / "data" / "logs").mkdir(parents=True)

    import mcp_server.api.routes.projects as projects_mod
    import mcp_server.api.routes.webhook as webhook_mod
    import mcp_server.api.permissions as permissions_mod
    import mcp_server.db.session as session_mod

    monkeypatch.setattr(session_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(projects_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(webhook_mod, "async_session_factory", session_factory)
    monkeypatch.setattr(permissions_mod, "async_session_factory", session_factory)

    from mcp_server.api.app import app
    from mcp_server.services.log_manager import LogManager

    # Clear any existing overrides
    app.dependency_overrides.clear()
    app.state.log_manager = LogManager()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_unauthenticated_request_rejected(unauth_client):
    """Security: requests without JWT cookie must get 401."""
    resp = await unauth_client.get("/mcp/projects")
    assert resp.status_code == 401


async def test_health_does_not_require_auth(unauth_client):
    """Health endpoint should always be accessible."""
    resp = await unauth_client.get("/health")
    assert resp.status_code == 200
