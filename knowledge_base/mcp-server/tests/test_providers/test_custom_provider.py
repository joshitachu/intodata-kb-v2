from __future__ import annotations

import json
import re
import asyncio
from pathlib import Path

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.agent_runtime import AgentTaskContract, AgentTaskRuntime, ScopedEvidenceTools, ToolBudget
from mcp_server.providers.custom.harness import CustomHarnessProvider, FailureKind, use_tools_for_core_generation
from mcp_server.providers.custom.investigator import (
    EvidenceValidator,
    InvestigationError,
    StagingToolbox,
    parse_json_action,
    write_investigation_artifacts,
)
from mcp_server.providers.custom.models import (
    CoverageReport,
    HarnessRunState,
    InvestigationEvidence,
    InvestigationReport,
    ModelOptions,
    ModelResult,
)
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES, materialize_prompt_packs
from mcp_server.providers.custom.prompts import REQUIRED_DOCS
from mcp_server.providers.custom.providers import OpenAICompatibleProvider, _execute_staging_tool
from mcp_server.providers.custom.validators import (
    build_facts_manifest,
    missing_required_analysis_sections,
    normalize_analysis_guide_headings,
    validate_analysis_depth,
    validate_analysis_coverage,
    validate_analysis_guide_sections,
    validate_doc_depth,
    validate_docs,
    validate_investigation_coverage,
)


def _write_scan(out_dir: Path, *, platform: str, jobs: list[dict], files: list[dict] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scan_result.json").write_text(json.dumps({
        "platform": platform,
        "repo_root": "/repo",
        "files": files or [],
        "jobs": jobs,
        "manifest": {},
        "warnings": [],
    }), encoding="utf-8")


def _job(name: str, group: str = "A", component_type: str = "tMap") -> dict:
    return {
        "name": name,
        "path": f"{name}.item",
        "group": group,
        "components": [{"unique_name": f"{component_type}_1", "component_type": component_type}],
    }


def _scorecard_text() -> str:
    return (
        "Score: 3/5\nConfidence: HIGH\nEvidence: scanned facts\n"
        "Red Flags: HIGH missing_error_path\nQuick Wins: add verification\nUnknowns: runtime SLA\n"
    )


def _assistant_doc_text(doc: str, facts, extra: str = "") -> str:
    unit_rows = "\n".join(
        f"| {u.name} | {u.group} | {u.path} | {u.unit_type} | purpose | dependency | HIGH |"
        for u in facts.units
    )
    unit_names = ", ".join(u.name for u in facts.units)
    paths = ", ".join(u.path for u in facts.units)
    platform_terms = {
        "talend": (
            "job, .item, group, context group, trigger, component, unique_name, "
            "FLOW, tMap, schema, context variable, REPOSITORY, tLogCatcher, execution order, validation query"
        ),
        "workato": (
            "recipe, callable function, folder, export artifact, trigger, step, action, block, "
            "datapill, formula, lookup table, property, connection, monitor, job history"
        ),
        "lobster": (
            "profile, agent, XML, format definition, schedule, route, parser, mapping, "
            "input format, output format, variable, exception profile, profile run"
        ),
        "generic": (
            "module, service, script, config, source artifact, entrypoint, handler, function, "
            "API payload, SQL, environment variable, exception handler, health check"
        ),
    }.get(facts.platform, "source artifact, entrypoint, dependency, config, error handling, test")
    common = (
        f"Canonical inventory: {facts.unit_count} {facts.canonical_unit_label}.\n"
        f"Evidence confidence: HIGH. Source artifacts: {paths}.\n"
        f"Units covered: {unit_names}.\n"
        f"Platform evidence includes {platform_terms}.\n"
        "Risk: HIGH missing_error_path. Confidence: HIGH. Evidence: facts_manifest.json.\n"
    )
    bodies = {
        "00_AGENT_BRIEF": (
            "# Agent Brief\n\n## Project Orientation\n" + common +
            "\n## Documentation Map\nRead 01_ARCHITECTURE_MAP then 02_COMPONENT_INVENTORY.\n"
            "## Onboarding\nStart with scanned artifacts.\n## Common Tasks\nTrace data, change config, debug runs.\n"
            "## Known Traps\nDo not ignore scanner-limited evidence.\n"
        ),
        "02_COMPONENT_INVENTORY": (
            "# Component Inventory\n\n## Canonical Unit Inventory\n"
            "| Unit | Group | Source Artifact | Type | Purpose | Dependencies | Confidence |\n"
            "|------|-------|-----------------|------|---------|--------------|------------|\n"
            f"{unit_rows}\n\n## Source Artifacts\n{paths}\n## Components\ncomponent evidence.\n"
            "## Dependencies\ndependency evidence.\n## Scanner-Limited Evidence\nNone.\n"
        ),
        "06_OPERATIONS_TESTING_AND_DEBUGGING": (
            "# Operations Testing And Debugging\n\n## Run\n" + common +
            "\n## Debug\nInspect logs.\n## Verify\nRun validation query.\n"
            "## Recovery\nRollback if needed.\n## Testing\nTest critical flow.\n"
        ),
        "07_QUALITY_SECURITY_RISK_SCORECARD": (
            "# Quality Security Risk Scorecard\n\n## Score\n" + common +
            "\n## Confidence\nHIGH.\n## Evidence\nfacts_manifest.json.\n"
            "## Red Flags\nHIGH missing_error_path.\n## Quick Wins\nAdd tests.\n## Unknowns\nRuntime SLA.\n"
        ),
        "08_CHANGE_PLAYBOOK": (
            "# Change Playbook\n\n## Change Impact\n" + common +
            "\n## Inspect\nInspect source artifact and dependency.\n## Test\nTest and Verify changed flow.\n"
            "## Rollback\nRollback deployment if validation fails.\n"
        ),
    }
    return bodies.get(
        doc,
        f"# {doc.replace('_', ' ').title()}\n\n## Overview\n{common}\n## Evidence\n{unit_names}\n"
    ) + extra


def _write_assistant_docs(staging: Path, facts, extra_by_doc: dict[str, str] | None = None) -> None:
    (staging / "_assessment").mkdir(parents=True, exist_ok=True)
    (staging / "_assessment" / "scorecard.md").write_text(_scorecard_text(), encoding="utf-8")
    extra_by_doc = extra_by_doc or {}
    for doc in REQUIRED_DOCS:
        (staging / f"{doc}.md").write_text(
            _assistant_doc_text(doc, facts, extra_by_doc.get(doc, "")),
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    ("platform", "label", "unit_type"),
    [
        ("talend", "jobs", "job"),
        ("workato", "recipes/functions", "recipe"),
        ("lobster", "profiles", "profile"),
        ("generic", "code units", "code_unit"),
    ],
)
def test_facts_manifest_generation_for_platforms(tmp_path, platform, label, unit_type):
    component = "WorkatoRecipe" if platform == "workato" else "Profile" if platform == "lobster" else "tMap"
    _write_scan(tmp_path, platform=platform, jobs=[_job("unit_1", "GroupA", component)])

    facts = build_facts_manifest("proj", tmp_path)

    assert facts.platform == platform
    assert facts.canonical_unit_label == label
    assert facts.unit_count == 1
    assert facts.groups == {"GroupA": 1}
    assert facts.units[0].unit_type == unit_type


def test_raw_source_audit_count_matches_parsed_units(tmp_path):
    files = [
        {"relative_path": "jobs/a.item", "file_type": "job"},
        {"relative_path": "jobs/b.item", "file_type": "job"},
    ]
    _write_scan(tmp_path, platform="talend", jobs=[_job("a"), _job("b")], files=files)

    facts = build_facts_manifest("proj", tmp_path)

    assert facts.raw_source_audit["detected_source_units"] == 2
    assert facts.raw_source_audit["parsed_units"] == 2
    assert facts.raw_source_audit["parse_gap"] == 0


def test_provider_factory_defaults_to_custom(monkeypatch):
    from mcp_server.config import config
    from mcp_server.providers import get_provider
    from mcp_server.providers.custom import CustomHarnessProvider

    monkeypatch.setattr(config, "doc_generation_provider", "custom")
    monkeypatch.setattr(config, "doc_generation_model", "moonshot/test-model")
    monkeypatch.setattr(config, "doc_generation_write_model", "moonshot/test-model")

    provider = get_provider()

    assert isinstance(provider, CustomHarnessProvider)


def test_publish_gate_fails_on_missing_group_analysis(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A"), _job("b", "B")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "A.md").write_text("## Coverage\n\n- COVERED: a\n", encoding="utf-8")

    report = validate_analysis_coverage(analysis_dir, facts)

    assert not report.ok
    assert "b" in report.missing_units
    assert any("Missing Phase 2 analysis for group B" in e for e in report.errors)


def test_publish_gate_fails_on_coverage_count_mismatch(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a"), _job("b")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "A.md").write_text("## Coverage\n\n- COVERED: a\n", encoding="utf-8")

    report = validate_analysis_coverage(analysis_dir, facts)

    assert not report.ok
    assert report.coverage_count == 1
    assert report.expected_count == 2


def test_publish_gate_passes_for_complete_coverage(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a"), _job("b")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n- COVERED: b\n",
        encoding="utf-8",
    )

    report = validate_analysis_coverage(analysis_dir, facts)

    assert report.ok
    assert report.coverage_count == 2


def test_publish_gate_passes_for_duplicate_unit_names_in_different_groups(tmp_path):
    _write_scan(
        tmp_path / "scan",
        platform="generic",
        jobs=[
            _job("README", "QuestBoard"),
            _job("README", "root"),
        ],
    )
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "QuestBoard.md").write_text("## Coverage\n\n- COVERED: README\n", encoding="utf-8")
    (analysis_dir / "root.md").write_text("## Coverage\n\n- COVERED: README\n", encoding="utf-8")

    report = validate_analysis_coverage(analysis_dir, facts)

    assert report.ok
    assert report.coverage_count == 2
    assert report.expected_count == 2
    assert report.missing_units == []
    assert len(report.covered_unit_ids) == 2
    assert any("Duplicate unit name 'README'" in warning for warning in report.warnings)


def test_publish_gate_fails_for_real_missing_duplicate_identity(tmp_path):
    _write_scan(
        tmp_path / "scan",
        platform="generic",
        jobs=[
            _job("README", "QuestBoard"),
            _job("README", "root"),
        ],
    )
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "QuestBoard.md").write_text("## Coverage\n\n- COVERED: README\n", encoding="utf-8")
    (analysis_dir / "root.md").write_text("## Coverage\n\nNo coverage marker here.\n", encoding="utf-8")

    report = validate_analysis_coverage(analysis_dir, facts)

    assert not report.ok
    assert report.coverage_count == 1
    assert report.expected_count == 2
    assert report.missing_unit_ids == ["root:README.item:README"]
    assert "README [root: README.item]" in report.missing_units


def test_investigation_coverage_passes_for_duplicate_unit_names_in_different_groups(tmp_path):
    _write_scan(
        tmp_path / "scan",
        platform="generic",
        jobs=[
            _job("README", "QuestBoard"),
            _job("README", "root"),
        ],
    )
    facts = build_facts_manifest("proj", tmp_path / "scan")
    reports = [
        InvestigationReport(
            group="QuestBoard",
            source_groups=["QuestBoard"],
            evidence=[
                InvestigationEvidence(
                    unit_name="README",
                    category="overview",
                    source_file="QuestBoard/README.md",
                    observation="QuestBoard README is present.",
                )
            ],
        ),
        InvestigationReport(
            group="root",
            source_groups=["root"],
            evidence=[
                InvestigationEvidence(
                    unit_name="README",
                    category="overview",
                    source_file="README.md",
                    observation="Root README is present.",
                )
            ],
        ),
    ]

    report = validate_investigation_coverage(reports, facts)

    assert report.ok
    assert report.coverage_count == 2
    assert len(report.covered_unit_ids) == 2


def test_doc_gate_fails_on_conflicting_unit_count(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a"), _job("b")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts, {"00_AGENT_BRIEF": "\nTotal jobs: 1 jobs.\n"})

    report = validate_docs(staging, facts)

    assert not report.ok
    assert any("conflicting total inventory claim" in e for e in report.errors)


def test_doc_gate_allows_group_and_pattern_counts(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[
        _job("a", "Account"),
        _job("b", "Account"),
        _job("c", "Item"),
        _job("d", "Item"),
        _job("e", "SalesOrder"),
    ])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts, {
        "00_AGENT_BRIEF": (
            "\nAccount group contains 2 jobs.\nItem (2 jobs) handles products.\n"
            "SalesOrder has 1 job.\nFull detail is available for ~4 jobs.\n"
            "3 of 5 jobs use a shared pattern.\n"
        )
    })

    report = validate_docs(staging, facts)

    assert report.ok


def test_doc_gate_fails_on_dynamic_conflicting_total_claim(tmp_path):
    _write_scan(tmp_path / "scan", platform="lobster", jobs=[
        _job("profile_a", "A"),
        _job("profile_b", "A"),
        _job("profile_c", "B"),
    ])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts, {"00_AGENT_BRIEF": "\nProject contains 2 profiles.\n"})

    report = validate_docs(staging, facts)

    assert not report.ok
    assert any("Project contains 2 profiles" in e for e in report.errors)


def test_analysis_depth_gate_fails_on_shallow_complete_coverage(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A"), _job("b", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    preprocess_dir = tmp_path / "preprocess"
    analysis_dir.mkdir()
    preprocess_dir.mkdir()
    (preprocess_dir / "A.md").write_text("source scan facts " * 1000, encoding="utf-8")
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n- COVERED: b\n\n"
        "## Model Analysis\n\n"
        "Inventory. Component. Data flow. Business. External. Operation. Security. Migration. Scanner. a b\n",
        encoding="utf-8",
    )

    report = validate_analysis_depth(analysis_dir, preprocess_dir, facts)

    assert not report.ok
    assert any("too shallow" in e for e in report.errors)


def test_analysis_depth_gate_passes_with_dynamic_deep_content(tmp_path):
    _write_scan(tmp_path / "scan", platform="workato", jobs=[
        _job("Recipe One", "Recipes", "WorkatoRecipe"),
        _job("Function One", "Recipes", "WorkatoRecipeFunction"),
    ])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    preprocess_dir = tmp_path / "preprocess"
    analysis_dir.mkdir()
    preprocess_dir.mkdir()
    (preprocess_dir / "Recipes.md").write_text("source scan facts " * 200, encoding="utf-8")
    deep = (
        "Inventory component data flow business external operation security migration scanner. "
        "Recipe One Function One. "
        * 220
    )
    (analysis_dir / "Recipes.md").write_text(
        "## Coverage\n\n- COVERED: Recipe One\n- COVERED: Function One\n\n"
        f"## Model Analysis\n\n{deep}",
        encoding="utf-8",
    )

    report = validate_analysis_depth(analysis_dir, preprocess_dir, facts)

    assert report.ok
    assert report.coverage_count == 2


def test_materialize_prompt_packs_writes_harness_safe_assets(tmp_path):
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (staging / "_assessment").mkdir()
    (preprocess / "_analysis_guide.md").write_text(
        "# Guide\nSpawn one sub-agent per group.\n### COMPONENTS\nSemantic detail.\n",
        encoding="utf-8",
    )
    (preprocess / "_phase3_guide.md").write_text("# Phase 3\nSemantic synthesis.\n", encoding="utf-8")
    (preprocess / "_doc_templates.md").write_text("# Templates\nStructure.\n", encoding="utf-8")
    (preprocess / "_ref_core.md").write_text("# Ref\nReference.\n", encoding="utf-8")
    for name in [
        "_scoring_rubric.md",
        "_anti_patterns.md",
        "_red_flags.md",
        "_security_checklist.md",
        "_pattern_library.md",
    ]:
        (preprocess / name).write_text(f"# {name}\ncontent\n", encoding="utf-8")

    packs = materialize_prompt_packs(staging, "_analysis_guide.md")

    phase2 = Path(packs["phase2"])
    assert phase2.exists()
    content = phase2.read_text(encoding="utf-8")
    assert "## Source: _analysis_guide.md" in content
    assert "Spawn one sub-agent per group." in content
    assert "Semantic detail." in content
    assert Path(packs["phase26"]).exists()
    assert Path(packs["phase3"]["core"]).exists()
    assert Path(packs["phase35"]).exists()


def test_analysis_guide_section_gate_requires_talend_headings(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n### COMPONENTS\ncontent\n",
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert not report.ok
    assert any("EXECUTION_FLOW" in error for error in report.errors)


def test_analysis_guide_section_gate_passes_with_all_talend_sections(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    body = "\n".join(f"### {name}\ncontent for {name} and a.\n" for name in ANALYSIS_SECTION_NAMES)
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n" + body,
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert report.ok


def test_normalize_analysis_guide_headings_accepts_model_heading_level(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    body = "\n".join(f"## {name}\ncontent for {name} and a.\n" for name in ANALYSIS_SECTION_NAMES)
    normalized = normalize_analysis_guide_headings(body, "talend")
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n" + normalized,
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert report.ok
    assert missing_required_analysis_sections(normalized, "talend") == []


def test_doc_depth_gate_fails_on_slim_docs_with_correct_counts(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a"), _job("b")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_preprocess" / "A.md").write_text("source scan facts " * 200, encoding="utf-8")
    for doc in REQUIRED_DOCS:
        (staging / f"{doc}.md").write_text(
            "Canonical inventory: 2 jobs.\nA group covers unit a and unit b.\n",
            encoding="utf-8",
        )

    report = validate_doc_depth(staging, facts)

    assert not report.ok
    assert any("too shallow" in e for e in report.errors)


def test_docs_validation_fails_when_scanned_config_is_called_unavailable(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("Web.config", "root", "ConfigFile")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_assessment").mkdir(parents=True)
    (staging / "_assessment" / "scorecard.md").write_text("scorecard evidence\n" * 4, encoding="utf-8")
    (staging / "config_runtime_map.json").write_text(json.dumps([{
        "config_type": "config_file",
        "key": "Web.config",
        "name": "Web.config",
        "value_hint": "content scanned; sensitive values redacted",
        "metadata": {"path": "Web.config"},
    }]), encoding="utf-8")
    _write_assistant_docs(
        staging,
        facts,
        {"00_AGENT_BRIEF": "\nWeb.config content is unavailable and was not scanned.\n"},
    )

    report = validate_docs(staging, facts)

    assert not report.ok
    assert any("contradict scanned config evidence" in e for e in report.errors)


def test_docs_validation_allows_redacted_config_values(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("Web.config", "root", "ConfigFile")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_assessment").mkdir(parents=True)
    (staging / "_assessment" / "scorecard.md").write_text("scorecard evidence\n" * 4, encoding="utf-8")
    (staging / "config_runtime_map.json").write_text(json.dumps([{
        "config_type": "config_file",
        "key": "Web.config",
        "name": "Web.config",
        "value_hint": "content scanned; sensitive values redacted",
        "metadata": {"path": "Web.config"},
    }]), encoding="utf-8")
    _write_assistant_docs(staging, facts)

    report = validate_docs(staging, facts)

    assert report.ok


def test_docs_validation_uses_source_coverage_for_unavailable_claims(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("Web.config", "root", "ConfigFile")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_assessment").mkdir(parents=True)
    (staging / "_assessment" / "scorecard.md").write_text("scorecard evidence\n" * 4, encoding="utf-8")
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_preprocess" / "_source_coverage.json").write_text(json.dumps({
        "mode": "direct_redacted",
        "files": [{"path": "Web.config", "status": "included_full"}],
    }), encoding="utf-8")
    _write_assistant_docs(
        staging,
        facts,
        {"00_AGENT_BRIEF": "\nWeb.config was not scanned and its content is unavailable.\n"},
    )

    report = validate_docs(staging, facts)

    assert not report.ok
    assert any("contradict scanned config evidence" in e for e in report.errors)


def test_docs_validation_allows_unavailable_code_claim_near_scanned_config(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("Web.config", "root", "ConfigFile")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_assessment").mkdir(parents=True)
    (staging / "_assessment" / "scorecard.md").write_text("scorecard evidence\n" * 4, encoding="utf-8")
    (staging / "config_runtime_map.json").write_text(json.dumps([{
        "config_type": "config_file",
        "key": "Web.config",
        "name": "Web.config",
        "value_hint": "content scanned; sensitive values redacted",
        "metadata": {"path": "Web.config"},
    }]), encoding="utf-8")
    _write_assistant_docs(
        staging,
        facts,
        {"00_AGENT_BRIEF": "\nThe exact code is not available in the scan, so downstream service logic is scanner-limited.\n"},
    )

    report = validate_docs(staging, facts)

    assert report.ok


def test_parse_json_action_accepts_fenced_json():
    action = parse_json_action('```json\n{"action":"list_dir","path":"_preprocess"}\n```')

    assert action == {"action": "list_dir", "path": "_preprocess"}


def test_parse_json_action_rejects_unknown_action():
    with pytest.raises(InvestigationError):
        parse_json_action('{"action":"write_file","path":"x"}')


def test_staging_toolbox_blocks_outside_access(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    toolbox = StagingToolbox(staging)

    with pytest.raises(InvestigationError):
        toolbox.execute({"action": "read_file", "path": "../secret.txt"})


def test_investigation_artifacts_and_coverage_validation(tmp_path):
    _write_scan(tmp_path / "scan", platform="workato", jobs=[_job("Recipe One", "Recipes", "WorkatoRecipe")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_preprocess" / "Recipes.md").write_text("Recipe One trigger detail", encoding="utf-8")
    payload = {
        "evidence": [{
            "unit_name": "Recipe One",
            "category": "inventory",
            "source_file": "_preprocess/Recipes.md",
            "line_range": "1-1",
            "snippet": "Recipe One trigger detail",
            "observation": "Recipe One appears in the scanner inventory with trigger detail.",
        }],
        "skipped_units": {},
    }
    validator = EvidenceValidator(facts, StagingToolbox(staging))
    report = validator.validate_report("Recipes", payload, {"Recipe One"})
    json_path, md_path = write_investigation_artifacts(report, staging / "_investigation")

    coverage = validate_investigation_coverage([report], facts)

    assert json_path.exists()
    assert md_path.exists()
    assert coverage.ok


def test_investigation_evidence_rejects_unknown_unit(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_preprocess" / "A.md").write_text("a component detail", encoding="utf-8")
    validator = EvidenceValidator(facts, StagingToolbox(staging))

    with pytest.raises(InvestigationError, match="unknown canonical unit"):
        validator.validate_report("A", {
            "evidence": [{
                "unit_name": "missing",
                "category": "inventory",
                "source_file": "_preprocess/A.md",
                "line_range": "1-1",
                "snippet": "a component detail",
                "observation": "The unit has concrete component detail in the scanner output.",
            }],
        }, {"a"})


def test_investigation_evidence_rejects_missing_snippet(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_preprocess" / "A.md").write_text("a component detail", encoding="utf-8")
    validator = EvidenceValidator(facts, StagingToolbox(staging))

    with pytest.raises(InvestigationError, match="snippet"):
        validator.validate_report("A", {
            "evidence": [{
                "unit_name": "a",
                "category": "inventory",
                "source_file": "_preprocess/A.md",
                "line_range": "1-1",
                "observation": "The unit has concrete component detail in the scanner output.",
            }],
        }, {"a"})


def test_investigation_coverage_fails_when_unit_missing(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A"), _job("b", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")

    report = validate_investigation_coverage([], facts)

    assert not report.ok
    assert report.coverage_count == 0
    assert "a" in report.missing_units
    assert "b" in report.missing_units


@pytest.mark.asyncio
async def test_provider_adapter_normalizes_success(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }

    class _Client:
        last_payload = None

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            _Client.last_payload = kwargs.get("json")
            return _Response()

    monkeypatch.setattr("mcp_server.providers.custom.providers.httpx.AsyncClient", _Client)
    provider = OpenAICompatibleProvider("test-model", api_key="key", base_url="http://example.test/v1")

    result = await provider.complete("hello", options=ModelOptions(retries=0))

    assert result.success
    assert result.text == "ok"
    assert result.input_tokens == 10
    assert result.output_tokens == 2
    assert result.model_name == "test-model"
    assert "temperature" in _Client.last_payload


@pytest.mark.asyncio
async def test_provider_adapter_normalizes_error(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr("mcp_server.providers.custom.providers.httpx.AsyncClient", _Client)
    provider = OpenAICompatibleProvider("test-model", api_key="key", base_url="http://example.test/v1")

    result = await provider.complete("hello", options=ModelOptions(retries=0))

    assert not result.success
    assert "network down" in (result.raw_error or "")
    assert result.input_tokens > 0
    assert result.model_name == "test-model"


@pytest.mark.asyncio
async def test_provider_adapter_includes_http_error_body(monkeypatch):
    class _Response:
        status_code = 400
        reason_phrase = "Bad Request"
        text = '{"error":"bad request"}'

        def raise_for_status(self):
            request = type("Req", (), {"method": "POST", "url": "http://example.test/v1/chat/completions"})()
            raise httpx.HTTPStatusError("bad", request=request, response=self)

        def json(self):
            return {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return _Response()

    import httpx

    monkeypatch.setattr("mcp_server.providers.custom.providers.httpx.AsyncClient", _Client)
    provider = OpenAICompatibleProvider("test-model", api_key="key", base_url="http://example.test/v1")

    result = await provider.complete("hello", options=ModelOptions(retries=0))

    assert not result.success
    assert "400 Bad Request" in (result.raw_error or "")
    assert '{"error":"bad request"}' in (result.raw_error or "")


@pytest.mark.asyncio
async def test_moonshot_adapter_omits_temperature(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }

    class _Client:
        last_payload = None

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            _Client.last_payload = kwargs.get("json")
            return _Response()

    monkeypatch.setattr("mcp_server.providers.custom.providers.httpx.AsyncClient", _Client)
    from mcp_server.providers.custom.providers import MoonshotKimiProvider

    provider = MoonshotKimiProvider("kimi-k2.6", api_key="key", base_url="https://api.moonshot.ai/v1")

    result = await provider.complete("hello", options=ModelOptions(retries=0))

    assert result.success
    assert "temperature" not in (_Client.last_payload or {})
    assert _Client.last_payload["model"] == "kimi-k2.6"
    assert _Client.last_payload["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_ollama_adapter_fails_fast_when_unreachable(monkeypatch):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise RuntimeError("connection refused")

        async def post(self, *args, **kwargs):
            raise AssertionError("chat completions should not be attempted when preflight fails")

    monkeypatch.setattr("mcp_server.providers.custom.providers.httpx.AsyncClient", _Client)
    from mcp_server.providers.custom.providers import OllamaCompatibleProvider

    provider = OllamaCompatibleProvider("qwen2.5-coder:7b", base_url="http://100.90.200.34:11434")

    result = await provider.complete("hello", options=ModelOptions(retries=0))

    assert not result.success
    assert "Ollama endpoint unreachable" in (result.raw_error or "")
    assert "100.90.200.34:11434" in (result.raw_error or "")


@pytest.mark.asyncio
async def test_ollama_adapter_requests_large_context(monkeypatch):
    class _VersionResponse:
        def raise_for_status(self):
            return None

    class _ChatResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Client:
        last_payload = None

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return _VersionResponse()

        async def post(self, *args, **kwargs):
            _Client.last_payload = kwargs.get("json")
            return _ChatResponse()

    monkeypatch.setattr("mcp_server.providers.custom.providers.httpx.AsyncClient", _Client)
    from mcp_server.providers.custom.providers import OllamaCompatibleProvider

    provider = OllamaCompatibleProvider("qwen2.5-coder:7b", base_url="http://100.90.200.34:11434")

    result = await provider.complete("hello", options=ModelOptions(retries=0))

    assert result.success
    assert _Client.last_payload["options"]["num_ctx"] == 32768
    assert result.metadata["provider_options"]["options"]["num_ctx"] == 32768


class _FakeProvider:
    model_name = "fake/model"
    calls: list[dict] | None = None

    async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
        if self.calls is not None:
            self.calls.append({
                "prompt": prompt,
                "context_names": [p.name for p in context_files or []],
                "options": options,
            })

        if "Allowed actions:" in prompt and '"action":"finish"' in prompt:
            if "Latest tool observation JSON" not in prompt:
                match = re.search(r"Primary group file: ([^\n]+)", prompt)
                path = match.group(1).strip() if match else "_preprocess/Recipes.md"
                return ModelResult(
                    success=True,
                    text=json.dumps({"action": "read_file", "path": path, "offset": 0, "max_bytes": 4000}),
                    model_name=self.model_name,
                    input_tokens=10,
                    output_tokens=20,
                )
            units_block = prompt.split("Allowed canonical units:", 1)[1].split("\n\n", 1)[0]
            units = [line[2:].strip() for line in units_block.splitlines() if line.startswith("- ")]
            source_match = re.search(r"Primary group file: ([^\n]+)", prompt)
            source = source_match.group(1).strip() if source_match else "_preprocess/Recipes.md"
            evidence = []
            for unit in units:
                evidence.append({
                    "unit_name": unit,
                    "category": "inventory",
                    "source_file": source,
                    "line_range": "1-3",
                    "snippet": f"{unit} scanner inventory",
                    "observation": f"{unit} appears in the preprocessed scanner inventory for this group.",
                })
            return ModelResult(
                success=True,
                text=json.dumps({"action": "finish", "evidence": evidence, "skipped_units": {}}),
                model_name=self.model_name,
                input_tokens=20,
                output_tokens=40,
            )

        if "Return ONLY a valid JSON object" in prompt and '"evidence"' in prompt:
            units_block = prompt.split("Units to cover:", 1)[1].split("\n\n", 1)[0]
            units = [line[2:].strip() for line in units_block.splitlines() if line.startswith("- ")]
            source_match = re.search(r"Source file: ([^\n]+)", prompt)
            source = source_match.group(1).strip() if source_match else "_preprocess/Recipes.md"
            evidence = []
            for unit in units:
                evidence.append({
                    "unit_name": unit,
                    "category": "inventory",
                    "source_file": source,
                    "line_range": "1-3",
                    "snippet": unit,
                    "observation": f"{unit} appears in the supplied scan data with concrete inventory detail.",
                })
            return ModelResult(
                success=True,
                text=json.dumps({"evidence": evidence, "skipped_units": {}}),
                model_name=self.model_name,
                input_tokens=12,
                output_tokens=24,
            )

        if "You are a documentation critic" in prompt:
            return ModelResult(
                success=True,
                text='{"expansions":[]}',
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=2,
            )

        deep_text = (
            "# Deep Generated Analysis\n\n"
            "Canonical inventory: 2 recipes/functions. Source artifacts: Recipe One.item, Function One.item.\n"
            "Platform evidence includes recipe, callable function, folder, export artifact, trigger, step, action, block, "
            "datapill, formula, lookup table, property, connection, monitor, job history.\n"
            "Score: 3/5. Confidence: HIGH. Evidence: facts_manifest.json. Red Flags: HIGH risk. Quick Wins: add tests. Unknowns: runtime SLA.\n"
            "Change Impact: inspect source artifacts, Test changed flows, Verify outputs, Rollback if validation fails.\n"
            "Documentation Map, Onboarding, Common Tasks, Known Traps. Debug and Recovery guidance with Testing evidence.\n"
            "## Inventory and purpose\n"
            "Recipe One and Function One are covered as canonical units in the Recipes and Functions groups.\n"
            "| Unit | Group | Source Artifact | Type | Purpose | Dependencies | Confidence |\n"
            "|------|-------|-----------------|------|---------|--------------|------------|\n"
            "| Recipe One | Recipes | Recipe One.item | recipe | purpose | dependency | HIGH |\n"
            "| Function One | Functions | Function One.item | function | purpose | dependency | HIGH |\n"
            "## Components, actions, or implementation units\n"
            "Components and actions are described with component evidence and connector behavior.\n"
            "## Data flows and lineage\n"
            "Data flow details describe source systems, transformation, and target systems for each group.\n"
            "## Transformation and business logic\n"
            "Business rule evidence, transformation logic, and implementation rules are documented.\n"
            "## External systems and context/configuration\n"
            "External systems, context values, configuration, and properties are documented.\n"
            "## Operations, monitoring, and error handling\n"
            "Operation, execution, monitoring, retry, and error handling details are documented.\n"
            "## Security risks\n"
            "Security, credential, access, risk, severity, and mitigation details are documented.\n"
            "## Migration risks and recommendations\n"
            "Migration readiness, impact, testing, recovery, runbook, troubleshooting, FAQ, glossary, "
            "pattern, standard, anti-pattern, architecture, connectivity, component, environment, "
            "key findings, recommendations, scope, groups, lineage, inventory, scanner limitations, "
            "executive overview, access, credential, severity, mitigation, onboarding, plan.\n"
            "## Scanner limitations\n"
            "Scanner limitations are called out where source detail is incomplete.\n"
        )
        for section in ANALYSIS_SECTION_NAMES:
            deep_text += f"\n### {section}\n"
            deep_text += (
                f"{section} evidence for Recipe One and Function One includes component, data flow, "
                "business, external, operation, security, migration, and scanner detail.\n"
            )
        deep_text = deep_text + ("\nDetailed evidence paragraph for Recipe One and Function One. " * 260)
        return ModelResult(
            success=True,
            text=deep_text,
            model_name=self.model_name,
            input_tokens=10,
            output_tokens=2500,
            metadata={
                "estimated_input_tokens": 10,
                "context_bytes": sum(p.stat().st_size for p in context_files or [] if p.exists()),
                "provider_options": (options.provider_options if options else {}),
            },
        )


def test_core_generation_tools_enabled_for_tool_capable_provider():
    provider = OpenAICompatibleProvider(
        "deepseek-v4-pro",
        api_key="test",
        base_url="http://example.test/v1",
        provider_name="openai",
    )

    assert use_tools_for_core_generation(provider) is True


def test_deterministic_phase2_merge_preserves_canonical_sections():
    merged = CustomHarnessProvider._deterministic_phase2_merge(
        "Orders",
        [
            "Intro evidence\n\n### COMPONENTS\nComponent A\n\n### DATA_FLOWS\nFlow A",
            "### COMPONENTS\nComponent B\n\nLoose finding\n\n### RISK_FLAGS\nRisk B",
        ],
    )

    assert "### COMPONENTS" in merged
    assert "#### Orders_chunk_1" in merged
    assert "#### Orders_chunk_2" in merged
    assert "Component A" in merged
    assert "Component B" in merged
    assert "### DATA_FLOWS" in merged
    assert "Flow A" in merged
    assert "### RISK_FLAGS" in merged
    assert "Risk B" in merged
    assert "### ADDITIONAL_FINDINGS" in merged
    assert "Intro evidence" in merged


def test_targeted_analysis_bundle_keeps_relevant_exact_sections(tmp_path):
    analysis = tmp_path / "Orders.md"
    analysis.write_text(
        "# Phase 2 Analysis: Orders\n\n"
        "### COMPONENTS\nExact component evidence\n\n"
        "### DATA_FLOWS\nExact lineage evidence\n\n"
        "### BUSINESS_RULES\nBusiness rule should not be in lineage bundle\n\n"
        "### RISK_FLAGS\nExact risk evidence\n",
        encoding="utf-8",
    )

    bundle = CustomHarnessProvider._targeted_analysis_bundle_text(
        [analysis],
        {"COMPONENTS", "DATA_FLOWS", "RISK_FLAGS"},
    )

    assert "Exact component evidence" in bundle
    assert "Exact lineage evidence" in bundle
    assert "Exact risk evidence" in bundle
    assert "Business rule should not be in lineage bundle" not in bundle


def test_final_doc_content_only_adds_unit_inventory_to_component_inventory():
    facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    state = HarnessRunState(project_id="proj", facts_manifest=facts)

    inventory = harness._final_doc_content(state, "02_COMPONENT_INVENTORY", "# Inventory\n\nInventory body.")
    brief = harness._final_doc_content(state, "00_AGENT_BRIEF", "# Brief\n\nBrief body.")

    assert "## Canonical Unit Inventory" in inventory
    assert "Unit A" in inventory
    assert "## Canonical Unit Inventory" not in brief
    assert "Unit A" not in brief


def test_phase4_cleanup_archives_engineering_markdown_off_top_level(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "engineering_facts.md").write_text("# Engineering Facts\n", encoding="utf-8")
    (staging / "dependency_map.md").write_text("# Dependency Map\n", encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    state = HarnessRunState(
        project_id="proj",
        facts_manifest=facts,
        engineering_fact_artifacts=["engineering_facts.md", "dependency_map.md"],
    )
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/deepseek",
        write_model="fake/deepseek",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))._phase4_cleanup(ctx, state)

    assert not (staging / "engineering_facts.md").exists()
    assert not (staging / "dependency_map.md").exists()
    assert (staging / "_facts" / "engineering_facts.md").exists()
    index = json.loads((staging / "index.json").read_text(encoding="utf-8"))
    assert index["engineering_facts"] == ["_facts/dependency_map.md", "_facts/engineering_facts.md"]


def test_phase4_cleanup_preserves_generation_warnings(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    state = HarnessRunState(
        project_id="proj",
        facts_manifest=facts,
        warnings=["Generated doc 06_OPERATIONS_TESTING_AND_DEBUGGING.md missing expected sections/terms: Debug"],
    )
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/deepseek",
        write_model="fake/deepseek",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))._phase4_cleanup(ctx, state)

    index = json.loads((staging / "index.json").read_text(encoding="utf-8"))
    assert index["warnings_count"] == 1
    assert "06_OPERATIONS_TESTING_AND_DEBUGGING.md" in index["warnings"][0]


def test_phase3_repair_targets_uses_doc_named_in_validation_error(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "02_COMPONENT_INVENTORY.md").write_text("# Component Inventory\n", encoding="utf-8")
    (staging / "06_OPERATIONS_TESTING_AND_DEBUGGING.md").write_text("# Operations\n", encoding="utf-8")
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/deepseek",
        write_model="fake/deepseek",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    targets = CustomHarnessProvider._phase3_repair_targets(
        ctx,
        ["Generated doc 06_OPERATIONS_TESTING_AND_DEBUGGING.md missing expected sections/terms: Debug"],
    )

    assert targets == [(
        staging / "06_OPERATIONS_TESTING_AND_DEBUGGING.md",
        ["Generated doc 06_OPERATIONS_TESTING_AND_DEBUGGING.md missing expected sections/terms: Debug"],
    )]


@pytest.mark.asyncio
async def test_core_generation_splits_context_and_excludes_large_json(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 400)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 800)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    large_md = staging / "group.md"
    large_md.write_text("Unit A evidence\n" * 1000, encoding="utf-8")
    large_json = staging / "engineering_facts.json"
    large_json.write_text('{"too":"large"}' * 1000, encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _ToolCapableProvider:
        model_name = "fake/deepseek"
        provider_name = "openai"
        supports_tools = False

        def __init__(self):
            self.calls = []

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            self.calls.append([p.name for p in context_files or []])
            return ModelResult(
                success=True,
                text="slice output for Unit A",
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=5,
                metadata={"estimated_input_tokens": 10, "context_bytes": 1},
            )

        async def complete_with_tools(self, *args, **kwargs):
            raise AssertionError("core generation must not use tools")

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/deepseek",
        write_model="fake/deepseek",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append(message)

    provider = _ToolCapableProvider()
    harness = CustomHarnessProvider(provider)
    result = await harness._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase2",
        target="A",
        prompt="Analyze Unit A",
        context_files=[large_json, large_md],
        opts=ModelOptions(),
        log_cb=log_cb,
    )

    assert result.success
    assert len(provider.calls) > 1
    assert all("engineering_facts.json" not in names for names in provider.calls)
    assert any("split decision" in message for message in logs)


@pytest.mark.asyncio
async def test_core_generation_uses_complete_with_tools_for_tool_capable_provider(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    context_file = staging / "group.md"
    context_file.write_text("Unit A evidence\n" * 100, encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _ToolProvider:
        model_name = "fake/tool-model"
        provider_name = "openai"
        supports_tools = True

        def __init__(self):
            self.tool_calls = []

        async def complete(self, *args, **kwargs):
            raise AssertionError("tool-capable core generation must use complete_with_tools")

        async def complete_with_tools(self, prompt, staging_dir, options=None, max_turns=30, scoped_tools=None):
            self.tool_calls.append(
                {
                    "prompt": prompt,
                    "staging_dir": staging_dir,
                    "has_scoped_tools": scoped_tools is not None,
                }
            )
            return ModelResult(
                success=True,
                text="Unit A authored markdown with sufficient details for validation.",
                model_name=self.model_name,
                input_tokens=8,
                output_tokens=12,
                metadata={"finish_reason": "stop", "turns": 1, "tool_calls": 0},
            )

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/tool-model",
        write_model="fake/tool-model",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    provider = _ToolProvider()
    contract = AgentTaskContract(
        phase="phase2_group",
        target_id="A",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        starter_context_files=["group.md"],
        expected_artifact="analysis markdown",
    )
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase2",
        target="A",
        prompt="Analyze Unit A",
        context_files=[context_file],
        opts=ModelOptions(),
        log_cb=log_cb,
        task_contract=contract,
        scoped_tools=ScopedEvidenceTools(staging, facts, contract),
    )

    assert result.success
    assert provider.tool_calls
    assert provider.tool_calls[0]["has_scoped_tools"]
    assert "Starter file hints" in provider.tool_calls[0]["prompt"]
    assert "group.md" in provider.tool_calls[0]["prompt"]


def test_context_splitter_never_returns_oversized_fixed_remainder(tmp_path, monkeypatch):
    from mcp_server.config import config
    from mcp_server.providers.custom.harness import estimate_input_tokens

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 400)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 800)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    prompt_pack = staging / "_phase3_prompt_pack_core.md"
    prompt_pack.write_text("phase 3 instructions\n" * 90, encoding="utf-8")
    bundle = staging / "_bundle_core.md"
    bundle.write_text("analysis evidence\n" * 900, encoding="utf-8")
    csv_facts = staging / "entity_index.csv"
    csv_facts.write_text("entity,row\n" * 700, encoding="utf-8")
    scorecard = staging / "scorecard.md"
    scorecard.write_text("scorecard facts\n" * 120, encoding="utf-8")
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/deepseek",
        write_model="fake/deepseek",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))

    slices = harness._split_context_pack(
        ctx,
        "phase3",
        "02_COMPONENT_INVENTORY",
        "Write the component inventory.",
        [prompt_pack, bundle, csv_facts, scorecard],
        hard_tokens=config.core_context_hard_ceiling_tokens,
        soft_tokens=config.core_context_soft_target_tokens,
    )

    assert len(slices) > 1
    assert all(
        estimate_input_tokens("Write the project overview.", slice_files)
        <= config.core_context_hard_ceiling_tokens
        for slice_files in slices
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            ModelResult(success=False, raw_error="HTTPStatusError: 413 Payload Too Large"),
            FailureKind.CONTEXT_TOO_LARGE,
        ),
        (
            ModelResult(success=False, raw_error="HTTPStatusError: 419 Authentication Timeout"),
            FailureKind.UNKNOWN,
        ),
        (
            ModelResult(success=True, text="", output_tokens=0),
            FailureKind.EMPTY_OUTPUT,
        ),
        (
            ModelResult(success=False, raw_error="HTTPStatusError: 429 Too Many Requests"),
            FailureKind.RATE_LIMIT,
        ),
        (
            ModelResult(success=False, raw_error="HTTPStatusError: 503 Service Unavailable"),
            FailureKind.NETWORK,
        ),
        (
            ModelResult(success=False, raw_error="HTTPStatusError: 401 Unauthorized"),
            FailureKind.AUTH_ERROR,
        ),
    ],
)
def test_model_failure_classifier_is_conservative(result, expected):
    assert CustomHarnessProvider._classify_failure(result) == expected


@pytest.mark.asyncio
async def test_empty_output_does_not_trigger_recursive_split_retry(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 120)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 180)
    monkeypatch.setattr(config, "core_slice_concurrency", 2)
    monkeypatch.setattr(config, "docgen_empty_outputs_per_doc_limit", 3)
    monkeypatch.setattr(config, "docgen_empty_outputs_per_run_limit", 10)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    large_md = staging / "group.md"
    large_md.write_text(("Unit A evidence " + ("x" * 400)) * 80, encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _BlankProvider:
        model_name = "fake/model"
        provider_name = "openai"
        supports_tools = False

        def __init__(self):
            self.calls = 0

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            self.calls += 1
            return ModelResult(
                success=True,
                text="",
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=0,
            )

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append(message)

    provider = _BlankProvider()
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase3",
        target="00_AGENT_BRIEF",
        prompt="Write Unit A docs",
        context_files=[large_md],
        opts=ModelOptions(retries=0),
        log_cb=log_cb,
    )

    assert not result.success
    assert provider.calls < 20
    assert "empty_output" in (result.raw_error or "")
    assert not any("retrying phase3" in message for message in logs)


@pytest.mark.asyncio
async def test_context_length_failure_can_trigger_split_retry(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 120)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 100_000)
    monkeypatch.setattr(config, "core_slice_concurrency", 2)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    large_md = staging / "group.md"
    large_md.write_text(("Unit A evidence " + ("x" * 400)) * 80, encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _ContextFailThenSuccessProvider:
        model_name = "fake/model"
        provider_name = "openai"
        supports_tools = False

        def __init__(self):
            self.calls = 0

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResult(
                    success=False,
                    model_name=self.model_name,
                    input_tokens=10,
                    raw_error="HTTPStatusError: 413 Payload Too Large",
                )
            return ModelResult(
                success=True,
                text=f"slice-{self.calls}",
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=3,
            )

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append(message)

    provider = _ContextFailThenSuccessProvider()
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase3",
        target="00_AGENT_BRIEF",
        prompt="Write Unit A docs",
        context_files=[large_md],
        opts=ModelOptions(retries=0),
        log_cb=log_cb,
    )

    assert result.success
    assert provider.calls > 1
    assert any("context limit failure" in message for message in logs)


@pytest.mark.asyncio
async def test_core_generation_runs_split_slices_concurrently_in_order(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 120)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 180)
    monkeypatch.setattr(config, "core_slice_concurrency", 3)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    large_md = staging / "group.md"
    large_md.write_text(("\n## Section\nUnit A evidence " + ("x" * 400)) * 80, encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _ConcurrentProvider:
        model_name = "fake/deepseek"
        provider_name = "openai"
        supports_tools = False

        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.targets: list[str] = []

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            target = re.search(r"slice (\d+) of", prompt)
            slice_no = target.group(1) if target else "0"
            self.targets.append(slice_no)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return ModelResult(
                success=True,
                text=f"slice-{slice_no}",
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=5,
                metadata={"estimated_input_tokens": 10, "context_bytes": 1},
            )

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/deepseek",
        write_model="fake/deepseek",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append(message)

    provider = _ConcurrentProvider()
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase2.6",
        target="scorecard",
        prompt="Score Unit A",
        context_files=[large_md],
        opts=ModelOptions(),
        log_cb=log_cb,
    )

    assert result.success
    assert provider.max_active > 1
    assert provider.max_active <= 3
    assert result.text.split("\n\n---\n\n") == [f"slice-{idx}" for idx in range(1, len(provider.targets) + 1)]


def build_facts_manifest_from_units(project_id: str, names: list[str]):
    from mcp_server.providers.custom.models import FactsManifest, UnitFact

    return FactsManifest(
        project_id=project_id,
        platform="generic",
        canonical_unit_label="code units",
        unit_count=len(names),
        groups={"A": len(names)},
        units=[UnitFact(name=name, group="A") for name in names],
    )


def test_scoped_evidence_tools_enforce_contract_and_budgets(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "engineering_facts.md").write_text("Unit A inventory evidence\n" * 50, encoding="utf-8")
    contract = AgentTaskContract(
        phase="phase2_group",
        target_id="A",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        allowed_categories=["inventory"],
        tool_budget=ToolBudget(max_calls=1, max_bytes=400, max_result_bytes=200),
        expected_artifact="analysis markdown",
    )
    tools = ScopedEvidenceTools(staging, facts, contract)

    text = tools.get_unit_evidence("Unit A", ["inventory"], limit=1)
    assert "Source: engineering_facts.md" in text

    with pytest.raises(RuntimeError, match="tool call budget exceeded"):
        tools.get_unit_evidence("Unit A", ["inventory"], limit=1)

    tools = ScopedEvidenceTools(staging, facts, contract.model_copy(update={"tool_budget": ToolBudget(max_calls=3)}))
    with pytest.raises(ValueError, match="unit outside task contract"):
        tools.get_unit_evidence("Unit B", ["inventory"], limit=1)
    with pytest.raises(ValueError, match="categories outside task contract"):
        tools.search_evidence("Unit A", categories=["security"], limit=1)


def test_provider_scoped_tool_execution_tracks_scope_errors(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "engineering_facts.md").write_text("Unit A inventory evidence\n", encoding="utf-8")
    contract = AgentTaskContract(
        phase="phase2_group",
        target_id="A",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        allowed_categories=["inventory"],
        expected_artifact="analysis markdown",
    )
    tools = ScopedEvidenceTools(staging, facts, contract)

    text = _execute_staging_tool(
        "get_unit_evidence",
        {"unit_name": "Unit A", "categories": ["inventory"], "limit": 1},
        staging,
        tools,
    )
    assert "Unit A inventory evidence" in text
    with pytest.raises(ValueError, match="unit outside task contract"):
        _execute_staging_tool("get_unit_evidence", {"unit_name": "Unit B"}, staging, tools)
    assert tools.calls[0].tool == "get_unit_evidence"
    assert tools.calls[0].ok
    assert tools.calls[0].bytes > 0
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_openai_tool_loop_accounts_turns_tokens_and_tool_results(tmp_path, monkeypatch):
    import httpx
    import mcp_server.providers.custom.providers as provider_module

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "engineering_facts.md").write_text("Unit A inventory evidence\n", encoding="utf-8")
    calls = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            calls.append(json)
            request = httpx.Request("POST", url)
            if len(calls) == 1:
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "read_artifact",
                                                "arguments": '{"path":"engineering_facts.md"}',
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                    },
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "Unit A final analysis"},
                        }
                    ],
                    "usage": {"prompt_tokens": 17, "completion_tokens": 9},
                },
            )

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _FakeAsyncClient)
    provider = OpenAICompatibleProvider(
        "fake/model",
        api_key="test",
        base_url="http://example.test/v1",
        provider_name="openai",
    )

    result = await provider.complete_with_tools("Analyze Unit A", staging, ModelOptions())

    assert result.success
    assert result.input_tokens == 28
    assert result.output_tokens == 12
    assert result.metadata["turns"] == 2
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["tool_result_bytes"] > 0
    assert result.metadata["finish_reasons"] == ["tool_calls", "stop"]
    assert result.metadata["tool_call_ledger"][0]["tool"] == "read_artifact"


@pytest.mark.asyncio
async def test_openai_tool_loop_rejects_length_finish_reason(tmp_path, monkeypatch):
    import httpx
    import mcp_server.providers.custom.providers as provider_module

    staging = tmp_path / "docs_staging"
    staging.mkdir()

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers):
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": "partial output"},
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7},
                },
            )

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _FakeAsyncClient)
    provider = OpenAICompatibleProvider(
        "fake/model",
        api_key="test",
        base_url="http://example.test/v1",
        provider_name="openai",
    )

    result = await provider.complete_with_tools("Analyze Unit A", staging, ModelOptions())

    assert not result.success
    assert result.text == "partial output"
    assert result.metadata["finish_reason"] == "length"
    assert result.metadata["incomplete_reason"] == "length"


@pytest.mark.asyncio
async def test_agent_task_runtime_persists_exact_output_and_resume(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    runtime = AgentTaskRuntime(staging, facts)
    contract = AgentTaskContract(
        phase="phase3_doc",
        target_id="02_COMPONENT_INVENTORY",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        expected_artifact="document markdown",
    )
    exact = "## Overview\n\nUnit A authored markdown."

    async def dispatch(task, tools):
        return ModelResult(success=True, text=exact, model_name="fake/model", input_tokens=3, output_tokens=4)

    result = await runtime.run(contract, dispatch, lambda text: {"ok": "Unit A" in text, "errors": []})

    assert result.accepted
    task_dir = staging / "_agent_tasks" / "phase3_doc" / "02_COMPONENT_INVENTORY"
    assert (task_dir / "contract.json").exists()
    assert (task_dir / "validation_report.json").exists()
    assert (task_dir / "tool_call_ledger.json").exists()
    assert (task_dir / "provider_metrics.json").exists()
    assert (task_dir / "status.json").exists()
    assert (task_dir / "conversation_trace.json").exists()
    assert (task_dir / "output.md").read_text(encoding="utf-8") == exact
    assert runtime.accepted_output(contract) == exact


@pytest.mark.asyncio
async def test_agent_task_runtime_rejects_length_and_marks_incomplete(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    runtime = AgentTaskRuntime(staging, facts)
    contract = AgentTaskContract(
        phase="phase2_group",
        target_id="A",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        expected_artifact="analysis markdown",
        max_attempts=1,
    )

    async def dispatch(task, tools):
        return ModelResult(
            success=False,
            text="partial Unit A",
            model_name="fake/model",
            raw_error="incomplete: provider stopped because max tokens were reached",
            metadata={"finish_reason": "length", "incomplete_reason": "length"},
        )

    result = await runtime.run(contract, dispatch, lambda text: {"ok": True, "errors": []})

    assert not result.accepted
    assert result.status == "incomplete"
    assert result.incomplete_reason == "length"
    status = json.loads((runtime.task_dir(contract) / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "incomplete"
    assert status["incomplete_reason"] == "length"


@pytest.mark.asyncio
async def test_agent_task_runtime_passes_validation_gaps_to_retry(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    runtime = AgentTaskRuntime(staging, facts)
    contract = AgentTaskContract(
        phase="phase3_doc",
        target_id="02_COMPONENT_INVENTORY",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        expected_artifact="document markdown",
        max_attempts=2,
    )
    seen_gaps = []

    async def dispatch(task, tools):
        seen_gaps.append(list(task.validation_gaps))
        if task.attempt == 1:
            return ModelResult(success=True, text="too shallow", model_name="fake/model")
        return ModelResult(success=True, text="Unit A fixed with required section", model_name="fake/model")

    def validate(text):
        if "Unit A fixed" in text:
            return {"ok": True, "errors": []}
        return {"ok": False, "errors": ["missing Unit A section"]}

    result = await runtime.run(contract, dispatch, validate)

    assert result.accepted
    assert seen_gaps == [[], ["missing Unit A section"]]
    trace = json.loads((runtime.task_dir(contract) / "conversation_trace.json").read_text(encoding="utf-8"))
    assert trace[1]["validation_gaps"] == ["missing Unit A section"]


@pytest.mark.asyncio
async def test_phase2_section_repair_appends_missing_talend_sections(tmp_path):
    from mcp_server.config import config

    project_id = "proj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="talend", jobs=[_job("a", "A")])
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    facts = build_facts_manifest(project_id, scan_dir)
    staging = tmp_path / "output" / project_id / "docs_staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    group_file = preprocess / "A.md"
    group_file.write_text("source scan facts for a", encoding="utf-8")

    class _RepairProvider:
        model_name = "fake/model"
        supports_tools = False

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            missing = [name for name in ANALYSIS_SECTION_NAMES if f"### {name}" in prompt]
            return ModelResult(
                success=True,
                text="\n".join(f"### {name}\nrepaired evidence for a\n" for name in missing),
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=100,
            )

    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        template_snapshot={},
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )
    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append((pid, message, level))

    partial = "## COMPONENTS\ncomponent evidence for a\n\n## EXECUTION_FLOW\nflow evidence for a"
    completed = await CustomHarnessProvider(_RepairProvider())._complete_phase2_sections(
        ctx,
        HarnessRunState(project_id=project_id, facts_manifest=facts),
        "A",
        partial,
        [],
        [],
        group_file,
        ModelOptions(),
        log_cb,
    )

    assert missing_required_analysis_sections(completed, "talend") == []
    assert "### COMPONENTS" in completed
    assert "### RISK_FLAGS" in completed
    assert any("guide-section repair complete" in message for _, message, _ in logs)
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_phase3_targeted_repair_appends_validator_gap(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
    staging = tmp_path / "docs_staging"
    (staging / "_assessment").mkdir(parents=True)
    (staging / "_analysis").mkdir()
    (staging / "_investigation").mkdir()
    facts.write(staging / "facts_manifest.json")
    (staging / "02_COMPONENT_INVENTORY.md").write_text("# Inventory\n\nUnit A is documented.\n", encoding="utf-8")
    (staging / "_analysis" / "_bundle_core.md").write_text("Unit B analysis evidence", encoding="utf-8")
    (staging / "_investigation" / "_summary.md").write_text("Unit B scanner evidence", encoding="utf-8")
    (staging / "_assessment" / "scorecard.md").write_text("Scorecard evidence", encoding="utf-8")

    class _RepairProvider:
        model_name = "fake/model"
        supports_tools = False

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            return ModelResult(
                success=True,
                text="### Missing Unit Coverage\n\nUnit B is covered from scanner evidence in the supplied analysis bundle.",
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=20,
            )

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=2,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        template_snapshot={},
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append(message)

    report = CoverageReport(
        ok=False,
        missing_units=["Unit B"],
        coverage_count=1,
        expected_count=2,
        errors=["Generated docs do not mention all units: Unit B"],
    )
    await CustomHarnessProvider(_RepairProvider())._phase3_targeted_repair(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        report,
        log_cb,
    )

    repaired = (staging / "02_COMPONENT_INVENTORY.md").read_text(encoding="utf-8")
    assert "Targeted Validator Repair" in repaired
    assert "Unit B" in repaired
    assert any("targeted repair starting" in message for message in logs)


@pytest.mark.asyncio
async def test_custom_harness_generates_valid_staging(tmp_path, monkeypatch):
    from mcp_server.config import config

    project_id = "proj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="workato", jobs=[
        _job("Recipe One", "Recipes", "WorkatoRecipe"),
        _job("Function One", "Functions", "WorkatoRecipeFunction"),
    ])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    monkeypatch.setattr(config, "model_pricing_json", '{"fake/model":{"input":1,"output":2}}')

    staging = tmp_path / "output" / project_id / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_analysis").mkdir()
    (staging / "_assessment").mkdir()
    (staging / "_preprocess" / "Recipes.md").write_text("Recipe One", encoding="utf-8")
    (staging / "_preprocess" / "Functions.md").write_text("Function One", encoding="utf-8")
    (staging / "_preprocess" / "_analysis_guide.md").write_text("Workato analysis guide", encoding="utf-8")
    (staging / "_preprocess" / "_phase3_guide.md").write_text("Workato phase 3 guide", encoding="utf-8")
    for name in [
        "_scoring_rubric.md",
        "_anti_patterns.md",
        "_red_flags.md",
        "_security_checklist.md",
        "_pattern_library.md",
        "_ref_core.md",
        "_ref_business_ops.md",
        "_ref_patterns.md",
        "_ref_user.md",
    ]:
        (staging / "_preprocess" / name).write_text(f"{name} content", encoding="utf-8")
    (staging / "_assessment" / "quick_scan.md").write_text("quick scan facts", encoding="utf-8")

    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Functions", "Recipes"],
        total_jobs=2,
        total_groups=2,
        effective_model="fake/model",
        write_model="fake/model",
        template_snapshot={},
        platform="workato",
        analysis_guide_file="_analysis_guide.md",
    )

    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append((pid, message, level))

    provider = _FakeProvider()
    provider.calls = []
    result = await CustomHarnessProvider(provider).run(ctx, log_cb)

    assert result.success
    assert result.cost_usd is not None
    assert result.cost_usd > 0
    assert (staging / "facts_manifest.json").exists()
    assert (staging / "_investigation" / "_summary.md").exists()
    assert (staging / "index.json").exists()
    assert all((staging / f"{doc}.md").exists() for doc in REQUIRED_DOCS)
    report = validate_docs(staging, build_facts_manifest(project_id, scan_dir))
    assert report.ok
    assert any("resolved providers" in message for _, message, _ in logs)
    assert any("_analysis_prompt_pack.md" in call["context_names"] for call in provider.calls)
    assert any("quick_scan.md" in call["context_names"] for call in provider.calls)
    assert any("_scorecard_prompt_pack.md" in call["context_names"] for call in provider.calls)
    assert any("_phase3_prompt_pack_core.md" in call["context_names"] for call in provider.calls)
    assert any("_summary.md" in call["context_names"] for call in provider.calls)
    assert not any("You are a documentation critic" in call["prompt"] for call in provider.calls)
