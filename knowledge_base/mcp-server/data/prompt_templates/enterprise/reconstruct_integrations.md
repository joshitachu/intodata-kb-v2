## Phase: RECONSTRUCT END-TO-END INTEGRATIONS

Goal: follow every trigger through routes, jobs, joblets, resources, transformations, mappings,
destinations, errors and monitoring, and record the resulting journeys. Jobs are evidence inside a
journey, not products. Use `_analysis/_exact/dependencies.json` (call edges, roots), `interfaces.json`
(inbound and outbound interfaces), `mapping_accounting.json` (exact mapping table ids) and the
accepted design analyses as your map, then read the designs in `_workspace/` to confirm each hop.

Assignment (${ASSIGNMENT_COUNT} designs that must end up placed in a journey or excluded with a reason):
${ASSIGNMENT}

Every entry point (units.json `entry_point: true`) must start at least one journey. A design that is a
pure utility, an orphan, or a superseded copy is excluded with a stated reason instead of being
forced into a journey.

Contract (JSON object):
{
  "schema_version": "enterprise-journeys-1",
  "journeys": [{
    "journey_id": "<lowercase slug, unique>",
    "name": "<business-language name>",
    "trigger": {"kind": "scheduler|rest|soap|jms|file|timer|manual|route|unknown", "unit_id": "<entry design>", "detail": "..."},
    "steps": [{"order": 1, "unit_id": "...", "components": ["..."], "mechanism": "tRunJob|cTalendJob|jms|direct|rest|file|db|...",
               "description": "...", "evidence": [{"path": "...", "locator": "...", "quote": "..."}]}],
    "destinations": [{"system": "...", "mechanism": "...", "detail": "..."}],
    "business_object": "<what moves, e.g. work order, article master>",
    "business_capability": "<supplied capability name if documentation states one, else empty>",
    "implementing_units": ["<every design in the journey>"],
    "mapping_table_ids": ["<table ids from mapping_accounting.json>"],
    "error_path_ids": [],
    "monitoring": ["<log, catcher, dead-letter or metric the journey emits>"],
    "confidence": "certain|high|medium|low",
    "evidence": [{"path": "...", "locator": "...", "quote": "..."}]
  }],
  "excluded_units": [{"unit_id": "...", "reason": "..."}]
}
