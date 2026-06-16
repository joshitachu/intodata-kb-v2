---
feature: Held Runtime-Data Architecture (Model 2 skeleton, source-agnostic)
slice: Slice 2 - stored provider + registry + request scope threading
sub_project: rag_pipeline
critical_area: yes - Auth/RBAC scope is threaded into runtime tool execution
verdict: PENDING
approved_at:
---

## Problem Statement

Slice 1 added the held runtime-data store and repository, but chat still cannot
read from it. `execute_runtime_tool` can resolve `mock` and `talend_tmc` only,
and the runtime tool loop does not pass the authenticated user's project scope
to providers.

To enable `RUNTIME_PROVIDER_KEY=stored` safely, the allowed-project scope must
come from the request principal via `get_user_project_ids`, then travel through
the backend runtime path out-of-band from LLM-controlled tool arguments. The
stored provider also needs the current `AsyncSession` so it can delegate to the
Slice 1 repository.

## Proposed Solution

Implement a DB-backed `stored` runtime provider and explicitly thread runtime
execution context through the existing runtime boundary.

Decision: use explicit additive parameters, not a `contextvar`.

- `run_conversational_rag*` already receives `project_ids` and `db`.
- `run_runtime_tool_loop` should accept keyword-only `project_scope` and `db`.
- `execute_runtime_tool` should accept keyword-only `project_scope` and `db`.
- Provider methods should receive a small `RuntimeProviderContext` containing
  `project_scope` and `db`.
- Existing `mock` and `talend_tmc` providers should accept and ignore that
  context, preserving their behavior.
- The new `stored` provider should use `RuntimeStoreRepository(db)` and pass
  `project_scope` into every read.

This keeps the runtime tool vocabulary, LLM-facing schemas, SSE event names,
frontend behavior, and existing provider keys unchanged.

## Affected Files

Expected changes:

- `RAG_pipeline/backend/src/runtime/providers/stored.py` (new)
- `RAG_pipeline/backend/src/runtime/providers/__init__.py`
- `RAG_pipeline/backend/src/runtime/providers/mock.py`
- `RAG_pipeline/backend/src/runtime/providers/talend_tmc.py`
- `RAG_pipeline/backend/src/runtime/client.py`
- `RAG_pipeline/backend/src/generation/agent_loop.py`
- `RAG_pipeline/backend/src/generation/conversational_rag.py`
- `RAG_pipeline/backend/tests/test_runtime/test_stored_provider.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_client.py`
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py`
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py`

Possibly touched if type imports or test fixtures require it:

- `RAG_pipeline/backend/tests/test_api/test_conversations.py`

Do not touch in Slice 2:

- LLM tool definitions in `RAG_pipeline/backend/src/runtime/registry.py`
- frontend files
- API route request/response schemas
- database schema or migrations
- seed routines

## Architecture Contracts

- `stored` must be provider-agnostic. Do not introduce Talend-specific names,
  fields, or behavior.
- Authorization must come only from `project_scope` produced by authenticated
  backend code. LLM `RuntimeToolCall.arguments` must never widen scope.
- `ProjectScope` semantics must match Slice 1 and `get_user_project_ids`:
  - `None`: unrestricted, for superadmin/view-all users.
  - `[]`: no access.
  - `["id", ...]`: restrict to those project IDs.
- `execute_runtime_tool` must require an explicit `project_scope` keyword so
  new call sites cannot accidentally omit scope and get unrestricted stored
  reads.
- The `stored` provider must require a DB session and map
  `RuntimeRecordNotFoundError` to `RuntimeProviderError`, preserving the
  structured `RuntimeToolResult` error contract.
- Existing `mock` and `talend_tmc` behavior must remain unchanged except for
  accepting the new context parameter.
- `_PROVIDER_MODULE_PATHS` must resolve `stored` lazily, following the current
  provider registry pattern so tests can patch provider methods.
- The LLM-facing tool names and input schemas remain exactly:
  `list_executions`, `get_execution_details`, `detect_failures`,
  `get_execution_logs`.
- SSE events remain `runtime_tool_call` and `runtime_tool_result`; payloads stay
  sanitized and bounded by `runtime/client.py`.

## Implementation Steps

1. Refine the provider seam.
   - Add `RuntimeProviderContext` in `src/runtime/providers/__init__.py` with:
     - `project_scope: ProjectScope`
     - `db: AsyncSession | None`
   - Update the `RuntimeProvider` protocol so each method accepts
     `(params, context)`.
   - Update `mock.py` and `talend_tmc.py` method signatures to accept the
     context and ignore it.

2. Update runtime client execution.
   - Change `execute_runtime_tool` to:
     `execute_runtime_tool(call, *, project_scope: ProjectScope, db: AsyncSession | None = None)`.
   - Build a `RuntimeProviderContext` after validation/provider resolution.
   - Pass context to the provider method.
   - Register `_PROVIDER_MODULE_PATHS["stored"] = "src.runtime.providers.stored"`.
   - Preserve disabled, unknown-provider, unknown-tool, validation, timeout,
     provider-error, sanitization, and truncation behavior.

3. Add the stored provider.
   - Create `src/runtime/providers/stored.py`.
   - Delegate the four generic methods to `RuntimeStoreRepository(context.db)`.
   - Pass `context.project_scope` to every repository read.
   - Raise `RuntimeProviderError` when `context.db is None`.
   - Map `RuntimeRecordNotFoundError` to `RuntimeProviderError`.
   - Return only the normalized runtime models already returned by the
     repository.

4. Thread context through the tool loop.
   - Change `run_runtime_tool_loop` to require keyword-only `project_scope` and
     `db`.
   - Pass those values into `execute_runtime_tool`.
   - Keep progress event order and payload shape unchanged.
   - Update tests to assert scope and db are forwarded.

5. Thread request scope from chat.
   - In both `run_conversational_rag` and `run_conversational_rag_stream`, pass
     `project_ids` and `db` into `run_runtime_tool_loop`.
   - Do not read project IDs from tool arguments.
   - Do not change `ConversationMessageRequest`, SSE event names, or frontend
     code.

6. Add provider/client security tests.
   - Seed Slice 1 repository rows for `project-a` and `project-b`.
   - With `runtime_provider_key="stored"` and `project_scope=["project-a"]`,
     prove list/failure/log/detail reads cannot return `project-b`.
   - Prove a tool argument such as `project_name` cannot widen access.
   - Prove `project_scope=[]` returns empty/structured not-found behavior.
   - Prove `project_scope=None` can read all rows.
   - Prove missing DB session maps to a structured provider error.

7. Update nearby runtime and chat tests.
   - Update existing `execute_runtime_tool` tests to pass explicit
     `project_scope=None` for unrestricted mock/Talend cases.
   - Update agent-loop tests for the new keyword-only scope/db arguments.
   - Update conversational runtime-tool tests to verify `project_ids` from the
     request path are passed through.

## Verification Strategy

Run syntax checks for changed Python files:

```bash
cd RAG_pipeline/backend && python3 -m py_compile \
  src/runtime/providers/__init__.py \
  src/runtime/providers/mock.py \
  src/runtime/providers/talend_tmc.py \
  src/runtime/providers/stored.py \
  src/runtime/client.py \
  src/generation/agent_loop.py \
  src/generation/conversational_rag.py
```

Run focused runtime tests:

```bash
cd RAG_pipeline/backend && uv run pytest \
  tests/test_runtime/test_store_repository.py \
  tests/test_runtime/test_stored_provider.py \
  tests/test_runtime/test_client.py \
  tests/test_runtime/test_mock_provider.py \
  tests/test_runtime/test_talend_tmc_provider.py
```

Run chat/tool-loop regressions:

```bash
cd RAG_pipeline/backend && uv run pytest \
  tests/test_generation/test_agent_loop.py \
  tests/test_generation/test_conversational_rag_runtime_tools.py \
  tests/test_api/test_conversations.py
```

Because this slice changes the Auth/RBAC runtime boundary, run `/inspect` after
implementation and before commit.

## Out of Scope

- Seed routine for mock fixtures.
- End-to-end chat acceptance against seeded stored data.
- Runtime freshness wording in final chat answers.
- New API routes.
- Frontend changes.
- New LLM tool names or schema changes.
- Database schema changes or migrations.
- External sync workers or live source ingestion.
- Vectorizing runtime data into Qdrant.
