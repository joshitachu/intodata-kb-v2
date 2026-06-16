---
feature: EP-004 TMC Live Integration
slice: Runtime Status Contract and Chat Indicator
sub_project: rag_pipeline
critical_area: no
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

IntoData KB has no platform-level contract for reporting whether live runtime
data is available. The chat UI therefore cannot tell users whether live
execution/log data is connected, unavailable, or disabled before later slices add
runtime tool calls.

Slice 1 must establish that contract without depending on the external Talend
TMC server. The result should be provider-agnostic: Talend TMC is the initial
provider label, but the backend/frontend contract must not encode TMC-only
status semantics.

## Proposed Solution

Add a small RAG backend runtime status module and route:

- `GET /api/v1/runtime/status`
- Authenticated via `CurrentUser`, but no additional role/permission gate.
- Returns a normalized status payload with:
  - `status`: `disabled | connected | unavailable`
  - `provider_key`: optional string, for example `talend_tmc`
  - `provider_label`: user-facing label, for example `Talend TMC`
  - `message`: short safe status text
  - `checked_at`: ISO timestamp

Add runtime config settings:

- `runtime_provider_key`: empty means disabled
- `runtime_provider_label`: default `Live runtime`
- `runtime_provider_health_url`: empty means provider is configured but not
  reachable yet, so status is `unavailable`
- `runtime_status_timeout`: short timeout for optional health checks

Implement `src/runtime/status.py` so Slice 1 can return:

- `disabled` when no provider key is configured
- `unavailable` when a provider key exists but no health URL exists or health
  fails
- `connected` when a configured health URL returns 2xx

Add a Next.js BFF proxy route and frontend state:

- Browser calls `/api/runtime/status`, not FastAPI directly.
- BFF forwards cookies/CSRF with existing proxy helpers.
- Zod validates the response before React sees it.
- `useRuntimeStatus()` uses React Query, handles loading/error/data, and falls
  back to an unknown/unavailable UI state rather than blocking chat.

Add a compact `RuntimeStatusIndicator` in the chat toolbar next to chat mode and
project selection. It should render a small status dot plus concise text:

- `Live runtime disabled`
- `Talend TMC unavailable`
- `Talend TMC connected`

The indicator must not disable message submission or change the existing RAG
conversation flow.

## Affected Files

- `RAG_pipeline/backend/src/config.py`
- `RAG_pipeline/backend/src/api/main.py`
- `RAG_pipeline/backend/src/api/routes/runtime.py` (new)
- `RAG_pipeline/backend/src/runtime/__init__.py` (new)
- `RAG_pipeline/backend/src/runtime/models.py` (new)
- `RAG_pipeline/backend/src/runtime/status.py` (new)
- `RAG_pipeline/backend/tests/test_api/test_runtime_status.py` (new)
- `RAG_pipeline/frontend/app/api/runtime/status/route.ts` (new)
- `RAG_pipeline/frontend/types/index.ts`
- `RAG_pipeline/frontend/hooks/useRuntimeStatus.ts` (new)
- `RAG_pipeline/frontend/components/RuntimeStatusIndicator.tsx` (new)
- `RAG_pipeline/frontend/components/ChatWindow.tsx`
- `RAG_pipeline/frontend/tests/hooks/useRuntimeStatus.test.ts` (new)
- `RAG_pipeline/frontend/tests/components/RuntimeStatusIndicator.test.tsx` (new)
- `RAG_pipeline/.agent/system/api-reference.md`
- `.env.production.template`

## Architecture Contracts

- The runtime status contract is generic. It must not expose Talend API details,
  task IDs, secrets, logs, or tool schemas.
- `runtime_provider_key` is the stable integration key. `provider_label` is
  display-only and safe for UI.
- Empty `runtime_provider_key` means disabled, not an error.
- Provider configured but not reachable means unavailable, not a chat failure.
- Runtime status is available to every authenticated user, with no extra RBAC
  gate in this slice.
- The frontend preserves the BFF pattern: client components call Next.js route
  handlers, and route handlers call FastAPI with server-side `FASTAPI_URL`.
- The status hook must implement loading, error, and data states. Errors degrade
  the indicator only; they do not block normal chat.
- Slice 1 does not register runtime tools, add a tool-use loop, call the TMC API,
  or modify streaming SSE events.

## Implementation Steps

1. Add backend runtime models:
   - Define `RuntimeConnectionStatus` as a string enum or literal-compatible
     type with `disabled`, `connected`, and `unavailable`.
   - Define `RuntimeStatusResponse` as a Pydantic v2 model.

2. Add backend runtime status service:
   - Read runtime settings from `settings`.
   - If no provider key is configured, return disabled.
   - If provider key exists and no health URL exists, return unavailable.
   - If health URL exists, perform an async `httpx` GET with timeout.
   - Treat 2xx as connected; all other statuses/exceptions as unavailable.
   - Log failures without including request bodies, secrets, or raw provider
     payloads.

3. Add backend API route:
   - Create `src/api/routes/runtime.py` with `router = APIRouter(prefix="/runtime", tags=["runtime"])`.
   - Add `GET /status`, require `CurrentUser`, and return `RuntimeStatusResponse`.
   - Register the router in `src/api/main.py` with prefix `/api/v1`.

4. Add backend tests:
   - No provider configured returns `disabled`.
   - Provider configured without health URL returns `unavailable`.
   - Health URL 2xx returns `connected`.
   - Health URL failure/timeout returns `unavailable`.
   - Endpoint requires authentication but no role-specific permission.

5. Add frontend schemas and BFF:
   - Add runtime status Zod schema and exported type in `frontend/types/index.ts`.
   - Add `frontend/app/api/runtime/status/route.ts`.
   - Forward cookies using existing proxy/header helpers.
   - Return backend status codes for auth failures; validate successful payloads.

6. Add frontend hook:
   - Create `useRuntimeStatus()` using React Query.
   - Fetch `/api/runtime/status` with `cache: "no-store"`.
   - Use a short stale time/refetch interval suitable for status, for example
     15-30 seconds.
   - On fetch/parse failure, expose error state without throwing in the UI.

7. Add chat indicator UI:
   - Create `RuntimeStatusIndicator`.
   - Render compact loading, disabled, unavailable, connected, and error states.
   - Use lucide icons or small semantic dots; avoid a large card.
   - Add the indicator to `ChatWindow` `toolbarContent` beside mode/project
     controls for both welcome and active conversation states.

8. Update docs/config examples:
   - Document `/api/v1/runtime/status` in `RAG_pipeline/.agent/system/api-reference.md`.
   - Add commented runtime env vars to `.env.production.template`.

## Verification Strategy

- Backend syntax:
  - `cd RAG_pipeline/backend && python3 -m py_compile src/config.py src/api/main.py src/api/routes/runtime.py src/runtime/models.py src/runtime/status.py`

- Backend targeted tests:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_api/test_runtime_status.py`

- Frontend typecheck/tests:
  - `cd RAG_pipeline/frontend && npx tsc --noEmit`
  - `cd RAG_pipeline/frontend && npm test -- useRuntimeStatus RuntimeStatusIndicator`

- Optional broader smoke after implementation:
  - `cd RAG_pipeline/backend && uv run pytest tests/test_api/test_runtime_status.py tests/test_api/test_conversations.py`
  - `cd RAG_pipeline/frontend && npm test -- useStreamMessage MessageList useRuntimeStatus RuntimeStatusIndicator`

## Out of Scope

- Dockerizing or composing the external Talend TMC server.
- Docker secret handling for the Talend token.
- Registering runtime tools.
- Calling `get_task_executions`, `get_execution_details`,
  `detect_execution_failures`, or `get_execution_logs`.
- Adding an LLM tool-use loop.
- Adding runtime SSE events.
- Real Talend TMC validation.
