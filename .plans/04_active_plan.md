---
feature: EP-004 TMC Live Integration
slice: Backend Runtime Tool-Use Loop with Mocked Answers
sub_project: rag_pipeline
critical_area: no
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

Slice 2 created the safe runtime boundary: a fixed generic tool allowlist,
normalized `RuntimeToolCall`/`RuntimeToolResult` models, a sanitized router, and
a deterministic mock provider. Chat still cannot use that boundary. A user can
ask about live runtime state, but the conversational RAG path only retrieves
documents and calls the LLM for a final answer.

Slice 3 must add a bounded backend tool-use loop so a conversational RAG request
can call the mock runtime tools and answer from the normalized tool result. The
loop must stay provider-agnostic, must not claim live runtime data when tool
support or provider access is unavailable, and must preserve the existing RAG
flow, ownership checks, persistence, citations, memory, usage accounting, and
streaming event contract.

## Proposed Solution

Add a small runtime-aware generation layer around the final-answer step, after
`_prepare_rag_context()` has already performed routing, retrieval, previous
source reuse, doc-gap detection, and source preparation.

The core will be a new `RAG_pipeline/backend/src/generation/agent_loop.py`
module that owns:

- a normalized LLM tool-decision/result model for runtime calls;
- a bounded loop:
  `generate -> runtime tool call -> execute_runtime_tool -> append tool result -> continue`;
- runtime progress event payloads for streaming callers;
- final-answer fallback behavior when the LLM/provider cannot issue structured
  tool calls.

`agent_loop.py` should reuse the Slice 2 public API:

- `list_runtime_tools()`
- `execute_runtime_tool(RuntimeToolCall(...))`
- `RuntimeToolResult`

`llm_client.py` should gain a tool-aware public function while keeping existing
`generate_answer()` and `generate_answer_stream()` compatible. The new function
should follow the current provider-chain and resolve-at-call-time pattern so
tests can patch provider behavior. For this slice:

- Kimi/OpenAI-compatible calls may use native tool schemas and parse
  `message.tool_calls` into the normalized loop model.
- Providers without structured tool support, including the current Ollama path
  unless implemented safely, should return a controlled "no tool support"
  outcome so normal RAG can continue without live-runtime claims.
- Tests may patch the new tool-aware function to force a runtime tool call
  without requiring an external LLM.

`conversational_rag.py` should invoke the loop only when there is no
`ctx.precomputed_answer`. The normal no-tool path should remain as close as
possible to today:

- existing document sources are still emitted before tokens;
- existing `step`, `sources`, `token`, `usage`, and `done` events remain valid;
- runtime-specific events are additive:
  - `runtime_tool_call`
  - `runtime_tool_result`

For Slice 3, when the runtime loop is active, the final answer can be emitted as
a normal `token` event after tool execution, even if it arrives as one chunk.
The non-runtime path should continue using `generate_answer_stream()`.

## Affected Files

- `RAG_pipeline/backend/src/config.py`
- `RAG_pipeline/backend/src/generation/agent_loop.py` (new)
- `RAG_pipeline/backend/src/generation/llm_client.py`
- `RAG_pipeline/backend/src/generation/conversational_rag.py`
- `RAG_pipeline/backend/src/generation/agent_orchestrator.py`
- `RAG_pipeline/backend/src/api/routes/conversations.py`
- `RAG_pipeline/backend/src/runtime/client.py`
- `RAG_pipeline/backend/src/runtime/models.py`
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py` (new)
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py` (new)
- `RAG_pipeline/backend/tests/test_generation/test_llm_client.py`
- `RAG_pipeline/backend/tests/test_api/test_conversations.py`

## Architecture Contracts

- The loop uses only the generic runtime tool names from Slice 2:
  `list_executions`, `get_execution_details`, `detect_failures`,
  `get_execution_logs`.
- No Talend/TMC-specific operation names may appear in generation, stream, or
  frontend-facing contracts.
- Tool execution remains backend-owned. The model may request an allowlisted
  tool, but `runtime/client.py` validates, sanitizes, bounds, and executes it.
- The loop has a hard iteration cap from settings, for example
  `runtime_tool_loop_max_iterations`, defaulting to a small value such as `2`.
- Unknown tools, invalid arguments, disabled runtime config, unavailable
  providers, provider errors, and timeouts must produce structured runtime
  results and a safe final answer. They must not produce fabricated live data.
- Tool results inserted back into LLM context must use sanitized/bounded
  `RuntimeToolResult.model_dump(mode="json")` data, never raw provider payloads.
- The route-level conversation ownership and project RBAC checks remain where
  they are in `api/routes/conversations.py`.
- Retrieval and source merging remain owned by `_prepare_rag_context()`; this
  slice must not modify `RAG_pipeline/backend/src/retrieval/`.
- Existing SSE clients remain compatible. New runtime events are additive, and
  the final stream still ends with `usage` and `done`.
- Usage accounting must not disappear. The implementation should aggregate
  tool-loop LLM call usage when provider usage is available; at minimum, final
  answer usage must still be emitted and persisted.
- Hindsight retention, correction detection, message persistence,
  `primary_source_layer`, `fallback_used`, and `doc_gap` metadata continue to
  behave as they do today.
- Existing `AgentTrace` remains the read-only RAG trace. Runtime tool progress
  should use dedicated runtime SSE events instead of consuming the bounded
  document-search trace slots.

## Implementation Steps

1. Add runtime loop settings.
   - Add `runtime_tool_loop_max_iterations: int = 2` to `src/config.py`.
   - Optionally add a narrowly named enable flag only if needed for clean
     fallback; prefer deriving availability from runtime config and provider
     tool-support results.

2. Add `generation/agent_loop.py`.
   - Define typed models/dataclasses for:
     - tool-aware LLM response: final text, runtime tool call, or no-tool-support;
     - runtime progress events;
     - loop outcome with final answer, progress events, usage, and whether
       runtime data was used.
   - Implement `run_runtime_tool_loop(messages, images=None)` or equivalent.
   - Use `list_runtime_tools()` to provide schemas to the tool-aware LLM call.
   - Execute requested tools with `execute_runtime_tool(RuntimeToolCall(...))`.
   - Append sanitized tool results as tool/context messages before the next LLM
     call.
   - Stop on final answer, no-tool-support, tool execution failure after a safe
     final response, or max iterations.

3. Extend `generation/llm_client.py` with a tool-aware API.
   - Keep `generate_answer()` and `generate_answer_stream()` unchanged for
     callers.
   - Add a provider-chain function such as
     `generate_answer_with_runtime_tools(messages, runtime_tools, images=None)`.
   - Resolve provider functions at call time, matching `_resolve()`.
   - Add Kimi/OpenAI-compatible native tool-call support if the SDK response
     shape is available and testable locally.
   - Return controlled no-tool-support for providers without a safe structured
     tool-call path.
   - Preserve or expose usage data from each call so the loop can aggregate or
     persist final usage.

4. Integrate non-streaming conversational RAG.
   - In `run_conversational_rag()`, after `_prepare_rag_context()`, use the
     runtime loop only when `ctx.precomputed_answer is None`.
   - If the loop returns no-tool-support, keep the existing `generate_answer()`
     path.
   - Persist the final answer exactly like current responses, including sources,
     latency, doc-gap metadata, usage, Hindsight retention, and correction
     detection.

5. Integrate streaming conversational RAG.
   - In `run_conversational_rag_stream()`, keep emitting existing `step` and
     `sources` events first.
   - If a runtime loop runs, yield `runtime_tool_call` and
     `runtime_tool_result` events as the loop progresses.
   - Emit the final loop answer through the existing `token` event type.
   - Preserve `usage` and `done` event ordering and payload compatibility.
   - If no runtime loop runs, preserve the current `generate_answer_stream()`
     behavior.

6. Update conversation route documentation/tests only as needed.
   - The route already relays arbitrary event types from
     `run_conversational_rag_stream()`.
   - Update the stream docstring/comment to include `runtime_tool_call` and
     `runtime_tool_result`.
   - Do not move auth, ownership, or project-access logic.

7. Add focused loop tests.
   - Mock an LLM response requesting `detect_failures`, assert the mock runtime
     provider result is appended and a final answer is produced.
   - Assert unknown or malformed tool calls are rejected through structured
     `RuntimeToolResult` failures.
   - Assert the max-iteration cap stops recursive tool calls.
   - Assert provider no-tool-support falls back without live-runtime claims.
   - Assert sanitized runtime results, not raw provider objects, are appended to
     LLM messages.

8. Add conversational RAG integration tests.
   - Patch `_prepare_rag_context()` to return a small context and patch the
     tool-aware LLM function to request a mock runtime tool.
   - Assert final answer content can reference the mocked runtime result.
   - Assert existing source persistence and doc-gap metadata are preserved.
   - Assert non-runtime/precomputed answers keep the current path.

9. Add SSE tests.
   - Patch `run_conversational_rag_stream()` or the lower loop layer, depending
     on existing test style, to validate the API stream contains:
     - `event: runtime_tool_call`
     - `event: runtime_tool_result`
     - `event: token`
     - `event: usage`
     - `event: done`
   - Keep existing error-stream behavior green.

10. Re-run existing compatibility tests around generation and runtime.
    - Existing LLM provider fallback tests should still pass.
    - Existing runtime registry/client/mock tests should still pass.
    - Existing conversation stream error tests should still pass.

## Verification Strategy

- Syntax for changed backend files:
  - `cd RAG_pipeline/backend && python3 -m py_compile src/config.py src/generation/agent_loop.py src/generation/llm_client.py src/generation/conversational_rag.py src/generation/agent_orchestrator.py src/api/routes/conversations.py src/runtime/client.py src/runtime/models.py`

- New and directly affected backend tests:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_generation/test_agent_loop.py tests/test_generation/test_conversational_rag_runtime_tools.py tests/test_generation/test_llm_client.py tests/test_api/test_conversations.py`

- Runtime compatibility tests:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_runtime`

- Broader backend smoke before inspection:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_generation tests/test_api/test_conversations.py tests/test_runtime`

- After implementation and before commit, run `/inspect` because this is the
  high-risk `US-024` loop even though it does not touch the listed critical
  retrieval/auth/MCP areas.

## Out of Scope

- Frontend parsing or rendering of runtime progress events. That is Slice 4.
- Talend TMC provider adapter implementation. That is Slice 5.
- Docker Compose, Docker secrets, and real TMC connectivity.
- Real-data validation against Talend TMC. That is Slice 6.
- Arbitrary raw log ingestion or generic log dashboards.
- Parallel runtime tool execution.
- Model-initiated write/mutation tools.
- Changes to retrieval, auth/RBAC, MCP API routes, or the doc-generation
  harness pipeline.
