---
feature: Held Runtime-Data Architecture (Model 2 skeleton, source-agnostic)
pipeline_step: 2_prd
---

## Executive Summary

Introduce a source-agnostic store that **holds** runtime execution data (executions,
failures, logs) in Postgres, behind the existing generic `RuntimeProvider` contract.
A new DB-backed `stored` provider answers chat questions from this held store instead
of calling a live source per question. The real data source and sync worker are
deferred; an in-process write-port plus a mock-fixture seed make the chat path work
end-to-end today. "Done" means chat answers from project-scoped held data with a
truthful "as of" freshness, with no change to the LLM-facing tool contract, SSE, or
frontend.

## Problem

The runtime path supports only live pass-through providers (`mock` fixtures and the
`talend_tmc` adapter that calls the source on every question). Nothing holds runtime
data, so each chat turn pays the source's latency/rate-limit/availability cost, the
data cannot be queried or trended, the system is coupled to a still-undecided source
(confirmed not to be MCP), and there is no freshness concept because answers are
assumed real-time.

## Goals

1. Persist runtime executions, failures, and log events in Postgres via an additive
   migration, each row project-scoped and timestamped with `ingested_at`.
2. Track store freshness with a per-source `last_synced_at` watermark.
3. Provide a repository layer with RBAC/project-filtered reads and an in-process
   write-port (upsert) for future ingestion.
4. Add a `stored` runtime provider implementing the existing 4-method protocol,
   registered alongside `mock`/`talend_tmc`, returning the normalized runtime models.
5. Enforce project-scoped access on every held-data read, sourced from the
   authenticated request principal — never from LLM-supplied tool arguments.
6. Seed the store from existing mock fixtures through the write-port so
   `RUNTIME_PROVIDER_KEY=stored` answers chat end-to-end.
7. Keep the change provider-agnostic: no Talend-specific fields or names in the store,
   repository, or provider.

## Non-Goals

- The real data source and the sync worker (only the in-process write-port seam).
- EP-004 Slice 6 real-data Talend PoC (paused).
- Any HTTP/external ingestion endpoint (no new API route surface).
- Changes to the LLM-facing tool vocabulary, chat prompts, SSE event contract, or
  frontend runtime rendering.
- Vectorizing runtime data into Qdrant.
- Retention/archival, backfill, or high-volume performance tuning.
- Changes to the RBAC framework itself (reuse `get_user_project_ids`).

## User Stories

1. As an **operations user**, I want chat to answer "what failed in the last 24h?"
   from held data, so that answers stay fast and work even if the source is down.
2. As a **platform engineer**, I want a future sync worker to write runtime data
   through one in-process port, so that the data source is pluggable without touching
   chat.
3. As a **security owner**, I want every held-data read filtered by the user's
   allowed projects, so that one client/project can never see another's runtime data.
4. As a **chat user**, I want answers to state how current the data is ("as of HH:MM"),
   so that I don't treat stale data as live.
5. As a **maintainer**, I want the store and provider to use the generic runtime
   models only, so that swapping or adding sources later requires no schema rework.

## Functional Requirements

1. New Postgres tables (additive): `runtime_executions`, `runtime_failures`,
   `runtime_log_events`, and `runtime_sync_state` (watermark). Provided via an Alembic
   migration; no changes to existing tables.
2. Every data row carries `project_id` (indexed) and `ingested_at`; `runtime_sync_state`
   holds `last_synced_at` (and source key) for freshness.
3. A repository exposes read methods mirroring the four provider operations
   (list executions, get one, detect failures, get logs), each accepting an explicit
   allowed-project scope and filtering by it.
4. The repository read scope contract matches `get_user_project_ids`: `None` = no
   restriction (superadmin/view-all); a list = restrict to those `project_id`s; an
   empty list = no access (returns nothing).
5. The repository exposes an in-process write-port: idempotent upsert of executions,
   failures, and log events, and a watermark update — the seam a future worker calls.
6. A `stored` runtime provider implements `list_executions`,
   `get_execution_details`, `detect_failures`, `get_execution_logs` by delegating to
   the repository, returning normalized `RuntimeExecution`/`RuntimeFailure`/
   `RuntimeLogEvent`. It is registered in `_PROVIDER_MODULE_PATHS`.
7. The authenticated request's allowed-project scope is threaded to
   `execute_runtime_tool` and into the provider call, out-of-band from the
   LLM-supplied tool `arguments`. Tool arguments must not be able to widen scope.
8. Unknown `execution_id` or empty result sets are handled as today (provider error
   for not-found details/logs; empty lists for list/detect).
9. A seed routine loads the existing mock fixtures through the write-port (assigning a
   demo `project_id`) and sets the watermark; running it twice is idempotent.
10. Freshness (`last_synced_at`, per-row `ingested_at`) is retrievable through the
    repository/provider so a later slice can surface "as of" in chat.

## Non-Functional Requirements

- **Security**: 100% of held-data reads pass through the project-scope filter; a test
  proves a user restricted to project A cannot retrieve project B rows. No RBAC
  decision is ever derived from LLM tool arguments.
- **Compatibility**: existing runtime, tool-loop, and chat tests remain green;
  `mock` and `talend_tmc` behavior is unchanged (scope threading is additive/optional
  for them).
- **Output safety**: provider output continues to pass through `runtime/client.py`
  sanitization and size bounding; no new unbounded payload path.
- **Performance**: held reads are indexed Postgres queries (project_id, started_at);
  target well under the existing `runtime_tool_timeout` (10s) for seeded volumes.
- **Migration safety**: additive-only; reversible `downgrade`.
- **Code conventions**: Python 3.11+, Pydantic v2, SQLAlchemy `Mapped`/`mapped_column`,
  Alembic, hatchling; type hints on every signature; async I/O.

## Architecture Impact

- **DB layer (new)**: four SQLAlchemy models + one Alembic migration under
  `RAG_pipeline/backend/src/db/models/` and `alembic/versions/`.
- **Repository (new)**: `src/runtime/store/` (repository + write-port) using the
  async session, following the `get_user_project_ids` scope contract.
- **Provider (new)**: `src/runtime/providers/stored.py`, registered in
  `src/runtime/client.py` `_PROVIDER_MODULE_PATHS` (key `stored`). Mirrors the
  `mock`/`talend_tmc` module-singleton pattern.
- **Runtime boundary (refinement — see Open Questions)**: `execute_runtime_tool` and
  the tool loop (`generation/agent_loop.py`, `generation/conversational_rag.py`) must
  thread an allowed-project scope from the chat route principal into the provider.
  This is additive and does NOT change the LLM tool vocabulary, the SSE event
  contract, or the frontend. It does touch backend tool-loop internals, which the
  grill listed as out of scope — this PRD flags the deviation as security-required.
- **Config**: `RUNTIME_PROVIDER_KEY=stored` selects the new provider; optional seed
  toggle. DB connection reuses existing settings.
- **Not touched**: harness pipeline, MCP API routes, RAG retrieval chain, SSE
  contract, frontend.

## Design Decision — Log/Runtime Retrieval Strategy (structured, not vectorized)

Runtime logs and executions are retrieved via **structured, parameterized tool calls
over Postgres — not by embedding them into Qdrant.** This is a deliberate, evidence-
backed decision and the reason "vectorizing runtime data" is a non-goal.

Rationale (industry practice, see Sources):
- Log volume is too large to embed wholesale, and embedding is costly.
- Log questions hinge on **exact tokens** (error codes, execution IDs, timestamps);
  pure vector search misses exact strings.
- The natural filters — time window, severity, service, execution_id — are
  structured predicates (`WHERE`), not semantic similarity.
- The dominant production pattern for "ask questions about logs" is
  text-to-query / tool-calling over a structured store (text-to-SQL, or LLMs emitting
  LogQL), not a vector index of logs.

Why predefined tools rather than free text-to-SQL: our four generic tools are
parameterized, bounded, and **RBAC-filtered server-side**. Raw text-to-SQL needs heavy
guardrails and self-healing repair loops and is an injection / scope-bypass risk. The
fixed-tool approach is the safer school and keeps project-scope enforcement intact.

UI consequence: runtime logs are shown as **tool results in the chat flow, not as RAG
document "sources."** They reuse the EP-004 runtime-progress surface; any drill-down is
a compact, sanitized, bounded structured view (timestamp · severity · message), never a
raw dump and never routed through the document/citation system. Building that panel is
a future frontend slice, out of scope here.

The one future exception that could justify a narrow embedding index: semantic
"find similar past failures" / incident clustering over **failure-summary text only**
(not all logs). Not in scope; flagged so it is a conscious future decision rather than
an accidental one. If that feature is never built, runtime data stays Qdrant-free
permanently.

Sources: AI21 "RAG for Structured Data"; "Chatting with Logs: Finetuning LLMs for
LogQL" (arXiv 2412.03612); Google Cloud "Architectural Patterns for Text-to-SQL";
Vanna (text-to-SQL); MLflow "Agent Observability Tools".

## Implementation Phases

- **Phase 1 — Store + repository + migration**: models, Alembic migration,
  repository reads (project-scoped) and write-port, freshness watermark. Validation:
  repository unit tests incl. RBAC filtering and idempotent upsert; migration
  up/down.
- **Phase 2 — `stored` provider + registry + scope threading**: provider delegates to
  the repository; register key `stored`; thread request project-scope through
  `execute_runtime_tool` and the tool loop. Validation: provider/registry tests,
  scope-enforcement test (project A user cannot read project B), existing runtime/chat
  tests green.
- **Phase 3 — Seed + end-to-end**: seed mock fixtures through the write-port; run
  chat against `RUNTIME_PROVIDER_KEY=stored`. Validation: seed idempotency test;
  manual/integration end-to-end chat returns seeded, project-filtered data; freshness
  retrievable.

## Risks and Open Questions

1. **OPEN — RBAC scope threading vs "no tool-loop change".** Enforcing project scope
   requires threading the request principal's allowed projects into
   `execute_runtime_tool`/the tool loop. Options: (A) explicit additive `project_scope`
   parameter through the loop (recommended — explicit, testable); (B) a request-scoped
   `contextvar` set in the chat route and read by the provider (no signature changes
   but implicit/magic). Decision needed at `/slices` or `/plan`. Recommended: A.
2. **Scope source integrity.** Tool `arguments` are LLM-controlled; project scope must
   come only from the authenticated request. Mitigated by FR-7 and a dedicated test.
3. **Project attribution of executions.** The real source may not map to projects; the
   seed assigns a demo project, and mapping is the future worker's responsibility.
4. **Schema evolution.** Real-source fields may differ; mitigated because the
   normalized models are the contract and changes stay local to store/provider/migration.
5. **Freshness truthfulness.** Chat must not imply live data; mitigated by explicit
   `last_synced_at`/`ingested_at`. Deep chat-UI surfacing may be a later slice.

## Acceptance Criteria

- [ ] Alembic migration creates `runtime_executions`, `runtime_failures`,
      `runtime_log_events`, `runtime_sync_state`; `downgrade` drops them; no existing
      table altered.
- [ ] Repository reads return normalized models and filter by allowed-project scope
      using the `get_user_project_ids` contract (None / list / empty).
- [ ] A test proves a user scoped to project A cannot retrieve project B runtime rows.
- [ ] In-process write-port performs idempotent upserts and updates the watermark.
- [ ] `stored` provider implements all four methods and resolves via
      `RUNTIME_PROVIDER_KEY=stored` through the registry.
- [ ] Project scope is threaded from the request to the provider; LLM tool arguments
      cannot widen it (covered by a test).
- [ ] Seed loads mock fixtures through the write-port; re-running is idempotent.
- [ ] End-to-end: with `RUNTIME_PROVIDER_KEY=stored`, chat answers "what failed in the
      last 24h?" from seeded held data, project-filtered.
- [ ] `last_synced_at` and per-row `ingested_at` are retrievable for "as of" use.
- [ ] No Talend-specific names/fields in store, repository, or provider.
- [ ] Existing backend tests (runtime, tool loop, chat) remain green; `mock`/
      `talend_tmc` unchanged.
- [ ] `/inspect` run and passed (critical area: Auth/RBAC).
