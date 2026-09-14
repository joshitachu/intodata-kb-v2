# ADR-010: Semantic Topology Is An Additive Proposal Layer Over Scanner Evidence

## Status
Accepted 2026-07-30; still current

## Context

Business-meaningful systems and exchanges are what a reader wants; raw technical facts are
what the scanner can prove. Collapsing the two lets a guess become a fact.

## Decision

Semantic systems and exchanges are additive proposals over raw scan evidence, carrying
exact artifact/component provenance plus explicit status and confidence. They never
replace technical facts and never become `confirmed` without supplied business evidence or
durable human curation.

## Rationale

"Use the XMI project ID in `talend.project` as the stable imported-project identity. A
ZIP/source-root path is provenance and drill-down scope, not an application identity."

"Talend `LABEL` is human presentation; `UNIQUE_NAME` remains the stable component identity
for connections, lineage, and drill-down."

"A component category or transport such as Hash, REST API, JMS Broker, or File endpoint
does not prove a peer system identity. Keep it as L2 mechanism or interface evidence until
an endpoint, repository connection, context identity, supplied business registry, or other
explicit fact establishes the peer."

"Direct and direct-vm channel names are route-local implementation addresses, never
cross-project business-exchange identity. Only shared transports with cross-root identity
evidence, such as an exact JMS destination, may create a cross-application technical
edge."

"Semantic systems/exchanges are additive proposals over raw scan evidence. They must carry
exact artifact/component provenance and explicit status and confidence. They never replace
technical facts or become `confirmed` without supplied business evidence or durable human
curation."

## Consequences

- Do not add fuzzy system merging or infer peers from endpoint declarations.
- Alias folding must derive from scanner landscape rows plus declared aliases, never from
  a hardcoded table.
- A scanner floor merge is additive: edges the extraction missed are appended and folded
  on scanner identity evidence, because the two builders mint different system ids and a
  naive union publishes each system twice.

## Source
Promoted from `knowledge_base/intodoc/.claude/PROJECT_MEMORY.md -- Talend topology identity invariants` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
