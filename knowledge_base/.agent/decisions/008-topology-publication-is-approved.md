# ADR-008: Static Topology Publication Is An All-Or-Nothing Approved Boundary

## Status
Accepted 2026-07-29; still current

## Context

Topology artifacts (scan result, manifest, lineage, landscape) are consumed by Runtime and
by the frontend as authoritative evidence. A partially-published or tampered set would let
a downstream surface make confident claims about a landscape that never existed.

## Decision

MCP may publish only when `publication_approval.json` approves and hash-binds
`scan_result.json`, the manifest, lineage and landscape together. Anything else fails
closed. A per-output `O_EXCL` lock spans scan through publish.

## Rationale

"MCP may publish only when `publication_approval.json` approves and hash-binds
`scan_result.json`, manifest, lineage, and landscape; redaction-bypass scans, stale
receipts, hash mismatches, and malformed resolved references fail closed. The per-output
O_EXCL lock spans scan through publish."

## Consequences

- A crashed process can leave the fail-closed lock in place; clearing it is an explicit
  operator action after verifying no scanner is running.
- Bypassed or tampered artifacts fail closed rather than degrading silently.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
