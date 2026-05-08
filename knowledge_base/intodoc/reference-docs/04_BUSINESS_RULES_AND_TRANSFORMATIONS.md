# Business Rules And Transformations

## Business Rules
Capture business rules only when source evidence shows a condition, expression, mapping, route, default, filter, lookup, or policy-like configuration. Keep inferred business meaning separate from observed mechanics.

| Rule ID | Expression Or Condition | Unit | Scope | Impact | Confidence |
|---------|-------------------------|------|-------|--------|------------|
| BR-{unit_name}-01 | Condition copied from `{source_artifact}` with sensitive values masked | `{unit_name}` | `{platform_component}` | MODERATE if `{target_entity}` has known consumers | `{confidence}` |
| BR-{unit_name}-02 | Inferred from adjacent mapping steps | `{unit_name}` | Unknown | ISOLATED until shared usage is proven | MEDIUM |

Example rule wording: "BR-{unit_name}-01 applies when `{source_entity}` meets the condition visible in `{source_artifact}` and sets or routes `{target_entity}`. Confidence is HIGH if the condition is explicit, MEDIUM if assembled from adjacent steps, and LOW if the scanner exposes labels without expressions."

## Transformation Chains
Trace complex chains from input through intermediate values to output. Do not collapse multiple steps into a conclusion unless each link has evidence.

Example chain wording: "`{source_entity}` feeds an intermediate value in `{unit_name}`, then `{platform_component}` writes `{target_entity}`. The chain is evidence-backed by `{source_artifact}`; business intent remains scanner-limited unless named in the artifact."

| Step | Input | Operation | Output | Evidence | Confidence |
|------|-------|-----------|--------|----------|------------|
| 1 | `{source_entity}` | Normalize, filter, or lookup | Intermediate value | `{source_artifact}` | HIGH if expression is visible |
| 2 | Intermediate value | Map through `{platform_component}` | `{target_entity}` | `{evidence_artifact}` | MEDIUM if link is inferred |

## Hardcoded Values
| Value | Location | Apparent Purpose | Staleness Risk | Unknown |
|-------|----------|------------------|----------------|---------|
| Masked literal or `{source_entity}` token | `{source_artifact}` in `{unit_name}` | Classify, route, filter, or default `{target_entity}` | MEDIUM unless owner and refresh process are documented | Owner and valid value set |
| Fixed endpoint/path/code | `{platform_component}` config | Environment or routing control | HIGH if shared across units | Override behavior |

Hardcoded-value wording: "The value is evidence-backed by `{source_artifact}`, but its business owner is unknown. If it controls routing or filtering, describe impact as WIDE only when downstream `{target_entity}` dependencies are visible in `{evidence_artifact}`."

## Impact Levels
| Impact | Use When | Example |
|--------|----------|---------|
| ISOLATED | Rule affects only `{unit_name}` and no shared dependency is visible. | Local expression change |
| MODERATE | `{target_entity}` has visible downstream consumers. | Mapping or validation change |
| WIDE | Rule, lookup, config, or `{platform_component}` is shared. | Shared routing table |

Conflict wording: "`{source_artifact}` and `{evidence_artifact}` disagree about the rule target. Treat the rule as MEDIUM confidence, name both artifacts, and list the conflict as an unknown rather than choosing one silently."

## Evidence Boundaries
If no rule is visible, say "No business rule was found in scanned evidence for `{unit_name}`." Do not turn naming conventions into policy claims.
