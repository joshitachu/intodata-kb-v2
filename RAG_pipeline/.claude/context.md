# Current State

**Status**: MVP Complete (Phase 1) + All Phase 1.5 improvements + Enterprise Retrieval Quality Upgrade (Phase 2) + Configurable LLM Provider Chain + Streaming Chat UI
**Last session**: 2026-02-11
**Last completed**: End-to-end streaming responses for chat UI. Backend `_prepare_rag_context()` shared helper + `run_conversational_rag_stream()`. Frontend `useStreamMessage` hook with SSE parsing, optimistic user messages, blinking cursor. 192 tests passing.

## What's Done

### Backend (192 tests passing)
- FastAPI app with CORS, lifespan events
- Database: SQLAlchemy async models (User, Role, Document, QueryLog, IngestionJob)
- Ingestion: PDF/DOCX/PPTX/XLSX parsing, **semantic chunking** (paragraph-aware, heading-aware, slide markers), **replace-on-re-upload**
- Retrieval:
  - **Hybrid search**: Dense (Ollama nomic-embed-text 768-dim) + Sparse (TF-IDF via sklearn) with RRF fusion
  - **Cross-encoder reranking**: sentence-transformers ms-marco-MiniLM-L6-v2
  - **Query expansion**: LLM-generated alternative phrasings, multi-query search with deduplication
  - **Chat-with-document**: Optional document_ids filter on all search functions
  - Named vectors in Qdrant (dense + sparse), payload indexes for RBAC + document_id
- Generation: **Multi-provider LLM client** (Groq → OpenRouter → Ollama fallback chain, sync + **streaming SSE**), prompt builder (4096 token context), RAG chain with intent routing
- **Intent classifier**: LLM-based router with `QueryType` enum (`QA` | `SUMMARY`) and `Action` enum (`SEARCH` | `RESPOND`)
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

- **Streaming Chat UI** (2026-02-11): End-to-end SSE streaming for conversations. Backend shared `_prepare_rag_context()` helper + `run_conversational_rag_stream()`. Frontend `useStreamMessage` hook, SSE proxy, optimistic user messages, blinking cursor. 192 tests passing.
- **Configurable LLM Provider Chain** (2026-02-11): 3-tier fallback chain Groq → OpenRouter → Ollama. `llm_providers` config list, lazy imports, same public API. 8 new tests, 192 total.
- **Enterprise Retrieval Quality Upgrade** (2026-02-11): 5-phase implementation:
  1. Hybrid search (dense + sparse TF-IDF with RRF fusion via Qdrant Prefetch)
  2. Cross-encoder reranking (ms-marco-MiniLM-L6-v2, ~60ms for 15 candidates)
  3. Semantic chunking (paragraph-aware, DOCX heading markers, PPTX slide markers)
  4. Query expansion (LLM-generated alternatives, multi-query search + dedup)
  5. Streaming SSE + chat-with-document (document_ids filter)
  - New files: `sparse_embedder.py`, `reranker.py`, `semantic_chunker.py`, `query_expander.py`
  - Modified: `vector_store.py`, `search.py`, `pipeline.py`, `parser.py`, `rag_chain.py`, `conversational_rag.py`, `llm_client.py`, `query.py`, `config.py`, `schemas.py`
  - 32 new tests, 186 total passing

## Blockers

- No Docker on dev machine — can't test E2E locally, use SQLite for unit tests
- Ollama required locally for embeddings + LLM
- `fastembed` package requires Rust toolchain (not available) — using sklearn TF-IDF instead

## Next Steps

- [ ] **CRITICAL**: Delete existing Qdrant collection and re-ingest all documents (schema changed to named vectors)
- [ ] E2E testing with real Docker services
- [x] Frontend: SSE streaming UI for conversations (done)
- [ ] Frontend: document selector for chat-with-doc
- [ ] Integration Agent (when company tooling is known)
- [ ] JWT/OAuth authentication

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
