"""Tests for WarningCollector, WarningCode, and Phase 4 warnings.json output."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_server.providers.custom.models import HarnessRunState
from mcp_server.providers.custom.pipeline.warning_collector import (
    WarningCode,
    WarningCollector,
)


# ---------------------------------------------------------------------------
# WarningCollector unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_accumulates_warnings() -> None:
    collector = WarningCollector()
    await collector.emit("phase1", WarningCode.PHASE1_GROUP_FAILURE, "group A failed")
    await collector.emit("phase2", WarningCode.PHASE2_COVERAGE_PARTIAL, "2/5 covered")
    assert collector.has_warnings() is True
    assert len(collector.warnings) == 2


@pytest.mark.asyncio
async def test_empty_collector_has_no_warnings() -> None:
    collector = WarningCollector()
    assert collector.has_warnings() is False
    assert collector.warnings == []


@pytest.mark.asyncio
async def test_emit_concurrent_safety() -> None:
    collector = WarningCollector()

    async def _emit_one(i: int) -> None:
        await collector.emit("phase1", WarningCode.PHASE1_GROUP_FAILURE, f"group {i}")

    await asyncio.gather(*[_emit_one(i) for i in range(50)])
    assert len(collector.warnings) == 50


@pytest.mark.asyncio
async def test_warning_fields() -> None:
    collector = WarningCollector()
    await collector.emit(
        "phase2",
        WarningCode.PHASE2_DEPTH_PARTIAL,
        "shallow analysis",
        context={"covered_count": 3, "expected_count": 5},
    )
    w = collector.warnings[0]
    assert w.phase == "phase2"
    assert w.code == WarningCode.PHASE2_DEPTH_PARTIAL
    assert w.message == "shallow analysis"
    assert w.context == {"covered_count": 3, "expected_count": 5}


@pytest.mark.asyncio
async def test_warnings_returns_copy() -> None:
    collector = WarningCollector()
    await collector.emit("phase1", WarningCode.THIN_BUNDLE, "thin bundle")
    copy = collector.warnings
    copy.clear()
    assert collector.has_warnings() is True
    assert len(collector.warnings) == 1


# ---------------------------------------------------------------------------
# Phase 4 integration tests
# ---------------------------------------------------------------------------


def _make_facts_manifest(groups: dict[str, int] | None = None):
    from mcp_server.providers.custom.models import FactsManifest
    return FactsManifest(
        project_id="test_proj",
        platform="talend",
        canonical_unit_label="job",
        unit_count=1,
        groups=groups or {},
    )


def _make_context(staging_dir: Path):
    from mcp_server.providers.base import DocGenContext
    ctx = MagicMock(spec=DocGenContext)
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging_dir
    ctx.logs_abs = str(staging_dir / "logs")
    return ctx


def _make_helpers(staging_dir: Path):
    helpers = MagicMock()
    helpers.enterprise_gate_report.return_value = {}
    helpers.archive_engineering_artifacts.return_value = []
    helpers.required_docs_list = []
    helpers.model_provider.model_name = "test-model"
    helpers.write_provider.model_name = "test-model"

    # Ensure enterprise_gates.json exists so Phase 4 skips the gate block
    assessment = staging_dir / "_assessment"
    assessment.mkdir(parents=True, exist_ok=True)
    (assessment / "enterprise_gates.json").write_text("{}", encoding="utf-8")
    return helpers


@pytest.mark.asyncio
async def test_phase4_writes_warnings_json(tmp_path: Path) -> None:
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    staging = tmp_path / "docs_staging"
    staging.mkdir()

    collector = WarningCollector()
    await collector.emit("phase1", WarningCode.PHASE1_GROUP_FAILURE, "group X failed", {"group": "X"})

    state = HarnessRunState(
        project_id="test_proj",
        facts_manifest=_make_facts_manifest(),
        warning_collector=collector,
    )

    helpers = _make_helpers(staging)
    ctx = _make_context(staging)

    phase = Phase4Cleanup(helpers=helpers)
    result = await phase.run(ctx, state, log_cb=MagicMock())

    assert result.success is True
    # warnings.json is debug-only; verify the warning survived in the collector
    assert not (staging / "_assessment" / "warnings.json").exists()
    assert not (staging / "_debug" / "_assessment" / "warnings.json").exists()
    assert len(state.warning_collector.warnings) == 1
    assert state.warning_collector.warnings[0].code == WarningCode.PHASE1_GROUP_FAILURE
    assert state.warning_collector.warnings[0].context == {"group": "X"}


@pytest.mark.asyncio
async def test_phase4_no_warnings_json_on_clean_run(tmp_path: Path) -> None:
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    staging = tmp_path / "docs_staging"
    staging.mkdir()

    state = HarnessRunState(
        project_id="test_proj",
        facts_manifest=_make_facts_manifest(),
    )

    helpers = _make_helpers(staging)
    ctx = _make_context(staging)

    phase = Phase4Cleanup(helpers=helpers)
    result = await phase.run(ctx, state, log_cb=MagicMock())

    assert result.success is True
    assert not (staging / "_assessment" / "warnings.json").exists(), (
        "warnings.json must NOT be written on a clean run"
    )


@pytest.mark.asyncio
async def test_phase4_builds_rag_support_before_deleting_analysis(tmp_path: Path) -> None:
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    staging = tmp_path / "docs_staging"
    analysis = staging / "_analysis"
    analysis.mkdir(parents=True)
    (analysis / "A.md").write_text("# Group A analysis", encoding="utf-8")
    (analysis / "_doc_pack_01_ARCHITECTURE_MAP.md").write_text("pack", encoding="utf-8")
    (staging / "01_OVERVIEW.md").write_text("# Overview", encoding="utf-8")

    state = HarnessRunState(
        project_id="test_proj",
        facts_manifest=_make_facts_manifest(groups={"A": 1}),
    )

    helpers = _make_helpers(staging)
    ctx = _make_context(staging)

    phase = Phase4Cleanup(helpers=helpers)
    result = await phase.run(ctx, state, log_cb=MagicMock())

    assert result.success is True
    assert not (staging / "_analysis").exists(), "_analysis must still be cleaned up"
    package_dir = staging / "_rag_support" / "phase2_analysis"
    assert (package_dir / "A.md").read_text(encoding="utf-8") == "# Group A analysis"
    assert sorted(p.name for p in package_dir.iterdir()) == ["A.md"]
    manifest = json.loads(
        (staging / "_rag_support" / "manifest.json").read_text(encoding="utf-8")
    )
    assert [a["group"] for a in manifest["artifacts"]] == ["A"]
    assert manifest["missing_groups"] == []
    assert (staging / "01_OVERVIEW.md").read_text(encoding="utf-8") == "# Overview", (
        "top-level visible docs must be untouched by packaging"
    )


@pytest.mark.asyncio
async def test_phase4_logs_missing_group_analyses(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock

    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    staging = tmp_path / "docs_staging"
    staging.mkdir()

    state = HarnessRunState(
        project_id="test_proj",
        facts_manifest=_make_facts_manifest(groups={"A": 1, "B": 2}),
    )

    helpers = _make_helpers(staging)
    ctx = _make_context(staging)
    log_cb = AsyncMock()

    phase = Phase4Cleanup(helpers=helpers)
    result = await phase.run(ctx, state, log_cb=log_cb)

    assert result.success is True
    manifest = json.loads(
        (staging / "_rag_support" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["missing_groups"] == ["A", "B"]
    assert manifest["artifacts"] == []
    warning_calls = [c for c in log_cb.await_args_list if c.args[2] == "warning"]
    assert any("A, B" in c.args[1] for c in warning_calls)
