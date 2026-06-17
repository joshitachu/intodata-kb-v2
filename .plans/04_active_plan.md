---
feature: Smart Runtime Log Intelligence
slice: Slice 1 - Deterministic Enrichment + Insight Store
sub_project: rag_pipeline
critical_area: no - additive runtime schema/service work; security-sensitive project_id propagation, but no auth/RBAC or retrieval-chain edits expected
verdict: PASS
approved_at: 2026-06-17
---

## Problem Statement

The runtime store can persist raw executions, failures, and log lines, but it
does not yet produce bounded operational insight. Chat and dashboard users still
need to read low-level log events or ask the model to interpret raw lines. That
does not scale to high-volume logs and would create token bloat if raw windows
were pushed into LLM context.

Slice 1 establishes the offline/deterministic insight layer: parse structured
signals from raw log lines, build project-scoped execution summaries and incident
records, and preserve bounded evidence references. This slice intentionally does
not add API endpoints, chat tools, or frontend UI; those are later slices.

## Proposed Solution

Add additive runtime-insight tables and a deterministic builder service under
`src/runtime/insights/`.

Use the existing held-runtime pattern:

- SQLAlchemy row models in `src/db/models/`.
- Alembic additive migration with indexes/unique constraints.
- Async SQLAlchemy service/repository code using select-then-update/insert rather
  than Postgres-only `ON CONFLICT`, so SQLite tests keep working.
- Seed/test flow driven by `seed_rich_runtime_data()`.

The parser should be conservative. It extracts strong signals when present and
returns `None`/empty fields when uncertain. It must not invent causes. The first
builder can use deterministic heuristics for the seeded Talend-style failure:
component extraction, Oracle timeout error code, exception class, retry attempts,
warning/error counts, primary failed component, evidence lines, and next-check
suggestions.

## Affected Files

- `RAG_pipeline/backend/alembic/versions/032_add_runtime_insights.py` (new)
- `RAG_pipeline/backend/src/db/models/runtime_insights.py` (new)
- `RAG_pipeline/backend/src/db/models/__init__.py`
- `RAG_pipeline/backend/src/runtime/insights/__init__.py` (new)
- `RAG_pipeline/backend/src/runtime/insights/parser.py` (new)
- `RAG_pipeline/backend/src/runtime/insights/builder.py` (new)
- `RAG_pipeline/backend/src/runtime/store/seed.py`
- `RAG_pipeline/backend/tests/test_runtime/test_insight_parser.py` (new)
- `RAG_pipeline/backend/tests/test_runtime/test_insight_builder.py` (new)

Possible but avoid unless needed:

- `RAG_pipeline/backend/src/runtime/store/repository.py` only if the builder needs
  a reusable raw-row read helper. Prefer keeping insight-specific reads inside the
  new builder for this slice.

## Architecture Contracts

- Raw runtime tables remain the source of truth. Insight tables are derived and
  rebuildable.
- Every derived table carries `project_id`. No insight row may be global or
  inferred only from execution id.
- Derived rows are idempotent per project/execution/log event/signature. Rebuilding
  the same seeded run with the same timestamps must update rows rather than
  duplicate them.
- No raw log vectorization and no LLM enrichment in this slice.
- Parser output is evidence metadata, not final truth. The builder may produce
  `root_cause_candidate` and `confidence`, but must preserve uncertainty.
- Store bounded evidence excerpts only. Full raw log text remains in
  `runtime_log_events`.
- Use generic JSON columns where needed for SQLite test compatibility, matching
  existing project conventions.
- Keep runtime insight modules source-agnostic where practical. Talend-specific
  regexes can live in parser helpers, but table and service names should not be
  Talend-specific.

## Implementation Steps

1. Create migration `032_add_runtime_insights.py`.
   - Add `runtime_enriched_events`.
   - Add `runtime_error_signatures`.
   - Add `runtime_execution_summaries`.
   - Add `runtime_incidents`.
   - Add `runtime_incident_evidence`.
   - Include `created_at` and `updated_at` columns in the migration, matching
     existing runtime migration style.
   - Add indexes for common future reads:
     `project_id`, `(project_id, execution_id)`, `(project_id, incident_id)`,
     `(project_id, signature_hash)`, and relevant timestamps.
   - Add unique constraints for idempotency, especially:
     `(project_id, log_event_id)` for enriched events,
     `(project_id, execution_id)` for summaries/incidents,
     `(project_id, signature_hash)` for signatures,
     `(project_id, incident_id, log_event_id)` for evidence.

2. Add SQLAlchemy models in `runtime_insights.py`.
   - Mirror migration columns exactly.
   - Import/export them from `src/db/models/__init__.py` so test metadata
     creation sees the tables.
   - Prefer simple scalar fields plus JSON for bounded lists such as
     `next_checks` or `evidence_refs`.

3. Implement `parser.py`.
   - Define a typed dataclass, e.g. `ParsedRuntimeLog`, with optional fields:
     `component_name`, `error_code`, `exception_class`, `retry_attempt`,
     `retry_max`, `duration_ms`, `timeout_ms`, `endpoint`, `host`,
     `database_name`, `table_name`, `queue_name`, `message_family`, and
     `confidence`.
   - Implement pure functions with type hints:
     `parse_runtime_log_message(message: str) -> ParsedRuntimeLog`.
   - Cover seeded rich log patterns:
     bracket component prefixes like `[tOracleConnection_1]`,
     `ORA-12170`,
     `SQLRecoverableException`,
     `attempt 3/3`,
     `30000ms`, `timeout=30s`,
     JDBC host/database,
     JMS queue names,
     SELECT table names.
   - Add basic credential redaction for extracted endpoints/excerpts:
     never preserve obvious `password=`, `token=`, `secret=`, or
     credential-like values.

4. Implement `builder.py`.
   - Add a function/service such as:
     `build_runtime_insights_for_execution(session, project_id, execution_id,
     source_key, ingested_at)`.
   - Read raw `RuntimeExecutionRow` and ordered `RuntimeLogEventRow` rows for the
     project/execution.
   - Parse each log event and upsert `RuntimeEnrichedEventRow`.
   - Build one `RuntimeExecutionSummaryRow`:
     warning/error counts, first warning/error, final error, failed component,
     primary error code, primary exception class, bounded summary text, and
     evidence references.
   - For failed executions, build one `RuntimeIncidentRow`:
     deterministic `incident_id`, `signature_hash`, symptom summary,
     root-cause candidate, confidence, first bad timestamp, last healthy timestamp,
     and next checks.
   - Upsert `RuntimeErrorSignatureRow` for the normalized signature.
   - Upsert bounded `RuntimeIncidentEvidenceRow` rows for the strongest evidence:
     first warning, first error, exception/error-code line, final failure line,
     and route shutdown/rollback line when present.
   - Keep all upserts dialect-portable with select-then-update/insert.

5. Wire an optional seed path.
   - Add an optional `build_insights: bool = False` parameter to
     `seed_mock_runtime_data()` and `seed_rich_runtime_data()`, or add a separate
     explicit helper such as `build_seed_runtime_insights(...)`.
   - Add a CLI flag such as `--build-insights`.
   - Do not auto-run this at application startup.

6. Add parser tests.
   - Validate component extraction.
   - Validate `ORA-12170`.
   - Validate `SQLRecoverableException`.
   - Validate retry attempt/max extraction.
   - Validate duration/timeout extraction.
   - Validate queue/table/host/database extraction where present.
   - Validate safe no-match behavior on generic informational lines.
   - Validate basic redaction behavior.

7. Add builder tests.
   - Use `seed_rich_runtime_data(..., project_id="esbmain")`.
   - Build insights for the failed execution.
   - Assert summary contains:
     job `esb_order_sync_route`,
     failed component `tOracleConnection_1`,
     error code `ORA-12170`,
     exception class `SQLRecoverableException`,
     warning and error counts,
     bounded evidence references.
   - Assert incident contains:
     deterministic incident id,
     signature hash,
     likely/root-cause candidate mentioning source database timeout,
     confidence,
     next checks,
     first bad timestamp.
   - Assert idempotency by building twice and checking row counts stay stable.
   - Assert project scoping by seeding a second project and verifying derived rows
     keep separate `project_id` values.

## Verification Strategy

Run focused backend validation for this slice:

```bash
cd RAG_pipeline/backend
python3 -m py_compile src/db/models/runtime_insights.py src/runtime/insights/parser.py src/runtime/insights/builder.py src/runtime/store/seed.py
uv run pytest tests/test_runtime/test_insight_parser.py tests/test_runtime/test_insight_builder.py tests/test_runtime/test_seed.py tests/test_runtime/test_store_repository.py
```

If migration syntax needs checking locally:

```bash
cd RAG_pipeline/backend
python3 -m py_compile alembic/versions/032_add_runtime_insights.py
```

Full validation can wait for `/validate`, but the focused runtime tests above
must pass before moving to inspection or the next slice.

## Out of Scope

- Runtime insight REST endpoints.
- Chat/runtime tool changes.
- Frontend dashboard changes.
- Similar-failure API/tool behavior beyond storing the first signature row needed
  by future slices.
- Real Talend TMC sync/polling.
- Production alert/email delivery.
- LLM-based enrichment.
- Any change to `src/auth/`, `src/rbac/`, or `src/retrieval/`.
