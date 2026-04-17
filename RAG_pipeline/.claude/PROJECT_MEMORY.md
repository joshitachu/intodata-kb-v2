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
| Auth | JWT httpOnly cookies (access 60min + refresh 7d) | Stateless, works across both frontends, secure against XSS; was header-based X-User-Id. Access token was 15min but caused session timeouts every 5-10min (cookie max_age expired + tight refresh margin) | 2026-03-05 |
| Frontend | Next.js App Router + Tailwind + shadcn/ui | SSR, route handlers as BFF proxy | 2026-02-05 |
| State mgmt | React Query | Server state caching | 2026-02-05 |
| API pattern | BFF via Next.js route.ts → FastAPI | Client never calls FastAPI directly | 2026-02-05 |
| Integrations | Phase 2 (deferred) | Company tooling unknown | 2026-02-05 |
| Intent routing | Zero-LLM heuristic (was LLM router) | All queries → SEARCH; keyword regex for summary detection. LLM call was pure overhead — Qdrant search is ~50ms | 2026-02-23 |
| Search threshold | 0.3 (was 0.7→0.4→0.2→0.4→0.3) | 0.3 suits API embeddings (Voyage AI); was 0.4 for nomic-embed-text | 2026-02-23 |
| Anti-hallucination | CRITICAL RULES in all system prompts + context boundary footer | LLM was fabricating SQL queries, migration plans, API endpoints not in any document. Explicit rules + footer dramatically reduce fabrication | 2026-03-27 |
| History anchoring | First exchange always kept + recent messages | Was purely recency-based — lost original topic in long conversations, causing self-contradictions | 2026-03-27 |
| Ollama context window | `num_ctx=16384` in both chat calls | gemma3 default is 8K, insufficient for 8192-token context + history + prompt | 2026-03-27 |
| Kimi thinking mode | Disabled via `extra_body` | Thinking mode adds 8-25s/call with no RAG quality benefit; temp must be exactly 0.6 in instant mode (API rejects all other values with 400) | 2026-02-23 |
| HTTP clients | Singleton with lazy init | Reuses TCP connections, avoids per-request overhead | 2026-02-23 |
| Chunk size | 256 tokens / 100 overlap | Larger overlap = better coherence; sentence-boundary snapping prevents mid-sentence splits | 2026-02-23 |
| Context window | 10000 tokens (search) / 8192 (chat) / per-source truncation | Was 6144/3000; chat was severely under-resourced | 2026-03-27 |
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
| Reranker | Voyage AI rerank-2.5 (API) | Local cross-encoder (ms-marco-MiniLM-L-12-v2) scored everything negative on technical docs — domain mismatch. Voyage reranker: calibrated 0-1 scores, same provider as embeddings, no local model (~500MB dep savings) | 2026-02-28 |
| Sentence-boundary chunking | Snap to sentence endings in last 20% | Prevents mid-sentence splits that degrade embedding quality | 2026-02-23 |
| Deep Dive mode | Claude Code CLI via subprocess, SSE streaming | Spawns `claude -p` with Q&A prompt, reads raw scan data via MCP tools, streams answer back to browser. Separate from RAG pipeline — for detailed code-level questions | 2026-03-01 |
| LLM for routing/expansion | **Routing removed** (heuristic); expansion disabled by default | Intent classifier eliminated (0 LLM calls). Query expansion `False` by default — re-enable via `.env` if quality > speed | 2026-02-23 |
| Intent routing (v2) | LLM classifier with `summary` + `conversation_summary` actions | LLM naturally handles typos, rephrasing, multilingual. Conversation context ("N previous messages") is the key signal. Regex kept as fallback only when LLM unavailable | 2026-03-18 |
| Qdrant named vectors | Always pass `using="dense"` to `query_points` | Collection has named vectors (dense + sparse); omitting `using` causes Qdrant 400 error | 2026-03-18 |
| Hindsight memory | Project-scoped banks + tags + gating + distillation | Was flat Q&A storage; now intent-gated (skip chitchat/metadata), LLM-distilled (knowledge statements not raw Q&A), project-scoped with tags, observation consolidation. Zero response latency impact (all fire-and-forget) | 2026-04-14 |

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
| Hindsight Memory Full Integration | 2026-02-28 | Added Hindsight recall+retain to rag_chain.py + /query/stream. Added memory_context to build_messages(). INFO-level diagnostics. 174 tests passing |
| Voyage AI Reranker | 2026-02-28 | Replaced local cross-encoder with Voyage AI rerank-2.5 API. Removed sentence-transformers dep. 174 tests passing |
| Deep Dive Mode | 2026-03-01 | Full-stack: DeepDiveService (subprocess), API endpoints (SSE), BFF routes, hooks, components, search page integration. Build passing |
| JWT Authentication System | 2026-03-04 | Full auth: JWT cookies, bcrypt passwords, refresh token rotation, admin panel, rate limiting, MCP server JWT validation, dashboard auth. Alembic migration 004 |

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
- **Hindsight must be added to ALL query paths**: Three separate query paths exist — `rag_chain.py` (/query), `query.py` (/query/stream), `conversational_rag.py` (conversations). Each needs its own recall+retain integration. Forgetting one means that endpoint silently skips memory. The pattern is always: `ensure_banks → recall → build_memory_context → inject into messages → retain after answer`.
- **Pre-load ML models at startup**: Cross-encoder model downloads ~80MB on first use. Lazy loading means the first user request takes +100s. Call `warmup()` in the lifespan handler.
- **Qdrant dimension mismatch on provider switch**: When changing embedding provider (e.g. Ollama 768-dim → Voyage AI 1024-dim), the existing Qdrant collection has the old dimension. `ensure_collection()` auto-detects this and recreates. All documents must be re-ingested after recreation.
- **NO_RESULTS_PROMPT vs memory injection**: When injecting memory context as a follow-up system message, the main system prompt must explicitly declare that memory context is trusted information. Otherwise, "NEVER make up information or answer from general knowledge" causes the LLM to ignore the injected memories (it treats them as "general knowledge" it should not use).
- **Cross-encoder domain mismatch**: MS MARCO-trained cross-encoders (e.g. `ms-marco-MiniLM-L-12-v2`) return negative scores for technical/enterprise documents — they're trained on web search relevance, not domain-specific content. When `min_score=0.0`, ALL candidates get filtered out and nothing reaches the LLM. Use a domain-agnostic reranker (e.g. Voyage AI `rerank-2.5`) that returns calibrated 0-1 scores.
- **Reranker should be the sole quality gate**: When using a reranker, set the pre-filter (RRF/vector search) threshold to 0.0 so all candidates reach the reranker. The reranker's min_score is the quality gate, not the vector search threshold. Double-filtering drops too many candidates.
- **subprocess stderr deadlock on Windows**: When using `subprocess.Popen` with both `stdout=PIPE` and `stderr=PIPE`, reading stdout first and stderr after can deadlock if stderr's pipe buffer fills (~4KB on Windows). Always drain stderr concurrently using `asyncio.create_task()` (or `asyncio.gather`). See `claude_service.py` and `deep_dive_service.py` for the pattern.
- **Claude CLI `stream-json` text delivery**: Claude CLI may not emit `content_block_delta` events (or may batch them due to Windows pipe buffering). Always handle text from both `content_block_delta` (preferred, incremental) AND `assistant` events (fallback, full turn). Use a `_text_streamed_via_delta` flag to avoid duplication.

- **`backdrop-filter: blur` is one of the most expensive CSS properties** — never use it on per-node or per-group elements in graph views. Even `blur(1px)` forces the GPU to re-blur whatever is behind every element on every paint. For ~10 elements this is a constant frame-rate killer; the visual effect at 1px is also imperceptible. Use a flat gradient or a colored border instead.
- **Infinite CSS animations (`animation: ... infinite`) cause constant repaint loops** — even when the user is idle, the GPU keeps painting every frame. In ReactFlow specifically, dashed/animated edge styles like `.react-flow__edge.animated path { animation: edge-flow ... infinite }` paint forever for every dashed edge in the visible graph. Either remove the infinite animation entirely or scope it to a JS-set `data-animated` attribute opt-in.
- **Per-node mount animation re-fires on every layout change in ReactFlow** — if a CSS class like `.topo-node-enter { animation: node-enter 0.3s }` is applied via `className` on each node component, every direction/compact/expand-collapse toggle remounts nodes and the keyframe re-runs across the entire visible graph at once, producing a visible "wash" of animation. Don't put per-instance entry animations on graph nodes.
- **ReactFlow's `setNodes` reconciles every node** — guard hover/search effects with a `useRef` that early-outs when the new state matches the previous (`prevHoverRef.current`, `prevSearchRef.current`). Without this, any unrelated re-render that re-enters the same hover state still rewrites every node object, triggers full reconciliation, and produces visible "ripple lag" on large graphs. Pattern: `if (state === prevRef.current) return; prevRef.current = state;` at the top of the effect.
- **`setNodes((nds) => nds.map(...))` MUST return same-reference nodes for unchanged ones** — the early-out alone is not enough. Even when we *do* run the effect (because the hovered node actually changed), the inner `map` still rewrites every node ref by default. With `React.memo` on the node components, returning the same node reference (or at least the same `data` reference) for nodes whose dimming/highlight state didn't actually change makes the per-component render skip. Pattern: `if (n.data.dimmed === newDimmed && n.data.highlighted === newHighlighted) return n;` before the `{ ...n, data: { ... } }` spread. Same idea for `setEdges` against `e.style?.opacity`. On a graph with 80+ nodes this is the difference between "ripple lag on every hover" and "instant". Use the destructuring trick `const { opacity: _drop, ...restStyle } = e.style ?? {}` to *remove* a key rather than set it to `undefined` (the latter still produces a new style object).
- **MiniMap re-renders all nodes on every viewport movement** — gate it on a node-count threshold (e.g. `visibleNodes.length <= 150 || isCollapsed`). On heavy graphs the minimap is both expensive and useless because every node is a pinhead.
- **Inline `transition: opacity 0.3s ease` is dangerous combined with hover dimming** — when hover dimming rewrites the entire node array, every node has to animate its opacity for 0.3s simultaneously, which is the visible "ripple lag". For graph views prefer instant dimming (no transition); the snappy feel beats the visual polish.
- **Same-reference dimming optimization is undone by the layout effect unless laid-out nodes carry explicit `dimmed: false, highlighted: false` defaults** — `setNodes([...bgNodes, ...laid])` writes nodes whose `data.dimmed` is `undefined`. The hover effect's same-reference fast path is `if (n.data.dimmed === !isMatch && n.data.highlighted === isMatch) return n;` — `undefined !== false` strict-equality fails for every freshly-laid node, so the entire graph gets rewritten on the FIRST hover after any layout change. Initialize the defaults: `laid.map((n) => ({ ...n, data: { ...n.data, dimmed: false, highlighted: false } }))` before calling `setNodes`. After the first hover all nodes have explicit `false`/`true` and subsequent hovers are fast — which is exactly what made round 3 of the topology perf pass invisible to most projects but lethal on `scherpenhuizen-talend`.
- **ReactFlow `fitView` animations stack when fired from multiple effects on the same frame and dominate frame time on heavy graphs** — the layout `useEffect` calls `fitView({duration: 300})` and the search `useEffect` calls `fitView({duration: 400})`; both can fire when `groupMembership` rebuilds with the same shape or when the user types fast. Gate them with a "last-fit-key" `useRef` keyed on the meaningful layout identity (`${selectedProject}|${direction}|${compactMode}|${nodeCount}`) and only call `fitView` when the key actually changed. Reset the ref on project switch so the new project still gets its initial fit.
- **Search auto-fit must not run on every keystroke when the match set is large** — typing one letter in a project that matches 40 nodes triggers a 400ms `fitView` zoom-fit across the entire viewport, and the animation stacks with subsequent keystrokes. Cap on `searchMatchIds.size <= 8` so single-letter searches just dim the rest; once the user has typed enough that the match shrinks to a useful size, the auto-fit kicks in. This matches every other code-search UI.
- **JWT cookie auth across services**: RAG backend (port 8000) issues cookies. Next.js (port 3000) proxies auth endpoints and forwards cookies to FastAPI. MCP server (port 8002) validates JWT independently using shared `JWT_SECRET_KEY`. Dashboard (port 8001/nginx) forwards cookies via `proxy_set_header Cookie`. All services must share the same JWT_SECRET_KEY.
- **Refresh token cookie path**: Refresh token cookie path is `/api/v1/auth/refresh` — browser only sends it to that endpoint. Access token cookie path is `/` — sent on every request.
- **Default admin password**: Set via `ADMIN_DEFAULT_PASSWORD` env var (default: "admin"). The lifespan handler seeds the admin user on first boot and also back-fills the password hash for existing admin users that were created before auth was added.

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
│   │   ├── api/          # FastAPI app, routes (auth, admin, query, etc.), dependencies
│   │   ├── auth/         # JWT security (hash, tokens), AuthService
│   │   ├── db/           # SQLAlchemy models (User, Role, RefreshToken, etc.), session
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
