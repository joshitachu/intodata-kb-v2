"""Contract tests for bounded Phase 2 claim extraction."""

from __future__ import annotations

import json

import pytest

from mcp_server.providers.custom.agent_runtime import AgentTaskContract, AgentTaskRuntime
from mcp_server.providers.custom.evidence_tasks import EvidenceTask, plan_evidence_tasks
from mcp_server.providers.custom.models import FactsManifest, ModelResult, UnitFact
from mcp_server.providers.custom.phase2_claims import (
    parse_claim_batch,
    plan_claim_tasks,
    render_claims_as_markdown,
    split_claim_task,
    unknown_batch,
    validate_claim_batch,
    write_claim_ledger,
)
from mcp_server.providers.custom.prompt_packs import ANALYSIS_SECTION_NAMES


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="project",
        platform="talend",
        canonical_unit_label="Jobs and Routes",
        unit_count=2,
        groups={"Interfaces": 2},
        units=[
            UnitFact(name="Inbound", unit_type="job", group="Interfaces"),
            UnitFact(name="Dispatch", unit_type="route", group="Interfaces"),
        ],
    )


def _task(tmp_path, *, units: list[str] | None = None) -> EvidenceTask:
    path = tmp_path / "task.md"
    path.write_text("# evidence\n", encoding="utf-8")
    return EvidenceTask(
        index=1,
        task_id="Interfaces__task_1",
        path=path,
        units=units or ["Inbound"],
    )


def test_claim_planner_uses_one_request_per_unit_packet(tmp_path):
    specs = plan_claim_tasks(
        _task(tmp_path),
        evidence_bytes=300_000,
        dense_threshold_bytes=1,
        max_output_tokens=6_000,
    )

    assert len(specs) == 1
    assert specs[0].concern == "analysis"
    assert specs[0].sections == ANALYSIS_SECTION_NAMES
    assert specs[0].max_output_tokens == 6_000


def test_length_replan_splits_sections_without_changing_unit_scope(tmp_path):
    spec = plan_claim_tasks(
        _task(tmp_path), evidence_bytes=100, max_output_tokens=6_000
    )[0]
    children = split_claim_task(spec)

    assert children is not None
    left, right = children
    assert left.task is spec.task
    assert right.task is spec.task
    assert set(left.sections).isdisjoint(right.sections)
    assert left.sections + right.sections == spec.sections


def test_claim_validation_enforces_section_unit_and_evidence_contract(tmp_path):
    spec = plan_claim_tasks(
        _task(tmp_path), evidence_bytes=100, max_output_tokens=6_000
    )[0]
    claims = []
    for section in spec.sections:
        claims.append(
            {
                "section": section,
                "kind": "fact",
                "unit_names": ["Inbound"],
                "related_units": [],
                "statement": f"Bounded finding for {section}",
                "evidence_refs": ["scan/jobs/Inbound.json"],
                "confidence": "supported",
                "claim_id": "",
            }
        )
    payload = json.dumps(
        {
            "schema_version": "phase2-claims-v1",
            "group": "Interfaces",
            "task_id": spec.target_id,
            "concern": spec.concern,
            "claims": claims,
        }
    )

    accepted = validate_claim_batch(
        payload, facts=_facts(), group="Interfaces", spec=spec
    )
    assert accepted["ok"]

    claims[0]["unit_names"] = ["Dispatch"]
    claims[1]["evidence_refs"] = []
    rejected = validate_claim_batch(
        json.dumps(
            {
                "schema_version": "phase2-claims-v1",
                "group": "Interfaces",
                "task_id": spec.target_id,
                "concern": spec.concern,
                "claims": claims,
            }
        ),
        facts=_facts(),
        group="Interfaces",
        spec=spec,
    )
    assert not rejected["ok"]
    assert any("outside task contract" in error for error in rejected["errors"])
    # R2.4: a missing evidence_refs is an evidential gap (the claim is demoted
    # per-section by apply_claim_floors), not a whole-batch rejection.
    assert any("has no evidence_refs" in warning for warning in rejected["warnings"])
    assert not any("has no evidence_refs" in error for error in rejected["errors"])


def test_unknowns_render_all_legacy_sections_and_persist_claim_ids(tmp_path):
    spec = plan_claim_tasks(
        _task(tmp_path), evidence_bytes=100, max_output_tokens=6_000
    )[0]
    batch = unknown_batch(group="Interfaces", spec=spec, reason="provider unavailable")
    rendered = render_claims_as_markdown(
        group="Interfaces", unit_names=["Inbound"], batches=[batch]
    )

    for section in ANALYSIS_SECTION_NAMES:
        assert f"### {section}" in rendered
    assert "### CROSS_JOB_FINDINGS" in rendered
    assert "### GROUP_SUMMARY" in rendered
    assert "explicit unknowns" in rendered

    ledger = tmp_path / "claims.jsonl"
    write_claim_ledger(
        ledger, group="Interfaces", unit_names=["Inbound"], batches=[batch]
    )
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["record_type"] == "claim_ledger_header"
    assert all(row["claim_id"].startswith("claim:") for row in rows[1:])


def test_claim_parser_assigns_stable_ids(tmp_path):
    spec = plan_claim_tasks(
        _task(tmp_path), evidence_bytes=100, max_output_tokens=6_000
    )[0]
    payload = json.dumps(
        {
            "schema_version": "phase2-claims-v1",
            "group": "Interfaces",
            "task_id": spec.target_id,
            "concern": spec.concern,
            "claims": [
                {
                    "section": "components",
                    "kind": "unknown",
                    "unit_names": ["Inbound"],
                    "statement": "No component evidence",
                    "confidence": "unknown",
                }
            ],
        }
    )
    first = parse_claim_batch(
        payload,
        expected_group="Interfaces",
        expected_task_id=spec.target_id,
        expected_concern=spec.concern,
    )
    second = parse_claim_batch(
        payload,
        expected_group="Interfaces",
        expected_task_id=spec.target_id,
        expected_concern=spec.concern,
    )
    assert first.claims[0].section == "COMPONENTS"
    assert first.claims[0].claim_id == second.claims[0].claim_id


def test_evidence_planner_can_force_one_canonical_unit_per_task(tmp_path):
    group_file = tmp_path / "Interfaces.md"
    group_file.write_text(
        "# group\n\n"
        "## Talend Job: Inbound\n\nInbound evidence\n\n"
        "## Talend Route: Dispatch\n\nDispatch evidence\n",
        encoding="utf-8",
    )

    tasks = plan_evidence_tasks(
        "Interfaces",
        group_file,
        ["Inbound", "Dispatch"],
        tmp_path / "analysis",
        budget_bytes=1_000_000,
        max_units_per_task=1,
    )

    assert [task.units for task in tasks] == [["Inbound"], ["Dispatch"]]
    assert all(len(task.all_units) == 1 for task in tasks)


@pytest.mark.asyncio
async def test_runtime_does_not_replay_provider_failure_with_full_context(tmp_path):
    runtime = AgentTaskRuntime(tmp_path, _facts())
    contract = AgentTaskContract(
        phase="phase2_group",
        target_id="Inbound__analysis",
        allowed_units=["Inbound"],
        allowed_groups=["Interfaces"],
        expected_artifact="phase2 evidence claim JSON",
        max_attempts=2,
    )
    calls = 0

    async def dispatch(task, tools):
        nonlocal calls
        calls += 1
        return ModelResult(success=False, raw_error="transport unavailable")

    result = await runtime.run(
        contract,
        dispatch,
        lambda text: {"ok": False, "errors": ["empty response"]},
    )

    assert calls == 1
    assert not result.accepted
