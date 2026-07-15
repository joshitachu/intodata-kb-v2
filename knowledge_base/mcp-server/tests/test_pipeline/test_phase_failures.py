"""Slice 2: Phase failure surfacing + Phase 2 gate demotion tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server.providers.custom.models import CoverageReport, FactsManifest, HarnessRunState
from mcp_server.providers.custom.pipeline.warning_collector import WarningCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_facts(unit_count: int = 5) -> FactsManifest:
    return FactsManifest(
        project_id="test_proj",
        platform="generic",
        canonical_unit_label="unit",
        unit_count=unit_count,
    )


def _make_state(unit_count: int = 5) -> HarnessRunState:
    return HarnessRunState(project_id="test_proj", facts_manifest=_make_facts(unit_count))


def _make_ctx(staging: Path, groups: list[str]) -> MagicMock:
    ctx = MagicMock()
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging
    ctx.preprocess_groups = groups
    ctx.resume_state = None
    return ctx


def _phase1_helpers(
    tasks_side_effect=None,
    tasks_return_value: list | None = None,
) -> MagicMock:
    h = MagicMock()
    h.plugin_pack_dir = None
    h.phase2_shared_context.return_value = []
    h.group_units.return_value = []
    h.provider_for_phase.return_value = MagicMock()
    h.concurrency_for_phase.return_value = 2
    h.load_investigation_artifact.return_value = None
    h.chunk_has_pack_relevant_content.return_value = False
    h.merge_investigation_reports.return_value = MagicMock()
    if tasks_side_effect is not None:
        h.plan_evidence_tasks.side_effect = tasks_side_effect
    elif tasks_return_value is not None:
        h.plan_evidence_tasks.return_value = tasks_return_value
    else:
        h.plan_evidence_tasks.return_value = []
    return h


# ---------------------------------------------------------------------------
# Phase 1 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase1_all_groups_fail_returns_false_and_emits_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.pipeline.phases.phase1_investigation import Phase1Investigation

    monkeypatch.setattr(config, "agent_phase1_agentic", False)
    monkeypatch.setattr(
        "mcp_server.services.generation_run_state.record_manifest_chunks",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.investigator.SingleShotInvestigator",
        lambda *a, **kw: MagicMock(),
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_investigation").mkdir()
    (staging / "_preprocess").mkdir()

    state = _make_state(unit_count=2)
    ctx = _make_ctx(staging, ["group_a", "group_b"])

    phase = Phase1Investigation(helpers=_phase1_helpers())
    result = await phase.run(ctx, state, log_cb=AsyncMock())

    assert result.success is False
    assert result.error is not None
    assert "all groups" in result.error
    assert len(state.warning_collector.warnings) == 2
    codes = {w.code for w in state.warning_collector.warnings}
    assert codes == {WarningCode.PHASE1_GROUP_FAILURE}
    groups = {w.context["group"] for w in state.warning_collector.warnings}
    assert groups == {"group_a", "group_b"}


@pytest.mark.asyncio
async def test_phase1_partial_failure_returns_true_and_emits_one_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.pipeline.phases.phase1_investigation import Phase1Investigation

    monkeypatch.setattr(config, "agent_phase1_agentic", False)
    monkeypatch.setattr(
        "mcp_server.services.generation_run_state.record_manifest_chunks",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.investigator.write_investigation_artifacts",
        MagicMock(),
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.investigator.investigation_summary",
        lambda reports: "",
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.investigator.SingleShotInvestigator",
        lambda *a, **kw: MagicMock(),
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_investigation").mkdir()
    preprocess = staging / "_preprocess"
    preprocess.mkdir()

    # group_b has a real (small) evidence file → synthetic report path
    chunk_b = preprocess / "group_b.md"
    chunk_b.write_text("content", encoding="utf-8")

    from mcp_server.providers.custom.evidence_tasks import EvidenceTask

    def _tasks(group: str, group_file: Path, group_units: list, out_dir: Path) -> list:
        if "group_b" in str(group_file):
            return [EvidenceTask(index=1, task_id="group_b", path=chunk_b, units=[])]
        return []

    helpers = _phase1_helpers(tasks_side_effect=_tasks)

    state = _make_state(unit_count=2)
    ctx = _make_ctx(staging, ["group_a", "group_b"])

    phase = Phase1Investigation(helpers=helpers)
    result = await phase.run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert len(state.warning_collector.warnings) == 1
    assert state.warning_collector.warnings[0].code == WarningCode.PHASE1_GROUP_FAILURE
    assert state.warning_collector.warnings[0].context == {"group": "group_a"}


# ---------------------------------------------------------------------------
# Phase 2 tests
# ---------------------------------------------------------------------------


def _phase2_helpers() -> MagicMock:
    h = MagicMock()
    h.plugin_mode = False
    h.phase2_analysis_direct = AsyncMock()
    h.phase2_targeted_repair = AsyncMock()
    h.pack_contract_mode = False
    return h


@pytest.mark.asyncio
async def test_phase2_coverage_partial_emits_warning_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.pipeline.phases.phase2_analysis import Phase2Analysis

    monkeypatch.setattr(config, "small_project_threshold", 9999)

    partial = CoverageReport(ok=False, coverage_count=2, errors=["unit_c missing"])
    full = CoverageReport(ok=True, coverage_count=5)
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_analysis_coverage",
        lambda *a, **kw: partial,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_analysis_depth",
        lambda *a, **kw: full,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_analysis_guide_sections",
        lambda *a, **kw: full,
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_analysis").mkdir()

    state = _make_state()
    ctx = MagicMock()
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging

    phase = Phase2Analysis(helpers=_phase2_helpers(), analysis_policy="run")
    result = await phase.run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert state.warning_collector.has_warnings()
    w = state.warning_collector.warnings[0]
    assert w.code == WarningCode.PHASE2_COVERAGE_PARTIAL
    assert w.context == {"covered": 2, "total": 5}


@pytest.mark.asyncio
async def test_phase2_coverage_zero_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.pipeline.phases.phase2_analysis import Phase2Analysis

    monkeypatch.setattr(config, "small_project_threshold", 9999)

    zero = CoverageReport(ok=False, coverage_count=0, errors=["no units covered"])
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_analysis_coverage",
        lambda *a, **kw: zero,
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_analysis").mkdir()

    state = _make_state()
    ctx = MagicMock()
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging

    phase = Phase2Analysis(helpers=_phase2_helpers(), analysis_policy="run")
    with pytest.raises(RuntimeError):
        await phase.run(ctx, state, log_cb=AsyncMock())


@pytest.mark.asyncio
async def test_phase2_depth_partial_emits_warning_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.config import config
    from mcp_server.providers.custom.pipeline.phases.phase2_analysis import Phase2Analysis

    monkeypatch.setattr(config, "small_project_threshold", 9999)

    full_cov = CoverageReport(ok=True, coverage_count=5)
    partial_depth = CoverageReport(ok=False, coverage_count=3, errors=["depth insufficient"])
    full_guide = CoverageReport(ok=True, coverage_count=5)
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_analysis_coverage",
        lambda *a, **kw: full_cov,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_analysis_depth",
        lambda *a, **kw: partial_depth,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_analysis_guide_sections",
        lambda *a, **kw: full_guide,
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_analysis").mkdir()

    state = _make_state()
    ctx = MagicMock()
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging

    phase = Phase2Analysis(helpers=_phase2_helpers(), analysis_policy="run")
    result = await phase.run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert state.warning_collector.has_warnings()
    w = state.warning_collector.warnings[0]
    assert w.code == WarningCode.PHASE2_DEPTH_PARTIAL
    assert w.context == {"covered": 3, "total": 5}
