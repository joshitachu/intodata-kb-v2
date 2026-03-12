"""Tests for the health endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def health_client(monkeypatch, tmp_path):
    """Minimal client that doesn't need DB -- just tests /health."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    (tmp_path / "data" / "logs").mkdir(parents=True)

    from mcp_server.api.app import app
    from mcp_server.services.log_manager import LogManager

    app.state.log_manager = LogManager()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health_returns_ok(health_client):
    resp = await health_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "intodoc-mcp-server"
