"""Repair feedback follows persisted progress without accepting unknowns as coverage."""
from __future__ import annotations

import json

import pytest

from mcp_server.providers.custom.knowledge_contracts import (
    LANDSCAPE_ROOT, KnowledgeClaim, KnowledgeClaimBatch, KnowledgeCitation,
    KnowledgeEvidence, KnowledgeManifest, KnowledgeRequirement, KnowledgeTask,
    accepted_knowledge_claims, digest, render_knowledge_group, write_json,
)
from mcp_server.providers.opencode_landscape import submit_landscape_claims


@pytest.fixture
def contract(tmp_path):
    evidence = {
        f"e{i}": KnowledgeEvidence(
            evidence_id=f"e{i}", snapshot_id="snapshot", owner_id="job",
            source_path="job.item", locator=f"rule/{i}", text=f"row.value + {i}",
            sha256=digest(f"row.value + {i}"),
        ) for i in range(2)
    }
    manifest = KnowledgeManifest(
        snapshot_id="snapshot", project_id="project", evidence=evidence,
        requirements=[KnowledgeRequirement(
            requirement_id=f"r{i}", unit_id="job", group="group",
            concern="transformation", description=f"Explain rule {i}", evidence_ids=[f"e{i}"],
        ) for i in range(2)],
    )
    task = KnowledgeTask(
        task_id="task", snapshot_id="snapshot", group="group", unit_ids=["job"],
        requirement_ids=["r0", "r1"], evidence_ids=["e0", "e1"],
        input_artifact="packet.json", output_artifact="claims.json",
    )
    write_json(tmp_path / LANDSCAPE_ROOT / "requirements.json", manifest)
    return task, manifest


def claim(index, *, unknown=False, invalid=False):
    return KnowledgeClaim(
        section="BUSINESS_RULES", kind="unknown" if unknown else "fact",
        unit_names=["job"], statement=f"Rule {index} is {'unresolved' if unknown else 'explained'}.",
        requirement_ids=[f"r{index}"], citations=[] if unknown else [KnowledgeCitation(
            evidence_id=f"e{index}", quote="fabricated" if invalid else f"row.value + {index}",
        )],
    ).model_dump(mode="json")


def submit(staging, task, claims):
    batch = KnowledgeClaimBatch(snapshot_id=task.snapshot_id, task_id=task.task_id,
                                group=task.group, claims=claims)
    return submit_landscape_claims(staging, {"knowledge_task": task.model_dump()}, batch.model_dump_json())


def test_partial_repair_reports_cumulative_coverage_and_is_idempotent(tmp_path, contract):
    task, manifest = contract
    first = submit(tmp_path, task, [claim(0)])
    assert not first["ok"]
    assert first["unresolved_requirement_ids"] == ["r1"]
    repaired = submit(tmp_path, task, [claim(1)])
    assert repaired["ok"]
    assert repaired["unresolved_requirement_ids"] == []
    assert repaired["addressed_requirement_count"] == 2
    assert repaired["accepted_count"] == 1  # Per-submission count retains its meaning.
    replay = submit(tmp_path, task, [claim(1)])
    assert replay["ok"]
    assert len(accepted_knowledge_claims(tmp_path, manifest)) == 2


def test_unknown_does_not_resolve_and_successful_repair_supersedes_it(tmp_path, contract):
    task, manifest = contract
    first = submit(tmp_path, task, [claim(0), claim(1, unknown=True)])
    assert first["unresolved_requirement_ids"] == ["r1"]
    assert first["addressed_requirement_count"] == 1
    repaired = submit(tmp_path, task, [claim(1)])
    assert repaired["ok"]
    assert all(c.kind != "unknown" for c in accepted_knowledge_claims(tmp_path, manifest))


def test_invalid_submission_preserves_accepted_progress_and_current_errors(tmp_path, contract):
    task, _ = contract
    submit(tmp_path, task, [claim(0), claim(1)])
    invalid = submit(tmp_path, task, [claim(1, invalid=True)])
    assert not invalid["ok"]
    assert "quoted source text does not exist" in invalid["errors"][0]
    assert invalid["unresolved_requirement_ids"] == []
    assert invalid["addressed_requirement_count"] == 2
    malformed = submit_landscape_claims(tmp_path, {"knowledge_task": task.model_dump()}, "not json")
    assert not malformed["ok"]
    assert malformed["unresolved_requirement_ids"] == []
    assert malformed["errors"]


def test_followup_subset_reports_only_assigned_requirements(tmp_path, contract):
    task, manifest = contract
    submit(tmp_path, task, [claim(0)])
    followup = task.model_copy(update={"requirement_ids": ["r1"]})
    result = submit(tmp_path, followup, [claim(1)])
    assert result["ok"]
    assert result["addressed_requirement_count"] == 1
    assert len(accepted_knowledge_claims(tmp_path, manifest)) == 2


def test_stale_ledger_cannot_report_completion(tmp_path, contract):
    task, _ = contract
    submit(tmp_path, task, [claim(0)])
    path = next((tmp_path / LANDSCAPE_ROOT / "accepted").glob("*.json"))
    record = json.loads(path.read_text())
    record["snapshot_id"] = "stale"
    write_json(path, record)
    with pytest.raises(ValueError, match="snapshot/task mismatch"):
        submit(tmp_path, task, [claim(1)])


def test_semantic_claim_requires_complete_review_but_only_decisive_citation(tmp_path, contract):
    task, manifest = contract
    requirement = manifest.requirements[0].model_copy(update={
        "requirement_id": "sem-transform", "evidence_ids": ["e0", "e1"],
    })
    manifest = manifest.model_copy(update={"requirements": [requirement]})
    write_json(tmp_path / LANDSCAPE_ROOT / "requirements.json", manifest)
    task = task.model_copy(update={"requirement_ids": ["sem-transform"]})
    semantic_claim = claim(0)
    semantic_claim.update(requirement_ids=["sem-transform"], reviewed_evidence_ids=["e0"])
    incomplete = submit(tmp_path, task, [semantic_claim])
    assert not incomplete["ok"]
    assert "review of all assigned evidence" in incomplete["errors"][0]
    semantic_claim["reviewed_evidence_ids"] = ["e0", "e1", "unassigned"]
    outside = submit(tmp_path, task, [semantic_claim])
    assert not outside["ok"]
    assert "outside assigned evidence" in outside["errors"][0]
    semantic_claim["reviewed_evidence_ids"] = ["e0", "e1"]
    complete = submit(tmp_path, task, [semantic_claim])
    assert complete["ok"]
    assert complete["unresolved_requirement_ids"] == []
    persisted = accepted_knowledge_claims(tmp_path, manifest)[0]
    assert len(persisted.citations) == 1
    assert persisted.review_state == "grounded"


def test_retained_atomic_evidence_is_not_rendered_as_missing_explanation(tmp_path, contract):
    _, manifest = contract
    manifest.requirements[0].explanation_required = False
    rendered = render_knowledge_group(tmp_path, "group", manifest)
    assert "Explain rule 0" not in rendered
    assert "UNKNOWN: job: Explain rule 1" in rendered
