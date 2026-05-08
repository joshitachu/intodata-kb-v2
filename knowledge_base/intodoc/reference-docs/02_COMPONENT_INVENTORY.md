# Component Inventory

## Canonical Unit Inventory
Build the inventory from scanner-visible units first. Each row should identify what the artifact proves, what is inferred, and what remains unknown.

| Unit | Group/Boundary | Source Artifact | Type | Purpose | Dependencies | Confidence |
|------|----------------|-----------------|------|---------|--------------|------------|
| `{unit_name}` | `{platform_component}` | `{source_artifact}` | Scanned unit type | Moves or transforms `{source_entity}` into `{target_entity}` when evidence supports it | `{target_entity}`, shared `{platform_component}` | `{confidence}` |
| `{unit_name}` variant | Inferred from path or label | `{evidence_artifact}` | Normalized fact | Purpose inferred from adjacent components | Unknown consumer set | MEDIUM |

Example inventory wording: "`{source_artifact}` is the canonical source for `{unit_name}` because it contains the unit definition and direct dependency references. The purpose is HIGH confidence if declared; MEDIUM if inferred from component names; LOW if only a filename suggests intent."

## Artifact Coverage
| Artifact | Covers | Does Not Prove | Confidence |
|----------|--------|----------------|------------|
| `{source_artifact}` | Unit structure, visible config, declared dependencies | Runtime success, data volume, SLA | HIGH for structure; LOW for runtime |
| `{evidence_artifact}` | Normalized cross-document facts | Operator intent or production usage | MEDIUM for derived inventory |
| Runtime artifact | Run status, logs, row counts if supplied | Static source ownership by itself | HIGH only for observed run facts |

Coverage-gap language: "No scanned artifact proves ownership for `{unit_name}`. Leave owner unknown and avoid substituting folder names or user IDs unless evidence identifies them as owners."

## Components
Use the platform prompt's local vocabulary where applicable: components, steps, actions, profiles, modules, routes, services, mappings, or jobs. Keep placeholders neutral in the reference doc.

Example component wording: `{platform_component}` appears inside `{unit_name}` and is referenced by `{source_artifact}`. Component existence is HIGH confidence; component purpose is MEDIUM when derived from a generic label; runtime behavior is scanner-limited.

## Dependencies
| Unit | Dependency | Evidence | Status | Risk |
|------|------------|----------|--------|------|
| `{unit_name}` | `{target_entity}` | `{evidence_artifact}` | Resolved in scanned artifacts | Change impact may extend to visible downstream consumers |
| `{unit_name}` | `{platform_component}` | `{source_artifact}` | Scanner-limited consumer list | Full blast radius is LOW confidence |
| `{unit_name}` | `{source_entity}` | Direct reference or inferred schema | Needs validation | Missing schema evidence can hide drift |

Dependency phrasing: "The inventory proves `{unit_name}` references `{platform_component}`. It does not prove that every consumer of the component was exported."

## Coverage Gaps
List missing references, orphaned units, inactive/test-only artifacts, and unresolved placeholders as inventory facts, not side comments.

| Gap | Affected Unit | Evidence | Inspect Next |
|-----|---------------|----------|--------------|
| Purpose not declared | `{unit_name}` | `{source_artifact}` exposes structure only | README, owner notes, runtime labels |
| Runtime state absent | `{unit_name}` | No log artifact in scan | Runtime logs and run history |
| Dependency unresolved | `{platform_component}` | `{evidence_artifact}` has reference without target | Full export or platform inventory |

Unknowns wording: "`{unit_name}` is present in the inventory, but activity, owner, and production status are unknown from scanned evidence."
