"""Tests for the standalone ``scripts/runtime_tools.py`` query CLI.

The script is copied verbatim into ``{staging}/_tools/runtime_tools.py`` and run
by the documentation agent with a bare ``python3``, so it is loaded here by path
rather than imported as a package module -- the test must exercise exactly the
file that ships, and must prove it runs with no ``mcp_server`` import and no
third-party package available.

What is pinned here:

* the three commands hit the fixed server contract (paths, method, auth header);
* ``project_id`` comes from the run, and agent-supplied JSON cannot change it;
* every call lands in ``_assessment/runtime_probe_ledger.jsonl`` as a SHAPE
  digest, never as a copy of the payload;
* the bearer key never reaches stdout or stderr -- on success, on a transport
  failure, or on an unexpected exception.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "mcp_server"
    / "scripts"
    / "runtime_tools.py"
)

KEY = "nexus-ingest-key-super-secret-0123456789"
PROJECT = "alitalend"
BASE_URL = "http://rag.internal:8000"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_runtime_tools_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime_tools = _load_script()


# ───────────────────────────────────────────────────────────────────────────
# Fixtures / doubles
# ───────────────────────────────────────────────────────────────────────────


class FakeResponse:
    """Minimal stand-in for ``http.client.HTTPResponse``."""

    def __init__(self, payload: object, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class Recorder:
    """Captures the requests the script builds and replays canned responses."""

    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[object] = []

    def __call__(self, request, timeout):  # noqa: ANN001 - test double
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return FakeResponse(self.response)

    @property
    def last(self):  # noqa: ANN201 - test double
        assert self.requests, "no request was sent"
        return self.requests[-1]

    def body(self) -> dict:
        data = self.last.data
        assert data is not None, "request carried no body"
        return json.loads(data.decode("utf-8"))


@pytest.fixture(autouse=True)
def _clean_secrets():
    """The script's secret registry is module-global; isolate it per test."""
    runtime_tools._SECRETS.clear()
    yield
    runtime_tools._SECRETS.clear()


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    tools = tmp_path / "_tools"
    tools.mkdir()
    (tools / runtime_tools.SIDECAR_NAME).write_text(
        json.dumps(
            {"project_id": PROJECT, "base_url": BASE_URL, "staging_dir": str(tmp_path)}
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def settings(staging: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEXUS_INGEST_KEY", KEY)
    monkeypatch.delenv("NEXUS_RAG_BASE_URL", raising=False)
    monkeypatch.delenv("NEXUS_PROJECT_ID", raising=False)
    monkeypatch.delenv("NEXUS_STAGING_DIR", raising=False)
    return runtime_tools.load_settings(staging / "_tools" / "runtime_tools.py")


def _ledger_lines(staging: Path) -> list[dict]:
    path = staging / "_assessment" / "runtime_probe_ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _install(monkeypatch: pytest.MonkeyPatch, response: object) -> Recorder:
    recorder = Recorder(response)
    monkeypatch.setattr(runtime_tools, "_open", recorder)
    return recorder


# ───────────────────────────────────────────────────────────────────────────
# list / call / elastic against a mocked HTTP layer
# ───────────────────────────────────────────────────────────────────────────


def test_list_hits_the_tools_endpoint_and_prints_json(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    payload = {
        "tools": [
            {"name": "job_reliability", "description": "d", "input_schema": {}},
            {"name": "recent_failures", "description": "d", "input_schema": {}},
        ]
    }
    recorder = _install(monkeypatch, payload)

    assert runtime_tools.command_list(settings) == 0

    assert recorder.last.full_url == f"{BASE_URL}/api/v1/runtime/tools"
    assert recorder.last.get_method() == "GET"
    assert recorder.last.data is None
    assert recorder.last.headers["Authorization"] == f"Bearer {KEY}"

    printed = json.loads(capsys.readouterr().out)
    assert [tool["name"] for tool in printed["tools"]] == ["job_reliability", "recent_failures"]

    entry = _ledger_lines(staging)[-1]
    assert entry["command"] == "list"
    assert entry["success"] is True


def test_call_posts_the_run_project_id_and_the_agent_arguments(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    result = {
        "success": True,
        "data": {"rows": [{"job": "Maximo2RHM_Workorders", "runs": 2122}]},
        "error_code": None,
        "truncated": False,
    }
    recorder = _install(monkeypatch, result)

    exit_code = runtime_tools.command_call(
        settings, "job_reliability", '{"window_days": 30}'
    )

    assert exit_code == 0
    assert recorder.last.full_url == f"{BASE_URL}/api/v1/runtime/tools/job_reliability"
    assert recorder.last.get_method() == "POST"
    assert recorder.body() == {
        "project_id": PROJECT,
        "arguments": {"window_days": 30},
    }
    assert json.loads(capsys.readouterr().out)["data"]["rows"][0]["runs"] == 2122


def test_elastic_probe_posts_to_the_probe_path(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    recorder = _install(monkeypatch, {"success": True, "data": {"fields": ["a", "b"]}})

    exit_code = runtime_tools.command_elastic(
        settings, "describe-fields", '{"index": "talend-esb-monitoring-*"}'
    )

    assert exit_code == 0
    assert (
        recorder.last.full_url
        == f"{BASE_URL}/api/v1/runtime/elastic/probe/describe-fields"
    )
    assert recorder.body() == {
        "index": "talend-esb-monitoring-*",
        "project_id": PROJECT,
    }
    capsys.readouterr()

    entry = _ledger_lines(staging)[-1]
    assert entry["command"] == "elastic"
    assert entry["tool"] == "describe-fields"


def test_every_documented_probe_name_is_reachable(
    settings, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    recorder = _install(monkeypatch, {"success": True, "data": {}})
    for probe in runtime_tools.KNOWN_PROBES:
        assert runtime_tools.command_elastic(settings, probe, "{}") == 0
        assert recorder.last.full_url.endswith(f"/elastic/probe/{probe}")
    capsys.readouterr()


def test_a_result_reporting_its_own_failure_is_still_an_answer(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """``success: false`` is the server answering, not a transport failure."""
    _install(
        monkeypatch,
        {"success": False, "data": None, "error_code": "no_runtime_source", "truncated": False},
    )

    assert runtime_tools.command_call(settings, "job_reliability", "{}") == 0
    assert json.loads(capsys.readouterr().out)["error_code"] == "no_runtime_source"

    entry = _ledger_lines(staging)[-1]
    assert entry["success"] is False
    assert entry["error_code"] == "no_runtime_source"


# ───────────────────────────────────────────────────────────────────────────
# project_id injection
# ───────────────────────────────────────────────────────────────────────────


def test_agent_supplied_project_id_cannot_defeat_the_injected_one(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    recorder = _install(monkeypatch, {"success": True, "data": {}})

    runtime_tools.command_call(
        settings, "job_reliability", '{"project_id": "someone_elses_project", "limit": 5}'
    )

    body = recorder.body()
    assert body["project_id"] == PROJECT
    assert body["arguments"] == {"limit": 5}
    assert "someone_elses_project" not in json.dumps(body)
    capsys.readouterr()

    entry = _ledger_lines(staging)[-1]
    assert entry["overrode_project_id"] is True
    assert entry["project_id"] == PROJECT


def test_agent_supplied_project_id_cannot_defeat_an_elastic_probe_either(
    settings, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    recorder = _install(monkeypatch, {"success": True, "data": {}})

    runtime_tools.command_elastic(
        settings, "count-matching", '{"project_id": "someone_elses_project"}'
    )

    assert recorder.body()["project_id"] == PROJECT
    assert "someone_elses_project" not in json.dumps(recorder.body())
    capsys.readouterr()


def test_the_per_run_sidecar_outranks_the_process_environment(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent run's env must not re-point this run at another project.

    ``os.environ`` is process-global and two documentation runs can overlap in
    one server process; the sidecar is per run and cannot race.
    """
    monkeypatch.setenv("NEXUS_INGEST_KEY", KEY)
    monkeypatch.setenv("NEXUS_PROJECT_ID", "a_concurrent_run")
    monkeypatch.setenv("NEXUS_RAG_BASE_URL", "http://somewhere-else:9999")

    resolved = runtime_tools.load_settings(staging / "_tools" / "runtime_tools.py")

    assert resolved.project_id == PROJECT
    assert resolved.base_url == BASE_URL


def test_the_environment_is_used_when_there_is_no_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXUS_INGEST_KEY", KEY)
    monkeypatch.setenv("NEXUS_PROJECT_ID", PROJECT)
    monkeypatch.setenv("NEXUS_RAG_BASE_URL", BASE_URL + "/")
    monkeypatch.setenv("NEXUS_STAGING_DIR", str(tmp_path))

    resolved = runtime_tools.load_settings(tmp_path / "_tools" / "runtime_tools.py")

    assert resolved.project_id == PROJECT
    assert resolved.base_url == BASE_URL  # trailing slash normalised
    assert resolved.staging_dir == tmp_path


def test_missing_identity_is_a_named_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEXUS_INGEST_KEY", KEY)
    monkeypatch.delenv("NEXUS_PROJECT_ID", raising=False)
    monkeypatch.delenv("NEXUS_RAG_BASE_URL", raising=False)

    with pytest.raises(runtime_tools.ConfigurationError) as excinfo:
        runtime_tools.load_settings(tmp_path / "_tools" / "runtime_tools.py")

    message = str(excinfo.value)
    assert "NEXUS_PROJECT_ID" in message and "NEXUS_RAG_BASE_URL" in message
    assert KEY not in message


def test_non_object_arguments_are_refused() -> None:
    for raw in ('["a"]', '"a"', "42", "{not json}"):
        with pytest.raises(runtime_tools.ConfigurationError):
            runtime_tools.parse_arguments(raw)
    assert runtime_tools.parse_arguments(None) == ({}, False)
    assert runtime_tools.parse_arguments("  ") == ({}, False)


# ───────────────────────────────────────────────────────────────────────────
# Ledger
# ───────────────────────────────────────────────────────────────────────────


def test_the_ledger_records_a_digest_and_not_the_payload(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    secret_ish_value = "Maximo2RHM_Workorders_row_payload_marker"
    _install(
        monkeypatch,
        {
            "success": True,
            "truncated": False,
            "error_code": None,
            "data": {
                "rows": [{"job": secret_ish_value, "runs": i} for i in range(37)],
                "window_days": 30,
            },
        },
    )

    runtime_tools.command_call(settings, "job_reliability", '{"window_days": 30}')
    capsys.readouterr()

    raw = (staging / "_assessment" / "runtime_probe_ledger.jsonl").read_text(encoding="utf-8")
    assert secret_ish_value not in raw

    entry = json.loads(raw.splitlines()[-1])
    assert entry["tool"] == "job_reliability"
    assert entry["arguments"] == {"window_days": 30}
    assert entry["digest"]["type"] == "object"
    assert entry["digest"]["counts"]["rows"] == 37
    assert set(entry["digest"]["keys"]) == {"rows", "window_days"}
    assert entry["truncated"] is False
    assert "ts" in entry and isinstance(entry["duration_ms"], int)


def test_the_ledger_is_append_only_across_calls(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install(monkeypatch, {"success": True, "data": {"rows": []}})
    runtime_tools.command_list(settings)
    runtime_tools.command_call(settings, "job_reliability", "{}")
    runtime_tools.command_elastic(settings, "list-indices", "{}")
    capsys.readouterr()

    assert [entry["command"] for entry in _ledger_lines(staging)] == [
        "list", "call", "elastic",
    ]


def test_long_arguments_are_bounded_in_the_ledger(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install(monkeypatch, {"success": True, "data": {}})
    big = json.dumps({"query": "x" * (runtime_tools.MAX_LEDGER_ARGUMENT_CHARS + 500)})

    runtime_tools.command_elastic(settings, "count-matching", big)
    capsys.readouterr()

    recorded = _ledger_lines(staging)[-1]["arguments"]
    assert recorded["truncated"] is True
    assert len(recorded["preview"]) <= runtime_tools.MAX_LEDGER_ARGUMENT_CHARS


def test_a_ledger_write_failure_does_not_cost_the_answer(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # _assessment exists as a FILE: mkdir fails, the call must still answer.
    (staging / "_assessment").write_text("not a directory", encoding="utf-8")
    _install(monkeypatch, {"success": True, "data": {"rows": []}})

    assert runtime_tools.command_call(settings, "job_reliability", "{}") == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["success"] is True
    assert "probe ledger not written" in captured.err


def test_digest_describes_shape_only() -> None:
    digest = runtime_tools.digest_result([{"job": "a", "runs": 1}, {"job": "b", "runs": 2}])
    assert digest == {"type": "array", "rows": 2, "item_keys": ["job", "runs"]}
    assert runtime_tools.digest_result(None) == {"type": "null"}
    assert runtime_tools.digest_result(7)["type"] == "number"


# ───────────────────────────────────────────────────────────────────────────
# The bearer key never leaves
# ───────────────────────────────────────────────────────────────────────────


def test_the_key_is_absent_from_stdout_and_stderr_on_success(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _install(monkeypatch, {"success": True, "data": {"rows": []}})

    runtime_tools.command_call(settings, "job_reliability", "{}")

    captured = capsys.readouterr()
    assert KEY not in captured.out
    assert KEY not in captured.err
    ledger = (staging / "_assessment" / "runtime_probe_ledger.jsonl").read_text(encoding="utf-8")
    assert KEY not in ledger


def test_the_key_is_absent_when_the_transport_fails(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import urllib.error

    _install(
        monkeypatch,
        urllib.error.URLError(f"connection refused while presenting {KEY}"),
    )

    assert runtime_tools.command_call(settings, "job_reliability", "{}") == 2

    captured = capsys.readouterr()
    assert KEY not in captured.out
    assert KEY not in captured.err
    assert "***" in captured.err

    entry = _ledger_lines(staging)[-1]
    assert entry["success"] is False
    assert entry["error_code"] == "transport_error"
    assert KEY not in json.dumps(entry)


def test_an_http_error_body_is_shown_and_the_key_is_not(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    import io
    import urllib.error

    error = urllib.error.HTTPError(
        url=f"{BASE_URL}/api/v1/runtime/tools/nope",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=io.BytesIO(json.dumps({"detail": "unknown tool 'nope'"}).encode("utf-8")),
    )
    _install(monkeypatch, error)

    assert runtime_tools.command_call(settings, "nope", "{}") == 2

    captured = capsys.readouterr()
    assert json.loads(captured.out)["detail"] == "unknown tool 'nope'"
    assert "HTTP 404" in captured.err
    assert KEY not in captured.out and KEY not in captured.err


def test_a_non_json_body_is_a_transport_failure_not_a_result(
    settings, staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A proxy's HTML error page is not a runtime tool result."""

    class HtmlResponse(FakeResponse):
        def __init__(self) -> None:  # noqa: D107 - test double
            self.status = 200
            self._body = b"<html><body>502 Bad Gateway</body></html>"

    monkeypatch.setattr(runtime_tools, "_open", lambda request, timeout: HtmlResponse())

    assert runtime_tools.command_call(settings, "job_reliability", "{}") == 2

    captured = capsys.readouterr()
    assert "502 Bad Gateway" in json.loads(captured.out)["raw_response"]
    assert "not JSON" in captured.err
    assert _ledger_lines(staging)[-1]["error_code"] == "transport_error"


def test_an_unexpected_exception_is_one_redacted_line_with_no_traceback(
    staging: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("NEXUS_INGEST_KEY", KEY)
    monkeypatch.setenv("NEXUS_PROJECT_ID", PROJECT)
    monkeypatch.setenv("NEXUS_RAG_BASE_URL", BASE_URL)
    monkeypatch.setenv("NEXUS_STAGING_DIR", str(staging))

    def _explode(request, timeout):  # noqa: ANN001 - test double
        raise ValueError(f"boom, and the key was {KEY}")

    monkeypatch.setattr(runtime_tools, "_open", _explode)

    assert runtime_tools.main(["call", "job_reliability", "{}"]) == 2

    captured = capsys.readouterr()
    assert KEY not in captured.err
    assert "Traceback" not in captured.err
    assert "unexpected ValueError" in captured.err


def test_the_key_is_never_registered_when_it_is_trivially_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scrubbing a 3-character value would mangle unrelated output."""
    monkeypatch.setenv("NEXUS_INGEST_KEY", "abc")
    monkeypatch.setenv("NEXUS_PROJECT_ID", PROJECT)
    monkeypatch.setenv("NEXUS_RAG_BASE_URL", BASE_URL)
    runtime_tools.load_settings(tmp_path / "_tools" / "runtime_tools.py")
    assert runtime_tools._SECRETS == []


# ───────────────────────────────────────────────────────────────────────────
# Output bounding
# ───────────────────────────────────────────────────────────────────────────


def test_an_oversized_result_is_replaced_by_a_valid_json_notice() -> None:
    payload = {"rows": [{"job": f"job_{i}", "note": "x" * 200} for i in range(500)]}
    rendered = runtime_tools.render(payload, max_bytes=5_000)

    notice = json.loads(rendered)  # still parses: no half-cut JSON
    assert notice["truncated_by_client"] is True
    assert notice["bytes"] > notice["limit_bytes"]
    assert notice["shape"]["counts"]["rows"] == 500


def test_a_small_result_is_rendered_verbatim() -> None:
    payload = {"rows": [{"job": "a"}]}
    assert json.loads(runtime_tools.render(payload, max_bytes=100_000)) == payload


# ───────────────────────────────────────────────────────────────────────────
# Really standalone: run the shipped file with a bare interpreter
# ───────────────────────────────────────────────────────────────────────────


def test_the_coordinator_stages_the_script_with_a_secret_free_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring half: what ``claude_service`` puts in staging for the agent."""
    from mcp_server.config import config as server_config
    from mcp_server.services import claude_service

    monkeypatch.setattr(server_config, "rag_base_url", BASE_URL + "/", raising=False)
    monkeypatch.setattr(server_config, "ingest_api_key", KEY, raising=False)

    staged = claude_service.stage_runtime_tools(tmp_path, PROJECT)

    assert staged == tmp_path / "_tools" / "runtime_tools.py"
    assert staged.read_bytes() == SCRIPT_PATH.read_bytes()  # verbatim, not a fork

    sidecar = json.loads(
        (tmp_path / "_tools" / "runtime_tools.json").read_text(encoding="utf-8")
    )
    assert sidecar["project_id"] == PROJECT
    assert sidecar["base_url"] == BASE_URL
    assert sidecar["staging_dir"] == str(tmp_path)
    # docs_staging/ is published on success: the bearer key must never be in it.
    assert KEY not in (tmp_path / "_tools" / "runtime_tools.json").read_text(encoding="utf-8")


def test_the_tool_directory_is_removed_before_the_staging_tree_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_tools/`` is run-time facility and must never reach a client docs tree.

    The CLI lane does NOT run ``phase4_cleanup.PUBLISHED_STAGING_DIRS`` -- that
    allowlist lives inside the custom harness's Phase 4, which the CLI
    transports never execute. Verified on a real published CLI-lane tree:
    ``output/alitalend/docs/`` carries ``_provenance/``, which is absent from
    that allowlist, so nothing generic would have stopped ``_tools/`` shipping
    to the client beside it.
    """
    from mcp_server.config import config as server_config
    from mcp_server.services import claude_service

    monkeypatch.setattr(server_config, "rag_base_url", BASE_URL, raising=False)
    monkeypatch.setattr(server_config, "ingest_api_key", KEY, raising=False)

    claude_service.stage_runtime_tools(tmp_path, PROJECT)
    assert (tmp_path / "_tools").is_dir()

    # The durable record of what the agent asked lives in _assessment/, which IS
    # published on purpose -- removing the facility must not remove the ledger.
    ledger = tmp_path / "_assessment" / "runtime_probe_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"tool": "list_runtime_jobs"}\n', encoding="utf-8")

    claude_service.remove_runtime_tools(tmp_path)

    assert not (tmp_path / "_tools").exists()
    assert ledger.is_file(), "the probe ledger is the reproducibility record"


def test_removing_the_tool_directory_twice_is_not_an_error(tmp_path: Path) -> None:
    """It runs on the failure path too, where staging may already be torn down."""
    from mcp_server.services import claude_service

    claude_service.remove_runtime_tools(tmp_path)  # never staged
    claude_service.remove_runtime_tools(tmp_path / "does-not-exist")


def test_the_coordinator_publishes_the_settings_to_the_subprocess_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both CLI transports copy ``os.environ``; this is the only channel."""
    from mcp_server.config import config as server_config
    from mcp_server.services import claude_service

    monkeypatch.setattr(server_config, "rag_base_url", BASE_URL, raising=False)
    monkeypatch.setattr(server_config, "ingest_api_key", KEY, raising=False)
    monkeypatch.setenv("NEXUS_PROJECT_ID", "previous_value")
    monkeypatch.delenv("NEXUS_INGEST_KEY", raising=False)

    with claude_service.runtime_tool_environment(PROJECT, tmp_path):
        assert os.environ["NEXUS_RAG_BASE_URL"] == BASE_URL
        assert os.environ["NEXUS_INGEST_KEY"] == KEY
        assert os.environ["NEXUS_PROJECT_ID"] == PROJECT
        assert os.environ["NEXUS_STAGING_DIR"] == str(tmp_path)

    # Restored, not leaked into the rest of the process.
    assert os.environ["NEXUS_PROJECT_ID"] == "previous_value"
    assert "NEXUS_INGEST_KEY" not in os.environ


def test_the_shipped_file_runs_with_no_mcp_server_on_the_path(
    tmp_path: Path,
) -> None:
    """The agent runs this with a bare ``python3``, outside the service venv."""
    tools_dir = tmp_path / "_tools"
    tools_dir.mkdir()
    staged = tools_dir / "runtime_tools.py"
    staged.write_bytes(SCRIPT_PATH.read_bytes())
    (tools_dir / "runtime_tools.json").write_text(
        json.dumps(
            {
                # Port 1 is never listening: a fast, deterministic transport failure.
                "base_url": "http://127.0.0.1:1",
                "project_id": PROJECT,
                "staging_dir": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL"}
    }
    env["NEXUS_INGEST_KEY"] = KEY
    env["PYTHONPATH"] = ""  # no mcp_server, no site-packages of this repo

    completed = subprocess.run(
        [sys.executable, str(staged), "call", "job_reliability", '{"project_id": "x"}'],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert completed.returncode == 2, completed.stderr
    assert KEY not in completed.stdout
    assert KEY not in completed.stderr
    assert "Traceback" not in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr

    entry = _ledger_lines(tmp_path)[-1]
    assert entry["project_id"] == PROJECT
    assert entry["overrode_project_id"] is True
    assert entry["success"] is False
