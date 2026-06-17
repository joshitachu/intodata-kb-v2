---
feature: Smart Runtime Log Intelligence
pipeline_step: 3_slices
total_slices: 5
current_slice: 0
---

## Slice 1: Deterministic Enrichment + Insight Store

**Delivers**: Raw seeded runtime logs can be converted into project-scoped enriched events, execution summaries, and incident records without using the LLM or dumping raw logs into context.

**Expected files**:
- `RAG_pipeline/backend/alembic/versions/032_add_runtime_insights.py` (new) — additive tables for enriched events, execution summaries, incidents, error signatures, and evidence references.
- `RAG_pipeline/backend/src/db/models/runtime_insights.py` (new) — SQLAlchemy models for the derived insight tables, all carrying `project_id`.
- `RAG_pipeline/backend/src/db/models/__init__.py` — export new models.
- `RAG_pipeline/backend/src/runtime/insights/__init__.py` (new)
- `RAG_pipeline/backend/src/runtime/insights/parser.py` (new) — deterministic parser for component names, error codes, exception classes, retry attempts, durations/timeouts, and safe no-match behavior.
- `RAG_pipeline/backend/src/runtime/insights/builder.py` (new) — builds summaries/incidents/evidence from raw `runtime_log_events`.
- `RAG_pipeline/backend/src/runtime/store/repository.py` — add read helpers needed by the builder, or keep raw reads reused by the builder.
- `RAG_pipeline/backend/src/runtime/store/seed.py` — optional developer flag/function to build insights after seeding rich logs.
- `RAG_pipeline/backend/tests/test_runtime/test_insight_parser.py` (new) — parser extraction and no-match tests.
- `RAG_pipeline/backend/tests/test_runtime/test_insight_builder.py` (new) — seeded rich Talend failure produces expected summary/incident/evidence.

**Critical area**: no — additive runtime schema/service work. It is security-sensitive because every derived row is project-scoped, but no `src/auth/` or `src/rbac/` changes are expected.

**Depends on**: none

---

## Slice 2: Scoped Insight Repository + REST API

**Delivers**: Authenticated clients can retrieve execution summaries, incidents, timelines, and evidence through bounded project-scoped API endpoints.

**Expected files**:
- `RAG_pipeline/backend/src/runtime/insights/repository.py` (new) — project-scoped queries for execution summary, incident detail, evidence, timeline, and incident lists.
- `RAG_pipeline/backend/src/runtime/insights/models.py` (new) or `RAG_pipeline/backend/src/runtime/models.py` — Pydantic response/domain models for summaries, incidents, evidence, timelines.
- `RAG_pipeline/backend/src/api/routes/runtime.py` — add read-only insight endpoints, likely:
  - `GET /api/v1/runtime/executions/{execution_id}/summary`
  - `GET /api/v1/runtime/incidents`
  - `GET /api/v1/runtime/incidents/{incident_id}`
  - `GET /api/v1/runtime/incidents/{incident_id}/evidence`
- `RAG_pipeline/backend/tests/test_api/test_runtime_insight_endpoints.py` (new) — happy paths, empty states, 404 for cross-project guessed ids, and project-scope narrowing behavior.

**Critical area**: yes — Auth/RBAC project-scope enforcement on new runtime API surface. Even if `src/auth/` and `src/rbac/` are not edited, `/inspect` is required because cross-project log leakage is the primary risk.

**Depends on**: Slice 1

---

## Slice 3: Runtime Intelligence Tools + Chat Answer Discipline

**Delivers**: Chat can answer "why did this job fail?" from bounded incident/summary data, with evidence and uncertainty separated from likely cause.

**Expected files**:
- `RAG_pipeline/backend/src/runtime/registry.py` — add high-level tool definitions:
  - `analyze_execution_failure`
  - `get_execution_timeline`
  - `get_runtime_incident`
- `RAG_pipeline/backend/src/runtime/client.py` — keep tool execution bounded/redacted for new tool result payloads.
- `RAG_pipeline/backend/src/runtime/providers/stored.py` — implement high-level tools from the insight repository.
- `RAG_pipeline/backend/src/runtime/providers/__init__.py` — extend provider protocol if needed.
- `RAG_pipeline/backend/src/generation/agent_loop.py` — update runtime guidance to prefer insight tools for failure-analysis questions and answer in Finding/Evidence/Likely Cause/Next Checks format.
- `RAG_pipeline/backend/src/generation/conversational_rag.py` — preserve existing `RequestScope` threading into runtime tools; no separate scope source.
- `RAG_pipeline/backend/tests/test_runtime/test_client.py` — new tool validation, redaction, bounds.
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_insights.py` (new) — chat/tool-loop tests for seeded failure answer, bounded evidence, uncertainty language, and cross-project isolation.

**Critical area**: yes — chat/runtime tool behavior with Auth/RBAC project-scope enforcement. `/inspect` required because LLM tool calls must not widen runtime access and wrong answers are user-facing.

**Depends on**: Slice 2

---

## Slice 4: Smart Logs Dashboard Panels

**Delivers**: `/admin/logs` shows smart failure insight beside raw logs: summary, failed component, likely cause, evidence lines, timeline, and clear empty/error states.

**Expected files**:
- `RAG_pipeline/frontend/types/runtime.ts` — add Zod schemas/types for execution summaries, incidents, evidence, and timelines.
- `RAG_pipeline/frontend/hooks/useRuntimeLogs.ts` or `RAG_pipeline/frontend/hooks/useRuntimeInsights.ts` (new) — React Query hooks for insight endpoints.
- `RAG_pipeline/frontend/app/api/runtime/executions/[id]/summary/route.ts` (new)
- `RAG_pipeline/frontend/app/api/runtime/incidents/route.ts` (new)
- `RAG_pipeline/frontend/app/api/runtime/incidents/[id]/route.ts` (new)
- `RAG_pipeline/frontend/app/api/runtime/incidents/[id]/evidence/route.ts` (new)
- `RAG_pipeline/frontend/app/admin/logs/page.tsx` — add smart summary, incident, timeline, and evidence panels while keeping raw log drilldown.
- `RAG_pipeline/frontend/app/admin/logs/page.test.tsx` or existing frontend test location — loading/error/empty and seeded-data rendering tests if local patterns support page tests.

**Critical area**: no — frontend/BFF work; RBAC is enforced by Slice 2 API endpoints.

**Depends on**: Slice 2

---

## Slice 5: Similar Failures + Alerting-Ready Metadata

**Delivers**: Users can see whether a failure is recurring within their project, and incident records carry enough metadata for a later alert/email slice without implementing production alert delivery.

**Expected files**:
- `RAG_pipeline/backend/src/runtime/insights/builder.py` — compute/maintain normalized error signatures and recurrence metadata.
- `RAG_pipeline/backend/src/runtime/insights/repository.py` — scoped similar-failure lookup by execution id, incident id, or signature.
- `RAG_pipeline/backend/src/api/routes/runtime.py` — add `GET /api/v1/runtime/incidents/{incident_id}/similar` or equivalent endpoint.
- `RAG_pipeline/backend/src/runtime/registry.py` — add `find_similar_failures` tool definition.
- `RAG_pipeline/backend/src/runtime/providers/stored.py` — implement `find_similar_failures`.
- `RAG_pipeline/backend/src/generation/agent_loop.py` — guidance for "has this happened before?" questions.
- `RAG_pipeline/frontend/types/runtime.ts` — similar-failure response schema.
- `RAG_pipeline/frontend/hooks/useRuntimeLogs.ts` or `RAG_pipeline/frontend/hooks/useRuntimeInsights.ts` — hook for similar failures.
- `RAG_pipeline/frontend/app/admin/logs/page.tsx` — show similar-failure count/list when available.
- `RAG_pipeline/backend/tests/test_runtime/test_similar_failures.py` (new) — same-project recurrence and cross-project non-leak tests.
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag_runtime_insights.py` — chat answers "has this happened before?" using scoped similar failures.

**Critical area**: yes — Auth/RBAC project-scope enforcement for recurrence/signature queries. Similarity is a leakage risk because matching signatures could reveal another project's incident existence. `/inspect` required.

**Depends on**: Slice 3 for chat tool integration; Slice 4 only for dashboard display of similar-failure UI.
