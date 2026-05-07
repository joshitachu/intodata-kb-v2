# Progress

## Phase 1 — MVP (COMPLETE)

- [x] Planning & architecture decisions
- [x] Project scaffolding (dirs, configs, placeholders)
- [x] Database Agent — models, migrations, session (11 tests)
- [x] Backend Agent — RAG pipeline, API routes, RBAC (48 tests)
- [x] Frontend Agent — Next.js, route handlers, components (build passing)

## Phase 1.5 — Scaling & Quality (COMPLETE)

- [x] Map-reduce summarization (full-document summaries via QueryType routing)
- [x] Document re-upload replacement (same filename replaces old vectors + DB record)
- [x] merge_sources_by_document max_documents parameter
- [x] Phase 3 future issues documented (multi-hop, chunking, embedding drift)
- [x] Conversation memory fix (pronoun resolution via assistant context + intent classifier context)
- [x] Context-first chat (follow-ups from previous context, [NEED_SEARCH] fallback)
- [x] LLM search reformulation (replaced expand_query with gemma3:4b reformulator) — **removed in later session**
- [x] Fix [NEED_SEARCH] leak (separate prompt for context-first, safety strip, removed reformulation)

## Phase 2 — Enterprise Retrieval Quality Upgrade (COMPLETE)

- [x] Hybrid search (dense + sparse TF-IDF vectors with RRF fusion)
- [x] Named vectors in Qdrant collection (dense + sparse)
- [x] Cross-encoder reranking (sentence-transformers ms-marco-MiniLM-L6-v2)
- [x] Semantic chunking (paragraph-aware, heading-aware, slide markers)
- [x] Enhanced parsers (DOCX heading styles → markdown markers, PPTX slide markers)
- [x] Query expansion (LLM-generated alternative phrasings, multi-query search)
- [x] search_and_rerank() — hybrid retrieval + cross-encoder pipeline
- [x] search_multi_query() — multi-query expansion + dedup + reranking
- [x] Streaming responses (SSE endpoint POST /api/v1/query/stream)
- [x] Chat-with-document (document_ids filter on all search functions)
- [x] Feature flags (hybrid_search_enabled, reranker_enabled, query_expansion_enabled)
- [x] All 186 tests passing (32 new tests)

## Phase 2.5 — LLM Provider Chain (COMPLETE)

- [x] Configurable `llm_providers` list (Groq → OpenRouter → Ollama fallback)
- [x] Groq provider (AsyncGroq, llama-3.3-70b-versatile)
- [x] OpenRouter provider (AsyncOpenAI with base_url, llama-3.3-70b-instruct:free)
- [x] Ollama provider (existing, unchanged)
- [x] Fallback logic for both sync and streaming generation
- [x] 8 new tests (192 total passing)

## Phase 2.6 — Streaming Chat UI (COMPLETE)

- [x] Backend: `_prepare_rag_context()` shared helper + `RAGContext` dataclass
- [x] Backend: `run_conversational_rag_stream()` async generator
- [x] Backend: `POST /conversations/{id}/messages/stream` SSE endpoint
- [x] Frontend: SSE proxy route handler (BFF pattern)
- [x] Frontend: `useStreamMessage` hook (ReadableStream + TextDecoder SSE parsing)
- [x] Frontend: Optimistic user messages + streaming assistant bubble with blinking cursor
- [x] Frontend: React Query cache invalidation on stream completion
- [x] 192 tests passing (no regressions)

## Phase 3 — RAG Retrieval Quality Overhaul (COMPLETE)

- [x] Smarter context truncation (6144 tokens, per-source truncation instead of dropping)
- [x] Kimi K2.5 LLM provider (OpenAI-compatible, primary in chain)
- [x] Query expansion + intent classifier → LLM provider chain (no more local Ollama dependency)
- [x] Search parameter tuning (top_k=10, threshold=0.3, rerank_top_n=30, prefetch_limit=50, max_documents=6)
- [x] Fixed critical sparse embedding bug (TF-IDF → deterministic hash-based approach)
- [x] Voyage AI embedding provider chain (voyage-4-large, 1024-dim, Ollama fallback)
- [x] Upgraded reranker (L6-v2 → L-12-v2, 33M params)
- [x] Dynamic embedding dimensions in vector store
- [x] Improved chunking (overlap 32→100/128, sentence-boundary detection)
- [x] Fixed null-safety bug in intent classifier JSON parser
- [x] 193 tests passing (1 new test)

## Phase 3.1 — Latency Optimization (COMPLETE)

- [x] Disable Kimi K2.5 thinking mode (`extra_body={"thinking": {"type": "disabled"}}`)
- [x] Lower temperature 1.0 → 0.4 (configurable via `kimi_temperature`)
- [x] Singleton HTTP clients (AsyncOpenAI + ollama.AsyncClient) with lazy init
- [x] Configurable timeouts (kimi: 15s, ollama: 30s, connect: 5s)
- [x] 172 tests passing (no regressions)

## Phase 3.2 — Latency Optimization: Eliminate Intent Classifier (COMPLETE)

- [x] Replace LLM-based intent classifier with zero-cost heuristic (keyword-based summary detection)
- [x] Make `analyze_query()` sync (no async, no LLM call)
- [x] All queries go through SEARCH — Qdrant + NO_RESULTS_PROMPT handles chitchat
- [x] Disable query expansion by default (`query_expansion_enabled: bool = False`)
- [x] Update all callers (rag_chain.py, conversational_rag.py) to sync call
- [x] Rewrite tests: 20 sync intent classifier tests, updated greeting tests in rag_chain + conversational_rag
- [x] 171 non-DB tests passing (no regressions)

## Phase 3.3 — Production Bug Fixes + Voyage AI Enablement (COMPLETE)

- [x] Reranker min-score filtering (`reranker_min_score: float = 0.0` in config)
- [x] Reranker warmup at startup (pre-load cross-encoder model in lifespan)
- [x] Fix Voyage AI 400 error (remove unsupported `dimensions` param)
- [x] Fix sync/async crash in streaming endpoint (`await` on sync `analyze_query()`)
- [x] Add `delete_collection()` helper for collection migration
- [x] Auto-detect Qdrant vector dimension mismatch at startup (auto-delete + recreate if dim changed)
- [x] 3 new reranker tests + updated existing test mocks
- [x] 174 non-DB tests passing (no regressions)

## Phase 4.1 — Deep Dive Mode (COMPLETE)

- [x] DeepDiveService: subprocess pattern reuse from ClaudeService, stream-json parsing, SSE event yielding
- [x] Backend API: POST /mcp/deep-dive (SSE streaming), GET /mcp/deep-dive/projects
- [x] Frontend BFF routes: /api/deep-dive/query (SSE pass-through), /api/deep-dive/projects
- [x] Frontend hooks: useDeepDive (SSE streaming + AbortController), useDeepDiveProjects (React Query)
- [x] Frontend components: ModeToggle, ProjectSelector, DeepDiveDisplay
- [x] Search page integration: mode toggle, project selector, conditional rendering
- [x] SearchBar: added placeholder + disabled props
- [x] Build passing (14 routes)

## Phase 4.2 — Deep Dive Streaming Fix + Chat Integration (COMPLETE)

- [x] Fix `_convert_event()` to handle `content_block_delta` events (streaming tokens)
- [x] Add `content_block_start` handler for tool_use and thinking block detection
- [x] Suppress duplicate text from `assistant` full-turn events
- [x] Multi-turn session management (`--session-id`/`--continue` CLI flags, UUID generation)
- [x] Knowledge persistence: `insert_to_rag` instructions in `_QA_PROMPT`
- [x] Frontend: `session_id` in DeepDiveRequest schema, `sessionId`/`thinking` in DeepDiveState
- [x] Frontend: `DeepDiveChatBubble` component (collapsible thinking, tool calls, streaming markdown, cancel, usage stats)
- [x] Frontend: `ModeToggle` refactored for `variant="chat"` (Chat/Deep Dive) and `variant="search"` (Search/Deep Dive)
- [x] Frontend: `ChatLayout`/`ChatWindow`/`MessageList`/`ChatInput` wired for deep dive mode
- [x] Build passing (14 routes)

## Phase 5.1 — JWT Authentication System (COMPLETE)

- [x] Backend auth core: password hashing (bcrypt), JWT tokens (python-jose), refresh token rotation
- [x] Auth endpoints: POST /login (rate-limited 5/min), /logout, /refresh, GET /me — httpOnly cookies
- [x] Admin endpoints: CRUD users (create, update, deactivate, reset-password) + CRUD roles
- [x] Updated dependencies.py: JWT cookie auth replaces X-User-Id header, AdminUser dependency
- [x] Alembic migration 004: auth columns on users + refresh_tokens table
- [x] Frontend: middleware route protection, login page, auth proxy routes, useAuth hook, AuthProvider
- [x] Frontend: admin panel (/admin/users) with user management table
- [x] Sidebar: logout button, admin link for superadmins
- [x] MCP server: JWT cookie validation on /mcp, /logs, /webhook routes (shared JWT_SECRET_KEY)
- [x] Dashboard: credentials:"include" on all fetches, 401→redirect to login, auth check on mount
- [x] Rate limiting: slowapi on login endpoint (5/minute per IP)
- [x] Docker: JWT_SECRET_KEY + ADMIN_DEFAULT_PASSWORD env vars in docker-compose.yml
- [x] Default admin creds: admin/admin (set via ADMIN_DEFAULT_PASSWORD env var)

## EP-002 — Secure Existing/Admin-Created Accounts (COMPLETE — 2026-05-05)

- [x] Backend: MFA-enabled login returns password-step challenge (`mfa_required`, short-lived `mfa_token`) without auth cookies
- [x] Backend: `/auth/verify-mfa` accepts TOTP or one single-use backup code and then issues auth cookies
- [x] Backend: authenticated users with `mfa_enabled=false` are blocked from normal protected APIs with `403 mfa_required`
- [x] Backend: enrollment/session endpoints remain usable before MFA setup (`me`, `logout`, `refresh`, `totp/setup`, `totp/verify`)
- [x] Frontend: authenticated users without MFA are redirected to `/settings/security`
- [x] Frontend: successful TOTP verify refreshes auth state and unlocks the app without manual reload
- [x] Admin-created users keep onboarding state and must complete MFA before normal app use
- [x] Bootstrap admin remains the first-user path for fresh deployments
- [x] Backup-code acceptance aligned to implementation: 10 single-use codes
- [x] Deferred from EP-002: Request Access, email infrastructure, invite tokens, questionnaire/self-service account creation

## Phase 6.1 — Azure-style Provisioning + Data-Driven Config (COMPLETE — 2026-04-17)

- [x] Dropped Tenant schema — switched to one-VPS-per-client model
- [x] Added workspace_name to UserOnboarding + migration 014 rewritten
- [x] New GET /onboarding/config with status=available|coming_soon
- [x] New PUT /onboarding/step/workspace endpoint
- [x] Frontend: WorkspaceStep component (step 2 of 6)
- [x] Frontend: Platform + Tool selects fetch from config API with badges
- [x] Frontend: ProvisioningStep replaces SetupLoadingStep (staged animation + summary card)
- [x] Tour copy updated for empty states
- [x] FALLBACK_CONFIG in types for graceful degradation

## Phase 6 — Enterprise Onboarding & Product Tour (COMPLETE — 2026-04-17)

- [x] SQLAlchemy models: UserOnboarding, Notification, Tenant (schema-only)
- [x] Alembic migration 014 (user_onboarding, notifications, tenants, users.tenant_id)
- [x] Backend API: onboarding routes (status, step saves, complete, setup-status, tour, reset)
- [x] Backend API: notifications routes (list, unread-count, read, read-all)
- [x] /auth/me + /auth/login extended with onboarding_completed + tour_completed
- [x] Admin user creation seeds UserOnboarding row
- [x] Frontend: OnboardingContext + useOnboarding / useNotifications hooks + zod types
- [x] Frontend: 5-step wizard (Welcome, Platforms, Tools, Source, Loading)
- [x] Frontend: OnboardingGuard mounted in Layout
- [x] Frontend: driver.js ProductTour with multi-page router navigation
- [x] Frontend: NotificationBell + dropdown in Sidebar (expanded + collapsed)
- [x] Frontend: Replay Tour button in Sidebar
- [x] data-tour attributes on ChatInput, ModeToggle, RagProjectSelector, pages
- [ ] Wire real ZIP upload for SourceUploadStep (currently records filename only)
- [ ] Replace `run_background_setup` stub with real ingest pipeline
- [ ] Wire Tenant / subdomain routing into request flow

## Phase 7 — Future

- [ ] Integration Agent (Slack, SharePoint, etc.) — deferred until company tooling known
- [ ] E2E testing with real Docker services
- [ ] Frontend: document selector for chat-with-doc
- [ ] Re-index CLI tool

## Quick Stats

| Component | Files | Tests | Status |
|-----------|-------|-------|--------|
| Backend | 35+ Python files | 174 passing (non-DB) | Complete |
| Frontend | 20+ TypeScript files | - | Build passing |
| Database | 5 models + migration | 11 passing | Complete |

See `PROGRESS_LOG.md` for detailed session history.
