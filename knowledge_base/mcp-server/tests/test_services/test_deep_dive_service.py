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


def _write_scan_result(tmp_path, project_id: str = "proj") -> None:
    """Write a minimal scan_result.json for scan-tool tests."""
    scan_dir = tmp_path / "intodoc-output" / project_id
    scan_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    data = {
        "platform": "talend",
        "repo_root": "/some/repo",
        "jobs": [
            {
                "name": "LoadCustomers",
                "path": "jobs/CRM/LoadCustomers.item",
                "group": "CRM",
                "subgroup": "",
                "components": [{"type": "tJDBCInput"}, {"type": "tMap"}],
                "connections": [],
                "context_groups": ["connection_CRM_Oracle"],
            },
            {
                "name": "ExportInvoices",
                "path": "jobs/Finance/ExportInvoices.item",
                "group": "Finance",
                "subgroup": "",
                "components": [{"type": "tOracleInput"}, {"type": "tFileOutputDelimited"}],
                "connections": [],
                "context_groups": ["connection_Finance_Oracle"],
            },
        ],
        "metadata_connections": [
            {
                "xmi_id": "abc123",
                "name": "connection_CRM_Oracle",
                "connection_type": "Oracle",
                "host": "[HOST_REDACTED]",
                "port": "context.connection_CRM_Oracle_Port",
                "database": "context.connection_CRM_Oracle_DB",
                "additional_params": {"driver_class": "oracle.jdbc.OracleDriver"},
            },
        ],
        "context_groups": [
            {
                "name": "connection_CRM_Oracle",
                "variables": [
                    {"name": "connection_CRM_Oracle_Port", "type": "id_String",
                     "values": {"Default": "", "PROD": "1521", "TEST": "1521"}},
                    {"name": "connection_CRM_Oracle_DB", "type": "id_String",
                     "values": {"Default": "", "PROD": "CRMDB", "TEST": "CRMTEST"}},
                ],
            },
        ],
        "warnings": [],
    }
    (scan_dir / "scan_result.json").write_text(_json.dumps(data), encoding="utf-8")


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


@pytest.mark.asyncio
async def test_repo_agent_recovers_partial_answer_on_max_tokens(tmp_path, monkeypatch):
    """When the model is truncated (stop_reason=max_tokens) but has partial text, yield it
    as a token event rather than surfacing a hard error."""
    _make_project(tmp_path, monkeypatch)

    class _TruncatedProvider:
        model_name = "fake"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=False,
                text="# Partial Answer\n\nSome useful content here.",
                model_name=self.model_name,
                raw_error="incomplete: stop_reason=max_tokens",
                input_tokens=1000,
                output_tokens=500,
            )

    events = [event async for event in RepoAgentEngine(provider=_TruncatedProvider()).query("proj", "question")]

    token_events = [e for e in events if e["type"] == "token"]
    error_events = [e for e in events if e["type"] == "error"]
    assert token_events, "expected partial answer to be emitted as token event"
    assert "Partial Answer" in token_events[0]["data"]
    assert not error_events, "expected no error event when partial text is available"
    assert events[-1]["type"] == "done"


def test_prune_transcript_reduces_large_transcript():
    """_prune_transcript stubs old TOOL_RESULTS blocks and keeps the last 3 intact."""
    marker = "\n\nMODEL_DECISION:\n"
    tool_marker = "\n\nTOOL_RESULTS:\n"
    header = "SYSTEM PROMPT\n\nUser question: what is this?"

    large_result = "x" * 50_000  # ~12,500 tokens each
    turns = [
        f'{{"tool": "read_file", "arguments": {{"path": "file{i}.xml"}}}}' + tool_marker + large_result
        for i in range(5)
    ]
    transcript = header + marker + marker.join(turns)

    pruned = RepoAgentEngine._prune_transcript(transcript, target_tokens=10_000)

    assert len(pruned) < len(transcript), "pruned transcript should be shorter"
    # Last 3 turns must be verbatim
    for turn in turns[-3:]:
        assert large_result in pruned, "last 3 TOOL_RESULTS blocks must be intact"
    # Older turns must be stubbed
    assert pruned.count("[pruned]") >= 2, "at least 2 old turns should be stubbed"
    # Header must be preserved
    assert "SYSTEM PROMPT" in pruned
    assert "User question" in pruned


def test_get_scan_connections_returns_connections(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    toolbox = RepoToolbox("proj")
    result = toolbox.get_scan_connections()
    assert result.ok
    assert "connection_CRM_Oracle" in result.content
    assert "Oracle" in result.content
    # xmi_id should be stripped
    assert "xmi_id" not in result.content
    assert result.metadata["count"] == 1


def test_get_scan_connections_missing_scan(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    toolbox = RepoToolbox("proj")
    result = toolbox.get_scan_connections()
    assert not result.ok


def test_get_context_group_exact_match(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    toolbox = RepoToolbox("proj")
    result = toolbox.get_context_group("connection_CRM_Oracle")
    assert result.ok
    assert "1521" in result.content
    assert "CRMDB" in result.content


def test_get_context_group_partial_match(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    toolbox = RepoToolbox("proj")
    result = toolbox.get_context_group("CRM_Oracle")
    assert result.ok
    assert "connection_CRM_Oracle" in result.content


def test_get_context_group_not_found(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    toolbox = RepoToolbox("proj")
    result = toolbox.get_context_group("nonexistent_group")
    assert not result.ok
    assert "not found" in result.content


def test_search_scan_jobs_by_component(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    toolbox = RepoToolbox("proj")
    result = toolbox.search_scan_jobs("tJDBCInput")
    assert result.ok
    assert "LoadCustomers" in result.content
    assert "ExportInvoices" not in result.content  # only LoadCustomers uses tJDBCInput


def test_search_scan_jobs_by_context_group(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    toolbox = RepoToolbox("proj")
    result = toolbox.search_scan_jobs("connection_Finance_Oracle")
    assert result.ok
    assert "ExportInvoices" in result.content


def test_search_scan_jobs_no_match(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    toolbox = RepoToolbox("proj")
    result = toolbox.search_scan_jobs("tSalesforceInput")
    assert result.ok
    assert result.metadata["count"] == 0


def test_build_project_briefing_includes_scan_and_docs(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    _write_scan_result(tmp_path)
    # Create a fake docs directory
    docs_dir = tmp_path / "kb" / "output" / "proj" / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "00_EXECUTIVE_SUMMARY.md").write_text("# Summary")
    (docs_dir / "03_DATA_FLOWS.md").write_text("# Flows")

    briefing = RepoToolbox.build_project_briefing("proj")

    assert briefing["platform"] == "talend"
    assert briefing["job_count"] == 2
    assert briefing["connection_count"] == 1
    assert briefing["connections"][0]["name"] == "connection_CRM_Oracle"
    assert "00_EXECUTIVE_SUMMARY.md" in briefing["generated_docs"]
    assert "03_DATA_FLOWS.md" in briefing["generated_docs"]


def test_search_repo_files_only(tmp_path, monkeypatch):
    repo = _make_project(tmp_path, monkeypatch)
    (repo / "job_a.item").write_text("<component type='tJDBCInput'/>")
    (repo / "job_b.item").write_text("<component type='tFileInput'/>")
    (repo / "job_c.xml").write_text("<component type='tJDBCInput'/>")
    toolbox = RepoToolbox("proj")

    result = toolbox.search_repo("tJDBCInput", files_only=True)
    assert result.ok
    lines = result.content.strip().splitlines()
    # Should return file paths, not file:line:content
    assert all(":" not in line.split("/")[-1] for line in lines if line != "(no matches)")
    # Both .item and .xml should match
    assert any("job_a.item" in line for line in lines)
    assert any("job_c.xml" in line for line in lines)
    assert not any("job_b.item" in line for line in lines)


def test_cap_transcript_keeps_header_and_tail():
    """_cap_transcript keeps the first 2 KB and the most recent content."""
    header_content = "A" * 2_000
    middle = "B" * 400_000
    tail_content = "C" * 50_000
    transcript = header_content + middle + tail_content

    capped = RepoAgentEngine._cap_transcript(transcript, max_tokens=100_000)

    max_chars = 100_000 * 4
    assert len(capped) <= max_chars + 100  # allow small overhead for the pruning notice
    assert capped.startswith(header_content[:2_000])
    assert capped.endswith(tail_content[-1_000:])
    assert "[...earlier tool results pruned" in capped
