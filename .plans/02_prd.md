---
feature: Persist runtime tool results into conversation context (runtime tool memory)
pipeline_step: 2_prd
---

## Executive Summary

Give chat a durable, bounded memory of the runtime executions it just discussed. After
a runtime tool-use turn, persist a compact, sanitized summary of the tool results
(tool name + `execution_id` + job/state/headline — never raw logs) on the assistant
message, and replay the most recent summary into the next turn's LLM context. "Done"
means a follow-up like "show me the logs for that execution" resolves the
`execution_id` from memory — no re-discovery, no asking the user — with no SSE/tool/
frontend changes and no cross-project leakage.

## Problem

Each chat message runs its own runtime tool-use loop and only the assistant's final
text is persisted; the structured tool results — notably the `execution_id` from
`detect_failures`/`list_executions` — are discarded at end of turn. On a follow-up
("show me the logs for that execution"), the model has no id in context. The recent
prompt-only guidance helps it re-discover the id, but that depends on model behaviour
every turn; there is no durable memory of "the execution we were just talking about."

## Goals

1. After a runtime turn, persist a bounded, sanitized `runtime_tool_summary` on the
   assistant `ConversationMessage`, derived from that turn's `RuntimeToolResult`s.
2. The summary stores only reference fields — `tool_name`, and when present
   `execution_id`, `job_name`, `state`, and a short `headline` — never raw logs,
   payloads, or secrets.
3. On the next turn, inject the most recent persisted summary into the runtime
   tool-use context so the model resolves "that execution" / "the failed job" without
   re-discovery or asking.
4. Replay is RBAC/project-safe: summaries are per-conversation (already user-owned) and
   built only from that turn's already-scoped results; no cross-project reference leaks.
5. No change to the SSE event contract, LLM tool vocabulary/input schemas, or frontend.
6. Additive, reversible migration; existing runtime + chat tests stay green.

## Non-Goals

- Persisting or rendering full runtime logs in history (summary only).
- Frontend display of the summary (context-only, invisible to the UI).
- Changing SSE events, tool names, or input schemas.
- Cross-conversation memory or a global runtime cache.
- Refreshing the underlying data from the summary (tools still fetch live on demand).
- Vectorizing runtime data into Qdrant.

## User Stories

1. As a **chat user**, after asking "what failed last night?" I want to say "show me
   the logs for that execution" and get them, without repeating the execution ID.
2. As a **chat user**, I want follow-ups about "the failed job" to just work across a
   couple of turns, so the conversation feels coherent.
3. As a **security owner**, I want the remembered context to never carry another
   project's data or any raw log/secret content.
4. As a **maintainer**, I want the memory stored like existing message metadata
   (a column on `conversation_messages`), so there's no new lifecycle to reason about.

## Functional Requirements

1. Add a nullable JSON column `runtime_tool_summary` to `conversation_messages`
   (additive migration `031`), mirroring the existing `sources`/`doc_gap` metadata.
2. Build the summary from `loop_outcome.progress_events` (the `runtime_tool_result`
   payloads, already sanitized/size-bounded by `runtime/client.py`): one entry per
   tool result with `tool_name` plus `execution_id`/`job_name`/`state`/`headline` when
   derivable from the result data. Cap the number of entries (e.g. ≤ 10).
3. Persist the summary on the assistant message at **both** persist sites
   (`run_conversational_rag` and `run_conversational_rag_stream`). When a turn used no
   runtime tools, the column is left null.
4. On a new turn, before/within the runtime tool-use loop, locate the most recent prior
   assistant message in the conversation that has a non-null `runtime_tool_summary` and
   inject it into the runtime context as a short note (e.g. "Recent runtime context
   you can reference: …"), alongside the existing runtime tool-use guide.
5. The injected note contains only summary fields (never raw logs); it is bounded in
   size; it is only sourced from the same conversation.
6. `MessageResponse` may expose the summary as an optional field for completeness, but
   the frontend is not required to render it and the SSE contract is unchanged.
7. If `runtime_tool_summary` is absent/empty (older messages, non-runtime turns), the
   loop behaves exactly as today (the prompt guidance still lets the model re-discover).

## Non-Functional Requirements

- **Security**: 100% of injected summaries originate from the current conversation's
  own prior turns; a test proves no cross-project execution reference is replayed. No
  raw `data`/log text or secret-bearing fields are ever persisted.
- **Bounded context**: summary capped (≤ ~10 entries, hard char cap) and only the most
  recent summary is replayed — context growth is O(1) per turn, not O(turns).
- **Compatibility**: `mock`/`talend_tmc`/`stored` providers unaffected; existing
  runtime, tool-loop, and chat tests remain green; the tool loop's progress/SSE output
  is unchanged.
- **Migration safety**: additive, nullable column; reversible `downgrade`; no backfill.
- **Conventions**: Python 3.11+, Pydantic v2, SQLAlchemy `Mapped`/`mapped_column`,
  Alembic; type hints; async I/O.

## Architecture Impact

- **Schema**: `conversation_messages.runtime_tool_summary` JSON nullable
  (`src/db/models/conversation.py`) + Alembic `031` (additive, reversible).
- **Persistence**: `generation/conversational_rag.py` — build the summary from
  `loop_outcome.progress_events` and set it on the assistant `ConversationMessage` at
  both persist sites; a small pure helper builds the summary from progress events.
- **Replay**: `generation/conversational_rag.py` reads the latest prior summary from
  loaded messages; `generation/agent_loop.py` accepts it (e.g. `prior_runtime_summary`)
  and folds it into the runtime context note (extending the existing
  `_with_runtime_guide`/guide injection). Mirrors how previous sources are threaded.
- **API**: `MessageResponse` (in `api` schemas) may gain an optional
  `runtime_tool_summary` field; backend-only, frontend unaffected, SSE unchanged.
- **Not touched**: retrieval chain, MCP API routes, harness pipeline, SSE event names,
  frontend, runtime tool vocabulary/schemas, `runtime/client.py` sanitization.

## Implementation Phases

- **Phase 1 — Persist the summary**: migration `031` + model column + summary-builder
  helper (from `progress_events`) + set it at both persist sites + optional
  `MessageResponse` field. Validation: migration up/down; unit test for the builder
  (incl. redaction/cap); persistence test that a runtime turn writes the column and a
  non-runtime turn leaves it null.
- **Phase 2 — Replay into context**: read latest prior summary; thread it into the
  runtime loop context note; behaviour unchanged when absent. Validation: a two-turn
  backend test (turn 1 detects a failure; turn 2 "show me the logs for that execution"
  resolves the `execution_id` from the injected summary) with the LLM decision mocked;
  an RBAC test that a summary from one project is not injected into another
  conversation/scope; full suite green.

## Risks and Open Questions

1. **OPEN (resolve at /plan): replay window** — most-recent summary only (recommended:
   bounded, covers the "that execution" case) vs accumulate last N runtime turns.
2. **Leak risk**: a replayed summary must not surface data beyond the turn's scope —
   mitigated by sourcing only from same-conversation prior results (already scoped) and
   an explicit RBAC replay test.
3. **Sensitive data**: persisting raw logs would store secrets — mitigated by storing
   only summary fields, never `data`/log text, reusing client redaction.
4. **Field derivation**: `execution_id`/`job_name`/`state` must be extracted from
   heterogeneous tool result data (list vs single object) — handle defensively; store
   only what is present.
5. **Migration**: must be additive + reversible; nullable column, no backfill.

## Acceptance Criteria

- [ ] Migration `031` adds nullable `runtime_tool_summary` to `conversation_messages`;
      `downgrade` drops it; no existing column altered.
- [ ] A pure builder turns `progress_events` into a capped list of sanitized summary
      entries (`tool_name` + optional `execution_id`/`job_name`/`state`/`headline`);
      never includes raw log text.
- [ ] Both persist sites set the column on runtime turns and leave it null otherwise.
- [ ] The most recent prior summary is injected into the next turn's runtime context.
- [ ] Two-turn test: "show me the logs for that execution" resolves the `execution_id`
      from the injected summary (no re-discovery needed in the test path).
- [ ] RBAC test: a summary is only replayed within its own conversation; no
      cross-project execution reference is injected.
- [ ] No raw logs/secrets persisted; summary size is bounded.
- [ ] SSE contract, tool vocabulary, and frontend unchanged.
- [ ] Existing backend tests (runtime, tool loop, chat) remain green.
- [ ] `/inspect` run and passed (Auth/RBAC replay safety).
