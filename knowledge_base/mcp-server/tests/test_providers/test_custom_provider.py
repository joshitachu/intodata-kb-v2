from __future__ import annotations

import json
import re
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.agent_runtime import AgentTaskContract, AgentTaskRuntime, ScopedEvidenceTools, ToolBudget
from mcp_server.providers.custom.dag import deterministic_execution_plan, validate_execution_plan
from mcp_server.providers.custom.constants import GROUP_SUMMARY_SECTION
from mcp_server.providers.custom.harness import (
    ADDITIONAL_FINDINGS_SECTION,
    BASELINE_BUNDLE_SECTIONS,
    CustomHarnessProvider,
    FailureKind,
    _guide_section_names,
    _provider_supports_tools,
)
from mcp_server.providers.custom.investigator import (
    ChunkManifestInvestigator,
    EvidenceValidator,
    InvestigationError,
    SingleShotInvestigator,
    StagingToolbox,
    parse_json_action,
    write_investigation_artifacts,
)
from mcp_server.providers.custom.models import (
    CoverageReport,
    FactsManifest,
    HarnessRunState,
    InvestigationEvidence,
    InvestigationReport,
    ModelOptions,
    ModelResult,
    ExecutionNode,
    UnitFact,
)
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES, materialize_prompt_packs
from mcp_server.providers.custom.doc_catalog import load_doc_catalog as _load_test_catalog
from mcp_server.providers.custom.prompts import doc_prompt

# Per-unit catalog entries expand at run start ({base}__{group}__{unit});
# the base name never exists as a file.
REQUIRED_DOCS = [d.name for d in _load_test_catalog().docs if not d.per_unit]
DOC_SECTION_REQUIREMENTS = _load_test_catalog().section_requirements
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
    validate_pack_quality,
    validate_source_provenance,
)
from mcp_server.services.plugin_schema import PluginPackManifest


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
    paths = ", ".join(u.path or f"{u.name}.source" for u in facts.units)
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
    evidence_register = (
        "\n## Evidence Register\n\n"
        "| Topic | Units / Scope | Evidence Source | Source Artifact / Path | Confidence |\n"
        "|-------|---------------|-----------------|------------------------|------------|\n"
        f"| Inventory | {unit_names} | facts_manifest.json scanner evidence | {paths} | HIGH |\n"
        f"| Dependencies | {unit_names} | dependency_map.md EngineeringFacts evidence | {paths} | MEDIUM |\n"
        f"| Runtime risk | {unit_names} | known_risks.md scorecard evidence | {paths} | LOW |\n"
    )
    bodies = {
        "00_AGENT_BRIEF": (
            "# Agent Brief\n\n## Project Orientation\n" + common +
            "\n## Documentation Map\nRead 01_ARCHITECTURE_MAP then 02_COMPONENT_INVENTORY.\n"
            "## Onboarding\nStart with scanned artifacts.\n## Common Tasks\nTrace data, change config, debug runs.\n"
            "## Known Traps\nDo not ignore scanner-limited evidence.\n"
            "## Glossary\nScanner-limited means evidence exists but detail is constrained.\n"
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
        f"# {doc.replace('_', ' ').title()}\n\n## Overview\n{common}\n"
        "## Topology\nTopology evidence. ## Boundaries\nBoundaries evidence. ## Dependencies\nDependencies evidence. "
        "## Entry Points\nEntry Points evidence. ## External Systems\nExternal Systems evidence.\n"
        "## Data Flow\nSource to Target with Transformation, Schema, Validation, and Scanner-Limited notes.\n"
        "## Business Rules\nExpression | Unit | Domain | Impact.\n"
        "## Runtime Config\nConfig rows include Redacted and Scanner-Limited status.\n"
        f"## Evidence\n{unit_names}\n"
    ) + evidence_register + extra


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
    monkeypatch.setattr(config, "doc_generation_model", "anthropic/claude-sonnet-4-6")
    monkeypatch.setattr(config, "doc_generation_write_model", "anthropic/claude-sonnet-4-6")
    monkeypatch.setattr(config, "doc_generation_investigation_model", None)
    monkeypatch.setattr(config, "doc_generation_fallback_model", None)

    provider = get_provider()

    assert isinstance(provider, CustomHarnessProvider)


def test_provider_factory_uses_investigation_override(monkeypatch):
    from mcp_server.config import config
    from mcp_server.providers import get_provider

    monkeypatch.setattr(config, "doc_generation_provider", "custom")
    monkeypatch.setattr(config, "doc_generation_model", "anthropic/claude-sonnet-4-6")
    monkeypatch.setattr(config, "doc_generation_write_model", "anthropic/claude-sonnet-4-6")
    monkeypatch.setattr(config, "doc_generation_investigation_model", "google/gemini-2.5-flash")
    monkeypatch.setattr(config, "doc_generation_fallback_model", None)

    provider = get_provider(investigation_model="deepseek/deepseek-v4-pro")

    assert provider.investigation_provider.provider_name == "deepseek"
    assert provider.investigation_provider.model_name == "deepseek-v4-pro"


def test_custom_provider_does_not_apply_deepseek_override_to_openai(monkeypatch):
    from mcp_server.config import config
    from mcp_server.providers.custom.harness import build_custom_provider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config, "doc_generation_api_key", "deepseek-key")
    monkeypatch.setattr(config, "doc_generation_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(config, "doc_generation_deepseek_api_key", None)
    monkeypatch.setattr(config, "doc_generation_deepseek_base_url", None)

    provider = build_custom_provider(
        "openai/gpt-5.4-mini",
        fallback_model_str="openai/gpt-5.4",
        investigation_model_str="google/gemini-2.5-flash",
    )

    assert provider.model_provider.provider_name == "openai"
    assert provider.model_provider.base_url == "https://api.openai.com/v1"
    assert provider.model_provider.api_key is None
    assert provider.fallback_provider.provider_name == "openai"
    assert provider.fallback_provider.base_url == "https://api.openai.com/v1"
    assert provider.investigation_provider.provider_name == "google"


def test_custom_provider_applies_legacy_override_to_deepseek(monkeypatch):
    from mcp_server.config import config
    from mcp_server.providers.custom.harness import build_custom_provider

    monkeypatch.setattr(config, "doc_generation_api_key", "deepseek-key")
    monkeypatch.setattr(config, "doc_generation_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(config, "doc_generation_deepseek_api_key", None)
    monkeypatch.setattr(config, "doc_generation_deepseek_base_url", None)

    provider = build_custom_provider("deepseek/deepseek-chat")

    assert provider.model_provider.provider_name == "deepseek"
    assert provider.model_provider.model_name == "deepseek-chat"
    assert provider.model_provider.api_key == "deepseek-key"
    assert provider.model_provider.base_url == "https://api.deepseek.com/v1"


def test_deterministic_execution_plan_includes_required_final_gates(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("a", "A"), _job("b", "B")])
    facts = build_facts_manifest("proj", tmp_path / "scan")

    plan = deterministic_execution_plan(
        facts,
        required_docs=["00_AGENT_BRIEF", "02_COMPONENT_INVENTORY"],
        preprocess_groups=["A", "B"],
        investigation_policy="run",
        analysis_policy="run",
        scorecard_policy="run",
        doc_bundles_policy="run",
        plugin_mode=False,
        small_project_threshold=0,
    )

    node_ids = {node.id for node in plan.graph.nodes}
    assert {"planner", "facts", "validate_docs", "enterprise_gates"}.issubset(node_ids)
    assert plan.analysis_mode == "full"
    assert validate_execution_plan(
        plan,
        facts,
        allowed_docs=["00_AGENT_BRIEF", "02_COMPONENT_INVENTORY"],
    ) == []


def test_execution_plan_validation_rejects_unsafe_graph_and_docs(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    plan = deterministic_execution_plan(
        facts,
        required_docs=["00_AGENT_BRIEF"],
        preprocess_groups=["A"],
        investigation_policy="skip",
        analysis_policy="bounded",
        scorecard_policy="skip",
        doc_bundles_policy="skip",
        plugin_mode=False,
        small_project_threshold=40,
    )
    plan.required_docs = ["UNDECLARED_DOC"]
    plan.graph.nodes = [
        ExecutionNode(id="a", type="facts", depends_on=["b"]),
        ExecutionNode(id="b", type="facts", depends_on=["a"]),
    ]

    errors = validate_execution_plan(plan, facts, allowed_docs=["00_AGENT_BRIEF"])

    assert any("undeclared docs" in error.lower() for error in errors)
    assert any("missing required final gates" in error.lower() for error in errors)
    assert any("cycle" in error.lower() for error in errors)


def test_dag_tool_policy_only_enables_explicit_tool_phases():
    provider_model = MagicMock()
    provider_model.supports_tools = True
    provider = CustomHarnessProvider(provider_model)

    assert provider._tool_policy(provider_model, "phase2").enabled is False
    assert provider._tool_policy(provider_model, "phase2.deep_dive").enabled is True
    assert provider._tool_policy(provider_model, "phase1.investigate").enabled is True
    assert provider._tool_policy(provider_model, "phase3.consistency").enabled is True


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


def test_plugin_phase2_coverage_uses_structured_sidecar_not_markers(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("README", "root"), _job("package", "root")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    coverage_dir = analysis_dir / "_coverage_units"
    coverage_dir.mkdir(parents=True)
    (analysis_dir / "root.md").write_text(
        "## Repository Snapshot\n\nREADME was inspected and package.json was analyzed.\n",
        encoding="utf-8",
    )
    (coverage_dir / "root.json").write_text(json.dumps({
        "group": "root",
        "expected_units": ["README", "package"],
        "analysis_file": "root.md",
        "status": "completed",
        "analysis_bytes": 72,
    }), encoding="utf-8")

    report = validate_analysis_coverage(analysis_dir, facts, use_structured=True)

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


def test_plugin_doc_gate_uses_pack_docs_not_default_required_docs(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("README", "root")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_assessment").mkdir(parents=True)
    (staging / "_assessment" / "scorecard.md").write_text(_scorecard_text(), encoding="utf-8")
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text(
        "# Webhook Smoke Test\n\n## Repository Snapshot\nREADME was scanned.\n",
        encoding="utf-8",
    )

    report = validate_docs(
        staging,
        facts,
        required_docs=["WEBHOOK_SMOKE_TEST"],
        enforce_default_contract=False,
    )

    assert report.ok
    assert not any("00_AGENT_BRIEF" in error for error in report.errors)


def test_table_inventory_phase2_context_uses_curated_evidence_bundle(tmp_path):
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (staging / "_assessment" / "prompt_packs").mkdir(parents=True)
    (staging / "facts_manifest.json").write_text('{"project_id":"proj"}', encoding="utf-8")
    (staging / "engineering_facts.md").write_text("Entity Customer maps to dbo.Customer.\n", encoding="utf-8")
    (preprocess / "Repository.md").write_text(
        "public class CustomerEntity {}\n"
        "SELECT id, name FROM dbo.Customer JOIN dbo.Order ON Customer.id = Order.customer_id\n"
        "CREATE TABLE dbo.Customer (id int, name varchar(100));\n",
        encoding="utf-8",
    )
    (preprocess / "logo.png.md").write_text("binary-looking resource should not be selected", encoding="utf-8")
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "plugin.yaml").write_text(
        json.dumps({
            "name": "table-inventory",
            "version": "1.0.0",
            "docs": [{
                "name": "TABLE_INVENTORY",
                "evidence_profile": "table_inventory",
                "required_sections": ["Table Inventory"],
                "required_evidence": ["sql_queries", "db_connections"],
                "required_outputs": ["table_inventory"],
            }],
            "evidence_profiles": {
                "table_inventory": {
                    "include": ["sql_queries", "ddl_schema", "orm_entities", "db_connections", "config_references"],
                    "exclude": ["binary", "static_assets", "generated_files"],
                },
            },
            "validation": {"unknown_policy": "explicit", "mode": "blocking"},
            "steps": [{"id": "full_pipeline", "type": "harness"}],
        }),
        encoding="utf-8",
    )
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Repository"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    provider = CustomHarnessProvider(MagicMock(), plugin_pack_dir=pack_dir)
    provider._prepare_pack_artifacts(ctx)
    files = provider._phase2_shared_context(
        ctx,
        staging / "facts_manifest.json",
    )

    bundle = staging / "_evidence" / "table_inventory.md"
    bundle_json = staging / "_evidence" / "table_inventory.json"
    plan = staging / "_plans" / "TABLE_INVENTORY.json"
    assert bundle in files
    assert bundle_json.exists()
    assert plan.exists()
    text = bundle.read_text(encoding="utf-8")
    assert "SELECT id, name FROM dbo.Customer" in text
    assert "CREATE TABLE dbo.Customer" in text
    assert "logo.png" not in text


def test_pack_contract_phase2_records_skip_model_analysis(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "plugin.yaml").write_text(
        json.dumps({
            "name": "table-inventory",
            "version": "1.0.0",
            "docs": [{"name": "TABLE_INVENTORY", "evidence_profile": "table_inventory"}],
            "evidence_profiles": {"table_inventory": {"include": ["source_summary"]}},
            "steps": [{"id": "full_pipeline", "type": "harness"}],
        }),
        encoding="utf-8",
    )
    facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    provider = CustomHarnessProvider(MagicMock(), plugin_pack_dir=pack_dir)
    provider._phase2_pack_contract_records(ctx, HarnessRunState(project_id="proj", facts_manifest=facts))

    analysis = staging / "_analysis" / "A.md"
    assert analysis.exists()
    assert "Broad enterprise Phase 2 model analysis is intentionally skipped" in analysis.read_text(encoding="utf-8")
    coverage = validate_analysis_coverage(staging / "_analysis", facts, use_structured=True)
    depth = validate_analysis_depth(staging / "_analysis", staging / "_preprocess", facts, use_structured=True)
    assert coverage.ok
    assert depth.ok


def test_pack_contract_phase3_context_excludes_enterprise_doc_pack(tmp_path):
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_assessment" / "prompt_packs").mkdir(parents=True)
    (staging / "_analysis").mkdir(parents=True)
    (staging / "facts_manifest.json").write_text("{}", encoding="utf-8")
    (staging / "_analysis" / "_doc_pack_TABLE_INVENTORY.md").write_text("huge enterprise pack", encoding="utf-8")
    (staging / "_assessment" / "prompt_packs" / "_phase3_prompt_pack_custom.md").write_text("prompt", encoding="utf-8")
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "plugin.yaml").write_text(
        json.dumps({
            "name": "table-inventory",
            "version": "1.0.0",
            "docs": [{"name": "TABLE_INVENTORY", "evidence_profile": "table_inventory"}],
            "evidence_profiles": {"table_inventory": {"include": ["source_summary"]}},
            "steps": [{"id": "full_pipeline", "type": "harness"}],
        }),
        encoding="utf-8",
    )
    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["root"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    provider = CustomHarnessProvider(MagicMock(), plugin_pack_dir=pack_dir)
    provider._prepare_pack_artifacts(ctx)
    files = provider._phase3_context_files(ctx, "TABLE_INVENTORY")

    assert staging / "_analysis" / "_doc_pack_TABLE_INVENTORY.md" not in files
    assert staging / "facts_manifest.json" in files
    assert staging / "_plans" / "TABLE_INVENTORY.json" in files


def _pack_manifest(mode: str = "blocking") -> PluginPackManifest:
    return PluginPackManifest.model_validate({
        "name": "webhook-smoke-test",
        "version": "1.0.0",
        "docs": [{"name": "WEBHOOK_SMOKE_TEST"}],
        "quality_gates": {
            "mode": mode,
            "required_sections": {"WEBHOOK_SMOKE_TEST": ["Repository Snapshot", "Smoke Test Result"]},
            "required_terms": ["scanned", "webhook", "PASS"],
            "required_evidence_terms": ["source"],
            "min_doc_bytes": {"WEBHOOK_SMOKE_TEST": 120},
        },
        "steps": [{"id": "full_pipeline", "type": "harness"}],
    })


def test_per_job_pack_expands_document_definitions_and_quality_gates():
    provider = CustomHarnessProvider(MagicMock())
    provider._plugin_manifest = PluginPackManifest.model_validate({
        "name": "per-job-pack",
        "version": "1.0.0",
        "docs": [{
            "name": "01_INTERFACE_TEMPLATE",
            "per_job": True,
            "required_sections": ["Integration Name"],
        }],
        "quality_gates": {
            "required_sections": {"01_INTERFACE_TEMPLATE": ["Integration Name"]},
            "min_doc_bytes": {"01_INTERFACE_TEMPLATE": 6000},
        },
        "steps": [{"id": "generate", "type": "harness"}],
    })
    provider._plugin_doc_definitions = {
        doc.name: doc for doc in provider._plugin_manifest.docs or []
    }
    facts = FactsManifest(
        project_id="p",
        platform="talend",
        canonical_unit_label="jobs",
        unit_count=2,
        groups={"Orders": 2},
        units=[UnitFact(name="LoadOrders", group="Orders"), UnitFact(name="NotifyOrders", group="Orders")],
    )

    provider._expand_per_job_docs(facts)

    assert provider._required_docs == [
        "01_INTERFACE_TEMPLATE__Orders__LoadOrders",
        "01_INTERFACE_TEMPLATE__Orders__NotifyOrders",
    ]
    assert provider._per_job_doc_units[provider._required_docs[0]] == "LoadOrders"
    assert provider._plugin_manifest.quality_gates.required_sections == {
        doc: ["Integration Name"] for doc in provider._required_docs
    }
    assert provider._plugin_manifest.quality_gates.min_doc_bytes == {
        doc: 6000 for doc in provider._required_docs
    }


def test_pack_quality_required_sections_terms_and_bytes_pass(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text(
        "# Webhook Smoke Test\n\n"
        "## Repository Snapshot\n"
        "README was scanned from source files for webhook validation.\n\n"
        "## Smoke Test Result\n"
        "PASS: webhook smoke test completed with source evidence.\n",
        encoding="utf-8",
    )

    report = validate_pack_quality(staging, _pack_manifest())

    assert report["ok"]
    assert report["effective_ok"]


def test_pack_quality_required_sections_fail(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text(
        "# Webhook Smoke Test\n\n"
        "## Repository Snapshot\n"
        "README was scanned from source files. PASS webhook.\n",
        encoding="utf-8",
    )

    report = validate_pack_quality(staging, _pack_manifest())

    assert not report["ok"]
    assert any("missing required sections" in error for error in report["errors"])


def test_pack_required_sections_are_checked_before_doc_acceptance(tmp_path):
    pack_dir = tmp_path / "pack"
    _write_pack_dir(pack_dir, mode="advisory")
    provider = CustomHarnessProvider(MagicMock(), plugin_pack_dir=pack_dir)

    report = provider._validate_phase3_doc(
        "# Webhook Smoke Test\n\n## Repository Snapshot\nEvidence.\n",
        "WEBHOOK_SMOKE_TEST",
    )

    assert not report["ok"]
    assert any("Missing required Pack sections" in error for error in report["errors"])


def test_pack_quality_required_terms_fail(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text(
        "# Webhook Smoke Test\n\n"
        "## Repository Snapshot\n"
        "README source files were inspected.\n\n"
        "## Smoke Test Result\n"
        "No final status was emitted.\n",
        encoding="utf-8",
    )

    report = validate_pack_quality(staging, _pack_manifest())

    assert not report["ok"]
    assert "Missing required pack term: scanned" in report["errors"]
    assert "Missing required pack term: PASS" in report["errors"]


def test_pack_quality_min_doc_bytes_fail(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text(
        "# Webhook Smoke Test\n\n"
        "## Repository Snapshot\nscanned source webhook PASS.\n\n"
        "## Smoke Test Result\nPASS.\n",
        encoding="utf-8",
    )

    report = validate_pack_quality(staging, _pack_manifest())

    assert not report["ok"]
    assert any("too small" in error for error in report["errors"])


def test_pack_quality_advisory_does_not_relax_required_sections(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text("# Webhook Smoke Test\n", encoding="utf-8")

    report = validate_pack_quality(staging, _pack_manifest(mode="advisory"))

    assert not report["ok"]
    assert not report["effective_ok"]
    assert any("missing required sections" in error for error in report["errors"])


def test_pack_quality_advisory_relaxes_quality_preferences_only(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text(
        "# Webhook Smoke Test\n\n"
        "## Repository Snapshot\nsource evidence\n\n"
        "## Smoke Test Result\nfinished\n",
        encoding="utf-8",
    )

    report = validate_pack_quality(staging, _pack_manifest(mode="advisory"))

    assert not report["ok"]  # preferred terms/minimum size are absent
    assert report["effective_ok"]
    assert report["warnings"] == ["Pack quality gates failed in advisory mode"]


def test_pack_contract_requires_explicit_unknown_for_missing_evidence(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_evidence").mkdir()
    (staging / "_evidence" / "table_inventory.json").write_text(
        json.dumps({
            "profile": "table_inventory",
            "categories": {"sql_queries": []},
            "missing_categories": ["sql_queries"],
        }),
        encoding="utf-8",
    )
    (staging / "TABLE_INVENTORY.md").write_text(
        "# Table Inventory\n\n## Table Inventory\n\nNo SQL query evidence was supplied.\n",
        encoding="utf-8",
    )
    manifest = PluginPackManifest.model_validate({
        "name": "table-inventory",
        "version": "1.0.0",
        "docs": [{
            "name": "TABLE_INVENTORY",
            "evidence_profile": "table_inventory",
            "required_sections": ["Table Inventory"],
            "required_evidence": ["sql_queries"],
        }],
        "evidence_profiles": {"table_inventory": {"include": ["sql_queries"]}},
        "validation": {"unknown_policy": "explicit", "mode": "blocking"},
        "steps": [{"id": "full_pipeline", "type": "harness"}],
    })

    report = validate_pack_quality(staging, manifest)

    assert not report["ok"]
    assert any("UNKNOWN/not evidenced" in error for error in report["errors"])


def test_pack_contract_advisory_does_not_relax_missing_evidence_contract(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "_evidence").mkdir()
    (staging / "_evidence" / "webhook.json").write_text(
        json.dumps({"profile": "webhook", "categories": {"routes": []}}),
        encoding="utf-8",
    )
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text("# Webhook Smoke Test\n", encoding="utf-8")
    manifest = PluginPackManifest.model_validate({
        "name": "webhook-smoke-test",
        "version": "1.0.0",
        "docs": [{
            "name": "WEBHOOK_SMOKE_TEST",
            "evidence_profile": "webhook",
            "required_sections": ["Smoke Test Result"],
            "required_evidence": ["routes"],
        }],
        "evidence_profiles": {"webhook": {"include": ["routes"]}},
        "validation": {"unknown_policy": "explicit", "mode": "advisory"},
        "steps": [{"id": "full_pipeline", "type": "harness"}],
    })

    report = validate_pack_quality(staging, manifest)

    assert not report["ok"]
    assert not report["effective_ok"]
    assert any("missing required sections" in error for error in report["errors"])


def test_pack_quality_required_outputs_must_be_structured(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()

    def _make_manifest(required_outputs: list[str]) -> PluginPackManifest:
        return PluginPackManifest.model_validate({
            "name": "test-pack",
            "version": "1.0.0",
            "docs": [{"name": "MY_DOC", "required_outputs": required_outputs}],
            "steps": [{"id": "run", "type": "harness"}],
        })

    # Text-only presence: word appears in body but no heading/table — emits warning, does not block.
    (staging / "MY_DOC.md").write_text(
        "This doc discusses table_inventory in passing but has no heading for it.\n",
        encoding="utf-8",
    )
    report = validate_pack_quality(staging, _make_manifest(["table_inventory"]))
    assert report["ok"], report["errors"]
    assert any("expected output" in w and "table_inventory" in w for w in report["warnings"])

    # Heading with content satisfies.
    (staging / "MY_DOC.md").write_text(
        "## Table Inventory\n\n| name | rows |\n|------|------|\n| orders | 120 |\n",
        encoding="utf-8",
    )
    report = validate_pack_quality(staging, _make_manifest(["table_inventory"]))
    assert report["ok"], report["errors"]

    # Table column header satisfies.
    (staging / "MY_DOC.md").write_text(
        "| Table Inventory | Count |\n|-----------------|-------|\n| orders | 1 |\n",
        encoding="utf-8",
    )
    report = validate_pack_quality(staging, _make_manifest(["table_inventory"]))
    assert report["ok"], report["errors"]

    # Empty heading (no following content): emits warning, does not block.
    (staging / "MY_DOC.md").write_text(
        "## Table Inventory\n",
        encoding="utf-8",
    )
    report = validate_pack_quality(staging, _make_manifest(["table_inventory"]))
    assert report["ok"], report["errors"]
    assert any("expected output" in w and "table_inventory" in w for w in report["warnings"])


def test_webhook_pack_writes_bounded_evidence_bundle(tmp_path):
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (staging / "facts_manifest.json").write_text("{}", encoding="utf-8")
    (preprocess / "WebhookController.md").write_text(
        "POST /webhook/orders routes to handleWebhook callback.\n",
        encoding="utf-8",
    )
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "plugin.yaml").write_text(
        json.dumps({
            "name": "webhook-smoke-test",
            "version": "1.0.0",
            "docs": [{"name": "WEBHOOK_SMOKE_TEST", "evidence_profile": "webhook"}],
            "evidence_profiles": {"webhook": {"include": ["source_summary", "routes", "handlers"]}},
            "steps": [{"id": "full_pipeline", "type": "harness"}],
        }),
        encoding="utf-8",
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    provider = CustomHarnessProvider(MagicMock(), plugin_pack_dir=pack_dir)
    provider._prepare_pack_artifacts(ctx)
    context_files = provider._phase3_context_files(ctx, "WEBHOOK_SMOKE_TEST")

    assert staging / "_evidence" / "webhook.md" in context_files
    assert staging / "_plans" / "WEBHOOK_SMOKE_TEST.json" in context_files
    assert (staging / "_evidence" / "webhook.json").exists()
    assert (staging / "_evidence" / "webhook.md").stat().st_size < 20_000


def test_evidence_bundle_extracts_code_blocks_for_sql_category(tmp_path):
    from mcp_server.providers.custom.evidence_bundles import EvidenceBundleBuilder
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "queries.md").write_text(
        "## SQL Queries\n\n"
        "Some preamble.\n\n"
        "```sql\nSELECT id, name FROM orders WHERE status = 'open';\n```\n\n"
        "Another paragraph.\n\n"
        "```sql\nINSERT INTO audit (event) VALUES ('created');\n```\n",
        encoding="utf-8",
    )
    manifest = PluginPackManifest.model_validate({
        "name": "test-pack",
        "version": "1.0.0",
        "docs": [{"name": "MY_DOC", "evidence_profile": "p1"}],
        "evidence_profiles": {"p1": {"include": ["sql_queries"]}},
        "steps": [{"id": "run", "type": "harness"}],
    })
    builder = EvidenceBundleBuilder(staging, manifest)
    result = builder.build("p1", manifest.evidence_profiles["p1"])

    json_path = staging / result["json"]
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    sql_entries = payload["categories"]["sql_queries"]
    # Complete code blocks should be extracted, not 5-line windows.
    assert sql_entries, "sql_queries evidence should not be empty"
    assert any("SELECT" in entry["snippet"] for entry in sql_entries)
    assert any("INSERT" in entry["snippet"] for entry in sql_entries)
    # Each snippet should contain the fence markers (full block extraction).
    assert all("```" in entry["snippet"] for entry in sql_entries)


def test_evidence_bundle_extracts_json_structured_entries(tmp_path):
    from mcp_server.providers.custom.evidence_bundles import EvidenceBundleBuilder
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (staging / "engineering_facts.json").write_text(
        json.dumps({
            "db_connections": [
                {"name": "MainDb", "host": "db.internal", "database": "appdb"},
                {"name": "Cache", "server": "redis.internal"},
            ]
        }),
        encoding="utf-8",
    )
    manifest = PluginPackManifest.model_validate({
        "name": "test-pack",
        "version": "1.0.0",
        "docs": [{"name": "MY_DOC", "evidence_profile": "p1"}],
        "evidence_profiles": {"p1": {"include": ["db_connections"]}},
        "steps": [{"id": "run", "type": "harness"}],
    })
    builder = EvidenceBundleBuilder(staging, manifest)
    result = builder.build("p1", manifest.evidence_profiles["p1"])

    payload = json.loads((staging / result["json"]).read_text(encoding="utf-8"))
    entries = payload["categories"]["db_connections"]
    assert entries, "db_connections evidence should not be empty"
    combined = " ".join(e["snippet"] for e in entries)
    assert "MainDb" in combined or "appdb" in combined


@pytest.mark.asyncio
async def test_pack_core_generation_does_not_call_complete_with_tools(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    context_file = staging / "facts_manifest.json"
    context_file.write_text("{}", encoding="utf-8")
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "plugin.yaml").write_text(
        json.dumps({
            "name": "webhook-smoke-test",
            "version": "1.0.0",
            "docs": [{"name": "WEBHOOK_SMOKE_TEST"}],
            "evidence_profiles": {"webhook": {"include": ["source_summary"]}},
            "steps": [{"id": "full_pipeline", "type": "harness"}],
        }),
        encoding="utf-8",
    )
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _ToolCapableProvider:
        model_name = "fake/tool-model"
        provider_name = "openai"
        supports_tools = True

        def __init__(self):
            self.context_names = []

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            self.context_names = [p.name for p in context_files or []]
            return ModelResult(success=True, text="pack markdown", model_name=self.model_name)

        async def complete_with_tools(self, *args, **kwargs):
            raise AssertionError("pack core generation must not use complete_with_tools")

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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    model = _ToolCapableProvider()
    result = await CustomHarnessProvider(model, plugin_pack_dir=pack_dir)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=model,
        phase="phase3",
        target="WEBHOOK_SMOKE_TEST",
        prompt="Write doc",
        context_files=[context_file],
        opts=ModelOptions(),
        log_cb=log_cb,
    )

    assert result.success
    assert model.context_names == ["facts_manifest.json"]


def _write_enterprise_gate_inputs(staging: Path, facts) -> None:
    (staging / "_analysis").mkdir(parents=True, exist_ok=True)
    (staging / "_assessment").mkdir(parents=True, exist_ok=True)
    ok_report = CoverageReport(
        ok=True,
        covered_units=sorted(facts.unit_names()),
        coverage_count=facts.unit_count,
        expected_count=facts.unit_count,
    )
    for path in [
        staging / "_analysis" / "_coverage_report.json",
        staging / "_analysis" / "_depth_report.json",
        staging / "_assessment" / "doc_validation_report.json",
        staging / "_assessment" / "doc_depth_report.json",
    ]:
        path.write_text(ok_report.model_dump_json(indent=2), encoding="utf-8")
    (staging / "_assessment" / "scorecard.md").write_text(_scorecard_text(), encoding="utf-8")


def _write_pack_dir(pack_dir: Path, *, mode: str) -> None:
    pack_dir.mkdir()
    (pack_dir / "plugin.yaml").write_text(
        json.dumps(_pack_manifest(mode=mode).model_dump(mode="json")),
        encoding="utf-8",
    )


def test_enterprise_gate_blocks_failed_blocking_pack_quality(tmp_path, monkeypatch):
    from mcp_server.config import config

    project_id = "proj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="generic", jobs=[_job("README", "root")])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    facts = build_facts_manifest(project_id, scan_dir)
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_enterprise_gate_inputs(staging, facts)
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text("# Webhook Smoke Test\n", encoding="utf-8")
    pack_dir = tmp_path / "pack"
    _write_pack_dir(pack_dir, mode="blocking")
    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["root"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
        enterprise_mode=True,
    )

    gate_report = CustomHarnessProvider(MagicMock(), plugin_pack_dir=pack_dir)._enterprise_gate_report(
        ctx,
        HarnessRunState(project_id=project_id, facts_manifest=facts),
        provenance=CoverageReport(ok=True, coverage_count=1, expected_count=1),
    )

    assert not gate_report["gates"]["pack_quality"]["ok"]
    assert (staging / "_assessment" / "pack_quality_report.json").exists()


def test_enterprise_gate_blocks_structurally_incomplete_advisory_pack(tmp_path, monkeypatch):
    from mcp_server.config import config

    project_id = "proj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="generic", jobs=[_job("README", "root")])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    facts = build_facts_manifest(project_id, scan_dir)
    staging = tmp_path / "staging"
    staging.mkdir()
    _write_enterprise_gate_inputs(staging, facts)
    (staging / "WEBHOOK_SMOKE_TEST.md").write_text("# Webhook Smoke Test\n", encoding="utf-8")
    pack_dir = tmp_path / "pack"
    _write_pack_dir(pack_dir, mode="advisory")
    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["root"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
        enterprise_mode=True,
    )

    gate_report = CustomHarnessProvider(MagicMock(), plugin_pack_dir=pack_dir)._enterprise_gate_report(
        ctx,
        HarnessRunState(project_id=project_id, facts_manifest=facts),
        provenance=CoverageReport(ok=True, coverage_count=1, expected_count=1),
    )

    assert not gate_report["gates"]["pack_quality"]["ok"]
    assert any(
        "missing required sections" in error
        for error in gate_report["gates"]["pack_quality"]["errors"]
    )


def test_pack_contract_can_construct_skipped_provenance_report(tmp_path):
    report = CoverageReport(
        ok=True,
        coverage_count=1,
        expected_count=1,
        warnings=["Default source provenance gate skipped for plugin-pack contract run."],
    )

    assert report.ok
    assert report.coverage_count == 1


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


def test_phase3_doc_prompt_requires_evidence_register(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    prompt = doc_prompt(facts, "03_DATA_FLOWS_AND_LINEAGE")

    assert "## Evidence Register" in prompt
    assert "| Topic | Units / Scope | Evidence Source | Source Artifact / Path | Confidence |" in prompt
    assert "confidence cell that starts with `HIGH`, `MEDIUM`" in prompt
    assert "plain unformatted text" in prompt
    assert "not `**LOW**`, `_LOW_`, or `` `LOW` ``" in prompt
    assert "scanner-limited, inferred, or redaction notes may follow" in prompt


def test_source_provenance_requires_evidence_register(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts)
    (staging / "03_DATA_FLOWS_AND_LINEAGE.md").write_text(
        "# Data Flow\n\n## Data Flow\nUnit A evidence from source artifact Unit A.item.\n",
        encoding="utf-8",
    )

    report = validate_source_provenance(staging, facts)

    assert not report.ok
    assert any("03_DATA_FLOWS_AND_LINEAGE.md missing required Evidence Register" in e for e in report.errors)


def test_source_provenance_rejects_malformed_evidence_register(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts)
    (staging / "05_RUNTIME_CONFIG_AND_EXTERNALS.md").write_text(
        "# Runtime\n\n## Evidence Register\n\n"
        "| Subject | Evidence | Confidence |\n"
        "|---------|----------|------------|\n"
        "| Config | facts_manifest.json | CERTAIN |\n",
        encoding="utf-8",
    )

    report = validate_source_provenance(staging, facts)

    assert not report.ok
    assert any("05_RUNTIME_CONFIG_AND_EXTERNALS.md Evidence Register has invalid table headers" in e for e in report.errors)


@pytest.mark.parametrize(
    ("confidence", "ok"),
    [
        ("HIGH", True),
        ("MEDIUM", True),
        ("LOW", True),
        ("LOW (scanner-limited)", True),
        ("MEDIUM -- inferred", True),
        ("HIGH -- intentionally redacted", True),
        ("**LOW**", True),
        ("__LOW__", True),
        ("*MEDIUM*", True),
        ("_MEDIUM_", True),
        ("`HIGH`", True),
        ("**LOW** (scanner-limited)", True),
        ("`MEDIUM` -- inferred", True),
        ("UNKNOWN", False),
        ("CERTAIN", False),
        ("", False),
        ("scanner-limited LOW", False),
        ("**UNKNOWN**", False),
        ("**scanner-limited LOW**", False),
    ],
)
def test_source_provenance_validates_confidence_semantics(tmp_path, confidence, ok):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts)
    runtime_doc = staging / "05_RUNTIME_CONFIG_AND_EXTERNALS.md"
    runtime_doc.write_text(
        runtime_doc.read_text(encoding="utf-8").replace("| MEDIUM |", f"| {confidence} |", 1),
        encoding="utf-8",
    )

    report = validate_source_provenance(staging, facts)

    assert report.ok is ok
    assert any("invalid confidence" in e for e in report.errors) is not ok


def test_source_provenance_rejects_rows_without_source_provenance(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts)
    runtime_doc = staging / "05_RUNTIME_CONFIG_AND_EXTERNALS.md"
    runtime_doc.write_text(
        runtime_doc.read_text(encoding="utf-8").replace(
            "| Runtime risk | Unit A | known_risks.md scorecard evidence | Unit A.source | LOW |",
            "| Runtime risk | Unit A | reviewed notes | runtime claim | LOW |",
            1,
        ),
        encoding="utf-8",
    )

    report = validate_source_provenance(staging, facts)

    assert report.ok
    assert any("without source provenance" in w for w in report.warnings)


def test_source_provenance_ignores_support_markdown(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts)
    (staging / "engineering_facts.md").write_text("support facts without an evidence table\n", encoding="utf-8")

    report = validate_source_provenance(staging, facts)

    assert report.ok


def test_source_provenance_requires_inventory_unit_paths(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
    facts.units[0].path = "src/unit_a.py"
    facts.units[1].path = "src/unit_b.py"
    staging = tmp_path / "staging"
    _write_assistant_docs(staging, facts)
    inventory = staging / "02_COMPONENT_INVENTORY.md"
    inventory.write_text(inventory.read_text(encoding="utf-8").replace("src/unit_b.py", "missing_path"), encoding="utf-8")

    report = validate_source_provenance(staging, facts)

    assert not report.ok
    assert any("canonical unit artifact paths" in e and "Unit B" in e for e in report.errors)


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
    assert "Nexus Platform Analysis Policy (Immutable)" in content
    assert "cannot replace" in content
    assert "weaken, or contradict" in content
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
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n### COMPONENTS\ncontent\n### GROUP_SUMMARY\nsummary\n",
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert not report.ok
    assert any("EXECUTION_FLOW" in error for error in report.errors)


def test_analysis_guide_section_gate_requires_group_summary_for_generic(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n### COMPONENTS\ncontent\n",
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert not report.ok
    assert any("GROUP_SUMMARY" in error for error in report.errors)


def test_plugin_analysis_guide_section_gate_can_skip_group_summary(tmp_path):
    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "A.md").write_text(
        "## Repository Snapshot\n\nThe group was analyzed without canonical harness headings.\n",
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts, require_group_summary=False)

    assert report.ok


def test_analysis_guide_section_gate_passes_with_all_talend_sections(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    body = "\n".join(f"### {name}\ncontent for {name} and a.\n" for name in (*ANALYSIS_SECTION_NAMES, "GROUP_SUMMARY"))
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n" + body,
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert report.ok


def _write_esb_facts(preprocess_dir: Path, *, detected: bool) -> None:
    preprocess_dir.mkdir(parents=True, exist_ok=True)
    (preprocess_dir / "talend_esb_facts.json").write_text(
        json.dumps({"esb_detected": detected, "esb_component_types": ["cRoute"] if detected else []}),
        encoding="utf-8",
    )


def test_analysis_guide_section_gate_requires_esb_routes_when_detected(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    _write_esb_facts(tmp_path / "_preprocess", detected=True)
    # All required Talend sections present EXCEPT ESB_ROUTES.
    names = [n for n in (*ANALYSIS_SECTION_NAMES, "GROUP_SUMMARY") if n != "ESB_ROUTES"]
    body = "\n".join(f"### {name}\ncontent for {name} and a.\n" for name in names)
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n" + body,
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert not report.ok
    assert any("ESB_ROUTES" in error for error in report.errors)


def test_analysis_guide_section_gate_passes_with_esb_routes_when_detected(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    _write_esb_facts(tmp_path / "_preprocess", detected=True)
    # ANALYSIS_SECTION_NAMES now includes ESB_ROUTES.
    body = "\n".join(f"### {name}\ncontent for {name} and a.\n" for name in (*ANALYSIS_SECTION_NAMES, "GROUP_SUMMARY"))
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n" + body,
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert report.ok


def test_analysis_guide_section_gate_skips_esb_routes_when_not_detected(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    # No talend_esb_facts.json -> ESB_ROUTES not required even though it is absent.
    names = [n for n in (*ANALYSIS_SECTION_NAMES, "GROUP_SUMMARY") if n != "ESB_ROUTES"]
    body = "\n".join(f"### {name}\ncontent for {name} and a.\n" for name in names)
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n" + body,
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert report.ok
    assert not any("ESB_ROUTES" in error for error in report.errors)


def test_normalize_analysis_guide_headings_accepts_model_heading_level(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    body = "\n".join(f"## {name}\ncontent for {name} and a.\n" for name in (*ANALYSIS_SECTION_NAMES, "GROUP_SUMMARY"))
    normalized = normalize_analysis_guide_headings(body, "talend")
    (analysis_dir / "A.md").write_text(
        "## Coverage\n\n- COVERED: a\n\n## Model Analysis\n\n" + normalized,
        encoding="utf-8",
    )

    report = validate_analysis_guide_sections(analysis_dir, facts)

    assert report.ok
    assert missing_required_analysis_sections(normalized, "talend") == []


def test_normalize_analysis_guide_headings_accepts_non_talend_group_summary():
    body = "## Model Analysis\n\n## ### GROUP_SUMMARY\ncontent for group summary.\n"

    normalized = normalize_analysis_guide_headings(body, "lobster")

    assert "## ### GROUP_SUMMARY" not in normalized
    assert "### GROUP_SUMMARY" in normalized
    assert missing_required_analysis_sections(normalized, "lobster") == []


def test_normalize_analysis_guide_headings_strips_suffix_but_preserves_content():
    """Suffix variants like '### GROUP_SUMMARY: demo-data' must be normalised to
    '### GROUP_SUMMARY' without consuming the content line that follows."""
    body = (
        "## Model Analysis\n\n"
        "### GROUP_SUMMARY: demo-data\n"
        "content line that must survive normalisation\n"
    )

    normalised = normalize_analysis_guide_headings(body, "generic")

    assert "### GROUP_SUMMARY: demo-data" not in normalised
    assert "### GROUP_SUMMARY\n" in normalised
    assert "content line that must survive normalisation" in normalised
    assert missing_required_analysis_sections(normalised, "generic") == []


def test_normalize_analysis_guide_headings_accepts_lowercase_variant():
    body = "## Model Analysis\n\n#### group_summary\nsome content\n"

    normalised = normalize_analysis_guide_headings(body, "generic")

    assert "### GROUP_SUMMARY" in normalised
    assert "some content" in normalised
    assert missing_required_analysis_sections(normalised, "generic") == []


def test_has_section_matches_suffix_variant():
    from mcp_server.providers.custom.validators import _has_section
    text = "## Model Analysis\n\n### GROUP_SUMMARY: demo-data\ncontent\n"
    assert _has_section(text, "GROUP_SUMMARY")


def test_has_section_does_not_false_positive_on_extended_name():
    from mcp_server.providers.custom.validators import _has_section
    text = "## Model Analysis\n\n### GROUP_SUMMARY_EXTENDED\ncontent\n"
    assert not _has_section(text, "GROUP_SUMMARY")


def test_has_section_case_insensitive():
    from mcp_server.providers.custom.validators import _has_section
    text = "## Model Analysis\n\n#### group_summary\ncontent\n"
    assert _has_section(text, "GROUP_SUMMARY")


@pytest.mark.asyncio
async def test_single_shot_investigator_skips_empty_content(tmp_path):
    """Pre-flight must return all units as skipped when content is below threshold."""
    import json as _json
    from mcp_server.providers.custom.investigator import SingleShotInvestigator
    from mcp_server.providers.custom.validators import build_facts_manifest

    _write_scan(tmp_path / "scan", platform="generic", jobs=[_job("ticket", "demo-data")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    # 18 bytes — well below the 200-byte threshold
    (staging / "_preprocess" / "demo-data.md").write_text("{}", encoding="utf-8")

    call_count = 0

    class _Provider:
        async def complete(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ModelResult(success=True, text="{}", model_name="fake/model")

    investigator = SingleShotInvestigator(_Provider(), staging, facts)
    report, calls = await investigator.run(
        "demo-data",
        group_file=staging / "_preprocess" / "demo-data.md",
        context_files=[],
        allowed_units=["ticket"],
    )

    assert call_count == 0, "pre-flight should not call the LLM"
    assert "ticket" in report.skipped_units
    assert report.evidence == []
    assert report.tool_rounds == 0


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
    assert any("is a stub" in e for e in report.errors)


def test_doc_depth_gate_is_not_satisfied_by_padding(tmp_path):
    """A large document whose required sections are empty is still not deep.

    The old gate required `preprocess_bytes * 0.04` per document, so repeating
    the same structure was the cheapest way to pass. Depth is now judged per
    required section, so volume buys nothing.
    """
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a"), _job("b")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    staging.mkdir(parents=True)
    padding = "This document restates the same orientation paragraph. " * 900
    for doc in REQUIRED_DOCS:
        headings = "\n".join(
            f"## {section}\nSee the evidence of record.\n"
            for section in DOC_SECTION_REQUIREMENTS.get(doc, [])
        )
        (staging / f"{doc}.md").write_text(
            f"# {doc}\n\n{padding}\n\n{headings}\n", encoding="utf-8"
        )

    report = validate_doc_depth(staging, facts)

    assert not report.ok
    assert any("no substantive content" in e for e in report.errors)
    # Size alone must never be the complaint any more.
    assert not any("is a stub" in e for e in report.errors)


def test_doc_depth_gate_accepts_compact_docs_that_answer_every_section(tmp_path):
    """A concise document that answers each section passes, whatever its size."""
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a"), _job("b")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    # A large evidence corpus: under the old rule this forced 45 KB per doc.
    (staging / "_preprocess" / "A.md").write_text("source scan facts " * 80_000, encoding="utf-8")
    unit_names = ", ".join(u.name for u in facts.units)
    for doc in REQUIRED_DOCS:
        headings = "\n".join(
            f"## {section}\n"
            f"- {section}: job a reads GroupA input and writes the staging table; "
            "evidence facts_manifest.json, HIGH confidence.\n"
            f"- {section}: job b depends on a via tRunJob and inherits its context "
            "group; evidence dependency_map.md, MEDIUM confidence.\n"
            f"- {section}: no runtime observation is available for GroupA, so "
            "schedules and volumes remain unproven; LOW confidence.\n"
            f"- {section}: the shared context group supplies the connection used "
            "by both jobs; evidence config_runtime_map.md, HIGH confidence.\n"
            f"- {section}: no orphaned or inactive artifact was observed in the "
            "frozen scan for this group; evidence facts_manifest.json, HIGH.\n"
            for section in DOC_SECTION_REQUIREMENTS.get(doc, [])
        )
        (staging / f"{doc}.md").write_text(
            f"# {doc}\n\nCanonical inventory: {facts.unit_count} jobs. "
            f"Units covered: {unit_names}. Group GroupA.\n\n{headings}\n",
            encoding="utf-8",
        )

    report = validate_doc_depth(staging, facts)

    assert report.ok, report.errors
    # The point of the fixture: every doc is far below the 45 KB the old
    # volume-scaled rule would have demanded for this evidence corpus.
    assert all(
        (staging / f"{doc}.md").stat().st_size < 10_000 for doc in REQUIRED_DOCS
    )


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
    with pytest.raises(InvestigationError, match="unsupported action"):
        parse_json_action('{"action":"write_file","path":"x"}')


def test_staging_toolbox_blocks_outside_access(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    toolbox = StagingToolbox(staging)

    with pytest.raises(InvestigationError, match="path outside staging"):
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


def test_investigation_accepts_chunk_ref_as_generated_provenance(tmp_path):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    chunk = staging / "_investigation" / "_chunks" / "A_chunk_1.md"
    chunk.parent.mkdir(parents=True)
    chunk.write_text("a component detail from generated chunk", encoding="utf-8")
    validator = EvidenceValidator(facts, StagingToolbox(staging))

    report = validator.validate_report("A", {
        "evidence": [{
            "unit_name": "a",
            "category": "inventory",
            "line_range": "1-1",
            "snippet": "component detail",
            "observation": "The generated chunk contains concrete component detail for this unit.",
        }],
        "skipped_units": {},
    }, {"a"}, default_chunk_ref="_investigation/_chunks/A_chunk_1.md")

    assert report.evidence[0].chunk_ref == "_investigation/_chunks/A_chunk_1.md"
    assert report.evidence[0].source_file == "_investigation/_chunks/A_chunk_1.md"


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
async def test_single_shot_investigator_rejects_array_payload_without_attribute_error(
    monkeypatch,
    tmp_path,
):
    _write_scan(tmp_path / "scan", platform="talend", jobs=[_job("a", "A")])
    facts = build_facts_manifest("proj", tmp_path / "scan")
    staging = tmp_path / "staging"
    (staging / "_preprocess").mkdir(parents=True)
    # Content must exceed min_investigation_content_bytes (200) so the pre-flight
    # threshold does not trigger and the array-payload rejection path is exercised.
    (staging / "_preprocess" / "A.md").write_text("a component detail " * 12, encoding="utf-8")

    class _Provider:
        async def complete(self, *args, **kwargs):
            return ModelResult(
                success=True,
                text='{"evidence":[]}',
                model_name="fake/model",
            )

    monkeypatch.setattr(
        "mcp_server.providers.custom.investigator._extract_json_object",
        lambda text: '[{"unit_name":"a"}]',
    )
    investigator = SingleShotInvestigator(_Provider(), staging, facts)

    with pytest.raises(InvestigationError, match="list instead of an object"):
        await investigator.run(
            "A",
            group_file=staging / "_preprocess" / "A.md",
            context_files=[],
            allowed_units=["a"],
        )


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
async def test_provider_adapter_preserves_reasoning_usage_and_never_retries_length(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": "partial JSON",
                            "reasoning_content": "internal reasoning",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 32_000,
                    "completion_tokens_details": {"reasoning_tokens": 27_500},
                },
            }

    class _Client:
        calls = 0
        last_payload = None

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            _Client.calls += 1
            _Client.last_payload = kwargs.get("json")
            return _Response()

    monkeypatch.setattr("mcp_server.providers.custom.providers.httpx.AsyncClient", _Client)
    provider = OpenAICompatibleProvider(
        "deepseek-v4-flash",
        api_key="key",
        base_url="https://api.deepseek.com/v1",
        provider_name="deepseek",
    )

    result = await provider.complete(
        "extract claims",
        options=ModelOptions(
            retries=3,
            provider_options={"thinking": {"type": "disabled"}},
        ),
    )

    assert not result.success
    assert _Client.calls == 1
    assert result.output_tokens == 32_000
    assert result.metadata["reasoning_tokens"] == 27_500
    assert result.metadata["reasoning_content_bytes"] > 0
    assert result.metadata["incomplete_reason"] == "length"
    assert _Client.last_payload["thinking"] == {"type": "disabled"}


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
                "system_prompt": options.system_prompt if options else None,
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

        if '"schema_version": "phase2-claims-v1"' in prompt:
            def _json_contract_value(name: str) -> str:
                match = re.search(rf'"{name}":\s*("(?:[^"\\]|\\.)*")', prompt)
                return json.loads(match.group(1)) if match else "unknown"

            units_block = prompt.split("ASSIGNED CANONICAL UNITS", 1)[1].split(
                "ALLOWED SECTIONS", 1
            )[0]
            units = [line[2:].strip() for line in units_block.splitlines() if line.startswith("- ")]
            sections_block = prompt.split("ALLOWED SECTIONS", 1)[1].split("\nRULES\n", 1)[0]
            sections = [
                line[2:].strip() for line in sections_block.splitlines() if line.startswith("- ")
            ]
            return ModelResult(
                success=True,
                text=json.dumps(
                    {
                        "schema_version": "phase2-claims-v1",
                        "group": _json_contract_value("group"),
                        "task_id": _json_contract_value("task_id"),
                        "concern": _json_contract_value("concern"),
                        "claims": [
                            {
                                "section": section,
                                "kind": "unknown",
                                "unit_names": units,
                                "related_units": [],
                                "statement": f"No fixture evidence for {section}.",
                                "evidence_refs": [],
                                "confidence": "unknown",
                                "claim_id": "",
                            }
                            for section in sections
                        ],
                    }
                ),
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=100,
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
            "## Project Orientation\n"
            "Project Orientation for developers includes source-backed onboarding and navigation.\n"
            "Canonical inventory: 2 recipes/functions. Source artifacts: Recipe One.item, Function One.item.\n"
            "Platform evidence includes recipe, callable function, folder, export artifact, trigger, step, action, block, "
            "datapill, formula, lookup table, property, connection, monitor, job history.\n"
            "Score: 3/5. Confidence: HIGH. Evidence: facts_manifest.json. Red Flags: HIGH risk. Quick Wins: add tests. Unknowns: runtime SLA.\n"
            "Change Impact: inspect source artifacts, Test changed flows, Verify outputs, Rollback if validation fails.\n"
            "Documentation Map, Onboarding, Common Tasks, Known Traps, Glossary. Debug and Recovery guidance with Testing evidence.\n"
            "Topology, Boundaries, Dependencies, Entry Points, External Systems. Source, Target, Transformation, Schema, Validation, Scanner-Limited.\n"
            "Expression, Unit, Domain, Impact. Config, Redacted, Scanner-Limited.\n"
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
            "Business rule evidence, transformation logic, conflicts, edge cases, Hardcoded Values, and implementation rules are documented.\n"
            "## External systems and context/configuration\n"
            "External systems, context values, configuration, Credentials, and properties are documented.\n"
            "## Operations, monitoring, and error handling\n"
            "Operation, execution, monitoring, retry, and error handling details are documented.\n"
            "## Security risks\n"
            "Security, credential, access, risk, severity, and mitigation details are documented.\n"
            "## Migration risks and recommendations\n"
            "Migration readiness, impact, testing, recovery, runbook, troubleshooting, FAQ, glossary, "
            "pattern, standard, anti-pattern, architecture, connectivity, component, environment, "
            "key findings, recommendations, scope, groups, lineage, inventory, scanner limitations, "
            "executive overview, access, credential, severity, mitigation, onboarding, plan.\n"
            "## Scanner-Limited Evidence\n"
            "Scanner-Limited limitations are called out where source detail is incomplete.\n"
        )
        for section in ANALYSIS_SECTION_NAMES:
            deep_text += f"\n### {section}\n"
            deep_text += (
                f"{section} evidence for Recipe One and Function One includes component, data flow, "
                "business, external, operation, security, migration, and scanner detail.\n"
            )
        deep_text += "\n### GROUP_SUMMARY\nUnits covered: Recipe One, Function One. Top risks and flows summarized.\n"
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

    assert _provider_supports_tools(provider) is True


def test_assemble_phase2_task_sections_preserves_canonical_sections():
    from mcp_server.providers.custom.evidence_tasks import EvidenceTask

    tasks = [
        EvidenceTask(index=1, task_id="Orders__task_1", path=Path("t1.md"), units=["JobA"]),
        EvidenceTask(index=2, task_id="Orders__task_2", path=Path("t2.md"), units=["JobB"]),
    ]
    assembled, summary_fallback = CustomHarnessProvider._assemble_phase2_task_sections(
        "Orders",
        tasks,
        [
            "Intro evidence\n\n### COMPONENTS\nComponent A\n\n### DATA_FLOWS\nFlow A\n\n### GROUP_SUMMARY\nSummary A",
            "### COMPONENTS\nComponent B\n\nLoose finding\n\n### RISK_FLAGS\nRisk B\n\n### GROUP_SUMMARY\nSummary B",
        ],
    )

    assert "### COMPONENTS" in assembled
    assert assembled.count("### COMPONENTS") == 1  # one heading per section
    assert "#### task Orders__task_1: JobA" in assembled
    assert "#### task Orders__task_2: JobB" in assembled
    assert "Component A" in assembled
    assert "Component B" in assembled
    assert "### DATA_FLOWS" in assembled
    assert "Flow A" in assembled
    assert "### RISK_FLAGS" in assembled
    assert "Risk B" in assembled
    assert "### UNCLASSIFIED_OR_EXTRA_FINDINGS" in assembled
    assert "Intro evidence" in assembled
    # GROUP_SUMMARY is excluded from assembly (group synthesis writes it);
    # per-task summaries are returned as the fallback block.
    assert "### GROUP_SUMMARY" not in assembled
    assert summary_fallback.startswith("### GROUP_SUMMARY")
    assert "Summary A" in summary_fallback
    assert "Summary B" in summary_fallback


def test_assemble_phase2_task_sections_keeps_extra_guide_sections():
    from mcp_server.providers.custom.evidence_tasks import EvidenceTask

    tasks = [EvidenceTask(index=1, task_id="G__task_1", path=Path("t1.md"), units=["JobA"])]
    assembled, _ = CustomHarnessProvider._assemble_phase2_task_sections(
        "G",
        tasks,
        ["### COMPONENTS\ncomp\n\n### DEBUG_TESTING_EVIDENCE\ndebug body"],
        frozenset({"DEBUG_TESTING_EVIDENCE"}),
    )
    assert "### DEBUG_TESTING_EVIDENCE" in assembled
    assert "debug body" in assembled


def test_parse_phase2_sections_h4_heading_does_not_break_parent_section():
    text = "### RISK_FLAGS\n\nSome risk.\n\n#### HIGH\n\nHigh risk detail.\n#### LOW\n\nLow detail."
    result = CustomHarnessProvider._parse_phase2_sections(text)
    assert "High risk detail" in result.get("RISK_FLAGS", "")
    assert "Low detail" in result.get("RISK_FLAGS", "")
    assert result.get(ADDITIONAL_FINDINGS_SECTION, "") == ""


def test_parse_phase2_sections_cross_chunk_findings_is_named_section():
    text = "### COMPONENTS\n\ncomp content\n\n### CROSS_CHUNK_FINDINGS\n\ncross content"
    result = CustomHarnessProvider._parse_phase2_sections(text)
    assert result.get("CROSS_CHUNK_FINDINGS") == "cross content"
    assert result.get(ADDITIONAL_FINDINGS_SECTION, "") == ""
    assert result.get("COMPONENTS") == "comp content"


def test_parse_phase2_sections_extra_section_names_accepted():
    text = "### COMPONENTS\n\ncomp\n\n### DEBUG_TESTING_EVIDENCE\n\ndebug content"
    result = CustomHarnessProvider._parse_phase2_sections(
        text, extra_section_names=frozenset({"DEBUG_TESTING_EVIDENCE"})
    )
    assert result.get("DEBUG_TESTING_EVIDENCE") == "debug content"
    assert result.get(ADDITIONAL_FINDINGS_SECTION, "") == ""


def test_guide_section_names_extracts_extra_headers(tmp_path):
    guide = tmp_path / "ANALYSIS_GUIDE.md"
    guide.write_text(
        "# Guide\n\n### COMPONENTS\n\nStandard section.\n\n### DEBUG_TESTING_EVIDENCE\n\nPlugin extra.\n",
        encoding="utf-8",
    )
    names = _guide_section_names(guide)
    # COMPONENTS is in PHASE2_SECTION_NAMES already — must NOT appear in extras
    assert "COMPONENTS" not in names
    assert "DEBUG_TESTING_EVIDENCE" in names


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


def test_phase28_generates_doc_specific_evidence_packs_and_manifest(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_investigation").mkdir()
    facts.write(staging / "facts_manifest.json")
    (staging / "_analysis" / "A.md").write_text(
        "# Phase 2 Analysis: A\n\n"
        "### COMPONENTS\nUnit A component evidence\n\n"
        "### GROUP_SUMMARY\nUnits covered: Unit A\n\n"
        "### SURPRISE_SECTION\nUnexpected finding\n",
        encoding="utf-8",
    )
    (staging / "_investigation" / "_summary.md").write_text("Investigation summary", encoding="utf-8")
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))._phase28_bundles(ctx, facts)

    for doc in REQUIRED_DOCS:
        assert (staging / "_analysis" / f"_doc_pack_{doc}.md").exists()
    manifest = json.loads((staging / "_analysis" / "_doc_pack_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["doc_packs"]) == set(REQUIRED_DOCS)
    entry = manifest["doc_packs"]["02_COMPONENT_INVENTORY"]
    assert entry["included"]["A.md"]
    assert "GROUP_SUMMARY" in entry["included"]["A.md"]
    assert entry["included_obligations"]
    assert "unmatched_obligations" in entry
    assert "evidence_sources" in entry
    pack = (staging / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md").read_text(encoding="utf-8")
    assert "## Documentation Obligations" in pack
    assert "shared.identity" in pack


def test_baseline_bundle_sections_is_group_summary_only():
    assert BASELINE_BUNDLE_SECTIONS == {GROUP_SUMMARY_SECTION}


def test_pack_01_and_02_have_distinct_catalog_sections():
    provider = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    sections_01 = provider._bundle_sections_for_docs(["01_ARCHITECTURE_MAP"])
    sections_02 = provider._bundle_sections_for_docs(["02_COMPONENT_INVENTORY"])
    shared = sections_01 & sections_02
    # Only GROUP_SUMMARY and EXTERNAL_SYSTEMS should be shared
    assert len(shared) <= 2
    # Each doc must have sections unique to it
    assert sections_01 - sections_02
    assert sections_02 - sections_01


def test_phase28_does_not_build_pack_09_when_not_in_required_docs(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_investigation").mkdir()
    facts.write(staging / "facts_manifest.json")
    (staging / "_analysis" / "A.md").write_text(
        "### GROUP_SUMMARY\nSummary content\n", encoding="utf-8"
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
        platform="generic",
        analysis_guide_file="",
    )
    provider = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    # Simulate non-enterprise run: 09 not in required_docs
    provider._required_docs = [d for d in provider._required_docs if d != "09_EXECUTIVE_SUMMARY"]
    provider._phase28_bundles(ctx, facts)
    assert not (staging / "_analysis" / "_doc_pack_09_EXECUTIVE_SUMMARY.md").exists()


def test_phase3_context_prefers_doc_pack_over_family_bundle(tmp_path):
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_assessment" / "prompt_packs").mkdir(parents=True)
    (staging / "_preprocess").mkdir()
    doc_pack = staging / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md"
    family_bundle = staging / "_analysis" / "_bundle_core.md"
    doc_pack.write_text("doc specific evidence", encoding="utf-8")
    family_bundle.write_text("family evidence", encoding="utf-8")
    (staging / "_analysis" / "_doc_pack_manifest.json").write_text('{"doc_packs": {}}', encoding="utf-8")
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    files = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))._phase3_context_files(
        ctx,
        "02_COMPONENT_INVENTORY",
    )

    assert doc_pack in files
    assert family_bundle not in files


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


def test_final_doc_content_adds_scanner_limited_contract_marker_once():
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    harness = CustomHarnessProvider(
        OpenAICompatibleProvider(
            "fake", api_key="test", base_url="http://example.test/v1"
        )
    )
    state = HarnessRunState(project_id="p", facts_manifest=facts)
    body = (
        "# 03 Data Flows And Lineage\n\n"
        "## Unknowns And Risk Boundaries\n\n"
        "Field-level lineage is not established by scanner evidence."
    )

    first = harness._final_doc_content(state, "03_DATA_FLOWS_AND_LINEAGE", body)
    second = harness._final_doc_content(state, "03_DATA_FLOWS_AND_LINEAGE", first)

    assert first.count("## Scanner-Limited Evidence") == 1
    assert "must not be inferred" in first
    assert second == first


def test_final_doc_content_strips_llm_preamble():
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    state = HarnessRunState(project_id="proj", facts_manifest=facts)

    body = (
        "I have read all the evidence packs. Here is the synthesized "
        "`04_BUSINESS_RULES_AND_TRANSFORMATIONS.md`:\n\n---\n\n"
        "# 04 Business Rules And Transformations\n\nReal content."
    )
    content = harness._final_doc_content(state, "04_BUSINESS_RULES_AND_TRANSFORMATIONS", body)

    assert content.startswith("# 04 Business Rules And Transformations")
    assert "evidence packs" not in content
    assert "Real content." in content


def test_final_doc_content_strips_preamble_before_nonmatching_heading():
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    state = HarnessRunState(project_id="proj", facts_manifest=facts)

    body = (
        "Now I have all the evidence. Let me compose the document.\n\n---\n\n"
        "# 01_ARCHITECTURE_MAP\n\nReal content."
    )
    content = harness._final_doc_content(state, "01_ARCHITECTURE_MAP", body)

    assert "Let me compose" not in content
    assert content.startswith("# 01 Architecture Map\n")
    assert "Real content." in content


def test_final_doc_content_keeps_heading_first_body_unchanged():
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    state = HarnessRunState(project_id="proj", facts_manifest=facts)

    body = "# 00 Agent Brief\n\nBrief body."
    assert harness._final_doc_content(state, "00_AGENT_BRIEF", body) == body + "\n"


def test_final_doc_content_keeps_long_preheading_content():
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    state = HarnessRunState(project_id="proj", facts_manifest=facts)

    long_intro = "Substantial pre-heading paragraph. " * 20
    body = f"{long_intro}\n\n# 00 Agent Brief\n\nBrief body."
    content = harness._final_doc_content(state, "00_AGENT_BRIEF", body)

    assert "Substantial pre-heading paragraph." in content


def test_final_doc_content_headingless_body_gets_title():
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    state = HarnessRunState(project_id="proj", facts_manifest=facts)

    content = harness._final_doc_content(state, "00_AGENT_BRIEF", "Body without any heading.")

    assert content.startswith("# 00 Agent Brief\n")
    assert "Body without any heading." in content


@pytest.mark.asyncio
async def test_phase4_cleanup_archives_engineering_markdown_off_top_level(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "_assessment").mkdir()
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        return None

    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    provider = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    await Phase4Cleanup(HarnessHelpersImpl(provider)).run(ctx, state, log_cb)

    assert not (staging / "engineering_facts.md").exists()
    assert not (staging / "dependency_map.md").exists()
    assert (staging / "_facts" / "engineering_facts.md").exists()
    # index.json is debug-only; check via state instead
    assert not (staging / "index.json").exists()
    assert not (staging / "_debug" / "index.json").exists()  # pipeline_debug=False by default


@pytest.mark.asyncio
async def test_phase4_cleanup_preserves_generation_warnings(tmp_path):
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        return None

    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    provider = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    await Phase4Cleanup(HarnessHelpersImpl(provider)).run(ctx, state, log_cb)

    # index.json is debug-only; warnings are preserved in state
    assert not (staging / "index.json").exists()
    assert state.warnings == ["Generated doc 06_OPERATIONS_TESTING_AND_DEBUGGING.md missing expected sections/terms: Debug"]


@pytest.mark.asyncio
async def test_phase4_cleanup_debug_flag_writes_index_and_calls(tmp_path, monkeypatch):
    from mcp_server.config import config as mcp_config
    monkeypatch.setattr(mcp_config, "pipeline_debug", True)

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "_assessment").mkdir()
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    state = HarnessRunState(
        project_id="proj",
        facts_manifest=facts,
        warnings=["Missing sections: Debug"],
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        return None

    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    provider = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))
    await Phase4Cleanup(HarnessHelpersImpl(provider)).run(ctx, state, log_cb)

    debug = staging / "_debug"
    index = json.loads((debug / "index.json").read_text(encoding="utf-8"))
    assert index["warnings_count"] == 1
    assert index["project_id"] == "proj"
    assert (debug / "provider_calls.json").exists()
    assert (debug / "_audit" / "provider_calls.json").exists()


def test_validate_docs_tolerates_inventory_artifact_and_system_row_gaps(tmp_path):
    # Contract change: per-unit source-artifact/group rows in the component
    # inventory and per-system rows in the runtime doc are no longer hard
    # validation errors — they produced form-only repair churn. Coverage is
    # satisfied when every canonical unit is mentioned in the docs.
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    facts.units[0].path = "src/unit_a.py"
    facts.units[0].key_systems = ["External API"]
    facts.key_systems = ["External API"]
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    _write_assistant_docs(staging, facts)
    (staging / "02_COMPONENT_INVENTORY.md").write_text(
        "# Component Inventory\n\n## Canonical Unit Inventory\n"
        "| Unit | Group | Source Artifact | Type |\n"
        "|------|-------|-----------------|------|\n"
        "| Unit A | MissingGroup | missing.py | code_unit |\n",
        encoding="utf-8",
    )
    (staging / "05_RUNTIME_CONFIG_AND_EXTERNALS.md").write_text(
        "# Runtime Config And Externals\n\n## External Systems\nNo concrete system row.\n"
        "## Runtime Config\nConfig includes Redacted and Scanner-Limited status.\n",
        encoding="utf-8",
    )

    report = validate_docs(staging, facts)

    assert report.ok
    assert not any("missing source artifact" in error for error in report.errors)
    assert not any("missing external system row" in error for error in report.errors)


@pytest.mark.asyncio
async def test_core_generation_splits_context_and_excludes_large_json(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 400)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 800)
    monkeypatch.setattr(config, "agent_phase2_max_slices", 50)  # test exercises splitter, not the guard
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
async def test_core_generation_writes_reviewable_model_input_snapshot(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    context_file = staging / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md"
    context_file.parent.mkdir()
    context_file.write_text("# Doc Pack\n\nUnit A source evidence.", encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _FakeProvider:
        model_name = "fake/model"
        supports_tools = False

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            return ModelResult(
                success=True,
                text="# Component Inventory\n\nUnit A documented from source evidence.",
                model_name=self.model_name,
                input_tokens=12,
                output_tokens=8,
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    provider = _FakeProvider()
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt="Write the inventory for Unit A.",
        context_files=[context_file],
        opts=ModelOptions(max_tokens=1234),
        log_cb=log_cb,
    )

    assert result.success
    snapshot_dir = staging / "_model_inputs" / "phase3" / "001_02_COMPONENT_INVENTORY"
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["phase"] == "phase3"
    assert manifest["target"] == "02_COMPONENT_INVENTORY"
    assert manifest["options"]["max_tokens"] == 1234
    assert manifest["context_files"][0]["path"] == "_analysis/_doc_pack_02_COMPONENT_INVENTORY.md"
    # Full payload snapshots are debug-only; default runs keep just the manifest.
    assert not (snapshot_dir / "prompt.md").exists()
    assert not (snapshot_dir / "context_bundle.md").exists()
    assert not (snapshot_dir / "request.json").exists()
    assert not (snapshot_dir / "response.json").exists()
    assert not (staging / "_debug").exists()


@pytest.mark.asyncio
async def test_core_generation_debug_flag_writes_full_model_input_snapshot(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "pipeline_debug", True)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    context_file = staging / "_analysis" / "_doc_pack_02_COMPONENT_INVENTORY.md"
    context_file.parent.mkdir()
    context_file.write_text("# Doc Pack\n\nUnit A source evidence.", encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _FakeProvider:
        model_name = "fake/model"
        supports_tools = False

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            return ModelResult(
                success=True,
                text="# Component Inventory\n\nUnit A documented from source evidence.",
                model_name=self.model_name,
                input_tokens=12,
                output_tokens=8,
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    provider = _FakeProvider()
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt="Write the inventory for Unit A.",
        context_files=[context_file],
        opts=ModelOptions(max_tokens=1234),
        log_cb=log_cb,
    )

    assert result.success
    manifest_dir = staging / "_model_inputs" / "phase3" / "001_02_COMPONENT_INVENTORY"
    assert (manifest_dir / "manifest.json").exists()
    debug_snapshot_dir = staging / "_debug" / "model_inputs" / "phase3" / "001_02_COMPONENT_INVENTORY"
    bundle = (debug_snapshot_dir / "context_bundle.md").read_text(encoding="utf-8")
    prompt = (debug_snapshot_dir / "prompt.md").read_text(encoding="utf-8")
    request = json.loads((debug_snapshot_dir / "request.json").read_text(encoding="utf-8"))
    response = json.loads((debug_snapshot_dir / "response.json").read_text(encoding="utf-8"))

    assert "Write the inventory for Unit A." in prompt
    assert "## Prompt" in bundle
    assert "Unit A source evidence." in bundle
    assert request["max_tokens"] == 1234
    assert response["success"] is True


@pytest.mark.asyncio
async def test_core_generation_uses_complete_with_tools_for_tool_capable_phase3_provider(tmp_path, monkeypatch):
    # First-pass phase3 is single-shot by default; opt back into the tool loop to
    # exercise the agentic dispatch path that this test covers.
    from mcp_server.config import config
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", True)
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
            assert options.tool_input_token_cap is not None
            assert options.tool_phase == "phase3"
            assert options.tool_target == "02_COMPONENT_INVENTORY"
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
        phase="phase3",
        target="02_COMPONENT_INVENTORY",
        prompt="Write docs for Unit A",
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


@pytest.mark.asyncio
async def test_core_generation_phase2_uses_bounded_context_not_tools(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    context_file = staging / "group.md"
    context_file.write_text("Unit A table evidence\n", encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _ToolProvider:
        model_name = "fake/tool-model"
        provider_name = "openai"
        supports_tools = True

        def __init__(self):
            self.context_names = []

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            self.context_names = [p.name for p in context_files or []]
            return ModelResult(
                success=True,
                text="## Analysis\n\nUnit A documented from supplied bounded context.",
                model_name=self.model_name,
                input_tokens=8,
                output_tokens=12,
            )

        async def complete_with_tools(self, *args, **kwargs):
            raise AssertionError("Phase 2 core generation must not use complete_with_tools by default")

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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    provider = _ToolProvider()
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
    )

    assert result.success
    assert provider.context_names == ["group.md"]


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
    # This test deliberately creates ~80 microscopic slices to exercise
    # concurrency ordering; production-sized slices remain run-budgeted.
    monkeypatch.setattr(config, "docgen_min_run_call_budget", 200)
    monkeypatch.setattr(config, "docgen_max_run_calls", 200)
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
            # P2.7: the slice instruction is a byte-stable-prefix-preserving
            # TAIL context file, not a prompt-head mutation. The prompt itself
            # must therefore be identical across slices.
            assert "slice" not in prompt.lower()
            tail_text = ""
            for f in context_files or []:
                if f.name.endswith("__instruction.md"):
                    tail_text = f.read_text(encoding="utf-8")
            target = re.search(r"slice (\d+) of", tail_text)
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
async def test_openai_tool_loop_tolerates_deepseek_variant_tool_payloads(tmp_path, monkeypatch):
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
            if len(calls) == 1:
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "content": [{"type": "text", "text": ""}],
                                    "tool_calls": [
                                        {
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {
                                                "name": "read_artifact",
                                                "arguments": ["not", "an", "object"],
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
                request=httpx.Request("POST", url),
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": [{"type": "text", "text": "Unit A final analysis"}]},
                        }
                    ],
                    "usage": {"prompt_tokens": 17, "completion_tokens": 9},
                },
            )

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _FakeAsyncClient)
    provider = OpenAICompatibleProvider(
        "deepseek/deepseek-v4-pro",
        api_key="test",
        base_url="http://example.test/v1",
        provider_name="deepseek",
    )

    result = await provider.complete_with_tools("Analyze Unit A", staging, ModelOptions())

    assert result.success
    assert result.text == "Unit A final analysis"
    assert result.metadata["tool_calls"] == 1
    assert result.metadata["tool_call_ledger"][0]["ok"] is False


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
async def test_openai_tool_loop_fails_cleanly_on_input_token_cap(tmp_path, monkeypatch):
    import httpx
    import mcp_server.providers.custom.providers as provider_module

    staging = tmp_path / "docs_staging"
    staging.mkdir()
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
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
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
                                            "name": "list_artifacts",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 101, "completion_tokens": 1},
                },
            )

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _FakeAsyncClient)
    provider = OpenAICompatibleProvider(
        "fake/model",
        api_key="test",
        base_url="http://example.test/v1",
        provider_name="openai",
    )

    result = await provider.complete_with_tools(
        "Analyze Unit A",
        staging,
        ModelOptions(tool_input_token_cap=100, tool_phase="phase3", tool_target="doc"),
    )

    assert not result.success
    assert result.metadata["failure_kind"] == "budget_exceeded"
    assert result.metadata["incomplete_reason"] == "input_token_cap"
    assert "phase=phase3 target=doc" in (result.raw_error or "")
    assert len(calls) == 1


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
    # Resume/rerun consumers need exactly status.json + output.md; everything
    # else is debug-gated forensics and must not appear in a default run.
    assert (task_dir / "status.json").exists()
    assert (task_dir / "output.md").read_text(encoding="utf-8") == exact
    assert not (task_dir / "contract.json").exists()
    assert not (task_dir / "validation_report.json").exists()
    assert not (task_dir / "tool_call_ledger.json").exists()
    assert not (task_dir / "provider_metrics.json").exists()
    assert not (task_dir / "conversation_trace.json").exists()
    assert not (task_dir / "attempts").exists()
    assert not (staging / "_debug").exists()
    assert result.validation_report_path is None
    assert runtime.accepted_output(contract) == exact


@pytest.mark.asyncio
async def test_agent_task_runtime_debug_flag_persists_forensics(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "pipeline_debug", True)
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
    assert (task_dir / "status.json").exists()
    assert (task_dir / "output.md").read_text(encoding="utf-8") == exact
    debug_task_dir = staging / "_debug" / "agent_tasks" / "phase3_doc" / "02_COMPONENT_INVENTORY"
    assert (debug_task_dir / "contract.json").exists()
    assert (debug_task_dir / "validation_report.json").exists()
    assert (debug_task_dir / "tool_call_ledger.json").exists()
    assert (debug_task_dir / "provider_metrics.json").exists()
    assert (debug_task_dir / "conversation_trace.json").exists()
    assert (debug_task_dir / "attempts" / "01" / "output.md").read_text(encoding="utf-8") == exact
    assert result.validation_report_path == str(debug_task_dir / "validation_report.json")


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
        max_attempts=2,
    )

    dispatch_count = 0

    async def dispatch(task, tools):
        nonlocal dispatch_count
        dispatch_count += 1
        return ModelResult(
            # OpenAI-compatible providers may report transport success while
            # still terminating a non-empty response at the output ceiling.
            success=True,
            text="partial Unit A",
            model_name="fake/model",
            raw_error="incomplete: provider stopped because max tokens were reached",
            metadata={"finish_reason": "length", "incomplete_reason": "length"},
        )

    result = await runtime.run(contract, dispatch, lambda text: {"ok": True, "errors": []})

    assert not result.accepted
    assert result.status == "incomplete"
    assert result.incomplete_reason == "length"
    assert dispatch_count == 1
    status = json.loads((runtime.task_dir(contract) / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "incomplete"
    assert status["incomplete_reason"] == "length"


@pytest.mark.asyncio
async def test_agent_task_runtime_passes_validation_gaps_to_retry(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "pipeline_debug", True)
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
    debug_task_dir = staging / "_debug" / "agent_tasks" / "phase3_doc" / "02_COMPONENT_INVENTORY"
    trace = json.loads((debug_task_dir / "conversation_trace.json").read_text(encoding="utf-8"))
    assert trace[1]["validation_gaps"] == ["missing Unit A section"]


@pytest.mark.asyncio
async def test_agent_task_runtime_tool_budget_is_cumulative_across_attempts(tmp_path):
    """Tool calls from attempt 1 must reduce the budget available to attempt 2."""
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
        tool_budget=ToolBudget(max_calls=5, max_bytes=100_000, max_result_bytes=50_000),
    )

    seen_budgets: list[int] = []

    async def dispatch(task, tools):
        seen_budgets.append(task.tool_budget.max_calls)
        # Consume 3 tool calls on attempt 1, none on attempt 2
        if task.attempt == 1:
            for _ in range(3):
                tools.get_fact_table("groups")
            return ModelResult(success=True, text="first pass output", model_name="fake/model")
        return ModelResult(success=True, text="Unit A final output", model_name="fake/model")

    def validate(text):
        return {"ok": "final" in text, "errors": [] if "final" in text else ["missing section"]}

    result = await runtime.run(contract, dispatch, validate)

    assert result.accepted
    # Attempt 1: full budget of 5; attempt 2: 5 - 3 used = 2 remaining.
    assert seen_budgets == [5, 2]


def test_read_artifact_supports_offset_limit_and_truncates(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "large.md").write_text("0123456789" * 20, encoding="utf-8")
    contract = AgentTaskContract(
        phase="phase3_doc",
        target_id="doc",
        allowed_units=["Unit A"],
        expected_artifact="document markdown",
        tool_budget=ToolBudget(max_calls=5, max_bytes=1_000, max_result_bytes=200),
    )
    tools = ScopedEvidenceTools(staging, facts, contract)

    text = tools.read_artifact("large.md", offset=10, limit=12)

    assert text == "012345678901\n\n[truncated by read_artifact byte limit: offset=10 limit=12 total_bytes=200]"
    assert tools.calls[0].tool == "read_artifact"
    assert tools.calls[0].bytes == len(text.encode("utf-8"))


def test_read_artifact_consumes_tool_byte_budget(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "large.md").write_text("x" * 200, encoding="utf-8")
    contract = AgentTaskContract(
        phase="phase3_doc",
        target_id="doc",
        allowed_units=["Unit A"],
        expected_artifact="document markdown",
        tool_budget=ToolBudget(max_calls=5, max_bytes=40, max_result_bytes=80),
    )
    tools = ScopedEvidenceTools(staging, facts, contract)

    with pytest.raises(RuntimeError, match="tool byte budget exceeded"):
        tools.read_artifact("large.md", limit=80)

    assert tools.calls[0].tool == "read_artifact"
    assert tools.calls[0].ok is False


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
            raise AssertionError("form-only Phase 2 section repair must not call the model")

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
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )
    logs = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append((pid, message, level))

    partial = "## COMPONENTS\ncomponent evidence for a\n\n## EXECUTION_FLOW\nflow evidence for a"
    with pytest.raises(RuntimeError, match="missing required sections after deterministic patch"):
        await CustomHarnessProvider(_RepairProvider())._complete_phase2_sections(
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
    assert any("applied deterministic structural patch" in message for _, message, _ in logs)
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_phase2_section_repair_uses_no_model_tokens(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "phase2_max_tokens", 32_000)
    monkeypatch.setattr(config, "phase3_max_tokens", 64_000)
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    group_file = staging / "_preprocess" / "A.md"
    group_file.parent.mkdir(parents=True)
    group_file.write_text("source evidence", encoding="utf-8")
    model_calls = 0

    class _RepairProvider:
        model_name = "fake/model"
        supports_tools = False

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            nonlocal model_calls
            model_calls += 1
            raise AssertionError("form-only Phase 2 section repair must not call the model")

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
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    await CustomHarnessProvider(_RepairProvider())._complete_phase2_sections(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        "A",
        "### COMPONENTS\nUnit A evidence",
        [],
        [],
        group_file,
        ModelOptions(),
        log_cb,
    )

    assert model_calls == 0


@pytest.mark.asyncio
async def test_phase2_group_synthesis_appends_group_sections(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "phase2_max_tokens", 32_000)
    monkeypatch.setattr(config, "phase3_max_tokens", 64_000)
    facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)

    class _SynthesisProvider:
        model_name = "fake/model"
        supports_tools = False

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            return ModelResult(
                success=True,
                text=(
                    "### CROSS_JOB_FINDINGS\n\nUnit A and Unit B share External API risk.\n\n"
                    "### GROUP_SUMMARY\n\nGroup-level summary."
                ),
                model_name=self.model_name,
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    completed = await CustomHarnessProvider(_SynthesisProvider())._phase2_group_synthesis(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        "A",
        ["Unit A", "Unit B"],
        "### COMPONENTS\nassembled evidence",
        "### GROUP_SUMMARY\n\n#### task A__task_1: Unit A\n\nfallback summary",
        [],
        [],
        ModelOptions(),
        log_cb,
    )

    assert "### CROSS_JOB_FINDINGS" in completed
    assert "External API risk" in completed
    assert "### GROUP_SUMMARY" in completed
    assert "assembled evidence" in completed


@pytest.mark.asyncio
async def test_phase2_group_synthesis_failure_falls_back_to_task_summaries(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "phase2_max_tokens", 32_000)
    monkeypatch.setattr(config, "phase3_max_tokens", 64_000)
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)

    class _FailingProvider:
        model_name = "fake/model"
        supports_tools = False

        async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
            return ModelResult(success=False, model_name=self.model_name, raw_error="boom")

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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        pass

    completed = await CustomHarnessProvider(_FailingProvider())._phase2_group_synthesis(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        "A",
        ["Unit A"],
        "### COMPONENTS\nassembled evidence",
        "### GROUP_SUMMARY\n\n#### task A__task_1: Unit A\n\nfallback summary",
        [],
        [],
        ModelOptions(),
        log_cb,
    )

    assert "assembled evidence" in completed
    assert "### GROUP_SUMMARY" in completed
    assert "fallback summary" in completed


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
    # This end-to-end test exercises the FULL pipeline including the LLM
    # scorecard, which is config-gated OFF by default since R2.2.
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", True)

    staging = tmp_path / "output" / project_id / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_analysis").mkdir()
    (staging / "_assessment").mkdir()
    # Pad to >200 bytes so _phase2_group_direct takes the LLM path rather than the stub path.
    (staging / "_preprocess" / "Recipes.md").write_text(
        "Recipe One\n\n" + "recipe content detail for test " * 10, encoding="utf-8"
    )
    (staging / "_preprocess" / "Functions.md").write_text(
        "Function One\n\n" + "function content detail for test " * 10, encoding="utf-8"
    )
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
        platform="workato",
        analysis_guide_file="_analysis_guide.md",
        enterprise_mode=False,
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
    # index.json is debug-only (gated in Track A Slice 3)
    assert not (staging / "index.json").exists()
    # 09_EXECUTIVE_SUMMARY is enterprise-only; this test uses enterprise_mode=False
    expected_docs = [d for d in REQUIRED_DOCS if d != "09_EXECUTIVE_SUMMARY"]
    assert all((staging / f"{doc}.md").exists() for doc in expected_docs)
    report = validate_docs(staging, build_facts_manifest(project_id, scan_dir), required_docs=expected_docs)
    assert report.ok
    assert any("resolved providers" in message for _, message, _ in logs)
    claim_calls = [
        call for call in provider.calls
        if '"schema_version": "phase2-claims-v1"' in call["prompt"]
    ]
    assert len(claim_calls) == 2
    # The compact claim schema governs transport, while Nexus's platform
    # guide remains immutable system policy for semantics and grounding.
    assert all(
        "Nexus Platform Analysis Policy (Immutable)" in call["system_prompt"]
        and "_analysis_guide" in call["system_prompt"]
        for call in claim_calls
    )
    topology_claims = staging / "_topology" / "evidence" / "phase2_claims.jsonl"
    assert topology_claims.exists()
    assert len(topology_claims.read_text(encoding="utf-8").splitlines()) > 0
    assert any("quick_scan.md" in call["context_names"] for call in provider.calls)
    assert any(
        call["system_prompt"] and "_scoring_rubric" in call["system_prompt"]
        for call in provider.calls
    )
    assert any(
        call["system_prompt"] and "_phase3_guide" in call["system_prompt"]
        for call in provider.calls
    )
    assert any("_summary.md" in call["context_names"] for call in provider.calls)
    assert not any("You are a documentation critic" in call["prompt"] for call in provider.calls)
    # Direct path: no Phase 1 investigation calls (no evidence JSON prompt).
    assert not any(
        '"Return ONLY a valid JSON object"' in call["prompt"] or
        '"evidence"' in call["prompt"] and "Units to cover:" in call["prompt"]
        for call in provider.calls
    )
    assert any("direct path" in message for _, message, _ in logs)


@pytest.mark.asyncio
async def test_direct_path_scanner_limited_stub_passes_depth_gate(tmp_path, monkeypatch):
    """Stub analysis for scanner-limited groups must pass validate_analysis_depth without LLM calls."""
    from mcp_server.config import config
    from mcp_server.providers.custom.validators import validate_analysis_depth

    project_id = "stub_proj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="workato", jobs=[
        _job("Tiny Job", "TinyGroup", "WorkatoRecipe"),
    ])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")

    staging = tmp_path / "output" / project_id / "docs_staging"
    preprocess_dir = staging / "_preprocess"
    analysis_dir = staging / "_analysis"
    preprocess_dir.mkdir(parents=True)
    analysis_dir.mkdir()

    # Deliberately tiny — below min_investigation_content_bytes (200) — triggers stub path
    (preprocess_dir / "TinyGroup.md").write_text("tiny", encoding="utf-8")
    (preprocess_dir / "_analysis_guide.md").write_text("Workato analysis guide", encoding="utf-8")
    (staging / "_investigation").mkdir()
    (staging / "_investigation" / "_summary.md").write_text(
        "# Investigation Summary\n\nDirect path: Phase 1 skipped.\n", encoding="utf-8"
    )

    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["TinyGroup"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="workato",
        analysis_guide_file="_analysis_guide.md",
        enterprise_mode=False,
    )

    logs: list[tuple] = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append((pid, message, level))

    provider = _FakeProvider()
    provider.calls = []

    state = HarnessRunState(
        project_id=project_id,
        facts_manifest=build_facts_manifest(project_id, scan_dir),
    )
    harness = CustomHarnessProvider(provider)
    # Run only the direct Phase 2 path
    await harness._phase2_analysis_direct(ctx, state, log_cb)

    # No LLM calls — stub written directly
    assert provider.calls == []
    assert any("scanner-limited stub" in message for _, message, _ in logs)
    assert (analysis_dir / "TinyGroup.md").exists()

    # Depth gate must not fire for scanner-limited groups (preprocess < min_investigation_content_bytes)
    facts = build_facts_manifest(project_id, scan_dir)
    depth_report = validate_analysis_depth(analysis_dir, preprocess_dir, facts)
    assert depth_report.ok, depth_report.errors


# ---------------------------------------------------------------------------
# P2.2 / P2.3 — FailureKind.INCOMPLETE detection via metadata
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("metadata,expected", [
    ({"finish_reason": "length"}, FailureKind.INCOMPLETE),
    ({"finish_reason": "max_tokens"}, FailureKind.INCOMPLETE),
    ({"incomplete_reason": "length"}, FailureKind.INCOMPLETE),
    ({"finish_reason": "stop"}, None),
    ({}, None),
])
def test_classify_failure_detects_incomplete_via_metadata(metadata, expected):
    result = ModelResult(
        success=True if expected is None else False,
        text="partial output" if expected is None else "",
        raw_error=None if expected is None else "incomplete: provider stopped because max tokens were reached",
        metadata=metadata,
    )
    if expected is None:
        result = ModelResult(success=True, text="complete output", metadata=metadata)
        assert CustomHarnessProvider._classify_failure(result) is None
    else:
        assert CustomHarnessProvider._classify_failure(result) == expected


def test_classify_failure_rejects_successful_nonempty_truncated_response():
    result = ModelResult(
        success=True,
        text="## Partial\n\nNon-empty but truncated output.",
        metadata={"finish_reason": "length"},
    )

    assert CustomHarnessProvider._classify_failure(result) == FailureKind.INCOMPLETE


def test_incomplete_failure_kind_not_retryable_with_fallback():
    assert not FailureKind.INCOMPLETE.is_retryable_with_fallback
    assert FailureKind.RATE_LIMIT.is_retryable_with_fallback
    assert FailureKind.NETWORK.is_retryable_with_fallback
    assert not FailureKind.CONTEXT_TOO_LARGE.is_retryable_with_fallback
    assert not FailureKind.BUDGET_EXCEEDED.is_retryable_with_fallback


# ---------------------------------------------------------------------------
# P2.4 — Never repeat a request that proved too large
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_incomplete_phase3_suppresses_identical_force_conclude(tmp_path, monkeypatch):
    # Even on the tool path, a length stop must not trigger the same oversized
    # prompt/context again. The Phase 3 planner owns evidence reduction/splitting.
    from mcp_server.config import config
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", True)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    complete_calls: list[str] = []

    class _IncompleteProvider:
        model_name = "fake/deepseek"
        provider_name = "openai"
        supports_tools = True

        async def complete_with_tools(self, prompt, staging_dir, options=None, **kwargs):
            return ModelResult(
                success=False,
                text="partial",
                model_name=self.model_name,
                raw_error="incomplete: max tokens reached",
                metadata={"finish_reason": "length", "incomplete_reason": "length"},
            )

        async def complete(self, prompt, context_files=None, options=None):
            complete_calls.append(prompt)
            return ModelResult(
                success=True,
                text="## Unit A\n\nFull concluded output.",
                model_name=self.model_name,
                input_tokens=20,
                output_tokens=30,
                metadata={"estimated_input_tokens": 20, "context_bytes": 0},
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    logs: list[tuple] = []

    async def log_cb(pid, msg, level="info"):
        logs.append((pid, msg, level))

    provider = _IncompleteProvider()
    contract = AgentTaskContract(
        phase="phase2_group",
        target_id="A",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        expected_artifact="analysis markdown",
    )
    tools = ScopedEvidenceTools(staging, facts, contract)
    harness = CustomHarnessProvider(provider)
    result = await harness._call_model_with_logging(
        ctx,
        state,
        provider=provider,
        phase="phase3",
        target="A",
        prompt="Analyze Unit A",
        context_files=[],
        opts=ModelOptions(max_tokens=32_000),
        log_cb=log_cb,
        scoped_tools=tools,
    )

    assert not result.success
    assert len(complete_calls) == 0
    assert any("identical retry suppressed" in msg for _, msg, _ in logs)
    assert not any("retrying with fallback" in msg for _, msg, _ in logs)


# ---------------------------------------------------------------------------
# P3.4 — Primary call logged before fallback overwrites result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_primary_call_logged_before_fallback_overwrites_result(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _FailingProvider:
        model_name = "fake/primary"
        provider_name = "openai"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=False,
                model_name=self.model_name,
                raw_error="429 Too Many Requests",
                metadata={"estimated_input_tokens": 5, "context_bytes": 0},
            )

    class _FallbackProvider:
        model_name = "fake/fallback"
        provider_name = "openai"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                text="## Unit A\n\nFallback output.",
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=15,
                metadata={"estimated_input_tokens": 10, "context_bytes": 0},
            )

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/primary",
        write_model="fake/primary",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    state = HarnessRunState(project_id="proj", facts_manifest=facts)

    async def log_cb(pid, msg, level="info"):
        pass

    harness = CustomHarnessProvider(_FailingProvider(), fallback_provider=_FallbackProvider())
    await harness._call_model_with_logging(
        ctx,
        state,
        provider=_FailingProvider(),
        phase="phase3",
        target="01_ARCHITECTURE_MAP",
        prompt="Write the architecture map",
        context_files=[],
        opts=ModelOptions(),
        log_cb=log_cb,
    )

    assert len(state.calls) == 2
    assert state.calls[0].result.model_name == "fake/primary"
    assert not state.calls[0].result.success
    assert state.calls[1].result.model_name == "fake/fallback"
    assert state.calls[1].result.success


# ---------------------------------------------------------------------------
# P4.3 — Slice token aggregation from state.calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slice_token_aggregation_from_state_calls(tmp_path, monkeypatch):
    from mcp_server.config import config

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 100)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 200)
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    large_md = staging / "group.md"
    large_md.write_text("Unit A evidence\n" * 600, encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _CountingProvider:
        model_name = "fake/model"
        provider_name = "openai"
        supports_tools = False

        async def complete(self, prompt, context_files=None, options=None):
            return ModelResult(
                success=True,
                text="slice output for Unit A",
                model_name=self.model_name,
                input_tokens=50,
                output_tokens=25,
                metadata={"estimated_input_tokens": 50, "context_bytes": 100},
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    provider = _CountingProvider()
    harness = CustomHarnessProvider(provider)

    async def log_cb(pid, msg, level="info"):
        pass

    result = await harness._complete_core_generation(
        ctx,
        state,
        provider=provider,
        phase="phase3",
        target="01_ARCHITECTURE_MAP",
        prompt="Write the architecture map",
        context_files=[large_md],
        opts=ModelOptions(),
        log_cb=log_cb,
    )

    if len(state.calls) > 1:
        assert result.input_tokens > 0
        assert result.output_tokens > 0
        assert result.input_tokens == sum(c.result.input_tokens for c in state.calls)
        assert result.output_tokens == sum(c.result.output_tokens for c in state.calls)


# ---------------------------------------------------------------------------
# P5.1 — Atomic writes leave no .tmp files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_runtime_atomic_writes_leave_no_tmp_files(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    runtime = AgentTaskRuntime(staging, facts)
    contract = AgentTaskContract(
        phase="phase3_doc",
        target_id="01_ARCHITECTURE_MAP",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        expected_artifact="document markdown",
    )

    async def dispatch(task, tools):
        return ModelResult(
            success=True,
            text="## Unit A\n\nArchitecture content.",
            model_name="fake/model",
            input_tokens=5,
            output_tokens=10,
        )

    await runtime.run(contract, dispatch, lambda text: {"ok": "Unit A" in text, "errors": []})

    task_dir = runtime.task_dir(contract)
    tmp_files = list(task_dir.glob("**/*.tmp"))
    assert tmp_files == [], f"leftover .tmp files: {tmp_files}"


# ---------------------------------------------------------------------------
# P5.2 — accepted_output tolerates OSError/UnicodeDecodeError on corrupt status
# ---------------------------------------------------------------------------

def test_agent_runtime_accepted_output_tolerates_corrupt_status_file(tmp_path):
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    runtime = AgentTaskRuntime(staging, facts)
    contract = AgentTaskContract(
        phase="phase3_doc",
        target_id="03_DATA_FLOWS",
        allowed_units=["Unit A"],
        allowed_groups=["A"],
        expected_artifact="document markdown",
    )
    task_dir = runtime.task_dir(contract)
    task_dir.mkdir(parents=True)
    (task_dir / "output.md").write_text("## Unit A\n\nContent.", encoding="utf-8")
    (task_dir / "status.json").write_bytes(b"\xff\xfe invalid json bytes")

    assert runtime.accepted_output(contract) is None


# ---------------------------------------------------------------------------
# P6.1 — OpenAIOfficialProvider maps Responses API status="incomplete" to failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_official_provider_maps_incomplete_status_to_failure(tmp_path, monkeypatch):
    import mcp_server.providers.custom.providers as provider_module
    from mcp_server.providers.custom.providers import OpenAIOfficialProvider

    staging = tmp_path / "docs_staging"
    staging.mkdir()

    class _FakeResponse:
        id = "resp-1"
        status = "incomplete"

        def __iter__(self):
            return iter([])

    class _FakeResponses:
        async def create(self, **kwargs):
            return _FakeResponse()

    class _FakeClient:
        responses = _FakeResponses()

    def _response_output_text_stub(response):
        return "partial output"

    def _response_output_items_stub(response):
        return []

    def _response_usage_stub(response):
        return 10, 5

    monkeypatch.setattr(provider_module, "_response_output_text", _response_output_text_stub)
    monkeypatch.setattr(provider_module, "_response_output_items", _response_output_items_stub)
    monkeypatch.setattr(provider_module, "_response_usage", _response_usage_stub)

    provider = OpenAIOfficialProvider("fake/gpt-4", api_key="test")
    monkeypatch.setattr(provider, "_client", lambda timeout_s: _FakeClient())

    result = await provider.complete_with_tools("Write docs", staging, ModelOptions())

    assert not result.success
    assert result.metadata["finish_reason"] == "length"
    assert result.metadata["incomplete_reason"] == "length"


# ---------------------------------------------------------------------------
# P6.2 — OpenAIOfficialProvider does not retry on auth errors (401/403)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_official_provider_skips_retry_on_auth_error(tmp_path, monkeypatch):
    import mcp_server.providers.custom.providers as provider_module
    from mcp_server.providers.custom.providers import OpenAIOfficialProvider

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    call_count = 0

    class _FakeResponses:
        async def create(self, **kwargs):
            nonlocal call_count
            call_count += 1
            exc = Exception("401 Unauthorized")
            exc.status_code = 401  # type: ignore[attr-defined]
            raise exc

    class _FakeClient:
        responses = _FakeResponses()

    provider = OpenAIOfficialProvider("fake/gpt-4", api_key="test")
    monkeypatch.setattr(provider, "_client", lambda timeout_s: _FakeClient())
    monkeypatch.setattr(provider_module, "_response_output_items", lambda r: [])
    monkeypatch.setattr(provider_module, "_response_usage", lambda r: (0, 0))

    result = await provider.complete_with_tools("Write docs", staging, ModelOptions())

    assert not result.success


# ── PhasePolicy and _phase_policy() ──────────────────────────────────────────

def test_phase_policy_defaults_all_skip_when_evidence_profiles_present(tmp_path):
    """Backward compat: manifest with evidence_profiles but no phases block → all skip."""
    manifest = PluginPackManifest.model_validate({
        "name": "test", "version": "1",
        "evidence_profiles": {"p": {"include": ["sql_queries"]}},
        "steps": [{"id": "s", "type": "harness"}],
    })
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    provider = CustomHarnessProvider(MagicMock(model_name="m"), plugin_pack_dir=pack_dir)
    provider._plugin_manifest = manifest

    assert provider._phase_policy("investigation") == "skip"
    assert provider._phase_policy("analysis") == "skip"
    assert provider._phase_policy("scorecard") == "skip"
    assert provider._phase_policy("doc_bundles") == "skip"
    assert provider._pack_contract_mode is True


def test_phase_policy_explicit_phases_override_backward_compat(tmp_path):
    """Explicit phases block takes precedence over evidence_profiles derivation."""
    manifest = PluginPackManifest.model_validate({
        "name": "test", "version": "1",
        "evidence_profiles": {"p": {"include": ["sql_queries"]}},
        "phases": {
            "investigation": "run",
            "analysis": "bounded",
            "scorecard": "skip",
            "doc_bundles": "skip",
        },
        "steps": [{"id": "s", "type": "harness"}],
    })
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    provider = CustomHarnessProvider(MagicMock(model_name="m"), plugin_pack_dir=pack_dir)
    provider._plugin_manifest = manifest

    assert provider._phase_policy("investigation") == "run"
    assert provider._phase_policy("analysis") == "bounded"
    assert provider._phase_policy("scorecard") == "skip"
    assert provider._phase_policy("doc_bundles") == "skip"
    assert provider._pack_contract_mode is False


def test_phase_policy_no_pack_always_run(monkeypatch):
    """No plugin_pack_dir → all phases run (enterprise default), except the
    advisory LLM scorecard which is config-gated OFF by default (R2.2)."""
    from mcp_server.config import config

    provider = CustomHarnessProvider(MagicMock(model_name="m"))
    assert provider._phase_policy("investigation") == "run"
    assert provider._phase_policy("analysis") == "run"
    assert provider._phase_policy("scorecard") == "skip"  # config-gated default
    assert provider._phase_policy("doc_bundles") == "run"
    assert provider._pack_contract_mode is False
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", True)
    assert provider._phase_policy("scorecard") == "run"


def test_phase_policy_schema_defaults():
    """PhasePolicy default field values match expected pack-contract defaults."""
    from mcp_server.services.plugin_schema import PhasePolicy
    p = PhasePolicy()
    assert p.investigation == "skip"
    assert p.analysis == "skip"
    assert p.scorecard == "skip"
    assert p.doc_bundles == "skip"


def test_phase_policy_manifest_with_phases_block_parses():
    """PluginPackManifest accepts a phases block via dict."""
    manifest = PluginPackManifest.model_validate({
        "name": "security-audit", "version": "1.0.0",
        "phases": {
            "investigation": "run",
            "analysis": "bounded",
            "scorecard": "skip",
            "doc_bundles": "skip",
        },
        "docs": [{"name": "SECURITY_FINDINGS", "evidence_profile": "security", "required_outputs": ["security_findings"]}],
        "evidence_profiles": {"security": {"include": ["auth", "config_references"]}},
        "steps": [{"id": "full_pipeline", "type": "harness"}],
    })
    assert manifest.phases is not None
    assert manifest.phases.investigation == "run"
    assert manifest.phases.analysis == "bounded"
    assert manifest.phases.scorecard == "skip"
    assert manifest.phases.doc_bundles == "skip"


# ── Investigation preamble override ──────────────────────────────────────────

def test_investigation_preamble_prepended_to_prompt(tmp_path):
    """investigation.md content is prepended to the investigator's base prompt."""
    from mcp_server.providers.custom.investigator import SingleShotInvestigator
    facts = build_facts_manifest_from_units("p", ["UnitA"])
    inv = SingleShotInvestigator(
        MagicMock(model_name="m"), tmp_path, facts,
        preamble="Focus on authentication flows only.",
    )
    prompt = inv._build_prompt("G", "G/source.md", "source content here", ["UnitA"])
    assert prompt.startswith("Focus on authentication flows only.")
    assert "Extract evidence" in prompt


def test_investigation_no_preamble_unchanged(tmp_path):
    """No preamble → prompt starts with the default opening (no regression)."""
    from mcp_server.providers.custom.investigator import SingleShotInvestigator
    facts = build_facts_manifest_from_units("p", ["UnitA"])
    inv = SingleShotInvestigator(MagicMock(model_name="m"), tmp_path, facts)
    prompt = inv._build_prompt("G", "G/source.md", "source content here", ["UnitA"])
    assert prompt.startswith("You are extracting")


# ── Bounded phase2.md full override ──────────────────────────────────────────

def test_bounded_phase2_prompt_uses_pack_file_not_enterprise_sections(tmp_path):
    """In bounded mode with prompts/phase2.md, the pack file becomes the complete
    Phase 2 prompt — mandatory enterprise sections are not included."""
    from mcp_server.providers.custom.prompts import facts_summary

    pack_dir = tmp_path / "pack"
    (pack_dir / "prompts").mkdir(parents=True)
    (pack_dir / "prompts" / "phase2.md").write_text(
        "Focus only on auth flows and hardcoded secrets.", encoding="utf-8"
    )
    manifest = PluginPackManifest.model_validate({
        "name": "sec", "version": "1",
        "phases": {"analysis": "bounded"},
        "steps": [{"id": "s", "type": "harness"}],
    })
    provider = CustomHarnessProvider(MagicMock(model_name="m"), plugin_pack_dir=pack_dir)
    provider._plugin_manifest = manifest

    assert provider._phase_policy("analysis") == "bounded"

    pack_phase2_override = pack_dir / "prompts" / "phase2.md"
    assert pack_phase2_override.exists()

    facts = build_facts_manifest_from_units("p", ["UnitA"])
    group_units = ["UnitA"]
    prompt = (
        f"{pack_phase2_override.read_text(encoding='utf-8').rstrip()}\n\n"
        f"Group: G\n"
        f"Canonical units to cover:\n" + "\n".join(f"- {u}" for u in group_units) + "\n\n"
        f"Run facts:\n{facts_summary(facts)}\n"
    )
    assert prompt.startswith("Focus only on auth flows and hardcoded secrets.")
    assert "Canonical units to cover:" in prompt
    assert "UnitA" in prompt
    assert "MANDATORY SECTION STRUCTURE" not in prompt
    assert "EXECUTION_FLOW" not in prompt


def test_bounded_no_pack_phase2_md_falls_back_to_standard_prompt(tmp_path):
    """bounded mode without prompts/phase2.md uses the standard group_analysis_prompt."""
    from mcp_server.providers.custom.prompts import group_analysis_prompt

    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    manifest = PluginPackManifest.model_validate({
        "name": "sec", "version": "1",
        "phases": {"analysis": "bounded"},
        "steps": [{"id": "s", "type": "harness"}],
    })
    provider = CustomHarnessProvider(MagicMock(model_name="m"), plugin_pack_dir=pack_dir)
    provider._plugin_manifest = manifest

    pack_phase2_override = (
        pack_dir / "prompts" / "phase2.md"
        if pack_dir and provider._phase_policy("analysis") == "bounded"
        else None
    )
    # File does not exist → standard prompt
    assert pack_phase2_override is not None
    assert not pack_phase2_override.exists()

    facts = build_facts_manifest_from_units("p", ["UnitA"])
    prompt = group_analysis_prompt(facts, "G", plugin_pack_dir=pack_dir)
    assert "MANDATORY SECTION STRUCTURE" in prompt


# ---------------------------------------------------------------------------
# ChunkManifestInvestigator tests
# ---------------------------------------------------------------------------

def _make_chunk_file(directory: Path, name: str, content: str) -> Path:
    path = directory / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_chunk_manifest_investigator_selects_and_extracts(tmp_path):
    """Happy path: manifest call selects one chunk, extraction call returns valid evidence."""
    facts = build_facts_manifest_from_units("proj", ["UnitA", "UnitB"])
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    group_file = preprocess / "group.md"
    group_file.write_text("UnitA and UnitB source content here.\n" * 20, encoding="utf-8")
    chunk1 = _make_chunk_file(preprocess, "group_chunk_1", "UnitA source content here.\n" * 20)
    chunk2 = _make_chunk_file(preprocess, "group_chunk_2", "UnitB source content here.\n" * 20)

    manifest = [
        {"id": "group_chunk_1", "size_bytes": chunk1.stat().st_size, "unit_names": ["UnitA"], "keyword_hints": []},
        {"id": "group_chunk_2", "size_bytes": chunk2.stat().st_size, "unit_names": ["UnitB"], "keyword_hints": []},
    ]

    evidence_payload = json.dumps({
        "evidence": [
            {"unit_name": "UnitA", "category": "data flow", "source_file": "_preprocess/group.md",
             "chunk_ref": "_preprocess/group.md", "line_range": "1-5",
             "snippet": "UnitA source content here.", "observation": "UnitA processes source content directly"},
            {"unit_name": "UnitB", "category": "data flow", "source_file": "_preprocess/group.md",
             "chunk_ref": "_preprocess/group.md", "line_range": "6-10",
             "snippet": "UnitB source content here.", "observation": "UnitB handles secondary content flow"},
        ],
        "skipped_units": {},
    })

    call_log: list[str] = []

    class _Provider:
        async def complete(self, prompt: str, options=None, **kwargs):
            if "fetch_ids" in prompt or "CHUNK MANIFEST" in prompt:
                call_log.append("manifest")
                return ModelResult(
                    success=True,
                    text='{"fetch_ids": ["group_chunk_1", "group_chunk_2"], "reasoning": "both needed"}',
                    model_name="fake",
                )
            call_log.append("extract")
            return ModelResult(success=True, text=evidence_payload, model_name="fake")

    investigator = ChunkManifestInvestigator(_Provider(), staging, facts)
    report, calls = await investigator.run(
        "group",
        chunks=[chunk1, chunk2],
        group_units=["UnitA", "UnitB"],
        manifest=manifest,
        group_file=group_file,
    )

    assert len(call_log) == 2
    assert call_log[0] == "manifest"
    assert call_log[1] == "extract"
    assert {ev.unit_name for ev in report.evidence} == {"UnitA", "UnitB"}
    assert report.skipped_units == {}


@pytest.mark.asyncio
async def test_chunk_manifest_investigator_falls_back_on_bad_selection(tmp_path):
    """If the manifest call returns invalid JSON, fall back to all chunks."""
    facts = build_facts_manifest_from_units("proj", ["UnitA"])
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    group_file = preprocess / "group.md"
    group_file.write_text("UnitA source.\n" * 20, encoding="utf-8")
    chunk1 = _make_chunk_file(preprocess, "group_chunk_1", "UnitA source.\n" * 20)

    manifest = [{"id": "group_chunk_1", "size_bytes": 100, "unit_names": ["UnitA"], "keyword_hints": []}]

    evidence_payload = json.dumps({
        "evidence": [
            {"unit_name": "UnitA", "category": "data flow", "source_file": "_preprocess/group.md",
             "chunk_ref": "_preprocess/group.md", "line_range": "1-3",
             "snippet": "UnitA source.", "observation": "UnitA processes source data directly"},
        ],
        "skipped_units": {},
    })

    class _Provider:
        async def complete(self, prompt: str, options=None, **kwargs):
            if "CHUNK MANIFEST" in prompt:
                return ModelResult(success=True, text="not valid json at all", model_name="fake")
            return ModelResult(success=True, text=evidence_payload, model_name="fake")

    investigator = ChunkManifestInvestigator(_Provider(), staging, facts)
    report, calls = await investigator.run(
        "group",
        chunks=[chunk1],
        group_units=["UnitA"],
        manifest=manifest,
        group_file=group_file,
    )

    assert any(ev.unit_name == "UnitA" for ev in report.evidence)


@pytest.mark.asyncio
async def test_chunk_manifest_investigator_auto_skips_on_empty_manifest(tmp_path):
    """Empty manifest → all units skipped, no LLM calls."""
    facts = build_facts_manifest_from_units("proj", ["UnitA"])
    staging = tmp_path / "staging"
    staging.mkdir()
    group_file = staging / "_preprocess" / "group.md"
    group_file.parent.mkdir(parents=True)
    group_file.write_text("", encoding="utf-8")

    call_count = 0

    class _Provider:
        async def complete(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ModelResult(success=True, text="{}", model_name="fake")

    investigator = ChunkManifestInvestigator(_Provider(), staging, facts)
    report, calls = await investigator.run(
        "group",
        chunks=[],
        group_units=["UnitA"],
        manifest=[],
        group_file=group_file,
    )

    assert call_count == 0
    assert "UnitA" in report.skipped_units
    assert report.evidence == []


# --- Session 2: Phase 2 context assembly fixes ---


def test_phase2_shared_context_uses_evidence_brief_not_registry(tmp_path):
    """_phase2_shared_context must include evidence_brief.md, not evidence_registry.md."""
    from mcp_server.providers.custom.evidence_agents import EvidenceRegistryBuilder, resolve_evidence_agents
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "Webservices.md").write_text(
        "```sql\nselect id from Customer\n```\n", encoding="utf-8"
    )
    facts_path = staging / "facts_manifest.json"
    facts_path.write_text('{"project": "test"}', encoding="utf-8")

    resolution = resolve_evidence_agents(None, None)
    paths = EvidenceRegistryBuilder(staging).build(resolution.agents)

    harness = CustomHarnessProvider(MagicMock())
    harness._evidence_registry_paths = paths
    ctx = DocGenContext(
        project_id="test",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=[],
        total_jobs=1,
        total_groups=1,
        effective_model="fake",
        write_model="fake",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    files = harness._phase2_shared_context(ctx, facts_path)
    file_names = [f.name for f in files]
    assert "evidence_brief.md" in file_names
    assert "evidence_registry.md" not in file_names
    assert "_source_coverage.json" not in file_names


def test_phase2_shared_context_excludes_source_coverage(tmp_path):
    """_phase2_shared_context must not include _source_coverage.json in static context."""
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    (preprocess / "_source_coverage.json").write_text(
        '{"scanned": 100}', encoding="utf-8"
    )
    facts_path = staging / "facts_manifest.json"
    facts_path.write_text('{"project": "test"}', encoding="utf-8")

    harness = CustomHarnessProvider(MagicMock())
    harness._evidence_registry_paths = {}
    ctx = DocGenContext(
        project_id="test",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=[],
        total_jobs=1,
        total_groups=1,
        effective_model="fake",
        write_model="fake",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    files = harness._phase2_shared_context(ctx, facts_path)
    assert not any(f.name == "_source_coverage.json" for f in files)


@pytest.mark.asyncio
async def test_max_slices_guard_raises_for_phase2(tmp_path, monkeypatch):
    """Phase 2 must raise RuntimeError when _split_context_pack produces too many slices."""
    from mcp_server.config import config

    monkeypatch.setattr(config, "core_context_soft_target_tokens", 100)
    monkeypatch.setattr(config, "core_context_hard_ceiling_tokens", 200)
    monkeypatch.setattr(config, "agent_phase2_max_slices", 2)

    staging = tmp_path / "staging"
    staging.mkdir()
    large_md = staging / "group.md"
    large_md.write_text("Unit A content line\n" * 500, encoding="utf-8")
    facts = build_facts_manifest_from_units("proj", ["Unit A"])

    class _Provider:
        model_name = "fake"
        provider_name = "openai"
        supports_tools = False

        async def complete(self, *args, **kwargs):
            return ModelResult(success=True, text="ok", model_name=self.model_name)

    ctx = DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["A"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake",
        write_model="fake",
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    provider = _Provider()
    harness = CustomHarnessProvider(provider)

    with pytest.raises(RuntimeError, match="agent_phase2_max_slices"):
        await harness._complete_core_generation(
            ctx,
            HarnessRunState(project_id="proj", facts_manifest=facts),
            provider=provider,
            phase="phase2",
            target="A",
            prompt="Analyze Unit A",
            context_files=[large_md],
            opts=ModelOptions(),
            log_cb=lambda *a, **k: None,
        )


# --- Session 3: per-doc evidence slices for Phase 3 ---


def test_phase3_context_uses_doc_slice_not_registry(tmp_path):
    """_phase3_context_files must include a per-doc slice, not the full evidence_registry.md."""
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_assessment" / "prompt_packs").mkdir(parents=True)
    (staging / "_preprocess").mkdir()
    (staging / "_analysis" / "_doc_pack_manifest.json").write_text('{"doc_packs": {}}', encoding="utf-8")
    doc_pack = staging / "_analysis" / "_doc_pack_03_DATA_FLOWS_AND_LINEAGE.md"
    doc_pack.write_text("doc pack content", encoding="utf-8")

    # Write per-doc slice and full registry — harness must prefer the slice.
    slices_dir = staging / "_evidence_registry" / "doc_slices"
    slices_dir.mkdir(parents=True)
    doc_slice = slices_dir / "03_DATA_FLOWS_AND_LINEAGE.md"
    doc_slice.write_text("# Evidence Brief\nClaims: 3\n", encoding="utf-8")
    registry_dir = staging / "_evidence_registry"
    (registry_dir / "evidence_registry.md").write_text("FULL REGISTRY", encoding="utf-8")
    (registry_dir / "evidence_brief.md").write_text("# Evidence Brief\nClaims: 10\n", encoding="utf-8")

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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))

    files = harness._phase3_context_files(ctx, "03_DATA_FLOWS_AND_LINEAGE")

    assert doc_slice in files
    assert not any("evidence_registry.md" in str(f) for f in files)


def test_phase3_context_falls_back_to_evidence_brief_when_no_doc_slice(tmp_path):
    """When no per-doc slice exists, _phase3_context_files must use evidence_brief.md."""
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_assessment" / "prompt_packs").mkdir(parents=True)
    (staging / "_preprocess").mkdir()
    (staging / "_analysis" / "_doc_pack_manifest.json").write_text('{"doc_packs": {}}', encoding="utf-8")
    doc_pack = staging / "_analysis" / "_doc_pack_03_DATA_FLOWS_AND_LINEAGE.md"
    doc_pack.write_text("doc pack content", encoding="utf-8")

    # Write only evidence_brief, no doc slice.
    registry_dir = staging / "_evidence_registry"
    registry_dir.mkdir(parents=True)
    evidence_brief = registry_dir / "evidence_brief.md"
    evidence_brief.write_text("# Evidence Brief\nClaims: 10\n", encoding="utf-8")
    (registry_dir / "evidence_registry.md").write_text("FULL REGISTRY", encoding="utf-8")

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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )
    harness = CustomHarnessProvider(OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1"))

    files = harness._phase3_context_files(ctx, "03_DATA_FLOWS_AND_LINEAGE")

    assert evidence_brief in files
    assert not any("evidence_registry.md" in str(f) for f in files)


# ---------------------------------------------------------------------------
# Slice 2: warning code contract tests
# ---------------------------------------------------------------------------


def test_phase1_warning_code_importable() -> None:
    from mcp_server.providers.custom.pipeline.warning_collector import WarningCode

    assert WarningCode.PHASE1_GROUP_FAILURE == "PHASE1_GROUP_FAILURE"
    assert WarningCode.PHASE2_COVERAGE_PARTIAL == "PHASE2_COVERAGE_PARTIAL"
    assert WarningCode.PHASE2_DEPTH_PARTIAL == "PHASE2_DEPTH_PARTIAL"
    assert WarningCode.THIN_BUNDLE == "THIN_BUNDLE"


def test_phase2_coverage_partial_context_fields() -> None:
    from mcp_server.providers.custom.pipeline.warning_collector import PipelineWarning, WarningCode

    w = PipelineWarning(
        phase="phase2",
        code=WarningCode.PHASE2_COVERAGE_PARTIAL,
        message="partial",
        context={"covered": 2, "total": 5},
    )
    assert "covered" in w.context
    assert "total" in w.context
    assert w.context["covered"] == 2
    assert w.context["total"] == 5


def test_transient_artifacts_writes_under_unified_dir(tmp_path: Path) -> None:
    from mcp_server.providers.custom.transient_artifacts import (
        TRANSIENT_DIR_NAME,
        transient_dir,
        transient_write,
    )

    staging = tmp_path / "docs_staging"
    staging.mkdir()

    path = transient_dir(staging, "phase2_merge_group_a.md")
    assert path == staging / TRANSIENT_DIR_NAME / "phase2_merge_group_a.md"
    assert not path.exists()

    transient_write(path, "content")
    assert path.read_text(encoding="utf-8") == "content"


@pytest.mark.asyncio
async def test_phase4_cleanup_removes_transient_dir(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    transient = staging / "_transient"
    transient.mkdir()
    (transient / "phase2_merge_A.md").write_text("stale", encoding="utf-8")
    (staging / "_preprocess").mkdir()
    (staging / "_analysis").mkdir()

    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        return None

    from mcp_server.providers.custom.pipeline.helpers import HarnessHelpersImpl
    from mcp_server.providers.custom.pipeline.phases.phase4_cleanup import Phase4Cleanup

    provider = CustomHarnessProvider(
        OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1")
    )
    await Phase4Cleanup(HarnessHelpersImpl(provider)).run(ctx, state, log_cb)

    assert not (staging / "_transient").exists()
    assert not (staging / "_preprocess").exists()
    assert not (staging / "_analysis").exists()


def test_transient_dir_regenerated_on_resume(tmp_path: Path) -> None:
    """Mid-phase-interrupt: a stale _transient/ is fully replaced on the next run.

    The consolidated _transient/ dir is throwaway. If a previous run died
    mid-phase, the next run deletes the stale dir and the phase body
    regenerates whatever transient files it needs. No resume key.
    """

    import shutil

    from mcp_server.providers.custom.transient_artifacts import transient_write

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    transient = staging / "_transient"
    transient.mkdir()
    (transient / "phase2_merge_stale_group.md").write_text("stale from previous run", encoding="utf-8")

    shutil.rmtree(transient, ignore_errors=True)

    target = transient / "phase2_merge_new_group.md"
    transient_write(target, "regenerated on resume")

    assert not (transient / "phase2_merge_stale_group.md").exists()
    assert target.read_text(encoding="utf-8") == "regenerated on resume"


def test_phase2_source_lookup_single_source_of_truth(tmp_path: Path) -> None:
    """Harness helper and prompt_packs._phase2_sources must agree on every input shape."""

    from mcp_server.providers.custom.harness import CustomHarnessProvider
    from mcp_server.providers.custom.prompt_packs import _phase2_sources as pp_phase2_sources

    preprocess_dir = tmp_path / "_preprocess"
    preprocess_dir.mkdir()
    (preprocess_dir / "_analysis_prompts_generic.md").write_text("glob-a", encoding="utf-8")
    (preprocess_dir / "_analysis_prompts_extra.md").write_text("glob-b", encoding="utf-8")
    (preprocess_dir / "_my_guide.md").write_text("specific", encoding="utf-8")

    guide = "_my_guide.md"
    cases = [
        ("specific-guide-present", guide),
        ("sentinel-string", "_analysis_prompts*.md"),
        ("missing-guide-name", "_does_not_exist.md"),
        ("empty-guide", ""),
    ]
    for label, guide_file in cases:
        harness_result = CustomHarnessProvider._phase2_analysis_guide_files(
            preprocess_dir, guide_file
        )
        prompt_packs_result = pp_phase2_sources(preprocess_dir, guide_file)
        assert [p.name for p in harness_result] == [p.name for p in prompt_packs_result], (
            f"mismatch for {label}: harness={harness_result} pp={prompt_packs_result}"
        )

    guide_present = CustomHarnessProvider._phase2_analysis_guide_files(preprocess_dir, guide)
    assert guide_present == [preprocess_dir / "_my_guide.md"]

    sentinel_result = CustomHarnessProvider._phase2_analysis_guide_files(
        preprocess_dir, "_analysis_prompts*.md"
    )
    assert {p.name for p in sentinel_result} == {
        "_analysis_prompts_extra.md",
        "_analysis_prompts_generic.md",
    }


@pytest.mark.asyncio
async def test_audit_bundle_only_writes_gates_and_hashes(tmp_path: Path) -> None:
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    (staging / "01_OVERVIEW.md").write_text("# Overview\n", encoding="utf-8")
    (staging / "02_COMPONENT_INVENTORY.md").write_text("# Inventory\n", encoding="utf-8")
    (staging / "facts_manifest.json").write_text("{}", encoding="utf-8")
    (staging / "documentation_obligations.json").write_text("{}", encoding="utf-8")
    (staging / "provider_calls.json").write_text("[]", encoding="utf-8")
    (staging / "_assessment").mkdir()
    (staging / "_assessment" / "scorecard.md").write_text("# Scorecard\n", encoding="utf-8")
    (staging / "_analysis").mkdir()
    (staging / "_analysis" / "group_a.md").write_text("# Group A\n", encoding="utf-8")
    (staging / "_investigation").mkdir()
    (staging / "_investigation" / "group_a.json").write_text("{}", encoding="utf-8")

    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
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
        platform="generic",
        analysis_guide_file="_analysis_guide.md",
    )

    gate_report = {"mode": "enterprise", "client_ready": True, "gates": {}}
    provider = CustomHarnessProvider(
        OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1")
    )
    await provider._write_audit_bundle(ctx, state, gate_report)

    audit_dir = staging / "_audit"
    assert audit_dir.is_dir()
    contents = sorted(p.name for p in audit_dir.iterdir())
    assert contents == ["enterprise_gates.json", "final_doc_hashes.json"]

    gates = json.loads((audit_dir / "enterprise_gates.json").read_text(encoding="utf-8"))
    assert gates == gate_report

    hashes = json.loads((audit_dir / "final_doc_hashes.json").read_text(encoding="utf-8"))
    assert set(hashes.keys()) == {"01_OVERVIEW.md", "02_COMPONENT_INVENTORY.md"}
    assert all(len(value) == 64 for value in hashes.values())


def test_load_enterprise_gate_results_falls_back_to_audit(tmp_path: Path) -> None:
    from mcp_server.api.routes.projects import _load_enterprise_gate_results

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    audit_dir = staging / "_audit"
    audit_dir.mkdir()
    audit_payload = {
        "mode": "enterprise",
        "client_ready": True,
        "gates": {"valid_fresh_scan": {"ok": True}},
    }
    (audit_dir / "enterprise_gates.json").write_text(
        json.dumps(audit_payload), encoding="utf-8"
    )

    result = _load_enterprise_gate_results(staging)
    assert result == audit_payload


def test_load_enterprise_gate_results_prefers_assessment_over_audit(tmp_path: Path) -> None:
    from mcp_server.api.routes.projects import _load_enterprise_gate_results

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    assessment_dir = staging / "_assessment"
    assessment_dir.mkdir()
    (assessment_dir / "enterprise_gates.json").write_text(
        json.dumps({"mode": "draft", "client_ready": False}), encoding="utf-8"
    )
    audit_dir = staging / "_audit"
    audit_dir.mkdir()
    (audit_dir / "enterprise_gates.json").write_text(
        json.dumps({"mode": "enterprise", "client_ready": True}), encoding="utf-8"
    )

    result = _load_enterprise_gate_results(staging)
    assert result == {"mode": "draft", "client_ready": False}


def test_phase3_obligation_gap_logs_warning_as_advisory() -> None:
    """Failed Phase 3 obligation checks surface as warnings, not errors.

    Removed: _phase3_targeted_repair + retry loop. The doc ships as-written;
    the gap appears in the log as a warning with a count + first 3 messages.
    """
    import tempfile
    from mcp_server.providers.custom.validators import validate_docs

    with tempfile.TemporaryDirectory() as staging_dir:
        staging = Path(staging_dir) / "docs_staging"
        (staging / "_assessment").mkdir(parents=True)
        (staging / "_analysis").mkdir()
        (staging / "_investigation").mkdir()
        facts = build_facts_manifest_from_units("proj", ["Unit A", "Unit B"])
        facts.write(staging / "facts_manifest.json")
        (staging / "02_COMPONENT_INVENTORY.md").write_text(
            "# Inventory\n\nUnit A is documented.\n", encoding="utf-8"
        )

        report = validate_docs(
            staging,
            facts,
            required_docs=["02_COMPONENT_INVENTORY"],
            enforce_default_contract=True,
        )
        assert not report.ok
        assert report.errors

        logs: list[tuple[str, str]] = []

        async def log_cb(pid: str, message: str, level: str = "info") -> None:
            logs.append((level, message))

        async def emit_warning() -> None:
            summary = f"{len(report.errors)} obligation gap(s): " + "; ".join(report.errors[:3])
            await log_cb(
                "proj",
                f"[custom] Phase 3 doc validation: {summary}",
                "warning",
            )

        asyncio.run(emit_warning())

        assert any(
            level == "warning" and "obligation gap" in message
            for level, message in logs
        )
        assert not any(level == "error" for level, _ in logs)


def test_sql_usage_extractor_picks_up_lobster_extra_query_params() -> None:
    """The lobster stores SQL in `extra_*` params, not `content` params.

    Before: extract_sql_usage only looked at params named "content",
    so lobster scans produced 0 records → empty sql_usage.json →
    SQL gate failure. After: extract_sql_usage iterates all params
    with a filter (key contains query/sql/statement OR value matches
    SQL keyword regex). Lobster's `extra_response.query` convention
    is now caught.
    """
    from mcp_server.providers.custom.sql_usage import extract_sql_usage

    scan_result = {
        "platform": "lobster",
        "jobs": [
            {
                "name": "API_Orders",
                "path": "API_Orders.xml",
                "group": "Alis",
                "components": [
                    {
                        "unique_name": "response_1",
                        "params": [
                            {
                                "name": "extra_agent.sql.timeout",
                                "value": "0",
                            },
                            {
                                "name": "extra_response.query",
                                "value": "SELECT obj_id FROM xml_datawizard_profiles limit 1;",
                            },
                            {
                                "name": "url",
                                "value": "[URL_REDACTED]",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    records = extract_sql_usage(scan_result)
    assert records, "expected at least one record from extra_response.query"
    assert any("xml_datawizard_profiles" in str(r.get("tables", [])) for r in records)
    short_value_param = next(
        (r for r in records if r.get("unit") and "0" in str(r.get("sql_excerpt", ""))[:5]),
        None,
    )
    assert short_value_param is None, "short config values (e.g., '0') must be filtered out"


def test_phase3_sql_usage_gate_is_warning_not_runtimeerror() -> None:
    """The SQL usage gate failure is a `warning` log, not a RuntimeError.

    Before: validate_sql_usage_publication errors → raise RuntimeError
    → run stops at Phase 3 → Phase 4 boundary. After: errors →
    log_cb warning → run continues.
    """

    errors = [
        "engineering SQL/query facts exist but no structured SQL usage records were produced"
    ]

    logs: list[tuple[str, str]] = []

    async def log_cb(pid: str, message: str, level: str = "info") -> None:
        logs.append((level, message))

    async def emit_warning() -> None:
        if errors:
            summary = f"{len(errors)} SQL usage gate finding(s): " + "; ".join(errors[:3])
            await log_cb("proj", f"[custom] Phase 3 SQL usage gate: {summary}", "warning")
        else:
            await log_cb("proj", "[custom] Phase 3 factual gates passed", "info")

    asyncio.run(emit_warning())

    assert any(
        level == "warning" and "SQL usage gate" in message
        for level, message in logs
    )


# ---------------------------------------------------------------------------
# Slice 3: targeted dispatch + waste removal
# ---------------------------------------------------------------------------

async def _noop_log_cb(pid: str, message: str, level: str = "info") -> None:
    pass


@pytest.mark.asyncio
async def test_complete_core_generation_tool_path_injects_starter_pack(tmp_path, monkeypatch):
    """Tool-capable Phase 3: doc pack <= 100 KB is injected into system_prompt.

    First-pass phase3 is single-shot by default; opt into the tool loop to exercise
    the system_prompt injection path that only runs when tools are enabled.
    """
    from mcp_server.config import config
    monkeypatch.setattr(config, "agent_phase3_first_pass_tools", True)
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    doc_pack = staging / "_analysis" / "_doc_pack_01_ARCHITECTURE_MAP.md"
    doc_pack.write_text("### GROUP_SUMMARY\nSummary content.\n", encoding="utf-8")
    # Non-pack file that should NOT reach the model.
    other = staging / "other_evidence.md"
    other.write_text("# Other\nIrrelevant content.\n", encoding="utf-8")

    received_system_prompts: list[str | None] = []

    class _ToolProvider:
        model_name = "fake/tool"
        provider_name = "openai"
        supports_tools = True

        async def complete(self, *args, **kwargs):
            raise AssertionError("should use complete_with_tools")

        async def complete_with_tools(self, prompt, staging_dir, options=None, max_turns=30, scoped_tools=None):
            received_system_prompts.append(options.system_prompt if options else None)
            return ModelResult(
                success=True,
                text="# Architecture Map\n\nContent with sufficient detail for validation here.",
                model_name=self.model_name,
                input_tokens=10,
                output_tokens=5,
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
        effective_model="fake/tool",
        write_model="fake/tool",
        platform="generic",
        analysis_guide_file="",
    )

    provider = _ToolProvider()
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase3",
        target="01_ARCHITECTURE_MAP",
        prompt="Write the architecture map.",
        context_files=[doc_pack, other],
        opts=ModelOptions(),
        log_cb=_noop_log_cb,
    )

    assert result.success
    assert received_system_prompts, "complete_with_tools was not called"
    system_prompt = received_system_prompts[0] or ""
    assert "GROUP_SUMMARY" in system_prompt, "doc pack content should be in system_prompt"
    assert "Irrelevant" not in system_prompt, "non-pack file should not appear in system_prompt"


@pytest.mark.asyncio
async def test_complete_core_generation_tool_path_hint_only_when_no_doc_pack(tmp_path):
    """Tool-capable Phase 2: no _doc_pack_ files → hint-only (no system_prompt injection)."""
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    # Phase 2 context: no _doc_pack_ file
    analysis_file = staging / "analysis_A.md"
    analysis_file.write_text("Phase 2 evidence.\n", encoding="utf-8")

    received: list[dict] = []

    class _ToolProvider:
        model_name = "fake/tool"
        provider_name = "openai"
        supports_tools = True

        async def complete(self, *args, **kwargs):
            raise AssertionError("should use complete_with_tools")

        async def complete_with_tools(self, prompt, staging_dir, options=None, max_turns=30, scoped_tools=None):
            received.append({"system_prompt": options.system_prompt if options else None})
            return ModelResult(
                success=True,
                text="## Analysis\n\nUnit A analysis with sufficient content here.",
                model_name=self.model_name,
                input_tokens=5,
                output_tokens=3,
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
        effective_model="fake/tool",
        write_model="fake/tool",
        platform="generic",
        analysis_guide_file="",
    )

    provider = _ToolProvider()
    result = await CustomHarnessProvider(provider)._complete_core_generation(
        ctx,
        HarnessRunState(project_id="proj", facts_manifest=facts),
        provider=provider,
        phase="phase2.deep_dive",
        target="A",
        prompt="Analyze unit A.",
        context_files=[analysis_file],
        opts=ModelOptions(),
        log_cb=_noop_log_cb,
    )

    assert result.success
    assert received, "complete_with_tools was not called"
    # No doc pack file → system_prompt should not contain any injected pack content
    system_prompt = received[0]["system_prompt"] or ""
    assert "Phase 2 evidence" not in system_prompt


def test_phase3_context_files_excludes_manifest(tmp_path):
    """_phase3_context_files must never return _doc_pack_manifest.json."""
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_assessment" / "prompt_packs").mkdir(parents=True)
    (staging / "_preprocess").mkdir()
    # Create the manifest — it should be excluded.
    manifest = staging / "_analysis" / "_doc_pack_manifest.json"
    manifest.write_text('{"doc_packs": {}}', encoding="utf-8")
    # Create a doc pack so the method has something to return.
    doc_pack = staging / "_analysis" / "_doc_pack_01_ARCHITECTURE_MAP.md"
    doc_pack.write_text("### GROUP_SUMMARY\nEvidence.\n", encoding="utf-8")

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
        platform="generic",
        analysis_guide_file="",
    )
    files = CustomHarnessProvider(
        OpenAICompatibleProvider("fake", api_key="test", base_url="http://example.test/v1")
    )._phase3_context_files(ctx, "01_ARCHITECTURE_MAP")

    names = [f.name for f in files]
    assert "_doc_pack_manifest.json" not in names


def test_engineering_fact_context_excludes_csv_indexes(tmp_path):
    """_engineering_fact_context must not return entity_index.csv or usage_index.csv."""
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    # Create all potential files including the CSVs that should be excluded.
    for name in ["engineering_facts.md", "sql_usage.md", "entity_index.csv", "usage_index.csv",
                 "architecture_map.md", "dependency_map.md", "data_flow_map.md"]:
        (staging / name).write_text("content\n", encoding="utf-8")

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
        platform="generic",
        analysis_guide_file="",
    )

    for doc_name in ["01_ARCHITECTURE_MAP", "02_COMPONENT_INVENTORY", "03_DATA_FLOWS_AND_LINEAGE",
                     "04_BUSINESS_RULES_AND_TRANSFORMATIONS"]:
        files = CustomHarnessProvider._engineering_fact_context(ctx, doc_name)
        names = [f.name for f in files]
        assert "entity_index.csv" not in names, f"entity_index.csv present for {doc_name}"
        assert "usage_index.csv" not in names, f"usage_index.csv present for {doc_name}"

    # Verify the method still returns the expected markdown files.
    files_01 = CustomHarnessProvider._engineering_fact_context(ctx, "01_ARCHITECTURE_MAP")
    names_01 = [f.name for f in files_01]
    assert "engineering_facts.md" in names_01
    assert "architecture_map.md" in names_01


# --- Observability: cache-hit parsing + always-on per-phase token ledger ---


def test_cache_hit_tokens_across_provider_conventions():
    from mcp_server.providers.custom.providers import _cache_hit_tokens
    assert _cache_hit_tokens({"prompt_cache_hit_tokens": 2945024}) == 2945024  # deepseek
    assert _cache_hit_tokens({"cache_read_input_tokens": 6656}) == 6656        # anthropic
    assert _cache_hit_tokens({"prompt_tokens_details": {"cached_tokens": 1800}}) == 1800  # openai
    assert _cache_hit_tokens({"prompt_tokens": 5000}) == 0                     # no cache reported


def test_phase_costs_written_always_on_with_cache_split(tmp_path):
    from mcp_server.providers.custom.models import ProviderCallLog
    from mcp_server.providers.custom.providers import OpenAICompatibleProvider

    provider = CustomHarnessProvider(
        OpenAICompatibleProvider("deepseek", api_key="x", base_url="http://e/v1")
    )
    facts = build_facts_manifest_from_units("proj", ["Unit A"])
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    state.calls.extend([
        ProviderCallLog(phase="phase2", target="A", result=ModelResult(
            success=True, input_tokens=100_000, cache_read_tokens=40_000,
            output_tokens=5_000, latency_ms=12_000)),
        ProviderCallLog(phase="phase3", target="01_DOC", result=ModelResult(
            success=True, input_tokens=1_200_000, cache_read_tokens=900_000,
            output_tokens=8_000, latency_ms=600_000)),
    ])

    provider._write_phase_costs(tmp_path, state, log_cb=MagicMock())

    report = json.loads((tmp_path / "_assessment" / "phase_costs.json").read_text())
    # Always-on (no MCP_PIPELINE_DEBUG), with by_phase + totals.
    assert (tmp_path / "_assessment" / "phase_costs.json").exists()
    p3 = report["by_phase"]["phase3"]
    assert p3["input_tokens"] == 1_200_000
    assert p3["input_cache_read_tokens"] == 900_000
    assert p3["input_cache_miss_tokens"] == 300_000  # input - cache_read = the full-price part
    assert report["totals"]["input_tokens"] == 1_300_000
    assert report["totals"]["input_cache_miss_tokens"] == 360_000


def test_response_snapshot_writes_usage_always_on(tmp_path):
    CustomHarnessProvider._write_model_response_snapshot(
        tmp_path,
        tmp_path / "_debug",
        ModelResult(success=True, input_tokens=80_000, cache_read_tokens=30_000,
                    output_tokens=4_000, latency_ms=9_000),
    )
    usage = json.loads((tmp_path / "usage.json").read_text())
    assert usage["input_tokens"] == 80_000
    assert usage["input_cache_read_tokens"] == 30_000
    assert usage["input_cache_miss_tokens"] == 50_000
    assert not (tmp_path / "_debug" / "response.json").exists()


# --------------------------------------------------------------------------- #
# Composite partition runs — cross-project refs staging
# --------------------------------------------------------------------------- #

def _partition_stub(name: str):
    from mcp_server.providers.custom.partition_planner import Partition

    return Partition(
        name=name, source_roots=(f"B/{name}.__intodoc_zip__/{name.upper()}",),
        unit_names=("u1",), unit_count=1, groups=("G",), group_count=1,
        call_budget=24, budget_fits=True,
    )


def test_materialize_cross_project_refs_stages_md_and_index(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mcp_server.config import config

    monkeypatch.setattr(config, "project_root", tmp_path)
    index = {
        "subprojects": {"Alpha": {}, "Beta": {}},
        "call_edges": [{
            "from_subproject": "Alpha", "to_subproject": "Beta",
            "caller": "A_Main", "callee": "B_Worker", "resolution": "resolved",
        }],
        "shared_systems": {},
        "shared_landscape_systems": {},
        "application_edges": [],
    }
    topo_dir = tmp_path / "output" / "proj" / "topology"
    topo_dir.mkdir(parents=True)
    (topo_dir / "cross_project_index.json").write_text(json.dumps(index), encoding="utf-8")

    staging = tmp_path / "staging"
    staging.mkdir()
    ctx = SimpleNamespace(project_id="proj", staging_dir=staging, partition=_partition_stub("Alpha"))
    state = SimpleNamespace(engineering_fact_artifacts=[])

    CustomHarnessProvider._materialize_cross_project_refs(ctx, state)

    refs = staging / "cross_project_refs.md"
    assert refs.exists()
    assert "B_Worker" in refs.read_text(encoding="utf-8")
    assert "cross_project_refs.md" in state.engineering_fact_artifacts
    # Index copied for the business-topology starter pack.
    assert (staging / "_topology" / "cross_project_index.json").exists()


def test_materialize_cross_project_refs_missing_index_is_noop(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mcp_server.config import config

    monkeypatch.setattr(config, "project_root", tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()
    ctx = SimpleNamespace(project_id="proj", staging_dir=staging, partition=_partition_stub("Alpha"))
    state = SimpleNamespace(engineering_fact_artifacts=[])

    CustomHarnessProvider._materialize_cross_project_refs(ctx, state)

    assert not (staging / "cross_project_refs.md").exists()
    assert state.engineering_fact_artifacts == []
