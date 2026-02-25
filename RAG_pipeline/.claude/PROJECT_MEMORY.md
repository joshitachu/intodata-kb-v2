# IntoData KB — Project Memory

> Read this at the start of every session. Update after every session.

## Architecture Decisions

| Decision | Choice | Rationale | Date |
|----------|--------|-----------|------|
| No LangChain | Direct Ollama client | Full control, fewer deps | 2026-02-05 |
| Embeddings | Voyage AI voyage-4-large (1024-dim) → Ollama fallback | +14% over OpenAI v3 Large, first 200M free; was Ollama nomic-embed-text (768-dim) | 2026-02-23 |
| LLM | Ollama (Mistral or Llama 3) | Local, no API costs | 2026-02-05 |
| Vector DB | Qdrant (Docker) | HNSW, payload filtering for RBAC | 2026-02-05 |
| Relational DB | PostgreSQL (async) | Users, roles, doc metadata, audit | 2026-02-05 |
| Auth (Phase 1) | Header-based (X-User-Id) | Simple for dev; JWT/OAuth later | 2026-02-05 |
| Frontend | Next.js App Router + Tailwind + shadcn/ui | SSR, route handlers as BFF proxy | 2026-02-05 |
| State mgmt | React Query | Server state caching | 2026-02-05 |
| API pattern | BFF via Next.js route.ts → FastAPI | Client never calls FastAPI directly | 2026-02-05 |
| Integrations | Phase 2 (deferred) | Company tooling unknown | 2026-02-05 |
| Intent routing | Zero-LLM heuristic (was LLM router) | All queries → SEARCH; keyword regex for summary detection. LLM call was pure overhead — Qdrant search is ~50ms | 2026-02-23 |
| Search threshold | 0.3 (was 0.7→0.4→0.2→0.4→0.3) | 0.3 suits API embeddings (Voyage AI); was 0.4 for nomic-embed-text | 2026-02-23 |
| Kimi thinking mode | Disabled via `extra_body` | Thinking mode adds 8-25s/call with no RAG quality benefit; temp must be exactly 0.6 in instant mode (API rejects all other values with 400) | 2026-02-23 |
| HTTP clients | Singleton with lazy init | Reuses TCP connections, avoids per-request overhead | 2026-02-23 |
| Chunk size | 256 tokens / 100 overlap | Larger overlap = better coherence; sentence-boundary snapping prevents mid-sentence splits | 2026-02-23 |
| Context window | 6144 tokens / per-source truncation | Was 4096 with drop-entire-source; now all sources represented | 2026-02-23 |
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
| Kimi K2.5 LLM provider | OpenAI-compatible API (moonshot.ai), primary in chain | 1T-param MoE (32B active), 256K context, $0.60/M input — very cost-effective | 2026-02-23 |
| Voyage AI embeddings | Provider chain: Voyage AI → Ollama fallback | voyage-4-large (1024-dim), +14% over OpenAI v3 Large on MTEB, first 200M tokens free | 2026-02-23 |
| Sparse embeddings | Hash-based (not TF-IDF) | TF-IDF had vocabulary mismatch bug between ingestion and query; hash-based is stateless and deterministic | 2026-02-23 |
| Context truncation | Per-source truncation (not drop) | All sources represented in LLM context; truncated text with `[...]` beats dropped text | 2026-02-23 |
| Reranker upgrade | ms-marco-MiniLM-L-12-v2 (33M params) | Outperforms L6-v2 (22M) and much larger models while staying fast on CPU | 2026-02-23 |
| Sentence-boundary chunking | Snap to sentence endings in last 20% | Prevents mid-sentence splits that degrade embedding quality | 2026-02-23 |
| LLM for routing/expansion | **Routing removed** (heuristic); expansion disabled by default | Intent classifier eliminated (0 LLM calls). Query expansion `False` by default — re-enable via `.env` if quality > speed | 2026-02-23 |

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
| RAG Retrieval Quality Overhaul | 2026-02-23 | Fixed sparse embedding bug, added Kimi K2.5 + Voyage AI providers, upgraded reranker, tuned search params, improved chunking. 193 tests passing |
| Eliminate Intent Classifier LLM Call | 2026-02-23 | Zero-LLM heuristic router, disabled query expansion. QA: 3→1 LLM calls, Chitchat: 2→1. 171 non-DB tests passing |
| Production Bug Fixes + Voyage AI | 2026-02-24 | Fixed reranker min-score, Voyage 400 error, reranker warmup, sync/async crash, added delete_collection(). 174 non-DB tests passing |

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
- **Qdrant Cloud uses port 443, not 6333**: `qdrant-client` defaults to port 6333 for REST connections. Qdrant Cloud exposes API on 443 (standard HTTPS). Always use `https://...cloud.qdrant.io:443` in the URL — without `:443`, the client tries port 6333 and fails with `All connection attempts failed`
- **TF-IDF sparse embeddings are broken for hybrid search**: `TfidfVectorizer` fitted per-call creates different vocabulary mappings between ingestion and query time. Index 0 means different words in different calls. Use hash-based approach: `hash(token) % DIM` for deterministic, stateless sparse vectors.
- **JSON null vs missing key**: `dict.get("key", "default")` returns `None` (not `"default"`) when key exists but has value `null`. Use `(dict.get("key") or "default")` to handle both missing and null.
- **Lazy imports break `unittest.mock.patch`**: When a function is imported inside another function body (`from module import func`), `patch("caller_module.func")` fails with `AttributeError`. Must patch at the definition site: `patch("definition_module.func")`.
- **Embedding provider chain**: Same pattern as LLM provider chain. Voyage AI uses OpenAI-compatible API format — reuse the `openai` SDK with different `base_url`.
- **Kimi K2.5 instant mode temperature**: When thinking is disabled (`extra_body={"thinking": {"type": "disabled"}}`), Kimi K2.5 enforces exactly `temperature=0.6`. Any other value returns HTTP 400: `invalid temperature: only 0.6 is allowed for this model`.
- **OpenAI SDK `max_retries=0` for provider chains**: The `AsyncOpenAI` client defaults to `max_retries=2` with exponential backoff. When using a multi-provider fallback chain, set `max_retries=0` — otherwise the SDK silently burns 10+ seconds retrying a failed call before the outer fallback logic can switch to the next provider.
- **Intent classifier is pure overhead**: Qdrant search is ~50ms. "Skipping search for chitchat" saves 50ms but costs 3-12s for the LLM call. The `NO_RESULTS_PROMPT` handles greetings/chitchat naturally when search returns 0 results. Always route through search.
- **Voyage AI `dimensions` param not supported**: `voyage-4-large` always returns 1024-dim natively. Passing `dimensions=` causes HTTP 400. Don't use the `dimensions` parameter with Voyage API.
- **Reranker min-score is essential**: Without a minimum score threshold, the reranker returns top_k candidates regardless of relevance. Chitchat queries get irrelevant chunks (score -9.13) sent to LLM. Use `reranker_min_score=0.0` to filter negative scores.
- **Pre-load ML models at startup**: Cross-encoder model downloads ~80MB on first use. Lazy loading means the first user request takes +100s. Call `warmup()` in the lifespan handler.
- **Qdrant dimension mismatch on provider switch**: When changing embedding provider (e.g. Ollama 768-dim → Voyage AI 1024-dim), the existing Qdrant collection has the old dimension. `ensure_collection()` auto-detects this and recreates. All documents must be re-ingested after recreation.

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
