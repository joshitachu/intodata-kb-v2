# Agent Brief

## Project Orientation
Use this doc to onboard a doc agent before it writes customer-facing documentation. Keep the mental model source-bound: `{unit_name}` is a canonical unit only when it is declared in `{source_artifact}` or normalized into `{evidence_artifact}`.

Example orientation: Treat `{unit_name}` as the safest working boundary because the scan shows it contains `{platform_component}` and references `{source_entity}`. Confidence is HIGH for the unit name and direct references, MEDIUM for purpose inferred from names, and LOW for runtime behavior that is not represented in the export.

Scanner-limited boundary: Do not infer owner, SLA, schedule reliability, incident history, data volume, production status, or business criticality unless an `{evidence_artifact}` states it. Write unknowns plainly rather than filling gaps with generic domain assumptions.

## Documentation Map
| Task | Read First | Then Read | Expected Evidence |
|------|------------|-----------|-------------------|
| Understand topology | 01_ARCHITECTURE_MAP.md | 02_COMPONENT_INVENTORY.md | Units, boundaries, dependencies |
| Trace data | 03_DATA_FLOWS_AND_LINEAGE.md | 04_BUSINESS_RULES_AND_TRANSFORMATIONS.md | Source, target, expressions |
| Debug a run | 06_OPERATIONS_TESTING_AND_DEBUGGING.md | 05_RUNTIME_CONFIG_AND_EXTERNALS.md | Trigger, config, logs if available |
| Plan a change | 08_CHANGE_PLAYBOOK.md | 07_QUALITY_SECURITY_RISK_SCORECARD.md | Impact, rollback, risk |

## Glossary Style
| Term | Use It When | Avoid |
|------|-------------|-------|
| `{unit_name}` | The artifact is the scanner-visible deployable or logical unit. | Renaming it to a business-friendly label. |
| `{platform_component}` | The component is visible in source or normalized facts. | Claiming implementation details not exposed by evidence. |
| `{source_entity}` / `{target_entity}` | A flow endpoint is explicit in source, config, schema, or lineage. | Treating a filename as a proven entity without corroboration. |

Good wording: "`{unit_name}` appears to route `{source_entity}` to `{target_entity}` through `{platform_component}`. The route is evidence-backed by `{source_artifact}`; business intent is MEDIUM confidence because it is inferred from structure."

## Common Tasks
Changing a mapping: Inspect `{source_artifact}`, find every `{source_entity}` to `{target_entity}` reference in lineage, then use the playbook to test and rollback. If no executable tests are visible, write: "Testing confidence is LOW; verification is limited to static evidence from `{evidence_artifact}`."

Debugging a failure: Start with the unit, then config, then externals. Phrase it as: "If `{target_entity}` is stale, inspect whether `{unit_name}` received `{source_entity}`, whether `{platform_component}` resolved its config, and whether runtime logs exist outside the scanner bundle."

Assessing risk: Tie each risk to evidence and unknowns. Do not say a system is secure, performant, or stable because the source compiles or the scan is clean; those claims need runtime, deployment, or policy evidence.

## Known Traps
| Severity | Trap | Evidence | Affected Unit | Read Next |
|----------|------|----------|---------------|-----------|
| HIGH | Shared `{platform_component}` may affect more units than the local file suggests. | `{evidence_artifact}` | `{unit_name}` | 08_CHANGE_PLAYBOOK.md |
| MEDIUM | Purpose is inferred from names only. | `{source_artifact}` | `{unit_name}` | 02_COMPONENT_INVENTORY.md |
| LOW | Redacted credential values are intentionally hidden. | `{source_artifact}` | `{platform_component}` | 05_RUNTIME_CONFIG_AND_EXTERNALS.md |

## Read Next Guidance
If evidence conflicts, read the source artifact first, then the normalized fact, then any runtime artifact. If a conflict remains, preserve both claims and label the conclusion MEDIUM or LOW confidence.

If a requested fact is not scanner-visible, say: "Unknown from scanned evidence. Inspect `{evidence_artifact}` or runtime records before making a production claim."
