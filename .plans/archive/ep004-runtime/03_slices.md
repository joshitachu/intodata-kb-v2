---
feature: EP-004 TMC Live Integration
pipeline_step: 3_slices
total_slices: 6
current_slice: 0
---

## Slice 1: Runtime Status Contract and Chat Indicator

**Delivers**: Users can see whether live runtime data is connected, unavailable, or disabled, with provider label, and normal chat remains usable when no runtime provider is configured.

**Expected files**:
- `RAG_pipeline/backend/src/config.py`
- `RAG_pipeline/backend/src/api/main.py`
- `RAG_pipeline/backend/src/api/routes/runtime.py` (new)
- `RAG_pipeline/backend/src/runtime/__init__.py` (new)
- `RAG_pipeline/backend/src/runtime/status.py` (new)
- `RAG_pipeline/backend/src/runtime/models.py` (new or started)
- `RAG_pipeline/backend/tests/test_api/test_runtime_status.py` (new)
- `RAG_pipeline/frontend/app/api/runtime/status/route.ts` (new)
- `RAG_pipeline/frontend/types/index.ts` or focused runtime schema file
- `RAG_pipeline/frontend/hooks/useRuntimeStatus.ts` (new)
- chat shell/header/sidebar component that will render the status
- `RAG_pipeline/frontend/tests/...` for status fetch/rendering

**Critical area**: no - RAG API/UI work only; does not touch RAG retrieval, auth/RBAC, MCP API routes, or harness pipeline.

**Depends on**: none

**Notes**:
- This slice must not require the external TMC server to exist.
- Status should report `disabled` or `unavailable` when no runtime provider endpoint is configured.
- The UI label should be generic, such as "Live runtime unavailable", with provider sublabel when configured, initially `Talend TMC`.
- This slice covers the user-visible base of `US-027` and gives later slices a stable platform-agnostic status contract.

## Slice 2: Runtime Provider Contract and Safe Mock Adapter

**Delivers**: The platform has normalized runtime models, an allowlisted execution-monitoring tool registry, and a mock provider that can execute or reject calls safely.

**Expected files**:
- `RAG_pipeline/backend/src/runtime/__init__.py`
- `RAG_pipeline/backend/src/runtime/models.py`
- `RAG_pipeline/backend/src/runtime/registry.py`
- `RAG_pipeline/backend/src/runtime/client.py`
- `RAG_pipeline/backend/src/runtime/providers/__init__.py`
- `RAG_pipeline/backend/src/runtime/providers/mock.py`
- `RAG_pipeline/backend/src/config.py`
- `RAG_pipeline/backend/tests/test_runtime/test_registry.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_client.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_mock_provider.py` (new)

**Critical area**: no - new RAG backend runtime integration boundary; no listed critical path.

**Depends on**: Slice 1

**Notes**:
- The generic runtime tools are:
  - `list_executions`
  - `get_execution_details`
  - `detect_failures`
  - `get_execution_logs`
- Normalized models should include provider status, executions, failures, log events, and structured tool errors.
- Unsupported tools must return structured not-available results without calling any provider.
- The router must enforce timeouts, result caps, schema validation, and sanitization before any result is eligible for LLM context.
- This slice covers `US-025` in a platform-agnostic, mockable, independently testable form.

## Slice 3: Backend Runtime Tool-Use Loop with Mocked Answers

**Delivers**: A conversational RAG request can call a mocked runtime tool through a bounded provider-agnostic loop and produce an answer grounded in the normalized tool result.

**Expected files**:
- `RAG_pipeline/backend/src/generation/agent_loop.py` (new)
- `RAG_pipeline/backend/src/generation/llm_client.py`
- `RAG_pipeline/backend/src/generation/conversational_rag.py`
- `RAG_pipeline/backend/src/generation/agent_orchestrator.py`
- `RAG_pipeline/backend/src/api/routes/conversations.py`
- `RAG_pipeline/backend/src/runtime/...` files from Slice 2 as needed
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py` (new)
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py` (new)
- `RAG_pipeline/backend/tests/test_api/test_conversations.py` or focused stream test

**Critical area**: no - RAG generation/chat path; does not touch retrieval, auth/RBAC, MCP API routes, or harness pipeline.

**Depends on**: Slice 2

**Notes**:
- This is the high-risk `US-024` slice.
- The loop must be bounded: generate -> runtime tool call -> execute -> append result -> continue.
- Providers without structured tool support must degrade cleanly and must not claim live runtime data.
- Existing conversation ownership, persistence, citations, memory, usage accounting, and doc-gap behavior must stay compatible.
- Backend streaming should emit typed runtime progress events without breaking existing `step`, `sources`, `token`, `usage`, and `done` events.

## Slice 4: Frontend Runtime Progress and Result Visibility

**Delivers**: Users can see when chat is calling a runtime tool and whether the tool result succeeded or failed while the answer is streaming.

**Expected files**:
- `RAG_pipeline/frontend/hooks/useStreamMessage.ts`
- `RAG_pipeline/frontend/types/index.ts` or focused stream schema file
- chat message/rendering components for streamed assistant state
- optional reusable `RuntimeToolStatus` component
- `RAG_pipeline/frontend/tests/hooks/useStreamMessage.test.ts`
- `RAG_pipeline/frontend/tests/components/...` for rendering runtime tool progress

**Critical area**: no - RAG frontend only.

**Depends on**: Slice 3

**Notes**:
- Zod schemas must validate the new event payloads.
- Existing stream tests for `step`, `sources`, `token`, `usage`, and `done` should remain green.
- Tool result display should avoid rendering raw sensitive log payloads. Summaries/status are enough for the UI.

## Slice 5: Talend TMC Provider Adapter and Compose Secrets

**Delivers**: The generic runtime layer can run against the real external Talend TMC provider service in Docker Compose using Docker-secret token handling.

**Expected files**:
- external TMC server repo/image source once provided (outside this repo if required)
- `docker-compose.yml`
- `.env.production.template`
- deployment docs such as `deploy/RUNBOOK.md` and/or `deploy/SMOKE-TEST.md`
- `RAG_pipeline/backend/src/runtime/providers/talend_tmc.py`
- `RAG_pipeline/backend/src/runtime/client.py`
- `RAG_pipeline/backend/src/config.py`
- focused backend integration tests with mocked HTTP/MCP transport

**Critical area**: no - deployment/config and RAG runtime provider work unless the final TMC protocol requires MCP API route changes.

**Depends on**: Slice 4 and availability of the external TMC server source, image, or API contract

**Notes**:
- This slice is blocked until the external TMC server source/image/protocol is available.
- The Talend adapter maps:
  - `list_executions` -> `get_task_executions`
  - `get_execution_details` -> `get_execution_details`
  - `detect_failures` -> `detect_execution_failures`
  - `get_execution_logs` -> `get_execution_logs`
- The Talend API token must be mounted as a Docker secret, not passed as a plaintext env var.
- No public TMC port should be exposed unless explicitly needed for operations.
- If the real server protocol differs from Slice 2 assumptions, update only the Talend adapter/client while preserving the generic runtime registry/loop contracts.

## Slice 6: PoC Validation with Real Talend TMC Data

**Delivers**: EP-004 is accepted against real Talend TMC data through the generic runtime contract for the Lobster PoC scenarios.

**Expected files**:
- `.plans/05_inspection_report.md` after inspection
- validation notes or a committed smoke-test artifact, if the project convention accepts one
- possible updates to `deploy/SMOKE-TEST.md`
- possible small fixes in files from Slices 1-5 based on real validation findings

**Critical area**: no by default; reclassify if validation fixes touch listed critical areas.

**Depends on**: Slice 5, real Talend API token, region/configuration, and a known job name for latest-execution validation

**Notes**:
- Required real-data prompts:
  - "what jobs failed in the last 24 hours?"
  - "when did job X last run and what was the result?"
- Mock tests are not enough to close this slice.
- The validation must prove the chat path used the generic runtime tools and the `talend_tmc` provider adapter, not a Talend-specific shortcut.
- Any failures found here should be fed back into the smallest affected earlier slice rather than patched ad hoc.
