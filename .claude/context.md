# Current State

**Status**: MVP Complete (Phase 1) + LLM Query Router + Search Recall + UI/UX Formatting + Strict Docs-Only RAG + Unified Retrieval Config + Reasoning Logs
**Last session**: 2026-02-10
**Last completed**: Unified retrieval defaults (top_k=3, threshold=0.4), silenced SQLAlchemy SQL echo, added structured reasoning logs to both RAG pipelines

## What's Done

### Backend (164 tests passing)
- FastAPI app with CORS, lifespan events
- Database: SQLAlchemy async models (User, Role, Document, QueryLog, IngestionJob)
- Ingestion: PDF/DOCX/PPTX/XLSX parsing, token-based chunking (256/32 overlap)
- Retrieval: Ollama embeddings (nomic-embed-text 768-dim), Qdrant vector store, threshold=0.4, top_k=3
- Generation: Ollama LLM client, prompt builder (4096 token context), RAG chain with intent routing
- **Source merging**: `merge_sources_by_document()` groups chunks by document, assigns numbered references
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

- **Retrieval config + reasoning logs** (2026-02-10): Unified top_k=3/threshold=0.4 across schemas, search.py, conversational_rag.py. Silenced SQLAlchemy SQL echo. Added structured reasoning logs (Retrieved Chunks, LLM Input Preview, Logic trace with token count) to both RAG pipelines.
- **LLM query router** (2026-02-10): Replaced regex `classify_intent()` with LLM-based `analyze_query()` using `gemma3:4b`. Routes search vs. respond + fixes typos in queries. Safe fallback to SEARCH on failure. 48 generation tests passing.
- **Strict docs-only RAG mode** (2026-02-10): Unified chitchat + no-results into single `NO_RESULTS_PROMPT`. Removed separate greeting/thanks/goodbye prompts. Top-1 source only from `merge_sources_by_document()`. Chat input stays visible during submission. Hidden "0 sources" metadata.
- **Fix: frontend conversation 500 errors** (2026-02-09): Cleared stale `.next` webpack cache (root cause of `Cannot find module './331.js'`). Added 204 No-Content handling in `fastapiFetch`. Hardened all 6 fetch functions in `useConversations.ts` to handle non-JSON error responses without secondary crashes.
- **Fix: conversations 500 error** (2026-02-09): Fixed `get_conversation` endpoint — replaced `Source` with `DocumentSource` deserialization in `conversations.py`. Added backward-compatible `_deserialize_sources()` helper for old DB records missing `reference_number`.
- **UI/UX formatting improvements** (2026-02-09): Added `DocumentSource` schema with reference numbers, `merge_sources_by_document()` utility, markdown system prompts with `[1]` citation format, `MarkdownContent` component with clickable citation links, `LoadingMessages` with rotating text + fade animation, document-level source cards with reference badges. 164 tests passing.
- **Search recall improvements** (2026-02-09): Threshold 0.4→0.2, top_k 5→10, MAX_CONTEXT_TOKENS 3000→4096, chunk size 500→256. Added debug endpoint and pipeline logging.
- **Intent routing**: LLM-based router (`gemma3:4b`) classifies search vs. respond and fixes typos. Replaces old regex classifier. No-results queries get LLM-generated helpful responses.

## Blockers

- No Docker on dev machine — can't test E2E locally, use SQLite for unit tests
- Ollama required locally for embeddings + LLM

## Next Steps (Phase 2)

- [ ] E2E testing with real Docker services
- [ ] Integration Agent (when company tooling is known)
- [ ] JWT/OAuth authentication

See `PROJECT_MEMORY.md` for architecture decisions and `PROGRESS_LOG.md` for session history.
