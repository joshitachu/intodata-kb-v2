from __future__ import annotations

from mcp_server.providers.custom.document_reconciliation import reconcile_landscape_documents
from mcp_server.providers.custom.knowledge_contracts import (
    LANDSCAPE_ROOT, KnowledgeClaim, KnowledgeManifest, KnowledgeRequirement,
    persist_knowledge_batch, write_json, KnowledgeBatchValidation, KnowledgeTask,
)


def test_reconciliation_separates_exact_reference_retention_from_prose(tmp_path):
    staging = tmp_path / "staging"
    requirement = KnowledgeRequirement(
        requirement_id="req-1", unit_id="unit-1", group="G", concern="transformation",
        description="Preserve expression", evidence_ids=[],
        output_destinations=["_evidence_registry/doc_slices/unit.md#req-1"],
    )
    manifest = KnowledgeManifest(snapshot_id="snapshot", project_id="p",
                                 requirements=[requirement], evidence={})
    write_json(staging / LANDSCAPE_ROOT / "requirements.json", manifest)
    reference = staging / "_evidence_registry" / "doc_slices" / "unit.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("# Exact reference\n<a id=\"req-1\"></a>\n", encoding="utf-8")
    (reference.parent / "landscape_index.md").write_text("# Index\n", encoding="utf-8")
    task = KnowledgeTask(task_id="task", snapshot_id="snapshot", group="G",
                         unit_ids=["unit-1"], requirement_ids=["req-1"], evidence_ids=[],
                         input_artifact="in.json", output_artifact="out.json")
    claim = KnowledgeClaim(section="BUSINESS_RULES", unit_names=["unit-1"],
                           statement="The expression determines the output.",
                           requirement_ids=["req-1"], claim_id="claim-1", review_state="grounded")
    persist_knowledge_batch(staging, task, KnowledgeBatchValidation(accepted=[claim]))
    (staging / "03_DATA_FLOWS.md").write_text("# Data flows\n\nNo interpreted rule.\n", encoding="utf-8")

    missing = reconcile_landscape_documents(staging)
    assert missing["missing_reference_requirement_ids"] == []
    assert missing["missing_claim_ids"] == ["claim-1"]
    assert missing["ok"] is False

    (staging / "03_DATA_FLOWS.md").write_text(
        "# Data flows\n\nThe expression determines the output. <!-- claim:claim-1 -->\n",
        encoding="utf-8",
    )
    retained = reconcile_landscape_documents(staging)
    assert retained["ok"] is True
    assert retained["retained_claim_count"] == 1
