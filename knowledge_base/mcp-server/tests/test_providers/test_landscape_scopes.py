from __future__ import annotations

import json

import pytest

from mcp_server.providers.custom.knowledge_contracts import (
    LANDSCAPE_ROOT,
    KnowledgeEvidence,
    KnowledgeManifest,
    KnowledgePlan,
    KnowledgeRequirement,
    digest,
)
from mcp_server.providers.custom.landscape_scopes import prepare_semantic_scopes


def _evidence(evidence_id: str, owner: str, text: str) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        evidence_id=evidence_id,
        snapshot_id="snapshot",
        owner_id=owner,
        source_path="scan_result.json",
        locator=f"/{evidence_id}",
        text=text,
        sha256=digest(text),
    )


def _plan(requirements, evidence) -> KnowledgePlan:
    manifest = KnowledgeManifest(
        snapshot_id="snapshot",
        project_id="project",
        requirements=requirements,
        evidence=evidence,
    )
    return KnowledgePlan(snapshot_id="snapshot", tasks=[], manifest=manifest)


def test_scopes_preserve_evidence_gaps_and_write_canonical_manifest(tmp_path):
    evidence = {
        "ev-a": _evidence("ev-a", "A", '{"kind":"component"}'),
        "ev-b": _evidence("ev-b", "B", '{"kind":"component"}'),
    }
    requirements = [
        KnowledgeRequirement(
            requirement_id="req-a",
            unit_id="A",
            group="G",
            concern="component_behavior",
            description="Explain A",
            evidence_ids=["ev-a"],
        ),
        KnowledgeRequirement(
            requirement_id="req-b",
            unit_id="B",
            group="G",
            concern="component_behavior",
            description="Explain B",
            evidence_ids=["ev-b"],
        ),
        KnowledgeRequirement(
            requirement_id="gap",
            unit_id="A",
            group="G",
            concern="routine_resolution",
            description="Unresolved routine",
            extraction_status="unresolved",
            gap_stage="resolution",
            gap_reason="No canonical target",
        ),
    ]
    result = prepare_semantic_scopes(
        staging_dir=tmp_path,
        atomic_plan=_plan(requirements, evidence),
        max_scope_evidence_bytes=10_000,
        evidence_page_bytes=2_000,
        max_units_per_scope=2,
    )

    assert result.manifest.evidence == evidence
    assert {eid for task in result.tasks for eid in task.evidence_ids} == set(evidence)
    raw = {
        requirement.requirement_id: requirement
        for requirement in result.manifest.requirements
        if not requirement.requirement_id.startswith("sem-")
    }
    assert raw["req-a"].explanation_required is False
    assert raw["gap"].explanation_required is True
    canonical = KnowledgeManifest.model_validate_json(
        (tmp_path / LANDSCAPE_ROOT / "requirements.json").read_text()
    )
    assert canonical == result.manifest
    receipt = json.loads(
        (tmp_path / LANDSCAPE_ROOT / "semantic_scope_receipt.json").read_text()
    )
    assert receipt["assigned_evidence_count"] == len(evidence)
    assert receipt["evidence_assignment_count"] >= len(evidence)
    assert receipt["repeated_evidence_assignments"] == (
        receipt["evidence_assignment_count"] - len(evidence)
    )

    for task in result.tasks:
        payload = json.loads((tmp_path / task.input_artifact).read_text())
        assert payload["evidence_artifacts"]
        assert "unit_index" not in payload
        for page in payload["evidence_pages"]:
            path = tmp_path / page["path"]
            assert path.is_file()
            assert page["evidence_bytes"] == path.stat().st_size


def test_scopes_order_scanner_resolved_same_group_dependencies_together(tmp_path):
    evidence = {
        "ev-a": _evidence(
            "ev-a", "A", '{"resolution":"resolved","resolved_job_id":"C"}'
        ),
        "ev-b": _evidence("ev-b", "B", '{"kind":"identity"}'),
        "ev-c": _evidence("ev-c", "C", '{"kind":"identity"}'),
    }
    requirements = [
        KnowledgeRequirement(
            requirement_id="req-a",
            unit_id="A",
            group="G",
            concern="dependencies",
            description="A calls C",
            evidence_ids=["ev-a"],
        ),
        KnowledgeRequirement(
            requirement_id="req-b",
            unit_id="B",
            group="G",
            concern="identity",
            description="B",
            evidence_ids=["ev-b"],
        ),
        KnowledgeRequirement(
            requirement_id="req-c",
            unit_id="C",
            group="G",
            concern="identity",
            description="C",
            evidence_ids=["ev-c"],
        ),
    ]
    result = prepare_semantic_scopes(
        staging_dir=tmp_path,
        atomic_plan=_plan(requirements, evidence),
        max_scope_evidence_bytes=10_000,
        evidence_page_bytes=2_000,
        max_units_per_scope=2,
    )
    assert result.tasks[0].unit_ids == ["A", "C"]
    first_payload = json.loads((tmp_path / result.tasks[0].input_artifact).read_text())
    assert first_payload["related_units"] == {"A": ["C"], "C": ["A"]}


def test_page_target_overflow_is_explicit_for_one_indivisible_record(tmp_path):
    evidence = {"ev": _evidence("ev", "A", "x" * 1_500)}
    requirement = KnowledgeRequirement(
        requirement_id="req",
        unit_id="A",
        group="G",
        concern="routine_behavior",
        description="Routine",
        evidence_ids=["ev"],
    )
    result = prepare_semantic_scopes(
        staging_dir=tmp_path,
        atomic_plan=_plan([requirement], evidence),
        max_scope_evidence_bytes=10_000,
        evidence_page_bytes=1_024,
    )
    payload = json.loads((tmp_path / result.tasks[0].input_artifact).read_text())
    assert payload["evidence_pages"][0]["over_page_target"] is True


def test_scope_planner_fails_closed_on_orphaned_or_oversized_evidence(tmp_path):
    orphan = {"ev": _evidence("ev", "A", "orphan")}
    with pytest.raises(ValueError, match="orphaned evidence"):
        prepare_semantic_scopes(
            staging_dir=tmp_path,
            atomic_plan=_plan([], orphan),
            max_scope_evidence_bytes=10_000,
            evidence_page_bytes=2_000,
        )

    evidence = {"ev": _evidence("ev", "A", "x" * 5_000)}
    requirement = KnowledgeRequirement(
        requirement_id="req",
        unit_id="A",
        group="G",
        concern="identity",
        description="Identity",
        evidence_ids=["ev"],
    )
    with pytest.raises(ValueError, match="re-fragment"):
        prepare_semantic_scopes(
            staging_dir=tmp_path / "large",
            atomic_plan=_plan([requirement], evidence),
            max_scope_evidence_bytes=1_024,
            evidence_page_bytes=1_024,
        )
