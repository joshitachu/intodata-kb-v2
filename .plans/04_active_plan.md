---
feature: Persist runtime tool results into conversation context (runtime tool memory)
slice: Slice 1 - Persist the runtime tool summary
sub_project: rag_pipeline
critical_area: no - write-only persistence of already-sanitized data; no RBAC decision or replay (replay is Slice 2)
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

Each chat turn's runtime tool results — including the `execution_id` from
`detect_failures`/`list_executions` — are discarded once the turn ends; only the
assistant's final text is persisted. There is no durable record of which executions a
turn referenced, which is the prerequisite for resolving follow-ups like "show me the
logs for that execution" (Slice 2). Slice 1 only *persists* a bounded, sanitized
summary; nothing reads it yet, so chat behaviour is unchanged.

## Proposed Solution

Add a nullable JSON column `runtime_tool_summary` to `conversation_messages` (additive
migration `031`), following the existing per-message metadata pattern (`sources`,
`image_data`, `doc_gap`). After the runtime tool loop runs, derive a compact summary
from `loop_outcome.progress_events` (the `runtime_tool_result` payloads, already
redacted and size-bounded by `runtime/client.py`) via a pure helper in `agent_loop.py`,
and set it on the assistant `ConversationMessage` at both persist sites in
`conversational_rag.py`. The summary holds reference fields only — `tool_name` plus
`execution_id`/`job_name`/`state`/`headline` when derivable — never raw `data` or log
text, capped to a small number of entries.

## Affected Files

- `RAG_pipeline/backend/src/db/models/conversation.py` — add
  `runtime_tool_summary: Mapped[list | None] = mapped_column(JSON, nullable=True)` to
  `ConversationMessage`.
- `RAG_pipeline/backend/alembic/versions/031_add_runtime_tool_summary.py` (new) —
  additive `add_column`, reversible `drop_column`.
- `RAG_pipeline/backend/src/generation/agent_loop.py` — pure helper
  `build_runtime_tool_summary(progress_events) -> list[dict] | None`.
- `RAG_pipeline/backend/src/generation/conversational_rag.py` — capture progress events
  across the precomputed/loop branches and set `runtime_tool_summary` on the assistant
  message at both persist sites (`run_conversational_rag` ~L1125,
  `run_conversational_rag_stream` ~L1293).
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py` — builder unit tests.
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py`
  — persistence tests (runtime turn sets the column; non-runtime/precomputed turn
  leaves it null).

Explicitly NOT changed: `src/schemas.py` `MessageResponse` (replay reads the ORM model
directly; the frontend does not need the field — kept out to minimise surface).

## Architecture Contracts

- Follow `RAG_pipeline/CLAUDE.md` conventions: Pydantic v2, SQLAlchemy
  `Mapped`/`mapped_column`, type hints on every signature, async I/O.
- Mirror the existing `conversation_messages` metadata pattern (plain `JSON` columns
  like `sources`/`image_data`; migration style of `029`/`030`). Column is nullable;
  additive only; no existing column altered; reversible `downgrade`.
- Summaries are built ONLY from `loop_outcome.progress_events`, which `runtime/client.py`
  has already redacted (`_redact`) and size-bounded. The builder must store reference
  fields only — never raw `data`, log `message` text, or secret-bearing fields.
- Provider-agnostic: no Talend-specific names. Works for `mock`/`talend_tmc`/`stored`.
- No change to the SSE event contract, the LLM tool vocabulary/input schemas, the
  frontend, or `runtime/client.py` sanitization. No RBAC decision in this slice (replay
  safety is Slice 2).
- PROJECT_MEMORY: runtime data stays structured in Postgres, never vectorized.

## Implementation Steps

1. Model + migration.
   - Add `runtime_tool_summary` nullable `JSON` column to `ConversationMessage`.
   - Create migration `031` (revises `030`): `op.add_column("conversation_messages",
     sa.Column("runtime_tool_summary", sa.JSON(), nullable=True))`; `downgrade` drops it.

2. Summary builder in `agent_loop.py`.
   - `build_runtime_tool_summary(progress_events: list[RuntimeProgressEvent]) -> list[dict] | None`.
   - Iterate `runtime_tool_result` events only. For each, read `tool_name` and `data`:
     - list of objects (executions/failures/logs): take up to a per-result cap and pull
       `execution_id`, `job_name`, `state` when present; for failures also a short
       `headline` from `error_summary` (truncated, e.g. ≤ 200 chars).
     - single object (`get_execution_details`): pull the same fields.
     - For `get_execution_logs`, store `execution_id` only (from a log entry) — never
       the log `message` text.
   - De-duplicate by `(tool_name, execution_id)`; cap total entries (≤ 10).
   - Return `None` when there are no runtime_tool_result events (so non-runtime turns
     store null). Defensive: skip malformed/missing fields rather than raise.

3. Capture progress across branches in `conversational_rag.py` (both functions).
   - Initialize `runtime_progress: list[RuntimeProgressEvent] = []` before the
     precomputed/loop branch; assign `runtime_progress = loop_outcome.progress_events`
     inside the `else` (loop) branch only.
   - At the persist site, set
     `runtime_tool_summary=build_runtime_tool_summary(runtime_progress)` on the
     assistant `ConversationMessage` (null when empty/precomputed).

4. Keep the slice boundary.
   - Do not read/inject the summary anywhere (that is Slice 2).
   - Do not add a `MessageResponse` field, SSE field, or frontend change.
   - Do not modify `runtime/client.py`, providers, or the tool registry.

## Verification Strategy

Syntax:
```bash
cd RAG_pipeline/backend && python3 -m py_compile \
  src/db/models/conversation.py \
  src/generation/agent_loop.py \
  src/generation/conversational_rag.py \
  alembic/versions/031_add_runtime_tool_summary.py
```

Builder unit tests (`test_agent_loop.py`):
- detect_failures result -> entry with `execution_id`/`job_name`/`headline`.
- list_executions result -> entries with `execution_id`/`job_name`/`state`.
- get_execution_logs result -> entry with `execution_id` only, NO message text present
  in any summary value.
- single-object (`get_execution_details`) result handled.
- cap respected; de-dup by `(tool_name, execution_id)`; empty/no-runtime -> `None`;
  malformed payload -> skipped, no exception.

Persistence tests (`test_conversational_rag_runtime_tools.py`):
- A runtime turn (mocked tool-aware decisions + mock/stored provider) writes a non-null
  `runtime_tool_summary` containing the execution reference.
- A precomputed / non-runtime turn leaves `runtime_tool_summary` null.

Migration (offline, no DB mutation):
```bash
cd RAG_pipeline/backend && uv run alembic upgrade 030:031 --sql && uv run alembic downgrade 031:030 --sql
```

Regression:
```bash
cd RAG_pipeline/backend && uv run pytest \
  tests/test_generation/test_agent_loop.py \
  tests/test_generation/test_conversational_rag_runtime_tools.py \
  tests/test_runtime
```
Then full suite `uv run pytest`.

## Out of Scope

- Reading/injecting the summary into any LLM context (Slice 2).
- RBAC replay safety (Slice 2).
- `MessageResponse`/API schema and frontend changes.
- SSE contract, tool vocabulary, or input-schema changes.
- `runtime/client.py`, provider, or registry changes.
- Refreshing underlying runtime data; vectorizing into Qdrant.
