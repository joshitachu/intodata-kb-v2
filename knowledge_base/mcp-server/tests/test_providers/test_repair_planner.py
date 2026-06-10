from __future__ import annotations

from pathlib import Path

from mcp_server.providers.custom.models import CoverageReport, FactsManifest, UnitFact
from mcp_server.providers.custom.repair_planner import (
    REPAIR_TOKEN_CAP,
    deterministic_phase2_section_patch,
    estimate_repair_tokens,
    mentioned_units_by_group,
    phase2_report_plan,
)


def _report(*, errors: list[str] | None = None, missing_units: list[str] | None = None) -> CoverageReport:
    return CoverageReport(
        ok=False,
        missing_units=missing_units or [],
        coverage_count=0,
        expected_count=1,
        errors=errors or [],
    )


def test_phase2_group_summary_failure_is_deterministic_patch() -> None:
    plan = phase2_report_plan(
        report=_report(errors=["Phase 2 analysis for group Jobs missing required sections: GROUP_SUMMARY"]),
        report_kind="guide_sections",
    )

    assert plan.packets[0].action == "deterministic_patch"
    assert plan.packets[0].trigger_kind == "section_heading"


def test_phase2_depth_only_failure_does_not_trigger_llm_repair() -> None:
    plan = phase2_report_plan(
        report=_report(errors=["Phase 2 analysis for group Jobs is too shallow: 3000 bytes < 4000 required"]),
        report_kind="depth",
    )

    assert plan.packets[0].action == "no_repair_warning"
    assert plan.packets[0].trigger_kind == "byte_depth"


def test_phase2_missing_covered_marker_is_deterministic_patch() -> None:
    plan = phase2_report_plan(
        report=_report(missing_units=["CustomerSyncJob"]),
        report_kind="coverage",
    )

    assert plan.packets[0].action == "deterministic_patch"
    assert plan.packets[0].trigger_kind == "coverage_marker"


def test_mentioned_units_by_group_finds_prose_mentions(tmp_path: Path) -> None:
    analysis = tmp_path / "_analysis"
    analysis.mkdir()
    (analysis / "Jobs.md").write_text("CustomerSyncJob loads customer data.\n", encoding="utf-8")
    facts = FactsManifest(
        project_id="p",
        platform="generic",
        canonical_unit_label="units",
        unit_count=1,
        groups={"Jobs": 1},
        units=[UnitFact(name="CustomerSyncJob", group="Jobs")],
    )

    found = mentioned_units_by_group(facts, analysis, ["CustomerSyncJob"])

    assert found == {"Jobs": ["CustomerSyncJob"]}


def test_deterministic_section_patch_adds_group_summary_without_llm_content() -> None:
    patched, missing = deterministic_phase2_section_patch(
        "### Existing Findings\n\nCustomerSyncJob loads customer data.\n",
        platform="generic",
        group="Jobs",
    )

    assert missing == ["GROUP_SUMMARY"]
    assert "### GROUP_SUMMARY" in patched
    assert "no new source claims" in patched


def test_deterministic_section_patch_does_not_shell_substantive_talend_sections() -> None:
    patched, missing = deterministic_phase2_section_patch(
        "### COMPONENTS\n\nCustomerSyncJob component inventory.\n",
        platform="talend",
        group="Jobs",
    )

    assert "GROUP_SUMMARY" in missing
    added = patched.split("### COMPONENTS", 1)[1]
    assert "### GROUP_SUMMARY" in added
    assert "### RISK_FLAGS" not in patched


def test_repair_token_estimate_exposes_over_cap_context(tmp_path: Path) -> None:
    huge = tmp_path / "huge.md"
    huge.write_text("x" * ((REPAIR_TOKEN_CAP + 1) * 4), encoding="utf-8")

    assert estimate_repair_tokens("repair", [huge]) > REPAIR_TOKEN_CAP
