# Quality Security Risk Scorecard

## Score
Score rows must be evidence-backed and confidence-labeled. A score is not a verdict on the whole platform; it is a compact assessment of scanner-visible quality, security, maintainability, operability, and change risk.

| Dimension | Score | Confidence | Evidence | Unknowns |
|-----------|-------|------------|----------|----------|
| Maintainability | 3/5 | `{confidence}` | `{unit_name}` has visible dependencies in `{source_artifact}` and normalized references in `{evidence_artifact}` | Ownership, change frequency, defect history |
| Security | 3/5 | MEDIUM | `{source_artifact}` shows redacted credential reference for `{platform_component}` | Rotation, permissions, secret strength |
| Operability | 2/5 | LOW | Trigger or config is visible, but runtime logs are absent | Run success, SLA, alerting |

Example score wording: "The maintainability score is based on visible dependency count and shared `{platform_component}` usage. It does not assume code quality beyond the scanner-visible structure."

## Confidence
Use HIGH when a score is tied to direct source facts, MEDIUM when structure is visible but intent is inferred, and LOW when the claim depends on runtime behavior, operator practice, deployment state, or policy evidence not present in the scan.

Evidence sentence: "The security score is based on `{source_artifact}` showing a redacted credential reference used by `{unit_name}` and `{evidence_artifact}` confirming the dependency. The score does not assume credential rotation, secret strength, least-privilege access, or production isolation."

## Red Flags
| Severity | Affected Units | Evidence | Risk | Remediation | Effort |
|----------|----------------|----------|------|-------------|--------|
| HIGH | `{unit_name}` | `{source_artifact}` references shared `{platform_component}` with scanner-limited consumers | A change may affect more flows than the visible inventory shows | Confirm all consumers, then add targeted regression checks | MEDIUM |
| MEDIUM | `{target_entity}` path | `{evidence_artifact}` lacks validation rule | Bad or malformed data may pass through | Add validation and failure-path test | LOW |
| LOW | `{unit_name}` docs | Purpose inferred from artifact name | Onboarding may be slower | Add owner note or purpose comment | LOW |

Red flag phrasing: "This is a red flag because evidence is incomplete or blast radius is uncertain, not because the implementation is proven defective."

## Quick Wins
| Quick Win | Affected Unit | Evidence | Expected Impact | Confidence |
|-----------|---------------|----------|-----------------|------------|
| Add explicit validation for `{target_entity}` | `{unit_name}` | Missing validation in `{evidence_artifact}` | Reduces data-quality risk and improves testability | MEDIUM |
| Document owner and rollback contact | `{unit_name}` | Ownership unknown from scan | Improves operability and change planning | HIGH for documentation value |
| Add one config-resolution check | `{platform_component}` consumers | Shared config reference in `{source_artifact}` | Reduces deployment risk | MEDIUM |

## Unknowns
Unknowns are not defects by themselves. State them as evidence boundaries: "The scanner proves `{unit_name}` references `{target_entity}`, but does not prove runtime volume, SLA, owner, credential rotation, access policy, alerting, or incident history."

| Unknown | Why It Matters | Inspect Next |
|---------|----------------|--------------|
| Runtime volume | Affects performance and rollback risk | Logs, metrics, run history |
| Credential rotation | Affects security score | Secret manager metadata |
| Consumer list | Affects blast radius | Full source search and deployment inventory |

## Scorecard Tone
Be specific and restrained. Prefer "confidence is LOW because runtime evidence is absent" over "system is risky" when the evidence gap is the main finding.
