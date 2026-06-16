---
feature: EP-004 TMC Live Integration
slice: PoC Validation with Real Talend TMC Data
sub_project: both
critical_area: no
verdict: PENDING
approved_at:
---

## Problem Statement

Slices 1-5 built the platform-agnostic runtime integration path:

- runtime status and generic tool registry;
- mock runtime provider;
- backend tool-use loop in chat streaming;
- frontend runtime progress display;
- `talend_tmc` provider adapter with secret-file configuration and Compose/env
  hooks.

The remaining risk is that the Talend adapter is still based on an assumed wire
format. Unit tests prove the internal contract with `httpx.MockTransport`, but
they do not prove the real Talend TMC service returns compatible payloads, that
the runtime-enabled stack is configured correctly, or that a user can ask chat
for live execution data and get a truthful answer from the generic runtime
tools.

Slice 6 closes EP-004 only when the chat path works against real Talend TMC
data. Mock data alone is not enough for acceptance.

## Proposed Solution

Run a structured PoC validation against a runtime-enabled stack configured for
the real Talend TMC service. The validation must prove:

- `RUNTIME_PROVIDER_KEY=talend_tmc` resolves through the generic runtime
  provider registry.
- `/api/v1/runtime/status` reports the configured live runtime state without a
  server error.
- Chat invokes generic runtime tools, not Talend-specific shortcuts.
- The Talend adapter returns normalized runtime models from real TMC data.
- Required operational prompts return answers that match known truth from TMC.
- Runtime progress reaches the frontend through `runtime_tool_call` and
  `runtime_tool_result` SSE events.
- The Talend token remains in the mounted secret file only and does not appear in
  logs, responses, docs, screenshots, or committed artifacts.

If validation finds a code mismatch, fix the smallest affected earlier slice:

- provider status/config: Slice 1 or Slice 5;
- runtime model or Talend payload mapping: Slice 2 or Slice 5;
- backend tool-use loop: Slice 3;
- frontend runtime indicator/stream state: Slice 4;
- deployment docs/secrets: Slice 5 or this validation slice.

Do not patch around failures in chat prompts, frontend display, or documentation
while leaving the underlying runtime contract wrong.

## Affected Files

Expected validation artifacts:

- `.plans/05_inspection_report.md`
- `deploy/EP004-TMC-VALIDATION.md` (new, redacted validation record)
- `deploy/SMOKE-TEST.md`
- `deploy/RUNBOOK.md`

Possible code files only if real validation exposes a defect:

- `RAG_pipeline/backend/src/runtime/providers/talend_tmc.py`
- `RAG_pipeline/backend/src/runtime/client.py`
- `RAG_pipeline/backend/src/runtime/models.py`
- `RAG_pipeline/backend/src/runtime/registry.py`
- `RAG_pipeline/backend/src/generation/agent_loop.py`
- `RAG_pipeline/backend/src/generation/conversational_rag.py`
- `RAG_pipeline/frontend/hooks/useStreamMessage.ts`
- `RAG_pipeline/frontend/components/RuntimeToolStatus.tsx`
- `RAG_pipeline/frontend/components/MessageBubble.tsx`
- `docker-compose.yml`
- `.env.production.template`

Critical-area status starts as `no` because the planned work is validation and
deployment documentation. Reclassify to `critical_area: yes` before inspection if
fixes touch auth/RBAC, retrieval, MCP API routes, or the MCP custom pipeline.

## Architecture Contracts

- The runtime contract remains provider-agnostic. Chat, the backend tool loop,
  frontend events, and persisted message data must use only generic tool names:
  - `list_executions`
  - `get_execution_details`
  - `detect_failures`
  - `get_execution_logs`
- Talend operation names and wire-format handling stay inside
  `runtime/providers/talend_tmc.py`.
- Real-data acceptance must go through the user chat path. Direct provider calls
  may be used for diagnosis, but they do not replace chat validation.
- Runtime tool results sent to the LLM and frontend must remain sanitized and
  bounded by `runtime/client.py`.
- No raw Talend payloads, full logs, request bodies, bearer tokens, or secret
  file contents may be committed or pasted into validation notes.
- The Talend API token is read from `TALEND_TMC_API_TOKEN_FILE`; do not add an
  env-var token fallback.
- The Talend runtime service must remain internal to the deployment network; do
  not publish a public host port as part of validation.
- If token rotation is tested, the current accepted behavior is that the backend
  must restart because the Slice 5 provider caches the token on the process
  singleton.
- If there are no failed jobs in the last 24 hours, record the verified TMC
  truth and either accept a truthful "none found" answer or agree a wider
  validation window before closing the slice.

## Implementation Steps

1. Confirm prerequisites.
   - Slice 5 inspection warnings are understood: assumed wire format, token
     cache lifetime, and live transport untested.
   - Obtain the real Talend TMC base URL/internal service URL.
   - Obtain the real Talend API token through an operator-controlled path.
   - Identify a known job name for the "last run" prompt.
   - Identify expected truth for the last 24 hours: failed jobs exist, or TMC
     confirms none exist.

2. Prepare the runtime-enabled stack.
   - Store the token in a restrictive host file such as
     `/opt/intodata/secrets/talend_tmc_api_token`.
   - Mount it into the backend as
     `/run/secrets/talend_tmc_api_token:ro`.
   - Set:
     - `RUNTIME_PROVIDER_KEY=talend_tmc`
     - `RUNTIME_PROVIDER_LABEL=Talend TMC`
     - `RUNTIME_PROVIDER_HEALTH_URL=<internal TMC health URL>`
     - `TALEND_TMC_BASE_URL=<internal TMC base URL>`
     - `TALEND_TMC_API_TOKEN_FILE=/run/secrets/talend_tmc_api_token`
   - Validate Compose with `docker compose config`.
   - Restart the backend or full stack.

3. Verify service health before chat testing.
   - Confirm backend `/health` is healthy.
   - Confirm the internal TMC health URL is reachable from the backend network.
   - Confirm authenticated `/api/v1/runtime/status` returns a valid status
     response and never a 500.
   - Record status output in redacted form in the validation artifact.

4. Establish expected truth from TMC.
   - Use the TMC UI, operator-provided evidence, or a direct TMC/API diagnostic
     to record:
     - expected failed jobs in the last 24 hours;
     - known job name;
     - known latest execution timestamp and result for that job.
   - Redact tenant names, tokens, customer data, and sensitive log content.

5. Run required chat prompt 1.
   - Prompt: `what jobs failed in the last 24 hours?`
   - Expected behavior:
     - backend emits a `runtime_tool_call` for a generic tool, normally
       `detect_failures`;
     - backend emits `runtime_tool_result`;
     - final answer matches TMC truth;
     - frontend shows runtime progress during streaming;
     - if no failures exist, answer clearly says none were found for the window
       instead of inventing failures.

6. Run required chat prompt 2.
   - Prompt: `when did job <known_job_name> last run and what was the result?`
   - Expected behavior:
     - backend invokes generic runtime tooling, normally `list_executions` and
       optionally `get_execution_details`;
     - final answer matches the known latest execution timestamp and status;
     - answer does not cite document-search snippets as the source of runtime
       truth.

7. Run an optional log-detail prompt if the real TMC service supports it.
   - Prompt: `show the latest error logs for execution <known_execution_id>`
   - Expected behavior:
     - backend invokes `get_execution_logs`;
     - response summarizes logs safely;
     - no sensitive raw payload or token appears in the stream.

8. Capture evidence.
   - Create `deploy/EP004-TMC-VALIDATION.md` with:
     - date/time and commit SHA;
     - stack mode and redacted runtime env summary;
     - runtime status result;
     - exact prompts used;
     - generic tools observed;
     - expected vs actual results;
     - frontend/runtime progress observation;
     - secret-hygiene result;
     - defects found and the slice they map back to.
   - Do not include the real token, raw bearer headers, full raw logs, or
     customer-sensitive payloads.

9. Fix only validated defects.
   - If the live TMC wire format differs, update only
     `runtime/providers/talend_tmc.py` and provider tests unless the normalized
     generic model is genuinely insufficient.
   - If status or Compose wiring is wrong, update config/docs/env wiring only.
   - If chat fails to call tools despite provider health, inspect Slice 3 code
     paths before changing prompt text.
   - If frontend progress is missing while SSE events exist, inspect Slice 4
     stream/UI code.

10. Update deployment docs if the run exposes gaps.
    - Keep `deploy/RUNBOOK.md` focused on operator setup.
    - Keep `deploy/SMOKE-TEST.md` focused on repeatable checks.
    - Move one-off run evidence into `deploy/EP004-TMC-VALIDATION.md`.

11. Run inspection after validation and any fixes.
    - Write `.plans/05_inspection_report.md`.
    - Include explicit verdict on real-data acceptance, secret handling, generic
      runtime contract, and any code changes made during this slice.

## Verification Strategy

Baseline checks before real-data validation:

- `cd RAG_pipeline/backend && python3 -m py_compile src/config.py src/runtime/client.py src/runtime/providers/talend_tmc.py`
- `cd RAG_pipeline/backend && uv run pytest tests/test_runtime/test_talend_tmc_provider.py tests/test_runtime/test_client.py tests/test_api/test_runtime_status.py`
- `docker compose config`

Runtime/tool-loop regression checks if backend runtime code changes:

- `cd RAG_pipeline/backend && uv run pytest tests/test_runtime tests/test_generation/test_agent_loop.py tests/test_generation/test_conversational_rag_runtime_tools.py tests/test_api/test_conversations.py`

Frontend regression checks if stream/UI files change:

- `cd RAG_pipeline/frontend && npx tsc --noEmit`
- `cd RAG_pipeline/frontend && npm test`

Real-data acceptance checks:

- Authenticated `/api/v1/runtime/status` returns a valid runtime status.
- Prompt `what jobs failed in the last 24 hours?` produces a truthful answer
  from `talend_tmc` runtime data.
- Prompt `when did job <known_job_name> last run and what was the result?`
  produces a truthful answer from `talend_tmc` runtime data.
- SSE contains `runtime_tool_call` and `runtime_tool_result` events.
- The frontend displays runtime progress while the answer streams.
- Backend logs identify generic runtime activity without printing tokens,
  request bodies, raw payloads, or secret file contents.
- Public access to the Talend runtime service port fails.
- `deploy/EP004-TMC-VALIDATION.md` records redacted evidence and any follow-up
  defects.

Secret hygiene:

- Do not paste token values into shell history, docs, screenshots, or chat.
- Do not commit `.env`, cookies, logs, raw payload dumps, screenshots containing
  secrets, or local token files.
- Run a log scan for the token value locally and treat any match as a blocking
  defect; do not paste leaked lines into the repo.

## Out of Scope

- Building or owning the external Talend TMC service.
- Adding a Talend-specific chat command or bypass around generic runtime tools.
- Persisting runtime events beyond the existing conversation/message behavior.
- Building a raw log dashboard.
- Hot token rotation without backend restart.
- New providers beyond `mock` and `talend_tmc`.
- Scanner, MCP doc-generation pipeline, retrieval ranking, auth/RBAC, or tenant
  model changes unless a real validation defect directly requires them.
