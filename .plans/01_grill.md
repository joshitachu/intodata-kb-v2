---
feature: Held Runtime-Data Architecture (Model 2 skeleton, source-agnostic)
pipeline_step: 1_grill
date: 2026-06-16
---

## Feature

Build the source-agnostic "held data" (Model 2) skeleton for runtime integration: a
Postgres store for runtime executions/failures/logs (+ migration), a repository
layer, a new DB-backed runtime provider implementing the existing 4-method
`RuntimeProvider` protocol (`list_executions`, `get_execution_details`,
`detect_failures`, `get_execution_logs`) registered alongside `mock`/`talend_tmc`,
and an in-process ingestion write-port where a future sync worker pushes data. The
real data source is deferred and out of scope; the store is seeded from the existing
mock fixtures (through the same write-port) so the chat path works end-to-end against
held data. Provider-agnostic: no changes to chat, the backend tool-use loop, SSE, or
frontend runtime rendering. Data freshness ("as of") is represented in the store.

Predecessor: EP-004 (runtime status, generic tool registry, mock provider, backend
tool-use loop, frontend progress, `talend_tmc` live adapter). EP-004 Slice 6
(real-data PoC) is PAUSED; its pipeline docs are archived in
`.plans/archive/ep004-runtime/`.

## Problem Statement

Today the runtime path supports only **live pass-through** providers: `mock`
(in-memory fixtures) and `talend_tmc` (calls the source on every chat question).
Nothing **holds** runtime data. Consequences:

- Every chat question pays the source's latency / rate-limit / availability cost on
  the critical path of a conversational turn; a source hiccup fails the answer.
- Runtime data cannot be queried, joined with RAG documents, or trended — each
  question is a one-shot call.
- The system is coupled to whatever source exists, and the long-term source is still
  undecided (confirmed: it will not be an MCP server).
- There is no freshness/"as of" concept, because answers are assumed real-time.

We want a store that holds runtime data with the source pluggable later.

## Definition of Done

- Postgres tables for runtime executions, failures, and log events (additive
  migration), each project-scoped (`project_id`) and carrying `ingested_at`, plus a
  store-level sync watermark (`last_synced_at`).
- A repository layer with: (a) RBAC/project-filtered read methods, and (b) an
  in-process write-port (upsert) interface for future ingestion.
- A new DB-backed runtime provider implementing the 4-method `RuntimeProvider`
  protocol, registered in `_PROVIDER_MODULE_PATHS` (e.g. key `stored`), returning the
  normalized `RuntimeExecution` / `RuntimeFailure` / `RuntimeLogEvent` models.
- A seed path that loads the existing mock fixtures **through the write-port**, so
  with `RUNTIME_PROVIDER_KEY=stored` the chat path answers from held data.
- Freshness surfaced in the store (watermark + per-record `ingested_at`), retrievable
  by the provider/repository; deep chat-UI wiring of "as of" may be minimal/deferred
  but the data must exist and be queryable.
- Tests: repository read/write, project/RBAC filtering, provider normalization +
  registry resolution, freshness/watermark behavior, seed idempotency. Existing
  runtime, tool-loop, and chat tests stay green.
- No changes to chat, the backend tool-use loop, SSE event contract, or frontend
  runtime rendering.

Verification: `uv run pytest` (backend) green incl. new tests; end-to-end chat against
`RUNTIME_PROVIDER_KEY=stored` returns the seeded data with correct project filtering;
`docker compose config` valid if compose touched.

## Out of Scope

- The real data source and the sync worker (deferred; "how data comes in is less
  important rn"). Only the in-process write-port seam is built.
- EP-004 Slice 6 real-data Talend PoC (paused).
- HTTP/external ingestion endpoint (chose in-process repository port — no new API
  route surface).
- Frontend changes, chat-prompt changes, SSE contract changes.
- Vectorizing runtime data into Qdrant (wrong store for time-series operational data).
- Retention/archival policy and high-volume backfill (future).
- Auth/RBAC framework changes — reuse the existing project-access filtering used for
  documents.

## Solution Rationale

Model 2 (sync-and-hold) was chosen over Model 1 (pull-on-demand) because this is a
chat product: per-question source calls put latency, rate limits, and availability on
the critical path, and cannot support querying/trends. A held store decouples chat
from the source, makes the data queryable, and absorbs the still-undecided source
behind a pluggable seam.

The existing `RuntimeProvider` Protocol + module registry (EP-004 slices 1-5) already
provide the swap seam, so adding a DB-backed provider needs **no** changes to chat,
the tool loop, SSE, or the frontend. Postgres is already in the stack and fits
structured, high-churn execution/time-series data; Qdrant is the wrong home.
Project-scoping matches the platform's RBAC-on-every-query rule and the document
access model. Building Model 1 first (EP-004) was not wasted: the `talend_tmc` adapter
becomes the future sync worker's fetch layer.

## Assumptions and Risk

- The generic 4-method contract and normalized models (`RuntimeExecution`,
  `RuntimeFailure`, `RuntimeLogEvent`) are sufficient to represent held data. Safe —
  they are the same models `mock`/`talend_tmc` already return.
- Executions can be attributed to a `project_id`. The real source may not map cleanly
  to projects yet; the seed assigns a demo project, and mapping is the future sync
  worker's responsibility. (User chose strict project-scoped over nullable.)
- Freshness = store watermark + per-record `ingested_at` is enough for now.
- An in-process write-port is enough; no external worker or network ingestion yet.
- Schema may need revision when the real source arrives. Mitigated: the normalized
  models are the contract, not raw source payloads, so changes stay local to the
  repository/provider/migration.

## Critical Areas Touched

- **Auth / RBAC — YES.** The new repository read methods must filter runtime data by
  the user's allowed projects, exactly as document queries do. This makes the change
  a critical area: **`/inspect` is mandatory** before commit, and the plan should
  carry `critical_area: yes`.
- New surface: Postgres schema + additive migration, repository layer, new runtime
  provider + registry entry, in-process write-port, seed path.
- NOT touched: harness pipeline phases, MCP API routes, RAG retrieval chain, chat /
  tool-use loop / SSE, frontend runtime rendering.

## Risk if Wrong

- **RBAC filter wrong → cross-project runtime data leakage** (confidentiality breach).
  Highest-severity risk; must be covered by explicit tests.
- Provider returns wrong/oversized data → blast radius limited: `runtime/client.py`
  still sanitizes, bounds, and structures errors at the boundary.
- Migration wrong → DB issues. Mitigated: additive tables only; no changes to existing
  tables.
- Freshness wrong → chat could imply data is current when it is stale. Mitigated by
  storing explicit `last_synced_at` + `ingested_at` so "as of" is truthful.
