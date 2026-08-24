"""The one pack format: a ``plugin.yaml`` that changes phases, attention and output.

The pack under test is the SQL-injection review the whole design was argued
from: it wants four questions of its own, one report, and none of the built-in
Talend analysis. It exercises every lever in one file -- ``phases``, ``lens``
(focus, sections, requirements), ``docs`` with routing, and ``audience``.

Two properties get the most attention here because they were the two real
defects:

* ``mode: replace`` must actually replace. Not just at the document level (packs
  always replaced documents) but at the ANALYSIS level -- the prompt must ask
  for the pack's four sections and none of the built-in sixteen. A green
  "2 docs, 4 sections" is a false pass unless nothing was stubbed, so the
  anti-stub assertions are mandatory.
* an unanswered declared section must NOT fail anything. A security pack
  pointed at code with no dynamic SQL should come back empty for that section;
  that is a correct result. It is recorded for the author and published clean.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import mcp_server.providers.custom.harness as harness_module
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import HarnessRunState, ModelResult
from mcp_server.providers.custom.pack_contract import (
    PackContract,
    PackSectionSpec,
    contract_from_plugin_manifest,
    default_contract,
)
from mcp_server.providers.custom.analysis_parser import parse_phase2_sections
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES
from mcp_server.providers.custom.prompts import group_analysis_prompt
from mcp_server.providers.custom.repair_planner import deterministic_phase2_section_patch
from mcp_server.providers.custom.validators import (
    build_facts_manifest,
    missing_required_analysis_sections,
)
from mcp_server.providers.custom.phase2_claims import plan_claim_tasks, claim_task_prompt
from mcp_server.services.doc_quality_scorer import score_project
from mcp_server.services.plugin_schema import (
    FOCUS_EXCLUDE_VOCABULARY,
    FOCUS_INCLUDE_VOCABULARY,
    load_plugin_manifest,
)

REPO_MCP_SERVER = Path(__file__).resolve().parents[2]

# One pack format. No schema_version, no layer, no governance block -- a git
# plugin.yaml with the attention block folded in.
SQL_INJECTION_PACK_YAML = """\
name: sql-injection-review
version: 0.3.0
description: Find and rank SQL injection surfaces.

phases:
  investigation: skip
  analysis: bounded
  scorecard: skip
  doc_bundles: run

lens:
  focus:
    include: [sql_queries, orm_entities, db_connections, config_references, routes]
    exclude: [binary, static_assets, generated_files]
  sections:
    mode: replace
    declare:
      - id: INJECTION_SINKS
        question: "every place user-controlled data reaches a query; name the unit and quote the construction site"
      - id: PARAMETERIZATION
        question: "parameterized vs concatenated, verbatim, per sink"
      - id: TAINT_PATHS
        question: "source to sink, naming each hop"
      - id: EXPLOITABILITY
        question: "is the sink reachable from an external entrypoint"
  requirements: |
    Required evidence: query construction sites, string concatenation into SQL,
    dynamic table/column names, ORM raw-query escapes, input validation at entry.

docs:
  - name: INJECTION_FINDINGS
    sections: [Summary, Findings by Severity, Taint Paths, Evidence Register]
    phase2_sections: [INJECTION_SINKS, TAINT_PATHS, EXPLOITABILITY]
  - name: REMEDIATION_BACKLOG
    per_unit: true
    sections: [Sink, Current Code, Proposed Fix, Effort]

audience:
  roles: [security reviewer, tech lead]
  technical_depth: deep

quality_gates:
  mode: advisory
  required_terms: [file, query, parameter]
"""

PACK_SECTIONS = ("INJECTION_SINKS", "PARAMETERIZATION", "TAINT_PATHS", "EXPLOITABILITY")


def _write_scan(out_dir: Path, *, platform: str, jobs: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scan_result.json").write_text(json.dumps({
        "platform": platform,
        "repo_root": "/repo",
        "files": [],
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


def _pack_dir(tmp_path: Path, yaml_text: str = SQL_INJECTION_PACK_YAML, name: str = "sql_pack") -> Path:
    pack_dir = tmp_path / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "plugin.yaml").write_text(yaml_text, encoding="utf-8")
    return pack_dir


def _sql_pack_contract(tmp_path: Path, platform: str = "talend") -> PackContract:
    return contract_from_plugin_manifest(
        load_plugin_manifest(_pack_dir(tmp_path)), platform
    )


class _SqlPackProvider:
    """Deterministic model provider for the SQL-injection pack run.

    ``omit_sections`` simulates a model that never answers those sections: no
    claim is emitted for them, which is the realistic shape of "the evidence
    had nothing for this question" on the claims path.
    """

    model_name = "fake/model"

    def __init__(self, omit_sections: set[str] | None = None) -> None:
        self.calls: list[dict] = []
        self.omit_sections = omit_sections or set()

    async def complete(self, prompt: str, context_files: list[Path] | None = None, options=None):
        self.calls.append({
            "prompt": prompt,
            "context_names": [p.name for p in context_files or []],
            "system_prompt": getattr(options, "system_prompt", "") or "",
        })
        if '"schema_version": "phase2-claims-v1"' in prompt:
            return self._claims(prompt)
        return self._doc(prompt)

    def _claims(self, prompt: str) -> ModelResult:
        def _value(name: str) -> str:
            match = re.search(rf'"{name}":\s*("(?:[^"\\]|\\.)*")', prompt)
            return json.loads(match.group(1)) if match else "unknown"

        units_block = prompt.split("ASSIGNED CANONICAL UNITS", 1)[1].split("ALLOWED SECTIONS", 1)[0]
        units = [line[2:].strip() for line in units_block.splitlines() if line.startswith("- ")]
        sections_block = prompt.split("ALLOWED SECTIONS", 1)[1].split("\nRULES\n", 1)[0]
        sections = [line[2:].strip() for line in sections_block.splitlines() if line.startswith("- ")]
        claims = [
            {
                "section": section,
                "kind": "fact",
                "unit_names": units[:1],
                "related_units": [],
                "statement": f"Evidenced finding for {section} in the scanned source file.",
                "evidence_refs": ["_preprocess/Orders.md"],
                "confidence": "supported",
                "claim_id": "",
            }
            for section in sections
            if section not in self.omit_sections
        ]
        return ModelResult(
            success=True,
            text=json.dumps({
                "schema_version": "phase2-claims-v1",
                "group": _value("group"),
                "task_id": _value("task_id"),
                "concern": _value("concern"),
                "claims": claims,
            }),
            model_name=self.model_name,
            input_tokens=10,
            output_tokens=100,
        )

    def _doc(self, prompt: str) -> ModelResult:
        body = (
            "# SQL Injection Review\n\n"
            "## Summary\n\nEvidenced review of the scanned source file DEU_LoadOrders. "
            "Every query parameter and file reference below is evidence-backed.\n\n"
            "## Findings by Severity\n\nHIGH: one concatenated query built from a request "
            "parameter in DEU_LoadOrders.\n\n"
            "## Taint Paths\n\nRequest parameter to query string in the same file.\n\n"
            "## Sink\n\nQuery construction site in DEU_LoadOrders.\n\n"
            "## Current Code\n\nThe query concatenates a parameter value.\n\n"
            "## Proposed Fix\n\nUse a parameterized query.\n\n"
            "## Effort\n\nSmall.\n\n"
            "## Evidence Register\n\n"
            "| # | Claim | Source | Confidence |\n"
            "|---|-------|--------|------------|\n"
            "| 1 | Concatenated query parameter | DEU_LoadOrders.item | HIGH |\n\n"
        )
        body += "Evidence detail for DEU_LoadOrders: file, query, parameter. " * 30
        return ModelResult(
            success=True,
            text=body,
            model_name=self.model_name,
            input_tokens=10,
            output_tokens=800,
            metadata={"finish_reason": "stop"},
        )


def _pack_ctx(tmp_path: Path, project_id: str, *, guide: bool) -> tuple[DocGenContext, Path]:
    staging = tmp_path / "output" / project_id / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    (staging / "_assessment").mkdir()
    # Above the 500-byte thin-bundle floor AND min_investigation_content_bytes.
    (staging / "_preprocess" / "Orders.md").write_text(
        "## Job: DEU_LoadOrders\n\n"
        + "**QUERY**: `SELECT * FROM orders WHERE id = ' + context.order_id + '` "
        + "query construction evidence for DEU_LoadOrders. " * 20,
        encoding="utf-8",
    )
    if guide:
        # The built-in Talend guide is deliberately present. Nexus must always
        # promote it to immutable system policy, including for replace lenses.
        (staging / "_preprocess" / "_analysis_guide.md").write_text(
            "### COMPONENTS\nlegacy\n### EXECUTION_FLOW\nlegacy\n", encoding="utf-8"
        )
    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Orders"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md" if guide else None,
        enterprise_mode=False,
    )
    return ctx, staging


# ---------------------------------------------------------------------------
# The pack actually replaces the analysis, and nothing gets stubbed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_injection_pack_produces_pack_shape_without_stubs(tmp_path, monkeypatch):
    from mcp_server.config import config

    project_id = "sqlproj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="talend", jobs=[_job("DEU_LoadOrders", "Orders")])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    monkeypatch.setattr(config, "model_pricing_json", '{"fake/model":{"input":1,"output":2}}')

    ctx, staging = _pack_ctx(tmp_path, project_id, guide=True)

    logs: list[tuple] = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append((pid, message, level))

    captured: dict[str, HarnessRunState] = {}
    _orig_state = harness_module.HarnessRunState

    def _capture_state(**kwargs):
        state = _orig_state(**kwargs)
        captured["state"] = state
        return state

    monkeypatch.setattr(harness_module, "HarnessRunState", _capture_state)

    harness = CustomHarnessProvider(_SqlPackProvider(), plugin_pack_dir=_pack_dir(tmp_path))

    assert harness.pack_contract.mode == "replace"
    assert harness.pack_contract.analysis_section_names == PACK_SECTIONS

    result = await harness.run(ctx, log_cb)
    assert result.success, result.error

    phase2_calls = [
        call for call in harness.model_provider.calls
        if '"schema_version": "phase2-claims-v1"' in call["prompt"]
    ]
    assert phase2_calls
    assert all(
        "Nexus Platform Analysis Policy (Immutable)" in call["system_prompt"]
        for call in phase2_calls
    )

    # Exactly 2 documents: the declared doc plus the per-unit expansion of the
    # second declared doc for the fixture's single unit.
    published = sorted(p.name for p in staging.glob("*.md"))
    assert published == [
        "INJECTION_FINDINGS.md",
        "REMEDIATION_BACKLOG__Orders__DEU_LoadOrders.md",
    ]

    # Exactly the 4 declared Phase 2 sections plus the protected floor; none of
    # the built-in 16 in the published analysis.
    support = staging / "_rag_support" / "phase2_analysis" / "Orders.md"
    assert support.exists()
    analysis_text = support.read_text(encoding="utf-8")
    for section in PACK_SECTIONS:
        assert f"### {section}" in analysis_text
    assert "### GROUP_SUMMARY" in analysis_text
    for legacy in ANALYSIS_SECTION_NAMES:
        assert f"### {legacy}" not in analysis_text
    assert "analysis task did not complete" not in analysis_text
    assert "PARTIAL" not in analysis_text

    # Anti-stub: without these, "4 sections, 2 docs" is a false pass.
    state = captured["state"]
    assert state.partial_groups == []
    assert state.thin_bundle_groups == []
    failed_docs = staging / "_assessment" / "failed_docs.json"
    if failed_docs.exists():
        payload = json.loads(failed_docs.read_text(encoding="utf-8"))
        assert not payload, f"stubbed documents recorded: {payload}"
    assert not any("marking PARTIAL" in message for _, message, _ in logs)


# ---------------------------------------------------------------------------
# No loud fails: an unanswered declared section publishes clean
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unanswered_declared_section_publishes_clean(tmp_path, monkeypatch):
    """The evidence has nothing for TAINT_PATHS. That is a result, not a fault.

    It must be recorded for the pack author, and it must not: annotate the
    document, warn the run, penalise the score, or stub the group.
    """
    from mcp_server.config import config

    project_id = "sqlgap"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="talend", jobs=[_job("DEU_LoadOrders", "Orders")])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    monkeypatch.setattr(config, "model_pricing_json", '{"fake/model":{"input":1,"output":2}}')

    ctx, staging = _pack_ctx(tmp_path, project_id, guide=False)

    logs: list[tuple] = []

    async def log_cb(pid: str, message: str, level: str = "info"):
        logs.append((pid, message, level))

    captured: dict[str, HarnessRunState] = {}
    _orig_state = harness_module.HarnessRunState

    def _capture_state(**kwargs):
        state = _orig_state(**kwargs)
        captured["state"] = state
        return state

    monkeypatch.setattr(harness_module, "HarnessRunState", _capture_state)

    harness = CustomHarnessProvider(
        _SqlPackProvider(omit_sections={"TAINT_PATHS"}),
        plugin_pack_dir=_pack_dir(tmp_path),
    )
    result = await harness.run(ctx, log_cb)
    assert result.success, result.error
    state = captured["state"]

    # Recorded for the author, with the question that went unanswered.
    coverage_path = staging / "_assessment" / "pack_section_coverage.json"
    assert coverage_path.exists()
    records = json.loads(coverage_path.read_text(encoding="utf-8"))
    gap = next(r for r in records if "TAINT_PATHS" in r["missing_sections"])
    assert gap["pack_id"] == "sql-injection-review"
    assert gap["group"] == "Orders"
    assert gap["questions"]["TAINT_PATHS"] == "source to sink, naming each hop"

    # Quiet: no notice in the documents.
    findings = (staging / "INJECTION_FINDINGS.md").read_text(encoding="utf-8")
    assert "Contract Violation" not in findings
    assert not findings.startswith(">")

    # Quiet: the run is plain success, with no pack-section warning.
    assert not any(
        w.code == "PACK_SECTION_CONTRACT" for w in state.warning_collector.warnings
    )
    assert not any("TAINT_PATHS" in w for w in state.warnings)

    # Quiet: nothing stubbed, real analysis kept for the answered sections.
    assert state.partial_groups == []
    assert not any("marking PARTIAL" in message for _, message, _ in logs)
    analysis_text = (
        staging / "_rag_support" / "phase2_analysis" / "Orders.md"
    ).read_text(encoding="utf-8")
    for section in ("INJECTION_SINKS", "PARAMETERIZATION", "EXPLOITABILITY"):
        assert f"### {section}" in analysis_text

    # Quiet: no quality penalty for the gap.
    report = score_project(project_id, docs_dir=staging)
    for doc in report.docs:
        assert "missing_declared_sections" not in doc.flags


@pytest.mark.asyncio
async def test_pack_section_miss_never_raises_but_builtin_contract_still_does(
    tmp_path, monkeypatch
):
    """``hard_required_names`` is the split: a pack's own sections are recorded,
    the built-in Talend contract keeps exactly the enforcement it always had."""
    from mcp_server.config import config
    from mcp_server.providers.custom.models import ModelOptions

    project_id = "partproj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="talend", jobs=[_job("DEU_LoadOrders", "Orders")])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    facts = build_facts_manifest(project_id, scan_dir)

    staging = tmp_path / "staging"
    (staging / "_assessment").mkdir(parents=True)
    ctx = DocGenContext(
        project_id=project_id,
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Orders"],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file=None,
        enterprise_mode=False,
    )

    async def log_cb(pid: str, message: str, level: str = "info"):
        return None

    opts = ModelOptions()
    body = (
        "### INJECTION_SINKS\nsink\n\n### PARAMETERIZATION\nparams\n\n"
        "### EXPLOITABILITY\nreach\n"
    )

    pack_harness = CustomHarnessProvider(
        _SqlPackProvider(), plugin_pack_dir=_pack_dir(tmp_path)
    )
    pack_state = harness_module.HarnessRunState(project_id=project_id, facts_manifest=facts)
    text = await pack_harness._complete_phase2_sections(
        ctx, pack_state, "Orders", body, [], [], staging / "Orders.md", opts, log_cb
    )
    # Real analysis returned (floor patched deterministically), gap recorded.
    assert "### INJECTION_SINKS" in text and "### GROUP_SUMMARY" in text
    assert pack_state.section_coverage_gaps[0]["missing_sections"] == ["TAINT_PATHS"]
    assert (staging / "_assessment" / "pack_section_coverage.json").exists()

    # No pack: unchanged raise on Talend.
    default_harness = CustomHarnessProvider(_SqlPackProvider())
    default_state = harness_module.HarnessRunState(project_id=project_id, facts_manifest=facts)
    with pytest.raises(RuntimeError, match="missing required sections"):
        await default_harness._complete_phase2_sections(
            ctx, default_state, "Orders", body, [], [], staging / "Orders.md", opts, log_cb
        )
    assert default_state.section_coverage_gaps == []


# ---------------------------------------------------------------------------
# The prompt asks the pack's questions, not the built-in ones
# ---------------------------------------------------------------------------


def test_phase2_prompt_asks_for_declared_sections(tmp_path, monkeypatch):
    from mcp_server.config import config

    project_id = "promptproj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="talend", jobs=[_job("DEU_LoadOrders", "Orders")])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    facts = build_facts_manifest(project_id, scan_dir)

    prompt = group_analysis_prompt(
        facts,
        "Orders",
        contract=_sql_pack_contract(tmp_path),
        required_docs=["INJECTION_FINDINGS", "REMEDIATION_BACKLOG"],
    )

    for section in PACK_SECTIONS:
        assert f"`### {section}`" in prompt
    assert "### INJECTION_SINKS" in prompt  # skeleton stub
    # The question is what makes the section more than a heading.
    assert "name the unit and quote the construction site" in prompt
    # And NONE of the built-in 16, prompt-asset layer included.
    for legacy in ANALYSIS_SECTION_NAMES:
        assert f"### {legacy}" not in prompt, f"built-in section {legacy} leaked in"
    # lens.requirements is additive to the platform evidence contract.
    assert "Talend-specific required evidence" in prompt
    assert "ADDITIONAL PLUGIN ANALYSIS REQUIREMENTS" in prompt
    assert "query construction sites" in prompt
    assert "full platform analysis guide" not in prompt
    # The protected floor is still demanded, exactly once.
    assert "`### GROUP_SUMMARY`" in prompt


def test_claim_prompt_uses_declared_sections(tmp_path, monkeypatch):
    from mcp_server.config import config
    from mcp_server.providers.custom.evidence_tasks import plan_evidence_tasks

    project_id = "claimproj"
    scan_dir = tmp_path / "intodoc" / project_id
    _write_scan(scan_dir, platform="talend", jobs=[_job("DEU_LoadOrders", "Orders")])
    monkeypatch.setattr(config, "intodoc_output_dir", tmp_path / "intodoc")
    facts = build_facts_manifest(project_id, scan_dir)

    group_file = tmp_path / "Orders.md"
    group_file.write_text("## Job: DEU_LoadOrders\n\nevidence\n", encoding="utf-8")
    tasks = plan_evidence_tasks("Orders", group_file, ["DEU_LoadOrders"], tmp_path / "_analysis", 48000)
    contract = _sql_pack_contract(tmp_path)
    specs = plan_claim_tasks(
        tasks[0], evidence_bytes=100, sections=contract.analysis_section_names
    )
    assert specs[0].sections == PACK_SECTIONS
    prompt = claim_task_prompt(
        facts=facts,
        group="Orders",
        spec=specs[0],
        contract=contract,
    )
    for section in PACK_SECTIONS:
        assert f"- {section}" in prompt
    for legacy in ANALYSIS_SECTION_NAMES:
        assert f"- {legacy}" not in prompt
    assert "ADDITIONAL PLUGIN ANALYSIS GUIDANCE" in prompt
    assert "query construction sites" in prompt


def test_replace_pack_cannot_suppress_platform_guide_context(tmp_path):
    staging = tmp_path / "staging"
    preprocess = staging / "_preprocess"
    preprocess.mkdir(parents=True)
    guide = preprocess / "_analysis_guide.md"
    guide.write_text("immutable Talend analysis semantics", encoding="utf-8")
    facts_path = staging / "facts_manifest.json"
    facts_path.write_text('{"project_id": "p"}', encoding="utf-8")

    harness = CustomHarnessProvider(
        _SqlPackProvider(), plugin_pack_dir=_pack_dir(tmp_path)
    )
    ctx = DocGenContext(
        project_id="p",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=[],
        total_jobs=1,
        total_groups=1,
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )

    assert guide in harness._phase2_shared_context(ctx, facts_path)


# ---------------------------------------------------------------------------
# Compilation, backward compatibility, and the protected floor
# ---------------------------------------------------------------------------


def test_pack_without_a_lens_behaves_exactly_as_before(tmp_path):
    """Every pack authored before the lens existed must keep working unchanged:
    extend mode, nothing section-enforced, its own documents."""
    manifest = load_plugin_manifest(
        _pack_dir(
            tmp_path,
            "name: legacy-pack\nversion: '1.0.0'\n"
            "docs:\n  - name: SUMMARY\n"
            "steps:\n  - id: run\n    type: harness\n",
            name="legacy",
        )
    )
    contract = contract_from_plugin_manifest(manifest, "talend")
    assert contract.is_pack
    assert contract.mode == "extend"
    assert contract.required_names == ()
    assert contract.hard_required_names == ()
    assert contract.declared_sections == ()
    assert contract.doc_names() == ("SUMMARY",)


def test_manifest_without_steps_defaults_to_the_documentation_pipeline(tmp_path):
    """`steps` was boilerplate that only ever produced registration failures."""
    manifest = load_plugin_manifest(
        _pack_dir(tmp_path, "name: p\nversion: '1.0'\n", name="nosteps")
    )
    assert [(s.id, s.type) for s in manifest.steps] == [("generate-docs", "harness")]


@pytest.mark.parametrize(
    "relative_path",
    [
        "prompts/phase2_platform_sections/talend.md",
        "prompts/phase2_platform_sections.md",
    ],
)
def test_manifest_rejects_platform_analysis_replacement_files(tmp_path, relative_path):
    pack = _pack_dir(tmp_path, "name: p\nversion: '1.0'\n", name="immutable")
    forbidden = pack / relative_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("replace Nexus guidance", encoding="utf-8")

    with pytest.raises(ValueError, match="platform analysis prompts are immutable"):
        load_plugin_manifest(pack)


def test_manifest_allows_additive_platform_extension(tmp_path):
    pack = _pack_dir(tmp_path, "name: p\nversion: '1.0'\n", name="extension")
    extension = pack / "prompts" / "extensions" / "platforms" / "talend.md"
    extension.parent.mkdir(parents=True)
    extension.write_text("Inspect the project control table.", encoding="utf-8")

    assert load_plugin_manifest(pack).name == "p"


def test_declared_lens_defaults_analysis_on(tmp_path):
    """Sections nobody asks for are sections nobody answers."""
    manifest = load_plugin_manifest(
        _pack_dir(
            tmp_path,
            "name: p\nversion: '1.0'\n"
            "lens:\n  sections:\n    mode: replace\n    declare:\n      - id: FINDINGS\n",
            name="lensonly",
        )
    )
    assert manifest.phases is not None
    assert manifest.phases.policy_for("analysis") == "bounded"


def test_full_pack_compiles_every_lever(tmp_path):
    contract = _sql_pack_contract(tmp_path)
    assert contract.mode == "replace"
    assert contract.analysis_section_names == PACK_SECTIONS
    assert contract.doc_names() == ("INJECTION_FINDINGS", "REMEDIATION_BACKLOG")
    assert contract.doc_spec("INJECTION_FINDINGS").phase2_sections == (
        "INJECTION_SINKS",
        "TAINT_PATHS",
        "EXPLOITABILITY",
    )
    assert contract.doc_spec("REMEDIATION_BACKLOG").per_unit is True
    assert "sql_queries" in contract.focus_include
    assert "binary" in contract.focus_exclude
    assert contract.audience is not None
    assert contract.audience.technical_depth == "deep"
    assert contract.audience.roles == ("security reviewer", "tech lead")
    # A replace pack asked for exactly its own sections; the built-in
    # relationship rollups are not among them.
    assert contract.relationship_section_names == ()


def test_parser_recognises_only_declared_sections_in_replace_mode(tmp_path):
    contract = _sql_pack_contract(tmp_path)
    parsed = parse_phase2_sections(
        "### INJECTION_SINKS\nsink evidence\n\n"
        "### COMPONENTS\nlegacy content\n\n"
        "### GROUP_SUMMARY\nsummary\n",
        contract=contract,
    )
    assert "INJECTION_SINKS" in parsed
    assert "GROUP_SUMMARY" in parsed
    # This is the union operator that made packs additive. It must be gone.
    assert "COMPONENTS" not in parsed
    assert "legacy content" in parsed["UNCLASSIFIED_OR_EXTRA_FINDINGS"]


def test_satisfied_replace_pack_can_never_reach_the_stub_chain(tmp_path):
    contract = _sql_pack_contract(tmp_path, platform="talend")
    body = "\n\n".join(f"### {s}\ncontent for {s}" for s in PACK_SECTIONS)
    # GROUP_SUMMARY deliberately missing: it is the floor and must be patchable.
    patched, missing = deterministic_phase2_section_patch(
        body, platform="talend", group="Orders", contract=contract
    )
    assert missing == ["GROUP_SUMMARY"]
    assert missing_required_analysis_sections(patched, "talend", contract=contract) == []


def test_default_contract_still_enforces_builtin_talend_sections():
    """Regression guard: no-pack Talend behaviour is untouched by any of this."""
    body = "### GROUP_SUMMARY\nsummary only\n"
    missing = missing_required_analysis_sections(
        body, "talend", contract=default_contract("talend")
    )
    assert "COMPONENTS" in missing and "RISK_FLAGS" in missing
    assert missing_required_analysis_sections(
        body, "generic", contract=default_contract("generic")
    ) == []


def test_focus_vocabulary_matches_evidence_bundles():
    """A closed vocabulary is only safe while it matches the real categories."""
    from mcp_server.providers.custom.evidence_bundles import (
        SUPPORTED_EVIDENCE_CATEGORIES,
        _EXCLUDE_PATTERNS,
    )

    assert FOCUS_INCLUDE_VOCABULARY == frozenset(SUPPORTED_EVIDENCE_CATEGORIES)
    assert FOCUS_EXCLUDE_VOCABULARY == frozenset(_EXCLUDE_PATTERNS)


def test_unknown_focus_category_is_rejected_at_load(tmp_path):
    """A typo must fail loudly HERE -- at authoring time -- because silently it
    would narrow evidence to nothing and read as "the codebase has no SQL"."""
    with pytest.raises(Exception, match="closed vocabulary"):
        load_plugin_manifest(
            _pack_dir(
                tmp_path,
                "name: p\nversion: '1.0'\n"
                "lens:\n  focus:\n    include: [sql_querys]\n",
                name="typo",
            )
        )


def test_replace_mode_requires_declared_sections(tmp_path):
    with pytest.raises(Exception, match="requires at least one declared section"):
        load_plugin_manifest(
            _pack_dir(
                tmp_path,
                "name: p\nversion: '1.0'\nlens:\n  sections:\n    mode: replace\n",
                name="emptyreplace",
            )
        )


def test_group_summary_floor_is_unremovable(tmp_path):
    """No manifest field removes the floor, in either direction."""
    with pytest.raises(Exception, match="protected structural section"):
        PackContract(
            pack_id="x",
            mode="replace",
            declared_sections=(PackSectionSpec(id="GROUP_SUMMARY"),),
            required_names=("GROUP_SUMMARY",),
        )

    manifest = load_plugin_manifest(
        _pack_dir(
            tmp_path,
            SQL_INJECTION_PACK_YAML.replace("id: INJECTION_SINKS", "id: GROUP_SUMMARY"),
            name="floorless",
        )
    )
    with pytest.raises(Exception, match="protected structural section"):
        contract_from_plugin_manifest(manifest, "talend")

    # And it is present on every pack that declares a lens, on every platform.
    for platform in ("talend", "workato", "generic"):
        contract = _sql_pack_contract(tmp_path, platform)
        assert "GROUP_SUMMARY" in contract.required_names
        assert contract.floor_names == frozenset({"GROUP_SUMMARY"})


def test_declared_section_count_is_not_artificially_capped(tmp_path):
    declare = "\n".join(f"      - id: SECTION_{i}" for i in range(30))
    manifest = load_plugin_manifest(
        _pack_dir(
            tmp_path,
            f"name: p\nversion: '1.0'\nlens:\n  sections:\n    mode: replace\n    declare:\n{declare}\n",
            name="many-sections",
        )
    )
    contract = contract_from_plugin_manifest(manifest, "talend")
    assert len(contract.declared_sections) == 30


def test_starter_pack_is_a_valid_pack_and_exercises_every_lever():
    """The starter is the template every pack begins from. If it does not
    itself compile, every new pack starts broken."""
    manifest = load_plugin_manifest(REPO_MCP_SERVER / "data" / "swap_pack_starter")
    contract = contract_from_plugin_manifest(manifest, "talend")
    assert contract.mode == "replace"
    assert contract.declared_sections, "starter must demonstrate the section lens"
    assert all(spec.question for spec in contract.declared_sections), (
        "every starter section must show what a question looks like"
    )
    assert contract.docs, "starter must demonstrate the doc set"
    assert contract.focus_include, "starter must demonstrate evidence focus"
    assert contract.audience is not None, "starter must demonstrate audience"
    assert manifest.phases is not None, "starter must demonstrate phase policy"


# ---------------------------------------------------------------------------
# Scoring sees pack-named documents
# ---------------------------------------------------------------------------


def test_scorer_returns_nonzero_for_pack_named_docs(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    body = (
        "# Injection Findings\n\n"
        "## Summary\n\nEvidence-backed findings for `DEU_LoadOrders` with confidence HIGH.\n\n"
        "## Findings by Severity\n\nHIGH risk: concatenated query in DEU_LoadOrders.item.\n\n"
        "## Taint Paths\n\n| Source | Sink |\n|--------|------|\n| request parameter | query |\n\n"
        "## Evidence Register\n\n| # | Claim | Source |\n|---|-------|--------|\n"
        "| 1 | query concat | DEU_LoadOrders.item |\n\n"
    ) + "Inspect, test, verify, rollback guidance with evidence.\n" * 50
    (docs_dir / "INJECTION_FINDINGS.md").write_text(body, encoding="utf-8")
    (docs_dir / "REMEDIATION_BACKLOG__Orders__DEU_LoadOrders.md").write_text(body, encoding="utf-8")
    (docs_dir / "_internal_report.md").write_text("not a product doc", encoding="utf-8")

    report = score_project("irrelevant", docs_dir=docs_dir)
    assert report.docs, "pack-named docs were filtered out of scoring"
    assert {d.file for d in report.docs} == {
        "INJECTION_FINDINGS.md",
        "REMEDIATION_BACKLOG__Orders__DEU_LoadOrders.md",
    }
    assert report.overall > 0.0
