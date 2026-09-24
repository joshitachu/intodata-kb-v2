"""Business topology on the CLI transports (claude-cli, opencode-cli).

Before the shared builder existed only the custom harness's Phase 3.7 produced a
topology, so a claude-cli or opencode run published documents with no
``_topology/`` at all. These pin that both CLI transports now stage one, that
each transport gets a bounded single-turn completion on the run's OWN model
(Claude CLI through ``ClaudeCliModelProvider``, OpenCode through
``OpenCodeCompletionProvider`` on the transport's route), that the custom
lane is left to its own phase, and that a topology failure never fails a run.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mcp_server.providers.custom.models import ModelResult
from mcp_server.services.claude_service import DocGenerationCoordinator
from tests.test_services.test_topology_flow_assembly import (
    _EXTRACTION,
    _write_docs,
    _write_scan,
)


@pytest.fixture
def coordinator(log_manager):
    return DocGenerationCoordinator(log_manager=log_manager)


def _ctx(tmp_path: Path) -> tuple[SimpleNamespace, Path]:
    scan_root = tmp_path / "scan-output"
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_scan(scan_root)
    _write_docs(staging)
    ctx = SimpleNamespace(
        project_id="demo",
        staging_dir=staging,
        partition=None,
        effective_model="sonnet",
        live_evidence=False,  # opted out: no HTTP to the runtime mirror
    )
    return ctx, scan_root


def _collecting_log():
    lines: list[tuple[str, str]] = []

    async def _log(pid: str, message: str, level: str = "info") -> None:
        lines.append((message, level))

    return lines, _log


async def test_opencode_uses_a_bounded_completion_on_the_transport_route(
    coordinator, tmp_path, monkeypatch
) -> None:
    ctx, scan_root = _ctx(tmp_path)
    _, log = _collecting_log()
    created: list = []

    class _FakeOpenCodeCompletion:
        def __init__(self, command=None, model_route=None) -> None:
            self.command = command
            self.model_name = model_route or "opencode"
            self.prompts: list[str] = []
            created.append(self)

        async def complete(self, prompt, context_files=None, options=None):
            self.prompts.append(prompt)
            return ModelResult(
                success=True, text=json.dumps(_EXTRACTION), model_name=self.model_name
            )

    monkeypatch.setattr(
        "mcp_server.providers.opencode_cli.OpenCodeCompletionProvider",
        _FakeOpenCodeCompletion,
    )
    transport = SimpleNamespace(command="opencode", model_route="opencode-go/some-model")

    await coordinator._build_cli_business_topology(
        ctx,
        uses_claude_cli=False,
        transport_provider=transport,
        scan_output_dir=scan_root,
        unit_allowlist=None,
        plugin_pack_dir=None,
        log_cb=log,
    )

    topo_dir = ctx.staging_dir / "_topology"
    state = json.loads((topo_dir / "state.json").read_text(encoding="utf-8"))
    assert state["available"] is True and state["validated"] is True
    assert state["lane"] == "flow_extraction"
    assert state["model_lanes"]["enabled"] is True
    assert [call["purpose"] for call in state["model_lanes"]["calls"]] == ["flow_extraction"]
    # The run's own OpenCode route and executable, not a separately configured one.
    assert created[0].model_name == "opencode-go/some-model"
    assert created[0].command == "opencode"
    assert "enrichment" in state
    assert (topo_dir / "business_topology.json").is_file()
    # Runtime evidence is used in memory; nothing is staged where it would publish.
    assert not (ctx.staging_dir / "_transient").exists()


async def test_claude_cli_uses_a_bounded_single_turn_completion(
    coordinator, tmp_path, monkeypatch
) -> None:
    ctx, scan_root = _ctx(tmp_path)
    _, log = _collecting_log()
    created: list = []

    class _FakeCliModel:
        def __init__(self, model_name: str | None = None) -> None:
            self.model_name = model_name or ""
            self.prompts: list[str] = []
            created.append(self)

        async def complete(self, prompt, context_files=None, options=None):
            self.prompts.append(prompt)
            return ModelResult(
                success=True, text=json.dumps(_EXTRACTION), model_name=self.model_name
            )

    monkeypatch.setattr(
        "mcp_server.providers.custom.providers.ClaudeCliModelProvider", _FakeCliModel
    )

    await coordinator._build_cli_business_topology(
        ctx,
        uses_claude_cli=True,
        scan_output_dir=scan_root,
        unit_allowlist=None,
        plugin_pack_dir=None,
        log_cb=log,
    )

    state = json.loads(
        (ctx.staging_dir / "_topology" / "state.json").read_text(encoding="utf-8")
    )
    assert state["lane"] == "flow_extraction"
    assert state["model_lanes"]["enabled"] is True
    calls = state["model_lanes"]["calls"]
    assert [call["purpose"] for call in calls] == ["flow_extraction"]
    # The run's own configured CLI model, not a separately configured one.
    assert created[0].model_name == "sonnet"
    assert len(created[0].prompts) == 1


async def test_topology_failure_never_escapes_the_hook(
    coordinator, tmp_path, monkeypatch
) -> None:
    ctx, scan_root = _ctx(tmp_path)
    lines, log = _collecting_log()

    async def _explode(*args, **kwargs):
        raise RuntimeError("builder exploded")

    monkeypatch.setattr(
        "mcp_server.services.business_topology.builder.build_business_topology", _explode
    )

    await coordinator._build_cli_business_topology(
        ctx,
        uses_claude_cli=False,
        scan_output_dir=scan_root,
        unit_allowlist=None,
        plugin_pack_dir=None,
        log_cb=log,
    )

    assert any("builder exploded" in message and level == "warning" for message, level in lines)


@pytest.mark.parametrize(
    ("provider", "expect_topology"),
    [("opencode-cli", True), ("claude-cli", True), ("custom", False)],
)
async def test_generate_docs_builds_topology_for_cli_transports_only(
    coordinator, monkeypatch, tmp_path, provider, expect_topology
) -> None:
    """The hook is wired on the transport, not the provider object.

    The custom harness builds its own in Phase 3.7; running the hook there too
    would overwrite that phase's model-lane result with a second build.
    """

    from mcp_server.config import config
    from mcp_server.providers.base import AgentResult
    from mcp_server.services.scanner_service import ScannerService

    monkeypatch.setattr(config, "project_root", tmp_path)
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc-output")
    _write_scan(config.intodoc_output_dir / "proj")

    class _FakeProvider:
        async def run(self, ctx, log_cb):
            _write_docs(ctx.staging_dir)
            return AgentResult(success=True)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class _NoModel:
        model_name = "sonnet"

        def __init__(self, model_name=None):
            pass

        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(success=False, raw_error="offline in tests")

    monkeypatch.setattr(
        "mcp_server.providers.custom.providers.ClaudeCliModelProvider", _NoModel
    )
    monkeypatch.setattr(
        "mcp_server.providers.opencode_cli.OpenCodeCompletionProvider",
        lambda command=None, model_route=None: _NoModel(),
    )
    monkeypatch.setattr(
        ScannerService, "get_analysis_prompts",
        lambda self, platform: {"prompts": {"ANALYSIS_GUIDE.md": "guide"}, "consolidated": True},
    )
    monkeypatch.setattr(
        ScannerService, "get_phase3_guide",
        lambda self, platform: {"content": "phase3 guide"},
    )

    with patch("mcp_server.db.session.async_session_factory", lambda: _FakeSession()), \
         patch(
             "mcp_server.services.project_registry.ProjectRegistry.get_project",
             return_value=SimpleNamespace(platform="talend"),
         ), \
         patch("mcp_server.services.claude_service.get_provider", return_value=_FakeProvider()), \
         patch(
             "mcp_server.services.claude_service.instance_settings_service.get_value",
             return_value=None,
         ):
        result = await coordinator.generate_docs(
            "proj", doc_generation_provider=provider, live_evidence=False
        )

    assert result.get("success") is True, result
    state_path = tmp_path / "output" / "proj" / "docs_staging" / "_topology" / "state.json"
    assert state_path.is_file() is expect_topology
    if expect_topology:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["validated"] is True
        # Both CLI transports now get a bounded model lane on their own model.
        assert state["model_lanes"]["enabled"] is True
