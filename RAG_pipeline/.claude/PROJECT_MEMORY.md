# IntoData KB — Project Memory

> Read this at the start of every session. Update after every session.

## Architecture Decisions

| Decision | Choice | Rationale | Date |
|----------|--------|-----------|------|
| No LangChain | Direct Ollama client | Full control, fewer deps | 2026-02-05 |
| Embeddings | Ollama nomic-embed-text (768-dim) | Same interface as LLM, good quality | 2026-02-05 |
| LLM | Ollama (Mistral or Llama 3) | Local, no API costs | 2026-02-05 |
| Vector DB | Qdrant (Docker) | HNSW, payload filtering for RBAC | 2026-02-05 |
| Relational DB | PostgreSQL (async) | Users, roles, doc metadata, audit | 2026-02-05 |
| Auth (Phase 1) | Header-based (X-User-Id) | Simple for dev; JWT/OAuth later | 2026-02-05 |
| Frontend | Next.js App Router + Tailwind + shadcn/ui | SSR, route handlers as BFF proxy | 2026-02-05 |
| State mgmt | React Query | Server state caching | 2026-02-05 |
| API pattern | BFF via Next.js route.ts → FastAPI | Client never calls FastAPI directly | 2026-02-05 |
| Integrations | Phase 2 (deferred) | Company tooling unknown | 2026-02-05 |
| Intent routing | LLM router (gemma3:4b) | Replaces regex — more robust routing + typo correction; safe fallback to SEARCH on failure | 2026-02-10 |
| Search threshold | 0.4 (was 0.7→0.4→0.2→0.4) | 0.2 too permissive, 0.58 too strict; 0.4 is the sweet spot for nomic-embed-text | 2026-02-10 |
| Chunk size | 256 tokens / 32 overlap | Smaller chunks = more focused embeddings = better similarity scores | 2026-02-09 |
| Context window | 4096 tokens | Fits ~16 chunks of 256 tokens | 2026-02-09 |
| Source merging | Document-level (not chunk-level) | Users don't need chunk granularity; numbered refs enable clickable UI links | 2026-02-09 |
| Markdown rendering | react-markdown + remark-gfm | Standard, lightweight, supports GFM tables/lists | 2026-02-09 |
| Summarization | Map-reduce (not stuff-all-in-context) | top_k=3 can't summarize a 50-chunk document; map-reduce scales to any size | 2026-02-10 |
| Document re-upload | Replace (not version) | Versioning adds UI complexity, storage costs; KB users always want latest version | 2026-02-10 |
| Context-first chat | LLM [NEED_SEARCH] marker | No new intent actions; main LLM decides if context is sufficient or needs fresh search | 2026-02-10 |
| Search reformulation | **Removed** — use cleaned_query directly | LLM reformulation added complexity without clear benefit; intent classifier already produces cleaned_query | 2026-02-10 |
| [NEED_SEARCH] isolation | Separate CONTEXT_FIRST_SYSTEM_PROMPT + safety strip | Prevents marker from leaking into final responses shown to users | 2026-02-10 |
| Hybrid search | Dense (Ollama) + Sparse (TF-IDF) with RRF fusion | Captures both semantic similarity and keyword matches; RRF is parameter-free | 2026-02-11 |
| Sparse embeddings | sklearn TfidfVectorizer (not fastembed) | fastembed needs Rust toolchain; sklearn TF-IDF provides adequate keyword matching | 2026-02-11 |
| Named vectors | Qdrant named vectors (dense + sparse) | Required for hybrid search; collection must be recreated (breaking change) | 2026-02-11 |
| Cross-encoder reranking | ms-marco-MiniLM-L6-v2 via sentence-transformers | ~80MB model, ~60ms for 15 candidates on CPU; significant precision improvement | 2026-02-11 |
| Semantic chunking | Paragraph-aware with heading/slide markers | Better chunks = better embeddings = better retrieval; XLSX delegates to mechanical | 2026-02-11 |
| Query expansion | LLM-generated alternatives + multi-query search | Expands abbreviations and synonyms; deduplicates + reranks with original query | 2026-02-11 |
| Streaming | SSE via generate_answer_stream() | Tokens streamed as they arrive; sources sent first; summary queries fall back to non-streaming | 2026-02-11 |
| Feature flags | hybrid_search_enabled, reranker_enabled, query_expansion_enabled | All features toggleable for A/B testing and gradual rollout | 2026-02-11 |
| LLM provider chain | Groq → OpenRouter → Ollama fallback via `llm_providers` list | Free cloud APIs first, local fallback; single env var to switch to paid API | 2026-02-11 |
| Streaming conversations | Shared `_prepare_rag_context()` + `run_conversational_rag_stream()` | Eliminates duplication between streaming/non-streaming; precomputed answers for RESPOND/SUMMARY, stream for QA | 2026-02-11 |

## Code Patterns

- Python: type hints everywhere, Pydantic validation, async for I/O, no bare except
- TypeScript: strict mode (never `any`), Zod for API validation, handle all states
- RBAC: Filter EVERY Qdrant query by user groups (payload metadata)
- Tests: Write alongside implementation, `@pytest.mark.integration` for Docker-dependent tests

## Completed Tasks

| Task | Date | Notes |
|------|------|-------|
| Project scaffolding | 2026-02-05 | All dirs, placeholders, configs created |
| Database Agent | 2026-02-05 | Config, models, session, Alembic, 11 tests passing |
| Backend Agent | 2026-02-05 | Full RAG pipeline, all API routes, RBAC, 48 tests passing |
| Frontend Agent | 2026-02-06 | Next.js App Router, route handlers, components, hooks, build passing |
| Qdrant payload index fix | 2026-02-06 | Added KEYWORD index on `allowed_groups` + INTEGER index on `document_id` in `ensure_collection()` |
| CLAUDE.md logging overhaul | 2026-02-06 | Added mandatory logging rules, structured format, read-before-work policy |
| Intent routing for RAG pipeline | 2026-02-06 | Regex classifier skips search for chitchat, LLM fallback for no-results. 156 tests passing |
| Search recall improvements | 2026-02-09 | Threshold 0.2, top_k 10, chunk size 256, context 4096, debug endpoint, pipeline logging. 157 tests passing |
| UI/UX & response formatting | 2026-02-09 | DocumentSource schema, merge_sources_by_document(), numbered citations [1], markdown rendering, LoadingMessages, clickable citation links. 164 tests passing |
| Fix frontend conversation 500s | 2026-02-09 | Cleared stale .next cache, added 204 handling in fastapiFetch, hardened useConversations error handling for non-JSON responses |
| LLM query router | 2026-02-10 | Replaced regex intent classifier with LLM-based router (gemma3:4b). Routes search/respond + typo correction. 48 generation tests passing |
| Retrieval config unification | 2026-02-10 | Unified top_k=3/threshold=0.4 across all defaults, silenced SQLAlchemy echo, added reasoning logs |
| Map-reduce summarization | 2026-02-10 | QueryType enum (QA/SUMMARY), map-reduce pipeline for full-document summaries, integrated into both RAG chains. 140 tests passing |
| Document re-upload replacement | 2026-02-10 | `_replace_existing_document()` deletes old Qdrant vectors + DB record when same filename is re-uploaded |
| merge_sources max_documents | 2026-02-10 | Added `max_documents` param to `merge_sources_by_document()` (default=1). Fixed pre-existing test that assumed all docs returned |
| Context-first chat | 2026-02-10 | Follow-ups answered from previous context (0 searches). LLM uses [NEED_SEARCH] marker for fallback. Replaced expand_query() with LLM-based reformulate_search_query(). 157 tests passing |
| Fix [NEED_SEARCH] leak | 2026-02-10 | Separated CONTEXT_FIRST_SYSTEM_PROMPT from main prompt, added safety strip, removed reformulate_search_query(). 154 tests passing |
| Enterprise Retrieval Quality Upgrade | 2026-02-11 | 5-phase: hybrid search (RRF), cross-encoder reranking, semantic chunking, query expansion, streaming SSE + chat-with-doc. 186 tests passing |
| Configurable LLM Provider Chain | 2026-02-11 | Groq → OpenRouter → Ollama fallback. `llm_providers` config list. 192 tests passing |

## Pending Tasks

- [x] Database Agent: models, migrations, session
- [x] Backend Agent: RAG pipeline, API routes, RBAC — 48 tests passing
- [x] Frontend Agent: Next.js app, route handlers, components — build passing
- [ ] Integration Agent: Phase 2 (deferred)

## Phase 3 — Quality Issues (Status)

| Priority | Issue | Status | Notes |
|----------|-------|--------|-------|
| 1 | Paragraph-aware chunking | **DONE** | Semantic chunker with paragraph merging, heading/slide markers |
| 2 | Query expansion | **DONE** | LLM-generated alternatives + multi-query search |
| 3 | Multi-hop query decomposition | Pending | Could build on query expansion infra |
| 4 | Cross-encoder re-ranking | **DONE** | ms-marco-MiniLM-L6-v2 via sentence-transformers |
| 5 | Section-aware chunking | **DONE** | DOCX headings + PPTX slide markers |
| 6 | Re-index CLI tool | Pending | Needed for any embedding/chunk/schema change |

**Multi-hop reasoning**: User asks "Compare X in doc A with Y in doc B" — current system does one search. Solution: decompose into sub-queries, search each independently, merge + deduplicate, then synthesize. Alternative: agentic RAG (LLM triggers follow-up searches mid-generation) — defer until Ollama tool-use matures.

**Chunking strategy**: Current 256-token sliding window is mechanical. Tier 1: paragraph-aware (split on `\n\n`). Tier 2: section-aware (detect headers). Tier 3: format-specific (PDF pages, DOCX headings, PPTX slides). Any chunking change requires full re-ingestion.

**Embedding drift / specialized vocab**: `nomic-embed-text` struggles with internal jargon. Options: (A) query expansion glossary (easiest, 80/20), (B) cross-encoder re-ranking, (C) fine-tuned embeddings (overkill), (D) model swap + re-index.

## Blockers

None currently.

## Lessons Learned / Gotchas

- PostgreSQL `JSONB` → generic `JSON` for SQLite test compat (still works as JSONB on Postgres)
- SQLite needs `PRAGMA foreign_keys=ON` for CASCADE tests — added to conftest.py
- SQLAlchemy needs `passive_deletes=True` on parent relationship for DB-level CASCADE to work
- `setuptools.backends._legacy:_Backend` doesn't exist on Python 3.14 — use `setuptools.build_meta`
- Python 3.14 is installed on this machine (not 3.11)
- Zod `.default()` doesn't make field optional in `z.infer` — use `.optional().default()` + `z.input` for request types
- OneDrive paths can cause build issues — delete `.next` cache if build fails with manifest errors
- Qdrant requires explicit `create_payload_index()` on any field used in `FieldCondition` filters — just creating the collection is not enough
- `create_payload_index()` is idempotent — safe to call on every startup without checking if index exists
- `nomic-embed-text` cosine similarity for natural language queries against relevant chunks: typically 0.2-0.5. Threshold must be ≤0.2 to avoid dropping borderline-relevant results
- Changing chunk size requires re-ingestion of all existing documents
- Always use `res.text()` + `try { JSON.parse(...) } catch {}` for error responses — never assume error bodies are valid JSON (Next.js can return HTML error pages from webpack crashes)
- `fastapiFetch` must check `res.status === 204` before calling `res.json()` — DELETE endpoints return empty bodies
- When mocking `AsyncSession.execute()` for `select()` queries, the mock must return a sync `MagicMock` (not `AsyncMock`) for `.scalars().first()` chain since `execute()` is already awaited
- Map-reduce: use `Semaphore(3)` for parallel LLM calls to avoid overwhelming local Ollama
- `fastembed` requires Rust toolchain (py-rust-stemmers) — use sklearn TF-IDF as fallback for sparse embeddings
- Qdrant `query_points()` with named vectors: use `query=embedding, using="dense"` (not `query=NamedVector(...)`)
- Qdrant hybrid search: use `Prefetch` with `query=raw_vector, using="dense"` and `query=SparseVector(...), using="sparse"`, then `query=FusionQuery(fusion="rrf")`
- When adding feature flags that change dispatch paths (e.g., `search_multi_query` vs `search_similar`), existing tests need settings mocks to disable new features
- Cross-encoder models are lazy-loaded singletons (~80MB download on first use); mock `_get_model()` in tests
- Provider registry must resolve functions at call time (not at import time) for `unittest.mock.patch()` to work — use string-based registry + `getattr(sys.modules[__name__], func_name)`
- Semantic chunker: single-paragraph texts that fit in one chunk should bypass min_tokens filter (otherwise empty returns for short documents)
- Python 3.14 has limited package compatibility — many packages don't have pre-built wheels yet

## Environment

- Platform: Windows (OneDrive path)
- Python: 3.14.3 (installed), pyproject says >=3.11
- Node: v24.13.0 (installed)
- Docker: Required for Qdrant + PostgreSQL (not available on dev machine)
- Ollama: Required locally for embeddings + LLM

## File Structure Summary

```
intodata-kb/
├── backend/
│   ├── src/
│   │   ├── api/          # FastAPI app, routes, dependencies
│   │   ├── db/           # SQLAlchemy models, session
│   │   ├── ingestion/    # Parser, chunker, metadata, pipeline
│   │   ├── retrieval/    # Embedder, vector store, search
│   │   ├── generation/   # LLM client, prompt builder, RAG chain
│   │   ├── rbac/         # Auth, permissions
│   │   ├── config.py     # Pydantic Settings
│   │   └── schemas.py    # Request/response models
│   ├── tests/            # 192 tests across all modules
│   ├── alembic/          # Database migrations
│   └── pyproject.toml
├── frontend/
│   ├── app/              # Next.js pages + API route handlers
│   ├── components/       # React components
│   ├── hooks/            # React Query hooks
│   ├── lib/              # Utils, FastAPI client
│   ├── types/            # Zod schemas
│   └── package.json
├── docker-compose.yml    # Qdrant + PostgreSQL
└── .claude/              # Project tracking files
```

## How to Resume Development

1. Read `context.md` for current state
2. Read `PROJECT_MEMORY.md` (this file) for architecture and patterns
3. Check `PROGRESS_LOG.md` for what was done in previous sessions
4. Run backend tests: `cd backend && python -m pytest tests/ -v`
5. Run frontend build: `cd frontend && npm run build`
