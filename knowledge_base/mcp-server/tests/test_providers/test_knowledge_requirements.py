from __future__ import annotations

import json

from mcp_server.providers.custom.knowledge_contracts import (
    KnowledgeClaimBatch, KnowledgeCitation, KnowledgeClaim,
    validate_knowledge_batch,
)
from mcp_server.providers.custom.knowledge_requirements import prepare_landscape
from mcp_server.providers.custom.models import FactsManifest, UnitFact
from mcp_server.providers.custom.flow_analysis import build_source_journeys


def _facts() -> FactsManifest:
    return FactsManifest(
        project_id="p", platform="talend", canonical_unit_label="Talend jobs",
        unit_count=1, groups={"G": 1},
        units=[UnitFact(name="root::job-1", display_name="Job", group="G",
                        path="process/G/Job.item", source_root="root")],
    )


def _scan() -> dict:
    entries = [
        {"table_name": "out", "column_name": f"C{i}", "expression": f"row.C{i} + {i}"}
        for i in range(40)
    ]
    return {
        "platform": "talend",
        "jobs": [{"job_id": "root::job-1", "name": "Job", "group": "G",
                  "path": "process/G/Job.item", "source_root": "root",
                  "components": [], "connections": [],
                  "tmap_data": [{"entries": entries, "var_entries": [],
                                  "input_tables": ["row"], "output_tables": ["out"]}]}],
        "routines": [{"routine_id": "root::routine-1", "name": "Rules",
                      "source_root": "root", "path": "code/routines/Rules.item",
                      "source_complete": True,
                      "source_code": "\n".join([f"// {i}" for i in range(220)]) + "\nreturn decisiveRule(value);"}],
    }


def _prepare(tmp_path):
    scan_dir = tmp_path / "scan-output"
    scan_dir.mkdir()
    (scan_dir / "scan_result.json").write_text(json.dumps(_scan()), encoding="utf-8")
    (scan_dir / "scan").mkdir()
    (scan_dir / "scan" / "manifest.json").write_text(json.dumps({
        "job_routine_links": {"root::job-1": [{"routine_id": "root::routine-1",
            "name": "Rules", "source_root": "root", "resolution": "same_root"}]},
    }), encoding="utf-8")
    staging = tmp_path / "staging"
    (staging / "_analysis").mkdir(parents=True)
    (staging / "_analysis" / "_noncode_project_context.md").write_text(
        "## Approved operating note\n\nJob transfers approved orders to the warehouse.\n",
        encoding="utf-8",
    )
    return prepare_landscape(staging_dir=staging, scan_output_dir=scan_dir,
                             facts=_facts(), max_requirements_per_task=12,
                             max_evidence_bytes=60_000)


def test_dense_mapping_and_routine_tail_survive_as_bounded_requirements(tmp_path):
    plan = _prepare(tmp_path)
    mapping = [r for r in plan.manifest.requirements if r.concern == "transformation"]
    routine = [r for r in plan.manifest.requirements if r.concern == "routine_behavior"]
    assert len(mapping) >= 40
    assert routine
    assert any("decisiveRule" in plan.manifest.evidence[e].text
               for requirement in routine for e in requirement.evidence_ids)
    assert all(len(task.requirement_ids) <= 12 for task in plan.tasks)
    refs = tmp_path / "staging" / "_evidence_registry" / "doc_slices"
    assert (refs / "landscape_index.md").is_file()
    assert "decisiveRule" in "".join(p.read_text() for p in refs.glob("*.md"))
    documented = [r for r in plan.manifest.requirements if r.concern == "documented_context"]
    assert documented and documented[0].mandatory is False
    doc_evidence = plan.manifest.evidence[documented[0].evidence_ids[0]]
    assert doc_evidence.authority == "documented_intent"


def test_claim_validation_requires_each_requirements_own_exact_quote(tmp_path):
    plan = _prepare(tmp_path)
    task = plan.tasks[0]
    requirement = next(r for r in plan.manifest.requirements if r.requirement_id == task.requirement_ids[0])
    evidence = plan.manifest.evidence[requirement.evidence_ids[0]]
    claim = KnowledgeClaim(
        section="BUSINESS_RULES", kind="fact", unit_names=[requirement.unit_id],
        statement="Explains the assigned behavior.", requirement_ids=[requirement.requirement_id],
        citations=[KnowledgeCitation(evidence_id=evidence.evidence_id, quote=evidence.text[:20])],
    )
    batch = KnowledgeClaimBatch(snapshot_id=plan.snapshot_id, task_id=task.task_id,
                                group=task.group, claims=[claim.model_dump(mode="json")])
    valid = validate_knowledge_batch(batch.model_dump_json(), task, plan.manifest)
    assert len(valid.accepted) == 1
    broken = claim.model_copy(update={"citations": [KnowledgeCitation(
        evidence_id=evidence.evidence_id, quote="text that is not present")]})
    invalid = batch.model_copy(update={"claims": [broken.model_dump(mode="json")]})
    result = validate_knowledge_batch(invalid.model_dump_json(), task, plan.manifest)
    assert result.accepted == []
    assert "does not exist" in result.errors[0]


def test_source_journey_preserves_branches_and_unresolved_calls(tmp_path):
    _prepare(tmp_path)
    scan_path = tmp_path / "scan-output" / "scan_result.json"
    scan = json.loads(scan_path.read_text())
    scan["jobs"][0]["connections"] = [
        {"source": "start", "target": "ok", "connector_name": "COMPONENT_OK", "name": "OnOk"},
        {"source": "start", "target": "error", "connector_name": "COMPONENT_ERROR", "name": "OnError"},
    ]
    scan["jobs"][0]["trunjob_refs"] = [
        {"child_job": "MissingChild", "resolved_job_id": "", "resolution": "unresolved"},
    ]
    scan_path.write_text(json.dumps(scan), encoding="utf-8")
    output = build_source_journeys(tmp_path / "staging", tmp_path / "scan-output")
    text = output.read_text()
    assert "[COMPONENT_OK; OnOk]" in text
    assert "[COMPONENT_ERROR; OnError]" in text
    assert "MissingChild` [resolution: unresolved]" in text
