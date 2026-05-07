"""Tests for dashboard discovery endpoints."""

from __future__ import annotations


async def test_list_platforms(client):
    resp = await client.get("/mcp/platforms")
    assert resp.status_code == 200
    data = resp.json()
    ids = {p["id"] for p in data["platforms"]}
    assert ids == {"talend", "workato", "lobster", "generic"}
    talend = next(p for p in data["platforms"] if p["id"] == "talend")
    assert talend["label"] == "Talend"
    assert talend["unit_label_plural"] == "Jobs"


async def test_list_models(client):
    resp = await client.get("/mcp/models")
    assert resp.status_code == 200
    data = resp.json()
    ids = {m["id"] for m in data["models"]}
    assert "custom" not in ids
    assert "moonshot/kimi-k2.6" in ids
    assert "claude-cli" in ids
    kimi = next(m for m in data["models"] if m["id"] == "moonshot/kimi-k2.6")
    claude_cli = next(m for m in data["models"] if m["id"] == "claude-cli")
    assert kimi["supports_analysis"] is True
    assert kimi["supports_writing"] is True
    assert claude_cli["supports_writing"] is True


async def test_list_models_marks_effective_provider_default(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "doc_generation_provider", "openai/deepseek-v4-pro")
    monkeypatch.setattr(config, "doc_generation_model", "moonshot/kimi-k2.6")
    monkeypatch.setattr(config, "doc_generation_write_model", "moonshot/kimi-k2.6")

    resp = await client.get("/mcp/models")
    assert resp.status_code == 200
    data = resp.json()

    deepseek = next(m for m in data["models"] if m["id"] == "openai/deepseek-v4-pro")
    kimi = next(m for m in data["models"] if m["id"] == "moonshot/kimi-k2.6")
    assert deepseek["is_default_analysis"] is True
    assert kimi["is_default_analysis"] is False
    assert kimi["is_default_writing"] is True


async def test_invalid_platform_still_rejected(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "badplatform",
        "platform": "jitterbit",
    })
    assert resp.status_code == 422
