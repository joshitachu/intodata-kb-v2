---
feature: Persist runtime tool results into conversation context (runtime tool memory)
pipeline_step: 1_grill
date: 2026-06-16
---

## Feature

After a runtime tool-use turn, persist a compact, sanitized summary of the runtime
tool results (tool name + `execution_id` + job name + state/short headline — never raw
logs or secrets) on the conversation, and replay it into the next turn's LLM context
so follow-up questions like "show me the logs for that execution" / "the failed job"
resolve the `execution_id` from memory instead of re-discovering it or asking the user.

Builds on the committed Held Runtime-Data epic (`stored` provider + RBAC scope
threading) and the just-made tool-use guidance change (registry descriptions +
`agent_loop` runtime guide + iteration cap, currently uncommitted). Predecessor epic
docs archived in `.plans/archive/held-runtime/`.

## Problem Statement

Each chat message runs its own runtime tool-use loop (`run_runtime_tool_loop`), and
only the final assistant *text* is persisted to `conversation_messages`. The
structured tool results — including the `execution_id` returned by `detect_failures` /
`list_executions` — are discarded at end of turn. So on a follow-up like "show me the
logs for that execution", the model has no `execution_id` in context. The just-shipped
tool-use guidance helps the model re-discover the id via a tool call, but that depends
on model behaviour every turn. There is no durable memory of "the execution we were
just talking about."

## Definition of Done

- After a runtime turn, the assistant message persists a bounded, sanitized
  `runtime_tool_summary` (e.g. list of `{tool_name, execution_id?, job_name?, state?,
  headline?}`) — derived from the turn's `RuntimeToolResult`s, never raw log text.
- On the next turn, the most recent persisted summary is injected into the LLM context
  (as a system/context note) so the model can resolve "that execution" without asking.
- Replaying is RBAC/project-safe: a summary is only injected for the same conversation
  (already user-owned) and carries no cross-project data beyond what that turn's
  scope already returned.
- No raw logs, secrets, or full payloads are persisted (reuse the runtime client's
  redaction + a hard size/element cap).
- SSE event contract, LLM tool vocabulary, and frontend are unchanged.
- Verification: a follow-up "show me the logs for that execution" resolves the id from
  the persisted summary (backend test drives two sequential turns); migration up/down
  clean; existing runtime + chat tests stay green.

## Out of Scope

- Persisting or rendering full runtime logs in history (only the compact summary).
- Frontend display of the summary (it is context-only, invisible to the UI).
- Changing the SSE events, tool names, or input schemas.
- Cross-conversation memory or a global runtime cache.
- Re-fetching/refreshing the underlying data (the summary stores the pointer/headline;
  live data is still fetched on demand by the tools).
- Vectorizing runtime data into Qdrant.

## Solution Rationale

Persisting a structured summary makes the `execution_id` *present* next turn, so
resolution no longer depends on the model choosing to re-look-it-up (the limitation of
the prompt-only fix). Storing it as a JSON column on `conversation_messages` matches
the existing per-message metadata pattern (`sources`, `primary_source_layer`,
`fallback_used`, `doc_gap` are already columns there) — no new table, no new lifecycle.
Building the summary from the loop's `RuntimeToolResult`s (already sanitized/bounded by
`runtime/client.py`) keeps it safe by construction. Replay is a context-build concern
in `conversational_rag`, mirroring how previous sources are already threaded.

## Assumptions and Risk

- **Open decision (resolve at /plan): replay window.** Default = inject only the most
  recent runtime summary found in conversation history (simplest, bounded, covers the
  "that execution" case). Alternative = accumulate the last N runtime turns. Recommend
  most-recent-only first.
- `conversation_messages` is the right home (consistent with existing metadata columns)
  — assume a nullable JSON column is acceptable (additive migration, like 029's
  `doc_gap`).
- The summary fields (`execution_id`, `job_name`, `state`, short `headline`) are enough
  for reference resolution; raw data is never needed in memory.
- Assumes the persisted id stays a valid pointer; live data is always re-fetched by the
  tools, so staleness of the *summary* only affects phrasing, not correctness.

## Critical Areas Touched

- **Auth / RBAC — yes (replay safety).** Must ensure the injected summary cannot leak
  data outside the turn's scope; since it is per-conversation (already owned by the
  user) and built from already-scoped results, risk is low but must be verified.
  `/inspect` warranted.
- Chat path (`generation/conversational_rag.py`, `generation/agent_loop.py`) — message
  persistence + context build.
- Schema — additive migration + a column on `conversation_messages`; `MessageResponse`
  may gain an optional field (backend only; not required to surface in frontend).
- NOT touched: retrieval chain, MCP API routes, harness pipeline, SSE contract,
  frontend, runtime tool vocabulary.

## Risk if Wrong

- **Leak**: injecting a summary built from a broader scope than the current request
  could surface another project's execution reference — mitigated by building only from
  the same-conversation, same-turn scoped results and adding an RBAC replay test.
- **Bloat / drift**: an unbounded or growing summary could inflate context — mitigated
  by a hard element/char cap and most-recent-only replay.
- **Sensitive data**: persisting raw logs would store secrets — mitigated by storing
  only the compact summary fields, never `data`/log text, reusing client redaction.
- **Migration**: must be additive + reversible; a bad migration risks the
  `conversation_messages` table — mitigated by a nullable column only (no backfill).
