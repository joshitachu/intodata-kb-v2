"""Tests for the P0-P3 legacy-engine upgrade.

Covers:
- P0.1 work-plan call/output budgets (count_evidence_tasks + _plan_call_budget);
- P0.2 salvage-first assembly (Phase 2 group containment, Phase 3 doc stubs /
  truncate-accept);
- P0.3 per-doc-type output ceilings;
- P1.4/P1.6 standard-catalog per-unit doc expansion + unit-docs digest;
- P1.5 protected-fact / unit-count validators;
- Duplicate-structure merge + guard (verified slice-restart bug);
- P3.10 claims density floor.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.doc_slice_merge import (
    dedupe_document,
    duplicate_heading_errors,
    merge_sliced_doc_outputs,
)
from mcp_server.providers.custom.evidence_tasks import (
    count_evidence_tasks,
    plan_evidence_tasks,
)
from mcp_server.providers.custom.fabrication_validators import (
    protected_fact_errors,
    unit_count_claim_errors,
)
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import (
    FactsManifest,
    HarnessRunState,
    ModelResult,
    UnitFact,
)
from mcp_server.providers.custom.validators import group_analysis_is_complete


def _facts(names: list[str], group: str = "A") -> FactsManifest:
    return FactsManifest(
        project_id="proj",
        platform="talend",
        canonical_unit_label="jobs",
        unit_count=len(names),
        groups={group: len(names)},
        units=[UnitFact(name=name, group=group) for name in names],
    )


def _provider() -> CustomHarnessProvider:
    mp = MagicMock()
    mp.model_name = "test/model"
    return CustomHarnessProvider(mp)


def _ctx(staging: Path, groups: list[str]) -> DocGenContext:
    return DocGenContext(
        project_id="proj",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(staging.parent / "logs"),
        preprocess_groups=groups,
        total_jobs=1,
        total_groups=len(groups),
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )


async def _noop_log(pid: str, message: str, level: str = "info") -> None:
    return None


# --------------------------------------------------------------------------- #
# P0.1 -- count_evidence_tasks parity + plan-based budget
# --------------------------------------------------------------------------- #

def _group_file(tmp_path: Path, jobs: int, body_bytes: int = 3_000) -> tuple[Path, list[str]]:
    parts = ["# Pre-processed Scan Data: A\n"]
    names = []
    for i in range(jobs):
        name = f"Job_{i:03d}"
        names.append(name)
        parts.append(f"## Talend Job: {name}\n\n" + ("x" * body_bytes) + "\n")
    path = tmp_path / "A.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path, names


def test_count_evidence_tasks_matches_plan(tmp_path):
    group_file, names = _group_file(tmp_path, jobs=12)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    for max_units in (None, 1, 3):
        planned = plan_evidence_tasks(
            "A", group_file, names, out_dir, 10_000, max_units_per_task=max_units
        )
        counted = count_evidence_tasks(group_file, names, 10_000, max_units_per_task=max_units)
        assert counted == len(planned), f"max_units={max_units}"
    assert count_evidence_tasks(tmp_path / "missing.md", names, 10_000) == 0


def test_plan_call_budget_scales_with_units_not_groups(tmp_path):
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    group_file, names = _group_file(staging / "_preprocess", jobs=40)
    group_file.rename(staging / "_preprocess" / "G.md")
    facts = _facts(names, group="G")
    provider = _provider()
    provider._expand_per_job_docs(facts)
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    ctx = _ctx(staging, ["G"])

    plan = provider._plan_call_budget(ctx, state)
    # One Phase 2 claim task per unit packet (units_per_task=1 default).
    assert plan["phase2_tasks"] == 40
    # R2.1: the plan carries a retry allowance for validation repairs.
    assert plan["phase2_retries"] == -(-40 * config.docgen_phase2_retry_allowance // 1)
    assert state.planned_call_budget == plan["budget"]
    # The old per-group heuristic granted 20 + 1*4 + docs*4; the plan-based
    # budget must cover at least one call per Phase 2 task.
    assert plan["budget"] > 40
    # R2.1: the plan is a GUIDE; the enforcement ceiling is the configured
    # hard cap (the verified Assetcheck run blocked Phase 3 at plan=179 while
    # cap=250 had ~71 calls unused).
    assert plan["ceiling"] == config.docgen_max_run_calls
    assert provider._run_model_call_budget(state) == config.docgen_max_run_calls
    # Late phases get guaranteed floors so early overruns cannot starve them.
    assert state.planned_phase_floors["phase3"] == plan["phase3_calls"]
    assert state.planned_phase_floors["business_topology"] == plan["topology_calls"]


def test_scorecard_and_evidence_agents_cut_from_plan_by_default(tmp_path):
    """R2.2/R2.3: standard runs plan no scorecard and no evidence-agent calls
    unless explicitly enabled."""
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    group_file, names = _group_file(staging / "_preprocess", jobs=6)
    group_file.rename(staging / "_preprocess" / "G.md")
    facts = _facts(names, group="G")
    provider = _provider()
    provider._expand_per_job_docs(facts)
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    ctx = _ctx(staging, ["G"])

    plan = provider._plan_call_budget(ctx, state)
    assert plan["scorecard_calls"] == 0
    assert plan["evidence_agent_calls"] == 0
    assert provider._phase_policy("scorecard") == "skip"


def test_scorecard_plan_estimates_slices_plus_reducer_when_enabled(tmp_path, monkeypatch):
    """R2.1: when the LLM scorecard is enabled, its estimate reflects the
    verified slice fan-out (17 groups -> 12 slices + reducer), not a flat 4."""
    monkeypatch.setattr(config, "docgen_llm_scorecard_enabled", True)
    staging = tmp_path / "docs_staging"
    (staging / "_preprocess").mkdir(parents=True)
    groups = [f"G{i}" for i in range(17)]
    all_names: list[str] = []
    facts_units: list[str] = []
    for g in groups:
        f, names = _group_file(staging / "_preprocess", jobs=1)
        f.rename(staging / "_preprocess" / f"{g}.md")
        all_names.extend(names)
    facts = FactsManifest(
        project_id="proj",
        platform="talend",
        canonical_unit_label="jobs",
        unit_count=17,
        groups={g: 1 for g in groups},
        units=[UnitFact(name=f"Job_{i:03d}", group=g) for i, g in enumerate(groups)],
    )
    provider = _provider()
    provider._expand_per_job_docs(facts)
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    ctx = _ctx(staging, groups)
    plan = provider._plan_call_budget(ctx, state)
    assert plan["scorecard_calls"] == 1 + -(-17 * 3 // 4)  # 1 reducer + ceil(0.75*17)


@pytest.mark.asyncio
async def test_phase3_floor_blocks_early_phase_not_phase3(tmp_path):
    """R2.1: the shared pool stops early phases at ceiling - floors, while a
    Phase 3 call may still reserve into its own protected floor."""
    facts = _facts(["Alpha"])
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    state.planned_call_budget = 100
    state.planned_phase_floors = {"phase3": 40, "business_topology": 2}
    # Simulate a run that already consumed everything the shared pool allows.
    state.budget_consumed_calls = config.docgen_max_run_calls - 42

    blocked, reason = await provider._reserve_model_budget(
        state, estimated_input_tokens=10, max_output_tokens=10, phase="phase2.claims"
    )
    assert blocked is None and "reserved for pending later phases" in reason

    granted, reason = await provider._reserve_model_budget(
        state, estimated_input_tokens=10, max_output_tokens=10, phase="phase3"
    )
    assert granted is not None, reason
    # Topology's floor is still protected from Phase 3.
    state.budget_consumed_calls = config.docgen_max_run_calls - 2
    state.pending_model_calls = 0
    blocked3, _ = await provider._reserve_model_budget(
        state, estimated_input_tokens=10, max_output_tokens=10, phase="phase3"
    )
    assert blocked3 is None
    # Once Phase 3 dispatch finishes, _phase3_docs releases its unused floor;
    # topology then reserves from the shared pool.
    state.planned_phase_floors.pop("phase3", None)
    topo, reason = await provider._reserve_model_budget(
        state, estimated_input_tokens=10, max_output_tokens=10, phase="business_topology"
    )
    assert topo is not None, reason
    # The hard ceiling itself is never crossed by any class.
    state.budget_consumed_calls = config.docgen_max_run_calls
    state.pending_model_calls = 0
    over, reason = await provider._reserve_model_budget(
        state, estimated_input_tokens=10, max_output_tokens=10, phase="business_topology"
    )
    assert over is None


def test_output_budget_sized_per_doc_task(tmp_path):
    facts = _facts([f"J{i}" for i in range(10)])
    provider = _provider()
    provider._expand_per_job_docs(facts)
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    state.planned_phase1_tasks = 4
    state.planned_phase2_tasks = 10
    budget = provider._run_output_token_budget(state)
    expected = (
        config.docgen_output_token_budget_base
        + 4 * config.investigation_max_tokens
        + 10 * config.phase2_claim_max_tokens
        + sum(provider._doc_output_ceiling(d, state) * 2 for d in provider._required_docs)
    )
    assert budget == min(config.docgen_max_run_output_tokens, expected)


# --------------------------------------------------------------------------- #
# P0.3 -- per-doc-type output ceilings
# --------------------------------------------------------------------------- #

def test_doc_output_ceilings_by_type(monkeypatch):
    # Unit docs default OFF; this test exercises the per-unit ceiling too.
    monkeypatch.setattr(config, "docgen_unit_docs_enabled", True)
    facts = _facts([f"J{i}" for i in range(50)])
    provider = _provider()
    provider._expand_per_job_docs(facts)
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    unit_doc = next(iter(provider._per_job_doc_units))
    brief = provider._doc_output_ceiling("00_AGENT_BRIEF", state)
    unit = provider._doc_output_ceiling(unit_doc, state)
    inventory = provider._doc_output_ceiling("02_COMPONENT_INVENTORY", state)
    other = provider._doc_output_ceiling("03_DATA_FLOWS_AND_LINEAGE", state)
    assert unit == config.docgen_unit_doc_output_tokens
    assert brief == config.docgen_brief_output_tokens
    assert brief < other == config.phase3_max_tokens
    assert inventory == min(
        config.phase3_max_tokens,
        config.docgen_inventory_output_tokens_base
        + 50 * config.docgen_inventory_output_tokens_per_unit,
    )


# --------------------------------------------------------------------------- #
# P1.4/P1.6 -- standard-catalog per-unit expansion
# --------------------------------------------------------------------------- #

def test_catalog_per_unit_docs_expand_per_unit(monkeypatch):
    monkeypatch.setattr(config, "docgen_unit_docs_enabled", True)
    facts = _facts(["Alpha", "Beta"])
    provider = _provider()
    base_docs = list(provider._required_docs)
    assert "10_UNIT_REFERENCE" not in base_docs  # base name is never a doc
    provider._expand_per_job_docs(facts)
    expanded = [d for d in provider._required_docs if d.startswith("10_UNIT_REFERENCE__")]
    assert len(expanded) == 2
    assert provider._per_job_doc_units[expanded[0]] == "Alpha"
    assert provider._doc_section_requirements_extra[expanded[0]][0] == "Overview"
    # Group docs unchanged.
    assert all(d in provider._required_docs for d in base_docs)


def test_unit_docs_disabled_keeps_previous_doc_set(monkeypatch):
    monkeypatch.setattr(config, "docgen_unit_docs_enabled", False)
    facts = _facts(["Alpha"])
    provider = _provider()
    before = list(provider._required_docs)
    provider._expand_per_job_docs(facts)
    assert provider._required_docs == before
    assert provider._per_job_doc_units == {}


def test_unit_docs_digest_built_from_finished_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "docgen_unit_docs_enabled", True)
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    facts = _facts(["Alpha"])
    provider = _provider()
    provider._expand_per_job_docs(facts)
    unit_doc = next(iter(provider._per_job_doc_units))
    (staging / f"{unit_doc}.md").write_text(
        "# Unit Document: Alpha\n\n## Overview\n\nAlpha moves rows.\n\n"
        "## Protected Scanner Facts\n\n| fact | value |\n|---|---|\n| group | A |\n\n"
        "## Component Inventory and Implemented Behavior\n\n- tMap_1\n",
        encoding="utf-8",
    )
    ctx = _ctx(staging, ["A"])
    digest = provider._build_unit_docs_digest(ctx, [unit_doc])
    assert digest is not None
    text = digest.read_text(encoding="utf-8")
    assert "Alpha moves rows." in text
    assert "| group | A |" in text
    assert "tMap_1" not in text  # only Overview + protected facts


# --------------------------------------------------------------------------- #
# P0.2 -- salvage-first assembly
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_phase2_group_failure_becomes_partial_stub(tmp_path):
    staging = tmp_path / "docs_staging"
    (staging / "_analysis").mkdir(parents=True)
    facts = _facts(["Alpha", "Beta"])
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    ctx = _ctx(staging, ["A"])

    await provider._contain_phase2_group_failures(
        ctx, state, [("A", RuntimeError("budget exhausted"))], _noop_log
    )
    stub = staging / "_analysis" / "A.md"
    assert stub.exists()
    text = stub.read_text(encoding="utf-8")
    assert "- COVERED: Alpha" in text and "PARTIAL" in text
    assert "A" in state.thin_bundle_groups and "A" in state.partial_groups
    # The stub must NOT be reusable on resume -- it lacks required sections.
    assert not group_analysis_is_complete(stub, "talend")


def test_salvaged_doc_stub_and_truncate_accept(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    facts = _facts(["Alpha"])
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    ctx = _ctx(staging, ["A"])

    # Hard failure -> stub with reason.
    provider._salvage_failed_doc(
        ctx, state, "03_DATA_FLOWS_AND_LINEAGE",
        ModelResult(success=False, raw_error="budget blocked"),
    )
    stub = (staging / "03_DATA_FLOWS_AND_LINEAGE.md").read_text(encoding="utf-8")
    assert "GENERATION GAP" in stub and "budget blocked" in stub
    assert state.failed_docs[-1]["disposition"] == "stub"

    # Output-cap hit with usable text -> truncate-flag-ACCEPT.
    provider._salvage_failed_doc(
        ctx, state, "04_BUSINESS_RULES_AND_TRANSFORMATIONS",
        ModelResult(
            success=False,
            text="# 04 Business Rules And Transformations\n\n## Business Rules\n\nBR-A-001 " + "x" * 300,
            raw_error="incomplete: provider stopped because max tokens were reached",
            metadata={"incomplete_reason": "length"},
        ),
    )
    doc = (staging / "04_BUSINESS_RULES_AND_TRANSFORMATIONS.md").read_text(encoding="utf-8")
    assert "TRUNCATION NOTICE" in doc and "BR-A-001" in doc
    assert state.failed_docs[-1]["disposition"] == "truncated_accepted"


# --------------------------------------------------------------------------- #
# Duplicate-structure merge + guard (verified slice-restart bug)
# --------------------------------------------------------------------------- #

_SLICE_A = (
    "# 01 Architecture Map\n\n## Table of Contents\n\n- Topology\n\n"
    "## Topology\n\nHub and spoke around the ESB.\n\n## Entry Points\n\n- JMS queue in\n"
)
_SLICE_B = (
    "# 01 Architecture Map\n\n## Table of Contents\n\n- Topology\n\n"
    "## Topology\n\nHub and spoke around the ESB.\n\n## Entry Points\n\n- SOAP endpoint\n\n"
    "## Dependencies\n\n- MSSQL LogInfo\n"
)


def test_merge_sliced_doc_outputs_collapses_restarts():
    merged = merge_sliced_doc_outputs([_SLICE_A, _SLICE_B])
    assert merged.count("# 01 Architecture Map") == 1
    assert merged.lower().count("## table of contents") == 1
    assert merged.count("## Topology") == 1
    assert merged.count("Hub and spoke around the ESB.") == 1  # duplicate block dropped
    # Union of unique content is preserved.
    assert "- JMS queue in" in merged and "- SOAP endpoint" in merged
    assert "- MSSQL LogInfo" in merged
    assert duplicate_heading_errors(merged) == []


def test_duplicate_heading_guard_and_dedupe():
    repeated = _SLICE_A + "\n\n---\n\n" + _SLICE_A + "\n\n---\n\n" + _SLICE_B
    errors = duplicate_heading_errors(repeated)
    assert errors and "top-level" in errors[0]
    repaired = dedupe_document(repeated)
    assert duplicate_heading_errors(repaired) == []
    assert "- SOAP endpoint" in repaired


def test_final_doc_content_repairs_duplicated_structure(tmp_path):
    facts = _facts(["Alpha"])
    provider = _provider()
    state = HarnessRunState(project_id="proj", facts_manifest=facts)
    body = _SLICE_A + "\n\n---\n\n" + _SLICE_A
    content = provider._final_doc_content(state, "01_ARCHITECTURE_MAP", body)
    assert duplicate_heading_errors(content) == []


# --------------------------------------------------------------------------- #
# P1.5 -- protected facts + unit-count validators
# --------------------------------------------------------------------------- #

_LEDGER = {"group": "AssetCheck2Vise", "component_count": "16", "version": "2.3"}


def test_protected_fact_errors_flag_recompute_and_missing_section():
    good = (
        "## Protected Scanner Facts\n\n| fact | value |\n|---|---|\n"
        "| group | AssetCheck2Vise |\n| component_count | 16 [fact:unit.X.component_count] |\n"
        "| version | 2.3 |\n"
    )
    assert protected_fact_errors(good, _LEDGER) == []
    recomputed = good.replace("| component_count | 16 ", "| component_count | 15 ")
    errors = protected_fact_errors(recomputed, _LEDGER)
    assert errors and "component_count" in errors[0]
    assert protected_fact_errors("# Doc with no facts table", _LEDGER)


def test_unit_count_claim_errors_only_flags_unmatched_counts():
    errors = unit_count_claim_errors(
        "This project contains 14 jobs across 3 groups and 3 external systems.",
        unit_count=13,
        canonical_unit_label="jobs",
        allowed_counts=frozenset({6, 4, 3}),
    )
    assert errors and "14" in errors[0]
    assert unit_count_claim_errors(
        "This project contains 13 jobs; group Maximo2RHM has 6 jobs.",
        unit_count=13,
        canonical_unit_label="jobs",
        allowed_counts=frozenset({6}),
    ) == []


# --------------------------------------------------------------------------- #
# P2.8 -- resume reuses accepted task artifacts, retries rejected ones
# --------------------------------------------------------------------------- #

def test_resume_reuses_accepted_but_not_rejected_tasks(tmp_path):
    import json as _json

    from mcp_server.providers.custom.agent_runtime import (
        AgentTaskContract,
        AgentTaskRuntime,
    )

    staging = tmp_path / "docs_staging"
    staging.mkdir()
    facts = _facts(["Alpha"])
    runtime = AgentTaskRuntime(staging, facts)

    def _persist(target: str, status: str, text: str) -> AgentTaskContract:
        contract = AgentTaskContract(
            phase="phase3_doc",
            target_id=target,
            allowed_units=["Alpha"],
            allowed_groups=["A"],
            allowed_categories=["inventory"],
            expected_artifact="document markdown",
        )
        task_dir = runtime.task_dir(contract)
        task_dir.mkdir(parents=True)
        (task_dir / "output.md").write_text(text, encoding="utf-8")
        (task_dir / "status.json").write_text(
            _json.dumps({"status": status, "accepted": status == "accepted"}),
            encoding="utf-8",
        )
        return contract

    accepted = _persist("03_DATA_FLOWS_AND_LINEAGE", "accepted", "# Doc\n\nGood.")
    rejected = _persist("04_BUSINESS_RULES_AND_TRANSFORMATIONS", "rejected", "# Doc\n\nBad.")
    assert runtime.accepted_output(accepted) == "# Doc\n\nGood."
    assert runtime.accepted_output(rejected) is None  # resume retries this one


# --------------------------------------------------------------------------- #
# P3.10 -- claims density floor
# --------------------------------------------------------------------------- #

def test_claims_density_floor_requires_verbatim_citation(tmp_path):
    import json as _json

    from mcp_server.providers.custom.evidence_tasks import EvidenceTask
    from mcp_server.providers.custom.phase2_claims import (
        ClaimTaskSpec,
        apply_claim_floors,
        parse_claim_batch,
        validate_claim_batch,
    )

    evidence = (
        "## Talend Job: Alpha\n\n#### tMSSqlInput_1 (tMSSqlInput)\n"
        "- **QUERY**: `\"SELECT ProcessID FROM Processes WHERE ProcessCode = 'X'\"`\n"
    )
    task_path = tmp_path / "task.md"
    task_path.write_text(evidence, encoding="utf-8")
    task = EvidenceTask(index=1, task_id="A", path=task_path, units=["Alpha"])
    spec = ClaimTaskSpec(
        task=task,
        concern="analysis",
        sections=("SOURCE_SQL",),
        artifact_names=frozenset(),
        max_output_tokens=4_000,
    )
    facts = _facts(["Alpha"])

    def _batch(statement: str, kind: str = "fact") -> str:
        return _json.dumps({
            "schema_version": "phase2-claims-v1",
            "group": "A",
            "task_id": spec.target_id,
            "concern": "analysis",
            "claims": [{
                "section": "SOURCE_SQL",
                "kind": kind,
                "unit_names": ["Alpha"] if kind != "unknown" else ["Alpha"],
                "statement": statement,
                "evidence_refs": ["task.md#tMSSqlInput_1"] if kind != "unknown" else [],
                "confidence": "supported" if kind != "unknown" else "unknown",
            }],
        })

    # R2.4: a paraphrase that still cites evidence_refs is ACCEPTED (verbatim
    # citation stays preferred, not mandatory) -- rejecting the whole batch
    # for one uncited claim stubbed 21/73 Assetcheck tasks to unknown.
    paraphrase = validate_claim_batch(
        _batch("Reads the process id from the Processes table."),
        facts=facts, group="A", spec=spec, evidence_text=evidence,
    )
    assert paraphrase["ok"], paraphrase["errors"]
    assert any("paraphrases" in w for w in paraphrase["warnings"])

    verbatim = validate_claim_batch(
        _batch("Selects the run bookmark: `SELECT ProcessID FROM Processes WHERE ProcessCode = 'X'`."),
        facts=facts, group="A", spec=spec, evidence_text=evidence,
    )
    assert verbatim["ok"], verbatim["errors"]
    assert not verbatim["warnings"]

    # An unknown skeleton in an evidenced code-bearing section is accepted
    # with a per-section gap warning, not a whole-batch rejection.
    skeleton_unknown = validate_claim_batch(
        _batch("Analysis unavailable for this scoped section.", kind="unknown"),
        facts=facts, group="A", spec=spec, evidence_text=evidence,
    )
    assert skeleton_unknown["ok"], skeleton_unknown["errors"]
    assert any("no substantive claim" in w for w in skeleton_unknown["warnings"])

    # Without evidence for the category, no floor warning fires.
    no_sql_evidence = validate_claim_batch(
        _batch("Analysis unavailable for this scoped section.", kind="unknown"),
        facts=facts, group="A", spec=spec, evidence_text="## Talend Job: Alpha\n\nplain\n",
    )
    assert no_sql_evidence["ok"], no_sql_evidence["errors"]
    assert not no_sql_evidence["warnings"]

    # STRUCTURAL problems still fail the batch: a section outside the task
    # contract is a contract violation, not a demotable gap.
    outside = _json.loads(_batch("Some claim with `SELECT ProcessID FROM Processes` quote."))
    outside["claims"][0]["section"] = "EXECUTION_FLOW"
    structural = validate_claim_batch(
        _json.dumps(outside), facts=facts, group="A", spec=spec, evidence_text=evidence,
    )
    assert not structural["ok"]
    assert any("outside task contract" in e for e in structural["errors"])

    # Deterministic demotion: a claim with neither evidence_refs nor a
    # verbatim citation is demoted to unknown per-claim; grounded siblings
    # in the same batch are untouched.
    ungrounded = _json.loads(_batch("Reads something from somewhere."))
    ungrounded["claims"][0]["evidence_refs"] = []
    batch = parse_claim_batch(
        _json.dumps(ungrounded),
        expected_group="A", expected_task_id=spec.target_id, expected_concern="analysis",
    )
    adjusted, notes = apply_claim_floors(batch, evidence)
    assert adjusted.claims[0].kind == "unknown"
    assert adjusted.claims[0].confidence == "unknown"
    assert notes and "demoted" in notes[0]

    grounded = parse_claim_batch(
        _batch("Selects the run bookmark: `SELECT ProcessID FROM Processes WHERE ProcessCode = 'X'`."),
        expected_group="A", expected_task_id=spec.target_id, expected_concern="analysis",
    )
    kept, kept_notes = apply_claim_floors(grounded, evidence)
    assert kept.claims[0].kind == "fact"
    assert kept_notes == []
