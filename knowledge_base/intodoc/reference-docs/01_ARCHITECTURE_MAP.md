# Architecture Map

## Topology
Describe the architecture as a scanner-backed topology, not a business narrative. Name the visible boundaries, entry points, shared components, and dependency direction using exact artifact names.

Example narrative: `{unit_name}` sits inside the `{platform_component}` boundary and coordinates movement from `{source_entity}` toward `{target_entity}`. This is HIGH confidence when the unit, component, and endpoints are all declared in `{source_artifact}`; it is MEDIUM when grouping is inferred from paths or labels; it is LOW when runtime sequencing is not visible.

Scanner-limited gap: The scan may prove that two units reference the same component, but it may not prove whether the relationship is synchronous, scheduled, event-driven, manually triggered, or only configured for future use. State that boundary explicitly.

## Entrypoints
| Entrypoint | Source Artifact | Trigger/Activation | Downstream | Confidence |
|------------|-----------------|--------------------|------------|------------|
| `{unit_name}` | `{source_artifact}` | Declared trigger, source event, manual hook, or scanner-limited | `{target_entity}` through `{platform_component}` | `{confidence}` |
| `{platform_component}` boundary | `{evidence_artifact}` | Shared dependency discovered from references | Units that reference the component | MEDIUM until runtime order is known |

Entrypoint wording: "`{unit_name}` is an entrypoint only for the scanner-visible path. The export does not prove production schedule, last run time, operator ownership, or traffic volume."

## Dependencies
| Dependency Type | From | To | Evidence | Confidence |
|-----------------|------|----|----------|------------|
| Upstream-downstream | `{source_entity}` | `{target_entity}` | `{evidence_artifact}` | HIGH when both endpoints are explicit |
| Unit-to-component | `{unit_name}` | `{platform_component}` | `{source_artifact}` | HIGH for direct reference |
| Shared resource | `{unit_name}` | `{platform_component}` | Repeated references in scan | MEDIUM until full consumer list is proven |
| Runtime dependency | `{unit_name}` | External execution state | Runtime evidence not in scanner | LOW until observed |

Example dependency wording: `{unit_name}` depends on `{platform_component}` because `{source_artifact}` references it directly. `{target_entity}` is downstream, but the scanner does not prove invocation timing or retry behavior.

## Boundaries
Describe each boundary by what it owns and what it does not prove. For `{platform_component}`, ownership can include orchestration, config, routing, mapping, or connector references only when those terms appear in source or normalized facts.

Boundary phrasing: "The `{platform_component}` boundary owns scanner-visible configuration and references used by `{unit_name}`. Runtime ownership, deployment ordering, environment promotion, and live throughput are unknown from scanned evidence."

## Architecture Gaps
| Gap | Why It Matters | Inspect Next | Confidence |
|-----|----------------|--------------|------------|
| Trigger schedule not exported | Affects run ordering and debug priority | Runtime schedule or deployment metadata | LOW |
| Shared component consumer list incomplete | Affects change blast radius | `{evidence_artifact}` plus full source search | MEDIUM |
| No deployment topology | Limits environment and failover claims | Deployment files or platform inventory | LOW |

Use gap language without overreaching: "Architecture gap" means the scanner did not expose enough evidence, not that the implementation is wrong.
