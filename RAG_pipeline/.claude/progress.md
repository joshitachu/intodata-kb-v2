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
- [x] Search parameter tuning (top_k=5, threshold=0.3, rerank_top_n=20, prefetch_limit=30, max_documents=3)
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

## Phase 6 — Future

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
