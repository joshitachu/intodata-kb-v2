---
feature: Smart Runtime Log Intelligence
slice: Slice 5 - Similar Failures + Alerting-Ready Metadata
sub_project: rag_pipeline
critical_area: yes - runtime API/tool recurrence lookups must enforce authenticated project scope; signature matching can leak another project's incident existence if scoped incorrectly
verdict: PASS
approved_at: 2026-06-17
---

## Problem Statement

Slices 1-4 let the system parse runtime logs, expose smart insight APIs, answer
failure questions in chat, and show insight panels in `/admin/logs`. Users can
now understand one failed execution, but they still cannot answer the next
operational question: "Has this happened before?"

The insight store already has `runtime_error_signatures`, and incidents already
carry `signature_hash`, but recurrence is not exposed through a scoped
repository/API/tool/UI path. There is also a correctness issue to fix before
exposing it: `RuntimeErrorSignatureRow.occurrence_count` currently increments
when the same execution is rebuilt, so repeated insight builds or reseeding can
inflate recurrence counts.

Slice 5 should make recurrence useful and safe:

- same-project similar failures are visible in the dashboard;
- chat can answer "has this happened before?" from bounded recurrence data;
- recurrence metadata is stable enough for a later alert/email slice;
- no alert delivery, email sending, scheduler, or notification workflow is built
  in this slice.

## Proposed Solution

Use deterministic exact-signature matching, not fuzzy/LLM similarity. The
existing normalized signature remains the recurrence key, but it must become
idempotent and queryable:

1. Harden signature maintenance in the insight builder so `occurrence_count`,
   `first_seen_at`, and `last_seen_at` are recomputed from distinct incident
   rows for the same `(project_id, signature_hash)`, not incremented blindly.
2. Add a scoped repository method that starts from an in-scope incident (or, for
   chat/provider use, an in-scope execution that resolves to an incident), then
   returns other incidents in the same project with the same signature.
3. Add a read-only REST endpoint:
   `GET /api/v1/runtime/incidents/{incident_id}/similar`.
4. Add a high-level runtime tool:
   `find_similar_failures`, accepting `incident_id` or `execution_id` and a
   bounded limit. Do not accept raw `signature_hash` from the LLM/public API.
5. Extend chat guidance so recurrence questions use `find_similar_failures` and
   answer in project-scoped language.
6. Extend the Slice 4 frontend panel with a recurrence section and bounded list.

The response should include alerting-ready metadata, but no alert action:

- stable `alert_fingerprint` / `dedupe_key` derived from project + signature;
- signature summary;
- occurrence count and similar count;
- first/last seen timestamps;
- boolean `is_recurring`;
- bounded same-project similar incident rows.

## Affected Files

- `RAG_pipeline/backend/alembic/versions/033_add_runtime_incident_signature_index.py` (new, likely index-only)
- `RAG_pipeline/backend/src/db/models/runtime_insights.py`
- `RAG_pipeline/backend/src/runtime/insights/builder.py`
- `RAG_pipeline/backend/src/runtime/insights/models.py`
- `RAG_pipeline/backend/src/runtime/insights/repository.py`
- `RAG_pipeline/backend/src/runtime/models.py`
- `RAG_pipeline/backend/src/runtime/registry.py`
- `RAG_pipeline/backend/src/runtime/providers/__init__.py`
- `RAG_pipeline/backend/src/runtime/providers/stored.py`
- `RAG_pipeline/backend/src/generation/agent_loop.py`
- `RAG_pipeline/backend/src/api/routes/runtime.py`
- `RAG_pipeline/backend/tests/test_runtime/test_insight_builder.py`
- `RAG_pipeline/backend/tests/test_runtime/test_similar_failures.py` (new)
- `RAG_pipeline/backend/tests/test_api/test_runtime_insight_endpoints.py`
- `RAG_pipeline/backend/tests/test_runtime/test_registry.py`
- `RAG_pipeline/backend/tests/test_runtime/test_client.py`
- `RAG_pipeline/backend/tests/test_runtime/test_stored_end_to_end.py`
- `RAG_pipeline/backend/tests/test_generation/test_agent_loop.py`
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_insights.py`
- `RAG_pipeline/frontend/types/runtime.ts`
- `RAG_pipeline/frontend/types/index.ts`
- `RAG_pipeline/frontend/hooks/useRuntimeLogs.ts`
- `RAG_pipeline/frontend/app/api/runtime/incidents/[id]/similar/route.ts` (new)
- `RAG_pipeline/frontend/components/runtime/RuntimeInsightPanel.tsx`
- `RAG_pipeline/frontend/tests/hooks/useRuntimeLogs.test.ts`
- `RAG_pipeline/frontend/tests/components/RuntimeInsightPanel.test.tsx`
- `RAG_pipeline/.agent/system/api-reference.md`

Avoid unless a real blocker appears:

- `RAG_pipeline/backend/src/auth/`
- `RAG_pipeline/backend/src/rbac/`
- `RAG_pipeline/backend/src/retrieval/`
- email/notification services
- new alert delivery tables

## Architecture Contracts

- Similar-failure matching is read-only.
- Scope comes only from authenticated backend code:
  - REST: `CurrentUser -> get_user_project_ids()`;
  - chat: `RequestScope -> project_scope -> RuntimeProviderContext`.
- Public REST and runtime tools must not accept arbitrary `signature_hash`.
  Signature lookup begins from an in-scope incident or execution.
- Unknown and out-of-scope incident/execution IDs return the same not-found
  behavior, with no existence leak.
- List results must include only incidents whose `project_id` is inside the
  authenticated scope, even when another project has the same signature hash.
- `runtime_error_signatures.occurrence_count` must represent distinct incident
  rows, not build attempts.
- Rebuilding insights for the same execution must not change recurrence count.
- Same signature in two projects must produce two independent project-scoped
  recurrence groups.
- Similar result payloads are bounded and do not include raw log lines or
  evidence excerpts.
- Chat answer guidance must say "in this project / accessible runtime data" and
  must not imply global recurrence across all customers/projects.
- Frontend recurrence UI is informational only. No acknowledge, mute, subscribe,
  send email, retry, rerun, or resolve action.
- No vector search or Qdrant path is added for runtime logs.

## Implementation Steps

1. Add a signature-query index.
   - Add `Index("ix_runtime_incidents_project_signature_bad_at", "project_id",
     "signature_hash", "first_bad_at")` to `RuntimeIncidentRow`.
   - Add migration `033_add_runtime_incident_signature_index.py`.
   - Keep it additive/reversible; no new columns are expected.

2. Make signature metadata idempotent.
   - Replace blind `occurrence_count += 1` behavior in `_upsert_error_signature`.
   - Upsert/update the incident row first, then refresh the signature aggregate
     from distinct `RuntimeIncidentRow` rows matching `(project_id,
     signature_hash)`.
   - Set:
     - `occurrence_count = count(distinct incident_id)`
     - `first_seen_at = min(first_bad_at)`
     - `last_seen_at = max(first_bad_at)`
   - Keep `summary_text`, `error_code`, `exception_class`, and `component_name`
     deterministic from the current parsed summary.
   - Avoid the "empty hash groups all unknown failures" trap: when diagnostic
     parts are missing, include a stable fallback such as `job_name` plus a
     conservative unknown marker so unrelated unknown failures do not all collapse
     into one signature.

3. Add recurrence models.
   - In `runtime/insights/models.py`, add:
     - `RuntimeSimilarIncident`
     - `RuntimeAlertReadyMetadata`
     - `RuntimeSimilarFailures`
   - Suggested shape:
     - `incident_id`, `execution_id`, `job_name`, `state`, `first_bad_at`,
       `symptom_summary`, `root_cause_candidate`, `confidence`
     - `signature_hash`, `signature_summary`, `occurrence_count`,
       `similar_count`, `first_seen_at`, `last_seen_at`, `is_recurring`
     - `alert_fingerprint`, `dedupe_key`, `alert_title`, `alert_reason`
     - `similar_incidents: list[RuntimeSimilarIncident]`

4. Add scoped repository reads.
   - Add `RuntimeInsightRepository.get_similar_failures_for_incident(scope,
     incident_id, limit=10)`.
   - Add provider-facing helper `get_similar_failures_for_execution(scope,
     execution_id, limit=10)` or resolve execution through `get_execution_summary`
     and reuse the incident method.
   - Behavior:
     - deny-all scope -> not found for point lookups;
     - parent incident/execution not in scope -> not found;
     - no signature -> response with `occurrence_count=1`, `is_recurring=false`,
       empty similar list;
     - signature exists -> query same project and same signature, exclude the
       current incident from `similar_incidents`, order newest first, limit
       bounded;
     - join execution metadata via the established two-query merge pattern.

5. Add the REST endpoint.
   - In `api/routes/runtime.py`, add:
     `GET /api/v1/runtime/incidents/{incident_id}/similar`.
   - Query params:
     - `limit`: default `10`, min `1`, max `50`.
   - Response envelope:
     `{ "incident_id": "...", "recurrence": ... }` or a similarly explicit
     typed envelope.
   - Map `RuntimeRecordNotFoundError` to `404` just like incident detail/evidence.
   - Update API reference docs with RBAC semantics and response shape.

6. Add the runtime tool.
   - In `runtime/models.py`, add `FindSimilarFailuresInput`:
     - `incident_id: str | None = None`
     - `execution_id: str | None = None`
     - `limit: int = Field(default=10, ge=1, le=50)`
     - validator: exactly one of `incident_id` or `execution_id` is required.
   - In `runtime/registry.py`, register `find_similar_failures`.
   - Description should steer:
     - use this for "has this happened before?", "is this recurring?", "again?";
     - call `analyze_execution_failure` first if only a job name/latest failure is
       known.
   - In provider protocol and `StoredRuntimeProvider`, implement the method using
     `RuntimeInsightRepository`.
   - Unsupported providers continue to return structured `PROVIDER_ERROR` via the
     existing client method guard.

7. Update chat guidance and memory summary.
   - Add `find_similar_failures` to `_RUNTIME_TOOL_GUIDE`.
   - Recurrence answer discipline:
     - state exact count in accessible project data;
     - list at most a few prior executions/timestamps;
     - say "No other same-signature failures found" when only current occurrence
       exists;
     - avoid global wording like "never happened before".
   - Extend `build_runtime_tool_summary()` so similar-failure results store only
     references:
     - current `incident_id` / `execution_id`;
     - short recurrence headline/count;
     - no similar incident list, no evidence, no log text.

8. Extend frontend/BFF.
   - Add BFF route:
     `frontend/app/api/runtime/incidents/[id]/similar/route.ts`.
   - Add Zod schemas/types:
     - `RuntimeSimilarIncidentSchema`
     - `RuntimeAlertReadyMetadataSchema`
     - `RuntimeSimilarFailuresSchema`
     - `IncidentSimilarResponseSchema`
   - Add hook:
     `useRuntimeSimilarFailures(incidentId, { limit }, enabled?)`.
   - In `RuntimeInsightPanel`, fetch recurrence when an incident ID exists and
     render a compact section:
     - recurring badge/count;
     - first/last seen;
     - alert fingerprint/dedupe key in muted monospace only if useful for ops;
     - bounded list of similar executions;
     - empty state: "No other matching failures found in this project."
   - Keep raw logs and existing insight sections unchanged.

9. Add tests.
   - Builder tests:
     - rebuilding same execution does not increase occurrence count;
     - two distinct failed executions with the same signature in one project
       produce occurrence count 2;
     - same signature in another project is counted separately.
   - Repository/API tests:
     - in-scope similar endpoint returns same-project prior incident(s);
     - current incident is excluded from `similar_incidents` but counted in
       `occurrence_count`;
     - cross-project/unknown incident returns `404`;
     - empty scope returns `404`;
     - limit is enforced and oversized limit returns `422`;
     - same signature in another project is not visible.
   - Runtime tool tests:
     - registry lists eight tools and validates `FindSimilarFailuresInput`;
     - stored provider returns recurrence by incident ID and by execution ID;
     - cross-project execution/incident returns provider error;
     - unsupported mock/talend provider returns structured provider error.
   - Chat tests:
     - mocked LLM uses `analyze_execution_failure` then `find_similar_failures`
       for "has this happened before?";
     - authenticated project scope is forwarded;
     - final answer uses project-scoped language and count.
   - Frontend tests:
     - hook builds `/api/runtime/incidents/{id}/similar?limit=...`;
     - hook does not fetch with null incident ID;
     - panel renders recurring count/list;
     - panel renders no-similar empty state.

## Verification Strategy

Run targeted backend checks:

```bash
cd RAG_pipeline/backend
python3 -m py_compile src/db/models/runtime_insights.py src/runtime/insights/builder.py src/runtime/insights/models.py src/runtime/insights/repository.py src/runtime/models.py src/runtime/registry.py src/runtime/providers/__init__.py src/runtime/providers/stored.py src/generation/agent_loop.py src/api/routes/runtime.py
uv run pytest tests/test_runtime/test_insight_builder.py tests/test_runtime/test_similar_failures.py tests/test_api/test_runtime_insight_endpoints.py tests/test_runtime/test_registry.py tests/test_runtime/test_client.py tests/test_runtime/test_stored_end_to_end.py tests/test_generation/test_agent_loop.py tests/test_generation/test_conversational_rag_runtime_insights.py
```

Run targeted frontend checks:

```bash
cd RAG_pipeline/frontend
npx tsc --noEmit
npm test -- tests/hooks/useRuntimeLogs.test.ts tests/components/RuntimeInsightPanel.test.tsx
```

Before commit, run `/inspect` because this slice is critical. Then run broader
validation:

```bash
cd RAG_pipeline/backend && uv run pytest
cd RAG_pipeline/frontend && npm test
```

## Out of Scope

- Production alert/email delivery.
- Alert scheduler, subscriptions, mute/acknowledge/resolve workflows.
- Writing notifications to users.
- Fuzzy/semantic similarity or vectorizing log summaries.
- Cross-project or cross-tenant recurrence matching.
- Real Talend TMC sync worker.
- Runtime write actions such as rerun/retry.
- Changing auth/RBAC dependencies.
