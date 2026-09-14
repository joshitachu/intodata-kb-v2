# ADR-019: Runtime Failure Knowledge Is Data, Not Control Flow

## Status
Accepted 2026-06-18; still current

## Context

Error-code matching, root-cause templates and next-checks are the kind of knowledge that
grows with every new platform. Encoding it as branches in the parser and builder makes each
new failure family a code change in the hottest path.

## Decision

All error-code matching, root-cause templates and next-checks live in
`runtime/insights/catalog.py` as a `FailurePatternCatalog` (Strategy/Registry, module
singleton `CATALOG`). `parser.py` and `builder.py` delegate to it.

## Rationale

"Adding a platform/failure family is catalog data plus a test with ZERO parser/builder
control-flow edits (the no-special-case rule). INVARIANT: numeric/3-digit and the generic
`[A-Z]{2,}-\d+` families are anchor-gated (`requires_context` plus `context_pattern`) so
benign message IDs like `ORD-8842193` never match and poison `primary_error_code` -- keep
that discipline when adding families."

"Parser resolves the deepest `Caused by:` root exception, NOT the outermost wrapper.
Builder selects `primary_error_code`/`failed_component` from the terminal/causal error
event, NOT first-seen. A benign earlier warning was misattributing the cause and poisoning
the signature."

## Consequences

- A signature-algorithm change REQUIRES a rebuild path:
  `builder.rebuild_runtime_insights` recomputes from the raw store, which is the source of
  truth.
- Distinct unknown failures must get distinct signatures; genuinely identical failures must
  still collide so recurrence holds.
- LLM fallback is a Decorator over the deterministic result, off by default, invoked only
  at `confidence == "low"`, bounded, and fails safe -- the flag-off path is byte-for-byte
  identical to the deterministic path.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
