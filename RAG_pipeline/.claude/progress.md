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

## Phase 3 — Future

- [ ] Integration Agent (Slack, SharePoint, etc.) — deferred until company tooling known
- [ ] JWT/OAuth authentication
- [ ] E2E testing with real Docker services
- [ ] Frontend: document selector for chat-with-doc
- [ ] Re-index CLI tool

## Quick Stats

| Component | Files | Tests | Status |
|-----------|-------|-------|--------|
| Backend | 35+ Python files | 192 passing | Complete |
| Frontend | 20+ TypeScript files | - | Build passing |
| Database | 5 models + migration | 11 passing | Complete |

See `PROGRESS_LOG.md` for detailed session history.
