---
inspected_at: 2026-06-17
verdict: WARN
---

## Verdict Summary

EP-006 Slice 4 conforms to the plan architecture contracts with two non-blocking warnings: a stale-data race condition in the project-change deselection logic, and the `useRuntimeIncidents` projectId wiring listed in the plan has no call site since no incident list view was added.

## Check Results

| Check             | Result | Notes |
|-------------------|--------|-------|
| Plan conformance  | WARN   | All 7 steps delivered; 5 BFF routes + hooks + component + project filter + insight panel all present. Two deviations: (1) plan step 4 says pass `projectId` to `useRuntimeIncidents`, but that hook has no call site in the page — no incidents list view exists; (2) `handleProjectChange` deselection logic checks stale data |
| Root cause        | PASS   | Frontend-only slice; no root-cause concern applies |
| Generalized       | PASS   | All 5 BFF routes follow identical proxy pattern (`backendProxyHeaders`, `encodeURIComponent`, `res.status` preserved, `cache: "no-store"`); all hooks follow the shared `buildQuery` + Zod parse pattern; no one-off deviations |
| Design patterns   | PASS   | React Query hooks + BFF proxy (Next.js route handler) pattern used consistently; `RuntimeInsightPanel` extracted per plan; `enabled` gates on all dependent hooks |
| Contract respect  | PASS   | Browser never calls FastAPI directly; all 5 BFF routes are read-only GET proxies; status codes preserved end-to-end; Zod validates all successful shapes; TypeScript strict; project filter is UI narrowing-only — no browser-side authorization decision made |
| No special-casing | PASS   | `SeverityChip` handles unknown severity values with a generic fallback (not a special branch); no compat shims; no feature flags |
| Edge cases        | PASS   | Summary loading/404/error handled per section; no-incident → neutral Info message; evidence empty → explicit message; timeline empty → explicit message; all three states (loading/error/data) rendered per section independently |
| RBAC              | PASS   | All RBAC enforcement is backend-side; `projectId` filter is explicitly narrowing UI state, not authorization; point lookups (summary, incident, evidence) are scoped by backend auth via forwarded cookies |
| Credential safety | PASS   | No credentials in code; cookies forwarded via existing `backendProxyHeaders`; no log statements added |

## Findings

1. **WARN** (`app/admin/logs/page.tsx:107`) — `handleProjectChange` checks against stale `executions.data` to decide whether to deselect `selectedId`. At call time, `executions.data` still holds the OLD unfiltered result. The check `executions.data.executions.some(e => e.execution_id === selectedId)` will almost always be `true` (the execution is in the old list), so `selectedId` is rarely cleared. A project filter change can therefore leave an execution panel open for an execution that belongs to the deselected project. The correct approach is to always reset `selectedId` on project change (the backend insight calls will return 404 and show a graceful degraded state, so user experience is safe either way).

2. **WARN** (`hooks/useRuntimeLogs.ts` — `useRuntimeIncidents`) — Plan step 4 lists `useRuntimeIncidents` as one of the hooks to receive the `projectId` filter. The hook is implemented and supports `IncidentFilters.projectId`, but it is not called anywhere in the logs page (there is no incidents list view). The hook's interface is correct; the gap is that the plan's filter-wiring step is only partially exercised. No functional regression — incident data enters the page via `useRuntimeIncident` (point lookup in `RuntimeInsightPanel`), which is correctly scoped by backend auth.

## Required Actions

**BLOCK:** None.

**WARN — optional improvements:**
1. In `handleProjectChange`, replace the stale-data check with `setSelectedId(null)` unconditionally (or defer the check to an effect that runs after `executions` refetches with the new filter). The current check is misleading and won't protect the user from a stale panel in practice.
2. Document in `PROJECT_MEMORY.md` that `useRuntimeIncidents` exists for future use (incidents list view or filtering) but is not yet called from the logs page.
