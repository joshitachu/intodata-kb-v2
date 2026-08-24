from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.opencode_cli import (
    OpenCodeAttemptLedger,
    OpenCodeCliProvider,
    OpenCodeCompatibilityError,
    OpenCodeTaskContract,
    _PhaseExecution,
)
from mcp_server.providers.opencode_evidence import EvidenceScope, OpenCodeEvidenceStore
from mcp_server.providers.opencode_investigation_mcp import InvestigationFacade


def _setup(tmp_path: Path) -> tuple[OpenCodeEvidenceStore, EvidenceScope, DocGenContext]:
    staging = tmp_path / "staging"
    source = staging / "_preprocess" / "Group.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "UnitA sends customer records through the integration route to UnitB.\n",
        encoding="utf-8",
    )
    facts = FactsManifest(
        project_id="project",
        platform="generic",
        canonical_unit_label="units",
        unit_count=2,
        groups={"Group": 2},
        units=[
            UnitFact(name="UnitA", group="Group", path="UnitA.py"),
            UnitFact(name="UnitB", group="Group", path="UnitB.py"),
        ],
    )
    scope = EvidenceScope(
        scope_id="scope-001-Group",
        group="Group",
        allowed_units=("UnitA", "UnitB"),
        source_ref="_preprocess/Group.md",
    )
    store = OpenCodeEvidenceStore(staging, facts)
    store.register_scope(scope)
    ctx = DocGenContext(
        project_id="project",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Group"],
        total_jobs=2,
        total_groups=1,
        effective_model="opencode",
        write_model="opencode",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    return store, scope, ctx


def test_investigation_facade_commits_progress_and_returns_validation_feedback(
    tmp_path: Path,
) -> None:
    store, scope, _ = _setup(tmp_path)
    facade = InvestigationFacade(store, {scope.scope_id})
    assert {tool.name for tool in facade.list_tools()} == {
        "submit_evidence",
        "report_gap",
        "get_progress",
        "finish_scope",
    }

    async def exercise() -> None:
        submitted = await facade.call(
            "submit_evidence",
            {
                "scope_id": scope.scope_id,
                "evidence": [
                    {
                        "unit_name": "UnitA",
                        "category": "data flow",
                        "snippet": "customer records through the integration route to UnitB",
                        "observation": "UnitA sends customer records through the evidenced route.",
                    }
                ],
            },
        )
        assert submitted["accepted_count"] == 1
        invalid = await facade.call("get_progress", {"scope_id": "another-scope"})
        assert invalid["ok"] is False
        await facade.call(
            "report_gap",
            {
                "scope_id": scope.scope_id,
                "unit_name": "UnitB",
                "reason": "No executable detail exists for this unit",
            },
        )
        finished = await facade.call("finish_scope", {"scope_id": scope.scope_id})
        assert finished["progress"]["complete"] is True

    asyncio.run(exercise())


def test_investigation_phase_config_is_native_read_only_and_tool_write_only(
    tmp_path: Path,
) -> None:
    store, scope, ctx = _setup(tmp_path)
    facade = InvestigationFacade(store, {scope.scope_id})
    phase_config = OpenCodeCliProvider()._phase_config(
        ctx=ctx,
        phase="gather",
        contracts=[],
        runtime=None,
        investigation_facade=facade,
    )
    try:
        payload = json.loads(phase_config.path.read_text(encoding="utf-8"))
        assert payload["permission"]["read"]["**"] == "allow"
        assert payload["permission"]["grep"]["**"] == "allow"
        assert payload["permission"]["glob"]["**"] == "allow"
        assert payload["permission"]["edit"] == {"*": "deny"}
        assert payload["permission"]["bash"] == {"*": "deny"}
        assert payload["permission"]["*"] == "deny"
        assert payload["permission"]["nexus_investigation_*"] == "allow"
        assert "mcp" not in payload["permission"]
        assert payload["mcp"]["nexus_investigation"]["environment"] == {
            "NEXUS_INVESTIGATION_GRANT": facade.token
        }
    finally:
        phase_config.cleanup()


def test_adaptive_scope_runner_uses_tool_commits_and_finishes_scope(
    monkeypatch, tmp_path: Path
) -> None:
    store, _, ctx = _setup(tmp_path)
    facts = store.facts
    provider = OpenCodeCliProvider()
    contract = OpenCodeTaskContract(
        phase="gather",
        target="investigation_Group",
        prompt_artifact="test",
        evidence_artifacts=("_preprocess/Group.md",),
        output_artifact="_transient/opencode/investigation_raw/Group.json",
        metadata={
            "task_id": "investigation_Group",
            "group": "Group",
            "allowed_units": ["UnitA", "UnitB"],
            "source_ref": "_preprocess/Group.md",
            "validation_receipt": "_transient/opencode/gather_guard/Group.validated.json",
        },
    )
    calls: list[str] = []

    async def execute_step(**kwargs):
        facade = kwargs["investigation_facade"]
        scope_id = next(iter(facade.scope_ids))
        calls.append(scope_id)
        await facade.call(
            "submit_evidence",
            {
                "scope_id": scope_id,
                "evidence": [
                    {
                        "unit_name": "UnitA",
                        "category": "data flow",
                        "snippet": "customer records through the integration route to UnitB",
                        "observation": "UnitA sends customer records through the evidenced route.",
                    }
                ],
            },
        )
        await facade.call(
            "report_gap",
            {
                "scope_id": scope_id,
                "unit_name": "UnitB",
                "reason": "No executable detail exists for this unit",
            },
        )
        await facade.call("finish_scope", {"scope_id": scope_id})
        return _PhaseExecution(session_id="session-1", events=[])

    monkeypatch.setattr(provider, "_execute_step", execute_step)
    monkeypatch.setattr(config, "opencode_scope_max_units", 40)
    monkeypatch.setattr(config, "opencode_scope_max_groups", 6)
    monkeypatch.setattr(config, "opencode_scope_concurrency", 3)
    monkeypatch.setattr(config, "opencode_scope_max_followups", 1)

    async def run() -> OpenCodeEvidenceStore:
        async def log_cb(*_args):
            return None

        return await provider._execute_gather_scopes(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            facts=facts,
            contracts=[contract],
            ledger=OpenCodeAttemptLedger(ctx.staging_dir),
            log_cb=log_cb,
            on_complete=lambda *_args: None,
        )

    result = asyncio.run(run())
    reports = result.list_scope_reports()
    assert len(calls) == 1
    assert len(reports) == 1
    assert reports[0].report.skipped_units == {
        "UnitB": "No executable detail exists for this unit"
    }


def test_scope_failure_preserves_run_and_becomes_explicit_gaps(monkeypatch, tmp_path: Path) -> None:
    store, _, ctx = _setup(tmp_path)
    facts = store.facts
    provider = OpenCodeCliProvider()
    contract = OpenCodeTaskContract(
        phase="gather",
        target="investigation_Group",
        prompt_artifact="test",
        evidence_artifacts=("_preprocess/Group.md",),
        output_artifact="_transient/opencode/investigation_raw/Group.json",
        metadata={
            "task_id": "investigation_Group",
            "group": "Group",
            "allowed_units": ["UnitA", "UnitB"],
            "source_ref": "_preprocess/Group.md",
            "validation_receipt": "_transient/opencode/gather_guard/Group.validated.json",
        },
    )

    async def fail_step(**_kwargs):
        raise OpenCodeCompatibilityError("provider stopped")

    monkeypatch.setattr(provider, "_execute_step", fail_step)
    monkeypatch.setattr(config, "opencode_scope_max_units", 40)
    monkeypatch.setattr(config, "opencode_scope_max_groups", 6)
    monkeypatch.setattr(config, "opencode_scope_concurrency", 1)
    monkeypatch.setattr(config, "opencode_scope_max_followups", 0)
    logs: list[tuple[str, str]] = []

    async def run() -> OpenCodeEvidenceStore:
        async def log_cb(_project, message, level):
            logs.append((message, level))

        return await provider._execute_gather_scopes(
            resolved="/usr/bin/opencode",
            ctx=ctx,
            facts=facts,
            contracts=[contract],
            ledger=OpenCodeAttemptLedger(ctx.staging_dir),
            log_cb=log_cb,
            on_complete=lambda *_args: None,
        )

    result = asyncio.run(run())
    report = result.list_scope_reports()[0].report
    assert set(report.skipped_units) == {"UnitA", "UnitB"}
    assert any("accepted evidence was preserved" in message for message, _ in logs)
    assert any("explicit coverage gap" in message for message, _ in logs)
