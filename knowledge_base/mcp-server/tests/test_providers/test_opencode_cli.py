from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.claude_cli import ClaudeCliProvider
from mcp_server.providers.opencode_cli import OpenCodeCliProvider


def _ctx(tmp_path: Path) -> DocGenContext:
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    return DocGenContext(
        project_id="parity", staging_dir=staging, staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"), preprocess_groups=[], total_jobs=0,
        total_groups=0, effective_model="openai/gpt-5.6-terra",
        write_model="openai/gpt-5.6-terra", platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )


def test_opencode_prompt_is_byte_identical_to_claude_prompt(tmp_path: Path):
    ctx = _ctx(tmp_path)
    assert OpenCodeCliProvider._build_prompt(ctx) == ClaudeCliProvider._build_prompt(ctx)


def test_opencode_command_is_one_json_session(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config, "opencode_model", "openai/gpt-5.6-terra")
    monkeypatch.setattr(config, "opencode_primary_agent", "build")
    monkeypatch.setattr(config, "opencode_auto_approve", True)
    assert OpenCodeCliProvider()._command("/usr/bin/opencode", _ctx(tmp_path)) == [
        "/usr/bin/opencode", "run", "--format", "json", "--model",
        "openai/gpt-5.6-terra", "--dir", str(tmp_path / "staging"),
        "--agent", "build", "--auto",
    ]


def test_usage_accepts_top_level_and_part_event_shapes():
    assert OpenCodeCliProvider._usage({"usage": {"input": 12, "output": 7, "cost": 0.25}}) == (12, 7, 0.25)
    assert OpenCodeCliProvider._usage({"part": {"tokens": {"inputTokens": 9, "outputTokens": 3}}}) == (9, 3, 0.0)
    assert OpenCodeCliProvider._usage({"part": {"type": "step-finish", "tokens": {"input": 4, "output": 2}, "cost": 0.1}}) == (4, 2, 0.1)


def test_json_event_errors_are_visible_to_operator_logs():
    message, level = OpenCodeCliProvider._event_summary({"type": "error", "message": "provider rejected request"})
    assert level == "error"
    assert message == "OpenCode error: provider rejected request"


def test_fake_cli_receives_the_canonical_prompt_once_on_stdin(monkeypatch, tmp_path: Path):
    ctx = _ctx(tmp_path)
    capture = tmp_path / "prompt.txt"
    executable = tmp_path / "fake-opencode"
    executable.write_text(
        "#!/bin/sh\ncat > \"$CAPTURE_PROMPT\"\nprintf '%s\\n' "
        "'{\"type\":\"step_finish\",\"sessionID\":\"ses_test\",\"usage\":{\"input\":2,\"output\":1}}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("CAPTURE_PROMPT", str(capture))
    monkeypatch.setattr(config, "opencode_timeout_seconds", 10)
    provider = OpenCodeCliProvider(command=str(executable))
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))

    logged: list[str] = []
    async def log_cb(_project_id: str, message: str, _level: str):
        logged.append(message)

    result = asyncio.run(provider.run(ctx, log_cb))
    assert result.success
    assert result.session_id == "ses_test"
    assert capture.read_text(encoding="utf-8") == ClaudeCliProvider._build_prompt(ctx)
    assert result.num_turns == 1


def test_transport_materializes_go_auth_without_exposing_key(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    monkeypatch.setattr(config, "opencode_go_api_key", "go-secret")
    provider = OpenCodeCliProvider(model_route="opencode-go/kimi-k3")
    env = provider._execution_env(tmp_path / "runtime")
    auth_path = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
    assert json.loads(auth_path.read_text()) == {"opencode-go": {"type": "api", "key": "go-secret"}}
    assert auth_path.stat().st_mode & 0o777 == 0o600
    assert "go-secret" not in env.values()


def test_transport_preserves_config_and_builds_route_credentials(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    monkeypatch.setattr(config, "opencode_config_path", "/operator/opencode.json")
    monkeypatch.setenv("MCP_DOC_GENERATION_OPENROUTER_API_KEY", "router-secret")
    env = OpenCodeCliProvider(model_route="openrouter/test")._execution_env(tmp_path / "runtime")
    assert env["OPENCODE_CONFIG"] == "/operator/opencode.json"
    assert env["OPENROUTER_API_KEY"] == "router-secret"


def test_transport_merges_ollama_provider_config(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    monkeypatch.setattr(config, "opencode_ollama_base_url", "http://ollama.test/v1")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps({"custom": {"keep": True}}))
    env = OpenCodeCliProvider(model_route="ollama/qwen3")._execution_env(tmp_path / "runtime")
    payload = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert payload["custom"] == {"keep": True}
    assert payload["provider"]["ollama"]["options"]["baseURL"] == "http://ollama.test/v1"
    assert payload["provider"]["ollama"]["models"]["qwen3"] == {"name": "qwen3"}


def test_timeout_terminates_and_reaps_child(monkeypatch, tmp_path: Path):
    ctx = _ctx(tmp_path)
    pid_path = tmp_path / "child.pid"
    executable = tmp_path / "hanging-opencode"
    executable.write_text(
        "#!/bin/sh\necho $$ > \"$CHILD_PID_FILE\"\ntrap 'exit 0' TERM\nwhile :; do sleep 1; done\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("CHILD_PID_FILE", str(pid_path))
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    monkeypatch.setattr(config, "opencode_timeout_seconds", 1)
    provider = OpenCodeCliProvider(command=str(executable))
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))

    async def log_cb(_project_id: str, _message: str, _level: str):
        return None

    result = asyncio.run(provider.run(ctx, log_cb))
    assert not result.success and "timed out" in (result.error or "")
    child_pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_cancellation_reaps_child_without_unretrieved_gather(monkeypatch, tmp_path: Path):
    ctx = _ctx(tmp_path)
    pid_path = tmp_path / "cancelled-child.pid"
    executable = tmp_path / "cancelled-opencode"
    executable.write_text(
        "#!/bin/sh\necho $$ > \"$CHILD_PID_FILE\"\ntrap 'exit 0' TERM\nwhile :; do sleep 1; done\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("CHILD_PID_FILE", str(pid_path))
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    provider = OpenCodeCliProvider(command=str(executable))
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))

    async def log_cb(_project_id: str, _message: str, _level: str):
        return None

    async def scenario() -> list[dict]:
        loop = asyncio.get_running_loop()
        errors: list[dict] = []
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        task = asyncio.create_task(provider.run(ctx, log_cb))
        for _ in range(100):
            if pid_path.exists():
                break
            await asyncio.sleep(0.01)
        assert pid_path.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        return errors

    assert asyncio.run(scenario()) == []
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_path.read_text()), 0)


def test_nested_error_event_fails_an_otherwise_zero_exit(monkeypatch, tmp_path: Path):
    ctx = _ctx(tmp_path)
    executable = tmp_path / "error-opencode"
    executable.write_text(
        "#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' "
        "'{\"part\":{\"type\":\"error\",\"message\":\"route denied\"}}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    provider = OpenCodeCliProvider(command=str(executable))
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))

    async def log_cb(_project_id: str, _message: str, _level: str):
        return None

    result = asyncio.run(provider.run(ctx, log_cb))
    assert not result.success
    assert result.error == "OpenCode error: route denied"


def test_stderr_is_streamed_as_redacted_operator_diagnostics(monkeypatch, tmp_path: Path):
    ctx = _ctx(tmp_path)
    executable = tmp_path / "stderr-opencode"
    executable.write_text(
        "#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' \"transport key=$MCP_DOC_GENERATION_OPENROUTER_API_KEY\" >&2\n"
        "printf '%s\\n' '{\"type\":\"step_finish\"}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    monkeypatch.setenv("MCP_DOC_GENERATION_OPENROUTER_API_KEY", "never-log-me")
    provider = OpenCodeCliProvider(command=str(executable), model_route="openrouter/test")
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))
    logs: list[tuple[str, str]] = []

    async def log_cb(_project_id: str, message: str, level: str):
        logs.append((message, level))

    result = asyncio.run(provider.run(ctx, log_cb))
    assert result.success
    assert ("[stderr] transport key=[redacted]", "warning") in logs
    assert all("never-log-me" not in message for message, _level in logs)


def test_stderr_redacts_credentials_nested_in_opencode_config(monkeypatch, tmp_path: Path):
    runtime_env = {
        "OPENCODE_CONFIG_CONTENT": json.dumps({"provider": {"custom": {"options": {"apiKey": "nested-secret-value"}}}}),
    }
    from mcp_server.providers.opencode_cli import _safe_stderr

    assert _safe_stderr("failed with nested-secret-value", runtime_env) == "failed with [redacted]"


@pytest.mark.parametrize(
    ("model_route", "setup", "expected"),
    [
        ("opencode-go/kimi-k3", lambda monkeypatch: monkeypatch.setattr(config, "opencode_go_api_key", ""), "OpenCode Go requires"),
        ("ollama/qwen3", lambda monkeypatch: monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "not-json"), "OPENCODE_CONFIG_CONTENT must be valid JSON"),
    ],
)
def test_transport_configuration_failure_returns_provider_result(
    monkeypatch, tmp_path: Path, model_route: str, setup, expected: str,
):
    ctx = _ctx(tmp_path)
    executable = tmp_path / "unused-opencode"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(config, "data_dir", tmp_path / "service-data")
    setup(monkeypatch)
    provider = OpenCodeCliProvider(command=str(executable), model_route=model_route)
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))

    async def log_cb(_project_id: str, _message: str, _level: str):
        return None

    result = asyncio.run(provider.run(ctx, log_cb))
    assert not result.success
    assert expected in (result.error or "")


def _fake_cli(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-opencode-events"
    executable.write_text(
        "#!/usr/bin/env python3\nimport json, sys\nsys.stdin.read()\n" + body,
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_json_event_larger_than_the_stream_limit_does_not_fail_the_run(monkeypatch, tmp_path: Path):
    # Verified failure: a single `--format json` event carrying a whole tool
    # result exceeded asyncio's 64 KiB line limit, so the stdout reader raised
    # `Separator is found, but chunk is longer than limit` and the run ended
    # with staging preserved and no documents.
    from mcp_server.providers import opencode_cli

    ctx = _ctx(tmp_path)
    executable = _fake_cli(
        tmp_path,
        "print(json.dumps({'type':'step_finish','sessionID':'ses_big','usage':{'input':2,'output':1}}))\n"
        "print(json.dumps({'type':'tool_result','output':'x'*300000}))\n"
        "print(json.dumps({'type':'step_finish','usage':{'input':5,'output':3}}))\n",
    )
    monkeypatch.setattr(config, "opencode_timeout_seconds", 30)
    # Pin the transport buffer to asyncio's own default: the fix must hold
    # because frames are split here, not because the buffer was widened.
    monkeypatch.setattr(opencode_cli, "_STREAM_BUFFER_BYTES", 64 * 1024)
    provider = OpenCodeCliProvider(command=str(executable))
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))

    async def log_cb(_project_id: str, _message: str, _level: str):
        return None

    result = asyncio.run(provider.run(ctx, log_cb))
    assert result.success
    assert result.session_id == "ses_big"
    # Events on both sides of the large one are still accounted for.
    assert (result.input_tokens, result.output_tokens, result.num_turns) == (7, 4, 2)


def test_event_beyond_the_explicit_cap_is_dropped_with_a_warning(monkeypatch, tmp_path: Path):
    from mcp_server.providers import opencode_cli

    ctx = _ctx(tmp_path)
    executable = _fake_cli(
        tmp_path,
        "print(json.dumps({'type':'tool_result','output':'x'*300000}))\n"
        "print(json.dumps({'type':'step_finish','sessionID':'ses_after','usage':{'input':5,'output':3}}))\n",
    )
    monkeypatch.setattr(config, "opencode_timeout_seconds", 30)
    monkeypatch.setattr(opencode_cli, "_MAX_EVENT_BYTES", 4096)
    provider = OpenCodeCliProvider(command=str(executable))
    monkeypatch.setattr(provider, "_preflight", lambda: str(executable))

    logs: list[tuple[str, str]] = []

    async def log_cb(_project_id: str, message: str, level: str):
        logs.append((message, level))

    result = asyncio.run(provider.run(ctx, log_cb))
    assert result.success
    # The stream resynchronises: the event after the dropped one is still read.
    assert result.session_id == "ses_after"
    assert (result.input_tokens, result.output_tokens) == (5, 3)
    assert any("Dropped an OpenCode event" in message and level == "warning" for message, level in logs)
