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
    assert "google/gemini-2.5-flash" in ids
    assert "claude-cli" in ids
    gemini = next(m for m in data["models"] if m["id"] == "google/gemini-2.5-flash")
    claude_cli = next(m for m in data["models"] if m["id"] == "claude-cli")
    assert gemini["supports_analysis"] is True
    assert gemini["supports_writing"] is True
    assert "is_default_investigation" in gemini
    assert claude_cli["supports_writing"] is True


async def test_list_models_marks_effective_provider_default(client, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "doc_generation_provider", "openai/gpt-5.4")
    monkeypatch.setattr(config, "doc_generation_model", "anthropic/claude-sonnet-4-6")
    monkeypatch.setattr(config, "doc_generation_investigation_model", "google/gemini-2.5-flash")
    monkeypatch.setattr(config, "doc_generation_write_model", "google/gemini-2.5-flash")

    resp = await client.get("/mcp/models")
    assert resp.status_code == 200
    data = resp.json()

    openai_model = next(m for m in data["models"] if m["id"] == "openai/gpt-5.4")
    gemini = next(m for m in data["models"] if m["id"] == "google/gemini-2.5-flash")
    assert openai_model["is_default_analysis"] is True
    assert gemini["is_default_analysis"] is False
    assert gemini["is_default_investigation"] is True
    assert gemini["is_default_writing"] is True


async def test_invalid_platform_still_rejected(client):
    resp = await client.post("/mcp/add-repo", json={
        "url": "zip",
        "project_id": "badplatform",
        "platform": "jitterbit",
    })
    assert resp.status_code == 422
