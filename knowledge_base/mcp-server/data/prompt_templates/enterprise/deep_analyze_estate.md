## Phase: DEEP ANALYZE ESTATE

Goal: understand every design (job, route, joblet, routelet) in this session's assignment deeply
enough to explain it to four audiences, and record the systems it touches. Read each design's
`.item` file in `_workspace/` (its path is in `_analysis/_exact/units.json`), its `.properties`
sidecar, the context groups it binds, the routines it calls, the metadata it references, and the
callers and callees listed for it. Follow references across the workspace when they matter.

Assignment (${ASSIGNMENT_COUNT} designs this session must account for):
${ASSIGNMENT}

Contract (JSON object):
{
  "schema_version": "enterprise-estate-1",
  "designs": [{
    "unit_id": "<canonical id from units.json>",
    "kind": "job|route|joblet|routelet",
    "summary": "<what it does, technically, in 2-5 sentences>",
    "views": {
      "developer": "<mechanics: components, code, mappings, routines, conditions>",
      "operator": "<how it is triggered, what it needs at runtime, how it fails, what to watch>",
      "architect": "<dependencies, systems, coupling, boundaries>",
      "business_owner": "<business meaning, or 'unknown: <reason>'>"
    },
    "systems": ["<system names as the design names them>"],
    "business_purpose_status": "evidenced|inferred|unknown",
    "evidence": [{"path": "_workspace/...", "locator": "...", "quote": "..."}],
    "unknowns": ["<open question with its locator>"]
  }],
  "systems": [{"system_id": "<slug>", "name": "...", "kind": "database|rest|soap|messaging|file|saas|erp|other",
               "aliases": [], "evidence": [{"path": "...", "locator": "...", "quote": "..."}]}],
  "unknowns": [{"unknown_id": "<slug>", "subject": "<unit_id or system>", "question": "...", "reason": "...",
                "audience": ["developer"], "locator": "..."}]
}
