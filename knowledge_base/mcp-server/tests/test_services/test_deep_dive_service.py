from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.custom.models import ModelResult
from mcp_server.services.deep_dive_service import (
    AgentState,
    ClaudeCliDeepDiveEngine,
    DeepDiveService,
    RepoAgentEngine,
    RepoToolbox,
)


def _make_project(tmp_path, monkeypatch, project_id: str = "proj") -> Path:
    data = tmp_path / "data"
    repos = data / "repos"
    repo = repos / project_id
    repo.mkdir(parents=True)
    monkeypatch.setattr(config, "data_dir", data)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    monkeypatch.setattr(config, "project_root", tmp_path / "kb")
    return repo


def test_repo_toolbox_rejects_path_traversal(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    toolbox = RepoToolbox("proj")

    with pytest.raises(ValueError):
        toolbox._resolve_repo_path("../secrets.txt")

    with pytest.raises(ValueError):
        toolbox._resolve_repo_path("/tmp/secrets.txt")


def test_repo_toolbox_read_file_is_bounded(tmp_path, monkeypatch):
    repo = _make_project(tmp_path, monkeypatch)
    (repo / "main.py").write_text("\n".join(f"line {i}" for i in range(1, 21)), encoding="utf-8")
    monkeypatch.setattr(config, "deep_dive_max_file_bytes", 1_000)

    result = RepoToolbox("proj").read_file("main.py", start_line=3, max_lines=4)

    assert result.ok
    assert "3: line 3" in result.content
    assert "6: line 6" in result.content
    assert "7: line 7" not in result.content
    assert result.truncated is True


def test_repo_toolbox_search_repo_is_bounded(tmp_path, monkeypatch):
    repo = _make_project(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("needle\nother\nneedle again\n", encoding="utf-8")
    (repo / "b.txt").write_text("needle third\n", encoding="utf-8")
    monkeypatch.setattr(config, "deep_dive_max_search_results", 2)

    result = RepoToolbox("proj").search_repo("needle", max_results=2)

    assert result.ok
    assert result.metadata["count"] == 2
    assert result.truncated is True


def test_repo_toolbox_scan_and_generated_doc_tools(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    scan_dir = config.intodoc_output_dir / "proj"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_result.json").write_text(
        json.dumps({"platform": "generic", "jobs": [{"name": "JobA", "group": "G"}], "warnings": []}),
        encoding="utf-8",
    )
    docs = config.project_root / "output" / "proj" / "docs"
    docs.mkdir(parents=True)
    (docs / "00_AGENT_BRIEF.md").write_text("# Summary\n\nEvidence", encoding="utf-8")

    toolbox = RepoToolbox("proj")
    scan = toolbox.read_scan_summary()
    doc = toolbox.read_generated_doc("00_AGENT_BRIEF.md")

    assert scan.ok
    assert '"job_count": 1' in scan.content
    assert doc.ok
    assert "# Summary" in doc.content


def test_deep_dive_engine_selection(monkeypatch):
    monkeypatch.setattr(config, "deep_dive_engine", "claude_cli")
    assert isinstance(DeepDiveService._build_engine(), ClaudeCliDeepDiveEngine)


@pytest.mark.asyncio
async def test_repo_agent_streams_tool_use_and_final_answer(tmp_path, monkeypatch):
    repo = _make_project(tmp_path, monkeypatch)
    (repo / "main.py").write_text("def customer_transform():\n    return True\n", encoding="utf-8")
    monkeypatch.setattr(config, "deep_dive_max_tool_calls", 4)
    monkeypatch.setattr(config, "deep_dive_model", "openai/fake")

    class _FakeProvider:
        model_name = "fake"
        supports_tools = False

        def __init__(self):
            self.calls = 0

        async def complete(self, prompt, context_files=None, options=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResult(
                    success=True,
                    text=json.dumps({"tool": "search_repo", "arguments": {"query": "customer_transform"}}),
                    model_name=self.model_name,
                    input_tokens=10,
                    output_tokens=5,
                )
            return ModelResult(
                success=True,
                text=json.dumps({"final": "Found `customer_transform` in `main.py`."}),
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=5,
            )

    events = [
        event
        async for event in RepoAgentEngine(provider=_FakeProvider()).query(
            "proj",
            "where is customer transform?",
        )
    ]

    assert any(event["type"] == "tool_use" for event in events)
    assert any(event["type"] == "token" and "main.py" in event["data"] for event in events)
    assert events[-1]["type"] == "done"


def test_repo_agent_parses_deepseek_xml_tool_calls():
    text = """<function_calls>
<invoke name="search_repo">
<parameter name="query" string="true">URL</parameter>
<parameter name="path" string="true">profiles</parameter>
<parameter name="glob" string="true">*.xml</parameter>
<parameter name="max_results" string="false">50</parameter>
</invoke>
<invoke name="read_file">
<parameter name="path" string="true">profiles/API_Orders.xml</parameter>
<parameter name="max_lines" string="false">100</parameter>
</invoke>
</function_calls>"""

    decision = RepoAgentEngine._parse_decision(text)

    assert decision == {
        "tool_calls": [
            {
                "tool": "search_repo",
                "arguments": {
                    "query": "URL",
                    "path": "profiles",
                    "glob": "*.xml",
                    "max_results": 50,
                },
            },
            {
                "tool": "read_file",
                "arguments": {
                    "path": "profiles/API_Orders.xml",
                    "max_lines": 100,
                },
            },
        ]
    }


def test_repo_agent_parses_function_style_tool_call():
    decision = RepoAgentEngine._parse_decision('list_files({"path": "", "max_depth": 3})')

    assert decision == {"tool": "list_files", "arguments": {"path": "", "max_depth": 3}}


def test_repo_agent_parses_tool_calls_with_trailing_tool_results():
    text = """{"tool_calls": [{"tool": "search_repo", "arguments": {"query": "http://", "path": "", "glob": ".xml", "max_results": 50}}]}

TOOL_RESULTS: [{"tool": "search_repo", "result": {"ok": true, "content": "Found 50 matches."}}]
Let me search for more endpoint-defining patterns."""

    decision = RepoAgentEngine._parse_decision(text)

    assert decision == {
        "tool_calls": [
            {
                "tool": "search_repo",
                "arguments": {
                    "query": "http://",
                    "path": "",
                    "glob": ".xml",
                    "max_results": 50,
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_repo_agent_executes_batch_tool_calls(tmp_path, monkeypatch):
    repo = _make_project(tmp_path, monkeypatch)
    (repo / "profiles").mkdir()
    (repo / "profiles" / "API_Orders.xml").write_text("<connection>URL</connection>\n", encoding="utf-8")
    monkeypatch.setattr(config, "deep_dive_max_tool_calls", 4)
    monkeypatch.setattr(config, "deep_dive_model", "openai/fake")

    class _XmlProvider:
        model_name = "fake"
        supports_tools = False

        def __init__(self):
            self.calls = 0

        async def complete(self, prompt, context_files=None, options=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResult(
                    success=True,
                    text="""<function_calls>
<invoke name="search_repo"><parameter name="query" string="true">URL</parameter><parameter name="path" string="true">profiles</parameter><parameter name="glob" string="true">*.xml</parameter><parameter name="max_results" string="false">50</parameter></invoke>
<invoke name="read_file"><parameter name="path" string="true">profiles/API_Orders.xml</parameter><parameter name="max_lines" string="false">100</parameter></invoke>
</function_calls>""",
                    model_name=self.model_name,
                    input_tokens=10,
                    output_tokens=5,
                )
            assert "TOOL_RESULTS" in prompt
            return ModelResult(
                success=True,
                text=json.dumps({"final": "Found URL evidence in `profiles/API_Orders.xml`."}),
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=5,
            )

    events = [event async for event in RepoAgentEngine(provider=_XmlProvider()).query("proj", "find URL")]

    tool_events = [event for event in events if event["type"] == "tool_use"]
    assert len(tool_events) == 2
    assert any(event["type"] == "token" and "API_Orders.xml" in event["data"] for event in events)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_repo_agent_stops_at_tool_limit(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "deep_dive_max_tool_calls", 1)

    class _LoopProvider:
        model_name = "fake"
        supports_tools = False
        calls = 0

        async def complete(self, prompt, context_files=None, options=None):
            self.calls += 1
            if "FINAL_SYNTHESIS_REQUIRED" in prompt:
                return ModelResult(
                    success=True,
                    text="I inspected the repository but found no conclusive answer.",
                    model_name=self.model_name,
                    input_tokens=1,
                    output_tokens=1,
                )
            return ModelResult(
                success=True,
                text=json.dumps({"tool": "list_files", "arguments": {}}),
                model_name=self.model_name,
                input_tokens=1,
                output_tokens=1,
            )

    events = [event async for event in RepoAgentEngine(provider=_LoopProvider()).query("proj", "loop")]

    assert any(event["type"] == "status" and "Preparing answer" in event["data"] for event in events)
    assert any(event["type"] == "token" and "inspected the repository" in event["data"] for event in events)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_repo_agent_errors_and_done_when_final_synthesis_is_unusable(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "deep_dive_max_tool_calls", 1)

    class _BadFinalProvider:
        model_name = "fake"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            if "FINAL_SYNTHESIS_REQUIRED" in prompt:
                return ModelResult(
                    success=True,
                    text='{"tool_calls": [{"tool": "list_files", "arguments": {}}]}',
                    model_name=self.model_name,
                    input_tokens=1,
                    output_tokens=1,
                )
            return ModelResult(
                success=True,
                text=json.dumps({"tool": "list_files", "arguments": {}}),
                model_name=self.model_name,
                input_tokens=1,
                output_tokens=1,
            )

    events = [event async for event in RepoAgentEngine(provider=_BadFinalProvider()).query("proj", "loop")]

    assert any(event["type"] == "error" and "final answer" in event["data"] for event in events)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_repo_agent_model_failure_emits_done(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)

    class _FailProvider:
        model_name = "fake"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=False,
                text="",
                model_name=self.model_name,
                raw_error="provider unavailable",
            )

    events = [event async for event in RepoAgentEngine(provider=_FailProvider()).query("proj", "question")]

    assert any(event["type"] == "error" and "provider unavailable" in event["data"] for event in events)
    assert events[-1]["type"] == "done"
