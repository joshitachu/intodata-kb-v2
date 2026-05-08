# Operations Testing And Debugging

## Run Behavior
Describe normal execution order, prerequisites, schedules, triggers, and runtime dependencies only to the extent evidence supports them.

Example run wording: "`{unit_name}` can be described as runnable only to the extent exposed by `{source_artifact}`. If the scanner shows a trigger but not execution history, write: 'Schedule/trigger evidence is HIGH; operational success rate is scanner-limited.'"

Runtime boundary: Static source evidence does not prove a successful run, current deployment, incident rate, row counts, or operator procedure. Treat those as unknowns until runtime evidence is supplied.

## Failure Scenarios
| Symptom | Likely Cause | Inspect | Affected Units | Evidence | Confidence |
|---------|--------------|---------|----------------|----------|------------|
| `{target_entity}` missing or stale | Input unavailable, mapping skipped, config unresolved, or external dependency failed | `{source_artifact}`, `{evidence_artifact}`, runtime logs if available | `{unit_name}` | Static evidence plus scanner-limited runtime state | `{confidence}` |
| `{unit_name}` cannot start | Missing config, credential reference, trigger mismatch | Config table and deployment metadata | `{unit_name}` and shared `{platform_component}` consumers | `{source_artifact}` | MEDIUM |
| Duplicate output | Retry, replay, or rollback side effect | Run history and target idempotency evidence | `{target_entity}` consumers | Runtime artifact | LOW until observed |

Example failure wording: "If `{target_entity}` is not updated, first inspect whether `{unit_name}` received `{source_entity}`, then verify `{platform_component}` config and any dependency named in `{evidence_artifact}`."

## Debug And Verify
| Verify | How | Evidence | Confidence |
|--------|-----|----------|------------|
| Source-to-target path | Confirm `{source_entity}` maps to `{target_entity}` in `{source_artifact}` | `{evidence_artifact}` | HIGH for static lineage |
| Config resolution | Check setting reference and environment override | `{source_artifact}` plus deployment evidence | MEDIUM |
| Runtime completion | Check run log, status event, row count, or output artifact | Runtime evidence outside scanner | LOW until provided |

Debug phrasing: "Inspect the smallest evidence-backed path first: entrypoint, source read, transformation, target write, external response, then retry/recovery behavior."

## Recovery
Document restart, retry, rollback, and data repair behavior. Mark scanner-limited procedures clearly.

Example recovery wording: "Restart or retry behavior for `{unit_name}` is scanner-limited unless `{source_artifact}` declares retry policy or error handling. If rollback may resend data to `{target_entity}`, describe the side effect and require an operator check before replay."

## Testing
| Test Type | Use For | Minimum Evidence | Gap Language |
|-----------|---------|------------------|--------------|
| Static lineage comparison | Mapping or schema changes | `{source_artifact}` and `{evidence_artifact}` | Does not prove runtime success |
| Happy-path run | Normal source-to-target behavior | Runtime execution artifact | Unknown until executed |
| Failure-path run | Invalid input or dependency outage | Test or controlled runtime evidence | Scanner-limited if absent |

Testing-gap phrasing: "No test artifact proves `{unit_name}` handles malformed `{source_entity}` values. Recommended checks: static lineage comparison, config resolution, one happy-path run, and one failure-path run. Confidence remains MEDIUM until runtime evidence confirms scanner-derived behavior."
