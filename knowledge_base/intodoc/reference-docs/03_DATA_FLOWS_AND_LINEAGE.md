# Data Flows And Lineage

## Data Flow
Document source-to-target movement by critical path and by unit. Keep the language mechanical: what reads, writes, maps, filters, enriches, or routes data, and where that claim is evidenced.

Example flow paragraph: `{unit_name}` reads `{source_entity}` and writes `{target_entity}` through `{platform_component}`. This is HIGH confidence when both endpoints and the unit reference appear in `{source_artifact}` or `{evidence_artifact}`. Transformation intent beyond visible expressions is scanner-limited.

Scanner-limited boundary: A static source reference does not prove data volume, schedule success, row counts, freshness, or that the path is enabled in production. These require runtime evidence.

## Flow Index
| Flow | Source | Unit | Target | Evidence | Confidence |
|------|--------|------|--------|----------|------------|
| F-{unit_name}-01 | `{source_entity}` | `{unit_name}` | `{target_entity}` | `{source_artifact}` | `{confidence}` |
| F-{unit_name}-02 | `{source_entity}` | `{platform_component}` | `{target_entity}` | `{evidence_artifact}` | MEDIUM when inferred from adjacent references |

## Lineage
| Source Field/Entity | Transformation | Target Field/Entity | Unit | Evidence | Confidence |
|---------------------|----------------|---------------------|------|----------|------------|
| `{source_entity}` | Visible mapping, expression, filter, or route | `{target_entity}` | `{unit_name}` | `{evidence_artifact}` | HIGH for explicit mapping |
| `{source_entity}` | Scanner-visible link without expression | `{target_entity}` | `{unit_name}` | `{source_artifact}` | MEDIUM |
| Unknown upstream | Derived output only | `{target_entity}` | `{unit_name}` | Partial scan | LOW |

Lineage wording: "`{target_entity}` is derived from `{source_entity}` using the expression visible in `{source_artifact}`. Null handling, default behavior, and intermediate runtime values are unknown unless the expression or tests show them."

## Transformations
Show full expressions, formulas, SQL, mappings, or code where present. Do not translate a technical expression into a business rule unless the artifact says so.

| Output | Input | Rule | Evidence | Notes |
|--------|-------|------|----------|-------|
| `{target_entity}` | `{source_entity}` | Direct assignment or scanned expression | `{source_artifact}` | HIGH if complete expression is visible |
| `{target_entity}` | `{source_entity}` plus config | Computed by `{platform_component}` | `{evidence_artifact}` | MEDIUM until config resolution is proven |

## Validation Gaps
Document validation checks, reject paths, missing validation, schema drift, and scanner-limited fields.

Example validation-gap phrasing: "No validation rule for `{target_entity}` is visible in `{source_artifact}`. This does not prove validation is absent; it means validation is scanner-limited and should be confirmed through runtime tests, upstream schema contracts, or another `{evidence_artifact}`."

## Confidence Rules
Use HIGH when endpoints and expressions are direct. Use MEDIUM when the flow is assembled from adjacent references. Use LOW when the flow depends on runtime state, generated code, truncated expressions, or external schemas not included in the scan.

Practical phrasing: "Lineage for `{unit_name}` is evidence-backed through `{target_entity}`, but freshness and production completeness are unknown from scanned evidence."
