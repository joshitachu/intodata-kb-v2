---
feature: Smart Runtime Log Intelligence
slice: Slice 2 - Scoped Insight Repository + REST API
sub_project: rag_pipeline
critical_area: yes - new runtime API read surface must enforce Auth/RBAC project scope; cross-project execution, incident, evidence, and timeline lookups are leakage risks
verdict: PASS
approved_at: 2026-06-17
---

## Problem Statement

Slice 1 created deterministic runtime insight rows, but those rows are still only
internal storage. The dashboard, future chat tools, and tests cannot retrieve
execution summaries, incidents, evidence, or timelines through a bounded API.

The existing runtime REST API exposes raw executions, failures, and logs with
project-scope enforcement. Slice 2 must add the smart-log read layer with the
same authorization semantics: scope comes from the authenticated user via
`get_user_project_ids()`, optional `project_id` filters may only narrow scope,
and guessed cross-project IDs must return `404` or empty lists without revealing
existence.

## Proposed Solution

Add a dedicated insight repository under `src/runtime/insights/` and extend the
existing `src/api/routes/runtime.py` router with read-only insight endpoints.

The repository should follow the existing `RuntimeStoreRepository` pattern:
async SQLAlchemy, explicit `ProjectScope`, `_denies_all` behavior, scope applied
in SQL, deterministic row-to-Pydantic mapping, and `RuntimeRecordNotFoundError`
for point lookups that are unknown or out of scope.

Because the Slice 1 schema is intentionally lean, API models should join derived
rows with raw runtime rows where needed:

- execution summary endpoint joins `runtime_execution_summaries` with
  `runtime_executions` and optionally `runtime_incidents`;
- incident detail joins `runtime_incidents` with execution summary/raw execution
  and signature rows;
- evidence joins `runtime_incident_evidence` to `runtime_log_events` so callers
  receive timestamps/severity plus bounded excerpts;
- timeline reads raw log events for the execution and left-joins enriched event
  fields when present.

No schema migration is planned for this slice unless implementation finds a hard
gap that cannot be solved by joins.

## Affected Files

- `RAG_pipeline/backend/src/runtime/insights/models.py` (new)
- `RAG_pipeline/backend/src/runtime/insights/repository.py` (new)
- `RAG_pipeline/backend/src/runtime/insights/__init__.py`
- `RAG_pipeline/backend/src/api/routes/runtime.py`
- `RAG_pipeline/backend/tests/test_api/test_runtime_insight_endpoints.py` (new)
- `RAG_pipeline/.agent/system/api-reference.md`

Possible only if needed:

- `RAG_pipeline/backend/src/runtime/models.py` if shared enums/input models belong
  there more cleanly than in `runtime/insights/models.py`.

Avoid unless a real blocker appears:

- `RAG_pipeline/backend/src/db/models/runtime_insights.py`
- `RAG_pipeline/backend/alembic/versions/032_add_runtime_insights.py`
- `RAG_pipeline/backend/src/auth/`
- `RAG_pipeline/backend/src/rbac/`
- `RAG_pipeline/backend/src/retrieval/`

## Architecture Contracts

- All insight reads are project-scoped at the repository/query level, not
  filtered after rows are loaded.
- Route handlers derive scope from `CurrentUser` and `get_user_project_ids()`.
  Scope never comes from query params, path params, request body, or future LLM
  tool arguments.
- Optional `project_id` query params narrow scope via the existing
  `_intersect_scope()` helper only.
- `ProjectScope` semantics stay identical to the held runtime store:
  `None` means unrestricted, `[]` means deny all, `[ids]` means restricted.
- Point lookups by `execution_id` or `incident_id` return `404` for unknown and
  out-of-scope records, with the same response shape so existence is not leaked.
- List endpoints return empty lists for deny-all scope or out-of-scope
  `project_id` filters.
- Endpoint payloads are bounded and redacted. Evidence may include bounded
  excerpts already written by Slice 1; do not expose arbitrary raw log windows
  through these new endpoints.
- Timeline endpoint may include raw log messages only in the same bounded way as
  the existing log endpoint, and should include enriched fields separately so UI
  and later tools can avoid reparsing text.
- No LLM calls, no vector search, no runtime write actions, and no sync worker
  behavior in this slice.

## Implementation Steps

1. Add insight domain/API models in `runtime/insights/models.py`.
   - `RuntimeExecutionSummaryInsight`
   - `RuntimeIncidentInsight`
   - `RuntimeIncidentEvidence`
   - `RuntimeTimelineEvent`
   - list/filter input models if useful, e.g. `ListRuntimeIncidentsInput` and
     `GetRuntimeTimelineInput`
   - Keep types strict and Pydantic v2-compatible; prefer concrete list element
     types over untyped JSON where response shape is known.

2. Add `RuntimeInsightRepository`.
   - Constructor accepts `AsyncSession`.
   - Reuse local `_denies_all()` and `_apply_scope()` helpers, or factor them only
     if duplication with `RuntimeStoreRepository` becomes meaningful.
   - Implement:
     - `get_execution_summary(scope, execution_id)`
     - `get_execution_timeline(scope, execution_id, min_severity=None, limit=200)`
     - `list_incidents(scope, project_id=None, since=None, until=None, limit=50)`
     - `get_incident(scope, incident_id)`
     - `get_incident_evidence(scope, incident_id, limit=20)`
   - Join raw execution rows where summary/incident fields need job, state,
     start/end, duration, or project name.
   - Join enriched rows into timeline events by `(project_id, log_event_id)`.
   - Use an existence gate for timeline/evidence point reads so an in-scope record
     with no matching rows returns empty `200`, while unknown/out-of-scope returns
     `404`.

3. Extend `src/api/routes/runtime.py`.
   - Keep the existing router and `_intersect_scope()` helper.
   - Add response envelopes:
     - `{ "summary": ... }`
     - `{ "events": [...], "total": n, "execution_id": "..." }`
     - `{ "incidents": [...], "total": n }`
     - `{ "incident": ... }`
     - `{ "incident_id": "...", "evidence": [...], "total": n }`
   - Add endpoints:
     - `GET /api/v1/runtime/executions/{execution_id}/summary`
     - `GET /api/v1/runtime/executions/{execution_id}/timeline`
     - `GET /api/v1/runtime/incidents`
     - `GET /api/v1/runtime/incidents/{incident_id}`
     - `GET /api/v1/runtime/incidents/{incident_id}/evidence`
   - Use FastAPI `Query` constraints for limits:
     - incidents limit: `1..200`, default `50`
     - evidence limit: `1..50`, default `20`
     - timeline limit: `1..1000`, default `200`
   - Reuse `RuntimeLogSeverity` for timeline `min_severity`.
   - Map `RuntimeRecordNotFoundError` to `HTTPException(404, ...)`.

4. Add route tests in `test_runtime_insight_endpoints.py`.
   - Seed `esbmain` with `seed_rich_runtime_data(..., build_insights=True)`.
   - Seed another project with rich or mock data and build insights where needed.
   - Patch `src.api.routes.runtime.get_user_project_ids` exactly like
     `test_runtime_endpoints.py`.
   - Assert in-scope summary includes seeded job, failed component,
     `ORA-12170`, `SQLRecoverableException`, warning/error counts, and incident
     reference.
   - Assert incident list is scoped and bounded.
   - Assert incident detail returns likely/root-cause candidate, confidence,
     next checks, execution id, signature hash, and project id.
   - Assert evidence returns bounded excerpts with roles plus joined timestamp and
     severity.
   - Assert timeline returns chronological events with enriched fields for the
     Oracle timeout/error lines.
   - Assert optional `project_id` cannot widen scope.
   - Assert empty scope returns empty lists for list endpoints.
   - Assert cross-project or unknown execution/incident point lookups return
     `404`.
   - Assert in-scope timeline/evidence filters that match no rows return empty
     `200` only after the parent execution/incident exists in scope.
   - Assert unauthenticated requests still return `401`.

5. Update API documentation.
   - Extend `RAG_pipeline/.agent/system/api-reference.md` under Runtime with the
     new smart-log endpoints, query params, response envelopes, and RBAC semantics.
   - Mention that smart-log API reads from derived Postgres insight tables plus
     scoped joins to raw runtime rows; logs are not vectorized into Qdrant.

## Verification Strategy

Run focused backend validation for this slice:

```bash
cd RAG_pipeline/backend
python3 -m py_compile src/runtime/insights/models.py src/runtime/insights/repository.py src/api/routes/runtime.py
uv run pytest tests/test_api/test_runtime_insight_endpoints.py tests/test_api/test_runtime_endpoints.py tests/test_runtime/test_insight_builder.py tests/test_runtime/test_seed.py
```

Because this is a critical Auth/RBAC API surface, run `/inspect` after build and
before `/validate` or commit.

## Out of Scope

- High-level runtime intelligence tools for chat.
- Runtime tool registry/provider changes.
- Conversational RAG answer formatting.
- Frontend dashboard or BFF route changes.
- Similar-failure endpoint/tool behavior.
- Alerting/email delivery.
- Real Talend TMC sync worker.
- LLM-based enrichment or summarization.
- Schema changes unless implementation proves Slice 1 fields cannot support the
  API contract through joins.
