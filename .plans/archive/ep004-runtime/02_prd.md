---
feature: EP-004 TMC Live Integration
pipeline_step: 2_prd
---

## Executive Summary

EP-004 integrates live Talend Management Console (TMC) execution data into the
IntoData KB chat experience. The platform should let authenticated users ask
questions such as "what jobs failed in the last 24 hours?" and receive answers
grounded in live runtime data, not only indexed documentation.

The epic remains a Talend TMC delivery epic, but the implementation must build a
platform-agnostic live runtime/logs layer. Talend TMC is the first provider
adapter. Future adapters, such as Workato, MuleSoft, Azure Data Factory, or a
generic HTTP/log provider, should be able to implement the same runtime contract
without changing the chat loop.

The epic covers six backlog stories: Dockerizing the external TMC server
(`US-022`), adding it to the platform stack (`US-023`), registering the limited
runtime tool surface backed by TMC (`US-025`), adding a platform-agnostic
tool-use loop to chat (`US-024`), showing live-runtime availability in the
frontend (`US-027`), and validating the PoC against real TMC data (`US-026`).

The largest technical risk is `US-024`. The existing RAG chat path has a
constrained read-only agent trace and SSE `step` events, but it does not yet
support external live tool calls. The implementation must extend the existing
provider and streaming contracts without bypassing auth, usage logging, or
normal RAG behavior.

## Problem

IntoData KB can answer questions from indexed documents and internal RAG support
layers. It cannot currently call live operational systems during a conversation.
For the Lobster PoC, that means users cannot ask the KB chat for current Talend
execution failures, latest run status, or execution logs.

The deeper platform need is broader than TMC: users should be able to ask about
runtime execution state and logs for any supported integration platform. That
does not mean arbitrary raw log browsing. It means each platform must implement
a bounded runtime adapter contract that normalizes executions, failures, logs,
and provider status.

The TMC server is external to this repository and is not present locally yet.
It is expected to expose live Talend TMC execution and log tools. Until that
server source, image, or API contract is available, the platform side can define
the generic runtime boundary and test against a mock adapter.

## Goals

- Build a platform-agnostic runtime/logs contract for execution monitoring.
- Implement Talend TMC as the first runtime provider adapter.
- Deploy a TMC provider service alongside the existing customer stack.
- Keep Talend credentials isolated in the TMC service via Docker secrets.
- Register only the four generic runtime capabilities needed for the PoC.
- Add a bounded, provider-agnostic tool-use loop to conversational chat.
- Preserve existing RAG behavior, citations, usage tracking, memory, and SSE
  stream semantics.
- Surface runtime tool-call progress and result visibility in streaming chat.
- Show live-runtime availability and provider name in the chat UI without
  blocking normal chat.
- Validate first with mock runtime data, then with real Talend TMC data before
  the epic is considered done.

## Non-Goals

- An arbitrary raw-log browser for any source without an adapter contract.
- Registering the full 39-tool TMC server surface.
- Direct Talend API calls from the RAG backend.
- Parallel tool execution.
- Persisted tool-call history as a product feature.
- Tool discovery or admin UI for arbitrary tool registration.
- EP-009 runtime scorecard scoring from TMC metrics.
- TMC/runtime load testing or stress testing.
- Additional role restrictions beyond existing authenticated chat access.

## User Stories

- `US-022` Dockerize TMC MCP Server: package the external TMC server as a
  provider service that can start consistently on a customer server.
- `US-023` Add TMC service to docker-compose: run the TMC provider service as
  part of the platform stack with healthcheck and Docker-secret token handling.
- `US-024` Implement Tool-Use Loop in LLM Client: let chat detect, execute,
  append, and continue from LLM runtime tool calls with a max-iteration cap.
- `US-025` Register TMC Tools in Platform: expose generic runtime capabilities
  backed by the TMC PoC operations.
- `US-026` PoC Scope Validation: prove live runtime answers work for failed
  jobs and latest execution status using real TMC data.
- `US-027` Frontend TMC Status Indicator: show live runtime connected or
  unavailable status, with the configured provider label, in chat.

## Functional Requirements

### Deployment and Configuration

- FR-1: The platform stack must include a runtime provider service for Talend
  TMC once the external TMC server image/source is available.
- FR-2: The runtime provider service must expose an internal health endpoint or
  equivalent readiness signal consumable by the platform.
- FR-3: The Talend API token must be provided to the TMC provider service as a
  Docker secret, not as a plaintext environment variable.
- FR-4: Runtime provider selection and provider-specific configuration must be
  environment driven. Initial provider key: `talend_tmc`.
- FR-5: TMC region/base URL/customer-specific configuration must be environment
  driven and documented, with no hardcoded customer values.
- FR-6: If no runtime provider is configured, platform startup and normal chat
  must still work. Runtime status should report disabled or unavailable.

### Runtime Provider Contract

- FR-7: The platform must define generic runtime capabilities:
  - `list_executions`
  - `get_execution_details`
  - `detect_failures`
  - `get_execution_logs`
- FR-8: The platform must define normalized models for:
  - `RuntimeProviderStatus`
  - `RuntimeExecution`
  - `RuntimeFailure`
  - `RuntimeLogEvent`
  - tool success/error results
- FR-9: The Talend TMC adapter must map generic capabilities to the PoC TMC
  operations:
  - `list_executions` -> `get_task_executions`
  - `get_execution_details` -> `get_execution_details`
  - `detect_failures` -> `detect_execution_failures`
  - `get_execution_logs` -> `get_execution_logs`
- FR-10: The runtime contract must be stable enough for future providers to
  implement without changing chat-generation code.

### Tool Registry and Router

- FR-11: The platform must define a fixed allowlist registry for the generic
  runtime capabilities. Only the configured provider adapter can translate them
  to provider-specific calls.
- FR-12: Each registered runtime tool must have a name, description, input
  schema, output handling contract, and timeout.
- FR-13: Calls to tools outside the allowlist must return a structured
  not-available tool result and must not reach any provider service.
- FR-14: Tool execution must be async and bounded by per-call timeout and result
  size limits.
- FR-15: Tool results must be sanitized before insertion into LLM context, logs,
  or SSE data.

### Tool-Use Chat Loop

- FR-16: The chat path must support a bounded loop:
  `generate -> detect runtime tool call -> execute tool -> append result -> continue`.
- FR-17: The loop must have a configurable maximum iteration count with a safe
  default that prevents infinite loops.
- FR-18: The loop must be provider-adapted through the existing RAG LLM provider
  pattern. It must not be Claude-only.
- FR-19: Providers without structured tool-call support must degrade gracefully:
  no live data claims, normal RAG remains available, and runtime status/progress
  indicates unavailable for live tool use.
- FR-20: Tool calls must not bypass existing authentication, conversation
  ownership checks, usage logging, or chat persistence.
- FR-21: When the runtime provider is unavailable or a tool call fails, the
  final answer must say live runtime data could not be retrieved. It must not
  fabricate execution status.

### Streaming and Frontend

- FR-22: Streaming chat must emit typed runtime tool progress events, such as
  `runtime_tool_call` and `runtime_tool_result`, or an equivalent contract that
  is documented in the PRD/slice and parsed by the frontend.
- FR-23: New SSE events must not break existing `step`, `sources`, `token`,
  `usage`, and `done` consumers.
- FR-24: The frontend stream parser must validate new event payloads with Zod
  or equivalent narrowing. No `any`.
- FR-25: The chat UI must render runtime status as connected, unavailable, or
  disabled/unknown, with provider label such as `Talend TMC`.
- FR-26: The runtime status indicator must not block message submission or
  normal RAG answers when unavailable.

### Validation

- FR-27: Unit tests must cover successful runtime tool execution, unsupported
  tool rejection, provider timeout/error, result-size limiting, loop
  max-iteration stop, and provider-without-tools fallback.
- FR-28: Frontend tests must cover parsing/rendering new streaming events and
  rendering connected/unavailable runtime status.
- FR-29: Mock/integration tests must prove the platform can answer with mocked
  runtime execution data before real credentials are used.
- FR-30: Final epic acceptance requires real Talend TMC validation:
  - "what jobs failed in the last 24 hours?"
  - "when did job X last run and what was the result?"

## Non-Functional Requirements

- NFR-1: Security: Talend API tokens must never appear in source code, `.env`
  templates as real values, logs, SSE payloads, frontend state, stored
  conversation messages, or LLM prompts.
- NFR-2: Isolation: runtime provider unavailability must not break normal
  document chat.
- NFR-3: Latency: runtime tool calls must be timeout-bounded and visibly
  surfaced to the user while waiting.
- NFR-4: Reliability: unknown tools, malformed tool arguments, provider errors,
  and provider failures must produce controlled responses, not 500s in chat.
- NFR-5: Observability: tool-call attempts, success/failure, latency, provider,
  and selected tool name must be logged without sensitive arguments or results.
- NFR-6: Compatibility: Existing conversation API response models, persisted
  messages, source citations, doc-gap metadata, and usage accounting must remain
  compatible.
- NFR-7: Maintainability: The runtime registry/router should be generic enough
  for future integrations, but the shipped PoC surface remains the four generic
  execution-monitoring tools.
- NFR-8: Type safety: Python function signatures must be typed, Pydantic v2
  models must validate external boundaries, and TypeScript must use strict
  types with Zod for API/SSE payload validation.

## Architecture Impact

### RAG Backend

The most likely backend changes are in:

- `RAG_pipeline/backend/src/generation/llm_client.py`
- a new `RAG_pipeline/backend/src/generation/agent_loop.py` or equivalent
- `RAG_pipeline/backend/src/generation/conversational_rag.py`
- `RAG_pipeline/backend/src/generation/agent_orchestrator.py`
- `RAG_pipeline/backend/src/api/routes/conversations.py`
- a new runtime status route such as `/api/v1/runtime/status`
- new runtime modules such as:
  - `RAG_pipeline/backend/src/runtime/models.py`
  - `RAG_pipeline/backend/src/runtime/registry.py`
  - `RAG_pipeline/backend/src/runtime/client.py`
  - `RAG_pipeline/backend/src/runtime/providers/mock.py`
  - `RAG_pipeline/backend/src/runtime/providers/talend_tmc.py`

The implementation should follow the existing provider registry pattern in
`llm_client.py`, where provider-specific behavior is hidden behind small
adapter functions and resolved at call time for testability. The runtime tool
loop should not replace `_prepare_rag_context()` or the retrieval ladder; it
should wrap the final-generation step only when live runtime tools are available
and relevant.

### Frontend

The frontend impact is primarily:

- stream parser updates in `RAG_pipeline/frontend/hooks/useStreamMessage.ts`
- schema updates in `RAG_pipeline/frontend/types/`
- message rendering updates for runtime tool progress/results
- status indicator UI in the chat shell/sidebar/header
- BFF proxy route for the runtime status endpoint

The BFF pattern must be preserved: browser code calls Next.js route handlers,
and route handlers proxy to FastAPI using server-side `FASTAPI_URL`.

### Deployment

The root `docker-compose.yml` will need a Talend TMC runtime provider service
only after the external server source/image is available. The compose change
must include:

- service name and internal network endpoint
- healthcheck
- Docker secret for `TALEND_API_TOKEN`
- environment-driven region/base URL
- provider selection such as `RUNTIME_PROVIDER=talend_tmc`
- no public port unless explicitly required for operations

### Security and RBAC

For this PoC, all authenticated chat users can access live runtime data. The
runtime router still needs a strict allowlist and must not expose arbitrary
external actions. Existing authentication and conversation ownership checks
must remain the access boundary.

## Implementation Phases

1. Runtime status contract and chat indicator.
   - Define generic runtime status.
   - Add backend status route and frontend BFF/UI.
   - Work without an external TMC server.

2. Runtime provider contract and mock adapter.
   - Define generic capabilities and normalized models.
   - Add registry/router, mock provider, timeout/result caps, sanitization, and
     unsupported-tool behavior.

3. Bounded runtime tool-use loop.
   - Add provider-aware structured tool-call support.
   - Preserve normal RAG generation when runtime tools are unavailable.
   - Emit streaming runtime progress events.

4. Frontend runtime progress visibility.
   - Parse and render runtime tool-call/result events.
   - Keep existing stream events compatible.

5. Talend TMC provider service integration.
   - Import or obtain the external TMC server source/image.
   - Add Dockerfile or image reference.
   - Add compose service, healthcheck, secrets, docs, and Talend adapter.

6. PoC validation.
   - Run mocked happy-path validation first.
   - Run real Talend TMC validation with provided token/region.
   - Record evidence that failed-job and latest-execution questions use live
     TMC data through the generic runtime contract.

## Risks and Open Questions

- R-1: The external TMC server is not present locally. The real-provider slice
  may be blocked until source, image, or an API contract is provided.
- R-2: The TMC server protocol is unknown. It may expose MCP stdio, HTTP, or
  another transport; the Talend adapter must adapt once confirmed.
- R-3: Current RAG chat providers are `kimi` and `ollama`; structured tool-call
  support may require adding or adapting an OpenAI-compatible/Anthropic-style
  tool interface.
- R-4: Ollama/local models may not reliably emit structured tool calls. The
  fallback behavior must be explicit.
- R-5: Runtime logs may contain sensitive operational data. Sanitization and
  result caps are required before LLM insertion.
- R-6: Natural-language job names may not map cleanly to provider execution IDs
  or task IDs. A future slice may need deterministic task search/selection.
- R-7: Real TMC validation requires a Talend API token and region. Mock tests
  cannot close the epic by themselves.
- R-8: Over-generalizing the adapter contract could delay TMC delivery. The
  contract should cover execution monitoring only, not every possible log use
  case.
- Open Question: Should the final implementation store lightweight tool-call
  metadata in usage/audit logs for operations, while keeping full tool-call
  history out of product scope?
- Open Question: Which provider is the first target for reliable structured
  tool calling in the production chat chain?

## Acceptance Criteria

- AC-1: Runtime status reports disabled/unavailable/connected with provider
  label and is proxied through the frontend BFF.
- AC-2: The generic runtime tool list contains exactly:
  `list_executions`, `get_execution_details`, `detect_failures`, and
  `get_execution_logs` unless a future approved plan expands scope.
- AC-3: Unsupported runtime tool calls are rejected safely and do not call any
  provider service.
- AC-4: A mocked conversation can call `list_executions`, receive mock runtime
  execution data, and produce a final answer referencing that data.
- AC-5: A mocked runtime provider failure produces a final answer that says
  live runtime data could not be retrieved.
- AC-6: The loop stops at the configured max iteration count.
- AC-7: Streaming chat emits and frontend parses runtime progress events
  without breaking existing stream behavior.
- AC-8: The chat UI shows live runtime connected/unavailable/disabled and
  normal chat works when runtime data is unavailable.
- AC-9: `docker-compose` can start the Talend TMC provider service when
  configured, and the service healthcheck reports healthy.
- AC-10: The Talend API token is supplied via Docker secret and is not visible
  in normal compose environment output or app logs.
- AC-11: Real validation against Talend TMC answers failed-job and latest-run
  questions with live data.
- AC-12: Targeted backend and frontend tests pass, and the full validation step
  is run before commit.
