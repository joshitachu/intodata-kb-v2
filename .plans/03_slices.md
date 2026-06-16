---
feature: Logs Dashboard + RAG×Runtime Cross-Reference
pipeline_step: 3_slices
total_slices: 3
current_slice: 0
---

## Slice 1: RBAC hardening + RAG×Runtime cross-reference

**Delivers**: The AI answers a question using both live log data and indexed
documentation in the same turn, with explicit per-claim source labels, and the
RBAC coupling between Qdrant and Postgres is enforced by type.

**Expected files**:
- `RAG_pipeline/backend/src/runtime/scope.py` (new) — `RequestScope` dataclass
  carrying `project_ids` (Qdrant) and `project_scope` (Postgres) as one object,
  derived once from the authenticated request.
- `RAG_pipeline/backend/src/generation/conversational_rag.py` — remove
  `precomputed_answer` early-return in the QA branches of both the streaming and
  non-streaming paths; thread `RequestScope` through both `run_runtime_tool_loop`
  call sites.
- `RAG_pipeline/backend/src/generation/agent_loop.py` — extend
  `_with_runtime_guide` / combined-turn system prompt to instruct the LLM to label
  each claim "(from logs)" or "(from [doc name])"; add explicit Ollama-fallback
  signal when `_TOOL_AWARE_FUNCS` does not include the active provider.
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_cross_ref.py`
  (new) — combined-answer test (logs + docs in one turn), cross-project isolation
  test, Ollama-fallback signal test.
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py` — extend for
  source-citation instruction and Ollama signal.

**Critical area**: yes — RAG retrieval chain (`src/generation/conversational_rag.py`)
and Auth/RBAC (explicit `RequestScope` threading). `/inspect` required.

**Depends on**: none

---

## Slice 2: Backend REST API for the logs UI

**Delivers**: Four RBAC-scoped read-only REST endpoints expose the runtime store to
any client; a frontend or curl call can list executions, view failures, fetch details,
and read log lines for projects the caller is authorized to see.

**Expected files**:
- `RAG_pipeline/backend/src/api/routes/runtime.py` — add 4 GET endpoints:
  `GET /api/v1/runtime/executions`, `GET /api/v1/runtime/executions/{execution_id}`,
  `GET /api/v1/runtime/executions/{execution_id}/logs`,
  `GET /api/v1/runtime/failures`. Each derives `project_scope` from `CurrentUser`
  via `get_user_project_ids`. Pagination via `limit`/`since`/`until` query params.
- `RAG_pipeline/backend/src/schemas.py` (or a new `src/api/schemas/runtime.py`) —
  Pydantic response schemas for each endpoint (`ExecutionListResponse`,
  `ExecutionDetailResponse`, `LogEventListResponse`, `FailureListResponse`).
- `RAG_pipeline/backend/tests/test_api/test_runtime_endpoints.py` (new) — happy
  path for each endpoint, RBAC scope enforcement (user A cannot read project B),
  401/403 for unauthenticated/unauthorized requests.

**Critical area**: yes — Auth/RBAC (new endpoints must enforce project-scope via
`CurrentUser` + `get_user_project_ids`). `/inspect` required.

**Depends on**: Slice 1 (uses `RequestScope`; endpoints share the RBAC contract
made explicit in Slice 1, but can be built independently if needed)

---

## Slice 3: Frontend logs dashboard

**Delivers**: An `/admin/logs` page in the existing RAG frontend where operators can
browse executions, view a failures panel, and drill into log lines for any execution
they have access to — with severity filtering and loading/error/empty states handled.

**Expected files**:
- `RAG_pipeline/frontend/app/admin/logs/page.tsx` (new) — three-view admin page:
  Executions table (filterable by state/job), Failures panel, Log detail drilldown.
- `RAG_pipeline/frontend/app/api/runtime/executions/route.ts` (new)
- `RAG_pipeline/frontend/app/api/runtime/executions/[id]/route.ts` (new)
- `RAG_pipeline/frontend/app/api/runtime/executions/[id]/logs/route.ts` (new)
- `RAG_pipeline/frontend/app/api/runtime/failures/route.ts` (new)
- `RAG_pipeline/frontend/hooks/useRuntimeLogs.ts` (new) — React Query hooks for
  executions list, log events, and failures; follows `useRuntimeStatus` pattern.
- `RAG_pipeline/frontend/types/runtime.ts` (new) — Zod schemas for all 4 API
  response types.
- `RAG_pipeline/frontend/components/Sidebar.tsx` — add "Logs" entry to
  `adminSubLinks`.

**Critical area**: no — frontend only; RBAC enforced at the API boundary (Slice 2).

**Depends on**: Slice 2 (requires the 4 REST endpoints to exist)
