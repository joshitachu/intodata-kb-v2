# ADR-007: Redaction Captures Safe Structure And Fails Closed

## Status
Accepted 2026-07-14 / 2026-07-29; still current

## Context

Two opposite failures were observed on real customer source. Cleartext credentials
(`MaximoPassword`, `AES_Key`) reached a published document because sensitivity matching
was keyed on an underscore-suffix heuristic that camelCase names bypass. Separately,
over-eager redaction rewrote `xmlns` declaration URIs, which silently dropped tMap
MapperData from every job because the namespaced `xsi:type` lookup then failed.

## Decision

Redaction removes values, not structure. Raw endpoints and credentials are removed while
safe scheme / category / auth-type / TLS / host-present descriptors survive. `xmlns`
declaration values are masked before any pattern runs and restored verbatim. A fail-loud
post-scan `redaction_verifier` gate is the single choke point: it exits non-zero and
writes no output on findings.

## Rationale

"Redaction is capture-not-discard: raw endpoints and credentials remain removed, while
safe scheme/category/auth-type/TLS/host-present descriptors survive on component and
context evidence. Descriptor transport markers cannot persist."

"Namespace URIs are structural XML identifiers -- redacting them silently breaks every
namespaced attribute lookup downstream (`xsi:type` -> tMap data lost for ALL jobs,
`xmi:id` -> connection index degraded). A test had codified the broken behavior as
acceptable; never assert that xmlns redaction 'is fine'."

"Redaction defense-in-depth: the post-scan `redaction_verifier` gate at the CLI `scan`
command is the single choke point (mcp-server shells out to it). If a new output path
writes scan artifacts, it MUST pass the gate too. Sensitivity matching is token-based
(camelCase-aware) -- never regress to underscore-suffix matching."

## Consequences

- Any new path that writes scan artifacts must pass through the same gate.
- Descriptors must be attached per environment while parsing and propagated only through
  exact `context.Variable` references with agreeing facts; post-filter normalization is
  too late to recover URI structure.
- Genuine per-job configuration differences (for example differing `ssl=require` values
  across job-embedded context snapshots) are customer facts, not aggregator bugs. Document
  provenance rather than "fixing" the data.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md and knowledge_base/intodoc/.claude/PROJECT_MEMORY.md` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
