from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.models import FactsManifest, HarnessRunState, UnitFact
from mcp_server.providers.opencode_artifact_mcp import ArtifactSubmissionFacade
from mcp_server.providers.opencode_cli import (
    OpenCodeAttemptError,
    OpenCodeAttemptLedger,
    OpenCodeCliProvider,
    OpenCodeCompatibilityError,
    OpenCodePhaseConfig,
    OpenCodeTaskContract,
    _error_reason,
    _PhaseExecution,
    parse_mcp_read_allowlist,
)


def _ctx(tmp_path: Path) -> DocGenContext:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True)
    return DocGenContext(
        "p",
        staging,
        str(staging),
        str(tmp_path / "logs"),
        [],
        0,
        0,
        "opencode",
        "opencode",
        "generic",
        "_analysis_guide.md",
    )


def test_provider_factory_routes_opencode_without_custom_or_claude(monkeypatch):
    from mcp_server.providers import get_provider

    monkeypatch.setattr(config, "doc_generation_provider", "custom")
    provider = get_provider(
        provider_type="opencode-cli",
        model="openai/gpt-5.6-sol",
    )
    assert isinstance(provider, OpenCodeCliProvider)
    assert provider.model_route == "openai/gpt-5.6-sol"


def test_mcp_allowlist_is_deny_by_default_and_rejects_mutations():
    assert parse_mcp_read_allowlist("{}") == {}
    assert parse_mcp_read_allowlist('{"docs": ["search", "get_page"]}') == {
        "docs": ("search", "get_page")
    }
    with pytest.raises(OpenCodeCompatibilityError, match="read-only"):
        parse_mcp_read_allowlist('{"docs": ["write_page"]}')


def test_attempt_ledger_allows_exactly_one_nexus_dispatch(tmp_path):
    ledger = OpenCodeAttemptLedger(tmp_path)
    ledger.dispatch("step:gather")
    with pytest.raises(OpenCodeAttemptError):
        ledger.dispatch("step:gather")
    payload = json.loads((tmp_path / "_audit" / "opencode_attempt_ledger.json").read_text())
    assert payload["max_nexus_attempts"] == 1

    resumed = OpenCodeAttemptLedger(tmp_path)
    with pytest.raises(OpenCodeAttemptError):
        resumed.dispatch("step:gather")
    resumed.dispatch("step:produce_topology")
    resumed.release_unstarted("step:produce_topology")
    OpenCodeAttemptLedger(tmp_path).dispatch("step:produce_topology")


def test_bounded_reingestion_contract_emits_liveness_updates(monkeypatch, tmp_path):
    provider = OpenCodeCliProvider(model_route="opencode/test")
    monkeypatch.setattr(provider, "_preflight", lambda: ("/usr/bin/opencode", {}))
    monkeypatch.setattr(config, "opencode_reingestion_progress_interval_seconds", 1)
    monkeypatch.setattr(config, "opencode_reingestion_timeout_seconds", 10)
    received: list[tuple[str, str]] = []

    async def fake_execute_step(**kwargs):
        assert kwargs["timeout_seconds"] == 10
        await asyncio.sleep(1.01)
        return _PhaseExecution(session_id="ses_liveness", events=[])

    async def log_cb(_project_id: str, message: str, level: str):
        received.append((message, level))

    monkeypatch.setattr(provider, "_execute_step", fake_execute_step)
    contract = OpenCodeTaskContract(
        "produce", "reingestion:test", "_reingestion/task.json", (),
        "_reingestion/patch_report.json", prompt="patch docs",
    )

    result = asyncio.run(
        provider.run_bounded_contract(
            project_id="poly", workspace=tmp_path / "workspace", contract=contract,
            log_cb=log_cb, step="reingestion_patch", prompt="patch docs",
        )
    )

    assert result.success is True
    assert any("still running" in message for message, _level in received)


def test_attempt_ledger_scopes_dispatch_ceiling_to_generation_run(tmp_path):
    first = OpenCodeAttemptLedger(tmp_path, run_id=41)
    first.dispatch("step:analyze")

    with pytest.raises(OpenCodeAttemptError):
        OpenCodeAttemptLedger(tmp_path, run_id=41).dispatch("step:analyze")

    resumed = OpenCodeAttemptLedger(tmp_path, run_id=42)
    resumed.dispatch("step:analyze")
    payload = json.loads((tmp_path / "_audit" / "opencode_attempt_ledger.json").read_text())
    assert payload["run_id"] == 42
    assert payload["attempts"] == {"step:analyze": 1}


def test_contract_keeps_exact_prompt_off_argv_and_in_transient_contract():
    contract = OpenCodeTaskContract(
        "analyze",
        "analysis:A",
        "authoritative:group_analysis_prompt",
        ("facts_manifest.json",),
        "_analysis/A.md",
        prompt="exact renderer output",
    )
    assert contract.to_wire()["prompt"] == "exact renderer output"
    assert len(contract.to_wire()["prompt_sha256"]) == 64


def test_1187_command_uses_documented_flags_and_continues_same_session(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "opencode_model", "openai/gpt-5.5")
    monkeypatch.setattr(config, "opencode_primary_agent", "build")
    monkeypatch.setattr(config, "opencode_auto_approve", True)
    provider = OpenCodeCliProvider()
    first = provider._command("/usr/bin/opencode", _ctx(tmp_path))
    assert first == [
        "/usr/bin/opencode",
        "run",
        "--format",
        "json",
        "--model",
        "openai/gpt-5.5",
        "--dir",
        str(tmp_path / "staging"),
        "--agent",
        "build",
        "--auto",
    ]
    continued = provider._command("/usr/bin/opencode", _ctx(tmp_path / "continued"), "ses_123")
    assert continued[-3:] == ["--session", "ses_123", "--auto"]
    assert "--stdin" not in first and "--no-retry" not in first


def test_1187_preflight_accepts_pinned_runtime_without_retry_gate(monkeypatch):
    monkeypatch.setattr(config, "opencode_version", "1.18.7")
    monkeypatch.setattr(config, "opencode_model", "openai/gpt-5.5")
    monkeypatch.setattr(config, "opencode_mcp_read_allowlist_json", "{}")
    monkeypatch.setattr(
        "mcp_server.providers.opencode_cli.shutil.which", lambda _: "/usr/bin/opencode"
    )
    monkeypatch.setattr(
        "mcp_server.providers.opencode_cli.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="1.18.7\n"),
    )
    resolved, allowlist = OpenCodeCliProvider()._preflight()
    assert resolved == "/usr/bin/opencode"
    assert allowlist == {}


def test_no_lane_probes_the_runtime_for_a_surviving_session():
    """``_session_exists`` is gone, and nothing may reintroduce a probe.

    It had no production caller -- only this test -- while the failure it
    was meant to prevent still happened: a repair attempt exited 1 with
    ``Error: Session not found`` after a valid artifact had been written.
    ``agent-workspace-v1`` restarts with a FRESH session and a prose
    checkpoint instead of asking whether the old one survived, so a probe is
    not the fix and its absence is the contract.
    """

    assert not hasattr(OpenCodeCliProvider, "_session_exists")


def test_runtime_env_repairs_system_account_home_from_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/nonexistent")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

    env = OpenCodeCliProvider._runtime_env()

    assert env["HOME"] == str(tmp_path)
    assert env["PYTHONUTF8"] == "1"


def test_execution_env_registers_selected_ollama_model_and_preserves_config(
    monkeypatch,
):
    monkeypatch.setattr(config, "opencode_ollama_base_url", "http://ollama.internal:11434/v1")
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps({"mcp": {"docs": {"enabled": True}}}),
    )

    env = OpenCodeCliProvider(model_route="ollama/gemma3:4b")._execution_env()
    payload = json.loads(env["OPENCODE_CONFIG_CONTENT"])

    assert payload["mcp"]["docs"]["enabled"] is True
    ollama = payload["provider"]["ollama"]
    assert ollama["npm"] == "@ai-sdk/openai-compatible"
    assert ollama["options"]["baseURL"] == "http://ollama.internal:11434/v1"
    assert ollama["models"]["gemma3:4b"] == {"name": "gemma3:4b"}


def test_phase_execution_env_strips_inherited_mcp_and_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps(
            {"mcp": {"unrelated": {}}, "permission": {"*": "allow"}, "agent": {"build": {}}}
        ),
    )
    phase_config = OpenCodePhaseConfig(path=tmp_path / "opencode.json", environment={})
    phase_config.path.write_text("{}")

    env = OpenCodeCliProvider(model_route="openai/model")._execution_env(phase_config)

    assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == {}
    assert env["OPENCODE_CONFIG"] == str(phase_config.path)


def test_phase_config_limits_writes_to_declared_outputs(tmp_path):
    ctx = _ctx(tmp_path)
    contract = OpenCodeTaskContract(
        "analyze",
        "analysis:A",
        "test",
        ("facts_manifest.json",),
        "_analysis/A.md",
        prompt="write A",
    )
    phase_config = OpenCodeCliProvider()._phase_config(
        ctx=ctx,
        phase="analyze",
        contracts=[contract],
        runtime=None,
    )
    try:
        payload = json.loads(phase_config.path.read_text())
        edit = payload["permission"]["edit"]
        assert edit["*"] == "deny"
        assert edit["_analysis/A.md"] == "allow"
        assert edit[str((ctx.staging_dir / "_analysis/A.md").resolve())] == "allow"
        assert "**" not in edit
    finally:
        phase_config.cleanup()


def test_artifact_phase_config_disables_direct_edits_and_exposes_submit_tool(tmp_path):
    ctx = _ctx(tmp_path)
    contract = OpenCodeTaskContract(
        "analyze",
        "analysis:A",
        "test",
        ("facts_manifest.json",),
        "_analysis/A.md",
        prompt="write A",
    )
    facade = ArtifactSubmissionFacade(
        staging_dir=ctx.staging_dir,
        phase="analyze",
        step="analyze_batch_001",
        contracts=[contract],
    )
    phase_config = OpenCodeCliProvider()._phase_config(
        ctx=ctx,
        phase="analyze",
        contracts=[contract],
        runtime=None,
        artifact_facade=facade,
    )
    try:
        payload = json.loads(phase_config.path.read_text())
        assert payload["permission"]["edit"] == {"*": "deny"}
        assert payload["permission"]["bash"] == {"*": "deny"}
        assert payload["permission"]["nexus_artifacts_*"] == "allow"
        assert payload["mcp"]["nexus_artifacts"]["environment"] == {
            "NEXUS_ARTIFACT_GRANT": facade.token
        }
    finally:
        phase_config.cleanup()


def test_analyze_and_produce_prompt_requires_durable_submission_tool(tmp_path):
    provider = OpenCodeCliProvider()
    for phase in ("analyze", "produce"):
        prompt = provider._phase_prompt(
            phase,
            f"{phase}_batch_001",
            "_transient/opencode/contracts.json",
            1,
            {},
        )
        assert "nexus_artifacts_submit_artifact" in prompt
        assert "Do not use write, edit, apply_patch" in prompt
        assert "Use native read, grep, glob, and list" in prompt


def test_opencode_go_phase_uses_isolated_auth_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "opencode_go_api_key", "go-test-key")
    monkeypatch.setenv("MCP_OPENCODE_GO_API_KEY", "go-test-key")
    provider = OpenCodeCliProvider(model_route="opencode-go/kimi-k3")

    phase_config = provider._phase_config(
        ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None
    )
    try:
        env = provider._execution_env(phase_config)
        auth_path = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        assert json.loads(auth_path.read_text()) == {
            "opencode-go": {"type": "api", "key": "go-test-key"}
        }
        assert auth_path.stat().st_mode & 0o777 == 0o600
        assert "MCP_OPENCODE_GO_API_KEY" not in env
    finally:
        phase_config.cleanup()


_REAL_CREDIT_ERROR_EVENT = {
    # Verbatim shape of the failure that made the ettm_elastic composite run
    # unexplainable: the message existed, and every layer threw it away.
    "type": "error",
    "sessionID": "ses_abc",
    "error": {
        "name": "AI_APICallError",
        "data": {
            "message": (
                "This request requires more credits, or fewer max_tokens. "
                "You requested up to 32000 tokens, but can only afford 10666."
            )
        },
    },
}


def test_error_reason_extracts_the_provider_message_not_the_exception_class():
    reason = _error_reason(_REAL_CREDIT_ERROR_EVENT)
    assert "requires more credits" in reason
    assert "10666" in reason
    # The longest text wins, so the bare class name must not shadow the message.
    assert reason != "AI_APICallError"


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"type": "error", "error": "plain string failure"}, "plain string failure"),
        ({"type": "error", "message": "top level message"}, "top level message"),
        ({"type": "error", "error": {"detail": "nested detail here"}}, "nested detail here"),
        ({"type": "error"}, ""),
    ],
)
def test_error_reason_handles_the_shapes_opencode_actually_emits(event, expected):
    assert _error_reason(event) == expected


def test_error_reason_collapses_newlines_and_respects_the_log_limit():
    event = {"type": "error", "message": "line one\n\n   line two " + ("x" * 500)}
    reason = _error_reason(event)
    assert "\n" not in reason
    assert reason.startswith("line one line two")
    assert len(reason) <= 300


def test_live_event_summary_now_reports_the_error_reason_and_points_at_the_artifact(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    message, level = OpenCodeCliProvider._live_event_summary(
        _REAL_CREDIT_ERROR_EVENT, phase="gather", step="gather", staging_dir=staging
    )
    assert level == "error"
    assert "requires more credits" in message
    assert "_audit/opencode_errors.json" in message
    assert "details redacted" not in message


def test_step_failure_persists_the_raw_event_and_returns_a_short_reason(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()

    reason = OpenCodeCliProvider._record_step_failure(
        staging,
        phase="gather",
        step="gather",
        model_route="openrouter/stealth/ox-alpha",
        returncode=1,
        error_events=[_REAL_CREDIT_ERROR_EVENT],
        stderr_text="some stderr\n",
    )
    assert "requires more credits" in reason

    payload = json.loads((staging / "_audit" / "opencode_errors.json").read_text())
    assert len(payload) == 1
    record = payload[0]
    assert record["phase"] == "gather"
    assert record["returncode"] == 1
    assert record["model_route"] == "openrouter/stealth/ox-alpha"
    assert record["stderr"] == "some stderr\n"
    # The FULL event survives, not the normalized content-free form.
    assert record["error_events"][0] == _REAL_CREDIT_ERROR_EVENT


def test_step_failure_falls_back_to_stderr_when_no_error_event_was_emitted(tmp_path):
    """The observed ettm_elastic failure raised on returncode, not on an event."""
    staging = tmp_path / "staging"
    staging.mkdir()

    reason = OpenCodeCliProvider._record_step_failure(
        staging,
        phase="gather",
        step="gather",
        model_route="openrouter/stealth/ox-alpha",
        returncode=1,
        error_events=[],
        stderr_text="opencode: unknown model\n",
    )
    assert reason == "opencode: unknown model"
    record = json.loads((staging / "_audit" / "opencode_errors.json").read_text())[0]
    assert record["error_event_count"] == 0
    assert "unknown model" in record["stderr"]


def test_step_failures_accumulate_across_steps(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    for step in ("gather", "analyze"):
        OpenCodeCliProvider._record_step_failure(
            staging,
            phase=step,
            step=step,
            model_route="r",
            returncode=1,
            error_events=[],
            stderr_text=f"{step} broke",
        )
    payload = json.loads((staging / "_audit" / "opencode_errors.json").read_text())
    assert [r["step"] for r in payload] == ["gather", "analyze"]


def test_recording_a_failure_never_masks_the_failure_it_describes(tmp_path):
    """A corrupt or unwritable artifact must not replace the real exception."""
    staging = tmp_path / "staging"
    (staging / "_audit").mkdir(parents=True)
    (staging / "_audit" / "opencode_errors.json").write_text("{ not json")

    reason = OpenCodeCliProvider._record_step_failure(
        staging,
        phase="gather",
        step="gather",
        model_route="r",
        returncode=1,
        error_events=[_REAL_CREDIT_ERROR_EVENT],
        stderr_text="",
    )
    assert "requires more credits" in reason


def test_failure_message_names_the_artifact_and_degrades_without_a_reason():
    with_reason = OpenCodeCliProvider._failure_message("OpenCode gather exited with code 1", "boom")
    assert with_reason == (
        "OpenCode gather exited with code 1: boom [see _audit/opencode_errors.json]"
    )
    without = OpenCodeCliProvider._failure_message("OpenCode gather exited with code 1", "")
    assert "no error event or stderr captured" in without
    assert "_audit/opencode_errors.json" in without


@pytest.mark.parametrize(
    "route",
    ["openrouter/stealth/ox-alpha", "openai/gpt-5.6-sol", "ollama/gemma3:4b", "opencode-go/kimi-k3"],
)
def test_every_route_gets_a_private_writable_data_and_state_home(monkeypatch, tmp_path, route):
    """Regression: only opencode-go used to redirect XDG_DATA_HOME.

    Every other route inherited the service account's ambient home, so a stray
    root-owned `opencode/log` or `opencode/locks` there killed the run with
    `PermissionDenied: FileSystem.open (.../opencode.log)`.
    """
    monkeypatch.setattr(config, "data_dir", tmp_path / "svc_data")
    monkeypatch.setattr(config, "opencode_go_api_key", "go-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    provider = OpenCodeCliProvider(model_route=route)

    phase_config = provider._phase_config(
        ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None
    )
    try:
        env = provider._execution_env(phase_config)
        data_home = Path(env["XDG_DATA_HOME"])
        state_home = Path(env["XDG_STATE_HOME"])
        for home in (data_home, state_home):
            assert home.is_dir()
            # Private to this invocation, not the shared account home.
            assert str(home).startswith(str(Path(phase_config.path).parent))
            probe = home / "probe"
            probe.write_text("x")
            probe.unlink()
        assert (data_home / "opencode").stat().st_mode & 0o777 == 0o700
    finally:
        phase_config.cleanup()


def test_cleanup_removes_the_private_homes_but_keeps_the_shared_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "data_dir", tmp_path / "svc_data")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    provider = OpenCodeCliProvider(model_route="openrouter/stealth/ox-alpha")

    phase_config = provider._phase_config(
        ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None
    )
    data_home = Path(phase_config.environment["XDG_DATA_HOME"])
    state_home = Path(phase_config.environment["XDG_STATE_HOME"])
    cache_home = Path(phase_config.environment["XDG_CACHE_HOME"])
    assert data_home.is_dir() and state_home.is_dir() and cache_home.is_dir()

    phase_config.cleanup()

    assert not data_home.exists()
    assert not state_home.exists()
    # models.json / the runtime binary are worth keeping between phases.
    assert cache_home.is_dir()


def test_shared_cache_lives_under_the_service_data_dir_and_is_reused(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "data_dir", tmp_path / "svc_data")
    first = OpenCodeCliProvider._shared_cache_home()
    second = OpenCodeCliProvider._shared_cache_home()
    assert first == second == tmp_path / "svc_data" / "opencode_cache"
    assert first.is_dir()
    # The write probe must not leave anything behind.
    assert list(first.iterdir()) == []


def test_preflight_fails_clearly_when_the_cache_dir_is_not_writable(monkeypatch, tmp_path):
    import os

    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    locked = tmp_path / "svc_data"
    (locked / "opencode_cache").mkdir(parents=True)
    (locked / "opencode_cache").chmod(0o500)
    monkeypatch.setattr(config, "data_dir", locked)
    monkeypatch.setattr(config, "opencode_version", "1.18.7")
    try:
        with pytest.raises(OpenCodeCompatibilityError, match="cache directory is not writable"):
            OpenCodeCliProvider()._preflight()
    finally:
        (locked / "opencode_cache").chmod(0o700)


def test_phase_config_reports_an_unusable_cache_instead_of_failing_mid_run(monkeypatch, tmp_path):
    import os

    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    locked = tmp_path / "svc_data"
    (locked / "opencode_cache").mkdir(parents=True)
    (locked / "opencode_cache").chmod(0o500)
    monkeypatch.setattr(config, "data_dir", locked)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    provider = OpenCodeCliProvider(model_route="openrouter/stealth/ox-alpha")
    try:
        with pytest.raises(OpenCodeCompatibilityError, match="cache directory is not usable"):
            provider._phase_config(
                ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None
            )
    finally:
        (locked / "opencode_cache").chmod(0o700)


def test_openrouter_route_passes_its_key_through_the_phase_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_DOC_GENERATION_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    provider = OpenCodeCliProvider(model_route="openrouter/stealth/ox-alpha")

    phase_config = provider._phase_config(
        ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None
    )
    try:
        assert phase_config.environment["OPENROUTER_API_KEY"] == "or-test-key"
        assert provider._execution_env(phase_config)["OPENROUTER_API_KEY"] == "or-test-key"
        # An env-credentialed route needs no auth file.
        assert not (Path(phase_config.environment["XDG_DATA_HOME"]) / "opencode/auth.json").exists()
    finally:
        phase_config.cleanup()


def test_openrouter_route_accepts_the_mcp_settings_key_name(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("MCP_DOC_GENERATION_OPENROUTER_API_KEY", "or-settings-key")
    provider = OpenCodeCliProvider(model_route="openrouter/stealth/ox-alpha")

    phase_config = provider._phase_config(
        ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None
    )
    try:
        # OpenCode's built-in provider reads the canonical name only.
        assert phase_config.environment["OPENROUTER_API_KEY"] == "or-settings-key"
        assert "MCP_DOC_GENERATION_OPENROUTER_API_KEY" not in phase_config.environment
    finally:
        phase_config.cleanup()


def test_openrouter_route_requires_a_key_before_the_session_starts(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MCP_DOC_GENERATION_OPENROUTER_API_KEY", raising=False)
    provider = OpenCodeCliProvider(model_route="openrouter/stealth/ox-alpha")

    with pytest.raises(OpenCodeCompatibilityError, match="OPENROUTER_API_KEY"):
        provider._phase_config(ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None)


def test_opencode_go_phase_requires_subscription_key(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "opencode_go_api_key", "")
    provider = OpenCodeCliProvider(model_route="opencode-go/kimi-k3")

    with pytest.raises(OpenCodeCompatibilityError, match="MCP_OPENCODE_GO_API_KEY"):
        provider._phase_config(ctx=_ctx(tmp_path), phase="analyze", contracts=[], runtime=None)


def test_preflight_surfaces_runtime_stderr_when_version_command_fails(monkeypatch):
    monkeypatch.setattr(config, "opencode_version", "1.18.7")
    monkeypatch.setattr(
        "mcp_server.providers.opencode_cli.shutil.which", lambda _: "/usr/bin/opencode"
    )
    monkeypatch.setattr(
        "mcp_server.providers.opencode_cli.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="EACCES: permission denied, mkdir '/nonexistent'\nstack trace",
        ),
    )
    with pytest.raises(OpenCodeCompatibilityError, match="EACCES: permission denied"):
        OpenCodeCliProvider()._preflight()


def test_command_uses_opencode_default_model_when_route_is_unset(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "opencode_model", "")
    monkeypatch.setattr(config, "opencode_primary_agent", "build")
    monkeypatch.setattr(config, "opencode_auto_approve", False)
    cmd = OpenCodeCliProvider()._command("/usr/bin/opencode", _ctx(tmp_path))
    assert "--model" not in cmd
    assert cmd[-2:] == ["--agent", "build"]


def test_phase_prompt_requires_contract_outputs_and_limits_connectors(tmp_path):
    provider = OpenCodeCliProvider()
    gather = provider._phase_prompt(
        "gather", "gather", "_transient/opencode/gather.json", 2, {"docs": ("search",)}
    )
    analyze = provider._phase_prompt(
        "analyze", "analyze", "_transient/opencode/analyze.json", 1, {}
    )
    assert "exact prompt without paraphrasing" in gather
    assert "Process the contracts sequentially in this one session" in gather
    assert "do not launch sub-agents" in gather
    assert "Do not use search, grep, glob" in gather
    assert "supplementary evidence" in gather
    assert "template_artifact" in gather
    assert "validation_command" in gather
    assert "supplementary evidence" not in analyze
    assert "Process the contracts sequentially in this one session" in analyze
    assert "do not launch sub-agents" in analyze
    assert "output_artifact" in analyze


def test_contract_index_exposes_workload_hints_without_copying_prompt(tmp_path):
    ctx = _ctx(tmp_path)
    evidence = ctx.staging_dir / "_preprocess" / "A.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("scanner evidence", encoding="utf-8")
    provider = OpenCodeCliProvider()
    contract = OpenCodeTaskContract(
        "analyze",
        "analysis:A",
        "authoritative:group_analysis_prompt",
        ("_preprocess/A.md",),
        "_analysis/A.md",
        prompt="exact renderer output",
    )

    index_path = provider._write_contracts(ctx, "analyze", [contract])
    index = json.loads((ctx.staging_dir / index_path).read_text())

    assert len(index) == 1
    assert index[0]["contract_bytes"] > 0
    assert index[0]["evidence_artifact_count"] == 1
    assert index[0]["evidence_bytes"] == len("scanner evidence")
    assert "prompt" not in index[0]


def test_resume_checkpoint_requires_outputs_and_gather_validation_receipts(tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contract = OpenCodeTaskContract(
        "gather",
        "investigation:A",
        "authoritative:gather",
        (),
        "_transient/raw/A.json",
        metadata={"validation_receipt": "_transient/receipts/A.json"},
    )

    assert provider._contracts_complete(ctx, "gather", [contract]) is False
    output = ctx.staging_dir / contract.output_artifact
    output.parent.mkdir(parents=True)
    output.write_text("{}", encoding="utf-8")
    assert provider._contracts_complete(ctx, "gather", [contract]) is False
    receipt = ctx.staging_dir / contract.metadata["validation_receipt"]
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    assert provider._contracts_complete(ctx, "gather", [contract]) is True


def test_installed_opencode_1187_help_matches_supported_command_contract():
    command = shutil.which("opencode")
    if command is None:
        pytest.skip("OpenCode is not installed in this test environment")
    result = subprocess.run([command, "run", "--help"], capture_output=True, text=True, check=True)
    help_text = result.stdout + result.stderr
    assert all(flag in help_text for flag in ("--model", "--agent", "--dir", "--session", "--auto"))
    assert "--stdin" not in help_text and "--no-retry" not in help_text


def test_real_json_event_shapes_are_parsed_without_fake_phase_events():
    provider = OpenCodeCliProvider()
    start = provider._parse_json_event(
        {"type": "step_start", "sessionID": "ses_1", "part": {"id": "p"}}
    )
    text = provider._parse_json_event(
        {"type": "text", "sessionID": "ses_1", "part": {"text": "hello"}}
    )
    assert start == {"type": "step_start", "session_id": "ses_1"}
    assert text == {"type": "text", "session_id": "ses_1"}
    assert "hello" not in json.dumps(text)
    assert provider._parse_json_event({"type": "step_finish", "sessionID": "ses_1"}) == {
        "type": "step_finish",
        "session_id": "ses_1",
    }
    assert provider._parse_json_event({"type": "phase_completed", "phase": "gather"}) is None
    assert provider._parse_json_event(
        {"type": "tool", "tool": "read", "state": {"status": "completed"}}
    ) == {"type": "tool", "status": "completed", "failed": False}
    assert provider._parse_json_event(
        {"type": "tool", "tool": "read", "state": {"status": "error"}}
    ) == {"type": "tool", "status": "error", "failed": True}


def test_step_finish_usage_is_collected_without_persisting_content():
    event = {
        "type": "step_finish",
        "part": {"tokens": {"input": 12, "output": 7}, "cost": 0.25},
    }
    assert OpenCodeCliProvider._event_usage(event) == (12, 7, 0.25)


def test_live_event_summary_reports_safe_tool_and_token_progress(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    tool_event = {
        "type": "tool",
        "tool": "read",
        "state": {
            "status": "completed",
            "input": {"filePath": str(staging / "evidence" / "task.md")},
            "output": "sensitive content must not be logged",
        },
    }
    message, level = OpenCodeCliProvider._live_event_summary(
        tool_event, phase="gather", step="gather", staging_dir=staging
    )
    assert message == "OpenCode gather/gather: tool read completed evidence/task.md"
    assert level == "info"
    assert "sensitive" not in message

    finish_event = {
        "type": "step_finish",
        "part": {
            "type": "step-finish",
            "tokens": {
                "input": 12,
                "output": 7,
                "reasoning": 3,
                "cache": {"read": 100, "write": 20},
            },
        },
    }
    message, level = OpenCodeCliProvider._live_event_summary(
        finish_event, phase="gather", step="gather", staging_dir=staging
    )
    assert "input=12 output=7 reasoning=3 cache_read=100 cache_write=20" in message
    assert level == "info"


def test_opencode_execution_is_written_to_legacy_provider_call_schema():
    state = SimpleNamespace(calls=[])
    execution = _PhaseExecution(
        session_id="ses_123",
        events=[],
        input_tokens=12,
        output_tokens=7,
        cost_usd=0.25,
        latency_ms=345,
    )

    OpenCodeCliProvider(model_route="openai/gpt-5.6-terra")._record_execution(
        state, "produce", "produce_topology", execution
    )

    call = state.calls[0]
    assert call.phase == "produce"
    assert call.target == "produce_topology"
    assert call.result.input_tokens == 12
    assert call.result.output_tokens == 7
    assert call.result.latency_ms == 345
    assert call.result.metadata["runtime"] == "opencode-cli"
    assert call.result.metadata["session_id"] == "ses_123"
    assert call.result.model_name == "openai/gpt-5.6-terra"


def _gather_contract(name: str) -> OpenCodeTaskContract:
    return OpenCodeTaskContract(
        "gather",
        f"investigation:{name}",
        "authoritative:gather",
        (f"_tasks/{name}.md", "facts_manifest.json"),
        f"_transient/raw/{name}.json",
        prompt=f"extract {name}",
        metadata={"validation_receipt": f"_transient/receipts/{name}.json"},
    )


def _analysis_contract(name: str) -> OpenCodeTaskContract:
    return OpenCodeTaskContract(
        "analyze",
        f"analysis:{name}",
        "authoritative:group_analysis_prompt",
        (f"_preprocess/{name}.md", f"_investigation/{name}.md"),
        f"_analysis/{name}.md",
        prompt=f"analyze {name}",
    )


def test_contract_batch_planner_packs_small_contracts_into_one_batch(tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contracts = [_gather_contract(f"task_{index}") for index in range(3)]
    target = sum(
        provider._estimate_contract_input_tokens(ctx, contract)
        for contract in contracts
    )

    batches = provider._plan_contract_batches(ctx, contracts, target_tokens=target)

    assert len(batches) == 1
    assert batches[0].contracts == tuple(contracts)
    assert batches[0].estimated_input_tokens <= target
    assert batches[0].over_target is False


def test_contract_batch_planner_scales_with_cost_and_is_deterministic(tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    small = [_gather_contract(f"task_{index}") for index in range(4)]
    target = sum(
        provider._estimate_contract_input_tokens(ctx, contract) for contract in small
    )
    small_batches = provider._plan_contract_batches(ctx, small, target_tokens=target)

    large = [
        OpenCodeTaskContract(
            contract.phase,
            contract.target,
            contract.prompt_artifact,
            contract.evidence_artifacts,
            contract.output_artifact,
            prompt="extract bounded evidence\n" + ("x" * 20_000),
            metadata=contract.metadata,
        )
        for contract in small
    ]
    first = provider._plan_contract_batches(ctx, large, target_tokens=target)
    second = provider._plan_contract_batches(ctx, large, target_tokens=target)

    assert len(small_batches) == 1
    assert len(first) > len(small_batches)
    assert [batch.estimated_input_tokens for batch in first] == [
        batch.estimated_input_tokens for batch in second
    ]
    assert [contract.target for batch in first for contract in batch.contracts] == [
        contract.target for contract in large
    ]


def test_contract_batch_planner_keeps_an_oversized_contract_alone(tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    oversized = OpenCodeTaskContract(
        "gather",
        "investigation:oversized",
        "authoritative:gather",
        (),
        "_transient/raw/oversized.json",
        prompt="x" * 50_000,
    )
    small = _gather_contract("small")
    target = provider._estimate_contract_input_tokens(ctx, small) * 2

    batches = provider._plan_contract_batches(
        ctx, [oversized, small], target_tokens=target
    )

    assert batches[0].contracts == (oversized,)
    assert batches[0].estimated_input_tokens > target
    assert batches[0].over_target is True
    assert batches[1].contracts == (small,)


def test_gather_batch_passes_multiple_contracts_to_one_fresh_session(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider(model_route="opencode/test")
    contracts = [_gather_contract("first"), _gather_contract("second")]
    target = sum(
        provider._estimate_contract_input_tokens(ctx, contract)
        for contract in contracts
    )
    batch = provider._plan_contract_batches(ctx, contracts, target_tokens=target)[0]
    captured: dict[str, object] = {}

    async def fake_execute_step(**kwargs):
        captured.update(kwargs)
        return _PhaseExecution(session_id="ses_gather_batch", events=[])

    monkeypatch.setattr(provider, "_execute_step", fake_execute_step)

    async def log_cb(*_args):
        return None

    step, execution = asyncio.run(
        provider._execute_gather_batch(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            batch=batch,
            allowlist={},
            ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=145),
            log_cb=log_cb,
        )
    )

    assert step.startswith("gather_batch_")
    assert execution.session_id == "ses_gather_batch"
    assert captured["phase"] == "gather"
    assert list(captured["contracts"]) == contracts
    assert captured["session_id"] is None


def test_gather_contracts_use_bounded_concurrency(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contracts = [_gather_contract(f"task_{index}") for index in range(5)]
    active = 0
    peak = 0
    completed: list[str] = []

    async def fake_execute_gather_batch(*, batch, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return f"gather_batch_{batch.index}", _PhaseExecution(
            session_id=f"ses_{batch.index}", events=[]
        )

    monkeypatch.setattr(config, "opencode_gather_concurrency", 2)
    monkeypatch.setattr(config, "opencode_batch_target_input_tokens", 1)
    monkeypatch.setattr(provider, "_execute_gather_batch", fake_execute_gather_batch)

    async def log_cb(*_args):
        return None

    asyncio.run(
        provider._execute_gather_contracts(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            contracts=contracts,
            allowlist={},
            ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=146),
            log_cb=log_cb,
            on_complete=lambda step, _execution: completed.append(step),
        )
    )

    assert peak == 2
    assert len(completed) == len(contracts)


def test_gather_resume_skips_each_validated_contract(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    ctx.resume_state = SimpleNamespace()
    provider = OpenCodeCliProvider()
    complete = _gather_contract("complete")
    unfinished = _gather_contract("unfinished")
    for artifact in (
        complete.output_artifact,
        str(complete.metadata["validation_receipt"]),
    ):
        path = ctx.staging_dir / artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    planned: list[str] = []
    invoked: list[str] = []
    logs: list[str] = []
    real_plan = provider._plan_contract_batches

    def capture_plan(plan_ctx, contracts, target_tokens=None):
        planned.extend(contract.target for contract in contracts)
        return real_plan(plan_ctx, contracts, target_tokens=target_tokens)

    async def fake_execute_gather_batch(*, batch, **_kwargs):
        invoked.extend(contract.target for contract in batch.contracts)
        return "gather_batch_1", _PhaseExecution(session_id="ses_unfinished", events=[])

    monkeypatch.setattr(provider, "_plan_contract_batches", capture_plan)
    monkeypatch.setattr(provider, "_execute_gather_batch", fake_execute_gather_batch)

    async def log_cb(_project_id, message, _level="info"):
        logs.append(message)

    asyncio.run(
        provider._execute_gather_contracts(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            contracts=[complete, unfinished],
            allowlist={},
            ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=147),
            log_cb=log_cb,
            on_complete=lambda _step, _execution: None,
        )
    )

    assert planned == invoked == [unfinished.target]
    assert any(complete.target in message and "skipping model work" in message for message in logs)


def test_gather_failure_preserves_completed_contract_and_cancels_siblings(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contracts = [_gather_contract(name) for name in ("accepted", "failed", "waiting")]
    completed: list[str] = []
    waiting_cancelled = False

    async def fake_execute_gather_batch(*, batch, **_kwargs):
        nonlocal waiting_cancelled
        target = batch.contracts[0].target
        if target.endswith("accepted"):
            return "gather_batch_1", _PhaseExecution(session_id="ses_accepted", events=[])
        if target.endswith("failed"):
            await asyncio.sleep(0.01)
            raise OpenCodeCompatibilityError("provider unavailable")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            waiting_cancelled = True
            raise

    monkeypatch.setattr(config, "opencode_gather_concurrency", 2)
    monkeypatch.setattr(config, "opencode_batch_target_input_tokens", 1)
    monkeypatch.setattr(provider, "_execute_gather_batch", fake_execute_gather_batch)

    async def log_cb(*_args):
        return None

    with pytest.raises(OpenCodeCompatibilityError, match="provider unavailable"):
        asyncio.run(
            provider._execute_gather_contracts(
                resolved="/usr/bin/opencode",
                ctx=ctx,
                contracts=contracts,
                allowlist={},
                ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=148),
                log_cb=log_cb,
                on_complete=lambda step, _execution: completed.append(step),
            )
        )

    assert completed == ["gather_batch_1"]
    assert waiting_cancelled is True


def test_analyze_batches_groups_with_bounded_concurrency_and_scorecard_barrier(
    monkeypatch, tmp_path
):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    groups = [_analysis_contract(name) for name in ("A", "B", "C", "D")]
    scorecard = OpenCodeTaskContract(
        "analyze",
        "scorecard",
        "authoritative:scorecard",
        tuple(contract.output_artifact for contract in groups),
        "_transient/opencode/scorecard_raw.md",
        prompt="score all groups",
    )
    per_group_cost = provider._estimate_contract_input_tokens(ctx, groups[0])
    monkeypatch.setattr(config, "opencode_batch_target_input_tokens", per_group_cost * 2)
    monkeypatch.setattr(config, "opencode_analyze_concurrency", 2)

    active = 0
    peak = 0
    completed_groups: list[str] = []
    group_batch_sizes: list[int] = []
    scorecard_observed_after_groups = False

    async def fake_execute_analyze_batch(*, batch, **_kwargs):
        nonlocal active, peak, scorecard_observed_after_groups
        targets = [contract.target for contract in batch.contracts]
        group_batch_sizes.append(len(batch.contracts))
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        completed_groups.extend(targets)
        for contract in batch.contracts:
            output = ctx.staging_dir / contract.output_artifact
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(f"# {contract.target}\n", encoding="utf-8")
        active -= 1
        return f"analyze_batch_{batch.index}", _PhaseExecution(
            session_id=f"ses_analyze_{batch.index}", events=[]
        )

    async def fake_execute_analyze_contract(*, contract, **_kwargs):
        nonlocal scorecard_observed_after_groups
        assert contract.target == "scorecard"
        scorecard_observed_after_groups = set(completed_groups) == {
            group.target for group in groups
        }
        output = ctx.staging_dir / contract.output_artifact
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# Scorecard\n", encoding="utf-8")
        return "analyze_scorecard", _PhaseExecution(
            session_id="ses_scorecard", events=[]
        )

    monkeypatch.setattr(provider, "_execute_analyze_batch", fake_execute_analyze_batch)
    monkeypatch.setattr(
        provider, "_execute_analyze_contract", fake_execute_analyze_contract
    )

    async def log_cb(*_args):
        return None

    recorded: list[str] = []
    asyncio.run(
        provider._execute_analyze_contracts(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            contracts=[*groups, scorecard],
            allowlist={},
            ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=149),
            log_cb=log_cb,
            on_complete=lambda step, _execution: recorded.append(step),
        )
    )

    assert group_batch_sizes == [2, 2]
    assert peak == 2
    assert scorecard_observed_after_groups is True
    assert recorded[-1] == "analyze_scorecard"


def test_analyze_partial_batch_recovers_only_missing_contract(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contracts = [_analysis_contract("A"), _analysis_contract("B")]
    monkeypatch.setattr(config, "opencode_batch_target_input_tokens", 10**9)
    monkeypatch.setattr(config, "opencode_phase_max_followups", 1)
    initial_targets: list[str] = []
    recovery_targets: list[str] = []

    async def fake_batch(*, batch, **_kwargs):
        initial_targets.extend(contract.target for contract in batch.contracts)
        output = ctx.staging_dir / batch.contracts[0].output_artifact
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# A analysis\n", encoding="utf-8")
        return "analyze_batch_001", _PhaseExecution(session_id="initial", events=[])

    async def fake_step(**kwargs):
        contract = kwargs["contracts"][0]
        recovery_targets.append(contract.target)
        output = ctx.staging_dir / contract.output_artifact
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# B analysis\n", encoding="utf-8")
        return _PhaseExecution(session_id="recovery", events=[])

    monkeypatch.setattr(provider, "_execute_analyze_batch", fake_batch)
    monkeypatch.setattr(provider, "_execute_step", fake_step)

    async def log_cb(*_args):
        return None

    recorded: list[str] = []
    asyncio.run(
        provider._execute_analyze_contracts(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            contracts=contracts,
            allowlist={},
            ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=151),
            log_cb=log_cb,
            on_complete=lambda step, _execution: recorded.append(step),
        )
    )

    assert initial_targets == ["analysis:A", "analysis:B"]
    assert recovery_targets == ["analysis:B"]
    assert (ctx.staging_dir / "_analysis/A.md").read_text() == "# A analysis\n"
    assert (ctx.staging_dir / "_analysis/B.md").read_text() == "# B analysis\n"
    assert len(recorded) == 2


def test_analyze_recovery_is_bounded_and_names_exact_missing_output(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contract = _analysis_contract("Missing")
    monkeypatch.setattr(config, "opencode_phase_max_followups", 1)

    async def fake_batch(**_kwargs):
        return "analyze_batch_001", _PhaseExecution(session_id="initial", events=[])

    recovery_calls = 0

    async def fake_step(**_kwargs):
        nonlocal recovery_calls
        recovery_calls += 1
        return _PhaseExecution(session_id="recovery", events=[])

    monkeypatch.setattr(provider, "_execute_analyze_batch", fake_batch)
    monkeypatch.setattr(provider, "_execute_step", fake_step)

    async def log_cb(*_args):
        return None

    with pytest.raises(OpenCodeCompatibilityError, match=r"_analysis/Missing\.md"):
        asyncio.run(
            provider._execute_analyze_contracts(
                resolved="/usr/bin/opencode",
                ctx=ctx,
                contracts=[contract],
                allowlist={},
                ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=152),
                log_cb=log_cb,
                on_complete=lambda *_args: None,
            )
        )
    assert recovery_calls == 1


def test_analyze_partial_batch_does_not_cancel_healthy_sibling(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contracts = [_analysis_contract("A"), _analysis_contract("B")]
    monkeypatch.setattr(config, "opencode_batch_target_input_tokens", 1)
    monkeypatch.setattr(config, "opencode_phase_max_followups", 1)
    sibling_completed = False
    sibling_cancelled = False

    async def fake_batch(*, batch, **_kwargs):
        nonlocal sibling_completed, sibling_cancelled
        contract = batch.contracts[0]
        if contract.target == "analysis:A":
            raise OpenCodeCompatibilityError("A session stopped early")
        try:
            await asyncio.sleep(0.02)
            output = ctx.staging_dir / contract.output_artifact
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("# B analysis\n", encoding="utf-8")
            sibling_completed = True
        except asyncio.CancelledError:
            sibling_cancelled = True
            raise
        return "analyze_batch_002", _PhaseExecution(session_id="sibling", events=[])

    async def fake_step(**kwargs):
        contract = kwargs["contracts"][0]
        output = ctx.staging_dir / contract.output_artifact
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# A recovered\n", encoding="utf-8")
        return _PhaseExecution(session_id="recovery", events=[])

    monkeypatch.setattr(provider, "_execute_analyze_batch", fake_batch)
    monkeypatch.setattr(provider, "_execute_step", fake_step)

    async def log_cb(*_args):
        return None

    asyncio.run(
        provider._execute_analyze_contracts(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            contracts=contracts,
            allowlist={},
            ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=154),
            log_cb=log_cb,
            on_complete=lambda *_args: None,
        )
    )

    assert sibling_completed is True
    assert sibling_cancelled is False
    assert (ctx.staging_dir / "_analysis/A.md").is_file()
    assert (ctx.staging_dir / "_analysis/B.md").is_file()


def test_opencode_usage_receipts_persist_model_usage_before_validation(tmp_path):
    audit_dir = tmp_path / "_audit"
    audit_dir.mkdir()
    receipts = [
        {
            "phase": "gather",
            "step": "gather",
            "status": "model_completed",
            "session_id": "ses_123",
            "input_tokens": 120,
            "output_tokens": 45,
            "cost_usd": 0.01,
            "latency_ms": 345,
        }
    ]

    OpenCodeCliProvider._write_usage_receipts(audit_dir, receipts)

    assert json.loads((audit_dir / "opencode_usage.json").read_text()) == receipts


def test_gather_fingerprint_tracks_evidence_not_retry_bookkeeping(tmp_path):
    ctx = _ctx(tmp_path)
    state_path = ctx.staging_dir / "_evidence/scopes/scope-a/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "status": "complete",
                "evidence": [],
                "gaps": {"UnitA": "attempt exhausted"},
            }
        ),
        encoding="utf-8",
    )
    original = OpenCodeCliProvider._gather_evidence_fingerprint(ctx)
    state_path.write_text(
        json.dumps(
            {
                "version": 2,
                "status": "open",
                "evidence": [],
                "gaps": {"UnitA": "attempt exhausted"},
            }
        ),
        encoding="utf-8",
    )
    assert OpenCodeCliProvider._gather_evidence_fingerprint(ctx) == original
    state_path.write_text(
        json.dumps(
            {
                "version": 3,
                "status": "complete",
                "evidence": [{"unit_name": "UnitA", "observation": "supported"}],
                "gaps": {},
            }
        ),
        encoding="utf-8",
    )
    assert OpenCodeCliProvider._gather_evidence_fingerprint(ctx) != original


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="p",
        platform="generic",
        canonical_unit_label="code units",
        unit_count=1,
        groups={"A": 1},
        units=[UnitFact(name="UnitA", group="A", path="UnitA.py", unit_type="code_unit")],
    )


def test_gather_output_is_materialized_as_legacy_investigation_artifacts(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    preprocess = ctx.staging_dir / "_preprocess"
    preprocess.mkdir()
    evidence_text = "## Artifact: UnitA\nconnection route maps customer records safely\n"
    (preprocess / "A.md").write_text(evidence_text * 20, encoding="utf-8")
    ctx.preprocess_groups = ["A"]
    facts = _facts()
    provider = OpenCodeCliProvider()
    harness = provider._legacy_harness(facts)
    state = HarnessRunState(project_id="p", facts_manifest=facts)
    monkeypatch.setattr(config, "small_project_threshold", 0)
    monkeypatch.setattr(config, "min_investigation_content_bytes", 1)
    contracts = provider._gather_contracts(ctx, facts, harness, {})
    assert len(contracts) == 1
    contract = contracts[0]
    template = ctx.staging_dir / contract.metadata["template_artifact"]
    spec = ctx.staging_dir / contract.metadata["validation_spec"]
    assert json.loads(template.read_text()) == {"evidence": [], "skipped_units": {}}
    validation_spec = json.loads(spec.read_text())
    assert validation_spec["task_id"] == contract.metadata["task_id"]
    assert validation_spec["allowed_units"] == ["UnitA"]
    assert validation_spec["source_ref"] == contract.metadata["source_ref"]
    assert validation_spec["validation_receipt"] == contract.metadata["validation_receipt"]
    assert "validate_gather_output.py" in contract.metadata["validation_command"]
    raw_path = ctx.staging_dir / contract.output_artifact
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        json.dumps(
            {
                "evidence": [
                    {
                        "unit_name": "UnitA",
                        "category": "data flow",
                        "source_file": contract.metadata["source_ref"],
                        "chunk_ref": contract.metadata["source_ref"],
                        "line_range": "1-2",
                        "snippet": "connection route maps customer records safely",
                        "observation": "UnitA maps customer records through the evidenced connection route.",
                    }
                ],
                "skipped_units": {},
            }
        ),
        encoding="utf-8",
    )
    provider._finalize_gather(ctx, facts, harness, state, contracts)
    assert (ctx.staging_dir / "_investigation" / "A.json").exists()
    coverage = json.loads(
        (ctx.staging_dir / "_investigation" / "_coverage_report.json").read_text()
    )
    assert coverage["ok"] is True and coverage["coverage_count"] == 1


def test_analyze_handoff_builds_doc_packs_and_topology_evidence(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    ctx.preprocess_groups = ["A"]
    preprocess = ctx.staging_dir / "_preprocess"
    preprocess.mkdir()
    (preprocess / "A.md").write_text(
        ("## Artifact: UnitA\nconnection route maps customer records safely\n" * 100),
        encoding="utf-8",
    )
    investigation = ctx.staging_dir / "_investigation"
    investigation.mkdir()
    (investigation / "A.md").write_text(
        "# Investigation Evidence: A\n\nUnitA connection route evidence.\n",
        encoding="utf-8",
    )
    analysis = ctx.staging_dir / "_analysis"
    analysis.mkdir()
    (analysis / "A.md").write_text(
        "### COMPONENTS\n\nUnitA maps customer records.\n\n"
        + ("UnitA connection route evidence and implementation detail.\n" * 120)
        + "\n### GROUP_SUMMARY\n\nUnitA summary.\n",
        encoding="utf-8",
    )
    (ctx.staging_dir / "_assessment").mkdir()

    scan_dir = tmp_path / "intodoc" / "p"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "generic",
                "repo_root": "/repo",
                "files": [],
                "jobs": [
                    {
                        "name": "UnitA",
                        "path": "UnitA.py",
                        "group": "A",
                        "components": [],
                    }
                ],
                "manifest": {},
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", False)
    facts = _facts()
    provider = OpenCodeCliProvider()
    harness = provider._legacy_harness(facts)
    state = HarnessRunState(project_id="p", facts_manifest=facts)

    async def log_cb(*_args):
        return None

    asyncio.run(provider._finalize_analyze(ctx, facts, harness, state, log_cb))
    asyncio.run(provider._finalize_analyze(ctx, facts, harness, state, log_cb))
    assert (analysis / "_doc_pack_manifest.json").exists()
    assert (ctx.staging_dir / "documentation_obligations.json").exists()
    assert (ctx.staging_dir / "_topology" / "factual_evidence_index.json").exists()
    assert (analysis / "A.md").read_text().count("# Phase 2 Analysis: A") == 1


def test_real_subprocess_adapter_continues_one_session_and_verifies_outputs(monkeypatch, tmp_path):
    executable = tmp_path / "fake-opencode"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, re, sys\n"
        "prompt = sys.stdin.read()\n"
        "match = re.search(r'Read ([^;]+);', prompt)\n"
        "root = pathlib.Path.cwd()\n"
        "index = json.loads((root / match.group(1)).read_text())\n"
        "for entry in index:\n"
        "    contract = json.loads((root / entry['contract_artifact']).read_text())\n"
        "    output = root / contract['output_artifact']\n"
        "    output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    output.write_text('ok')\n"
        "args = sys.argv[1:]\n"
        "session = args[args.index('--session') + 1] if '--session' in args else 'ses_real'\n"
        "print(json.dumps({'type':'step_start','sessionID':session,'part':{'type':'step-start'}}))\n"
        "print(json.dumps({'type':'step_finish','sessionID':session,'part':{'type':'step-finish','tokens':{'input':2,'output':1},'cost':0}}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(config, "opencode_model", "opencode/test")
    monkeypatch.setattr(config, "opencode_primary_agent", "build")
    monkeypatch.setattr(config, "opencode_auto_approve", True)
    ctx = _ctx(tmp_path / "run")
    provider = OpenCodeCliProvider(command=str(executable))
    ledger = OpenCodeAttemptLedger(ctx.staging_dir)
    live_logs: list[str] = []

    async def log_cb(_project_id, message, _level="info"):
        live_logs.append(message)

    first_contract = OpenCodeTaskContract(
        "gather", "first", "test", (), "first.txt", prompt="write first"
    )
    first = asyncio.run(
        provider._execute_step(
            resolved=str(executable),
            ctx=ctx,
            phase="gather",
            step="gather",
            contracts=[first_contract],
            allowlist={},
            session_id=None,
            ledger=ledger,
            log_cb=log_cb,
        )
    )
    second_contract = OpenCodeTaskContract(
        "analyze", "second", "test", (), "second.txt", prompt="write second"
    )
    second = asyncio.run(
        provider._execute_step(
            resolved=str(executable),
            ctx=ctx,
            phase="analyze",
            step="analyze",
            contracts=[second_contract],
            allowlist={},
            session_id=first.session_id,
            ledger=ledger,
            log_cb=log_cb,
        )
    )
    assert first.session_id == second.session_id == "ses_real"
    assert (ctx.staging_dir / "first.txt").read_text() == "ok"
    assert (ctx.staging_dir / "second.txt").read_text() == "ok"
    assert first.input_tokens == 2 and second.output_tokens == 1
    assert any("model turn started" in message for message in live_logs)
    assert any("input=2 output=1" in message for message in live_logs)


def test_produce_batches_always_start_fresh_artifact_driven_sessions(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    ledger = OpenCodeAttemptLedger(ctx.staging_dir)
    supplied_session_ids: list[str | None] = []

    async def fake_execute_step(**kwargs):
        supplied_session_ids.append(kwargs["session_id"])
        return _PhaseExecution(
            session_id=f"ses_{len(supplied_session_ids)}",
            events=[],
        )

    monkeypatch.setattr(provider, "_execute_step", fake_execute_step)

    async def log_cb(*_args):
        return None

    async def scenario():
        for index, step in enumerate(
            ("produce_group_docs", "produce_executive_summary", "produce_topology")
        ):
            contract = OpenCodeTaskContract(
                "produce",
                step,
                "test",
                (),
                f"output_{index}.md",
                prompt="write",
            )
            await provider._execute_produce_batch(
                resolved="/usr/bin/opencode",
                ctx=ctx,
                step=step,
                contracts=[contract],
                allowlist={},
                ledger=ledger,
                log_cb=log_cb,
            )

    asyncio.run(scenario())

    assert supplied_session_ids == [None, None, None]


def test_produce_partial_batch_recovers_only_missing_documents(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contracts = [
        OpenCodeTaskContract(
            "produce",
            f"document:D{index}",
            "test",
            (),
            f"D{index}.md",
            prompt=f"write D{index}",
        )
        for index in range(3)
    ]
    monkeypatch.setattr(config, "opencode_batch_target_input_tokens", 10**9)
    monkeypatch.setattr(config, "opencode_phase_max_followups", 1)
    initial_batches: list[list[str]] = []
    recovered: list[str] = []

    async def fake_produce_batch(*, contracts, **_kwargs):
        initial_batches.append([contract.target for contract in contracts])
        first = ctx.staging_dir / contracts[0].output_artifact
        first.write_text("# D0\n", encoding="utf-8")
        return _PhaseExecution(session_id="initial", events=[])

    async def fake_step(**kwargs):
        contract = kwargs["contracts"][0]
        recovered.append(contract.target)
        (ctx.staging_dir / contract.output_artifact).write_text(
            f"# {contract.target}\n", encoding="utf-8"
        )
        return _PhaseExecution(session_id=f"recovery-{contract.target}", events=[])

    monkeypatch.setattr(provider, "_execute_produce_batch", fake_produce_batch)
    monkeypatch.setattr(provider, "_execute_step", fake_step)

    async def log_cb(*_args):
        return None

    asyncio.run(
        provider._execute_produce_contracts(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            step="produce_group_docs",
            contracts=contracts,
            allowlist={},
            ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=153),
            log_cb=log_cb,
            on_complete=lambda *_args: None,
        )
    )

    assert initial_batches == [["document:D0", "document:D1", "document:D2"]]
    assert set(recovered) == {"document:D1", "document:D2"}
    assert (ctx.staging_dir / "D0.md").read_text() == "# D0\n"
    assert all((ctx.staging_dir / f"D{index}.md").is_file() for index in range(3))


def test_produce_recovery_is_bounded_and_names_exact_missing_output(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    provider = OpenCodeCliProvider()
    contract = OpenCodeTaskContract(
        "produce",
        "document:Missing",
        "test",
        (),
        "Missing.md",
        prompt="write Missing",
    )
    monkeypatch.setattr(config, "opencode_phase_max_followups", 1)
    recovery_calls = 0

    async def fake_produce_batch(**_kwargs):
        return _PhaseExecution(session_id="initial", events=[])

    async def fake_step(**_kwargs):
        nonlocal recovery_calls
        recovery_calls += 1
        return _PhaseExecution(session_id="recovery", events=[])

    monkeypatch.setattr(provider, "_execute_produce_batch", fake_produce_batch)
    monkeypatch.setattr(provider, "_execute_step", fake_step)

    async def log_cb(*_args):
        return None

    with pytest.raises(OpenCodeCompatibilityError, match=r"Missing\.md"):
        asyncio.run(
            provider._execute_produce_contracts(
                resolved="/usr/bin/opencode",
                ctx=ctx,
                step="produce_group_docs",
                contracts=[contract],
                allowlist={},
                ledger=OpenCodeAttemptLedger(ctx.staging_dir, run_id=155),
                log_cb=log_cb,
                on_complete=lambda *_args: None,
            )
        )
    assert recovery_calls == 1


def test_produce_contract_repair_fixes_invalid_checkpoint(monkeypatch, tmp_path):
    ctx = _ctx(tmp_path)
    facts = _facts()
    provider = OpenCodeCliProvider()
    ledger = OpenCodeAttemptLedger(ctx.staging_dir)
    doc_name = "04_BUSINESS_RULES_AND_TRANSFORMATIONS"
    doc_path = ctx.staging_dir / f"{doc_name}.md"
    doc_path.write_text(
        "# Business rules\n\nRule | Unit | Impact\n--- | --- | ---\nR1 | UnitA | Creates order\n",
        encoding="utf-8",
    )
    original = OpenCodeTaskContract(
        phase="produce",
        target=f"document:{doc_name}",
        prompt_artifact="test",
        evidence_artifacts=("facts_manifest.json",),
        output_artifact=f"{doc_name}.md",
        prompt="write",
    )
    monkeypatch.setattr(provider, "_doc_contract", lambda *_args: original)
    captured: dict[str, object] = {}

    async def fake_execute_produce_batch(**kwargs):
        captured.update(kwargs)
        doc_path.write_text(
            "# Business rules\n\n"
            "Rule | Expression | Unit | Domain | Impact\n"
            "--- | --- | --- | --- | ---\n"
            "R1 | status = ready | UnitA | Ordering | Creates order\n",
            encoding="utf-8",
        )
        return _PhaseExecution(session_id="ses_repair", events=[])

    monkeypatch.setattr(provider, "_execute_produce_batch", fake_execute_produce_batch)
    messages: list[str] = []

    async def log_cb(_project_id, message, _level="info"):
        messages.append(message)

    executions = asyncio.run(
        provider._repair_produce_documents(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            facts=facts,
            harness=SimpleNamespace(),
            repair_step="produce_group_docs_contract_repair",
            doc_names=[doc_name],
            allowlist={},
            ledger=ledger,
            log_cb=log_cb,
        )
    )

    assert len(executions) == 1 and executions[0][1].session_id == "ses_repair"
    assert str(captured["step"]).startswith("produce_group_docs_contract_repair_")
    repair_contract = captured["contracts"][0]
    assert f"{doc_name}.md" in repair_contract.evidence_artifacts
    assert "Expression" in repair_contract.prompt
    assert "Domain" in repair_contract.prompt
    assert provider._document_contract_errors(ctx, facts, [doc_name]) == {}
    assert any("targeted repair session" in message for message in messages)


def test_subprocess_progress_is_streamed_before_process_exit(monkeypatch, tmp_path):
    executable = tmp_path / "streaming-opencode"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, re, sys, time\n"
        "prompt = sys.stdin.read()\n"
        "match = re.search(r'Read ([^;]+);', prompt)\n"
        "root = pathlib.Path.cwd()\n"
        "index = json.loads((root / match.group(1)).read_text())\n"
        "print(json.dumps({'type':'step_start','sessionID':'ses_stream'}), flush=True)\n"
        "time.sleep(0.5)\n"
        "contract = json.loads((root / index[0]['contract_artifact']).read_text())\n"
        "output = root / contract['output_artifact']\n"
        "output.parent.mkdir(parents=True, exist_ok=True)\n"
        "output.write_text('ok')\n"
        "print(json.dumps({'type':'step_finish','sessionID':'ses_stream','part':{'tokens':{'input':2,'output':1}}}), flush=True)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(config, "opencode_model", "opencode/test")
    monkeypatch.setattr(config, "opencode_primary_agent", "build")
    monkeypatch.setattr(config, "opencode_auto_approve", True)
    ctx = _ctx(tmp_path / "run")
    provider = OpenCodeCliProvider(command=str(executable))
    ledger = OpenCodeAttemptLedger(ctx.staging_dir)

    async def scenario():
        progress_seen = asyncio.Event()

        async def log_cb(_project_id, message, _level="info"):
            if "model turn started" in message:
                progress_seen.set()

        run_task = asyncio.create_task(
            provider._execute_step(
                resolved=str(executable),
                ctx=ctx,
                phase="analyze",
                step="analyze",
                contracts=[
                    OpenCodeTaskContract(
                        "analyze", "stream", "test", (), "stream.txt", prompt="write"
                    )
                ],
                allowlist={},
                session_id=None,
                ledger=ledger,
                log_cb=log_cb,
            )
        )
        await asyncio.wait_for(progress_seen.wait(), timeout=2)
        assert not run_task.done()
        result = await run_task
        assert result.session_id == "ses_stream"

    asyncio.run(scenario())


def test_both_step_finish_event_shapes_are_charged_to_the_budget():
    """A usage ceiling that sees half the turns is not a ceiling.

    Regression from a real `opencode-go/gpt-5.6-luna` session: 19 turns were
    written to the live log and only 11 were charged, because the accounting
    keyed on a top-level `type: "step_finish"` while the log also accepted the
    nested `part.type: "step-finish"`. The run-level processed-token limit
    therefore read 1,586,524 of an actual 3,157,666.
    """
    from mcp_server.providers.opencode_cli import _is_turn_finished

    top_level = {"type": "step_finish", "part": {"tokens": {"input": 1, "output": 2}}}
    nested = {"type": "message.part.updated",
              "part": {"type": "step-finish", "tokens": {"input": 3, "output": 4}}}
    assert _is_turn_finished(top_level) is True
    assert _is_turn_finished(nested) is True
    # Anything else must not be charged.
    for other in (
        {"type": "step_start", "part": {"type": "step-start"}},
        {"type": "text", "part": {"type": "text"}},
        {"type": "error"},
        {"part": None},
        {},
    ):
        assert _is_turn_finished(other) is False, other


def test_turn_usage_is_read_from_either_shape():
    from mcp_server.providers.opencode_cli import OpenCodeCliProvider

    nested = {"type": "message.part.updated", "part": {
        "type": "step-finish",
        "tokens": {"input": 11, "output": 22, "reasoning": 3, "cache": {"read": 100, "write": 50}},
        "cost": 0.5,
    }}
    assert OpenCodeCliProvider._event_usage(nested) == (11, 22, 0.5)
