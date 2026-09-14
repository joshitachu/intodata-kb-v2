# ADR-003: RAG Push Is Server-Side, Never Requested From The Generation Prompt

## Status
Accepted (Feb 2026); still current

## Context

Early doc-generation prompts asked the model to call an `insert_to_rag` tool once it had
written documents. That made indexing a model behaviour: it could be skipped, partially
applied, or applied to intermediate artifacts.

## Decision

`_run_generate_background` calls `RagBridge.push_docs()` after the atomic swap. The
generation prompt must never mention or invoke `insert_to_rag`.

## Rationale

"`_run_generate_background` calls `RagBridge.push_docs()` -- Claude prompt should NOT
call `insert_to_rag`, server handles it."

"Do NOT add `insert_to_rag` to the Claude Code prompt -- RAG push is server-side."

## Consequences

- The push filter is deliberate: `_read_md_files` uses `glob("*.md")` plus a digit-prefix
  filter, not `rglob`, because `_assessment/`, `_analysis/` and `_investigation/`
  subdirectories hold system prompts and debug output that must never appear in query
  results.
- There are two push paths that both need `extra_doc_names`: the post-generation push in
  `_run_generate_background` and the manual `/push-to-rag` endpoint.
- Internal layers are pushed separately by `RagBridge.sync_internal_layers()` as a
  wholesale delete-then-repush, so stale and fresh sets never mix.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Architecture Decisions / Lessons Learned` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
