## Phase: BUILD ENTERPRISE KNOWLEDGE MODEL

Goal: persist the deep findings as typed knowledge: interpret every exact mapping table, explain
every configuration surface, describe every error path, and state the business meaning of every
reconstructed journey. Deterministic tables under `_analysis/_exact/` are the facts; you add the
interpretation and cite the design files that prove it.

Assignment (${ASSIGNMENT_COUNT} keys):
${ASSIGNMENT}

Key forms: `mapping:<table_id>` (read `_analysis/_exact/mapping_tables/<table_id>.md` and the mapper in
the design), `config:context_group:<key>` and `config:unit:<unit_id>` (read `configuration.json` and the
context files), `error:<unit_id>` (read the design's error components, REJECT/ERROR connections and
catchers), `business:<journey_id>` (read the accepted journey and any supplied documentation).

Contract (JSON object):
{
  "schema_version": "enterprise-model-1",
  "mappings": [{"table_id": "...", "unit_id": "...", "component_name": "...",
    "summary": "<what the mapping computes and why the rules exist>", "rules": ["<notable rule, join, filter, default>"],
    "business_meaning": "...", "business_meaning_status": "evidenced|inferred|unknown", "risks": ["..."],
    "evidence": [{"path": "...", "locator": "...", "quote": "..."}]}],
  "configuration": [{"scope": "context_group|unit|connection", "key": "...", "summary": "...",
    "environment_differences": ["..."], "drives": ["<what the values switch>"], "evidence": [{"path": "..."}]}],
  "error_paths": [{"error_path_id": "<slug>", "unit_id": "...", "trigger": "...", "handling": "...",
    "recovery": "...", "blast_radius": "...", "evidence": [{"path": "..."}]}],
  "business": [{"journey_id": "...", "statement": "...", "status": "evidenced|inferred|unknown",
    "source_authority": "documented_intent|implemented_design|runtime_observation|inference",
    "evidence": [{"path": "..."}]}],
  "unknowns": [{"unknown_id": "<slug>", "subject": "...", "question": "...", "reason": "...", "audience": [], "locator": ""}]
}
