from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import asyncio

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.opencode_cli import (
    OpenCodeAttemptError,
    OpenCodeAttemptLedger,
    OpenCodeCliProvider,
    OpenCodeCompatibilityError,
    OpenCodeTaskContract,
    _PhaseExecution,
    parse_mcp_read_allowlist,
)
from mcp_server.providers.custom.models import FactsManifest, HarnessRunState, UnitFact


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
    payload = json.loads(
        (tmp_path / "_audit" / "opencode_attempt_ledger.json").read_text()
    )
    assert payload["max_nexus_attempts"] == 1

    resumed = OpenCodeAttemptLedger(tmp_path)
    with pytest.raises(OpenCodeAttemptError):
        resumed.dispatch("step:gather")
    resumed.dispatch("step:produce_topology")
    resumed.release_unstarted("step:produce_topology")
    OpenCodeAttemptLedger(tmp_path).dispatch("step:produce_topology")


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


def test_1187_command_uses_documented_flags_and_continues_same_session(
    monkeypatch, tmp_path
):
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
    continued = provider._command(
        "/usr/bin/opencode", _ctx(tmp_path / "continued"), "ses_123"
    )
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


def test_resume_uses_runtime_session_only_when_it_still_exists(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout='[{"id":"ses_existing"}]',
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
    )
    monkeypatch.setattr(
        "mcp_server.providers.opencode_cli.subprocess.run",
        lambda *_args, **_kwargs: next(responses),
    )
    provider = OpenCodeCliProvider()

    assert provider._session_exists("/usr/bin/opencode", "ses_existing") is True
    assert provider._session_exists("/usr/bin/opencode", "ses_missing") is False


def test_runtime_env_repairs_system_account_home_from_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/nonexistent")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

    env = OpenCodeCliProvider._runtime_env()

    assert env["HOME"] == str(tmp_path)
    assert env["PYTHONUTF8"] == "1"


def test_execution_env_registers_selected_ollama_model_and_preserves_config(
    monkeypatch,
):
    monkeypatch.setattr(
        config, "opencode_ollama_base_url", "http://ollama.internal:11434/v1"
    )
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
    assert "choose the execution topology dynamically" in gather
    assert "contract_bytes, evidence_artifact_count, and evidence_bytes" in gather
    assert "completing contracts directly is valid" in gather
    assert "fan out only as much as is useful" in gather
    assert "Do not use a fixed worker count" in gather
    assert "exactly one child agent" not in gather
    assert "strictly one at a time" not in gather
    assert "do not search/grep/glob" in gather
    assert "supplementary evidence" in gather
    assert "template_artifact" in gather
    assert "validation_command" in gather
    assert "supplementary evidence" not in analyze
    assert "choose the execution topology dynamically" in analyze
    assert "Do not use a fixed worker count" in analyze
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
    result = subprocess.run(
        [command, "run", "--help"], capture_output=True, text=True, check=True
    )
    help_text = result.stdout + result.stderr
    assert all(
        flag in help_text
        for flag in ("--model", "--agent", "--dir", "--session", "--auto")
    )
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
    assert provider._parse_json_event(
        {"type": "step_finish", "sessionID": "ses_1"}
    ) == {"type": "step_finish", "session_id": "ses_1"}
    assert provider._parse_json_event(
        {"type": "phase_completed", "phase": "gather"}
    ) is None


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


def test_opencode_usage_receipts_persist_model_usage_before_validation(tmp_path):
    audit_dir = tmp_path / "_audit"
    audit_dir.mkdir()
    receipts = [{
        "phase": "gather",
        "step": "gather",
        "status": "model_completed",
        "session_id": "ses_123",
        "input_tokens": 120,
        "output_tokens": 45,
        "cost_usd": 0.01,
        "latency_ms": 345,
    }]

    OpenCodeCliProvider._write_usage_receipts(audit_dir, receipts)

    assert json.loads((audit_dir / "opencode_usage.json").read_text()) == receipts


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="p",
        platform="generic",
        canonical_unit_label="code units",
        unit_count=1,
        groups={"A": 1},
        units=[UnitFact(name="UnitA", group="A", path="UnitA.py", unit_type="code_unit")],
    )


def test_gather_output_is_materialized_as_legacy_investigation_artifacts(
    monkeypatch, tmp_path
):
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


def test_real_subprocess_adapter_continues_one_session_and_verifies_outputs(
    monkeypatch, tmp_path
):
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
                contracts=[OpenCodeTaskContract(
                    "analyze", "stream", "test", (), "stream.txt", prompt="write"
                )],
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
