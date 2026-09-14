# ADR-013: Generated Documentation Is A Typed Evidence Tier, Never An Authority

## Status
Accepted 2026-08-27; still current

## Context

Phase 3.7 business topology runs after `Phase3Docs`, so this run's own finished documents
are available to it. The original prompt forbade reading them ("Do not read or cite Phase 3
generated documentation"), which is exactly why the pipeline could not reach the depth of a
hand-curated topology file that had itself been extracted from those documents.

## Decision

Documentation is registered as a typed `documentation_derived` evidence tier so a citation
resolves, but the type is deliberately absent from the scanner, directional and
system-identity evidence sets. It can never promote a system or create an inter-system
edge.

## Rationale

"Doc sections register as `documentation_derived` evidence ids so a citation resolves; the
type is deliberately absent from `_SCANNER_EVIDENCE_TYPES`, `_DIRECTIONAL_EVIDENCE_TYPES`
and `_SYSTEM_IDENTITY_EVIDENCE_TYPES`, so it can never promote a system or create an
inter-system edge. A doc-asserted flow is capped below `observed`, must still resolve real
scanner `artifact_ids`, and its missing-directional-evidence check is a WARNING for that
tier only."

## Consequences

- Three grounding tiers: a scanner edge establishing exact direction may reach `observed`;
  endpoint-corroborated (implementing designs scanner-proven at both endpoints) stays
  `inferred` with uncapped confidence; documentation-only is capped at 0.6.
- Documents are discovered, never assumed, because a pack replaces the doc set wholesale
  and a fixed catalog filename list would silently disable the lane for every pack.
- A claim-local validation error drops that claim plus dangling references and the rest
  publishes; global defects (schema version, scan fingerprint, duplicate ids, secret
  findings) are not prunable and still fail closed.

## Source
Promoted from `knowledge_base/CLAUDE.md -- Business Topology` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
