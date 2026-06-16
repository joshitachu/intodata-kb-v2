---
feature: Held Runtime-Data Architecture (Model 2 skeleton, source-agnostic)
slice: Slice 3 - mock-fixture seed + end-to-end chat
sub_project: rag_pipeline
critical_area: no - reuses Slice 2 RBAC-enforced read path
verdict: PASS
approved_at: 2026-06-16
---

## Problem Statement

Slices 1 and 2 created the held runtime-data store, repository, `stored`
provider, and request-scope threading. The system can now read project-filtered
held data when rows exist, but there is still no supported way to populate the
store for local validation or demo use.

Slice 3 closes the skeleton by loading the existing credential-free mock runtime
fixtures through the repository write-port, then proving `RUNTIME_PROVIDER_KEY=stored`
can answer through the runtime tool path from seeded, project-scoped data. The
seed must be idempotent and must set a truthful freshness watermark. It must not
create a new HTTP ingestion endpoint, bypass the repository, or change the
LLM-facing tool vocabulary.

One implementation detail matters: the mock provider fixtures use fixed
timestamps (`2026-01-01`). To keep "last 24h" demo validation meaningful, the
seed should preserve fixture content but shift fixture timestamps to a supplied
or current `last_synced_at`, with tests passing an explicit deterministic clock.

## Proposed Solution

Add an explicit in-process seed module under `src/runtime/store/seed.py`.

The seed will:

- Read the existing mock runtime fixture data via the mock provider's generic
  provider methods, not by duplicating fixture rows.
- Assign all seeded rows to a caller-provided demo `project_id`.
- Use the Slice 1 repository write-port for executions, failures, log events,
  and sync-state watermark updates.
- Shift fixture timestamps relative to a supplied `synced_at` timestamp so
  seeded data is fresh for local chat validation while tests remain stable.
- Be safe to run repeatedly without duplicate rows.
- Provide a small module entrypoint, e.g.
  `cd RAG_pipeline/backend && python -m src.runtime.store.seed --project-id demo-runtime`.

Then add focused tests for idempotency/freshness and an end-to-end backend test
through `execute_runtime_tool` with `RUNTIME_PROVIDER_KEY=stored`. A chat-level
test may mock the tool-aware LLM decision so the existing conversational runtime
loop asks for `detect_failures` and receives seeded stored data without requiring
network LLM access.

## Affected Files

Expected changes:

- `RAG_pipeline/backend/src/runtime/store/seed.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_seed.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_stored_end_to_end.py` (new)
- `.env.production.template` (document `RUNTIME_PROVIDER_KEY=stored` for held-data validation)

Likely test-only or documentation/comment updates:

- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_tools.py`
- `docker-compose.yml` (comment update only, if runtime provider comments still omit `stored`)

Do not touch unless a real defect is found:

- `RAG_pipeline/backend/src/runtime/client.py`
- `RAG_pipeline/backend/src/runtime/providers/stored.py`
- `RAG_pipeline/backend/src/runtime/store/repository.py`
- `RAG_pipeline/backend/src/generation/agent_loop.py`
- `RAG_pipeline/backend/src/generation/conversational_rag.py`

## Architecture Contracts

- Seed writes must go through `RuntimeStoreRepository`; do not insert SQLAlchemy
  rows directly.
- The seed must be source-agnostic at the store boundary. `source_key` may be
  `"mock"` because the fixture source is mock, but table fields and repository
  calls must remain generic.
- Running the seed twice with the same `project_id`, `source_key`, and fixture
  data must update existing rows rather than duplicate them.
- Seeded rows must be project-scoped with the provided `project_id`.
- The watermark must be written via `update_sync_state(source_key, project_id,
  last_synced_at)`.
- Fixture timestamp shifting must be deterministic when `synced_at` is passed
  by tests and useful for demo runs when omitted.
- `stored` provider reads must continue to enforce `ProjectScope`; this slice
  must not weaken or bypass Slice 2's RBAC path.
- No new API route, frontend behavior, SSE contract, or LLM tool schema changes.
- Runtime data stays structured in Postgres and is not embedded into Qdrant.
- Do not auto-seed at backend startup. Seeding is an explicit operator/developer
  action to avoid hidden production data mutation.

## Implementation Steps

1. Add the seed module.
   - Create `src/runtime/store/seed.py`.
   - Define constants for default source/project values, for example
     `DEFAULT_SOURCE_KEY = "mock"` and `DEFAULT_PROJECT_ID = "demo-runtime"`.
   - Define a public async function such as:
     `seed_mock_runtime_data(session, *, project_id, source_key="mock", synced_at=None)`.
   - Use `RuntimeStoreRepository(session)` for all writes.

2. Reuse mock fixtures through provider methods.
   - Import `src.runtime.providers.mock.provider`.
   - Call the generic provider methods with `RuntimeProviderContext(project_scope=None, db=session)`
     or `None` if the mock provider ignores context.
   - Fetch:
     - `list_executions(ListExecutionsInput(limit=...))`
     - `detect_failures(DetectFailuresInput(limit=...))`
     - `get_execution_logs(GetExecutionLogsInput(execution_id=...))` for each execution.
   - Avoid copying `_EXECUTIONS`, `_FAILURES`, or `_LOGS` constants into the
     seed module.

3. Shift fixture timestamps.
   - Compute the max fixture timestamp across executions, failures, and logs.
   - If `synced_at` is omitted, use current UTC time.
   - Shift every timestamp by `synced_at - max_fixture_timestamp`.
   - Preserve relative ordering and durations.
   - Set `last_synced_at` to `synced_at`.

4. Write through the repository.
   - Upsert all executions with `upsert_execution(project_id, execution, source_key, synced_at)`.
   - Upsert all failures with `upsert_failure(project_id, failure, source_key, synced_at)`.
   - Upsert all log events with `upsert_log_event(project_id, event, source_key, synced_at)`.
   - Call `update_sync_state(source_key, project_id, synced_at)`.
   - Return a small summary dataclass or dict with counts and `last_synced_at`
     for CLI output and tests.

5. Add a module CLI entrypoint.
   - Use `argparse` with `--project-id`, `--source-key`, and optional
     `--synced-at` ISO timestamp.
   - Open `async_session_factory`, run the seed, commit on success, rollback on
     failure, and log a concise summary.
   - Follow the existing `scripts/seed_data.py` style: `asyncio.run(main())`,
     `logging.basicConfig`, explicit rollback, non-zero exit on failure.

6. Add seed tests.
   - Verify executions, failures, logs, and watermark are written for the demo
     project.
   - Verify running the seed twice does not increase row counts.
   - Verify timestamp shifting makes seeded failures/logs fall within the
     supplied deterministic freshness window.
   - Verify all rows use the requested `project_id`.
   - Verify repository freshness returns `last_synced_at`.

7. Add stored end-to-end tests.
   - Seed mock data through `seed_mock_runtime_data`.
   - Set `runtime_provider_key="stored"` and call `execute_runtime_tool` with
     `project_scope=[demo_project]` and `db=db_session`.
   - Verify `detect_failures` returns the seeded failed job.
   - Verify `get_execution_logs` returns seeded logs for the failed execution.
   - Verify a different project scope cannot read the seeded rows.
   - Verify `project_scope=None` can read them for view-all users.

8. Add a chat-path validation test if it stays focused and deterministic.
   - Reuse existing conversational runtime-tool tests.
   - Patch `generate_answer_with_runtime_tools` so the first LLM turn requests
     `detect_failures` and the second produces a final answer.
   - Run `run_conversational_rag` or `run_conversational_rag_stream` with
     `RUNTIME_PROVIDER_KEY=stored`, seeded `db_session`, and `project_ids=[demo_project]`.
   - Assert runtime progress events include the stored tool result and the final
     answer path uses runtime data.
   - Keep this backend-only; do not require real LLM/Ollama or frontend.

9. Update runtime configuration docs.
   - Update `.env.production.template` comments to mention:
     - `RUNTIME_PROVIDER_KEY=stored` reads held runtime data from Postgres.
     - Mock fixture seed command for local validation.
     - `mock` remains direct fixture provider; `stored` is the DB-backed held provider.
   - Update `docker-compose.yml` comments only if they still list `mock` and
     `talend_tmc` but omit `stored`.
   - Do not add a startup seed toggle unless implementation uncovers a concrete
     need; explicit seeding is safer.

## Verification Strategy

Run syntax checks for changed Python files:

```bash
cd RAG_pipeline/backend && python3 -m py_compile \
  src/runtime/store/seed.py
```

Run focused seed and stored-provider tests:

```bash
cd RAG_pipeline/backend && uv run pytest \
  tests/test_runtime/test_seed.py \
  tests/test_runtime/test_stored_end_to_end.py \
  tests/test_runtime/test_store_repository.py \
  tests/test_runtime/test_stored_provider.py \
  tests/test_runtime/test_client.py
```

Run conversational runtime regression tests:

```bash
cd RAG_pipeline/backend && uv run pytest \
  tests/test_generation/test_agent_loop.py \
  tests/test_generation/test_conversational_rag_runtime_tools.py \
  tests/test_api/test_conversations.py
```

Optional local operator check after migrations are applied:

```bash
cd RAG_pipeline/backend
python -m src.runtime.store.seed --project-id demo-runtime
RUNTIME_PROVIDER_KEY=stored uv run pytest tests/test_runtime/test_stored_end_to_end.py
```

Because this slice should not alter the RBAC filter itself, it is not critical
by default. Reclassify to critical and run `/inspect` if implementation changes
the repository scope filter, runtime client scope threading, auth dependencies,
or conversation request authorization.

## Out of Scope

- Real runtime source ingestion or sync worker.
- Auto-seeding on application startup.
- New HTTP ingestion endpoints.
- Frontend changes.
- New runtime tool names or input schemas.
- Changing the SSE contract.
- Changing auth/RBAC policy.
- Vectorizing runtime data into Qdrant.
- Structured log drill-down UI.
- Semantic "similar past failures" search.
