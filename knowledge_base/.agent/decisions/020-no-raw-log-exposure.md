# ADR-020: Raw Runtime Logs Are Never Exposed To Dashboard Or Chat

## Status
Accepted 2026-07-02; still current

## Context

The product question is "show me what is broken and why", not "let me browse logs". Raw
log lines can carry secrets and are too low-level for a client or an admin.

## Decision

Raw logs may be retained internally for parsing and evidence derivation, but dashboard and
chat must not expose raw log dumps or quote raw execution log text. User-facing surfaces
use sanitized evidence snippets, structured timeline signals, incidents, recurrence,
freshness and health rollups. `get_execution_logs` is not in the user-facing chat tool
allowlist.

## Rationale

"The product workflow is 'show me what's broken and why', not raw log browsing. Raw lines
can contain secrets/noise and are too low-level for clients/admins. Defensive sanitizer
code may still recognize event-like tools for legacy/internal summaries."

"`parser.redact_credentials` covers BOTH `keyword=value` and inline `user:pass@host` /
`user/pass@host` JDBC userinfo; it is the single source of truth, applied before any value
is persisted (especially `endpoint`) or sent to the LLM. Inline JDBC URLs were leaking
credentials into the surfaced `endpoint` field. Fixed once at the shared-helper level so
every path is covered -- do not reimplement either helper."

## Consequences

- Runtime tool memory persists reference fields only (tool name plus execution id / job
  name / state / headline); logs become an execution pointer, never message text.
- Any LLM fallback receives already-redacted excerpts and structured signals only.

## Source
Promoted from `RAG_pipeline/.claude/PROJECT_MEMORY.md -- Architecture Decisions` on 2026-09-04. The original file is preserved verbatim under the
owning sub-project's `.claude/archive/PROJECT_MEMORY.md`.
