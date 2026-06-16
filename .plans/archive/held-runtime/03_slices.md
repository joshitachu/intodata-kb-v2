---
feature: Held Runtime-Data Architecture (Model 2 skeleton, source-agnostic)
pipeline_step: 3_slices
total_slices: 3
current_slice: 0
---

## Slice 1: Held runtime store + repository + migration

**Delivers**: A project-scoped Postgres store for runtime executions/failures/logs
with a freshness watermark, plus a repository exposing RBAC-filtered reads and an
in-process write-port — fully unit-tested. Foundation only; nothing reads it yet, so
the running system is unchanged.

**Expected files**:
- `RAG_pipeline/backend/src/db/models/runtime_data.py` (new — SQLAlchemy models:
  executions, failures, log events, sync state; `project_id` indexed, `ingested_at`)
- `RAG_pipeline/backend/src/db/models/__init__.py` (register new models)
- `RAG_pipeline/backend/alembic/versions/<rev>_runtime_data.py` (new additive migration
  with reversible `downgrade`)
- `RAG_pipeline/backend/src/runtime/store/__init__.py` (new)
- `RAG_pipeline/backend/src/runtime/store/repository.py` (new — scope-filtered reads +
  idempotent upsert write-port + watermark, following the `get_user_project_ids`
  contract: None / list / empty)
- `RAG_pipeline/backend/tests/test_runtime/test_store_repository.py` (new — reads,
  RBAC/project filtering incl. cross-project denial, idempotent upsert, watermark)

**Critical area**: yes — Auth/RBAC. The project-scope filter that prevents
cross-project data leakage lives in the repository; `/inspect` required.

**Depends on**: none

## Slice 2: `stored` provider + registry + request scope threading

**Delivers**: With `RUNTIME_PROVIDER_KEY=stored`, `execute_runtime_tool` resolves the
new provider and returns normalized, project-filtered held data. The allowed-project
scope is threaded from the authenticated request principal into the provider call —
never from LLM-supplied tool arguments. No change to the LLM tool vocabulary, SSE
contract, or frontend.

**Expected files**:
- `RAG_pipeline/backend/src/runtime/providers/stored.py` (new — delegates the four
  generic methods to the repository, returns normalized models)
- `RAG_pipeline/backend/src/runtime/client.py` (register `stored` in
  `_PROVIDER_MODULE_PATHS`; thread allowed-project scope into `execute_runtime_tool`)
- `RAG_pipeline/backend/src/runtime/providers/__init__.py` (only if the scope-passing
  mechanism refines the provider seam — decide at /plan)
- `RAG_pipeline/backend/src/generation/agent_loop.py` (pass scope through
  `run_runtime_tool_loop`)
- `RAG_pipeline/backend/src/generation/conversational_rag.py` (pass the request's
  project scope into the tool loop)
- `RAG_pipeline/backend/tests/test_runtime/test_stored_provider.py` (new — method
  mapping/normalization, registry resolution)
- `RAG_pipeline/backend/tests/test_runtime/test_client.py` (scope-enforcement test:
  project-A scope cannot read project-B rows; LLM arguments cannot widen scope)

**Critical area**: yes — Auth/RBAC (scope enforcement at the runtime boundary and the
backend tool loop). `/inspect` required.

**Depends on**: Slice 1

**Open decision (resolve at /plan)**: scope-threading mechanism — (A, recommended)
explicit additive `project_scope` parameter through `run_runtime_tool_loop` →
`execute_runtime_tool` → provider; or (B) request-scoped `contextvar` set in the chat
route. A is explicit and testable; B avoids signature changes but is implicit.

## Slice 3: Mock-fixture seed + end-to-end chat

**Delivers**: A seed loads the existing mock fixtures through the in-process
write-port (assigning a demo `project_id`) and sets the watermark; running it twice is
idempotent. With `RUNTIME_PROVIDER_KEY=stored`, chat answers "what failed in the last
24h?" from seeded, project-filtered held data, and freshness (`last_synced_at`,
`ingested_at`) is retrievable.

**Expected files**:
- `RAG_pipeline/backend/src/runtime/store/seed.py` (new — seeds via the write-port;
  reuses the mock fixtures, no change to `providers/mock.py`)
- `RAG_pipeline/backend/tests/test_runtime/test_seed.py` (new — idempotency, watermark
  set, rows project-scoped)
- `RAG_pipeline/backend/tests/test_runtime/test_stored_end_to_end.py` (new — through
  `execute_runtime_tool` against the seeded store with a scope)
- `.env.production.template` (document `RUNTIME_PROVIDER_KEY=stored`, optional)
- `docker-compose.yml` (only if a seed/runtime toggle env is needed)

**Critical area**: no — seed + integration validation; reuses Slice 2's enforced read
path. (Reclassify to yes only if a fix here touches the RBAC filter.)

**Depends on**: Slice 2

## Out of scope for this epic (recorded design decisions)

- **Log retrieval is structured, not vectorized.** Runtime logs/executions are served
  by the four parameterized, RBAC-filtered tools over Postgres — never embedded into
  Qdrant. See `02_prd.md` "Design Decision — Log/Runtime Retrieval Strategy".
- **Log display UI** (an expandable, sanitized, bounded structured-log panel reusing
  the EP-004 runtime-progress surface, not the RAG document/citation system) is a
  future frontend slice, not part of this store skeleton.
- **Semantic "similar past failures" search** would be the only justification for a
  narrow embedding index over failure-summary text. Deferred and explicitly flagged;
  if never built, runtime data stays Qdrant-free.
