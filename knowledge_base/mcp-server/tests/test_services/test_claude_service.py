"""Tests for ClaudeService."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.claude_cli import ClaudeCliProvider
from mcp_server.providers.custom.providers import ClaudeCliModelProvider
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


async def test_preprocess_generic_direct_redacted_source_mode(claude_svc, monkeypatch, tmp_path):
    from mcp_server.config import config

    project_id = "proj"
    scan_dir = tmp_path / "intodoc-output" / project_id
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "generic",
        "jobs": [{
            "name": "app",
            "path": "src/app.py",
            "group": "src",
            "components": [{
                "unique_name": "app",
                "component_type": "CodeFile",
                "params": [
                    {"name": "file_path", "value": "src/app.py"},
                    {"name": "content", "value": "TOKEN = EXAMPLE_VALUE\nprint('hello')\n"},
                ],
            }],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config, "source_context_mode", "direct_redacted")

    monkeypatch.setattr(
        "mcp_server.services.scanner_service.ScannerService.get_analysis_prompts",
        lambda self, platform: {"consolidated": True, "prompts": {"guide": "analysis guide"}},
    )
    monkeypatch.setattr(
        "mcp_server.services.scanner_service.ScannerService.get_phase3_guide",
        lambda self, platform: {"content": "phase 3 guide"},
    )

    groups = await claude_svc._preprocess_scan_data(project_id, tmp_path / "staging", platform="generic")

    assert groups == ["src"]
    bundle = (tmp_path / "staging" / "_preprocess" / "src.md").read_text(encoding="utf-8")
    assert "## File: src/app.py" in bundle
    assert "EXAMPLE_VALUE" not in bundle
    assert "[REDACTED]" in bundle
    assert (tmp_path / "staging" / "_preprocess" / "_source_coverage.json").exists()


async def test_generate_docs_cli_not_found(claude_svc, monkeypatch, tmp_path):
    from mcp_server.config import config
    monkeypatch.setattr(config, "claude_command", "nonexistent_claude_xyz")
    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    scan_dir = config.intodoc_output_dir / "proj"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "talend",
        "jobs": [{
            "name": "job_a",
            "group": "A",
            "path": "process/job_a.item",
            "components": [{"unique_name": "tMap_1", "component_type": "tMap"}],
        }],
        "files": [],
        "manifest": {},
        "warnings": [],
    }), encoding="utf-8")
    project = SimpleNamespace(platform="talend")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    def _fake_session_factory():
        return _FakeSession()

    with patch("mcp_server.db.session.async_session_factory", _fake_session_factory), \
         patch("mcp_server.services.project_registry.ProjectRegistry.get_project", return_value=project), \
         patch("mcp_server.providers.claude_cli.ensure_token_fresh", return_value=True):
        result = await claude_svc.generate_docs("proj", doc_generation_provider="claude-cli")

    assert result is False or result.get("success") is False


async def test_claude_cli_command_uses_resolved_cli_model(monkeypatch, tmp_path):
    from mcp_server.config import config

    captured: dict[str, list[str]] = {}

    class _FakeProc:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            self.pid = 12345
            self.stdin = StringIO()
            self.stdout = StringIO(json.dumps({
                "type": "result",
                "session_id": "test-session",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }) + "\n")
            self.stderr = StringIO()

        def wait(self):
            return 0

    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_preprocess" / "group.md").write_text("scan data", encoding="utf-8")

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["group"],
        total_jobs=1,
        total_groups=1,
        effective_model="sonnet",
        write_model="openai/deepseek-v4-pro",
        platform="generic",
        analysis_guide_file="_analysis_prompts.md",
    )

    async def _log_cb(project_id: str, message: str, level: str = "info") -> None:
        return None

    monkeypatch.setattr(config, "claude_command", "claude")
    monkeypatch.setattr("mcp_server.providers.claude_cli.ensure_token_fresh", lambda: True)
    monkeypatch.setattr("mcp_server.providers.claude_cli.shutil.which", lambda command: "/usr/bin/claude")
    monkeypatch.setattr("mcp_server.providers.claude_cli.subprocess.Popen", _FakeProc)

    result = await ClaudeCliProvider().run(ctx, _log_cb)

    assert result.success is True
    assert captured["cmd"][0:5] == ["/usr/bin/claude", "-p", "-", "--model", "sonnet"]
    assert "claude-cli" not in captured["cmd"]


async def test_claude_cli_model_provider_returns_text(monkeypatch):
    from mcp_server.config import config

    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "result": "generated docs",
                "usage": {"input_tokens": 12, "output_tokens": 3},
                "modelUsage": {"claude-sonnet-test": {}},
                "session_id": "session-1",
            }),
            stderr="",
        )

    monkeypatch.setattr(config, "claude_command", "claude")
    monkeypatch.setattr("mcp_server.providers.custom.providers.ensure_token_fresh", lambda: True)
    monkeypatch.setattr("mcp_server.providers.custom.providers.shutil.which", lambda command: "/usr/bin/claude")
    monkeypatch.setattr("mcp_server.providers.custom.providers.subprocess.run", _fake_run)

    result = await ClaudeCliModelProvider("sonnet").complete("Write docs")

    assert result.success is True
    assert result.text == "generated docs"
    assert result.model_name == "claude-sonnet-test"
    assert captured["cmd"][0:5] == ["/usr/bin/claude", "-p", "-", "--model", "sonnet"]
    assert "claude-cli" not in captured["cmd"]
    assert captured["input"] == "Write docs"
