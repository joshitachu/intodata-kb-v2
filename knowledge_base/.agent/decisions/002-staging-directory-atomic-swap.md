# ADR-002: Documentation Is Published By Atomic Staging Swap

## Status
Accepted (Feb 2026); still current

## Context

A documentation run can fail at any phase: a provider length stop, a rate limit, a
crashed process. Writing generated markdown straight into `output/{project_id}/docs/`
means a failed run destroys the last good documentation set.

## Decision

The harness writes to `output/{project_id}/docs_staging/`. The server swaps
`docs_staging/` into `docs/` only on success. On failure `docs_staging/` is preserved
for repair/resume and the old `docs/` is untouched, with `doc_count` unchanged.

## Rationale

"Claude writes to `docs_staging/`, server swaps to `docs/` on success -- protects
existing docs from failed runs."

"On failure, staging is preserved; on resume, `_detect_resume_state()` inspects
artifacts and `_manifest.json` tracks scan mtime for stale detection -- prevents loss
of ~30 min work on rate limit failures."

"Enterprise generation must never publish to `docs/` unless all gates pass. Failed
enterprise runs preserve `docs_staging/` for repair/resume; treat that directory as
work-in-progress, not client-ready output."

## Consequences

- Anything that must survive publication has to be written under `docs_staging/` before
  the swap (`_rag_support/`, `_audit/`, `_assessment/`), and must be excluded from the
  Phase 4 cleanup deletion list.
- `_preprocess/`, `_analysis/` and `_transient/` are deleted at Phase 4 and never reach
  `docs/`.
- Resume correctness depends on `status.json` being written last in the agent task
  runtime, so a crash forces a safe re-run instead of a phantom acceptance.

## Source
Promoted from `knowledge_base/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
