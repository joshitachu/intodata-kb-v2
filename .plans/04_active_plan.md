---
feature: Persist runtime tool results into conversation context (runtime tool memory)
slice: Slice 2 - Replay the summary into the next turn's context
sub_project: rag_pipeline
critical_area: yes - Auth/RBAC replay safety (a summary must only be injected within its own conversation; no cross-conversation/cross-project leakage)
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

Slice 1 persists `runtime_tool_summary` on assistant messages, but nothing reads it.
A follow-up like "show me the logs for that execution" still depends on the model
re-discovering the `execution_id` via a tool call. Slice 2 replays the most recent
prior summary into the next turn's runtime context so the id is simply present, making
reference resolution reliable rather than model-behaviour-dependent.

## Proposed Solution

Read the most recent prior `runtime_tool_summary` from the current conversation's
messages (mirroring `get_previous_sources`), and inject it into the runtime tool-use
context as a short "recent runtime context" note alongside the existing runtime guide.
The note carries only the already-sanitized summary fields; actual data access remains
RBAC-gated by the `stored` provider at fetch time. The summary is sourced only from the
same conversation (already verified user-owned by the route), so replay introduces no
new cross-conversation/cross-project exposure beyond what the user already saw in this
conversation.

## Affected Files

- `RAG_pipeline/backend/src/generation/conversational_rag.py` — add
  `get_latest_runtime_tool_summary(messages)` (mirrors `get_previous_sources`); compute
  it from `conversation.messages` BEFORE the runtime loop and pass it into
  `run_runtime_tool_loop` at both call sites (`run_conversational_rag`,
  `run_conversational_rag_stream`).
- `RAG_pipeline/backend/src/generation/agent_loop.py` — add a keyword-only
  `prior_runtime_summary: list[dict] | None = None` param to `run_runtime_tool_loop`;
  extend `_with_runtime_guide` to append a bounded "recent runtime context" note when a
  summary is provided.
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py` — guide includes the
  prior summary when provided; unchanged when absent/None.
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py`
  — two-turn resolution test; cross-conversation isolation (RBAC) test.

Explicitly NOT changed: schema/migrations (Slice 1), `runtime/client.py`, providers,
registry, SSE contract, tool vocabulary, frontend.

## Architecture Contracts

- Follow `RAG_pipeline/CLAUDE.md` conventions (Pydantic v2, type hints, async I/O) and
  mirror `get_previous_sources` for reading prior-message metadata.
- **RBAC replay safety (critical)**: the summary is read ONLY from the current
  `conversation.messages`; never from another conversation or a global store. The route
  already enforces `conversation.user_id == user.id`. A summary entry carries no raw
  data — only references the user already saw in this same conversation.
- **Data access stays gated**: replaying an `execution_id` into context does not bypass
  RBAC — if the model then calls `get_execution_logs`/`get_execution_details`, the
  `stored` provider still enforces `project_scope` at fetch time (Slice 2 of the
  held-runtime epic). The note must never include raw `data`/log text.
- Bounded: inject only the single most recent summary; reuse Slice 1's cap (≤10 entries)
  so context growth is O(1) per turn.
- Behaviour-preserving: when no prior summary exists (older messages, first turn,
  non-runtime history), the loop behaves exactly as today.
- No SSE event, LLM tool vocabulary/schema, or frontend change. No
  `runtime/client.py`/provider/registry change.

## Implementation Steps

1. Reader in `conversational_rag.py`.
   - `get_latest_runtime_tool_summary(messages: list[ConversationMessage]) -> list[dict] | None`:
     reverse-scan; return the first assistant message's non-null/non-empty
     `runtime_tool_summary`; else `None`. (Mirror `get_previous_sources`.)

2. Thread it into the loop (both call sites).
   - Before each `run_runtime_tool_loop(...)` call, compute
     `prior_runtime_summary = get_latest_runtime_tool_summary(conversation.messages)`
     (prior turns only — the current assistant message is not persisted yet).
   - Pass `prior_runtime_summary=prior_runtime_summary` to the loop.

3. Inject in `agent_loop.py`.
   - `run_runtime_tool_loop(..., *, project_scope=None, db=None, prior_runtime_summary=None)`.
   - Extend `_with_runtime_guide(messages, prior_runtime_summary=None)`: when a summary
     is present, append a concise, bounded note to the system content, e.g.
     "Recent runtime context from this conversation you may reference (re-fetch as
     needed): <compact rendering of execution_id/job_name/state/headline>". Cap length;
     render only summary fields.

4. Keep the boundary.
   - Do not alter the persisted column, migration, providers, client, registry, SSE, or
     frontend. Reading is purely additive to the runtime context.

## Verification Strategy

Syntax:
```bash
cd RAG_pipeline/backend && python3 -m py_compile \
  src/generation/agent_loop.py src/generation/conversational_rag.py
```

Unit (`test_agent_loop.py`):
- `_with_runtime_guide(messages, prior_runtime_summary=[...])` includes the
  `execution_id`/job/state text and keeps the base guide; with `None`/absent it is the
  Slice-1 behaviour (no recent-context note).
- A loop run with `prior_runtime_summary` forwards it into the merged system message
  (assert via the messages handed to a patched `generate_answer_with_runtime_tools`).

Integration (`test_conversational_rag_runtime_tools.py`):
- Two-turn: turn 1 (mocked decisions) detects a failure and persists the summary; turn 2
  ("show me the logs for that execution") — the injected note carries the prior
  `execution_id` (assert the id appears in the system message seen by the patched
  tool-aware LLM). Backend-only; LLM decisions mocked.
- RBAC isolation: a summary persisted on conversation A is NOT injected when running a
  turn on conversation B (the reader only sees `conversation.messages`).
- No raw data: the injected note contains only summary fields, never log message text.

Regression:
```bash
cd RAG_pipeline/backend && uv run pytest \
  tests/test_generation/test_agent_loop.py \
  tests/test_generation/test_conversational_rag_runtime_tools.py \
  tests/test_runtime
```
Then full suite `uv run pytest`. Because this is an Auth/RBAC change, run `/inspect`.

Optional live check (after backend rebuild + migration 031 applied): with
`RUNTIME_PROVIDER_KEY=stored` and `esbmain` seeded, ask "what failed?" then "show me the
logs for that execution" — the second turn should resolve the id without asking.

## Out of Scope

- Schema/migration changes (Slice 1 owns the column).
- Accumulating more than the most recent summary (single-summary replay only).
- Adding `project_id` to summary entries / scope-filtering the note (data fetch is
  already RBAC-gated by the provider; documented as the chosen trade-off).
- `MessageResponse`/API schema, SSE, frontend, runtime client, providers, registry.
- Refreshing underlying runtime data; vectorizing into Qdrant.
