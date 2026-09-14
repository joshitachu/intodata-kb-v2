# ADR-023: Layered Retrieval Defaults To Canonical Documents Only

## Status
Accepted 2026-06-12; still current

## Context

Internal analysis artifacts (Phase 2 group analyses, support maps, scorecards, evidence
slices) are valuable for deep retrieval and useless -- actively harmful -- as answers to a
normal user question.

## Decision

`source_layers=None` means canonical documents plus legacy chunks that carry no
`source_layer`. Internal layers require an explicit list; `[]` is reserved for an
intentional all-layer search.

## Rationale

"Prevents internal analysis artifacts from polluting normal retrieval while keeping
pre-Slice-1 chunks visible until re-ingestion is complete. Summary document resolution must
preserve this default and still thread `project_ids`."

## Consequences

- Default retrieval is protected by `source_layer` filtering, NOT by the `_internal/`
  filename prefix; the prefix only scopes the delete lifecycle and keeps internal documents
  out of canonical push and diff globs.
- The internal-lifecycle delete must escape the LIKE pattern: `_` is a SQL LIKE wildcard and
  project ids contain underscores.
- Known gap: admin document-list endpoints still show `_internal/` rows.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
