---
feature: EP-004 TMC Live Integration
pipeline_step: 1_grill
date: 2026-06-16
---

## Feature

EP-004: TMC Live Integration, implemented through a platform-agnostic live
runtime/logs layer with Talend TMC as the first provider adapter.

Backlog source:
- `/Users/joshuakabel/Documents/Source/backlog/backlog/epics/EP-004-tmc-live-integration.md`
- `US-022`: Dockerize TMC MCP Server
- `US-023`: Add TMC service to docker-compose
- `US-024`: Implement Tool-Use Loop in LLM Client
- `US-025`: Register TMC Tools in Platform
- `US-026`: PoC Scope Validation
- `US-027`: Frontend TMC Status Indicator

The implementation target is the whole epic, likely sliced per user story if the
dependency graph allows it. The product value is still Talend TMC live data for
the Lobster PoC. The internal design should avoid TMC-only abstractions by
introducing a generic runtime provider contract that future platforms can
implement.

## Problem Statement

The IntoData KB platform can answer from indexed documentation and internal RAG
layers, but it cannot query live execution state, failure signals, or logs from
runtime platforms during chat. For the Lobster PoC, the first concrete provider
is Talend Management Console (TMC), where users need conversational access to
live execution state, failure detection, and execution logs without leaving the
KB chat interface.

The desired Talend TMC server is external to this repository and not present on
this machine yet. It is expected to expose live Talend TMC execution/log data.
The platform must integrate that server without hardcoding customer-specific
details, without exposing Talend API tokens to the RAG backend/frontend/LLM, and
without locking the chat tool architecture to Talend-only names.

## Definition of Done

- A generic live runtime/logs contract exists for execution monitoring:
  - `list_executions`
  - `get_execution_details`
  - `detect_failures`
  - `get_execution_logs`
- Generic normalized models exist for runtime status, executions, failures, and
  log events.
- Talend TMC is implemented as the first runtime provider adapter. It maps the
  generic runtime tools to the TMC PoC operations:
  - `get_task_executions`
  - `get_execution_details`
  - `detect_execution_failures`
  - `get_execution_logs`
- The TMC server can run as a Dockerized service in the customer stack.
- `docker-compose.yml` has a TMC runtime provider service with a healthcheck
  and secure token handling. The Talend token is managed as a Docker secret, not
  plaintext env.
- The chat path has a platform-agnostic tool-use loop that can:
  - ask the configured LLM for an answer with registered runtime tool definitions;
  - detect a runtime tool call;
  - execute only allowlisted runtime tools through the configured provider;
  - append the normalized tool result;
  - continue generation;
  - stop at a bounded max-iteration limit.
- Out-of-scope or unknown tool calls are rejected with a tool result that lets
  the LLM continue gracefully.
- Streaming chat emits visible runtime tool progress events, such as
  `runtime_tool_call` and `runtime_tool_result`, or an equivalent typed event
  contract that the frontend can render without breaking existing `step`,
  `sources`, `token`, `usage`, and `done` events.
- A chat-visible live-runtime status indicator shows connected/unavailable and
  includes the configured provider label, initially `Talend TMC`.
- All authenticated chat users can use live runtime data in this PoC. No extra
  role-level restriction is required beyond existing authentication.
- Verification happens in two layers:
  - mocked/unit tests first for runtime tool-loop behavior, unavailable provider
    behavior, unsupported tool rejection, and status endpoint behavior;
  - real validation against Talend TMC before done, including:
    - "what jobs failed in the last 24 hours?"
    - "when did job X last run and what was the result?"

## Out of Scope

- An arbitrary raw-log browser for any source without an adapter contract.
- Registering all 39 TMC tools.
- Parallel tool execution.
- Persisting full tool-call history as a product feature.
- Tool discovery UI.
- EP-009 runtime scoring from TMC metrics.
- Load or stress testing the runtime/TMC connection.
- Calling the Talend API directly from the RAG backend instead of using the
  runtime provider adapter/service boundary.
- Customer-specific hardcoded job names, regions, project IDs, or group names.

## Solution Rationale

The linked stories de-risk the epic in dependency order while keeping the
implementation extensible:

1. Define a generic runtime status/tool contract and show provider availability
   in chat (`US-027` foundation).
2. Add a small allowlisted runtime registry/router for execution-monitoring
   tools (`US-025`), with a mock provider for tests.
3. Add the LLM tool-use loop in the RAG chat path (`US-024`), reusing existing
   streaming/chat infrastructure where possible.
4. Package and run the Talend TMC provider service in the stack (`US-022`,
   `US-023`) once the external server source/image/protocol is available.
5. Validate with mock data first and real TMC data before closing (`US-026`).

The tool loop should be platform-agnostic rather than Claude-only or
Talend-only. The current RAG backend uses a provider chain and already has
platform-specific LLM adapter patterns in `llm_client.py`; the new loop should
follow that pattern. Providers that cannot perform structured tool calling
should fail closed or continue with live runtime unavailable, not fabricate live
data.

## Assumptions and Risk

- The external Talend TMC server source or image will become available before
  the real-provider slice. It is not present locally under the path listed in
  the backlog.
- The TMC server exposes a callable protocol that the platform can reach from
  Docker Compose, and it supports the four PoC operations with stable schemas.
- Talend API token and region/configuration will be provided for real PoC
  validation.
- The generic runtime contract can normalize TMC concepts without losing the
  fields users need for failure and latest-run questions.
- Future platforms, such as Workato, MuleSoft, Azure Data Factory, or generic
  HTTP/log providers, can add adapters later without changing the chat loop.
- The chat provider chain can support a common tool-call abstraction. If a
  provider lacks native tools, the loop must degrade gracefully.
- Runtime logs may contain sensitive operational information. Tool results must
  be bounded and sanitized before entering the LLM context or SSE stream.
- Live job names may not map cleanly from user phrasing to provider task IDs.
  The first slice may need explicit query parameters or a deterministic
  resolver.
- Tool latency may affect streaming UX. The UI should show status/progress
  while waiting and should not make normal RAG chat unusable if runtime data is
  slow or unavailable.

## Critical Areas Touched

- RAG chat generation:
  - `RAG_pipeline/backend/src/generation/llm_client.py`
  - likely a new `RAG_pipeline/backend/src/generation/agent_loop.py` or similar
  - `RAG_pipeline/backend/src/generation/conversational_rag.py`
  - `RAG_pipeline/backend/src/generation/agent_orchestrator.py`
- RAG conversation streaming/API event contract:
  - `RAG_pipeline/backend/src/api/routes/conversations.py`
  - frontend stream parser and message rendering under `RAG_pipeline/frontend/`
- Runtime tool/status routing:
  - likely a RAG backend `/api/v1/runtime/status` route plus BFF proxy route, or
    an equivalent route following the existing BFF pattern.
- Deployment:
  - root `docker-compose.yml`
  - deployment env/secret documentation

This is full-pipeline work. If implementation touches MCP API routes, RAG
retrieval, auth/RBAC, or the harness pipeline, it requires an `/inspect` audit
before commit. Even if it stays in RAG generation/chat, the epic is large enough
to keep the full grill -> PRD -> slices -> plan -> approve -> build -> inspect
-> validate gate.

## Risk if Wrong

- The chat could present stale, fabricated, or cross-customer execution status
  as live runtime truth.
- Talend API tokens or sensitive execution logs could leak into prompts, SSE
  events, browser state, logs, or persisted messages.
- A model could call unsupported tools or loop repeatedly, increasing latency
  and cost or hanging the conversation.
- Runtime provider downtime could break normal chat if the integration is not
  isolated.
- A weak runtime registry could become an unsafe general external-action
  surface.
- Incorrect Docker secret handling could put customer credentials in plaintext
  config, process listings, or logs.
