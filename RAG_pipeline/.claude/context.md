# RAG_pipeline Sub-project Context

Live state only -- no dated history. Every dated status block this file used to carry is verbatim in `.claude/archive/context-history.md`.
Session detail: `.claude/PROGRESS_LOG.md` (newest-first, ~30-day window; older entries under `.claude/archive/`).
Architecture decisions: `knowledge_base/.agent/decisions/`.

## What this sub-project is

`RAG_pipeline/` is the Nexus product surface: a FastAPI backend (:8000 -- Qdrant, PostgreSQL, Ollama/Kimi, auth/RBAC, the agent
runtime, the runtime-data mirror) and a Next.js frontend (:3000 -- chat, topology, admin, onboarding). See `RAG_pipeline/CLAUDE.md`.

## Current state

- **Committed** through `acd990f`; `UNCOMMITTED` suffixes in older PROGRESS_LOG headers are stale markers.
- **Answer path**: Hindsight was removed from both RAG entry points (2026-09-01) -- it cost ~20-25s per turn on the critical path and
  its timeouts were hanging turns. `hindsight_client`, the `/memory` routes and the health check are untouched; memory is still
  reachable, just not on the answer path. The turn-claim lease is now derived (`agent_runtime_v2_hard_deadline_seconds` + margin =
  330s), not a literal 90s, so a turn can no longer be marked `abandoned` two seconds after producing a complete answer.
- **Streaming**: provisional draft streaming is on for every tool path (`AGENT_RUNTIME_V2_DRAFT_ANSWER_STREAMING_ENABLED`, default
  true); the authoritative post-persistence replay always wins on screen.
- **Onboarding / walkthrough**: persona is resolved server-side from persisted permissions (`src/rbac/persona.py`), role is granted at
  access approval, and the tour is a custom engine (driver.js removed) with per-role steps and a `using` / `running` split of `/guide`.
- **Runtime mirror**: TMC sources choose their ingest scope at connect time; doc read scope is a separate per-project setting. Poll
  interval defaults rose 60s -> 1800s after the scheduler was measured pinning a CPU core. `/admin/logs` is the hub + drill-down console.
- **Runtime tools for doc runs** (uncommitted): `GET/POST /api/v1/runtime/tools*` exposes the 25-tool registry via
  `execute_runtime_tool`; `POST /api/v1/runtime/elastic/probe/*` adds 4 read-only Elastic primitives. Both CSRF-exempt (bearer key,
  no cookie). Probe bodies are forced to `size: 0`/`_source: false` and RAISE if documents return; an unmapped field yields `None`,
  never an ambiguous `0`. No read-grant widening yet (0 grant rows) -- fix belongs in `execute_runtime_tool`, not inline. See ADR-035.
- **Runtime health on the topology** (uncommitted): providers DECLARE how their rows bind to scanned designs
  (`static_ref_strategy()` -- tmc/mock `artifact_name`, elastic/stored none) and what a row MEANS per kind
  (`executable_semantics()` -- job=execution, route/data_service=lifecycle). `runtime/topology/binding.py` writes
  `runtime_job_scan_maps` during sync: exact only, ambiguity refused, `resolved_by="manual"` never overwritten, SAVEPOINT so it
  cannot fail a mirror cycle (migration 106). `/runtime/job-health` no longer hardcodes `executable_kind='job'` (omitted = ALL) and
  carries `artifact_name`/`executable_kind`/`liveness`. Business canvas overlay in `lib/businessHealth.ts` propagates
  source->destination -- the OPPOSITE direction to `lib/healthPropagation.ts` (callee->caller); unmeasured is grey with a reason,
  never green. Inspection WARN, `.plans/inspection.md`.
- **Topology**: L1 landscape + L2 drill-down; mirrors match the MCP contract field-for-field (parity tests), every field renders,
  and `get_topology_node` on a stream returns `business_facts` (uncommitted, 2026-09-23).
- **Sources**: GitHub App, Azure DevOps (organization PAT) and ZIP are first-class; multi-repo projects are selectable from
  `/settings/sources` with branch pinning. Model: `kimi/kimi-k2.6` (k2.5 was retired by the provider).

## Active work

- Nothing from the last several sessions has been watched rendered: the walkthrough, `/admin/runtime`, `/admin/organization` and
  `/admin/logs` all need an authenticated visual pass. Migrations 097 and 098 are applied locally; multi-repo and read-grant work has
  not been exercised against a live GitHub / Azure DevOps / Talend Cloud account.

## Known blockers

- No cross-provider LLM fallback in the V2 runtime: a Moonshot `engine_overloaded_error` surfaces as a 429. Deliberate, per `config.py`.
- No lease heartbeat: a genuinely crashed turn now blocks its conversation for 330s instead of 90s. The cheap mitigation (sweep
  `claimed` rows to `abandoned` at startup) is not implemented.
- `runtime_log_events` is ~868 MB / 424k rows with no retention policy, and polling still runs inside the API process.
- EP-001 acceptance still needs the runbook executed on a clean reference VPS. `inspect` was skipped on several changes in critical
  areas (`src/rbac/`, the read-only agent runtime) by explicit instruction.

## Next step

Authenticated browser pass over the walkthrough and the admin consoles, then narrow each runtime source's environment scope and
decide a `runtime_log_events` retention policy.

## Unverified

- **The runtime outcome-trend chart is in an unknown state.** Two same-day archive blocks contradict: one says it shipped and passed
  browser verification, the other says live evidence disproved its assumptions (empty buckets are not proven zero-run periods) and the
  edit was rolled back before build. Dates cannot break the tie; both are in `.claude/archive/`.
- Whether reranking is live is unconfirmed -- the archived blockers list a Voyage AI key placeholder and nothing since retires it.
- Latest baselines: backend `pytest tests/ --ignore=tests/test_scripts` 4082 passed / 7 failed, and `tests/test_scripts` cannot be
  collected at all (`ModuleNotFoundError: No module named 'langchain_openai'`); frontend `npm test -- --run` 1474 passed / 7 failed.
  Both sets of 7 reproduce on a clean-HEAD worktree. The 59 jsdom `localStorage.clear` failures are GONE: Node 26 declares a
  `localStorage` global that is `undefined` without `--localstorage-file` and shadows jsdom's, so `tests/setup.ts` now restores an
  in-memory Storage -- that recovered 52 tests and revealed the 7 that were hidden behind it.
