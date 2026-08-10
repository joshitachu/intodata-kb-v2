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
from mcp_server.services.claude_service import (
    DocGenerationCoordinator as ClaudeService,
    _render_chunk_extras,
)


def test_format_chunk_markdown_uses_composite_job_identity():
    class Scanner:
        def read_chunk_data(self, _output_dir, _chunk_id, unit_allowlist=None):
            return {"jobs": [{"name": "Shared", "job_id": "root_a_Shared-123", "source_root": "exports/A", "components": []}]}

    parts, count = ClaudeService._format_chunk_markdown(Scanner(), "/tmp", [{"chunk_id": "one"}])
    rendered = "".join(parts)
    assert count == 1
    assert "## Talend Job: root_a_Shared-123" in rendered
    assert "Display name: `Shared`" in rendered
    assert "Source root: `exports/A`" in rendered


def test_format_chunk_markdown_keeps_talend_routes_distinct_from_jobs():
    class Scanner:
        def read_chunk_data(self, _output_dir, _chunk_id, unit_allowlist=None):
            return {
                "jobs": [{
                    "name": "Inbound",
                    "job_id": "Inbound",
                    "artifact_kind": "route",
                    "components": [],
                }],
            }

    parts, count = ClaudeService._format_chunk_markdown(
        Scanner(), "/tmp", [{"chunk_id": "one"}], platform="talend",
    )
    rendered = "".join(parts)

    assert count == 1
    assert "## Talend Route: Inbound" in rendered
    assert "## Talend Job: Inbound" not in rendered
    assert "Analyze as message mediation" in rendered


def test_render_chunk_extras_includes_redacted_support_context_evidence():
    parts: list[str] = []
    _render_chunk_extras({
        "related_contexts": [{
            "name": "Runtime",
            "source_project": "Shared Configuration",
            "source_root": "shared",
            "source_role": "support",
            "purpose": "Runtime endpoints",
            "variables": [{
                "name": "API_SECRET",
                "type": "id_String",
                "values": {"DEV": "[REDACTED]", "PROD": "[REDACTED]"},
            }],
        }],
    }, "chunk", parts)

    rendered = "".join(parts)
    assert "Shared Configuration" in rendered
    assert "`shared` (support)" in rendered
    assert '"DEV": "[REDACTED]"' in rendered
    assert '"PROD": "[REDACTED]"' in rendered


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


def test_doc_templates_require_unformatted_evidence_confidence(tmp_path):
    preprocess = tmp_path / "_preprocess"
    preprocess.mkdir()

    ClaudeService._write_doc_templates(preprocess)

    templates = (preprocess / "_doc_templates.md").read_text(encoding="utf-8")
    assert "confidence cells must start with plain unformatted HIGH / MEDIUM / LOW text" in templates
    assert "do not bold, italicize, quote, or wrap that leading token in backticks" in templates


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


async def test_generate_docs_revalidates_pack_governance_before_execution(
    claude_svc,
    monkeypatch,
    tmp_path,
):
    import mcp_server.services.claude_service as claude_mod
    from mcp_server.config import config
    from mcp_server.services.pack_governance import PackGovernanceDenied

    monkeypatch.setattr(config, "project_root", tmp_path)
    project = SimpleNamespace(platform="talend", plugin_pack_id=7)
    pack = SimpleNamespace(id=7, name="changed-after-assignment", path=str(tmp_path / "pack"))

    class _Result:
        def scalar_one_or_none(self):
            return pack

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, _statement):
            return _Result()

    async def reject_changed_pack(_pack):
        raise PackGovernanceDenied("Pack integrity changed after assignment. Submit changes through Git/PR.")

    monkeypatch.setattr(claude_mod, "async_session_factory", _FakeSession)
    monkeypatch.setattr(
        claude_mod.pack_governance,
        "require_generation_authority",
        reject_changed_pack,
    )

    with patch(
        "mcp_server.services.project_registry.ProjectRegistry.get_project",
        return_value=project,
    ):
        result = await claude_svc.generate_docs("proj", plugin_pack_id=7)

    assert result["success"] is False
    assert "integrity changed after assignment" in result["error"]
    assert not (tmp_path / "output" / "proj" / "docs_staging" / ".generation.lock").exists()


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


async def test_generate_docs_partition_routes_output_and_scopes_preprocess(
    claude_svc, monkeypatch, tmp_path,
):
    """Composite sub-project run: output routes to output/{pid}/{sub}/ and the
    preprocess sees ONLY that partition's units (shared-name jobs stay local)."""
    from mcp_server.config import config
    from mcp_server.providers.base import AgentResult
    from mcp_server.providers.custom.validators import build_facts_manifest
    from mcp_server.providers.custom.partition_planner import plan_partitions
    from mcp_server.services.scanner_service import ScannerService

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    scan_dir = config.intodoc_output_dir / "proj"
    scan_dir.mkdir(parents=True)
    zip_a = "B/Alpha.__intodoc_zip__/ALPHA"
    zip_a_refs = "B/Alpha.__intodoc_zip__/REFERENCES"
    zip_b = "B/Beta.__intodoc_zip__/BETA"
    zip_b_refs = "B/Beta.__intodoc_zip__/REFERENCES"
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "talend",
        "jobs": [
            {"name": "A_Main", "job_id": "A_Main-h1", "group": "Flows",
             "source_root": zip_a, "path": "process/A_Main.item", "components": []},
            {"name": "LogInfo", "job_id": "LogInfo-ha", "group": "Logging",
             "source_root": zip_a_refs, "path": "process/LogInfo.item", "components": []},
            {"name": "B_Main", "job_id": "B_Main-h2", "group": "Flows",
             "source_root": zip_b, "path": "process/B_Main.item", "components": []},
            {"name": "LogInfo", "job_id": "LogInfo-hb", "group": "Logging",
             "source_root": zip_b_refs, "path": "process/LogInfo.item", "components": []},
        ],
        "files": [], "manifest": {}, "warnings": [],
    }), encoding="utf-8")

    facts = build_facts_manifest("proj", scan_dir)
    plan = plan_partitions(facts)
    alpha = next(p for p in plan.partitions if p.name == "Alpha")

    project = SimpleNamespace(platform="talend")
    captured: dict = {}

    class _FakeProvider:
        async def run(self, ctx, log_cb):
            captured["ctx"] = ctx
            return AgentResult(success=True)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    # Keep the preprocess off the intodoc package (not installed in tests).
    monkeypatch.setattr(
        ScannerService, "get_analysis_prompts",
        lambda self, platform: {"prompts": {"ANALYSIS_GUIDE.md": "guide"}, "consolidated": True},
    )
    monkeypatch.setattr(
        ScannerService, "get_phase3_guide",
        lambda self, platform: {"content": "phase3 guide"},
    )

    with patch("mcp_server.db.session.async_session_factory", lambda: _FakeSession()), \
         patch("mcp_server.services.project_registry.ProjectRegistry.get_project", return_value=project), \
         patch("mcp_server.services.claude_service.get_provider", return_value=_FakeProvider()), \
         patch("mcp_server.services.claude_service.instance_settings_service.get_value", return_value=None):
        result = await claude_svc.generate_docs(
            "proj", doc_generation_provider="custom", partition=alpha,
        )

    assert result.get("success") is True
    ctx = captured["ctx"]
    # Output routed to the sub-project directory.
    part_output = tmp_path / "output" / "proj" / "Alpha"
    assert ctx.staging_dir == part_output / "docs_staging"
    assert ctx.partition is alpha
    # Preprocess contains only Alpha's units: its own shared-name LogInfo copy
    # is present, Beta's copy and Beta's units are not.
    preprocess = ctx.staging_dir / "_preprocess"
    logging_md = (preprocess / "Logging.md").read_text(encoding="utf-8")
    assert "LogInfo-ha" in logging_md
    assert "LogInfo-hb" not in logging_md
    flows_md = (preprocess / "Flows.md").read_text(encoding="utf-8")
    assert "A_Main-h1" in flows_md
    assert "B_Main-h2" not in flows_md
    assert sorted(ctx.preprocess_groups) == ["Flows", "Logging"]
    assert ctx.total_jobs == 2


async def test_preprocess_disambiguates_case_colliding_groups(claude_svc, monkeypatch, tmp_path):
    """Case-insensitive filesystems must not clobber groups differing only in
    case (verified on werkorder: VISE2AssetCheck vs Vise2AssetCheck). The later
    collider gets a `{group}_part_ci{k}` slice name, which the existing
    slice->group resolvers already map back to the right group."""
    from mcp_server.config import config
    from mcp_server.services.scanner_service import ScannerService

    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    scan_dir = config.intodoc_output_dir / "proj"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_result.json").write_text(json.dumps({
        "platform": "talend",
        "jobs": [
            {"name": "UpperJob", "job_id": "UpperJob", "group": "VISE2AssetCheck",
             "path": "process/UpperJob.item", "components": []},
            {"name": "LowerJob", "job_id": "LowerJob", "group": "Vise2AssetCheck",
             "path": "process/LowerJob.item", "components": []},
        ],
        "files": [], "manifest": {}, "warnings": [],
    }), encoding="utf-8")
    monkeypatch.setattr(
        ScannerService, "get_analysis_prompts",
        lambda self, platform: {"prompts": {"g": "guide"}, "consolidated": True},
    )
    monkeypatch.setattr(
        ScannerService, "get_phase3_guide", lambda self, platform: {"content": "p3"},
    )

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    groups = await claude_svc._preprocess_scan_data("proj", staging, "talend")

    assert sorted(groups) == ["VISE2AssetCheck", "Vise2AssetCheck_part_ci1"]
    preprocess = staging / "_preprocess"
    upper = (preprocess / "VISE2AssetCheck.md").read_text(encoding="utf-8")
    lower = (preprocess / "Vise2AssetCheck_part_ci1.md").read_text(encoding="utf-8")
    # Each group keeps its OWN units; nothing clobbered or cross-wired.
    assert "UpperJob" in upper and "LowerJob" not in upper
    assert "LowerJob" in lower and "UpperJob" not in lower

    # The existing slice->group resolvers map the suffixed slice back correctly.
    from types import SimpleNamespace

    from mcp_server.providers.custom.harness import CustomHarnessProvider
    from mcp_server.providers.custom.validators import build_facts_manifest

    facts = build_facts_manifest("proj", scan_dir)
    state = SimpleNamespace(facts_manifest=facts)
    assert CustomHarnessProvider._group_units(state, "Vise2AssetCheck_part_ci1") == ["LowerJob"]
    assert CustomHarnessProvider._group_units(state, "VISE2AssetCheck") == ["UpperJob"]
