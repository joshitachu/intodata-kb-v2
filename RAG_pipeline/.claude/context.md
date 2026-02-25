# Current State

**Status**: MVP Complete (Phase 1) + All Phase 1.5 improvements + Enterprise Retrieval Quality Upgrade (Phase 2) + Configurable LLM Provider Chain + Streaming Chat UI + RAG Retrieval Quality Overhaul (Phase 3) + Latency Optimization (Phase 3.2) + **Production Bug Fixes + Voyage AI Enablement (Phase 3.3)**
**Last session**: 2026-02-24
**Last completed**: Auto-detect Qdrant vector dimension mismatch at startup — `ensure_collection()` now checks existing collection dim vs `get_embedding_dim()` and recreates if they differ.

## What's Done

### Backend (174 non-DB tests passing)
- FastAPI app with CORS, lifespan events
- Database: SQLAlchemy async models (User, Role, Document, QueryLog, IngestionJob)
- Ingestion: PDF/DOCX/PPTX/XLSX parsing, **semantic chunking** (paragraph-aware, heading-aware, slide markers), **replace-on-re-upload**, **sentence-boundary chunking** (overlap=100)
- Retrieval:
  - **Hybrid search**: Dense (provider chain: Voyage AI → Ollama) + Sparse (hash-based, fixed TF-IDF bug) with RRF fusion
  - **Cross-encoder reranking**: sentence-transformers ms-marco-MiniLM-L-12-v2 (upgraded from L6-v2)
  - **Query expansion**: LLM-generated alternatives via provider chain (more-specific + more-general)
  - **Chat-with-document**: Optional document_ids filter on all search functions
  - Named vectors in Qdrant (dense + sparse), payload indexes for RBAC + document_id
  - **Tuned parameters**: top_k=5, threshold=0.3, rerank_top_n=20, prefetch_limit=30, max_documents=3
- Generation: **Multi-provider LLM client** (Kimi K2.5 → Ollama fallback chain, sync + **streaming SSE**), prompt builder (6144 token context with per-source truncation), RAG chain with intent routing
- **Embedding provider chain**: Voyage AI (voyage-4-large, 1024-dim) → Ollama (nomic-embed-text, 768-dim) fallback
- **Intent classifier**: Zero-LLM heuristic router (keyword-based summary detection, all queries go through SEARCH)
- **Map-reduce summarization**: Full-document summaries via parallel map → reduce
- **Numbered citations**: LLM uses `[1]`, `[2]` format with `[Reference N: filename]` headers
- RBAC: Header-based auth (X-User-Id), role-to-group mapping for Qdrant filtering
- API routes: /health, /api/v1/query, /api/v1/query/debug, /api/v1/query/stream (SSE), /api/v1/documents, /api/v1/ingest
- Conversations: /api/v1/conversations with conversational RAG pipeline
- Feature flags: `hybrid_search_enabled`, `reranker_enabled`, `query_expansion_enabled`

### Frontend (build passing)
- Next.js 15 App Router with BFF pattern (route.ts → FastAPI)
- React Query for server state, Zod for validation
- Tailwind + lucide-react for styling
- **Markdown rendering**: `react-markdown` + `remark-gfm` for rich answer display
- **Clickable citations**: `[N]` in answers scroll to `#source-N` anchors
- Pages: / (search), /chat (conversations), /documents (upload/list/delete), /settings (health)

## To Run

```bash
docker-compose up -d          # Qdrant + PostgreSQL
cd backend && alembic upgrade head && uvicorn src.api.main:app --reload
cd frontend && npm run dev    # http://localhost:3000
```

## Recent Changes

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

- [x] **DONE**: Added Kimi + Voyage API key placeholders to `.env` — replace with real keys
- [x] **DONE**: Fixed Voyage AI 400 error (removed unsupported `dimensions` param)
- [x] **DONE**: Added reranker min-score filtering + warmup at startup
- [x] **DONE**: Fixed sync/async crash in streaming endpoint
- [x] **DONE**: Auto-detect dimension mismatch in `ensure_collection()` — backend now auto-deletes and recreates collection on startup if dim changed
- [ ] E2E testing with real Docker services + new providers
- [ ] Frontend: Update Zod schemas for new default top_k=5, threshold=0.3
- [ ] Frontend: document selector for chat-with-doc
- [ ] Integration Agent (when company tooling is known)
- [ ] JWT/OAuth authentication

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
