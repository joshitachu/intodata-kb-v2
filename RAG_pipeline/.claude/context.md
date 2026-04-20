# Current State

**Status**: Phase 6.1 Azure-style provisioning + data-driven wizard config (2026-04-17). Wizard is now 6 steps with workspace naming and a staged provisioning animation; platform/tool options are data-driven via `GET /onboarding/config` and coming-soon tools render with badges. Tenant table dropped — switched to one-VPS-per-client deployment model.
**Last session**: 2026-04-17
**Last completed**: Phase 6.1 refactor — migration 014 rewritten to drop tenants + add `workspace_name`. New `GET /onboarding/config` endpoint + `PUT /onboarding/step/workspace` endpoint. Frontend: new `WorkspaceStep`, new `ProvisioningStep` (replaces `SetupLoadingStep`) with staged checklist + summary card, Platform/Tool steps now fetch options from backend config, coming-soon items show "COMING SOON" badge. Background setup flow is still stubbed (3s sleep + notification) — real ingest pipeline integration is TODO.
**Also this session (earlier from dev-B)**: Hindsight memory retention fix deployed. `decide_retention()` now allows FULL retention for metadata queries with search results, all retain call sites pass `action`/`query_type`/`project_ids`, distillation disabled (structured fallback), Hindsight timeout increased to 300s.

## What's Done

### Backend (216 tests passing)
- FastAPI app with CORS, lifespan events
- Database: SQLAlchemy async models (User, Role, Document, QueryLog, IngestionJob)
- Ingestion: PDF/DOCX/PPTX/XLSX parsing, **semantic chunking** (paragraph-aware, heading-aware, slide markers), **replace-on-re-upload**, **sentence-boundary chunking** (overlap=100)
- Retrieval:
  - **Hybrid search**: Dense (provider chain: Voyage AI → Ollama) + Sparse (hash-based, fixed TF-IDF bug) with RRF fusion
  - **Voyage AI reranking**: rerank-2.5 via API (replaced local cross-encoder ms-marco-MiniLM-L-12-v2)
  - **Query expansion**: LLM-generated alternatives via provider chain (more-specific + more-general)
  - **Chat-with-document**: Optional document_ids filter on all search functions
  - Named vectors in Qdrant (dense + sparse), payload indexes for RBAC + document_id
  - **Tuned parameters**: top_k=10, threshold=0.3, rerank_top_n=30, prefetch_limit=50, max_documents=6
- Generation: **Multi-provider LLM client** (Kimi K2.5 → Ollama fallback chain, sync + **streaming SSE**, Ollama num_ctx=16384), prompt builder (10000 token context with per-source truncation), RAG chain with intent routing + anti-hallucination prompts
- **Embedding provider chain**: Voyage AI (voyage-4-large, 1024-dim) → Ollama (nomic-embed-text, 768-dim) fallback
- **Intent classifier**: Zero-LLM heuristic router (keyword-based summary detection, all queries go through SEARCH)
- **Map-reduce summarization**: Full-document summaries via parallel map → reduce
- **Numbered citations**: LLM uses `[1]`, `[2]` format with `[Reference N: filename]` headers
- **Auth**: JWT httpOnly cookies (access 60min + refresh 7d rotation), bcrypt passwords, rate-limited login, admin panel for user management
- **RBAC**: Full permission system (9 codenames), Manager/Developer roles, project assignments, audit logging
- **Security**: Hardcoded secrets removed, localhost-only Docker ports, Caddy auto-TLS, UFW+fail2ban, security headers, CORS tightened, ingestion API key auth, extended rate limiting, daily PostgreSQL backups
- API routes: /health, /api/v1/query, /api/v1/query/debug, /api/v1/query/stream (SSE), /api/v1/documents, /api/v1/ingest
- Conversations: /api/v1/conversations with conversational RAG pipeline
- Feature flags: `hybrid_search_enabled`, `reranker_enabled`, `query_expansion_enabled`

### Frontend (build passing)
- Next.js 15 App Router with BFF pattern (route.ts → FastAPI)
- React Query for server state, Zod for validation
- Tailwind + lucide-react for styling
- **Markdown rendering**: `react-markdown` + `remark-gfm` for rich answer display
- **Clickable citations**: `[N]` in answers scroll to `#source-N` anchors
- Pages: / (chat — main interface), /chat (redirects to /), /documents (upload/list/delete), /settings (health)

## To Run

```bash
docker-compose up -d          # Qdrant + PostgreSQL
cd backend && alembic upgrade head && uvicorn src.api.main:app --reload
cd frontend && npm run dev    # http://localhost:3000
```

## Recent Changes

- **Phase 5 Memory Dashboard** (2026-04-15): Admin dashboard at `/admin/hindsight` with Insight Pipeline, Memory Banks, and Reflect tabs. Backend: 4 GET endpoints + `get_bank_stats()` / `list_bank_memories()` helpers. Frontend: 4 BFF proxy routes, Sidebar Brain icon link, full dashboard page. Bug fix: `acreate_mental_model`. Files: `hindsight_client.py`, `memory.py`, `Sidebar.tsx`, 4 new route.ts, `hindsight/page.tsx`.
- **Enhanced Hindsight Memory Integration** (2026-04-14): Project-scoped banks, tags, intent-based gating, LLM distillation, observations, reflect, mental models. Admin API at `/memory/mental-models` and `/memory/reflect-patterns`. Files: `hindsight_client.py` (full rewrite), `conversational_rag.py`, `config.py`, `memory.py` (new), `main.py`.
- **Admin sidebar visibility & role separation** (2026-04-09): Login sets auth state instantly (no refresh needed), admin role restricted to read-only dashboard (usage/audit/benchmark), superadmin-only for user/role/project management. Migration 012 + frontend guards + backend AuditViewer dependency.
- **Post-stress-test improvements** (2026-04-03): 5 fixes — targeted enrichment query for project_detail (overview docs), reranker for metadata enrichment, document_sources init bug, METADATA_SYSTEM_PROMPT rewrite (doc content primary), classifier prompt refinement (content vs metadata). Files: `conversational_rag.py`, `prompt_builder.py`, `intent_classifier.py`.
- **RAG follow-up robustness** (2026-04-03): Streaming context-first enabled, `needs_new_search()` safety net, metadata enrichment for all content intents, cross-project source filter, prompt improvements (partial info + no retraction), temperatures lowered (kimi 0.45, ollama 0.45, classifier 0.0), reranker_min_score 0.05→0.15. Files: `conversational_rag.py`, `prompt_builder.py`, `config.py`, `llm_client.py`, `intent_classifier.py`.
- **RAG hallucination/recall fix** (2026-03-27): Increased retrieval depth (`top_k` 5→10, `max_result_documents` 3→6, `prefetch_limit` 30→50, `rerank_top_n` 20→30), `MAX_CONTEXT_TOKENS` 3000→8192 (chat) / 6144→10000 (search), strengthened anti-hallucination prompts, added project attribution to source headers, improved history truncation (anchors first exchange, 4000 tokens / 20 messages), added `num_ctx=16384` to Ollama calls, fixed hardcoded `top_k=3` in chat path. Files: `config.py`, `conversational_rag.py`, `prompt_builder.py`, `llm_client.py`.
- **Admin project-scoped search** (2026-03-24): Fixed `_build_search_filter()` in `search.py` to apply `allowed_groups` filter when admin selects a specific project. Without this fix, admins saw all documents regardless of project selection.
- **Conversation summary intent** (2026-03-18): LLM classifier now distinguishes "summarize our chat" from "summarize document X". Added `CONVERSATION_SUMMARY` action, conversation context passing, dedicated prompt builder. Fixed `using="dense"` Qdrant crash in `map_reduce.py` and `query.py`. Files: `intent_classifier.py`, `prompt_builder.py`, `conversational_rag.py`, `map_reduce.py`, `query.py`.
- **Auto-detect corrections** (2026-03-16): When a user corrects the chatbot ("no that's wrong, it's actually X"), the system detects it via keyword heuristics, extracts a structured correction via LLM, and stores it in a shared `rag-corrections` Hindsight bank. Corrections are recalled for ALL users on relevant queries and injected as "## Known corrections" in the LLM context. Zero latency impact (fire-and-forget). Files: `hindsight_client.py`, `prompt_builder.py`, `conversational_rag.py`, `rag_chain.py`, `query.py`.
- **Deploy Security + RBAC to VPS** (2026-03-09): Deployed all Phase 6.1/6.2 changes to running VPS. Reverted docker-compose ports to public (no Caddy yet). Added MCP_INGEST_API_KEY to .env and docker-compose.yml. Fixed migration 006 setval bug (`GREATEST(..., 1)` to avoid 0 on empty sequences). Migration 006 applied successfully — permissions, role_permissions, project_assignments, audit_logs tables created and seeded. All services healthy.
- **Fix MCP server DuplicateTableError** (2026-03-02): MCP server `create_all` now checks existing tables first before creating — fixes crash on restart when `projects` table already exists.
- **Fix Docker startup crash** (2026-03-02): Backend Dockerfile now runs `alembic upgrade head` before `uvicorn` startup. Fixes `relation "users" does not exist` crash when PostgreSQL has no tables.
- **Deep Dive Streaming Repair** (2026-03-02): Fixed three compounding issues preventing Deep Dive output: (1) concurrent stderr reading via `asyncio.create_task` to prevent pipe buffer deadlock, (2) restored `assistant` text as fallback when `content_block_delta` events don't arrive (tracked via `_text_streamed_via_delta` flag), (3) made `--session-id` conditional — only used for multi-turn continuations, not first queries from search page. Added debug logging for raw events and parsed event types.
- **Deep Dive Streaming Fix + Chat Integration** (2026-03-02): Fixed `_convert_event()` to handle `content_block_delta` events (streaming tokens were silently dropped). Added multi-turn session management (`--session-id`/`--continue`). Updated prompt for knowledge persistence (`insert_to_rag`). Integrated Deep Dive into `/chat` page with ModeToggle (Chat/Deep Dive), ProjectSelector, DeepDiveChatBubble (collapsible thinking, tool calls, streaming markdown). Build passing.
- **Deep Dive mode** (2026-03-01): Full-stack feature — DeepDiveService (subprocess + stream-json), API endpoints (POST /mcp/deep-dive SSE, GET /mcp/deep-dive/projects), BFF routes, useDeepDive + useDeepDiveProjects hooks, ModeToggle + ProjectSelector + DeepDiveDisplay components, search page integration. Build passing.
- **Fix markdown rendering** (2026-03-01): Installed `@tailwindcss/typography`, registered plugin, added prose CSS variable overrides for HSL theme integration, simplified `MarkdownContent.tsx` by delegating `code`/`pre`/`blockquote` styling to the plugin. Build passing.
- **Fix Hindsight retain blocking response** (2026-02-28): `retain_interaction()` was `await`-ed at all 4 call sites, blocking HTTP response until quality bank timed out (~60s). Changed to `asyncio.create_task()` (fire-and-forget), `asyncio.gather()` (concurrent bank retains), and reduced timeout 60→15s. Response now returns immediately after LLM completes.
- **Fix Voyage reranker 429 crash** (2026-02-28): Added retry with backoff tuned for 3 RPM free tier (`[2s, 5s, 15s]`, 3 attempts) and `Retry-After` header parsing (capped at 30s) for 429 rate limits. Added graceful fallback in `search.py` — on any reranker API failure, returns candidates by original RRF scores instead of crashing.
- **Replace local reranker with Voyage AI rerank-2.5** (2026-02-28): Local cross-encoder returned negative scores for all candidates → nothing reached LLM. Replaced with Voyage AI `rerank-2.5` API (async httpx, 0-1 calibrated scores, `min_score=0.05`). Lowered pre-filter threshold to 0.0 in `search_and_rerank()` so reranker is the sole quality gate. Removed `sentence-transformers` dep (~500MB). 174 tests passing.
- **Fix Hindsight memory not working on /query and /query/stream** (2026-02-28): Added Hindsight recall+retain to `rag_chain.py` (search bar queries) and `query.py` `/query/stream` endpoint. Added `memory_context` parameter to `build_messages()` in `prompt_builder.py`. Added INFO-level diagnostic logging to `hindsight_client.py`. All three query paths (search bar, streaming, conversations) now have full Hindsight integration.
- **Fix LLM timeouts on all endpoints** (2026-02-27): Increased timeouts (Kimi 15→60s, Ollama 30→120s), eliminated non-streaming `generate_answer()` calls from streaming path via `precompute=False` parameter in `_prepare_rag_context()`, improved error logging with exception types. Streaming endpoint no longer blocks on hidden non-streaming LLM calls.
- **Fix RBAC group mismatch — 0 search results** (2026-02-27): Admin users now bypass RBAC group filtering in `_build_search_filter()` (`backend/src/retrieval/search.py` line 35) and `query_debug()` (`backend/src/api/routes/query.py` line 68). Documents stored with `["public", project_id]` were invisible to users with `groups=["admin"]` because `"admin"` was never in `allowed_groups`.
- **Fix NO_RESULTS_PROMPT vs Hindsight memory** (2026-02-27): Added explicit trust declaration for memory context in `NO_RESULTS_PROMPT` (`backend/src/generation/prompt_builder.py` lines 33–43). LLM was ignoring injected Hindsight memories because "NEVER make up information" caused it to treat memory context as untrusted general knowledge. Prompt now distinguishes trusted memory context from general knowledge.
- **Fix config.py drift** (2026-02-24): Synced `config.py` with runtime expectations — removed dead groq/openrouter settings, added missing `kimi_timeout`, `kimi_temperature`, `ollama_timeout`, `reranker_min_score`, fixed `llm_providers` to `["kimi", "ollama"]`, set `query_expansion_enabled=False`. Fixes `AttributeError: 'Settings' object has no attribute 'kimi_timeout'` and `KeyError: 'groq'`.
- **Auto-detect Qdrant dimension mismatch** (2026-02-24): `ensure_collection()` now checks existing collection's dense vector dim against `get_embedding_dim()`. If they differ (e.g. 768→1024 after Voyage AI switch), auto-deletes and recreates. No manual collection deletion needed.
- **Fix SQLAlchemy engine log noise** (2026-02-24): Changed `echo=settings.is_dev` to `echo=False` in `backend/src/db/session.py` — SQLAlchemy's `echo=True` overrides logger level, causing INFO log flood on first request.
- **Fix production bugs + enable Voyage AI** (2026-02-24): Fixed reranker min-score filtering (chitchat no longer gets -9.13 scores sent to LLM), removed `dimensions` param from Voyage API calls (was causing 400 errors), added reranker warmup at startup (no more +100s first request), fixed `await` on sync `analyze_query()` in streaming endpoint, added `delete_collection()` helper for collection migration.
- **Eliminate intent classifier LLM call** (2026-02-23): Replaced LLM-based intent classifier with zero-cost heuristic. All queries → SEARCH. Summary detection via keyword regex. Disabled query expansion by default. QA: 3→1 LLM calls, Chitchat: 2→1 LLM calls.
- **Fix Kimi K2.5 temperature 400 error** (2026-02-23): Changed `kimi_temperature` from 0.4 to 0.6 — Kimi K2.5 instant mode (thinking disabled) requires exactly 0.6. Was causing all Kimi calls to fail with HTTP 400 and fall back to Ollama.
- **Fix RAG Query Latency** (2026-02-23): Disabled Kimi K2.5 thinking mode (`extra_body={"thinking": {"type": "disabled"}}`), set temperature to 0.6 (required by instant mode), added singleton HTTP clients with timeouts (15s Kimi, 30s Ollama). Expected ~27s → ~3-5s.
- **RAG Retrieval Quality Overhaul** (2026-02-23): 4-phase implementation:
  1. Quick wins: Kimi K2.5 provider, smarter context truncation (6144 tokens, per-source), tuned search params
  2. Fixed critical sparse embedding bug (TF-IDF vocabulary mismatch → deterministic hash-based approach)
  3. Voyage AI embedding provider chain (1024-dim), upgraded reranker (L-12-v2), dynamic embedding dimensions
  4. Improved chunking (overlap 32→100/128, sentence-boundary detection)
  - Intent classifier + query expander now use LLM provider chain instead of local Ollama
  - Fixed null-safety bug in intent classifier JSON parser
  - 193 tests passing

## Blockers

- No Docker on dev machine — can't test E2E locally, use SQLite for unit tests
- Kimi API key placeholder added to `.env` — needs real key
- Voyage AI API key placeholder added to `.env` — needs real key

## Next Steps

- [x] RBAC: Project-scoped document access (2026-03-12)
- [x] Metadata-aware RAG: "Ask Anything" mode (2026-03-12)
- [ ] Deploy onboarding: `docker compose up -d --build backend frontend` then `docker exec intodata-kb-v2-backend-1 alembic upgrade head` (applies 014 — creates user_onboarding + notifications tables)
- [ ] Wire `run_background_setup` in `onboarding_service.py` to the real ingest pipeline (currently stubbed with 3s sleep)
- [ ] Wire real file upload for wizard SourceUploadStep (`source_filename` is recorded but ZIP bytes are not yet uploaded)
- [ ] Flip coming-soon tools to `"available"` in `_TOOL_CATEGORIES` (api/routes/onboarding.py) as each integration lands
- [ ] Render workspace_name in the app chrome (sidebar header / page title) once onboarding ships
- [ ] Per-deployment env vars for tenant branding (subdomain, logo, color) — one VPS per client
- [ ] Run Qdrant migration: `python scripts/migrate_qdrant_remove_public.py --apply`
- [ ] Verify: new user login → wizard → tour; existing user → no wizard
- [ ] Phase 2: Workato scanner plugin
- [ ] Frontend: document selector for chat-with-doc

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
