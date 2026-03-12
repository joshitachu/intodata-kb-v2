"""Tests for ClaudeService."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.services.claude_service import ClaudeService


@pytest.fixture
def claude_svc(log_manager):
    return ClaudeService(log_manager=log_manager)


def test_format_event_assistant_text():
    event = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Hello from Claude"}]
        },
    }
    result = ClaudeService._format_event(event)
    assert "Hello from Claude" in result


def test_format_event_tool_use():
    event = {
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use",
                "name": "read_file",
                "input": {"path": "/tmp/test.py"},
            }]
        },
    }
    result = ClaudeService._format_event(event)
    assert "[tool_call]" in result
    assert "read_file" in result


def test_format_event_result():
    event = {"type": "result", "result": "All done!"}
    result = ClaudeService._format_event(event)
    assert "[result]" in result
    assert "All done!" in result


def test_format_event_system():
    event = {"type": "system", "message": "Initializing..."}
    result = ClaudeService._format_event(event)
    assert "[system]" in result


def test_format_event_suppressed():
    event = {"type": "tool_result"}
    result = ClaudeService._format_event(event)
    assert result is None


async def test_generate_docs_cli_not_found(claude_svc, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "claude_command", "nonexistent_claude_xyz")
    monkeypatch.setattr(config, "project_root", tmp_path)

    with patch("mcp_server.services.claude_service.ensure_token_fresh", return_value=True):
        result = await claude_svc.generate_docs("proj")

    assert result is False or result.get("success") is False
