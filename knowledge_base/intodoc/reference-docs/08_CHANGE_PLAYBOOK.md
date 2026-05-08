# Change Playbook

## Change Impact
Use this playbook to turn scanner evidence into practical inspect, test/verify, rollback, and affected-unit guidance. Every row should name confidence and evidence boundaries.

| Change Target | Inspect First | Affected Units | Test/Verify | Rollback Risk | Confidence |
|---------------|---------------|----------------|-------------|---------------|------------|
| Mapping from `{source_entity}` to `{target_entity}` | `{source_artifact}`, lineage table, rule table | `{unit_name}` plus visible downstream consumers | Test static lineage, one happy path, one malformed input, downstream output | MEDIUM if replay can duplicate writes | `{confidence}` |
| Shared `{platform_component}` config | Config table, external systems table, dependency inventory | All units referencing the component in `{evidence_artifact}` | Verify config resolution and one representative consumer per dependency group | HIGH if rollback changes credentials or endpoints globally | MEDIUM until full consumer list is proven |
| Schema or entity change | Lineage, validation rules, target contracts | `{unit_name}`, `{target_entity}` consumers | Verify schema compatibility, null/default behavior, reject path | HIGH if target has already accepted incompatible data | LOW without runtime contract evidence |
| External dependency change | Config, credentials, connector references | Units using `{source_entity}` or `{target_entity}` | Verify auth, reachability, rate limits, failure handling | MEDIUM to HIGH depending on shared usage | MEDIUM |

## Inspect
Before changing `{unit_name}`, inspect `{source_artifact}` for direct expressions, `{evidence_artifact}` for normalized dependencies, and the scorecard for scanner-limited risks. If `{target_entity}` is shared, inspect all consumers before treating the change as isolated.

Mapping inspect exemplar: "Inspect the exact `{source_entity}` to `{target_entity}` path, including intermediate values, hardcoded literals, validation rules, and downstream references. Confidence is HIGH only when each link has evidence."

Config inspect exemplar: "Inspect whether `{platform_component}` is local to `{unit_name}` or shared. If the consumer list is scanner-limited, mark affected-unit scope as MEDIUM or LOW confidence."

## Test And Verify
Test the exact changed behavior, not just the edited file. Include static checks, targeted execution where available, and downstream verification.

| Change Type | Required Checks | Evidence-Limited Wording |
|-------------|-----------------|--------------------------|
| Mapping | Static lineage, happy path, malformed input, target output | "Verification is limited to static evidence until a runtime run is supplied." |
| Shared config | Config resolution, representative consumers, external response | "Full blast radius is unknown until all consumers are proven." |
| Schema/entity | Compatibility, defaults, validation, downstream contract | "Runtime data shape is scanner-limited." |

Example test wording: "Verify `{source_entity}` still maps to `{target_entity}` through `{unit_name}`, then confirm `{platform_component}` config resolves in the target environment."

## Rollback
Rollback is low risk when the change only affects a local expression in `{unit_name}` and no side effects have been written. Rollback is higher risk when `{target_entity}` has already received data, `{platform_component}` is shared, or retry/replay behavior is scanner-limited.

Rollback exemplar: "If rollback replays writes to `{target_entity}`, inspect idempotency evidence first. If idempotency is unknown, require an operator-approved repair plan rather than promising reversibility."

## Confidence And Unknowns
Use HIGH confidence for direct source evidence, MEDIUM for inferred scope, and LOW for runtime-dependent claims. Affected-unit lists are scanner-limited when the export may omit consumers, deployment overlays, or external schedules.

Final playbook wording: "Proceed only after inspect/test/rollback steps are tied to `{source_artifact}` or `{evidence_artifact}`. Unknown owner, missing run logs, and unproven consumer lists should be carried into the change record."

Change-record exemplar: "Affected-unit scope for `{unit_name}` is MEDIUM confidence because `{platform_component}` appears shared; verify consumers before implementation and keep rollback approval explicit."
