## Phase: WRITE CATALOGUE AND DOSSIERS

Goal: write client-readable prose for the planned documents from the validated knowledge model
(`_evidence_registry/enterprise/knowledge_model.json`, whose entries carry `claim_id` values) and the
document plan (`_evidence_registry/enterprise/dossier_plan.json`). Python inserts the exact tables,
flow tables, interface tables and runtime tables deterministically; you write the explanation around
them. Name Talend designs, systems and business objects. Never mention staging paths, registries,
evidence ids, claim ids, scanners or pipelines in the prose; cite claims only in `claim_ids`.

Assignment (${ASSIGNMENT_COUNT} documents):
${ASSIGNMENT}

Dossier sections (all required, each at least 160 characters except risks_and_open_questions):
overview, business_purpose, end_to_end_flow, mapping_notes, configuration, operations,
errors_and_recovery, risks_and_open_questions.
Catalogue sections: overview, how_to_read, estate_observations.

Contract (one JSON object per document):
{
  "schema_version": "enterprise-doc-sections-1",
  "doc": "<document name from the plan>",
  "sections": {
    "<section>": {"text": "<markdown prose; paragraphs and bullet lists only, no tables>", "claim_ids": ["ek-..."]}
  }
}
