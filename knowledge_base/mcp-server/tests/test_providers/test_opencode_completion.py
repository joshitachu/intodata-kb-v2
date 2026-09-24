"""OpenCodeCompletionProvider: one bounded, tool-denied OpenCode completion.

Driven through a fake ``opencode`` executable so the real transport code runs:
preflight, isolated env, inline configuration, event parsing and cleanup.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from mcp_server.config import config
from mcp_server.providers.custom.models import ModelOptions
from mcp_server.providers.opencode_cli import OpenCodeCompletionProvider

VERSION = "9.9.9-test"

_FAKE = r'''#!{python}
import json, os, sys
if sys.argv[1:] == ["--version"]:
    print("{version}"); sys.exit(0)
prompt = sys.stdin.read()
record = {{
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "cwd_entries": sorted(os.listdir(os.getcwd())),
    "config": json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{{}}"),
    "prompt": prompt,
}}
with open(os.environ["FAKE_OPENCODE_RECORD"], "w") as fh:
    json.dump(record, fh)
mode = os.environ.get("FAKE_OPENCODE_MODE", "ok")
if mode == "error":
    print(json.dumps({{"type": "error", "part": {{"type": "error", "error": "model refused"}}}}))
    sys.exit(0)
print(json.dumps({{"type": "text", "part": {{"id": "p1", "type": "text-delta", "delta": "{{\"a\": "}}}}))
print(json.dumps({{"type": "text", "part": {{"id": "p1", "type": "text-delta", "delta": "1}}"}}}}))
print(json.dumps({{"type": "step_finish", "part": {{"type": "step-finish", "tokens": {{"input": 11, "output": 7}}}}}}))
'''


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch) -> Path:
    exe = tmp_path / "bin" / "opencode"
    exe.parent.mkdir()
    exe.write_text(_FAKE.format(python=sys.executable, version=VERSION), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_OPENCODE_RECORD", str(record))
    monkeypatch.setattr(config, "opencode_version", VERSION)
    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config, "opencode_config_path", "")
    return exe


async def test_completion_returns_assistant_text_with_tools_denied(fake_opencode, tmp_path) -> None:
    context = tmp_path / "doc.md"
    context.write_text("# Context doc", encoding="utf-8")
    provider = OpenCodeCompletionProvider(command=str(fake_opencode), model_route="ollama/test")

    result = await provider.complete("PROMPT", [context], ModelOptions(timeout_s=30))

    assert result.success is True, result.raw_error
    assert json.loads(result.text) == {"a": 1}
    assert (result.input_tokens, result.output_tokens) == (11, 7)
    record = json.loads((tmp_path / "record.json").read_text(encoding="utf-8"))
    # An empty scratch directory, never staging, and no file tools needed.
    assert record["cwd_entries"] == []
    assert "--dir" in record["argv"] and "--model" in record["argv"]
    permission = record["config"]["permission"]
    # Catch-all first (covers MCP tool ids too), then every known key by name.
    assert list(permission)[0] == "*" and set(permission.values()) == {"deny"}
    for name in ("read", "glob", "grep", "list", "edit", "bash", "webfetch", "task",
                 "websearch", "skill", "external_directory"):
        assert permission[name] == "deny"
    # Existing inline configuration (the Ollama provider block) survives the merge.
    assert "ollama" in record["config"]["provider"]
    assert record["prompt"].startswith("PROMPT") and "# Context doc" in record["prompt"]
    # The scratch state is removed afterwards.
    assert not Path(record["cwd"]).exists()


async def test_operator_allow_rules_do_not_survive_the_completion_policy(
    fake_opencode, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps({"permission": {"read": "allow", "mcp_tool_x": "allow"}}),
    )
    provider = OpenCodeCompletionProvider(command=str(fake_opencode), model_route="ollama/test")

    result = await provider.complete("PROMPT", None, ModelOptions(timeout_s=30))

    assert result.success is True, result.raw_error
    permission = json.loads((tmp_path / "record.json").read_text(encoding="utf-8"))["config"][
        "permission"
    ]
    assert "mcp_tool_x" not in permission and permission["read"] == "deny"
    assert permission["*"] == "deny"


async def test_error_event_is_a_failed_result_not_an_exception(
    fake_opencode, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FAKE_OPENCODE_MODE", "error")
    provider = OpenCodeCompletionProvider(command=str(fake_opencode), model_route="ollama/test")

    result = await provider.complete("PROMPT", None, ModelOptions(timeout_s=30))

    assert result.success is False
    assert "model refused" in (result.raw_error or "")


async def test_version_mismatch_fails_closed_before_launch(fake_opencode, monkeypatch) -> None:
    monkeypatch.setattr(config, "opencode_version", "0.0.0")
    provider = OpenCodeCompletionProvider(command=str(fake_opencode), model_route="ollama/test")

    result = await provider.complete("PROMPT", None, ModelOptions(timeout_s=30))

    assert result.success is False
    assert "version mismatch" in (result.raw_error or "")
