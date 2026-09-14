from __future__ import annotations

import json
from pathlib import Path

from mcp_server.providers.custom.knowledge_contracts import (
    KnowledgeCitation,
    KnowledgeClaim,
    KnowledgeClaimBatch,
    persist_knowledge_batch,
    validate_knowledge_batch,
)
from mcp_server.providers.custom.knowledge_requirements import prepare_landscape
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.talend_landscape_eval import evaluate_talend_landscape


EXPECTED = Path(__file__).parents[1] / "fixtures" / "talend_landscape" / "expected.json"


def test_offline_eval_detects_retention_coverage_and_source_accounting(tmp_path):
    scan_dir = tmp_path / "scan"
    (scan_dir / "scan").mkdir(parents=True)
    entries = [
        {"table_name": "out", "column_name": f"C{i}", "expression": f"row.C{i} + {i}"}
        for i in range(40)
    ]
    scan = {
        "platform": "talend",
        "jobs": [{"job_id": "root::job", "name": "Job", "group": "G",
                  "path": "process/G/Job.item", "source_root": "root",
                  "components": [], "connections": [],
                  "tmap_data": [{"entries": entries}]}],
        "routines": [{"routine_id": "root::routine", "name": "Rules",
                      "source_root": "root", "path": "code/routines/Rules.item",
                      "source_complete": True,
                      "source_code": "\n".join([f"// {i}" for i in range(220)])
                                     + "\nreturn decisiveRule(value);"}],
        "source_inventory": [
            {"path": "process/G/Job.item", "outcome": "indexed", "reason": "Talend design parsed"},
            {"path": "code/routines/Rules.item", "outcome": "indexed", "reason": "Talend routine parsed"},
        ],
    }
    (scan_dir / "scan_result.json").write_text(json.dumps(scan), encoding="utf-8")
    (scan_dir / "scan" / "manifest.json").write_text(json.dumps({
        "job_routine_links": {"root::job": [{"routine_id": "root::routine",
            "source_root": "root", "resolution": "same_root"}]},
    }), encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    facts = FactsManifest(project_id="p", platform="talend", canonical_unit_label="Jobs",
        unit_count=1, groups={"G": 1}, units=[UnitFact(name="root::job", display_name="Job",
        group="G", path="process/G/Job.item", source_root="root")])
    plan = prepare_landscape(staging_dir=staging, scan_output_dir=scan_dir, facts=facts)
    for task in plan.tasks:
        claims = []
        for requirement_id in task.requirement_ids:
            requirement = next(r for r in plan.manifest.requirements if r.requirement_id == requirement_id)
            evidence = plan.manifest.evidence[requirement.evidence_ids[0]]
            claims.append(KnowledgeClaim(section="TECHNICAL", kind="fact",
                unit_names=[requirement.unit_id], statement="Fixture fact retained.",
                requirement_ids=[requirement_id], citations=[KnowledgeCitation(
                    evidence_id=evidence.evidence_id, quote=evidence.text[:20])]))
        raw = KnowledgeClaimBatch(snapshot_id=plan.snapshot_id, task_id=task.task_id,
            group=task.group, claims=[claim.model_dump(mode="json") for claim in claims])
        persist_knowledge_batch(staging, task,
            validate_knowledge_batch(raw.model_dump_json(), task, plan.manifest))
    (staging / "01_OVERVIEW.md").write_text("# Overview\n", encoding="utf-8")

    report = evaluate_talend_landscape(staging, EXPECTED)

    assert report["ok"] is True
    assert report["counts"]["mandatory"] == report["counts"]["mandatory_addressed"]


def test_offline_eval_fails_when_a_known_fact_and_inventory_outcome_are_lost(tmp_path):
    root = tmp_path / "staging"
    landscape = root / "_evidence_registry" / "landscape"
    landscape.mkdir(parents=True)
    (landscape / "requirements.json").write_text(json.dumps({
        "schema_version": "talend-knowledge-v2", "snapshot_id": "s", "project_id": "p",
        "requirements": [], "evidence": {},
    }), encoding="utf-8")
    (landscape / "source_inventory.json").write_text(
        json.dumps([{"path": "lost.item", "outcome": "", "reason": ""}]), encoding="utf-8")

    report = evaluate_talend_landscape(root, EXPECTED)

    assert report["ok"] is False
    assert "row.C39 + 39" in report["missing_exact_facts"]
    assert report["source_inventory_without_outcome"] == ["lost.item"]
