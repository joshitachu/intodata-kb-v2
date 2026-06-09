"""Slice 3: EvidenceQualityGate and Phase 3 thin-bundle notice tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server.providers.custom.models import CoverageReport, FactsManifest, HarnessRunState
from mcp_server.providers.custom.pipeline.evidence_quality_gate import EvidenceQualityGate
from mcp_server.providers.custom.pipeline.warning_collector import WarningCode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state() -> HarnessRunState:
    return HarnessRunState(
        project_id="test_proj",
        facts_manifest=FactsManifest(
            project_id="test_proj",
            platform="generic",
            canonical_unit_label="unit",
            unit_count=2,
        ),
    )


def _make_ctx(staging: Path, groups: list[str]) -> MagicMock:
    ctx = MagicMock()
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging
    ctx.preprocess_groups = groups
    return ctx


# ---------------------------------------------------------------------------
# EvidenceQualityGate unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_detects_thin_bundle(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    preprocess = staging / "_preprocess"
    preprocess.mkdir()
    (preprocess / "jobs.md").write_text("x" * 100, encoding="utf-8")

    state = _make_state()
    ctx = _make_ctx(staging, ["jobs"])

    result = await EvidenceQualityGate().run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert state.thin_bundle_groups == ["jobs"]
    assert state.warning_collector.has_warnings()
    w = state.warning_collector.warnings[0]
    assert w.code == WarningCode.THIN_BUNDLE
    assert w.context["group"] == "jobs"
    assert w.context["size_bytes"] == 100

    report = staging / "_assessment" / "thin_bundle_report.json"
    assert report.exists()
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["thin_groups"] == ["jobs"]
    assert data["threshold_bytes"] == 500


@pytest.mark.asyncio
async def test_gate_ok_on_sufficient_bundle(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    preprocess = staging / "_preprocess"
    preprocess.mkdir()
    (preprocess / "jobs.md").write_text("x" * 1000, encoding="utf-8")

    state = _make_state()
    ctx = _make_ctx(staging, ["jobs"])

    result = await EvidenceQualityGate().run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert state.thin_bundle_groups == []
    assert not state.warning_collector.has_warnings()


@pytest.mark.asyncio
async def test_gate_detects_missing_bundle(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_preprocess").mkdir()
    # no file written — bundle is absent

    state = _make_state()
    ctx = _make_ctx(staging, ["jobs"])

    result = await EvidenceQualityGate().run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert "jobs" in state.thin_bundle_groups
    w = state.warning_collector.warnings[0]
    assert w.context["size_bytes"] == 0


@pytest.mark.asyncio
async def test_gate_resume_loads_from_report(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    assessment = staging / "_assessment"
    assessment.mkdir()
    (assessment / "thin_bundle_report.json").write_text(
        json.dumps({"schema_version": 1, "thin_groups": ["jobs"], "threshold_bytes": 500}),
        encoding="utf-8",
    )

    state = _make_state()
    ctx = _make_ctx(staging, ["jobs"])

    result = await EvidenceQualityGate().resume(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert result.was_cached is True
    assert state.thin_bundle_groups == ["jobs"]


@pytest.mark.asyncio
async def test_gate_always_returns_success(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    preprocess = staging / "_preprocess"
    preprocess.mkdir()
    # all groups thin — still success
    for group in ["g1", "g2", "g3"]:
        (preprocess / f"{group}.md").write_text("x", encoding="utf-8")

    state = _make_state()
    ctx = _make_ctx(staging, ["g1", "g2", "g3"])

    result = await EvidenceQualityGate().run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert len(state.thin_bundle_groups) == 3


# ---------------------------------------------------------------------------
# Phase 3 thin-bundle notice tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase3_prepends_notice_for_thin_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.providers.custom.pipeline.phases.phase3_docs import Phase3Docs

    staging = tmp_path / "staging"
    staging.mkdir()
    assessment = staging / "_assessment"
    assessment.mkdir()

    passing = CoverageReport(ok=True, coverage_count=2)
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_docs",
        lambda *a, **kw: passing,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_doc_depth",
        lambda *a, **kw: passing,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_source_provenance",
        lambda *a, **kw: passing,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.scanned_config_files",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.covered_source_files",
        lambda *a, **kw: [],
    )

    helpers = MagicMock()
    helpers.phase3_docs = AsyncMock()
    helpers.reconcile_sql_usage_classification.return_value = 0
    helpers.validate_sql_usage_publication.return_value = []
    helpers.required_docs_list = ["01_OVERVIEW"]
    helpers.plugin_mode = False
    helpers.pack_contract_mode = False
    helpers.enterprise_gate_report.return_value = {"gates": {}}

    # Write the doc BEFORE running (simulating what phase3_docs would produce)
    doc_path = staging / "01_OVERVIEW.md"
    doc_path.write_text("Original content.", encoding="utf-8")

    state = HarnessRunState(
        project_id="test_proj",
        facts_manifest=FactsManifest(
            project_id="test_proj", platform="generic",
            canonical_unit_label="unit", unit_count=2,
        ),
        thin_bundle_groups=["jobs"],
    )

    ctx = MagicMock()
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging
    ctx.enterprise_mode = False

    phase = Phase3Docs(helpers=helpers)
    result = await phase.run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    final = doc_path.read_text(encoding="utf-8")
    assert final.startswith("> **Evidence Notice:**")
    assert "jobs" in final
    assert "Original content." in final
    assert all(ord(c) < 128 for c in final.split("\n")[0])  # ASCII-only notice


@pytest.mark.asyncio
async def test_phase3_no_notice_on_clean_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_server.providers.custom.pipeline.phases.phase3_docs import Phase3Docs

    staging = tmp_path / "staging"
    staging.mkdir()
    assessment = staging / "_assessment"
    assessment.mkdir()

    passing = CoverageReport(ok=True, coverage_count=2)
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_docs",
        lambda *a, **kw: passing,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_doc_depth",
        lambda *a, **kw: passing,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.validate_source_provenance",
        lambda *a, **kw: passing,
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.scanned_config_files",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(
        "mcp_server.providers.custom.validators.covered_source_files",
        lambda *a, **kw: [],
    )

    helpers = MagicMock()
    helpers.phase3_docs = AsyncMock()
    helpers.reconcile_sql_usage_classification.return_value = 0
    helpers.validate_sql_usage_publication.return_value = []
    helpers.required_docs_list = ["01_OVERVIEW"]
    helpers.plugin_mode = False
    helpers.pack_contract_mode = False
    helpers.enterprise_gate_report.return_value = {"gates": {}}

    doc_path = staging / "01_OVERVIEW.md"
    original_text = "Original content."
    doc_path.write_text(original_text, encoding="utf-8")

    state = HarnessRunState(
        project_id="test_proj",
        facts_manifest=FactsManifest(
            project_id="test_proj", platform="generic",
            canonical_unit_label="unit", unit_count=2,
        ),
    )

    ctx = MagicMock()
    ctx.project_id = "test_proj"
    ctx.staging_dir = staging
    ctx.enterprise_mode = False

    phase = Phase3Docs(helpers=helpers)
    result = await phase.run(ctx, state, log_cb=AsyncMock())

    assert result.success is True
    assert doc_path.read_text(encoding="utf-8") == original_text
