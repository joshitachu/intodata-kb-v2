# IntoData KB — Progress Log

## Session: 2026-02-11 (Streaming Responses for Chat UI)

### [Feature]: End-to-end streaming responses for conversation chat

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — extracted `_prepare_rag_context()` helper + `RAGContext` dataclass, added `run_conversational_rag_stream()`, refactored `run_conversational_rag()` to use shared helper
  - `backend/src/api/routes/conversations.py` — added `POST /{conversation_id}/messages/stream` SSE endpoint
  - `frontend/app/api/conversations/[id]/messages/stream/route.ts` — **new** SSE proxy route handler
  - `frontend/hooks/useStreamMessage.ts` — **new** streaming hook with ReadableStream + TextDecoder SSE parsing
  - `frontend/components/ChatLayout.tsx` — replaced `useSendMessage` mutation with `useStreamMessage` hook, added optimistic user message + React Query invalidation on stream done
  - `frontend/components/ChatWindow.tsx` — added `streamingMessage` and `pendingUserMessage` props, removed `LoadingMessages` spinner
  - `frontend/components/MessageList.tsx` — renders optimistic user message and streaming assistant message at end of list, auto-scrolls on streaming content changes
  - `frontend/components/MessageBubble.tsx` — added `isStreaming` prop with blinking cursor (`animate-pulse`)
- **Problem**: Chat UI waited for full LLM generation to finish before showing anything — felt sluggish
- **Change**: Wired streaming end-to-end: backend extracts shared prep logic so both streaming and non-streaming endpoints reuse the same search/routing/context-building. Frontend reads SSE stream via ReadableStream, parses `event: sources`, `event: token`, `event: done` events, and renders tokens incrementally with a blinking cursor.
- **Why this approach**: Reuses existing `generate_answer_stream()` infrastructure. Shared `_prepare_rag_context()` helper eliminates code duplication between streaming and non-streaming paths. SSE is the standard for server-push; frontend proxy follows existing BFF pattern.
- **Verification**: 192 backend tests passing (no regressions), TypeScript compiles without errors

## Session: 2026-02-11 (Configurable LLM Provider Chain)

### [Feature]: Configurable LLM Provider Chain (Groq → OpenRouter → Ollama)

- **Files changed**:
  - `backend/pyproject.toml` — Added `groq>=0.11.0` and `openai>=1.0.0` dependencies
  - `backend/src/config.py` — Added `llm_providers` list, `groq_llm_model`, `openrouter_llm_model` settings; reorganized config sections
  - `backend/src/generation/llm_client.py` — Rewrote with 3-tier provider chain: Groq (AsyncGroq), OpenRouter (AsyncOpenAI with base_url), Ollama (existing); fallback logic for both `generate_answer()` and `generate_answer_stream()`
  - `backend/tests/test_generation/test_llm_client.py` — Replaced 2 old tests with 8 new tests: first-success, fallback-to-second, fallback-to-third, all-fail, custom-provider-order, stream-success, stream-fallback, stream-all-fail
- **Problem**: LLM generation was hardcoded to Ollama only. Needed configurable provider chain with fallback for reliability and cost optimization (free cloud APIs → local fallback).
- **Change**:
  - Added provider implementations: `_groq_generate/_groq_stream`, `_openrouter_generate/_openrouter_stream`, `_ollama_generate/_ollama_stream`
  - Provider registry `_PROVIDER_FUNCS` maps names to function name strings, resolved at call time via `_resolve()` (enables test patching)
  - `generate_answer()` and `generate_answer_stream()` iterate `settings.llm_providers` list, try each provider, log warnings on failure, raise `RuntimeError` if all fail
  - Groq and OpenRouter imports are lazy (inside function body) to avoid import errors when packages not installed
  - Config: `llm_providers: list[str] = ["groq", "openrouter", "ollama"]` — simple env var override to change provider chain
- **Why this approach**: Same public API signatures (`generate_answer`, `generate_answer_stream`), so no changes needed in `rag_chain.py`, `conversational_rag.py`, or `query.py`. Provider chain is a simple list — easy to reconfigure via `.env` for scaling to paid APIs later.
- **Verification**: 192 tests passing (8 new llm_client tests, full suite green)

## Session: 2026-02-11 (Enterprise Retrieval Quality Upgrade — 5 Phases)

### [Feature]: Phase 1 — Hybrid Search (Dense + Sparse with RRF)

- **Files changed**:
  - `backend/src/retrieval/sparse_embedder.py` (**new**) — TF-IDF sparse embedding via sklearn `TfidfVectorizer`
  - `backend/src/retrieval/vector_store.py` — Named vectors config (dense + sparse), `SparseVectorParams`, `upsert_chunks()` accepts optional `sparse_embeddings`
  - `backend/src/retrieval/search.py` — Added `search_similar_hybrid()` using Qdrant Prefetch + RRF fusion, `_build_search_filter()` helper, `_process_results()` refactored from inline code
  - `backend/src/ingestion/pipeline.py` — Generates sparse embeddings alongside dense when `hybrid_search_enabled`
  - `backend/src/config.py` — Added `hybrid_search_enabled`, `sparse_embed_model`
  - `backend/tests/test_retrieval/test_sparse_embedder.py` (**new**) — 5 tests
  - `backend/tests/test_retrieval/test_search.py` — 5 new hybrid search tests + 2 _process_results tests
- **Problem**: Pure cosine vector search missed keyword-exact matches (e.g., "ISO 27001")
- **Change**: Added sparse TF-IDF embeddings stored as named sparse vectors in Qdrant. Hybrid search uses Prefetch API to retrieve from both dense and sparse indexes, fused server-side with Reciprocal Rank Fusion (RRF).
- **Why this approach**: RRF is parameter-free and performs well without tuning. Qdrant's Prefetch API handles fusion server-side (no client-side merging). TF-IDF via sklearn used instead of fastembed (Rust toolchain not available on dev machine).
- **Verification**: 186 tests passing. Hybrid search tested with mock Qdrant.

### [Feature]: Phase 2 — Cross-Encoder Reranking

- **Files changed**:
  - `backend/src/retrieval/reranker.py` (**new**) — CrossEncoder wrapper with `rerank()` function, lazy-loaded singleton model
  - `backend/src/retrieval/search.py` — Added `search_and_rerank()`: retrieves N candidates → reranks with cross-encoder → returns top_k
  - `backend/src/generation/rag_chain.py` — Uses `search_and_rerank()` when `reranker_enabled`
  - `backend/src/generation/conversational_rag.py` — Same
  - `backend/src/config.py` — Added `reranker_enabled`, `reranker_model`, `rerank_top_n`
  - `backend/tests/test_retrieval/test_reranker.py` (**new**) — 5 tests
- **Problem**: Separate embedding models (dense/sparse) can't jointly score query-document relevance
- **Change**: After hybrid search retrieves 15 candidates, cross-encoder `ms-marco-MiniLM-L6-v2` (~80MB, CPU) scores each (query, document) pair jointly and reranks.
- **Verification**: 186 tests passing. Reranker tested with mocked model.

### [Feature]: Phase 3 — Semantic Chunking

- **Files changed**:
  - `backend/src/ingestion/semantic_chunker.py` (**new**) — `chunk_text_semantic()`: paragraph-aware splitting, small paragraph merging, oversized paragraph fallback to mechanical chunker, min_tokens filtering
  - `backend/src/ingestion/parser.py` — `_parse_docx()`: detects heading styles → markdown `# Heading` markers. `_parse_pptx()`: adds `[SLIDE N]` markers between slides
  - `backend/src/ingestion/pipeline.py` — Uses `chunk_text_semantic()` instead of `chunk_text()`
  - `backend/tests/test_ingestion/test_semantic_chunker.py` (**new**) — 10 tests
- **Problem**: Mechanical 256-token sliding window split content mid-sentence, diluting embeddings
- **Change**: Semantic chunker splits on paragraph boundaries (`\n\n`), merges small paragraphs up to max_tokens, preserves structural markers (headings, slides). XLSX delegates to mechanical chunker (tables don't benefit). Short single-chunk texts bypass min_tokens filter.
- **Verification**: 186 tests passing.

### [Feature]: Phase 4 — Query Expansion (Multi-Query)

- **Files changed**:
  - `backend/src/retrieval/query_expander.py` (**new**) — `expand_query()` generates 2 LLM alternative phrasings, `should_expand_query()` heuristic
  - `backend/src/retrieval/search.py` — Added `search_multi_query()`: expand → search each variant → deduplicate by (doc_id, chunk_idx) → rerank with original query
  - `backend/src/generation/rag_chain.py` — Uses `search_multi_query()` when `query_expansion_enabled`
  - `backend/src/generation/conversational_rag.py` — Same
  - `backend/src/config.py` — Added `query_expansion_enabled`
  - `backend/tests/test_retrieval/test_query_expander.py` (**new**) — 7 tests
- **Problem**: Short/ambiguous queries like "HR WFH policies" missed relevant docs
- **Change**: LLM generates 2 alternative phrasings (e.g., "Human Resources work from home guidelines"). Each variant searched independently, results deduplicated, then reranked using original query.
- **Verification**: 186 tests passing.

### [Feature]: Phase 5 — Streaming SSE + Chat-with-Document

- **Files changed**:
  - `backend/src/generation/llm_client.py` — Added `generate_answer_stream()` async generator
  - `backend/src/api/routes/query.py` — Added `POST /api/v1/query/stream` SSE endpoint (events: token, sources, done)
  - `backend/src/schemas.py` — Added `document_ids: list[int] | None` to `QueryRequest`
  - `backend/src/retrieval/search.py` — All search functions accept optional `document_ids` filter via `_build_search_filter()`
- **Problem**: No streaming (user waits for full response), no way to search within specific documents
- **Change**: SSE endpoint streams tokens as they arrive. `document_ids` filter adds Qdrant FieldCondition on `document_id` to restrict search scope.
- **Verification**: 186 tests passing.

### [Fix]: Existing tests updated for new feature flags

- **Files changed**:
  - `backend/tests/test_generation/test_rag_chain.py` — Added `disable_new_features` autouse fixture
  - `backend/tests/test_generation/test_conversational_rag.py` — Added `disable_new_features` to `TestConversationalRagIntentRouting` and `TestContextFirstFlow`
  - `backend/tests/test_ingestion/test_pipeline.py` — Mock `embed_texts_sparse` and `settings.hybrid_search_enabled`
- **Problem**: Existing tests mocked `search_similar` but code now calls `search_multi_query` (query expansion enabled by default)
- **Root cause**: New feature flags changed the search dispatch path in rag_chain and conversational_rag
- **Change**: Added autouse fixtures that set `query_expansion_enabled=False`, `reranker_enabled=False`, `hybrid_search_enabled=False` so existing tests continue testing the dense-only path
- **Verification**: All 186 tests passing (154 existing + 32 new)

### [Fix]: fastembed → scikit-learn for sparse embeddings

- **Files changed**: `backend/src/retrieval/sparse_embedder.py`, `backend/pyproject.toml`
- **Problem**: `fastembed` package depends on `py-rust-stemmers` which requires Rust toolchain (not available on dev machine)
- **Change**: Replaced fastembed with sklearn `TfidfVectorizer` for sparse embedding generation. Produces comparable sparse vectors for hybrid search keyword matching.
- **Why this approach**: sklearn already installed, no compilation needed. TF-IDF provides adequate keyword matching for hybrid search. Can swap to fastembed when Rust is available.
- **Verification**: 5 sparse embedder tests passing, all 186 tests passing.

## Session: 2026-02-10 (Fix [NEED_SEARCH] Leak + Remove Reformulation)

### [Fix]: [NEED_SEARCH] marker leaking into frontend UI

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — Major changes (see below)
  - `backend/tests/test_generation/test_conversational_rag.py` — Removed 4 tests, added 1, updated 3
- **Problem**: `[NEED_SEARCH]` marker appeared in the frontend UI. The instruction to respond with `[NEED_SEARCH]` was in `CONVERSATIONAL_SYSTEM_PROMPT`, which was used for ALL LLM calls — including the final answer after search. The LLM could respond `[NEED_SEARCH]` on the second attempt, and it got saved as the assistant message.
- **Root cause**: Single system prompt (`CONVERSATIONAL_SYSTEM_PROMPT`) contained the `[NEED_SEARCH]` instruction and was used for both the context-first attempt AND the final answer generation.
- **Changes**:
  1. **Removed `[NEED_SEARCH]` from `CONVERSATIONAL_SYSTEM_PROMPT`** — restored to "If the context does not contain enough information, say so clearly."
  2. **Created `CONTEXT_FIRST_SYSTEM_PROMPT`** — identical to main prompt but with: "respond with exactly [NEED_SEARCH] and nothing else." Used only by `build_conversational_messages_with_context()`.
  3. **Added safety strip in `run_conversational_rag()`** — `answer = answer.replace("[NEED_SEARCH]", "").strip()` before saving the assistant message.
  4. **Deleted `reformulate_search_query()`** and `REFORMULATE_PROMPT` — search now uses `analysis.cleaned_query` directly (last user message).
  5. **Updated 2 call sites** — summary path (line ~253) and QA search path (line ~285) now use `analysis.cleaned_query` instead of `await reformulate_search_query(...)`.
  6. **Removed unused imports** — `ollama as ollama_lib`, `from src.config import settings`
- **Why this approach**: Two-prompt separation ensures `[NEED_SEARCH]` is only available during the context-first attempt, never during final answer generation. Safety strip is a belt-and-suspenders defense. Reformulation removed for simplicity — the intent classifier's `cleaned_query` already handles typo correction.
- **Test changes**:
  - Deleted `TestReformulateSearchQuery` class (4 tests)
  - Removed `reformulate_search_query` from imports
  - Removed `mock_reform` patches from 3 integration tests
  - Added `test_need_search_marker_stripped_from_final_response` — verifies `[NEED_SEARCH]` is never present in response content
- **Verification**: 154 backend tests passing (was 157; removed 4 reformulation tests, added 1 safety test).

## Session: 2026-02-10 (Context-First Chat + LLM Search Reformulation)

### [Feature]: Context-first QA flow — try previous context before searching

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — Major rewrite of QA branch
  - `backend/tests/test_generation/test_conversational_rag.py` — 15 new tests (38 total in file)
- **Problem**: Every chat message triggered a fresh semantic search, even follow-ups like "tell me more" or "what about X in that document?" when the LLM already had the context to answer.
- **Root cause**: QA branch always called `search_similar()` unconditionally, regardless of whether previous sources existed in conversation history.
- **Changes**:
  1. **Deleted `expand_query()`** and `PRONOUN_PATTERNS` — replaced by `reformulate_search_query()` (see below)
  2. **Added `get_previous_sources()`** — walks conversation messages in reverse, returns `list[DocumentSource]` from the most recent assistant message that has sources
  3. **Added `needs_new_search()`** — checks if LLM response contains `[NEED_SEARCH]` marker
  4. **Added `build_context_from_document_sources()`** — builds context string from `DocumentSource` objects (like `build_context()` but for document-level sources instead of chunk-level `Source` objects)
  5. **Added `build_conversational_messages_with_context()`** — builds LLM messages from a pre-built context string (for context-first path)
  6. **Updated `CONVERSATIONAL_SYSTEM_PROMPT`** — added instruction: "If the provided context does not contain enough information to answer the question, respond with exactly [NEED_SEARCH] and nothing else."
  7. **Rewrote QA branch in `run_conversational_rag()`** — context-first flow:
     - Has previous sources? → Try answering from previous context + history
     - LLM answers well → return (0 searches)
     - LLM responds `[NEED_SEARCH]` → fall through to search (1 search)
     - No previous sources → search directly (1 search, same as before)
  8. **Changed `document_sources` scoping** — initialized as `list[DocumentSource] = []` at top of function, removed the old `if not sources: document_sources = []` pattern
- **Why this approach**: The LLM itself decides when it needs new information (via `[NEED_SEARCH]` marker). No new intent classifier actions, no hardcoded follow-up detection. Simple two-step logic that the LLM controls.
- **Verification**: 157 backend tests passing (15 new). New tests cover: `get_previous_sources` (4 tests), `needs_new_search` (4 tests), `build_context_from_document_sources` (4 tests), `build_conversational_messages_with_context` (2 tests), context-first QA flow (3 integration tests: no-search, NEED_SEARCH fallback, direct search).

### [Feature]: LLM-based search query reformulation (replaces expand_query)

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` — Added `reformulate_search_query()`, `REFORMULATE_PROMPT`
  - `backend/tests/test_generation/test_conversational_rag.py` — 4 new tests for reformulator
- **Problem**: `expand_query()` was dumb string concatenation: `"Context: {messages}. Current question: {query}"`. This produced noisy search queries that hurt vector similarity.
- **Root cause**: Regex-based pronoun detection + string concatenation can't produce clean, focused search queries.
- **Change**: Added `reformulate_search_query()` — calls the router model (`gemma3:4b`) with a prompt that resolves pronouns, strips conversational fluff, and outputs a concise 3-10 word search query. Falls back to original query on error or empty messages. Used in both QA search path and summary `resolve_document_id()` path.
- **Why this approach**: Small LLM call (~200-500ms) produces much better search queries than string concatenation. Same model already loaded for intent classification, so minimal overhead.
- **Verification**: 4 new tests: returns original on empty messages, calls router model correctly, falls back on error, falls back on empty LLM response.

## Session: 2026-02-10 (Conversation Memory Fix — Pronoun Resolution)

### [Fix]: Chat conversation memory — every message triggered blind semantic search

- **Files changed**:
  - `backend/src/generation/conversational_rag.py` (lines 68-77) — `expand_query()` now uses last 4 messages (user + assistant) instead of only last 2 user messages. Assistant responses contain document names critical for resolving pronouns.
  - `backend/src/generation/conversational_rag.py` (lines 213-216) — Summary path now calls `expand_query()` before `resolve_document_id()`, matching the QA path behavior.
  - `backend/src/generation/conversational_rag.py` (lines 204-210) — Builds conversation context string from last 4 messages and passes it to `analyze_query()`.
  - `backend/src/generation/intent_classifier.py` (line 86) — `analyze_query()` now accepts optional `conversation_context: str | None` parameter. When provided, prepends conversation context to the user message sent to the router LLM, enabling pronoun resolution (e.g., "summarize it" → "summarize The Quiet Revolution").
  - `backend/tests/test_generation/test_intent_classifier.py` — Added 2 new tests: `test_conversation_context_included_in_prompt` and `test_no_context_sends_plain_query`.
- **Problem**: When a user asked "is there a file about X?", got an answer, then said "summarize it", the system ran a vector search on "summarize it" with no context and matched the wrong document.
- **Root cause**: Three issues: (1) `expand_query()` only used user messages, ignoring assistant responses that contained document names; (2) Summary path skipped `expand_query()` entirely; (3) Intent classifier received no conversation context, so `cleaned_query` couldn't resolve pronouns.
- **Change**: (1) `expand_query()` now takes last 4 messages (both roles); (2) Summary path calls `expand_query()` before `resolve_document_id()`; (3) `analyze_query()` accepts optional conversation context string, prepended to the user message for the router LLM.
- **Why this approach**: Assistant messages are the richest source of context (they contain exact document names, answers, etc.). Passing conversation context to the intent classifier allows the LLM to resolve "it" → "The Quiet Revolution" at classification time, before search even begins.
- **Verification**: All 142 backend tests passing (including 2 new intent classifier tests).

## Session: 2026-02-10 (Map-Reduce Summarization, Document Re-Upload, Phase 3 Docs)

### [Feature]: Map-reduce summarization pipeline for full-document summaries

- **Files changed**:
  - `backend/src/generation/intent_classifier.py` — Added `QueryType` enum (`QA` | `SUMMARY`), `query_type` field to `QueryAnalysis` dataclass, updated `ROUTER_PROMPT` to classify query type, updated `_parse_router_response()` to parse `query_type` (defaults to `QA`)
  - `backend/src/generation/map_reduce.py` (**new**) — Full map-reduce pipeline: `resolve_document_id()` (top_k=1 vector search to identify document), `retrieve_document_chunks()` (Qdrant scroll to get all chunks sorted by chunk_index), `map_summarize()` (summarize one batch), `reduce_summaries()` (combine all maps), `run_map_reduce()` (orchestrator with `asyncio.gather` + `Semaphore(3)`)
  - `backend/src/generation/rag_chain.py` — Added `QueryType` import + `map_reduce` imports. After `analyze_query()`, branches on `query_type`: `SUMMARY` → `resolve_document_id()` → `run_map_reduce()`, `QA` → existing search path. Fallback: if `resolve_document_id()` returns None → no-results message.
  - `backend/src/generation/conversational_rag.py` — Same branching pattern as rag_chain.py for summary queries in conversations.
- **Problem**: Users asking "Summarize the HR policy document" only got 3 chunks via top_k=3, missing 90%+ of the document content.
- **Root cause**: No mechanism to retrieve ALL chunks for a document; retrieval was designed for Q&A (best-N chunks), not summarization.
- **Change**: Added intent classifier query type detection + full map-reduce pipeline that scrolls all document chunks, batches them (8 per batch), summarizes each batch in parallel (max 3 concurrent), then reduces all summaries into a coherent final answer.
- **Why this approach**: Map-reduce scales to any document size. Semaphore(3) prevents overwhelming local Ollama. Single-batch documents skip the reduce step for efficiency.
- **Verification**: 14 new tests in `test_map_reduce.py` (resolve, retrieve, map, reduce, batching, edge cases). All 140 tests passing.

### [Feature]: Document re-upload replacement (staleness fix)

- **Files changed**:
  - `backend/src/ingestion/pipeline.py` — Added `_replace_existing_document()` helper that queries DB for same filename, deletes old Qdrant vectors via `delete_document_points()`, deletes old DB record. Called at start of `ingest_document()` before creating new Document record. Added `sqlalchemy.select` and `delete_document_points` imports.
- **Problem**: Uploading "HR_Policy_v2.pdf" when "HR_Policy_v1.pdf" already exists with same filename creates duplicate vectors. Old chunks pollute search results.
- **Root cause**: No check for existing documents at any step of the ingestion pipeline.
- **Change**: Before creating a new Document record, check if a document with the same filename exists. If yes, delete old Qdrant vectors + old DB record, then proceed with fresh ingestion.
- **Why this approach**: Replace (not version) — versioning adds UI complexity, storage costs. KB users always want the latest version. Logged replacement for auditing.
- **Verification**: 2 new tests (no-existing, existing-is-deleted). All 140 tests passing.

### [Fix]: merge_sources_by_document max_documents parameter

- **Files changed**:
  - `backend/src/retrieval/search.py` — Added `max_documents` parameter (default=1) to `merge_sources_by_document()`. Changed from hardcoded `[merged[0]]` return to `merged[:max_documents]` with loop to assign reference numbers.
  - `backend/tests/test_retrieval/test_search.py` — Replaced `test_merge_sources_multiple_documents` (which expected 3 results but function returned 1) with 3 new tests: `test_merge_sources_multiple_documents_default` (1 result), `test_merge_sources_multiple_documents_max_3` (3 results), `test_merge_sources_max_documents_limits` (2 of 3).
- **Problem**: Pre-existing test `test_merge_sources_multiple_documents` expected 3 documents but `merge_sources_by_document()` was hardcoded to return only 1 (the top result). Also, function was not flexible for future use cases.
- **Root cause**: Function was modified to return top-1 only in the "strict docs-only RAG" session, but the old test was never updated.
- **Change**: Added `max_documents` param, replaced hardcoded slice with configurable limit. Default=1 preserves existing behavior.
- **Verification**: 3 new merge tests + all 140 tests passing.

### [Fix]: Pipeline test mock for _replace_existing_document

- **Files changed**: `backend/tests/test_ingestion/test_pipeline.py` — Updated `mock_db` fixture to include `db.delete = MagicMock()` and mock `db.execute` to return a result with `.scalars().first() → None` (no existing document). This prevents `AttributeError: 'coroutine' object has no attribute 'first'` when `_replace_existing_document()` calls `await db.execute(select(...))`.
- **Problem**: `test_ingest_document_success` failed because `db.execute()` returned an `AsyncMock` coroutine, but `.scalars().first()` was called synchronously on the awaited result.
- **Root cause**: `AsyncMock` auto-generates coroutines for all attribute access. Need a sync `MagicMock` for the `.scalars().first()` chain since `execute()` is already awaited.
- **Verification**: All 4 pipeline tests passing.

### [Docs]: Phase 3 future issues documented

- **Files changed**: `.claude/PROJECT_MEMORY.md`, `.claude/progress.md`, `.claude/context.md`, `.claude/PROGRESS_LOG.md`
- **Change**: Documented 3 future issues with proposed solutions and effort estimates:
  1. **Multi-hop reasoning**: Query decomposition → parallel search → merge + synthesize
  2. **Chunking strategy**: 3 tiers (paragraph-aware → section-aware → format-specific)
  3. **Embedding drift**: 4 options (glossary → cross-encoder → fine-tune → model swap)
- **Why**: These issues are real scaling concerns but require more design work. Documented so nothing gets lost.
- **Verification**: All project tracking files updated consistently.

## Session: 2026-02-10 (Retrieval Config, Logging Overhaul & Reasoning Logs)

### [Feature]: Unify retrieval defaults to top_k=3, threshold=0.4

- **Files changed**: `backend/src/schemas.py` (lines 12-13, 110-111), `backend/src/retrieval/search.py` (lines 20-21), `backend/src/generation/conversational_rag.py` (line 183-184)
- **Problem**: Inconsistent retrieval defaults across the codebase — `schemas.py` had top_k=10/threshold=0.2, `conversational_rag.py` had top_k=5/threshold=0.58. This caused different behavior between one-off queries and conversations.
- **Root cause**: Defaults were set independently in each location without a single source of truth
- **Change**: Set all three locations to `top_k=3, threshold=0.4` — schemas.py (API defaults), search.py (function signature safety net), conversational_rag.py (was overriding with its own hardcoded values)
- **Why this approach**: top_k=3 avoids flooding the LLM with low-relevance chunks; threshold=0.4 is a good middle ground (0.2 was too permissive, 0.58 was too strict for nomic-embed-text scores)
- **Verification**: Code review confirms all three locations now match

### [Fix]: Silence SQLAlchemy SQL echo in dev mode

- **Files changed**: `backend/src/api/main.py` (line 17)
- **Problem**: SQLAlchemy's `echo=settings.is_dev` in session.py floods the terminal with raw SQL statements, burying useful diagnostic logs
- **Root cause**: Engine's `echo` parameter feeds into `sqlalchemy.engine` logger at INFO level
- **Change**: Added `logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)` after `logging.basicConfig()` in lifespan
- **Why this approach**: Suppresses SQL noise without changing engine config; warnings/errors still visible
- **Verification**: Code review confirms placement after basicConfig

### [Feature]: Add structured reasoning logs to RAG pipelines

- **Files changed**: `backend/src/generation/rag_chain.py` (lines 5, 50-65), `backend/src/generation/conversational_rag.py` (lines 227-240)
- **Problem**: No visibility into which chunks were sent to the LLM, at what scores, or how many tokens — makes debugging retrieval quality difficult
- **Change**: Added three log sections in both `rag_chain.py` and `conversational_rag.py` (before `generate_answer()` call when sources exist):
  1. `--- Retrieved Chunks ---`: filename + score for each source
  2. `--- LLM Input Preview ---`: first 100 chars of each chunk
  3. `Logic trace:` summary line with chunk count, threshold, and total context tokens (via tiktoken)
- **Why this approach**: Shows the decision trail at a glance without being verbose. Complements existing `search.py` raw Qdrant logs with post-threshold, clean summaries
- **Verification**: Code review confirms logs are placed after search returns and before LLM call in both pipelines

## Session: 2026-02-10 (LLM-Based Query Router)

### [Feature]: Replace regex intent classifier with LLM-based query router

- **Files changed**: `backend/src/generation/intent_classifier.py` (full rewrite), `backend/src/config.py` (line 15), `backend/src/generation/rag_chain.py` (lines 10, 29-44), `backend/src/generation/conversational_rag.py` (lines 13, 203-215), `backend/tests/test_generation/test_intent_classifier.py` (full rewrite)
- **Problem**: Regex-based `classify_intent()` was brittle — missed misspellings, variations, edge cases. No typo correction for search queries.
- **Root cause**: Hardcoded regex patterns can't generalize beyond exact pattern matches
- **Change**:
  1. **intent_classifier.py**: Removed all regex patterns, `Intent` enum, `classify_intent()`. Added `Action` enum (`SEARCH`/`RESPOND`), `QueryAnalysis` dataclass, `ROUTER_PROMPT`, `_parse_router_response()` (JSON parser with markdown fence stripping + fallback), `analyze_query()` (async Ollama call using `ollama_router_model`). Safe fallback: any parse/connection failure defaults to `Action.SEARCH` with original query.
  2. **config.py**: Added `ollama_router_model: str = "gemma3:4b"` setting
  3. **rag_chain.py**: Replaced `classify_intent(request.query)` with `await analyze_query(request.query)`. Uses `analysis.action == Action.RESPOND` for branching. Passes `analysis.cleaned_query` to `search_similar()` for better search results.
  4. **conversational_rag.py**: Same pattern as rag_chain. Passes `analysis.cleaned_query` through `expand_query()` and to `search_similar()`.
  5. **Tests**: 14 new tests — 8 for `_parse_router_response()` (valid JSON, respond, code fences, invalid JSON fallback, missing fields, unknown action, empty cleaned_query) + 6 for `analyze_query()` (empty/whitespace, mocked Ollama calls, greeting routing, connection failure fallback, garbage response fallback)
- **Why this approach**: Small LLM call (~200-500ms) is a worthwhile trade-off for much more robust routing + automatic typo correction. `gemma3:4b` is small/fast enough for routing. Safe fallback to SEARCH ensures no queries are lost if router fails.
- **Verification**: All 48 generation tests passing (14 intent classifier + 34 others). Existing rag_chain and conversational_rag tests still pass with updated imports.

## Session: 2026-02-10 (Strict Docs-Only RAG + Chat UI Stability)

### [Refactor]: Unify chitchat + no-results into single response path

- **Files changed**: `backend/src/generation/prompt_builder.py`, `backend/src/generation/rag_chain.py`, `backend/src/generation/conversational_rag.py`
- **Problem**: Separate hardcoded prompts (GREETING_PROMPT, THANKS_PROMPT, GOODBYE_PROMPT) for chitchat intents, duplicating logic and producing inconsistent tone
- **Change**: Removed `GREETING_PROMPT`, `THANKS_PROMPT`, `GOODBYE_PROMPT`, `_INTENT_TO_PROMPT` dict, and `build_chitchat_messages()` function. Updated `NO_RESULTS_PROMPT` to handle all no-context scenarios (greetings, thanks, no results) naturally. Changed chitchat branches in `rag_chain.py` and `conversational_rag.py` to call `build_no_results_messages()` instead of `build_chitchat_messages()`. Removed unused `Intent` import from `prompt_builder.py` and `build_chitchat_messages` imports from both chain files.
- **Why this approach**: Single prompt is more maintainable and produces natural responses regardless of intent type. Intent classifier still used for efficiency (skip search for chitchat).
- **Verification**: All imports resolve, no remaining references to removed functions in codebase (except old `test.py` copy)

### [Feature]: Return only top-1 source from merge

- **Files changed**: `backend/src/retrieval/search.py` (lines 116-121)
- **Problem**: Multiple sources returned, cluttering the UI when only the best match matters
- **Change**: After sorting merged documents by score descending, return only `[merged[0]]` with `reference_number=1` instead of the full list
- **Why this approach**: Single best source reduces noise, matches the "docs-only" philosophy
- **Verification**: Function still returns empty list when no sources exist

### [Fix]: Chat input stays visible during submission

- **Files changed**: `frontend/components/ChatInput.tsx`
- **Problem**: Input cleared immediately on submit (`setMessage("")`), causing text to disappear before response arrives
- **Change**: Added `pendingRef` (useRef) and `useEffect` watching `isLoading`. On submit, set `pendingRef.current = true` instead of clearing. When `isLoading` transitions to false and pending is true, clear the input.
- **Why this approach**: Ref avoids re-renders, effect cleanly syncs with loading state
- **Verification**: Visual — text stays until response arrives, then clears

### [Fix]: Hide "0 sources" in metadata

- **Files changed**: `frontend/components/ResponseDisplay.tsx` (lines 41-44)
- **Problem**: "0 sources" displayed in metadata when no results found
- **Change**: Wrapped source count in conditional `{response.sources.length > 0 && (...)}` so it only renders when sources exist
- **Why this approach**: Clean UI — only show source info when relevant
- **Verification**: Visual — latency still shown, source count hidden when empty

## Session: 2026-02-09 (UI/UX & Response Formatting)

### [Feature]: Document-level source merging with numbered citations

- **Files changed**:
  - `backend/src/schemas.py` — Added `DocumentSource` model (document_id, filename, score, text, reference_number). Updated `QueryResponse.sources` and `MessageResponse.sources` to `list[DocumentSource]`.
  - `backend/src/retrieval/search.py` — Added `merge_sources_by_document()` function: groups chunks by document_id, sorts by chunk_index, concatenates texts, keeps highest score, assigns reference numbers.
  - `backend/src/generation/prompt_builder.py` — Rewrote `SYSTEM_PROMPT` with markdown formatting instructions and numbered citation format `[1]`, `[2]`. Added `reference_numbers` param to `build_messages()` and `_build_context()`. Context headers now use `[Reference N: filename]` instead of `[Source: filename, chunk N]`.
  - `backend/src/generation/rag_chain.py` — After search, merges sources via `merge_sources_by_document()`, builds ref_map, passes to `build_messages()`, returns `DocumentSource` in response.
  - `backend/src/generation/conversational_rag.py` — Updated `CONVERSATIONAL_SYSTEM_PROMPT` with numbered citation + markdown instructions. Added `reference_numbers` param to `build_context()` and `build_conversational_messages()`. `run_conversational_rag()` now merges sources and returns `DocumentSource`.
  - `backend/tests/test_generation/test_prompt_builder.py` — Added `test_build_messages_with_reference_numbers`, `test_build_context_with_reference_numbers`.
  - `backend/tests/test_generation/test_rag_chain.py` — Updated to assert `DocumentSource` type and `reference_number` field.
  - `backend/tests/test_generation/test_conversational_rag.py` — Added `test_single_source_with_reference_numbers` to TestBuildContext.
  - `backend/tests/test_retrieval/test_search.py` — Added 4 tests for `merge_sources_by_document()`: single chunk, multiple chunks same doc, multiple documents, empty.
  - `backend/tests/test_api/test_query.py` — Updated to use `DocumentSource` in mock response.
- **Problem**: Sources were exposed at chunk level (showing `chunk_index`), LLM used `[Source: filename, chunk N]` format.
- **Root cause**: No document-level aggregation existed; chunks were passed directly to frontend.
- **Change**: Added `DocumentSource` schema + `merge_sources_by_document()` utility. Updated all RAG pipelines to merge before returning. Updated prompts to instruct numbered `[1]` citations.
- **Why this approach**: Document-level sources are more meaningful to users. Numbered refs enable clickable citation links in the frontend.
- **Verification**: 164 tests passing (`pytest`). Frontend builds without type errors (`next build`).

### [Feature]: Markdown rendering, citation links, and loading animations in frontend

- **Files changed**:
  - `frontend/types/index.ts` — Added `DocumentSourceSchema` + `DocumentSource` type. Updated `QueryResponseSchema`, `MessageResponseSchema`, `QueryHistoryEntry` to use `DocumentSourceSchema`.
  - `frontend/components/LoadingMessages.tsx` (new) — Rotating loading messages with fade animation, cycles every 2.5s.
  - `frontend/components/MarkdownContent.tsx` (new) — `react-markdown` + `remark-gfm` renderer with clickable `[N]` citation links that scroll to `#source-N`.
  - `frontend/components/SourceCitation.tsx` — Changed from `Source` to `DocumentSource` prop type. Removed `chunk_index` display. Added reference number badge and `id={source-N}` anchor with `scroll-mt-4`.
  - `frontend/components/ResponseDisplay.tsx` — Replaced skeleton loader with `LoadingMessages`. Replaced plain text `<p>` with `MarkdownContent`. Uses `source.document_id` as key.
  - `frontend/components/MessageBubble.tsx` — Assistant messages now render with `MarkdownContent` (no bubble wrapper). User messages stay as plain text bubbles.
  - `frontend/components/ChatWindow.tsx` — Added `LoadingMessages` with Bot avatar when `isSending` is true, shown below message list.
  - `frontend/package.json` — Added `react-markdown` and `remark-gfm` dependencies.
- **Problem**: Answers rendered as plain text, chunk-level sources exposed, basic skeleton loader.
- **Change**: Full markdown rendering, clickable numbered citations, rotating loading messages, document-level sources with reference badges.
- **Verification**: `next build` passes with 0 type errors. All routes compile successfully.

## Session: 2026-02-09

### [Fix]: Frontend Zod schemas sending stale threshold/top_k defaults

- **Files changed**: `frontend/types/index.ts` (lines 7-8, 95-96)
- **Problem**: Backend defaults were updated (threshold 0.2, top_k 10) but frontend Zod schemas still hardcoded `threshold: 0.7` and `top_k: 5`. Since the frontend explicitly sends these values in every request, they override backend defaults. Logs showed `Threshold 0.7: 0 passed, 5 dropped` — all results were being filtered out.
- **Root cause**: Frontend schemas were not updated when backend defaults were changed in the previous session.
- **Change**: In `QueryRequestSchema` and `ConversationMessageRequestSchema`:
  - `top_k`: `.default(5)` → `.default(10)`, `.max(20)` → `.max(50)`
  - `threshold`: `.default(0.7)` → `.default(0.5)`
- **Why this approach**: User wants threshold 0.5 (accept results scoring above 0.5). The query's 5 results scored 0.56-0.68, all above 0.5. max(50) for top_k aligns with backend schema.
- **Verification**: Visual inspection of file changes. Restart frontend and re-run query to confirm logs show `Threshold 0.5` and results pass.

### [Feature]: Improve semantic search recall — tuned defaults, debug endpoint, diagnostic logging

- **Files changed**:
  - `backend/src/schemas.py` — threshold default 0.4→0.2, top_k default 5→10, top_k max 20→50 (both `QueryRequest` and `ConversationMessageRequest`). Added `DebugSearchRequest`, `DebugSearchResult`, `DebugSearchResponse` schemas.
  - `backend/src/retrieval/search.py` — function defaults updated (top_k=10, threshold=0.2). Replaced debug-level logging with comprehensive INFO-level diagnostics: embedding time, RBAC filter details, all raw results with scores/filenames/text previews, threshold pass/drop summary with dropped scores listed.
  - `backend/src/retrieval/embedder.py` — added timing + INFO logging to `embed_text()` and `embed_texts()` (model name, dimension, elapsed ms).
  - `backend/src/generation/rag_chain.py` — added INFO logging: query text + classified intent, chitchat skip, search result count, no-results fallback trigger, total latency + source count.
  - `backend/src/generation/prompt_builder.py` — MAX_CONTEXT_TOKENS 3000→4096. Added logger import + INFO logging in `_build_context()`: chunks used, tokens used vs max, chunks truncated.
  - `backend/src/ingestion/chunker.py` — DEFAULT_CHUNK_SIZE 500→256, DEFAULT_OVERLAP 50→32. Updated log message to include average chunk size.
  - `backend/src/api/routes/query.py` — added `POST /api/v1/query/debug` endpoint returning raw Qdrant scores without threshold filtering (embedding_ms, RBAC info, all results with scores).
  - `backend/tests/test_retrieval/test_search.py` — updated `test_search_no_results_below_threshold` to use score 0.15 (below new 0.2 threshold, was 0.3 for old 0.4 threshold).
  - `backend/tests/test_api/test_query.py` — added `test_query_debug_endpoint` test.
  - `backend/tests/test_ingestion/test_chunker.py` — updated `test_default_parameters` assertions: 500→256, 50→32.
  - `backend/tests/test_generation/test_prompt_builder.py` — updated `test_build_context_truncates_at_max_tokens`: increased long_text from 4000 to 5000 words to exceed new 4096 token limit.
- **Problem**: Semantic search returned few or no hits for natural language queries. 5 cascading filters suppressed recall: RBAC pre-filter, low top_k (5), strict threshold (0.4), small context window (3000 tokens), and large chunk size (500 tokens). No diagnostic logging made it impossible to see what scores Qdrant was actually returning.
- **Root cause**: `nomic-embed-text` cosine similarity for natural language queries against relevant chunks typically scores 0.2-0.5. Threshold 0.4 dropped borderline-relevant results. top_k=5 was too restrictive after RBAC filtering. 500-token chunks dilute embeddings.
- **Change**: Lowered threshold (0.2), doubled top_k (10), expanded max top_k (50), increased context window (4096 tokens), halved chunk size (256 tokens). Added debug endpoint for empirical score analysis. Added comprehensive INFO-level pipeline logging.
- **Why this approach**: Empirical tuning based on nomic-embed-text score distributions. Debug endpoint enables data-driven threshold decisions. Logging makes the full RAG pipeline transparent in terminal output.
- **Verification**: All 157 backend tests passing. Existing documents must be re-ingested after chunk size change.

## Session: 2026-02-06

### [Feature]: Intent routing for RAG pipeline — greetings, chitchat, and LLM fallback for no-results

- **Files changed**:
  - `backend/src/generation/intent_classifier.py` (new) — Intent enum + regex-based classify_intent()
  - `backend/src/generation/prompt_builder.py` — added GREETING/THANKS/GOODBYE/NO_RESULTS prompts + build_chitchat_messages() + build_no_results_messages()
  - `backend/src/generation/rag_chain.py` — added intent routing in run_rag_query(): chitchat skips search, no-results uses LLM fallback
  - `backend/src/generation/conversational_rag.py` — same routing in run_conversational_rag() with conversation history awareness
  - `backend/tests/test_generation/test_intent_classifier.py` (new) — 30 parametrized tests for all intents
  - `backend/tests/test_generation/test_rag_chain.py` — replaced hardcoded fallback test with LLM fallback test + added greeting-skips-search test
  - `backend/tests/test_generation/test_conversational_rag.py` — added greeting-skips-search + no-results LLM fallback tests
- **Problem**: Every user input (including "hi", "thanks", vague text) triggered semantic search. When no results were found, the system returned a hardcoded string: "I could not find any relevant information in the knowledge base for your question." — robotic and poor UX.
- **Root cause**: No intent classification layer; all queries went through the same search → threshold → hardcoded fallback path.
- **Change**: Hybrid rule-based classifier + LLM fallback approach:
  1. Regex patterns detect greetings/thanks/goodbye → skip search, respond via LLM with appropriate prompt
  2. Short queries (1-2 words) without conversation history → classified as UNCLEAR, still runs search but gets LLM fallback if nothing found
  3. Short queries WITH conversation history → classified as KB_QUERY (likely follow-ups)
  4. When search returns no results → LLM generates helpful response (asks clarifying questions or suggests related topics) instead of hardcoded string
- **Why this approach**: No extra LLM call for KB queries (preserves performance), greetings are actually faster (skip embedding + Qdrant), failed searches get better UX. Works well with small local LLM.
- **Verification**: All 156 backend tests passing (91 in generation suite alone), no regressions

### [Fix]: Lower similarity threshold from 0.7 to 0.4 — most queries returning zero results

- **Files changed**: `backend/src/schemas.py` (lines 13, 79), `backend/src/retrieval/search.py` (line 20, added debug logging lines 43-45), `backend/tests/test_retrieval/test_search.py` (lines 42-45)
- **Problem**: Most natural language queries returned "I searched the vector database and found nothing relevant." Only near-exact text matches returned results.
- **Root cause**: Default similarity threshold was 0.7. With `nomic-embed-text`, natural language queries produce cosine similarity scores in the 0.3-0.6 range against relevant chunks. Only near-verbatim matches score above 0.7, so all semantically relevant results were silently dropped at the threshold filter.
- **Change**:
  - Lowered default threshold from `0.7` to `0.4` in `QueryRequest.threshold`, `ConversationMessageRequest.threshold`, and `search_similar()` function signature
  - Added debug logging in `search_similar()` to log raw Qdrant result count and individual scores before threshold filtering, making future debugging easier
  - Updated `test_search_returns_sources_above_threshold` to expect 2 results (score 0.5 now passes the 0.4 threshold)
- **Why this approach**: 0.4 is a reasonable threshold for nomic-embed-text cosine similarity — low enough to capture semantically relevant results, high enough to filter noise. Debug logging added to avoid similar blind-spot issues in the future.
- **Verification**: All 93 backend tests passing (previously 1 failure in test_search due to old threshold expectation)

## Session: 2026-02-05

### Worked On
- Project planning and architecture decisions
- Project scaffolding (all directories, configs, placeholders)

### Completed
- Full implementation plan created
- All architecture decisions confirmed
- Directory structure created
- Tracking files initialized

### Issues Encountered
- None

---

## Session: 2026-02-05 (continued)

### Worked On
- Database Agent: config, models, session, Alembic migration, tests
- Backend Agent: full RAG pipeline, all API routes, RBAC, tests

### Completed
- **Database Agent**: config.py, db/base.py, db/session.py, 5 models (User, Role, Document, QueryLog, IngestionJob), Alembic async migration, 11 tests
- **Backend Agent**: schemas.py, api/main.py, api/dependencies.py, 4 route files (health, query, documents, ingest), ingestion/ (parser, chunker, metadata, pipeline), retrieval/ (embedder, vector_store, search), generation/ (llm_client, prompt_builder, rag_chain), rbac/ (auth, permissions), 37 new tests → 48 total

### Issues Encountered
- Python 3.14 doesn't have `setuptools.backends._legacy` → use `setuptools.build_meta`
- JSONB not supported by SQLite → use generic JSON
- SQLite needs PRAGMA foreign_keys=ON for CASCADE
- SQLAlchemy needs passive_deletes=True for DB-level CASCADE

---

## Session: 2026-02-06

### Worked On
- Frontend Agent: Complete Next.js implementation

### Completed
- **Frontend Agent**: Full implementation including:
  - Config files: `package.json`, `tsconfig.json`, `tailwind.config.ts`, `next.config.ts`, `postcss.config.js`, `components.json`
  - Types: Zod schemas for all API types (`types/index.ts`)
  - Lib: `utils.ts` (cn helper), `fastapi.ts` (server-side fetch wrapper with X-User-Id header)
  - Route handlers (BFF pattern): `/api/query`, `/api/documents`, `/api/ingest`, `/api/health`
  - Hooks: `useSearch`, `useDocuments` (list/delete/upload), `useQueryHistory` (React Query + client state)
  - Components: `SearchBar`, `ResponseDisplay`, `SourceCitation`, `QueryHistory`, `DocumentUpload`, `Sidebar`, `Layout`
  - Pages: `/` (search), `/documents` (upload/list/delete), `/settings` (health check)
  - Providers: QueryClientProvider wrapper

### Issues Encountered
- Zod `.default()` doesn't make field optional for `z.infer` → use `.optional().default()` + `z.input`
- OneDrive path caused `.next/build-manifest.json` error → delete `.next` cache and rebuild

### Build Status
- TypeScript: passes (`npx tsc --noEmit`)
- Next.js build: succeeds (10 routes generated)
- Backend tests: 48/48 passing

### MVP Status: COMPLETE

All Phase 1 features are implemented:
- Backend: FastAPI with RAG pipeline, RBAC, 48 tests
- Frontend: Next.js with BFF pattern, React Query, Tailwind
- Database: PostgreSQL schema with Alembic migrations

### To Run the Full Stack
```bash
# Terminal 1: Start Docker services
docker-compose up -d  # Starts Qdrant + PostgreSQL

# Terminal 2: Run migrations + start backend
cd backend
alembic upgrade head
uvicorn src.api.main:app --reload

# Terminal 3: Start frontend
cd frontend
npm run dev
```

Then open http://localhost:3000

### Next Session Priorities
1. End-to-end testing with real Docker services
2. Integration Agent (Phase 2) — when company tooling is known
3. Optional: Add JWT/OAuth authentication (Phase 2)

---

## Session: 2026-02-06 (continued)

### [Fix]: Add missing Qdrant payload indexes for RBAC filtering

- **Files changed**: `backend/src/retrieval/vector_store.py` (lines 7-11 imports, lines 29-39 new index calls)
- **Problem**: Every search query using RBAC filtering failed with:
  > Bad request: Index required but not found for "allowed_groups" of one of the following types: [keyword].
- **Root cause**: `ensure_collection()` only created the Qdrant collection with vector config but never called `create_payload_index()`. Qdrant requires explicit keyword indexes on payload fields used in `FieldCondition` / `MatchAny` filters.
- **Change**:
  1. Added `PayloadSchemaType` to the `qdrant_client.models` import block
  2. Added two `create_payload_index()` calls after collection creation (outside the `if` block so they run idempotently on every startup):
     - `allowed_groups` with type `KEYWORD` — required for `MatchAny` RBAC filtering in search
     - `document_id` with type `INTEGER` — required for filtered deletes in `delete_document_points()`
- **Why this approach**: Qdrant's `create_payload_index` is idempotent (no-op if index already exists), so placing it outside the `if` block ensures both new and existing collections get indexes without conditional logic.
- **Verification**: Code review confirms correct API usage. Runtime verification requires Docker (Qdrant) — to be tested in E2E.

### [Improvement]: Overhaul CLAUDE.md with mandatory logging rules

- **Files changed**: `CLAUDE.md` (full rewrite of context system section, added new section)
- **Problem**: CLAUDE.md had a minimal "Context System" section that said to update files after work, but had no enforcement, no format specification, and no detail on what to log.
- **Change**:
  - Added a new **"MANDATORY: Logging & Documentation Rules"** section with 4 subsections:
    1. **Read Before Working** — lists all 4 `.claude/` files to read at session start
    2. **Log Every Change** — specifies exactly what to write in each file, with a template format for PROGRESS_LOG entries (date, files changed, problem, root cause, change, reasoning, verification)
    3. **Never Skip Logging** — explicit rule that even small changes must be logged
    4. **Log Format Rules** — specificity requirements (exact paths, verbatim errors, before/after)
  - Updated Critical Rules list to include logging (rule 6), context reading (rule 7), and specificity (rule 8)
  - Updated project structure to show all 4 `.claude/` files with descriptions
- **Why this approach**: Without explicit, detailed logging rules, context gets lost between sessions. The structured format ensures consistency and makes logs actually useful for resuming work.
- **Verification**: CLAUDE.md reads correctly, rules are clear and actionable.
- **Also updated**: `PROGRESS_LOG.md` (this entry), `context.md`, `PROJECT_MEMORY.md`

---

## Session: 2026-02-06 (continued #2)

### [Fix]: Add Qdrant `ensure_collection()` call to FastAPI lifespan startup

- **Files changed**: `backend/src/api/main.py` (lines 17-22, added 5 lines in lifespan function)
- **Problem**: Queries fail with `Index required but not found for "allowed_groups" of one of the following types: [keyword]` because `ensure_collection()` (which creates the KEYWORD index) was only called during document ingestion, not at app startup. If no documents had been ingested yet, or the collection was created before the index code existed, queries would fail.
- **Root cause**: `ensure_collection()` was wired into `pipeline.py` (ingestion) but never called from the FastAPI lifespan. The app startup didn't initialize Qdrant indexes.
- **Change**: Added Qdrant initialization to the `lifespan()` function in `main.py`:
  ```python
  from src.api.dependencies import get_qdrant
  from src.retrieval.vector_store import ensure_collection
  qdrant = await get_qdrant()
  await ensure_collection(qdrant)
  logger.info("Qdrant collection and indexes verified")
  ```
- **Why this approach**: `ensure_collection()` and `create_payload_index()` are both idempotent — safe to call on every startup. This guarantees indexes exist before any query is attempted, regardless of whether documents have been ingested.
- **Verification**: Code review confirms correct usage. Runtime verification requires Docker (Qdrant).

## Session: 2026-02-09 (Fix — Frontend Conversation 500 Errors)

### [Fix]: Clear stale .next webpack cache causing 500 on all conversation routes

- **Files changed**: Deleted `frontend/.next/` directory
- **Problem**: All conversation route handlers (`GET /api/conversations`, `GET /api/conversations/{id}`) returned 500 with `Cannot find module './331.js'`. The compiled webpack chunks referenced a missing module.
- **Root cause**: Stale `.next` build cache — incremental compilation left orphan chunk references after code changes. The OneDrive path exacerbates this (known gotcha).
- **Change**: `rm -rf frontend/.next` to force a full rebuild on next `npm run dev`.
- **Verification**: `.next` directory removed successfully.

### [Fix]: Handle 204 No-Content in `fastapiFetch` to prevent JSON parse crash

- **Files changed**: `frontend/lib/fastapi.ts` (lines 28-30)
- **Problem**: DELETE endpoints return 204 with no body, but `fastapiFetch` always called `res.json()` which throws `SyntaxError: Unexpected end of JSON input` on empty responses.
- **Root cause**: No check for 204 status before attempting JSON parse.
- **Change**: Added `if (res.status === 204) { return undefined as T; }` before the `res.json()` call.
- **Why this approach**: 204 is the standard "success, no body" status. Returning `undefined` matches the `Promise<void>` expectation of delete operations.
- **Verification**: Code review — the early return prevents `res.json()` from being called on empty bodies.

### [Fix]: Harden error handling in `useConversations.ts` — prevent secondary JSON parse crash

- **Files changed**: `frontend/hooks/useConversations.ts` (lines 19-21, 31-34, 48-51, 69-72, 81-83, 100-103)
- **Problem**: All 6 fetch functions called `await res.json()` on error responses without checking if the body is valid JSON. When the BFF returns a non-JSON error (e.g., from webpack crash or HTML error page), this throws `SyntaxError: Unexpected end of JSON input` which masks the real error.
- **Root cause**: Error handling assumed error responses always contain JSON `{ error: "..." }` body.
- **Change**: Replaced `const data = await res.json()` with `const text = await res.text()` + `try { message = JSON.parse(text).error || message; } catch {}` in all 6 functions: `fetchConversations`, `fetchConversation`, `createConversation`, `updateConversation`, `deleteConversation`, `sendMessage`.
- **Why this approach**: `res.text()` never throws on any valid HTTP response. The try-catch around `JSON.parse` gracefully falls back to the default error message when the body isn't JSON.
- **Verification**: Code review — all 6 error paths now handle non-JSON responses without throwing secondary errors.

## Session: 2026-02-09 (Bug Fix — Conversations 500 Error)

### [Fix]: 500 error on GET /api/v1/conversations/{id} due to Source→DocumentSource mismatch

- **Files changed**: `backend/src/api/routes/conversations.py` (lines 11-35, 127)
- **Problem**: After the UI/UX formatting session changed `MessageResponse.sources` from `list[Source]` to `list[DocumentSource]`, the `get_conversation` endpoint still constructed `Source(**s)` objects from stored JSON. Pydantic rejected these, causing a 500 Internal Server Error when loading any conversation with sources.
- **Root cause**: The schema migration to `DocumentSource` (with `reference_number` instead of `chunk_index`) was applied to schemas.py but the conversation route was not updated to match.
- **Change**:
  - Replaced `Source` import with `DocumentSource` import
  - Added `_deserialize_sources()` helper that constructs `DocumentSource` objects from DB JSON, with backward compatibility: uses `reference_number` if present, otherwise assigns sequential numbers (1, 2, 3...)
  - Changed line 127 from `[Source(**s) for s in m.sources]` to `_deserialize_sources(m.sources)`
- **Why this approach**: A helper function keeps the deserialization logic in one place and handles both old-format sources (with `chunk_index`, no `reference_number`) and new-format sources cleanly. Sequential numbering for old sources is a reasonable fallback.
- **Verification**: Code review confirms the fix matches the `DocumentSource` schema fields. Runtime verification requires Docker.
