"""Regression tests for scanner-aware bounded Phase 2 context packets."""

from __future__ import annotations

import json

import pytest

from mcp_server.config import config
from mcp_server.providers.base import DocGenContext
from mcp_server.providers.custom.harness import CustomHarnessProvider
from mcp_server.providers.custom.models import (
    FactsManifest,
    HarnessRunState,
    ModelResult,
    UnitFact,
)
from mcp_server.providers.custom.phase2_context import build_phase2_context_packet
from mcp_server.providers.custom.prompt_packs import REQUIRED_SECTION_NAMES
from mcp_server.providers.custom.prompts import group_analysis_prompt
from mcp_server.providers.custom.validators import build_facts_manifest


def _facts() -> FactsManifest:
    units = [
        UnitFact(
            name="InboundJob",
            path="wrapper/PROJECT_A/process/InboundJob.item",
            unit_type="job",
            group="Integration",
            component_count=5,
            key_systems=["JMS"],
        ),
        UnitFact(
            name="DispatchRoute",
            path="wrapper/PROJECT_A/routes/DispatchRoute.item",
            unit_type="route",
            group="Integration",
            component_count=4,
            key_systems=["JMS"],
        ),
        UnitFact(
            name="SiblingJob",
            path="bundle/PROJECT_B/process/SiblingJob.item",
            unit_type="job",
            group="Sibling",
        ),
        UnitFact(
            name="SiblingRoute",
            path="bundle/PROJECT_B/routes/SiblingRoute.item",
            unit_type="route",
            group="Sibling",
        ),
    ]
    return FactsManifest(
        project_id="composite_project",
        platform="talend",
        canonical_unit_label="Jobs and Routes",
        unit_count=len(units),
        groups={"Integration": 2, "Sibling": 2},
        units=units,
        key_systems=["JMS"],
    )


def test_phase2_packet_keeps_typed_scope_and_only_related_project_rows(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    task = staging / "task.md"
    task.write_text(
        "# Assigned source\n\n## InboundJob\ncomponent: tJMSInput_1\n"
        "## DispatchRoute\ncomponent: cJMS_1\n",
        encoding="utf-8",
    )
    facts_path = staging / "facts_manifest.json"
    facts_path.write_text(_facts().model_dump_json(indent=2), encoding="utf-8")
    evidence_brief = staging / "evidence_brief.md"
    evidence_brief.write_text("global evidence\n" * 2_000, encoding="utf-8")
    dependency = staging / "dependency_map.md"
    dependency.write_text(
        "# Dependencies\n\n| Source | Target | Channel |\n|---|---|---|\n"
        "| InboundJob | DispatchRoute | JMS |\n"
        "| DispatchRoute | SiblingJob | JMS |\n"
        + "".join(
            f"| Unrelated{index} | Other{index} | HTTP |\n" for index in range(400)
        ),
        encoding="utf-8",
    )
    prompt_pack = staging / "_analysis_prompt_pack.md"
    prompt_pack.write_text("# Static analysis guide\n", encoding="utf-8")

    packet = build_phase2_context_packet(
        staging_dir=staging,
        facts=_facts(),
        group="Integration",
        task_id="integration_task_1",
        unit_names=["InboundJob", "DispatchRoute"],
        task_evidence=task,
        shared_context=[facts_path, evidence_brief, dependency, prompt_pack],
        investigation_context=[],
        scoped_evidence=None,
        max_bytes=8_000,
        max_scoped_artifact_bytes=2_000,
    )

    assert packet.total_bytes <= 8_000
    assert task in packet.files
    assert evidence_brief not in packet.files
    scoped_facts_path = next(path for path in packet.files if path.name == "SCOPED_FACTS.json")
    scoped_facts = json.loads(scoped_facts_path.read_text(encoding="utf-8"))
    assert scoped_facts["project"]["canonical_unit_count"] == 4
    assert scoped_facts["project"]["kind_totals"] == {"job": 2, "route": 2}
    assert [unit["name"] for unit in scoped_facts["scope"]["units"]] == [
        "InboundJob",
        "DispatchRoute",
    ]
    scoped_dependency = next(
        path for path in packet.files if path.name == "scoped_dependency_map.md"
    ).read_text(encoding="utf-8")
    assert "InboundJob | DispatchRoute" in scoped_dependency
    assert "DispatchRoute | SiblingJob" in scoped_dependency
    assert "Unrelated399" not in scoped_dependency
    manifest = json.loads(packet.manifest_path.read_text(encoding="utf-8"))
    assert not manifest["over_budget_required_evidence"]


def test_phase2_packet_preserves_required_source_and_reports_required_overage(tmp_path):
    staging = tmp_path / "docs_staging"
    staging.mkdir()
    task = staging / "large_task.md"
    task.write_text("InboundJob evidence\n" * 1_000, encoding="utf-8")
    optional = staging / "known_risks.md"
    optional.write_text("InboundJob optional risk\n" * 500, encoding="utf-8")

    packet = build_phase2_context_packet(
        staging_dir=staging,
        facts=_facts(),
        group="Integration",
        task_id="required_overage",
        unit_names=["InboundJob"],
        task_evidence=task,
        shared_context=[optional],
        investigation_context=[],
        scoped_evidence=None,
        max_bytes=1_000,
        max_scoped_artifact_bytes=500,
    )

    assert task in packet.files
    assert packet.total_bytes > 1_000
    manifest = json.loads(packet.manifest_path.read_text(encoding="utf-8"))
    assert manifest["over_budget_required_evidence"]
    assert any("scoped_known_risks.md" in path for path in packet.omitted)


def test_phase2_prompt_lists_only_units_assigned_to_the_task():
    prompt = group_analysis_prompt(
        _facts(),
        "Integration",
        task_units=["InboundJob"],
        multi_task=True,
    )

    assert "- InboundJob | group=Integration" in prompt
    assert "- DispatchRoute | group=Integration" not in prompt
    assert "- SiblingJob | group=Sibling" not in prompt
    assert "Canonical unit count: 4" in prompt
    assert "Do not repeat complete component inventories" in prompt


def test_facts_manifest_group_identity_matches_preprocess_segmentation(tmp_path):
    """The facts-manifest group MUST be the clean ``job.group`` used everywhere
    else in the pipeline (preprocess bundles, Phase 2 analysis files, coverage,
    ``_group_units``). Deriving it from the hashed ``manifest.job_files`` path
    produced a disjoint key space (Gap #3) so ``_group_units`` returned ``[]``
    for every group and coverage bookkeeping went blind. A composite-ZIP scan
    where two same-named jobs from different roots share one clean group must
    still collapse into that single clean group, disambiguated by unit identity
    (job_id/source_root), and ``_group_units`` must resolve them non-empty.
    """
    scan_dir = tmp_path / "scan_output"
    scan_dir.mkdir()
    jobs = [
        {
            "job_id": "root-a::SharedJob",
            "name": "SharedJob",
            "group": "Interfaces",
            "source_root": "bundle/ROOT_A",
            "path": "Interfaces/SharedJob.item",
            "components": [],
        },
        {
            "job_id": "root-b::SharedJob",
            "name": "SharedJob",
            "group": "Interfaces",
            "source_root": "bundle/ROOT_B",
            "path": "Interfaces/SharedJob.item",
            "components": [],
        },
    ]
    (scan_dir / "scan_result.json").write_text(
        json.dumps(
            {
                "platform": "talend",
                "jobs": jobs,
                "manifest": {
                    "job_files": {
                        "root-a::SharedJob": "jobs/bundle_ROOT_A__Interfaces-a1/job-a.json",
                        "root-b::SharedJob": "jobs/bundle_ROOT_B__Interfaces-b2/job-b.json",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    facts = build_facts_manifest("composite", scan_dir)

    # The manifest group is the clean logical group -- the SAME identity that
    # names _preprocess/{group}.md and _analysis/{group}.md on disk.
    assert facts.groups == {"Interfaces": 2}
    assert [unit.name for unit in facts.units] == [
        "root-a::SharedJob",
        "root-b::SharedJob",
    ]
    assert {unit.group for unit in facts.units} == {"Interfaces"}
    # group and logical_group are now reconciled to one identity.
    assert {unit.logical_group for unit in facts.units} == {"Interfaces"}
    # Composite origin is not lost -- source_root still disambiguates the roots.
    assert {unit.source_root for unit in facts.units} == {
        "bundle/ROOT_A",
        "bundle/ROOT_B",
    }

    # group -> units resolution is non-empty and consistent between the two
    # schemes: iterating the facts groups (== preprocess groups) resolves the
    # real units, and every unit is assigned to exactly one resolvable group.
    state = HarnessRunState(project_id="composite", facts_manifest=facts)
    assert CustomHarnessProvider._group_units(state, "Interfaces") == [
        "root-a::SharedJob",
        "root-b::SharedJob",
    ]
    resolved = {
        name
        for group in facts.groups
        for name in CustomHarnessProvider._group_units(state, group)
    }
    assert resolved == facts.unit_names()
    assert all(CustomHarnessProvider._group_units(state, g) for g in facts.groups)


def test_phase2_task_validation_rejects_missing_late_sections():
    partial = (
        "### COMPONENTS\n\nInboundJob uses tInput_1.\n\n"
        + "Evidence detail. " * 40
    )

    report = CustomHarnessProvider._validate_phase2_task_output(
        partial,
        ["InboundJob"],
        platform="talend",
        require_sections=True,
    )

    assert not report["ok"]
    assert "EXTERNAL_SYSTEMS" in report["missing_sections"]
    assert "RISK_FLAGS" in report["missing_sections"]
    assert "GROUP_SUMMARY" in report["missing_sections"]

    complete = "\n\n".join(
        f"### {section}\n\nInboundJob: UNKNOWN in scoped evidence."
        for section in (*REQUIRED_SECTION_NAMES, "GROUP_SUMMARY")
    )
    complete += "\n\n" + ("Bounded evidence detail. " * 30)
    accepted = CustomHarnessProvider._validate_phase2_task_output(
        complete,
        ["InboundJob"],
        platform="talend",
        require_sections=True,
    )
    assert accepted["ok"]


@pytest.mark.asyncio
async def test_run_token_budget_reservations_are_atomic(monkeypatch):
    class _Provider:
        model_name = "fake/model"

    monkeypatch.setattr(config, "docgen_input_token_budget_base", 100)
    monkeypatch.setattr(config, "docgen_input_token_budget_per_unit", 0)
    monkeypatch.setattr(config, "docgen_input_token_budget_per_doc", 0)
    monkeypatch.setattr(config, "docgen_max_run_input_tokens", 100)
    monkeypatch.setattr(config, "docgen_output_token_budget_base", 1_000)
    monkeypatch.setattr(config, "docgen_output_token_budget_per_unit", 0)
    monkeypatch.setattr(config, "docgen_output_token_budget_per_doc", 0)
    monkeypatch.setattr(config, "docgen_max_run_output_tokens", 1_000)
    monkeypatch.setattr(config, "docgen_token_estimate_safety_factor", 1.0)

    harness = CustomHarnessProvider(_Provider())
    state = HarnessRunState(project_id="composite_project", facts_manifest=_facts())
    first, first_error = await harness._reserve_model_budget(
        state, estimated_input_tokens=60, max_output_tokens=10
    )
    assert first is not None
    assert first_error is None

    blocked, blocked_error = await harness._reserve_model_budget(
        state, estimated_input_tokens=50, max_output_tokens=10
    )
    assert blocked is None
    assert "input token budget" in (blocked_error or "")

    await harness._settle_model_budget(
        state,
        first,
        ModelResult(success=True, input_tokens=40, output_tokens=5),
    )
    after_settlement, after_error = await harness._reserve_model_budget(
        state, estimated_input_tokens=50, max_output_tokens=10
    )
    assert after_settlement is not None
    assert after_error is None
    await harness._settle_model_budget(state, after_settlement, None)


@pytest.mark.asyncio
async def test_run_input_token_budget_can_be_unlimited(monkeypatch):
    class _Provider:
        model_name = "fake/model"

    monkeypatch.setattr(config, "docgen_max_run_input_tokens", 0)
    monkeypatch.setattr(config, "docgen_output_token_budget_base", 1_000)
    monkeypatch.setattr(config, "docgen_output_token_budget_per_unit", 0)
    monkeypatch.setattr(config, "docgen_output_token_budget_per_doc", 0)
    monkeypatch.setattr(config, "docgen_max_run_output_tokens", 1_000)
    monkeypatch.setattr(config, "docgen_token_estimate_safety_factor", 1.0)

    harness = CustomHarnessProvider(_Provider())
    state = HarnessRunState(project_id="composite_project", facts_manifest=_facts())
    reservation, error = await harness._reserve_model_budget(
        state, estimated_input_tokens=10_000_000, max_output_tokens=10
    )

    assert reservation is not None
    assert error is None
    await harness._settle_model_budget(state, reservation, None)


@pytest.mark.asyncio
async def test_direct_phase2_promotes_oversized_group_to_unit_task_planner(
    tmp_path, monkeypatch
):
    class _Provider:
        model_name = "fake/model"

    staging = tmp_path / "docs_staging"
    preprocess = staging / "_preprocess"
    analysis = staging / "_analysis"
    preprocess.mkdir(parents=True)
    analysis.mkdir()
    (preprocess / "Integration.md").write_text(
        "InboundJob source evidence and component tInput_1\n" * 50,
        encoding="utf-8",
    )
    ctx = DocGenContext(
        project_id="composite_project",
        staging_dir=staging,
        staging_abs=str(staging),
        logs_abs=str(tmp_path / "logs"),
        preprocess_groups=["Integration"],
        total_jobs=4,
        total_groups=2,
        effective_model="fake/model",
        write_model="fake/model",
        platform="talend",
        analysis_guide_file="_analysis_guide.md",
    )
    state = HarnessRunState(project_id="composite_project", facts_manifest=_facts())
    harness = CustomHarnessProvider(_Provider())
    promoted: list[str] = []

    async def _promoted(*args, **kwargs):
        promoted.append(args[3])

    monkeypatch.setattr(config, "phase2_task_evidence_bytes", 100)
    monkeypatch.setattr(harness, "_phase2_group_agent", _promoted)

    async def _log(*args, **kwargs):
        return None

    await harness._phase2_group_direct(
        ctx,
        state,
        "Integration",
        [],
        analysis,
        preprocess,
        _log,
    )

    assert promoted == ["Integration"]
