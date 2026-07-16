# Runtime Logs — Redesign Plan (Requirements + Slices)

> Standalone planning doc (not the LFE pipeline). Captures the agreed scope,
> evidence, decisions, requirements, and vertical-slice breakdown for the
> Runtime Operations / Logs redesign. Design reference (approved mockup):
> https://claude.ai/code/artifact/0ab71ffc-5114-4744-8e91-a1d40e958ec4

Status: **implemented and validated locally.** Last updated 2026-07-15.

Implementation result:
- S1-S4 are enabled in the default product path.
- S5 is implemented behind `RUNTIME_SEMANTIC_ENABLED=false`; exact Postgres
  search remains authoritative when semantic recall is unavailable.
- Migration `060_runtime_logs_storage` was exercised against a restored backup
  through upgrade, downgrade, and re-upgrade. The live local database retains
  only the `cdc` mirror and keeps reversible archive tables.

---

## 1. Goal

Turn the runtime logs surface (`/admin/logs`) into a clean, readable operational
console that serves **two consumers**:

- **Humans** (developers / analysts) — read and explore TMC runtime logs to understand
  what ran, what failed, and why.
- **The Nexus agent** — search and cite these logs to answer questions, grounded in the
  logs *and* the documentation Nexus generates.

Secondary: legible enough at the summary layer that a non-operator stakeholder can
grasp health at a glance.

---

## 2. Context / current system

- Frontend page: `RAG_pipeline/frontend/app/admin/logs/page.tsx` (~1,286 lines, single file).
- Data hooks: `RAG_pipeline/frontend/hooks/useRuntimeLogs.ts` (~18 queries).
- Types: `RAG_pipeline/frontend/types/runtime.ts`.
- Backend routes: `RAG_pipeline/backend/src/api/routes/runtime.py` (prefix `/runtime`).
- Sync + store: `RAG_pipeline/backend/src/runtime/` (`sync/service.py`, `store/repository.py`,
  `providers/talend_tmc.py`); insights under `runtime/insights/`.
- DB models: `RAG_pipeline/backend/src/db/models/runtime_data.py`, `runtime_insights.py`.
- Agent path: gated tool loop in `RAG_pipeline/backend/src/generation/agent_loop.py`
  (`run_runtime_tool_loop`), tools via `runtime/registry.py`, reads the `stored` provider.

TMC = Talend Management Console. Entity model:
`Source → Environments/Workspaces → Engines`, `Jobs → Job Runs → Executions → Failures`,
`Plans → Plan Runs → Step Results`, plus an insights layer (error signatures, execution
summaries, incidents, evidence).

---

## 3. Root-cause findings (verified against live CDC data, 2026-07-15)

### 3.1 Duplication (the main visible complaint)
- One physical TMC is registered as **two sources / projects**: `cdc` and `IntodataStreams`,
  **byte-identical** (419/419 shared executions). Every table is mirrored ~2x; the default
  "All projects" view renders both copies.
  - Confirmed: `runtime_sources` has two rows for `talend_tmc` (projects `cdc`, `IntodataStreams`).
  - Fan-out ~2x across all tables: executions 843/422, job_runs 841/422, plan_runs 412/207,
    jobs 202/102, plans 63/32.
- Write side is **idempotent** (per-project unique keys, e.g. `uq_runtime_executions_project_exec`),
  so this is a **data-model / onboarding** issue, not a dedup bug.
- Contributing factors:
  - Same run stored in both `runtime_executions` and `runtime_job_runs` → "runs" and
    "executions" tabs show the same events under different labels.
  - List queries order by `started_at DESC` with **no tiebreaker** → nondeterministic order
    across refreshes reads as flicker/dupes.

### 3.2 Truncation / completeness
- Logs fetched **only for failed runs**, capped 200 lines each (`talend_tmc.py`); successful
  runs store none. Live data: **only 19 of 419 executions have any log lines**.
- `error_summary` truncated to 300 chars (ingest) / 240 chars (sync boundary).
- Log-event fingerprint dedup (`repository.py`) can collapse legitimately repeated lines.
- All list endpoints are `limit`-capped (50–500) with **no offset/cursor pagination**; the
  `total` field is "rows returned", not a real count — anything past the cap is unreachable.

### 3.3 Agent access
- A gated tool loop exists (`stored` provider, 18 tools) but:
  - `get_execution_logs` is **not** in the agent allowlist — the agent never sees raw log lines.
  - There is **no log-content search** (no Postgres full-text index, no vector index over
    `runtime_log_events.message`), so the agent cannot answer "search the logs for X".
  - Agent results are curated/bounded (≤300-char snippets, 12k-char tool-result cap) and the
    guide forbids quoting raw logs.

### 3.4 UI structure
- Tab bar only swaps the *bottom* section; four heavy panels render on every tab → switching
  tabs shows the same top-of-page, changed content is below the fold ("same thing / scroll forever").
- ~18 queries fire on mount regardless of active tab; refresh refetches all 18.
- Plans / plan-runs / jobs rows are not clickable (dead ends); detail appended at page bottom.

---

## 4. Locked decisions

| Decision | Choice |
|----------|--------|
| Duplication fix | **Collapse to one source.** `cdc` project **survives**; remove the `IntodataStreams` mirror; add an onboarding guard so one TMC cannot be registered twice. Keep read-side dedupe + stable sort as defense in depth. |
| Log capture | **Capture all runs** (not failures-only), raise caps. **90-day** raw-log retention; summaries/incidents retained longer. |
| Agent search | **Postgres full-text / trigram first** (`search_logs` tool). Semantic Qdrant layer over curated incident/error summaries is a later, optional add. |
| Raw logs to agent | Expose **bounded, credential-redacted** snippets — never full raw dumps. Redaction verified end-to-end before any agent exposure. |
| UI | Hub + drill-down routes, summary-first, drill-down drawer, shared "Ask these logs" path. Reskinned to real Nexus tokens (deep indigo `225 72% 30%`, slate neutrals, Inter, `0.5rem` radius, elevation shadows). |

---

## 5. Requirements

### Functional
- **FR1** A single physical TMC appears once (one estate, one project). No duplicate rows in any view.
- **FR2** Onboarding rejects registering the same TMC (by stable identity) more than once.
- **FR3** List views have stable, deterministic ordering and real pagination (offset or cursor) with an honest total.
- **FR4** Every row in every entity list is navigable to a detail view (executions, jobs, plans, plan-runs, incidents).
- **FR5** Log lines are captured for all runs (success + failure), within the retention window.
- **FR6** A `search_logs(question, filters)` capability lets the agent find relevant log content by keyword/error, project/RBAC-scoped.
- **FR7** The dashboard exposes an "Ask these logs" affordance that uses the same agent path and cites specific executions + generated docs.
- **FR8** Summary layer (KPIs + trend + "needs attention") is understandable without operator knowledge.

### Non-functional
- **NFR1** Credential redaction preserved on every path (ingest, storage, dashboard, agent). No raw secrets to LLM/logs/docs.
- **NFR2** RBAC/project scoping enforced server-side for both dashboard and agent (never from LLM arguments).
- **NFR3** UI matches Nexus design tokens; full light/dark; no horizontal body scroll; keyboard-accessible; reduced-motion respected.
- **NFR4** Per-request data volume bounded (pagination) even with full-run log capture.
- **NFR5** 90-day retention enforced by a scheduled prune; index sized for the retained window.

---

## 6. Vertical slices (each independently demoable)

### S1 — Dedupe + onboarding guard
- **Status:** complete.
- **Scope:** data migration collapsing the `IntodataStreams` mirror into `cdc` (repoint/merge
  dependent rows: failures, log events, insights, incidents); remove the redundant source.
  Add stable sort tiebreaker (`ORDER BY started_at DESC, id DESC`) to all list queries.
  Add read-side dedupe safety net. Add onboarding guard against double-registering one TMC.
- **Touches:** `runtime/store/repository.py`, `api/routes/runtime.py`, `api/routes/runtime_sources.py`,
  new Alembic migration.
- **Acceptance:** default view shows each execution once; counts halve to true totals
  (execs ~422, jobs ~102, plans ~32); re-registering the same TMC is rejected; migration reversible.
- **Notes:** critical area (API routes) — inspect before commit. Back up before the migration.

### S2 — Dashboard restructure (approved design)
- **Status:** complete.
- **Scope:** split the single page into hub + entity routes (`/admin/logs`, `/executions`,
  `/jobs`, `/plans`, `/incidents`, `/estate`); route-scoped queries (gate with `enabled`);
  clickable rows → drill-down drawer/detail; unified "run" concept (stop executions-vs-runs
  duplication); summary-first hub (KPIs, outcome trend, needs-attention). Apply Nexus tokens.
- **Touches:** `frontend/app/admin/logs/**`, `frontend/hooks/useRuntimeLogs.ts`,
  `frontend/components/runtime/**`, `frontend/types/runtime.ts`.
- **Acceptance:** switching views changes the whole screen; hub loads only its queries; every
  list row drills down; matches the approved mockup in both themes.

### S3 — Full log capture + pagination
- **Status:** complete. Retention pruning runs as part of every scheduled or
  manual source sync.
- **Scope:** capture log lines for all runs (lift failed-only + 200-line cap with a sane ceiling);
  add offset/cursor pagination + real `total` to list endpoints; fix fingerprint collapse
  (add ordinal/line-number); raise/store full error text + short summary; 90-day retention prune.
- **Touches:** `runtime/providers/talend_tmc.py`, `runtime/sync/service.py`,
  `runtime/store/repository.py`, `api/routes/runtime.py`, migration (retention/index).
- **Acceptance:** successful runs have logs; nothing is unreachable past a cap; repeated lines
  preserved; prune removes logs older than 90 days; redaction verified.

### S4 — Agent log search  *(critical area — /inspect required)*
- **Status:** complete. Exact duplicate snippets are collapsed for agent/UI
  evidence while their occurrence count is retained; raw rows remain intact.
- **Scope:** Postgres full-text/trigram index on `runtime_log_events.message`; `search_logs(question, filters)`
  tool returning bounded, credential-redacted snippets, project/RBAC-scoped; wire into the answer
  flow; expose in the dashboard "Ask these logs" panel.
- **Touches:** `generation/agent_loop.py`, `runtime/registry.py`, `runtime/providers/stored.py`,
  `runtime/store/repository.py`, migration (FTS/trigram index), `frontend` ask panel.
- **Acceptance:** agent answers a free-text log question with correct citations; results
  RBAC-scoped; no raw secrets surfaced; snippets bounded.

### S5 — Semantic layer (optional)
- **Status:** complete and opt-in. Only deterministic execution summaries,
  incidents, and error signatures are embedded; raw log lines are never sent to
  the embedding provider.
- **Scope:** embed curated incident/error-signature summaries into Qdrant for semantic recall,
  reusing the doc-RAG path; merge with FTS results.
- **Acceptance:** paraphrased/semantic log questions return relevant curated summaries.

---

## 7. Risks / open items
- **Migration risk:** collapsing the mirror must safely merge/repoint dependents and be reversible; back up first.
- **Credential exposure:** wider log capture + agent-visible snippets widen the leak surface —
  redaction must be verified end-to-end (technical control; validate data-handling/GDPR posture with compliance owner).
- **Storage/index growth:** full-run capture increases volume; retention prune + index sizing required.
- **Critical areas:** S1 (API routes) and S4 (retrieval/agent + RBAC) need extra review before commit.
- **Resolved:** the physical TMC identity is the normalized credential-free base
  URL (`tmc:<normalized-base-url>`). The provider contract owns this identity so
  future runtime connectors can define an equivalent stable key.

---

## 8. Design reference
Approved interactive mockup (Nexus-branded, light/dark, drill-down drawer, ask panel):
https://claude.ai/code/artifact/0ab71ffc-5114-4744-8e91-a1d40e958ec4
