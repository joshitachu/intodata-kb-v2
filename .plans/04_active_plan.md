---
feature: Logs Dashboard + RAG x Runtime Cross-Reference
slice: Slice 3 - Frontend logs dashboard
sub_project: rag_pipeline
critical_area: no - frontend only; RBAC enforced by Slice 2 backend endpoints
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

Slice 2 exposes RBAC-scoped runtime data through backend REST endpoints, but the
RAG frontend still has no operator-facing logs view. The only existing runtime UI
surface is the small runtime status indicator and chat-side runtime progress.
Operators need an admin page where they can browse recent executions, inspect
failures, and drill into log lines without asking the chat to fetch data.

This slice should not add a marketing page or a separate app shell. It should add
a functional `/admin/logs` page inside the existing admin section, with dense,
scan-friendly operational controls and clear loading, error, empty, and detail
states.

## Proposed Solution

Build the logs dashboard as a frontend-only slice on top of the Slice 2 backend
contract. Add Next.js BFF proxy routes under `app/api/runtime/*`, Zod schemas and
TypeScript types for the four runtime response shapes, React Query hooks in
`useRuntimeLogs.ts`, and a new `/admin/logs` admin page.

Use the existing frontend conventions:

- BFF proxy routes forward cookies/CSRF with `backendProxyHeaders()`.
- Hooks fetch through the local `/api/*` routes and validate with Zod.
- The admin page lives under `app/admin`, so `app/admin/layout.tsx` already gates
  access with `hasAnyAdminPermission`.
- Sidebar visibility uses `adminSubLinks` and `usePermissions()`.
- Icons come from `lucide-react`.

The page should be an actual operational dashboard, not a landing page:

- Top-level tabs or segmented controls for `Executions` and `Failures`.
- Executions table with filter controls for state and job name.
- Failures table/panel showing recent failure summaries.
- Log detail drilldown opened by selecting an execution row, with severity filter
  and timestamped log lines.
- Loading, error, empty, and selected-detail states handled explicitly.

## Affected Files

- `RAG_pipeline/frontend/app/admin/logs/page.tsx` (new)
  - Main client page for executions, failures, and log detail drilldown.
  - Use dense tables/panels consistent with existing admin pages.
  - Avoid nested cards; use full-width admin layout sections and simple repeated
    row/detail surfaces.

- `RAG_pipeline/frontend/app/api/runtime/executions/route.ts` (new)
  - Proxy `GET /api/v1/runtime/executions`.
  - Preserve query string filters.

- `RAG_pipeline/frontend/app/api/runtime/executions/[id]/route.ts` (new)
  - Proxy `GET /api/v1/runtime/executions/{execution_id}`.

- `RAG_pipeline/frontend/app/api/runtime/executions/[id]/logs/route.ts` (new)
  - Proxy `GET /api/v1/runtime/executions/{execution_id}/logs`.
  - Preserve `min_severity` and `limit`.

- `RAG_pipeline/frontend/app/api/runtime/failures/route.ts` (new)
  - Proxy `GET /api/v1/runtime/failures`.
  - Preserve query string filters.

- `RAG_pipeline/frontend/hooks/useRuntimeLogs.ts` (new)
  - React Query hooks for executions, execution detail, log events, and failures.
  - Follow `useRuntimeStatus()` style: local fetch, `res.ok` guard, Zod parse,
    stable query keys.

- `RAG_pipeline/frontend/types/runtime.ts` (new)
  - Zod schemas and inferred types for runtime executions, failures, logs, and
    response envelopes.
  - Export severity/state enums as Zod unions.

- `RAG_pipeline/frontend/types/index.ts`
  - Re-export runtime log schemas/types, or import from `types/runtime.ts` where
    used. Keep one clear public type surface.

- `RAG_pipeline/frontend/components/Sidebar.tsx`
  - Add a "Logs" admin sub-link, likely with `ScrollText`, `ListTree`, or another
    existing lucide icon.
  - Permission should match the intended admin audience. Prefer `perm:
    "view_audit_logs"` unless Slice 2 introduced a more specific permission.

- Optional tests:
  - `RAG_pipeline/frontend/tests/hooks/useRuntimeLogs.test.ts`
  - `RAG_pipeline/frontend/tests/app/admin/logs.test.tsx` or component-level tests
    if page logic is extracted into testable child components.

## Architecture Contracts

- Frontend never calls FastAPI directly from client components; all browser calls
  go through Next.js BFF routes.
- BFF routes must forward auth cookies and CSRF token using `backendProxyHeaders()`.
- Backend remains the RBAC boundary. Frontend permission checks hide navigation and
  gate admin layout, but they are not the data-isolation mechanism.
- All API responses are validated with Zod before being used by hooks/components.
- TypeScript strict mode: no `any`; use `unknown` and schema parsing/narrowing.
- Handle loading, error, empty, and data states for each view.
- No new npm dependencies.
- No backend code in this slice, except only if Slice 2 response shape is found to
  be incompatible with its own documented contract.

## Implementation Steps

1. Add runtime frontend types
   - Create `types/runtime.ts` with:
     - `RuntimeExecutionStateSchema`: `success | failed | running | unknown`
     - `RuntimeLogSeveritySchema`: `debug | info | warning | error`
     - `RuntimeExecutionSchema`
     - `RuntimeFailureSchema`
     - `RuntimeLogEventSchema`
     - `ExecutionListResponseSchema`
     - `ExecutionDetailResponseSchema`
     - `LogEventListResponseSchema`
     - `FailureListResponseSchema`
   - Export inferred TypeScript types.
   - Re-export from `types/index.ts` if that is the local convention used by the
     new hooks.

2. Add BFF proxy routes
   - Implement all four `app/api/runtime/*` route handlers.
   - Use `FASTAPI_URL` and `backendProxyHeaders(request)` like
     `app/api/runtime/status/route.ts`.
   - Forward the incoming `request.nextUrl.search` query string unchanged.
   - Parse backend JSON defensively with `res.json().catch(() => ({}))`.
   - Return `NextResponse.json(data, { status: res.status })`.

3. Add `useRuntimeLogs.ts`
   - Add `useRuntimeExecutions(filters)`.
   - Add `useRuntimeExecution(executionId, enabled)`.
   - Add `useRuntimeExecutionLogs(executionId, filters, enabled)`.
   - Add `useRuntimeFailures(filters)`.
   - Construct query strings with `URLSearchParams`, omitting empty filters.
   - Use stable query keys that include filter values.
   - Set short stale times appropriate for operational data, for example 10-30s.

4. Build `/admin/logs`
   - Add a client page under `app/admin/logs/page.tsx`.
   - State:
     - selected tab/view
     - `state` filter
     - `jobName` text filter
     - selected execution id
     - log `min_severity`
   - Executions view:
     - Filter row with state segmented/select control, job input, refresh button.
     - Table columns: execution id, job name, project name, state badge,
       started_at, duration_ms.
     - Row click selects an execution and loads logs.
   - Failures view:
     - Table/panel columns: job name, failed_at, error_summary, execution id.
     - Row click selects the failure's execution and opens log detail.
   - Detail panel:
     - Shows selected execution metadata if loaded.
     - Severity filter.
     - Timestamped log lines with severity badges.
     - Empty state when an execution has no log lines for the selected severity.
   - Use accessible buttons/inputs, fixed table layout where needed, and avoid text
     overflow in narrow widths.

5. Add sidebar navigation
   - Add `Logs` to `adminSubLinks` in `components/Sidebar.tsx`.
   - Use a lucide icon rather than custom SVG.
   - Use the existing permission filtering pattern.

6. Tests
   - Add hook tests for URL construction, Zod validation success, and failed fetch.
   - Add a page/component test for loading/error/empty/data states if feasible.
   - Update existing sidebar tests only if they exist; otherwise rely on typecheck
     and manual navigation.

7. Documentation/logging after build
   - Update RAG frontend/session logs through the project workflow after the slice
     is implemented and validated.

## Verification Strategy

- Typecheck:
  - `cd RAG_pipeline/frontend && npx tsc --noEmit`

- Tests:
  - `cd RAG_pipeline/frontend && npm test`
  - If tests are scoped during implementation, run at least:
    - `cd RAG_pipeline/frontend && npm test -- tests/hooks/useRuntimeLogs.test.ts`
    - relevant component/page tests if added.

- Build:
  - `cd RAG_pipeline/frontend && npm run build`

- Manual UI check with Slice 2 backend running and seeded runtime data:
  - Open `/admin/logs`.
  - Confirm executions table loads and filters by state/job.
  - Select a failed execution and confirm log detail loads.
  - Set severity to `error` and confirm lower severity lines are hidden.
  - Open failures view and select a failure; confirm it opens the same detail flow.
  - Confirm empty/error states render without layout breakage.
  - Confirm "Logs" appears in the admin sidebar for permitted users.

- Browser check:
  - After implementation, run the frontend dev server and inspect `/admin/logs` in
    the in-app browser or equivalent local browser workflow.

## Out of Scope

- Slice 1 conversational RAG cross-reference behavior.
- Slice 2 backend runtime REST endpoints or schemas.
- Any backend RBAC change.
- Runtime write actions such as retry, acknowledge, resolve, or delete.
- Trend/analytics charts, success-rate dashboards, or SLA views.
- Real Talend TMC polling/sync worker.
- New npm dependencies.
