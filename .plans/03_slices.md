---
feature: Persist runtime tool results into conversation context (runtime tool memory)
pipeline_step: 3_slices
total_slices: 2
current_slice: 2
---

## Slice 1: Persist the runtime tool summary

**Delivers**: Every runtime tool-use turn writes a bounded, sanitized
`runtime_tool_summary` (tool name + `execution_id`/`job_name`/`state`/`headline`, never
raw logs) onto the assistant message; non-runtime turns leave it null. Foundation —
nothing reads it yet, so chat behaviour is unchanged.

**Expected files**:
- `RAG_pipeline/backend/src/db/models/conversation.py` (add nullable JSON
  `runtime_tool_summary` column to `ConversationMessage`)
- `RAG_pipeline/backend/alembic/versions/031_add_runtime_tool_summary.py` (new, additive,
  reversible)
- `RAG_pipeline/backend/src/generation/agent_loop.py` (pure helper
  `build_runtime_tool_summary(progress_events)` -> capped list of sanitized entries)
- `RAG_pipeline/backend/src/generation/conversational_rag.py` (set the column on the
  assistant `ConversationMessage` at both persist sites, using the helper)
- `RAG_pipeline/backend/src/api/schemas.py` (optional `runtime_tool_summary` field on
  `MessageResponse`; backend-only, frontend unaffected) — only if it doesn't change SSE
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py` (builder unit tests:
  field derivation from list vs single-object results, cap, no raw-log leakage)
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py`
  (runtime turn persists the column; non-runtime turn leaves it null)

**Critical area**: no — write-only persistence of already-sanitized data; no RBAC
decision and no replay yet. (Reclassify if the builder ends up touching scope logic.)

**Depends on**: none

## Slice 2: Replay the summary into the next turn's context

**Delivers**: On a new turn, the most recent prior `runtime_tool_summary` in the
conversation is injected into the runtime tool-use context, so a follow-up like "show me
the logs for that execution" resolves the `execution_id` from memory instead of
re-discovering it or asking the user.

**Expected files**:
- `RAG_pipeline/backend/src/generation/conversational_rag.py` (find the latest prior
  assistant message with a non-null summary; pass it into the runtime loop)
- `RAG_pipeline/backend/src/generation/agent_loop.py` (accept `prior_runtime_summary`
  and fold it into the runtime context note alongside the existing guide)
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py` (context note includes
  the prior summary when provided; unchanged when absent)
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py`
  (two-turn test: turn 1 detects a failure, turn 2 "show me the logs for that execution"
  resolves the `execution_id` from the injected summary; RBAC replay test: a summary is
  only injected within its own conversation — no cross-project reference leaks)

**Critical area**: yes — Auth/RBAC (replay must not surface data beyond the turn's
scope). `/inspect` required.

**Depends on**: Slice 1

## Notes

- Replay window decision (resolve at /plan for Slice 2): most-recent summary only
  (recommended — bounded, covers the "that execution" case) vs accumulate last N.
- Summaries are built only from `loop_outcome.progress_events`, which are already
  redacted and size-bounded by `runtime/client.py`; the builder stores reference fields
  only, never raw `data`/log text.
- No SSE event, LLM tool vocabulary, or frontend changes in either slice.
