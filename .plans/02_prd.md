---
feature: Logs Dashboard + RAG×Runtime Cross-Reference
pipeline_step: 2_prd
---

## Executive Summary
Two gaps are closed in this epic: the AI learns to answer questions using both live
execution logs AND indexed documentation in the same turn ("I see ORA-12170 in the
logs — what should I check?"), and operators get a dedicated admin page to browse
executions, view failure summaries, and drill into log lines without going through
the chat. Together these turn runtime log data from a hidden Postgres asset into an
interactive, AI-augmented operational tool.

## Problem
Runtime execution data (job runs, failures, log lines) already exists in Postgres and
is accessible to the AI via 4 tool calls. Two things prevent it from being useful:
(1) there is no UI — the only way to see logs is to ask the chat a question; and
(2) the chat either does document search OR runtime tool calls, never both. A question
that needs both — "this job failed with ORA-12170, what does our ESB doc say is the
most likely cause?" — cannot be answered today because a `precomputed_answer`
short-circuit in the conversational RAG path exits before the runtime tool loop fires.
The RBAC coupling between the two data sources is also implicit convention, not a
type-enforced invariant, which is a latent data-isolation risk.

## Goals
1. The AI answers combined questions (logs + docs) in a single turn, correctly citing
   which source each claim comes from.
2. Cross-project isolation holds: a user scoped to project A receives no log data
   from project B, even in a combined RAG+runtime answer.
3. Operators can browse executions, filter by state/job/project, and drill into log
   lines in a dedicated `/admin/logs` frontend page without using the chat.
4. The Ollama fallback degradation is explicit — when the active LLM provider lacks
   tool-call support, the user receives a visible signal rather than a silent
   RAG-only answer.
5. The RBAC coupling between Qdrant scope and Postgres scope is enforced by type,
   not by convention.

## Non-Goals
- Building a real polling/sync worker against Talend TMC or any live source.
  Data is seeded manually; the poller is a separate future epic.
- Modifying the ingest, chunking, or embedding pipeline.
- New LLM providers or model changes.
- Write actions on the logs page (retry, acknowledge, resolve).
- Trend/analytics aggregation (success rates over time, SLA dashboards).
- Gap detection tools ("jobs with no documentation") — noted as a future category,
  out of scope here.

## User Stories
1. As a developer, I want to ask the AI "this Talend job failed with ORA-12170 — what
   does the ESB architecture doc say is the most likely cause and what should I check?"
   and get an answer that references both the actual log lines and the relevant doc
   section, so I can diagnose faster without context-switching between tools.

2. As an operator, I want to open an `/admin/logs` page and see a table of recent job
   executions filterable by project, job name, and state, so I can get an overview
   of what ran and what failed without typing a question to the AI.

3. As an operator, I want to click a failed execution and see its raw log lines in a
   drilldown panel (filterable by severity), so I can read the full error trace
   without asking the AI to fetch it for me.

4. As an admin, I want to be sure that when a user scoped to project A asks a combined
   question, no log data from project B appears in the answer, even if the user knows
   an execution_id from another project.

5. As a developer on Ollama, I want to know when the AI cannot use runtime tools
   (because Ollama doesn't support tool calls), so I understand why I'm getting a
   docs-only answer instead of being silently misled.

## Functional Requirements

### Cross-reference (LLM path)
1. The `precomputed_answer` early-return in the QA branches of `_prepare_rag_context`
   (non-streaming and streaming paths) must be replaced with a `llm_messages` pass-
   through so both paths always reach the runtime tool loop.
2. Both the streaming path (`run_conversational_rag_stream`) and the non-streaming
   path (`run_conversational_rag`) must be updated symmetrically. No divergence.
3. A single `RequestScope` dataclass must carry both `project_ids` (Qdrant) and
   `project_scope` (Postgres runtime), derived once from the authenticated request
   and threaded through both data-source calls. It must not be possible to pass
   different project scopes to the two sources at any call site.
4. When the active provider lacks tool-call support, the response must include a
   structured signal (e.g. an SSE `info` event or a `[runtime unavailable]` note in
   the answer) rather than silently falling back to RAG-only.
5. The system prompt injected into combined turns must instruct the LLM to cite its
   source for each claim: "(from logs)" or "(from [doc name])". It must not assert
   log data as fact when it came from documents, or vice versa.

### Backend REST API (logs UI)
6. Four new read-only RBAC-scoped GET endpoints must be added to
   `src/api/routes/runtime.py`:
   - `GET /api/v1/runtime/executions` — list executions; accepts query params
     `project_id`, `job_name`, `state`, `since`, `until`, `limit` (max 200).
   - `GET /api/v1/runtime/executions/{execution_id}` — single execution detail.
   - `GET /api/v1/runtime/executions/{execution_id}/logs` — log events for one
     execution; accepts `min_severity`, `limit` (max 1000).
   - `GET /api/v1/runtime/failures` — failure list; accepts `since`, `until`,
     `limit` (max 200).
7. Every endpoint must derive `project_scope` from the authenticated user's assigned
   projects (same `get_user_project_ids` contract used elsewhere). No endpoint may
   return data from a project not in the caller's scope.
8. All four endpoints return 401 for unauthenticated requests and 403 for
   insufficient permissions.

### Frontend logs dashboard
9. A new admin page at `/admin/logs` must exist within the existing Next.js App Router
   structure, accessible to users with admin or manager permissions (consistent with
   the existing admin section guard).
10. The page must have three views:
    - **Executions**: paginated table with columns `execution_id`, `job_name`,
      `project_name`, `state` (badge), `started_at`, `duration_ms`. Filterable by
      state and job name.
    - **Failures**: table with columns `job_name`, `failed_at`, `error_summary`.
    - **Log detail**: drilldown panel (triggered by clicking an execution row)
      showing timestamped log lines with severity badges. Filterable by
      `min_severity`.
11. Four Next.js API proxy routes (`app/api/runtime/*/route.ts`) must forward
    requests to FastAPI with the user's auth cookies, following the existing BFF
    pattern in `lib/fastapi.ts`.
12. A "Logs" link must be added to the admin sidebar navigation.
13. All three states (loading, error, empty data) must be handled and rendered.

## Non-Functional Requirements
- New REST endpoints must respond in < 200ms p95 for result sets up to 200 rows
  (Postgres indexed queries, no ORM N+1).
- No new npm or Python dependencies may be introduced without explicit justification.
- RBAC: zero untested code paths where `project_scope` is not enforced. Every new
  endpoint and every combined LLM call must have a test that verifies a user cannot
  read another project's data.
- The Ollama degradation signal must not add latency to non-Ollama paths.

## Architecture Impact

**RAG retrieval chain (critical area — `/inspect` required):**
- `src/generation/conversational_rag.py`: remove `precomputed_answer` early-return
  in QA branches; pass `llm_messages` through to the runtime loop instead. Symmetric
  change in both streaming and non-streaming functions.
- `src/generation/agent_loop.py`: minor — update `_with_runtime_guide` system prompt
  to include source-citation instruction.
- No schema changes. No migration.

**Auth/RBAC (critical area — `/inspect` required):**
- New `RequestScope` dataclass in `src/runtime/` (or `src/rbac/`) carrying both
  `project_ids: list[str] | None` and `project_scope: list[str] | None`. These are
  always the same value (derived from `get_user_project_ids`), but carrying them
  together makes the coupling explicit and type-checked.
- 4 new FastAPI endpoints in `src/api/routes/runtime.py` using `CurrentUser` +
  `get_user_project_ids` (same RBAC dependency used by all existing endpoints).

**Frontend:**
- New page: `app/admin/logs/page.tsx`
- New proxy routes: `app/api/runtime/executions/route.ts`,
  `app/api/runtime/executions/[id]/route.ts`,
  `app/api/runtime/executions/[id]/logs/route.ts`,
  `app/api/runtime/failures/route.ts`
- Sidebar update: `components/Sidebar.tsx` — add "Logs" entry to `adminSubLinks`

## Implementation Phases

### Phase 1 — RBAC hardening + cross-reference enablement
**Goal**: LLM answers questions using both logs and docs in the same turn.

Deliverables:
- `RequestScope` dataclass; both call sites in `conversational_rag.py` updated
- `precomputed_answer` short-circuit removed from QA branches (streaming + non-streaming)
- Source-citation instruction added to combined-turn system prompt
- Ollama degradation signal (explicit, not silent)
- Tests: combined answer test, cross-project isolation test, Ollama-fallback signal test

Validation:
- Ask "what failed in the last 24 hours and what does the ESB doc say about it?" with
  seeded `esbmain` data → answer must cite both log data and a document.
- Attempt a combined query as a user scoped to project X with data seeded only for
  project Y → must return no log data.
- 538+ backend tests pass.

### Phase 2 — Backend REST API for logs UI
**Goal**: Four RBAC-scoped REST endpoints exposing the runtime store to the frontend.

Deliverables:
- 4 GET endpoints in `src/api/routes/runtime.py`
- Pydantic response schemas for each
- Tests: happy path, RBAC scope enforcement (user A cannot read project B), 401/403

Validation:
- All 4 endpoints return correct data for `esbmain` seeded project.
- A request with a user scoped to a different project returns empty/404.
- No N+1 queries (confirmed by logging or query count assertion).

### Phase 3 — Frontend logs dashboard
**Goal**: `/admin/logs` page with executions table, failures panel, log drilldown.

Deliverables:
- `app/admin/logs/page.tsx` with three views
- 4 BFF proxy routes
- Sidebar entry
- Hook(s) following existing React Query patterns
- Zod schemas for all API response types

Validation:
- Browse executions for `esbmain`, click the failed run, view the 16 ORA-12170 log
  lines in the drilldown. Severity filter to `error` shows only the 4 error lines.
- Loading, error, and empty states render without crashing.
- `npm run build` passes with no TypeScript errors.

## Risks and Open Questions
1. **Are there other early-returns in the QA paths beyond the one identified?**
   The audit found one fork, but a thorough diff is needed during Phase 1 to confirm
   there are no other `precomputed_answer` assignments in the streaming path that were
   missed.
2. **Ollama tool-call gap**: `_TOOL_AWARE_FUNCS` currently only lists `kimi`. On Ollama
   deployments, runtime tools silently do nothing. The explicit signal (Requirement 4)
   addresses the UX but the underlying capability gap remains until Ollama supports
   structured tool calls.
3. **`RequestScope` naming**: the existing codebase uses `project_ids` (for Qdrant)
   and `project_scope` (for runtime) as separate names. A `RequestScope` wrapper must
   not rename or break either — it wraps them, it does not replace the underlying
   contract.
4. **Seeded data coverage**: the cross-reference acceptance test requires both live
   log data AND indexed documentation for the same project. Confirm the `esbmain`
   project has indexed docs; if not, seed a minimal test document as part of Phase 1
   validation setup.

## Acceptance Criteria
- [ ] Ask "what failed in the last 24h and what does the ESB doc say about the cause?"
  → answer references both a log entry (execution_id or error message) AND a doc section.
- [ ] Every log-data claim in the answer is labelled "(from logs)"; every doc claim is
  labelled "(from [doc name])".
- [ ] A user scoped to project A making a combined query receives zero log data from
  project B (automated test + manual check).
- [ ] `GET /api/v1/runtime/executions` returns the 4 seeded executions for an admin user
  and returns an empty list for a user with no project assignments.
- [ ] `/admin/logs` page loads without error, shows executions table, failures panel,
  and log drilldown for `esbmain` seeded data.
- [ ] Severity filter on log drilldown works: selecting `error` hides `info` and
  `warning` lines.
- [ ] On an Ollama-only deployment, the chat renders a visible signal when runtime tools
  are unavailable (not a blank/docs-only answer with no explanation).
- [ ] `npm run build` passes. `pytest` passes (≥538 tests, new tests included).
- [ ] No new Python or npm dependencies added without documented justification.
