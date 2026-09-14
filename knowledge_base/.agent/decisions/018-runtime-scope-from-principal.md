# ADR-018: Runtime Authorization Comes From The Authenticated Principal, Never From LLM Tool Arguments

## Status
Accepted 2026-06-16; still current

## Context

The chat runtime executes tools whose arguments are produced by a language model. If any
tool argument can influence which projects are read, the model controls authorization.

## Decision

`project_scope` is threaded keyword-only from request to loop to `execute_runtime_tool` to
`RuntimeProviderContext`. `execute_runtime_tool` requires the keyword with no default.

## Rationale

"Authorization must come from the authenticated principal, not LLM-controlled `arguments`.
`execute_runtime_tool` requires the keyword (no default) so a call site can't silently
grant unrestricted reads; tested that a tool arg naming another project can't widen scope.
Follows the existing `get_user_project_ids` None/[]/[ids] contract."

"`src/runtime/scope.py` frozen value object wraps ONE authenticated allow-list and exposes
both `.project_ids` (Qdrant) and `.project_scope` (runtime); built once at each
`run_conversational_rag*` entry. Makes the Qdrant/runtime RBAC coupling type-enforced
instead of two separately-passed values that must stay equal by convention."

## Consequences

- Replaying a remembered `execution_id` into model context does not bypass RBAC: the reader
  is conversation-scoped and any data fetch still enforces `project_scope`.
- REST runtime endpoints deliberately do not use `RequestScope` because they touch a single
  data source and have no second scope to couple.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
