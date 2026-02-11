# Current State

**Status**: MVP Complete (Phase 1) + LLM Query Router + Search Recall + UI/UX Formatting + Strict Docs-Only RAG + Unified Retrieval Config + Reasoning Logs + Map-Reduce Summarization + Document Re-Upload Fix + Conversation Memory Fix + Context-First Chat + [NEED_SEARCH] Leak Fix
**Last session**: 2026-02-10
**Last completed**: Fixed [NEED_SEARCH] leak to frontend + removed LLM search reformulation. [NEED_SEARCH] marker now only in CONTEXT_FIRST_SYSTEM_PROMPT (not main prompt). Safety strip before saving. Search uses cleaned_query directly.

## What's Done

### Backend (154 tests passing)
- FastAPI app with CORS, lifespan events
- Database: SQLAlchemy async models (User, Role, Document, QueryLog, IngestionJob)
- Ingestion: PDF/DOCX/PPTX/XLSX parsing, token-based chunking (256/32 overlap), **replace-on-re-upload** (same filename deletes old vectors + DB record)
- Retrieval: Ollama embeddings (nomic-embed-text 768-dim), Qdrant vector store, threshold=0.4, top_k=3
- Generation: Ollama LLM client, prompt builder (4096 token context), RAG chain with intent routing
- **Intent classifier**: LLM-based router with `QueryType` enum (`QA` | `SUMMARY`) and `Action` enum (`SEARCH` | `RESPOND`)
- **Map-reduce summarization**: Full-document summaries via `resolve_document_id()` → `retrieve_document_chunks()` → parallel `map_summarize()` → `reduce_summaries()`
- **Source merging**: `merge_sources_by_document()` with `max_documents` param (default=1)
- **Numbered citations**: LLM instructed to use `[1]`, `[2]` format; context uses `[Reference N: filename]` headers
- RBAC: Header-based auth (X-User-Id), role-to-group mapping for Qdrant filtering
- API routes: /health, /api/v1/query, /api/v1/query/debug, /api/v1/documents, /api/v1/ingest
- Conversations: /api/v1/conversations with conversational RAG pipeline
- Comprehensive INFO-level diagnostic logging across full RAG pipeline

### Frontend (build passing)
- Next.js 15 App Router with BFF pattern (route.ts → FastAPI)
- React Query for server state, Zod for validation
- Tailwind + lucide-react for styling
- **Markdown rendering**: `react-markdown` + `remark-gfm` for rich answer display
- **Clickable citations**: `[N]` in answers scroll to `#source-N` anchors
- **Loading animations**: Rotating messages with fade transitions
- **Document-level sources**: Reference number badges, no chunk_index exposed
- Pages: / (search), /chat (conversations), /documents (upload/list/delete), /settings (health)
- Components: SearchBar, ResponseDisplay, SourceCitation, DocumentUpload, Sidebar, ChatWindow, MessageBubble, MarkdownContent, LoadingMessages

## To Run

```bash
docker-compose up -d          # Qdrant + PostgreSQL
cd backend && alembic upgrade head && uvicorn src.api.main:app --reload
cd frontend && npm run dev    # http://localhost:3000
```

## Recent Changes

- **[NEED_SEARCH] leak fix + remove reformulation** (2026-02-10): Fixed `[NEED_SEARCH]` leaking to frontend. Separated into `CONTEXT_FIRST_SYSTEM_PROMPT` (has marker) vs `CONVERSATIONAL_SYSTEM_PROMPT` (no marker). Added safety strip. Removed `reformulate_search_query()` — search uses `cleaned_query` directly. 154 tests passing.
- **Context-first chat + LLM search reformulation** (2026-02-10): Follow-ups answered from previous context without search. LLM uses `[NEED_SEARCH]` marker when it needs fresh data. Replaced `expand_query()` with LLM-based `reformulate_search_query()`. 15 new tests, 157 total.
- **Conversation memory fix** (2026-02-10): Fixed 3 issues causing chat to lose context: (1) `expand_query()` now includes assistant messages (not just user); (2) summary path calls `expand_query()` before `resolve_document_id()`; (3) `analyze_query()` receives conversation context for pronoun resolution. 2 new tests, 142 total.
- **Map-reduce summarization** (2026-02-10): Added `QueryType` enum to intent classifier, created `map_reduce.py` with full pipeline (resolve → retrieve all chunks → parallel map → reduce). Integrated into both `rag_chain.py` and `conversational_rag.py`. 14 new map-reduce tests.
- **Document re-upload replacement** (2026-02-10): Added `_replace_existing_document()` to pipeline.py — checks for same filename, deletes old Qdrant vectors + DB record before fresh ingestion. 2 new pipeline tests.
- **merge_sources max_documents** (2026-02-10): Added `max_documents` param (default=1) to `merge_sources_by_document()`. Fixed pre-existing test that assumed all docs returned. 3 new merge tests.
- **Phase 3 documented** (2026-02-10): Multi-hop reasoning, chunking strategy, embedding drift — all documented in PROJECT_MEMORY.md with approaches, effort estimates, and priority order.

## Blockers

- No Docker on dev machine — can't test E2E locally, use SQLite for unit tests
- Ollama required locally for embeddings + LLM

## Next Steps (Phase 2+3)

- [ ] E2E testing with real Docker services
- [ ] Integration Agent (when company tooling is known)
- [ ] JWT/OAuth authentication
- [ ] Phase 3: Paragraph-aware chunking → Query expansion glossary → Multi-hop decomposition

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
