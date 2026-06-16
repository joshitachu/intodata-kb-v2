---
feature: EP-004 TMC Live Integration
slice: Runtime Provider Contract and Safe Mock Adapter
sub_project: rag_pipeline
critical_area: no
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

Slice 1 established a provider-agnostic runtime status surface. The platform
still lacks the backend contract that later chat tooling can call safely: there
is no normalized execution/log model, no allowlisted runtime tool registry, no
provider adapter protocol, and no safe mock provider for tests.

Slice 2 must create that backend runtime boundary without calling Talend TMC and
without modifying chat generation. The purpose is to make the future tool-use
loop call one generic runtime client, while each platform adapter handles its
own provider-specific mapping.

## Proposed Solution

Extend `RAG_pipeline/backend/src/runtime/` with a small Strategy-style provider
interface plus an allowlisted registry/router:

- Generic runtime tools:
  - `list_executions`
  - `get_execution_details`
  - `detect_failures`
  - `get_execution_logs`
- Normalized Pydantic models:
  - `RuntimeExecution`
  - `RuntimeFailure`
  - `RuntimeLogEvent`
  - `RuntimeToolDefinition`
  - `RuntimeToolCall`
  - `RuntimeToolResult`
  - structured runtime error payloads
- Provider protocol:
  - async methods for the four generic capabilities;
  - no Talend-specific names in the caller-facing client.
- Runtime registry:
  - fixed allowlist of tool definitions and input model schemas;
  - rejects unknown tools before reaching any provider.
- Runtime client/router:
  - validates tool arguments with Pydantic;
  - selects the configured provider adapter;
  - enforces per-call timeout and result size limits;
  - sanitizes bounded output before the result is eligible for LLM context;
  - returns controlled `RuntimeToolResult` failures for disabled/unavailable
    providers, unsupported tools, validation errors, provider errors, and
    timeouts.
- Mock provider:
  - deterministic, credential-free sample execution/failure/log data;
  - can be enabled with `runtime_provider_key="mock"`;
  - exists only for tests/local validation and does not call external systems.

This slice should extend the existing Slice 1 runtime status models instead of
replacing them. It should not touch `conversational_rag.py`, `llm_client.py`,
streaming SSE, frontend code, Docker Compose, or Talend provider code.

## Affected Files

- `RAG_pipeline/backend/src/config.py`
- `RAG_pipeline/backend/src/runtime/__init__.py`
- `RAG_pipeline/backend/src/runtime/models.py`
- `RAG_pipeline/backend/src/runtime/registry.py` (new)
- `RAG_pipeline/backend/src/runtime/client.py` (new)
- `RAG_pipeline/backend/src/runtime/providers/__init__.py` (new)
- `RAG_pipeline/backend/src/runtime/providers/mock.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_registry.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_client.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_mock_provider.py` (new)

## Architecture Contracts

- Runtime callers use generic tool names only. Provider-specific operation names
  are adapter internals.
- The allowlist is closed over the four PoC tools. Unknown tools return a
  structured not-available result and must not call any provider.
- Provider selection is driven by `settings.runtime_provider_key`. Empty means
  disabled. `mock` resolves to the mock adapter.
- Tool argument validation happens before provider execution.
- Tool execution is async and timeout-bounded with `settings.runtime_tool_timeout`.
- Tool results are size-bounded with `settings.runtime_tool_result_max_chars`
  before they can be inserted into prompts, logs, or future SSE events.
- Sanitization/redaction must remove obvious credential-bearing keys and values
  such as tokens, passwords, secrets, API keys, and authorization headers from
  nested dict/list payloads.
- Logs may include provider key, tool name, status, and latency. Logs must not
  include raw arguments, raw logs, credentials, or full tool result payloads.
- The mock provider must be deterministic and safe for unit tests.
- This slice does not change chat auth, conversation ownership, persistence,
  usage accounting, retrieval, or frontend behavior.

## Implementation Steps

1. Extend runtime settings:
   - Add `runtime_tool_timeout: float = 10.0`.
   - Add `runtime_tool_result_max_chars: int = 12000`.
   - Keep existing Slice 1 status settings intact.

2. Extend `runtime/models.py`:
   - Preserve `RuntimeConnectionStatus` and `RuntimeStatusResponse`.
   - Add runtime status enums for execution state and log severity.
   - Add normalized execution/failure/log-event models.
   - Add request/input models for the four generic tools:
     - list executions: optional project/job name, time window, limit;
     - execution details: execution ID;
     - failure detection: optional time window and limit;
     - execution logs: execution ID, optional limit/severity.
   - Add `RuntimeToolDefinition`, `RuntimeToolCall`, `RuntimeToolError`, and
     `RuntimeToolResult` models.

3. Add `runtime/registry.py`:
   - Define a fixed registry mapping tool names to descriptions, input model
     classes, timeouts, and result caps.
   - Add helpers to list definitions and resolve/validate a tool call.
   - Ensure unknown tool names return a controlled error path.

4. Add provider interface and resolution:
   - Define a typed provider `Protocol` or abstract base in `runtime/client.py`
     or `runtime/providers/__init__.py`.
   - Resolve providers by key in a string/function registry, following the
     existing LLM/embedder pattern that resolves at call time for patchability.
   - Support `mock` only in this slice.
   - Return provider-unavailable errors when no provider is configured or an
     unknown provider key is configured.

5. Add `runtime/providers/mock.py`:
   - Implement the four async generic methods.
   - Use small deterministic fixtures with at least:
     - one successful execution,
     - one failed execution,
     - failure summary data,
     - log events with timestamps/severity/message.
   - Never read env secrets or call the network.

6. Add `runtime/client.py` router:
   - Public API should be small, for example:
     - `list_runtime_tools() -> list[RuntimeToolDefinition]`
     - `execute_runtime_tool(call: RuntimeToolCall) -> RuntimeToolResult`
   - Validate the tool against the registry.
   - Resolve the provider.
   - Execute the provider method with `asyncio.wait_for` or equivalent timeout.
   - Catch specific validation/provider/timeout exceptions and return structured
     failure results.
   - Sanitize and size-bound output in one place.
   - Record latency on every result.

7. Add backend tests:
   - Registry lists exactly the four generic tools.
   - Registry rejects unsupported tools without provider calls.
   - Argument validation rejects malformed inputs.
   - Mock provider returns normalized execution, failure, and log models.
   - Client returns success for supported mock calls.
   - Client returns disabled/unavailable for empty provider config.
   - Client returns unavailable for unknown provider key.
   - Client handles provider exceptions and timeouts as structured failures.
   - Result-size limiting truncates oversized provider output.
   - Sanitization removes credential-looking fields from nested results.

8. Keep Slice 1 status compatible:
   - Do not change `/api/v1/runtime/status` response shape.
   - If `runtime_provider_key="mock"`, status may still depend on the Slice 1
     health URL behavior until a later slice chooses tighter integration.

## Verification Strategy

- Backend syntax:
  - `cd RAG_pipeline/backend && python3 -m py_compile src/config.py src/runtime/models.py src/runtime/registry.py src/runtime/client.py src/runtime/providers/__init__.py src/runtime/providers/mock.py`

- Backend targeted tests:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_runtime/test_registry.py tests/test_runtime/test_client.py tests/test_runtime/test_mock_provider.py`

- Compatibility smoke:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_api/test_runtime_status.py`

- Broader optional smoke before moving to Slice 3:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_runtime tests/test_api/test_runtime_status.py tests/test_generation/test_llm_client.py`

## Out of Scope

- LLM tool-call parsing or agent-loop behavior.
- Any changes to `RAG_pipeline/backend/src/generation/conversational_rag.py`.
- Any changes to `RAG_pipeline/backend/src/generation/llm_client.py`.
- SSE runtime tool events.
- Frontend runtime progress rendering.
- Talend TMC provider implementation.
- Docker Compose changes and Docker secrets.
- Real Talend TMC validation.
