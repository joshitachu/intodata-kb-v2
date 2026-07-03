"""Tests for the MCP-side /mcp/admin/instance-settings endpoints."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import Request
from sqlalchemy import text


def _non_admin_auth(request: Request) -> dict:
    request.state.auth_payload = {"sub": "99", "type": "access", "username": "dev"}
    return request.state.auth_payload


class TestGet:
    async def test_get_requires_superadmin(self, client, session_factory):
        # Insert a non-superadmin user and override auth to act as them.
        async with session_factory() as s:
            await s.execute(
                text("INSERT INTO users (id, username, is_superadmin) VALUES (99, 'dev', 0)")
            )
            await s.commit()

        from mcp_server.api.app import app, require_auth
        previous = app.dependency_overrides.get(require_auth)
        app.dependency_overrides[require_auth] = _non_admin_auth
        try:
            resp = await client.get("/mcp/admin/instance-settings")
        finally:
            if previous is not None:
                app.dependency_overrides[require_auth] = previous

        assert resp.status_code == 403

    async def test_get_returns_empty_when_unset(self, client):
        resp = await client.get("/mcp/admin/instance-settings")
        assert resp.status_code == 200
        assert resp.json() == {"settings": {}}

    async def test_get_returns_existing_overrides(self, client, db_session):
        from mcp_server.db.models import InstanceSetting
        db_session.add(InstanceSetting(key="deep_dive_model", value="anthropic/claude-sonnet-4-6"))
        await db_session.commit()
        resp = await client.get("/mcp/admin/instance-settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["settings"]["deep_dive_model"] == "anthropic/claude-sonnet-4-6"


class TestPut:
    async def test_put_rejects_unknown_key(self, client):
        resp = await client.put(
            "/mcp/admin/instance-settings",
            json={"settings": {"not_a_real_key": "x"}},
        )
        assert resp.status_code == 400
        assert "Unknown setting key" in resp.text

    async def test_put_rejects_invalid_mcp_model_id(self, client):
        with patch(
            "mcp_server.api.routes.instance_settings._valid_mcp_model_ids",
            return_value={"anthropic/claude-sonnet-4-6", "google/gemini-2.5-flash"},
        ):
            resp = await client.put(
                "/mcp/admin/instance-settings",
                json={"settings": {"deep_dive_model": "bogus/model"}},
            )
        assert resp.status_code == 400
        assert "Unknown model id" in resp.text

    async def test_put_writes_and_audits(self, client, db_session):
        resp = await client.put(
            "/mcp/admin/instance-settings",
            json={"settings": {"deep_dive_model": "anthropic/claude-sonnet-4-6"}},
        )
        assert resp.status_code == 200
        assert resp.json()["settings"]["deep_dive_model"] == "anthropic/claude-sonnet-4-6"

        result = await db_session.execute(
            text("SELECT action, details FROM audit_logs WHERE action = 'update_instance_settings'")
        )
        rows = result.fetchall()
        assert rows, "expected an audit row"
        _action, details_raw = rows[-1]
        if isinstance(details_raw, str):
            details = json.loads(details_raw)
        elif isinstance(details_raw, (bytes, bytearray)):
            details = json.loads(details_raw.decode("utf-8"))
        else:
            details = details_raw
        assert "deep_dive_model" in details["diff"], f"details={details!r}"
        assert details["diff"]["deep_dive_model"]["after"] == "anthropic/claude-sonnet-4-6"

    async def test_put_empty_string_clears(self, client, db_session):
        from mcp_server.db.models import InstanceSetting
        db_session.add(InstanceSetting(key="deep_dive_model", value="anthropic/claude-sonnet-4-6"))
        await db_session.commit()
        resp = await client.put(
            "/mcp/admin/instance-settings",
            json={"settings": {"deep_dive_model": ""}},
        )
        assert resp.status_code == 200
        assert resp.json()["settings"] == {}

    async def test_put_rag_chat_model_accepts_runnable_local(self, client):
        # A local model is always runnable (no credentials needed).
        resp = await client.put(
            "/mcp/admin/instance-settings",
            json={"settings": {"rag_chat_model": "ollama/gemma3:4b"}},
        )
        assert resp.status_code == 200
        assert resp.json()["settings"]["rag_chat_model"] == "ollama/gemma3:4b"

    async def test_put_rag_chat_model_rejects_non_chat_model(self, client):
        # claude-cli is a doc-gen-only mechanism (rag_capable=False) → rejected.
        resp = await client.put(
            "/mcp/admin/instance-settings",
            json={"settings": {"rag_chat_model": "claude-cli"}},
        )
        assert resp.status_code == 400
        assert "not runnable" in resp.json()["detail"]

    async def test_put_rag_chat_model_rejects_missing_credentials(self, client, monkeypatch):
        # A cloud model with no credentials in the environment is not runnable.
        for var in ("ANTHROPIC_API_KEY",):
            monkeypatch.delenv(var, raising=False)
        resp = await client.put(
            "/mcp/admin/instance-settings",
            json={"settings": {"rag_chat_model": "anthropic/claude-sonnet-4-6"}},
        )
        assert resp.status_code == 400
        assert "not runnable" in resp.json()["detail"]

    async def test_put_accepts_rag_embed_key_verbatim(self, client):
        # Embeddings are out of scope for runnable-validation; stored verbatim.
        resp = await client.put(
            "/mcp/admin/instance-settings",
            json={"settings": {"rag_embed_model": "voyage-4-large"}},
        )
        assert resp.status_code == 200
        assert resp.json()["settings"]["rag_embed_model"] == "voyage-4-large"