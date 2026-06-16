---
feature: EP-004 TMC Live Integration
slice: Frontend Runtime Progress and Result Visibility
sub_project: rag_pipeline
critical_area: no
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

Slice 3 added backend runtime tool-use events to the conversation SSE stream:

- `runtime_tool_call` with `{ "tool_name": string, "arguments": object }`
- `runtime_tool_result` with the sanitized `RuntimeToolResult` payload:
  `{ "tool_name": string, "success": boolean, "data": ..., "error": ..., "latency_ms": number, "truncated": boolean }`

The frontend currently ignores those events. Users can see normal streaming text
and the existing read-only RAG `step` label, but they cannot tell that chat is
checking live runtime data or whether that tool call succeeded or failed. This
creates ambiguity during TMC/log questions: the answer may arrive eventually,
but there is no visible progress or result status while the runtime call is in
flight.

Slice 4 must parse the new backend events, keep existing stream behavior
compatible, and render a compact runtime status in the streaming assistant
message without exposing raw logs or sensitive payload data.

## Proposed Solution

Extend the existing frontend stream pipeline instead of adding a separate
runtime polling path:

- Add Zod schemas and TypeScript types for runtime stream events in
  `RAG_pipeline/frontend/types/index.ts`.
- Extend `StreamState` in `hooks/useStreamMessage.ts` with runtime progress
  state, for example `runtimeEvents` or `runtimeTools`.
- Parse `runtime_tool_call` and `runtime_tool_result` in the same SSE dispatcher
  that already parses `step`, `sources`, `token`, `usage`, and `done`.
- Add a small presentation component, likely `RuntimeToolStatus`, that renders
  safe summaries only:
  - while a call is pending: "Checking runtime: Detect failures";
  - on success: "Runtime check complete";
  - on failure: "Runtime check unavailable" plus the structured error code or
    short message;
  - when truncated: show a small "truncated" hint, not raw result content.
- Pass runtime progress from `MessageList` into `MessageBubble` for the live
  assistant bubble.
- Render runtime progress near the existing streaming status text, not as a
  nested card or a verbose log viewer.

The BFF stream route should remain a pass-through. It already forwards all SSE
events from FastAPI unchanged, so schema validation and state updates belong in
the client hook.

## Affected Files

- `RAG_pipeline/frontend/types/index.ts`
- `RAG_pipeline/frontend/hooks/useStreamMessage.ts`
- `RAG_pipeline/frontend/components/MessageBubble.tsx`
- `RAG_pipeline/frontend/components/MessageList.tsx`
- `RAG_pipeline/frontend/components/RuntimeToolStatus.tsx` (new, optional but preferred)
- `RAG_pipeline/frontend/tests/hooks/useStreamMessage.test.ts`
- `RAG_pipeline/frontend/tests/components/MessageList.test.tsx`
- `RAG_pipeline/frontend/tests/components/RuntimeToolStatus.test.tsx` (new if component is extracted)

## Architecture Contracts

- The frontend treats runtime progress as additive stream state. Existing
  streams with only `step`, `sources`, `token`, `usage`, and `done` must behave
  exactly as they do today.
- The BFF route at
  `app/api/conversations/[id]/messages/stream/route.ts` remains a transparent
  SSE proxy unless implementation finds a concrete bug there.
- All runtime event parsing uses Zod. Invalid runtime event payloads are ignored
  or marked unavailable without crashing the stream.
- No `any`. Runtime payloads with arbitrary `data` use `unknown` and are narrowed
  only for safe display fields.
- Runtime UI must be provider-agnostic. Labels should say "runtime" or "live
  runtime", not "Talend TMC", except where a backend status endpoint already
  supplies a provider label.
- Do not render raw `data` payloads or raw logs in the chat bubble. Slice 2
  sanitizes backend data, but the frontend still shows only status summaries.
- The persisted conversation message schema does not change in this slice.
  Runtime progress is live-stream state, not historical message content.
- Existing `RuntimeStatusIndicator` remains the connection/status indicator in
  the chat header. Runtime tool progress is per-message stream state and should
  be visually distinct from global connection status.
- Deep Dive streaming is out of scope and should not be affected.
- Existing failed stream retry behavior stays intact.

## Implementation Steps

1. Add runtime SSE schemas and types.
   - In `types/index.ts`, add:
     - `RuntimeToolCallEventSchema`
     - `RuntimeToolResultEventSchema`
     - `RuntimeToolProgress` or equivalent UI-state type
   - Model result errors with a narrow schema:
     - `code: string`
     - `message: string`
   - Use `z.unknown()` for `data` and `z.record(z.unknown())` for arguments.

2. Extend `StreamState`.
   - Add a runtime progress collection, for example:
     - `runtimeTools: RuntimeToolProgress[]`
   - Initialize it in `INITIAL_STATE`, `reset()`, and the `sendMessage()` start
     state.
   - Keep the existing `steps` array unchanged.

3. Parse runtime SSE events in `useStreamMessage`.
   - On `runtime_tool_call`, add or update a progress item with status
     `running`, the tool name, and safe argument metadata if needed.
   - On `runtime_tool_result`, match the item by `tool_name` when no stronger ID
     exists, then update status to `completed` or `failed`.
   - Preserve event ordering and all existing event handling.
   - Do not set `error` for a failed runtime tool result unless the backend sends
     a stream-level `error` event; tool failure should remain visible as runtime
     progress while the assistant can still answer safely.

4. Add safe label helpers.
   - Map generic tool names to readable labels:
     - `list_executions` -> "List executions"
     - `get_execution_details` -> "Get execution details"
     - `detect_failures` -> "Detect failures"
     - `get_execution_logs` -> "Get execution logs"
   - Unknown tool names fall back to a prettified generic label.
   - Keep labels platform-agnostic.

5. Add or update runtime progress rendering.
   - Prefer a focused `RuntimeToolStatus` component so `MessageBubble.tsx` stays
     readable.
   - Display a compact inline row with the existing visual density:
     - spinner/pending icon for running;
     - check for success;
     - warning/error icon for failed/unavailable;
     - short latency when available.
   - Avoid cards, raw JSON, raw log text, and large payload previews.

6. Thread runtime state through chat components.
   - Pass runtime progress from `MessageList` to `MessageBubble` for the
     streaming assistant message.
   - Ensure auto-scroll reacts to runtime progress changes, not just token text.
   - Keep persisted messages unchanged because runtime progress is not currently
     stored with message history.

7. Update hook tests.
   - Assert `runtime_tool_call` creates a running runtime progress entry.
   - Assert `runtime_tool_result` marks that entry completed on success.
   - Assert failed result marks the runtime progress failed without setting
     stream-level `error`.
   - Assert old streams with only `step` and `done` still parse correctly.
   - Assert malformed runtime payloads do not crash and do not end streaming
     prematurely.

8. Update component tests.
   - Assert the streaming assistant bubble shows the runtime running label before
     answer text arrives.
   - Assert successful and failed runtime result summaries render.
   - Assert raw `data` payload content is not rendered.
   - Keep existing streamed failure/retry tests green.

9. Keep styling restrained and responsive.
   - Use existing Tailwind tokens and lucide icons.
   - Avoid nested cards.
   - Ensure the status row wraps safely on narrow screens and does not overlap
     message text, sources, retry controls, or usage metadata.

## Verification Strategy

- Frontend typecheck:
  - `cd RAG_pipeline/frontend && npx tsc --noEmit`

- Focused frontend tests:
  - `cd RAG_pipeline/frontend && npm test -- tests/hooks/useStreamMessage.test.ts tests/components/MessageList.test.tsx tests/components/RuntimeToolStatus.test.tsx`

- Broader frontend smoke:
  - `cd RAG_pipeline/frontend && npm test`

- Optional manual browser check after implementation:
  - Start frontend/backend stack as available.
  - Send a mocked runtime prompt that triggers `detect_failures`.
  - Confirm the stream shows runtime progress, then answer text, without raw
    result payloads.

## Out of Scope

- Backend runtime loop changes.
- Talend TMC provider adapter or real TMC connectivity.
- Docker Compose, secrets, or deployment changes.
- Persisting runtime tool events into conversation history.
- Rendering raw execution logs or detailed runtime result tables.
- Deep Dive stream changes.
- Global runtime status indicator redesign.
- Any retrieval, auth/RBAC, MCP API, or doc-generation harness changes.
