## Phase: RECONCILE CODE / DESIGN / RUNTIME

Goal: compare the three authorities without conflating them. Exported code proves implemented
design; `_analysis/_runtime_authority.json` proves observations within an environment and time
window (or states that no runtime source was available); `_analysis/_noncode_project_context.md`,
when present, proves stated intent. Record every disagreement as a contradiction with both
authorities cited, and record confirmations where they agree. Missing runtime data is unavailable,
never proof of non-execution.

Assignment: ${ASSIGNMENT}

Contract (JSON object):
{
  "schema_version": "enterprise-reconcile-1",
  "contradictions": [{"contradiction_id": "<slug>", "subject": "<unit, journey or system>",
    "authorities": ["implemented_design", "runtime_observation"], "statements": ["<what code says>", "<what runtime says>"],
    "resolution": "open|favours_code|favours_runtime|favours_documentation",
    "evidence": [{"path": "...", "locator": "...", "quote": "..."}]}],
  "confirmations": ["<short statement of an agreement worth recording>"],
  "unknowns": [{"unknown_id": "<slug>", "subject": "...", "question": "...", "reason": "...", "audience": [], "locator": ""}]
}

Submit exactly one object even when the contradictions list is empty.
