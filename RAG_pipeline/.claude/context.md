# RAG_pipeline Sub-project Context

Live state only -- no dated history. Every dated status block this file used to carry is verbatim in `.claude/archive/context-history.md`.
Session detail: `.claude/PROGRESS_LOG.md` (newest-first, ~30-day window; older entries under `.claude/archive/`).
Architecture decisions: `knowledge_base/.agent/decisions/`.

## What this sub-project is

`RAG_pipeline/` is the Nexus product surface: a FastAPI backend (:8000 -- Qdrant, PostgreSQL, Ollama/Kimi, auth/RBAC, the agent
runtime, the runtime-data mirror) and a Next.js frontend (:3000 -- chat, topology, admin, onboarding). See `RAG_pipeline/CLAUDE.md`.

## Current state

- **Committed.** `457622c` (2026-08-24) squashed the long-standing uncommitted tree onto `master` and later work is committed on top.
  `UNCOMMITTED` suffixes still present in PROGRESS_LOG session headers are stale markers.
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
- **Topology**: `/topology` opens on the scanner-computed L1 landscape with L2 drill-down; the copilot resolves relationship-shaped
  targets client-side.
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

- **Two 2026-08-26 blocks contradict on the runtime outcome-trend chart.** One says it shipped, passed authenticated browser
  verification at 24h/7d/30d/90d and the frontend image was rebuilt. Another, same date, says live evidence disproved its core
  assumptions (empty buckets are not proven zero-run periods; the adaptive log rule triggers on p50-vs-p95 spread), that the edit was
  rolled back before build, and that nothing should be rebuilt until a truth contract is agreed. Which describes the deployed chart
  cannot be determined from dates; both are in the archive.
- The archived `## Blockers` section lists a Voyage AI API key placeholder needing a real key; nothing dated since confirms or retires
  it, so whether reranking is live is unconfirmed.
- Latest baselines, from different trees: backend 2,672 passed / 5 pre-existing (2026-09-01); frontend 1,063 / 5 (2026-08-28).
