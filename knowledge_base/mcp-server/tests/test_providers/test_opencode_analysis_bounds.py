from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.opencode_cli import (
    OpenCodeAttemptLedger,
    OpenCodeCliProvider,
    OpenCodeCompatibilityError,
    OpenCodeTaskContract,
    _PhaseExecution,
)


def _ctx(tmp_path: Path, *, analysis_version: int = 3) -> DocGenContext:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True)
    return DocGenContext(
        project_id="project",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["group"],
        total_jobs=1,
        total_groups=1,
        effective_model="opencode",
        write_model="opencode",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
        generation_profile={
            "contract": "talend-landscape-v2",
            "analysis_version": analysis_version,
        },
    )


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="project",
        platform="talend",
        canonical_unit_label="job",
        unit_count=1,
        groups={"group": 1},
        units=[UnitFact(name="job", display_name="Job", group="group")],
    )


def _sleeping_opencode(tmp_path: Path, *, event_type: str = "step_finish") -> Path:
    executable = tmp_path / f"opencode-{event_type}"
    if event_type == "step_finish":
        event = {
            "type": "step_finish",
            "sessionID": "session",
            "part": {
                "type": "step-finish",
                "tokens": {
                    "input": 2,
                    "output": 3,
                    "reasoning": 5,
                    "cache": {"read": 7, "write": 11},
                },
                "cost": 0.25,
            },
        }
    else:
        event = {"type": "step_start", "sessionID": "session"}
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, time\n"
        "sys.stdin.read()\n"
        f"print(json.dumps({event!r}), flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _bounded_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> OpenCodeCliProvider:
    provider = OpenCodeCliProvider(model_route="ollama/test")
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(provider, "_shared_cache_home", lambda: cache)
    return provider


def _contract() -> OpenCodeTaskContract:
    return OpenCodeTaskContract(
        phase="analyze",
        target="knowledge:job",
        prompt_artifact="knowledge-claims-v2",
        evidence_artifacts=("_evidence_registry/landscape/tasks/job.json",),
        output_artifact="_evidence_registry/landscape/accepted/job.json",
        prompt="Analyze the explicitly assigned evidence.",
    )


async def _quiet_log(*_args: object) -> None:
    return None


def test_prepare_run_preflights_landscape_after_support_and_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mcp_server.providers.custom.engineering_facts import EngineeringFactsService
    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.providers.custom import prompt_packs
    from mcp_server.providers import opencode_landscape

    ctx = _ctx(tmp_path)
    facts = _facts()
    events: list[str] = []
    provider = OpenCodeCliProvider(model_route="ollama/test")
    monkeypatch.setattr(provider, "_facts_for_context", lambda _ctx: facts)

    def write_engineering(_self: object, **kwargs: object) -> None:
        Path(kwargs["staging_dir"]).joinpath("engineering_facts.md").write_text("facts")
        events.append("engineering")

    monkeypatch.setattr(EngineeringFactsService, "extract_and_write", write_engineering)
    monkeypatch.setattr(
        prompt_packs,
        "materialize_prompt_packs",
        lambda *_args, **_kwargs: events.append("prompt_support"),
    )

    class Harness:
        def _write_cross_cutting_facts(self, run_ctx: DocGenContext, _state: object) -> None:
            (run_ctx.staging_dir / "_cross_cutting_facts.md").write_text("support")
            events.append("cross_cutting")

        def _ensure_noncode_plan(self, run_ctx: DocGenContext) -> None:
            (run_ctx.staging_dir / "_noncode_plan.md").write_text("support")
            events.append("noncode")

    monkeypatch.setattr(provider, "_legacy_harness", lambda _facts: Harness())

    async def write_runtime(
        _self: object,
        run_ctx: DocGenContext,
        _state: object,
        _log_cb: object,
    ) -> None:
        (run_ctx.staging_dir / "_runtime.md").write_text("runtime")
        events.append("runtime")

    monkeypatch.setattr(HarnessHelpersImpl, "ensure_runtime_evidence", write_runtime)

    def preflight(run_ctx: DocGenContext, planned_facts: FactsManifest) -> object:
        assert planned_facts is facts
        for name in (
            "facts_manifest.json",
            "engineering_facts.md",
            "_cross_cutting_facts.md",
            "_noncode_plan.md",
            "_runtime.md",
        ):
            assert (run_ctx.staging_dir / name).is_file()
        events.append("preflight")
        return object()

    monkeypatch.setattr(opencode_landscape, "plan_landscape_analysis", preflight)
    asyncio.run(provider._prepare_run(ctx, _quiet_log))

    assert events[-2:] == ["runtime", "preflight"]


def test_semantic_v3_skips_unconsumed_legacy_gather_and_records_direct_path(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path, analysis_version=3)
    provider = OpenCodeCliProvider(model_route="ollama/test")

    class NoLegacyPolicy:
        def _phase_policy(self, _phase: str) -> str:
            raise AssertionError("semantic v3 must not consult the legacy Gather policy")

    assert provider._gather_contracts(ctx, _facts(), NoLegacyPolicy(), {}) == []
    provider._finalize_gather(
        ctx,
        _facts(),
        NoLegacyPolicy(),
        SimpleNamespace(investigation_reports=[]),
        [],
    )
    summary = (ctx.staging_dir / "_investigation" / "_summary.md").read_text()
    assert "Legacy model-driven Gather was not dispatched" in summary
    assert "deterministic scoped evidence plan directly" in summary


def test_persisted_analysis_v2_keeps_legacy_gather_policy(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, analysis_version=2)
    provider = OpenCodeCliProvider(model_route="ollama/test")
    consulted: list[str] = []

    class LegacyPolicy:
        def _phase_policy(self, phase: str) -> str:
            consulted.append(phase)
            return "skip"

    assert provider._gather_contracts(ctx, _facts(), LegacyPolicy(), {}) == []
    assert consulted == ["investigation"]


def test_scoped_analysis_allows_only_declared_reads_and_denies_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)
    provider = _bounded_provider(monkeypatch, tmp_path)
    contract = _contract()
    task = contract.evidence_artifacts[0]
    page = "_evidence_registry/landscape/pages/job.md"
    index = provider._write_contracts(ctx, "knowledge_job", [contract])
    contract_artifact = json.loads((ctx.staging_dir / index).read_text())[0][
        "contract_artifact"
    ]
    artifacts = (index, contract_artifact, task, page)

    phase_config = provider._phase_config(
        ctx=ctx,
        phase="analyze",
        contracts=[contract],
        runtime=None,
        exploration_artifacts=artifacts,
        restrict_search=True,
    )
    try:
        permissions = json.loads(phase_config.path.read_text())["permission"]
        assert permissions["glob"] == permissions["grep"] == permissions["list"] == "deny"
        assert "**" not in permissions["read"]
        for artifact in artifacts:
            assert permissions["read"][artifact] == "allow"
            assert permissions["read"][str((ctx.staging_dir / artifact).resolve())] == "allow"
        prompt = provider._phase_prompt(
            "analyze", "knowledge_job", index, 1, {}, restrict_search=True
        )
        assert "Read only the contract index, contract files" in prompt
        assert "Do not use search, grep, glob, list" in prompt
        assert "Use native read, grep, glob" not in prompt
    finally:
        phase_config.cleanup()


@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        ({"max_model_turns": 1}, "model_turn_budget_exhausted"),
        ({"max_processed_tokens": 28}, "processed_token_budget_exhausted"),
    ],
)
def test_analysis_budget_stops_after_completed_turn_and_receipts_all_token_classes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    limits: dict[str, int],
    reason: str,
) -> None:
    executable = _sleeping_opencode(tmp_path)
    provider = _bounded_provider(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path / reason)
    started = time.monotonic()

    with pytest.raises(OpenCodeCompatibilityError, match=reason):
        asyncio.run(
            provider._execute_step(
                resolved=str(executable),
                ctx=ctx,
                phase="analyze",
                step=reason,
                contracts=[_contract()],
                allowlist={},
                session_id=None,
                ledger=OpenCodeAttemptLedger(ctx.staging_dir),
                log_cb=_quiet_log,
                exploration_artifacts=_contract().evidence_artifacts,
                require_outputs=False,
                timeout_seconds=10,
                **limits,
            )
        )

    assert time.monotonic() - started < 3
    receipt = json.loads(
        (
            ctx.staging_dir
            / "_audit"
            / "opencode_interrupted_usage"
            / f"{reason}.json"
        ).read_text()
    )
    assert receipt["reason"] == reason
    assert receipt["model_turns"] == 1
    assert receipt["input_tokens"] == 2
    assert receipt["output_tokens"] == 3
    assert receipt["processed_tokens"] == 28
    assert receipt["usage_complete"] is False


def test_successful_session_exposes_processed_tokens_even_at_exact_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "completed-opencode"
    event = {
        "type": "step_finish",
        "sessionID": "complete",
        "part": {
            "tokens": {
                "input": 2,
                "output": 3,
                "reasoning": 5,
                "cache": {"read": 7, "write": 11},
            }
        },
    }
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "sys.stdin.read()\n"
        f"print(json.dumps({event!r}), flush=True)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    provider = _bounded_provider(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path / "complete")
    execution = asyncio.run(
        provider._execute_step(
            resolved=str(executable),
            ctx=ctx,
            phase="analyze",
            step="complete",
            contracts=[_contract()],
            allowlist={},
            session_id=None,
            ledger=OpenCodeAttemptLedger(ctx.staging_dir),
            log_cb=_quiet_log,
            exploration_artifacts=_contract().evidence_artifacts,
            require_outputs=False,
            timeout_seconds=10,
            max_processed_tokens=28,
        )
    )
    assert execution.processed_tokens == 28
    assert _PhaseExecution(session_id=None, events=[]).processed_tokens == 0
    assert not (ctx.staging_dir / "_audit" / "opencode_interrupted_usage").exists()


def test_timeout_persists_completed_turn_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = _sleeping_opencode(tmp_path)
    provider = _bounded_provider(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path / "timeout")

    with pytest.raises(TimeoutError, match="session timeout"):
        asyncio.run(
            provider._execute_step(
                resolved=str(executable),
                ctx=ctx,
                phase="analyze",
                step="timeout",
                contracts=[_contract()],
                allowlist={},
                session_id=None,
                ledger=OpenCodeAttemptLedger(ctx.staging_dir),
                log_cb=_quiet_log,
                exploration_artifacts=_contract().evidence_artifacts,
                require_outputs=False,
                timeout_seconds=1,
            )
        )
    receipt = json.loads((
        ctx.staging_dir / "_audit" / "opencode_interrupted_usage" / "timeout.json"
    ).read_text())
    assert receipt["reason"] == "timeout"
    assert receipt["processed_tokens"] == 28


def test_cancellation_persists_partial_usage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = _sleeping_opencode(tmp_path)
    provider = _bounded_provider(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path / "cancel")

    async def scenario() -> None:
        completed_turn = asyncio.Event()

        async def log_cb(_project: str, message: str, _level: str) -> None:
            if "model turn complete" in message:
                completed_turn.set()

        task = asyncio.create_task(
            provider._execute_step(
                resolved=str(executable),
                ctx=ctx,
                phase="analyze",
                step="cancel",
                contracts=[_contract()],
                allowlist={},
                session_id=None,
                ledger=OpenCodeAttemptLedger(ctx.staging_dir),
                log_cb=log_cb,
                exploration_artifacts=_contract().evidence_artifacts,
                require_outputs=False,
                timeout_seconds=10,
            )
        )
        await asyncio.wait_for(completed_turn.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    receipt = json.loads(
        (
            ctx.staging_dir / "_audit" / "opencode_interrupted_usage" / "cancel.json"
        ).read_text()
    )
    assert receipt["reason"] == "cancelled"
    assert receipt["processed_tokens"] == 28


def test_stdout_consumer_failure_terminates_child_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = _sleeping_opencode(tmp_path, event_type="step_start")
    provider = _bounded_provider(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path / "consumer")

    async def scenario() -> None:
        async def failing_log(*_args: object) -> None:
            raise RuntimeError("log consumer failed")

        with pytest.raises(RuntimeError, match="log consumer failed"):
            await asyncio.wait_for(
                provider._execute_step(
                    resolved=str(executable),
                    ctx=ctx,
                    phase="analyze",
                    step="consumer_failure",
                    contracts=[_contract()],
                    allowlist={},
                    session_id=None,
                    ledger=OpenCodeAttemptLedger(ctx.staging_dir),
                    log_cb=failing_log,
                    exploration_artifacts=_contract().evidence_artifacts,
                    require_outputs=False,
                    timeout_seconds=10,
                ),
                timeout=2,
            )

    asyncio.run(scenario())
