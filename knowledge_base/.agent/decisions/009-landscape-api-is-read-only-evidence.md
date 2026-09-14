# ADR-009: The Landscape API Is A Read-Only Evidence Surface, Not A Curation Or UI Layer

## Status
Accepted 2026-07-29; still current

## Context

The scanner aggregation layer is the natural place to "just fix" a label, merge two
systems that look alike, or shape a payload for one frontend. Every such change makes the
evidence surface untraceable.

## Decision

`GET /mcp/projects/{project_id}/landscape` is RBAC-scoped and explicitly typed, and serves
evidence only. Frontend illustration, application-boundary curation and chat are separate
work and must not be pushed down into the scanner aggregation layer.

## Rationale

"`GET /mcp/projects/{id}/landscape` is RBAC-scoped and explicitly typed. Frontend
illustration, application boundary curation, and chat are separate future work and must
not be smuggled into the scanner aggregation layer."

"The frontend consumes the scanner/MCP `ProjectLandscapeResponse` as the authority: L1
contains applications/systems/aggregated edges; L2 is selected-edge provenance plus exact
channels/interfaces. [...] Prevents display-name merging, endpoint-to-peer invention, and
redacted placeholder channel joins."

## Consequences

- The API must not discard the additive scanner semantic layer at the response boundary.
- Stable edge identity is source/target kinds plus IDs, dependency type, mechanism and
  direction -- never display names.
- A curated overlay is a separate artifact preferred by a composite loader, not a mutation
  of the evidence surface.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
