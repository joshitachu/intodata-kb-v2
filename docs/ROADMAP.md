# IntoData KB Platform — Product Roadmap
**Date:** March 10, 2026 | **Timeline:** 12 weeks (Mar 10 – Jun 1, 2026) | **Team:** 1 developer
**Goal:** MVP → customer-ready product with multi-platform support

---

## At a Glance

| Metric | Value |
|--------|-------|
| Overall Complete | ~70% |
| Lines of Code | ~18,000 |
| Built So Far | 5 weeks |
| Remaining Roadmap | 12 weeks |

---

# What We've Built (Feb 3 – Mar 9, 2026)

*Project started: February 3, 2026 · 5 weeks of development · 1 developer*

## Development Timeline

| Period | Phase | Key Accomplishments |
|--------|-------|---------------------|
| Feb 3–9 | **Foundation** | Scaffolding, FastAPI backend, PostgreSQL + Qdrant, document ingestion (PDF/DOCX/PPTX/XLSX), Next.js frontend, 48 tests |
| Feb 9–11 | **Retrieval Quality** | Semantic chunking (256-token, heading-aware), hybrid search (dense+sparse+RRF), cross-encoder reranking, query expansion, streaming SSE, conversational RAG, citations with [1] anchors, 186 tests |
| Feb 23–24 | **Performance** | Eliminated LLM intent classifier (27s→3-5s latency), added Kimi K2.5 ($0.60/M, 256K context), Voyage AI embeddings (+14% vs OpenAI), Voyage reranker (replaced broken local cross-encoder), fixed TF-IDF sparse embedding bug (hash-based), singleton HTTP clients, 174 tests |
| Feb 27–Mar 1 | **Memory + Deep Dive** | Hindsight memory integration (all 3 query paths), early Deep Dive mode (then Claude Code CLI subprocess + SSE streaming; now superseded by the repo-owned engine path), streaming/timeout fixes, markdown typography |
| Mar 4–6 | **Auth + Security** | JWT httpOnly cookies (access 60min + refresh 7d), bcrypt, RBAC (3 roles, 9 permissions, project assignments, audit logs), VPS security (UFW, fail2ban, port isolation, Caddy prep, daily backups) |
| Mar 6–9 | **Deployment** | VPS production deployment, Docker stack (6 services), migration fixes, Hindsight model optimization (gemma3:12b→4b for memory) |

---

## What Was Built From Scratch

| Component | Lines (est.) | Notes |
|-----------|-------------|-------|
| RAG backend (FastAPI) | ~5,000 | 11 DB models, hybrid search, streaming, reranking, provider chains |
| RAG frontend (Next.js) | ~3,000 | Search, chat, documents, admin, settings pages |
| Talend scanner (intodoc) | ~4,000 | XML/XMI parser, distiller (50-65% token reduction), 22 analysis prompts |
| MCP server | ~2,500 | 8 MCP tools, 12 REST endpoints, SSE log streaming, doc generation orchestration |
| KB dashboard (React) | ~1,500 | Project management, run history, live logs |
| JWT auth system | ~800 | Cross-service auth, refresh rotation, admin panel |
| RBAC system | ~600 | Permissions, roles, project assignments, audit logs |
| VPS deployment | ~400 | Docker configs, systemd, backups, security hardening |
| **TOTAL** | **~18,000** | **Built in 5 weeks by 1 developer** |

---

## Key Architecture Decisions

| Decision | Why | Status |
|----------|-----|--------|
| No LangChain | Full control, fewer deps, easier debugging | Revisiting — will add langchain-core for token tracking |
| BFF pattern (Next.js proxies FastAPI) | Security, no direct client→API calls | Locked |
| Voyage AI embeddings + reranker | +14% over OpenAI, 200M free tokens | Locked |
| Kimi K2.5 as primary LLM | 1T MoE, fast, $0.60/M, 256K context | Locked (model switching added in Phase 3) |
| Hash-based sparse embeddings | Fixed TF-IDF vocabulary mismatch bug | Locked |
| JWT httpOnly cookies | Stateless, XSS-safe, cross-service | Locked |
| Plugin architecture for scanners | PlatformPlugin ABC + registry | Locked — ready for new platforms |
| Custom harness for doc generation | Repo-owned 7-phase pipeline, provider/model adapters, staging, validation, and RAG push | Locked |

---

## Performance Improvements Achieved

| Metric | Before | After |
|--------|--------|-------|
| Query latency | ~27s | ~3-5s |
| Intent classification | LLM call (3-12s) | Keyword regex (0ms) |
| Sparse embeddings | Broken (vocabulary mismatch) | Working (hash-based) |
| Hindsight memory | Blocking (60s timeout) | Fire-and-forget (15s) |
| Reranker cold-start | +100s (first request) | Pre-loaded at startup |

---

## Current Completion Status (~70% Overall)

| Component | Status | % |
|-----------|--------|---|
| Talend Scanner | Production | 95% |
| Universal IR | Production | 90% |
| MCP Server (8 tools + 12 endpoints) | Production | 85% |
| VPS Deployment (Docker, UFW, fail2ban) | Production | 80% |
| RAG Pipeline (hybrid search, streaming, conversations) | Production MVP | 80% |
| RAG Frontend (search, chat, docs, admin, benchmark) | Production | 85% |
| Auth/RBAC (JWT, 3 roles, 9 perms, audit, permission guards) | Production | 95% |
| Dashboard (scan/generate/push, SSE logs) | Mature MVP | 70% |
| Token/Cost Tracking (all interfaces, usage dashboard) | Production | 95% |
| Benchmarking (CLI + API + frontend page) | Production | 95% |
| Second Platform Scanner | Not started | 0% |
| Model Switching / BYOM | Not started | 0% |
| HTML Assessment Sites | Not started | 0% |
| Cross-Platform Migration | Not started | 0% |

---

## Token Tracking: Current State (Phase 1 Complete)

All interfaces now track and persist token usage to the `query_usage` table:

| Interface | Token Tracking | Display | Persistence |
|-----------|---------------|---------|-------------|
| RAG queries (search/chat) | Kimi/Ollama response.usage extracted | SSE usage event + token badge | QueryUsage table (auto) |
| RAG streaming (conversations) | Same as above | SSE usage event | QueryUsage table (auto) |
| Embedding (Voyage/Ollama) | Token counts captured per call | Included in QueryUsage | embedding_tokens column |
| Doc generation (KB dashboard) | Provider/runtime usage extracted by the generation coordinator/harness | RunHistory.tsx + GenerateRun | QueryUsage via POST /usage/record |
| Deep dive (KB Q&A) | Repo-agent/provider usage captured during streaming | Shown in real-time SSE | QueryUsage via POST /usage/record |
| **Aggregation** | Admin usage dashboard | Per-user, per-model, per-interface, daily charts | GET /admin/usage/* endpoints |

---

# Phase 1: Finish & Polish (Weeks 1–4)

---

## 1.1 Complete RBAC — Week 1 (5-7 days)

**Goal:** Admin manages roles/permissions/projects via UI; developers only see their assigned data.

- Admin UI pages: role editor, project assignment manager
- Per-query RBAC filtering on all RAG search endpoints (Qdrant `project_id` filter from `ProjectAssignment`)
- Dashboard hides actions for non-permitted users

### Key Files
- `RAG_pipeline/backend/src/retrieval/search.py` — add `project_ids` filter to `_build_search_filter()`
- `RAG_pipeline/backend/src/api/routes/query.py` — enforce project-level RBAC
- `RAG_pipeline/frontend/` — new `/admin/roles` and `/admin/project-assignments` pages
- Reuse: `knowledge_base/mcp-server/src/mcp_server/api/permissions.py` (already has `require_project_access()`)

---

## 1.2 Token / Cost Tracking — Weeks 1–2 (4-5 days)

**Goal:** Every LLM call records tokens/cost; always visible in UI across ALL interfaces.

### What Needs to Be Built

**A) Fix KB Doc Generation Token Tracking (shows `-` currently):**
- `claude_service.py` / generation coordinator: accumulate token counts from provider events during streaming, not just from final result events
- Fallback: if result event has `cost_usd`/`total_input_tokens`, use those; otherwise use accumulated counts
- Verify `RunHistory.tsx` displays actual numbers after fix

**B) RAG Query Token Tracking (the biggest gap):**
- `llm_client.py`: extract `response.usage` from Kimi (OpenAI SDK returns `prompt_tokens`, `completion_tokens`) and `eval_count`/`prompt_eval_count` from Ollama
- `embedder.py`: return token counts from embedding calls (Voyage returns usage, Ollama doesn't)
- `query.py`: emit `event:usage` SSE event after stream completes with `{input_tokens, output_tokens, model, cost_usd, latency_ms}`
- New `QueryUsage` DB table (migration 007): per-query token metrics
- RAG frontend: display token badge after each response

**C) Deep Dive Persistence:**
- New `DeepDiveRun` DB table or extend `GenerateRun` to cover deep dive
- `deep_dive_service.py`: persist usage data after stream completes

**D) Aggregation Dashboard:**
- Usage dashboard page: daily/weekly/monthly aggregates by user, model, and interface
- Per-project cost summary widget

### Approach: LangChain Callbacks (approved)

Use LangChain's callback system for centralized token tracking. This gives:
- Automatic token counting for any LLM provider (OpenAI-compat, Ollama)
- Built-in cost calculation with up-to-date pricing
- LangSmith tracing integration (optional, for detailed pipeline observability)
- Works as lightweight callbacks — doesn't require rewriting the RAG pipeline

**Implementation:** Add `langchain-core` + `langchain-community` as dependencies. Wrap existing LLM calls with `CallbackHandler` that logs to DB. This is additive — existing code stays, callbacks capture usage on top.

**Alternative:** Custom `UsageStats` dataclass + manual extraction from response objects. Simpler but requires per-provider code.

### Key Files to Modify
- `RAG_pipeline/backend/src/generation/llm_client.py` — add usage extraction (lines 71-84 for Kimi, 96-106 for Ollama)
- `RAG_pipeline/backend/src/api/routes/query.py` — emit usage SSE event, persist to DB
- `RAG_pipeline/backend/src/retrieval/embedder.py` — return embedding token counts
- `knowledge_base/mcp-server/src/mcp_server/services/deep_dive_service.py` — persist usage after stream
- **NEW:** `RAG_pipeline/backend/src/db/models/query_usage.py`
- **NEW:** `RAG_pipeline/backend/alembic/versions/007_add_query_usage.py`

---

## 1.3 Dashboard Polish — Weeks 2–3 (4-5 days)

- Doc preview panel (rendered markdown inline, no navigation)
- Scan history view (same pattern as GenerateRun history — reuse `RunHistory.tsx`)
- Project settings panel (edit name, platform, re-scan config)
- Usage summary widget per project (total tokens, cost across all runs)

### Key Files
- `knowledge_base/dashboard/src/components/` — new: `DocPreview.tsx`, `ScanHistory.tsx`, `ProjectSettings.tsx`
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` — add `PATCH /projects/{id}`, `GET /scan-runs/{id}`

---

## 1.4 Benchmarking Framework — Weeks 3–4 (5-6 days)

- Test corpus: 20-30 curated Q&A pairs from Vaxxinova Talend docs
- Benchmark runner CLI: `python -m benchmark run`
- Metrics: retrieval precision@k, answer relevance (LLM-as-judge), latency, token count, cost
- Results stored in PostgreSQL (migration 008); viewable in dashboard
- Baseline recorded: current Kimi K2.5 + Voyage AI scores

### New Package
`RAG_pipeline/backend/benchmark/` — `runner.py`, `judge.py`, `report.py`, `corpus/`

---

## Phase 1 Week-by-Week Schedule

| Week | Focus | Deliverable | Status |
|------|-------|-------------|--------|
| 1 | RBAC + token tracking start | Admin UI for roles; query usage recording | DONE |
| 2 | Token tracking + dashboard | Usage dashboard; doc preview | DONE |
| 3 | Dashboard polish + bench setup | Scan history; project settings; benchmark corpus | DONE |
| 4 | Benchmarking framework | Runner, judge, baseline scores | DONE |

> **Exit Criteria:** Admin assigns project → developer only sees that project's RAG data. Every LLM call tracked. Benchmark baseline recorded. **All criteria met.**

---

# Phase 2: Multi-Platform + Customer Delivery (Weeks 5–9)

*Theme: Add 3 platforms (Workato, Lobster, ADF), migration view, and assessment sites.*

---

## Platform Plugin Architecture — Reuse Model

All platforms follow the same `PlatformPlugin` ABC → registry pattern. Each needs: parser, distiller, prompts, `best_practices`. Zero changes to core CLI/MCP/RAG code.

| Platform | Source Format | Parse Complexity | Effort | Notes |
|----------|--------------|-----------------|--------|-------|
| Workato | JSON recipe exports | Low | 4-5 days | Simplest; good first candidate |
| Lobster | XML/visual mapping configs | Low-Medium | 5-6 days | German iPaaS, XML configs |
| Azure Data Factory | JSON ARM templates + pipeline JSON | Medium | 6-8 days | Customer demand: "Talend naar Azure" |

### Key Reuse Per Platform
`platforms/base.py` (PlatformPlugin ABC), `models.py` (Component, ParsedJob, MetadataConnection), `platforms/registry.py`, 80% of prompt structure from Talend.

### New Directory Structure Per Platform
```
knowledge_base/intodoc/src/intodoc/platforms/<name>/
├── __init__.py          # <Name>Plugin class (implements PlatformPlugin)
├── parser.py            # Source format parser → ScanResult
├── distiller.py         # Token reduction for platform-specific noise
├── best_practices.yaml  # Platform knowledge for LLM context
└── prompts/             # p01-p10+ analysis prompts (reuse from Talend, retune)
```

---

## 2.1 Workato Scanner — Week 5 (4-5 days)

- `WorkatoPlugin` implementing `PlatformPlugin`
- JSON recipe parser: recipes → `ParsedJob`, actions/triggers → `Component`, connections → `MetadataConnection`
- Workato distiller (strip UI metadata, auth tokens)
- 10+ Workato-tuned prompts
- Register: `"workato": ("intodoc.platforms.workato", "WorkatoPlugin")`
- Unit tests with sample recipe JSON

---

## 2.2 Lobster Scanner — Week 6 (5-6 days)

- `LobsterPlugin` implementing `PlatformPlugin`
- XML/config parser: profiles → `ParsedJob`, phases/steps → `Component`, partners → `MetadataConnection`
- Lobster distiller
- 10+ Lobster-tuned prompts
- Register: `"lobster": ("intodoc.platforms.lobster", "LobsterPlugin")`
- Unit tests

---

## 2.3 Azure Data Factory Scanner — Weeks 7–8 (6-8 days)

- `AdfPlugin` implementing `PlatformPlugin`
- ARM template + pipeline JSON parser: pipelines → `ParsedJob`, activities → `Component`, linked services → `MetadataConnection`, datasets → schema
- ADF distiller
- 10+ ADF-tuned prompts
- Register: `"adf": ("intodoc.platforms.adf", "AdfPlugin")`
- Dashboard: add all 3 new platforms to platform dropdown

---

## 2.4 Cross-Platform Migration View — Weeks 8–9 (4-5 days)

- Static mapping tables: component pairs per platform combination (Talend↔ADF, Talend↔Workato, etc.)
- Migration comparison endpoint: `POST /migration/compare` (source project + target platform → mapping report)
- Gap analysis: components with no target equivalent
- Risk scoring per migration item
- Dashboard: migration comparison page

### New Files
- `knowledge_base/mcp-server/src/mcp_server/services/migration_service.py`
- `knowledge_base/mcp-server/src/mcp_server/api/routes/migration.py`

---

## 2.5 HTML Assessment Site — Week 9 (3-4 days)

**Goal:** Auto-generated HTML site per project, updated on every doc generation.

- Static site generator: Markdown → HTML with Jinja2 templates + Tailwind CSS
- Per-project site at `/output/{project_id}/site/` with sidebar nav + client-side search (lunr.js)
- Auto-generation hook in `_run_generate_background()` (after RAG push succeeds)
- Role-based access via Caddy JWT validation

### New Files
- `knowledge_base/mcp-server/src/mcp_server/services/site_generator.py`
- `knowledge_base/mcp-server/templates/assessment/` (`base.html`, `doc.html`, `static/`)

---

## Phase 2 Week-by-Week Schedule

| Week | Focus | Deliverable |
|------|-------|-------------|
| 5 | Workato scanner | Full plugin: parser, distiller, prompts, tests |
| 6 | Lobster scanner | Full plugin: parser, distiller, prompts, tests |
| 7 | ADF scanner (part 1) | Parser core, models, file classification |
| 8 | ADF scanner (part 2) + migration | Complete ADF plugin; migration comparison endpoint |
| 9 | Migration UI + assessment site | Dashboard migration page; HTML site generator |

> **Exit Criteria:** All 3 platforms scan + generate docs + push to RAG. Migration comparison produces report. Assessment site auto-generated.

---

# Phase 3: Model Comparison + Risk (Weeks 10–12)

---

## 3.1 Runtime Model Switching + BYOM — Week 10 (5 days)

- `ModelConfig` DB table: name, provider type (`openai-compat`/`ollama`), `base_url`, `api_key` (encrypted), pricing
- Generic `openai_compat` provider (works with any OpenAI-compatible API: LM Studio, vLLM, Together AI, etc.)
- Per-request `model_id` parameter in query endpoints
- Frontend: model selector dropdown in search/chat UI
- Admin: model management page (add/edit/test custom endpoints)

### Key File
`RAG_pipeline/backend/src/generation/llm_client.py` — already has `_PROVIDER_FUNCS` registry; extend to dynamic DB-backed config.

---

## 3.2 Model Benchmarking + Comparison — Week 11 (5 days)

- A/B comparison runner: same corpus, two model configs
- Side-by-side answer comparison UI
- Radar/bar chart: quality vs speed vs cost (recharts)
- Historical benchmark tracking

*Builds on 1.4 benchmark framework + 3.1 model switching.*

---

## 3.3 Risk Assessment + LiteLLM (Optional) — Week 12 (5 days)

- Risk scoring engine: code complexity, data sensitivity, migration risk
- Risk report integrated into assessment site HTML
- Risk assessment API endpoint
- Optional: LiteLLM proxy Docker service for centralized cost tracking (if time allows)

---

## AI Debugging Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Hallucination | HIGH | Ground in scan data (MCP tools); citations; human review |
| Credential exposure | HIGH | `credential_filter.py` runs BEFORE any LLM call |
| Data residency (code sent to external APIs) | HIGH | Support local-only mode (Ollama); per-customer policy |
| Incomplete context | MEDIUM | ProjectManifest + multi-pass analysis |
| Stale docs | MEDIUM | Auto-regenerate on push; timestamp all docs |
| Model bias | MEDIUM | Benchmarking framework validates quality |
| Cost overrun | LOW | Token tracking + budget limits + alerts |

---

## Phase 3 Week-by-Week Schedule

| Week | Focus | Deliverable |
|------|-------|-------------|
| 10 | Model switching + BYOM | Dynamic model registry, frontend selector, OpenAI-compat provider |
| 11 | Model benchmarking | A/B comparison runner, side-by-side UI, charts |
| 12 | Risk assessment + polish | Risk scores in assessment site, final integration |

> **Exit Criteria:** Users switch models at query time. BYOM works. Benchmark comparison shows quality/speed/cost. Risk assessment in every project report. All token/cost tracking complete.

---

# Dependency Graph + Effort Summary

## Dependency Graph

```
Phase 1 (weeks 1-4):
  1.1 RBAC ─────────────┐
  1.2 Token Tracking ───┼──▶  1.3 Dashboard Polish
                        └──▶  1.4 Benchmarking ──▶ 3.1 Model Switching ──▶ 3.2 Comparison

Phase 2 (weeks 5-9, can overlap with late Phase 1):
  2.1 Workato ──▶ 2.2 Lobster ──▶ 2.3 ADF ──▶ 2.4 Migration View
                                               └──▶ 2.5 Assessment Site ──▶ 3.3 Risk

Phase 3 (weeks 10-12):
  3.1 Model Switching ──▶ 3.2 Benchmarking ──▶ 3.3 Risk + Polish
```

**Critical path:** `1.2 → 1.4 → 3.1 → 3.2` (token tracking → benchmarking → model comparison)

---

## Effort Summary (1 dev, 12 weeks = 60 working days)

| Phase | Milestone | Days | Weeks |
|-------|-----------|------|-------|
| 1 | 1.1 RBAC | 5-7 | 1 |
| 1 | 1.2 Token Tracking | 4-5 | 1-2 |
| 1 | 1.3 Dashboard Polish | 4-5 | 2-3 |
| 1 | 1.4 Benchmarking Framework | 5-6 | 3-4 |
| 2 | 2.1 Workato Scanner | 4-5 | 5 |
| 2 | 2.2 Lobster Scanner | 5-6 | 6 |
| 2 | 2.3 ADF Scanner | 6-8 | 7-8 |
| 2 | 2.4 Migration View | 4-5 | 8-9 |
| 2 | 2.5 Assessment Site | 3-4 | 9 |
| 3 | 3.1 Model Switching + BYOM | 5 | 10 |
| 3 | 3.2 Model Benchmarking | 5 | 11 |
| 3 | 3.3 Risk Assessment | 5 | 12 |
| — | **TOTAL** | **55-66 days** | **12 weeks** |

*Tight but achievable. Buffer: scanner plugins share 80% structure (each gets faster after the first), and Phase 1 milestones 1.1+1.2 can partially overlap in the same week.*

---

# Blockers & Risks

| Blocker | Impact | Mitigation |
|---------|--------|------------|
| No sample projects for Workato/Lobster/ADF | Can't test scanners | Get exports from customers OR create synthetic test data from public docs |
| Voyage AI rate limits (3 RPM free) | Slow benchmarks | Use Ollama for speed tests; Voyage for quality tests |
| VPS memory (6 services already) | Adding more may exceed RAM | Monitor; consider upgrading VPS or splitting services |
| Customer data access for migration demo | Need real cross-platform projects | Use Vaxxinova Talend + synthetic target projects |
| 1 dev / 12 weeks is tight | Risk of slipping on Phase 3 | Scanner plugins get faster after first one; Phase 3 can compress |

---

# Verification Plan

After each milestone, verify:

1. **RBAC:** Create developer user → assign 1 project → query RAG → confirm only that project's docs returned
2. **Token tracking:** Run query → check `query_usage` table → confirm frontend shows token badge
3. **Dashboard:** Scan project → view scan history → preview docs → check usage widget
4. **Benchmarking:** Run `python -m benchmark run` → view results in dashboard
5. **ADF Scanner:** `intodoc scan test-adf --platform adf` → verify JSON cache → generate docs → push to RAG
6. **Migration:** Compare Talend + ADF projects → verify mapping report with gaps and risk scores
7. **Assessment site:** Generate docs → verify HTML site at `/output/{project_id}/site/`
8. **Model switching:** Select different model in UI → run same query → compare responses
9. **BYOM:** Add custom OpenAI-compatible endpoint → query with it → verify token tracking
10. **Risk assessment:** View assessment site → verify risk section with scores and mitigations

---

# Long-Term Vision (Post-12 Weeks)

| Feature | When | Notes |
|---------|------|-------|
| Alumio scanner | Q3 2026 | Same plugin pattern, 4th platform |
| MuleSoft scanner | Q3 2026 | XML-based, similar complexity to Talend |
| dbt scanner | Q3 2026 | SQL + YAML, simplest format |
| Auto-assessment on git push (webhooks) | Q3 2026 | Builds on 2.5 assessment site |
| Migration code generation (A→B) | Q4 2026 | Generate target platform code from IR |
| Multi-tenant SaaS | Q4 2026 | Full RBAC + per-customer isolation |
| CI/CD pipeline | Q3 2026 | Automated testing + deployment |
