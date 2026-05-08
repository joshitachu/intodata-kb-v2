# Runtime Config And Externals

## Runtime Config
List configuration variables, properties, context values, environment references, defaults, and consumers. Preserve redaction and do not invent secret values.

| Setting | Scope | Source Artifact | Visible Value Pattern | Consumers | Confidence |
|---------|-------|-----------------|-----------------------|-----------|------------|
| `{platform_component}` setting | Unit or shared scope from evidence | `{source_artifact}` | Literal, masked, defaulted, or scanner-limited | `{unit_name}` | `{confidence}` |
| `{source_entity}` endpoint reference | Environment or external system | `{evidence_artifact}` | Variable name or unresolved placeholder | `{unit_name}` | MEDIUM |

Example config wording: "`{unit_name}` consumes a setting declared in `{source_artifact}`. The value is HIGH confidence when present as a literal or normalized fact, MEDIUM when resolved through another artifact, and LOW when the scanner shows only a reference name."

## External Systems
| System | Type | Connection Or Connector | Direction | Units | Auth/Secret Handling | Confidence |
|--------|------|-------------------------|-----------|-------|----------------------|------------|
| `{source_entity}` | External dependency type from evidence | `{platform_component}` | Inbound | `{unit_name}` | Secret value redacted; reference retained | `{confidence}` |
| `{target_entity}` | External target or downstream system | `{platform_component}` | Outbound | `{unit_name}` | Credential store unknown from scan | MEDIUM |

External wording: "The scanner proves `{unit_name}` references `{target_entity}` through `{platform_component}`. It does not prove endpoint reachability, account permissions, firewall rules, rate limits, or production credentials."

## Credentials
Document credential storage and redaction. Redacted secrets are intentionally hidden, not missing.

Example credential wording: "`{source_artifact}` shows a credential reference for `{platform_component}`, but the secret value is intentionally redacted. Do not reconstruct, guess, or mark the secret as missing. State only the storage reference, consumer unit, and any scanner-visible rotation or environment metadata."

## Environment And Portability
| Risk | Evidence | Affected Unit | Portability Impact | Unknown |
|------|----------|---------------|--------------------|---------|
| Environment-specific config | `{evidence_artifact}` | `{unit_name}` | May require coordinated config changes before deployment | Owner, rotation cadence, runtime override behavior |
| Hardcoded endpoint or path | `{source_artifact}` | `{platform_component}` | May break promotion across environments | Whether override exists |
| Unresolved variable | `{source_artifact}` | `{unit_name}` | Deployment may depend on external injection | Source of value |

Portability language: "`{unit_name}` may be environment-coupled if `{source_artifact}` contains hardcoded endpoints, paths, tenant identifiers, region-like values, or fixed runtime values. Confidence is MEDIUM when the value is visible but deployment context is absent; LOW when only an unresolved variable is scanned."

## Unknowns
State unknowns as evidence boundaries: owner, rotation cadence, endpoint reachability, permissions, production-vs-test status, rate limits, and runtime override precedence are unknown unless a supplied `{evidence_artifact}` says otherwise.

Debug handoff wording: "Before changing `{platform_component}` config, inspect all `{unit_name}` consumers, verify environment overrides, and prepare rollback for credentials or endpoints that affect multiple units."

Change phrasing: "If `{source_entity}` or `{target_entity}` uses a shared external connection, affected-unit scope stays MEDIUM confidence until every consumer is found in `{evidence_artifact}`."

Verification phrasing: "Test config resolution without exposing secret values; evidence should name the reference, consumer, and environment behavior, not the credential contents."
