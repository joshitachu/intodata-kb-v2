---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
slice: Slice 5 - Deep-rung retrieval quality
sub_project: rag_pipeline
critical_area: yes - RAG retrieval chain, Qdrant filtering, conversational ladder integration; /inspect required before validation/commit
verdict: PASS
approved_at: 2026-06-13
depends_on:
  - Slice 4 committed as d10282a: retrieval ladder + doc-gap logging
inspection_basis:
  - Slice 4 inspection blocker fixed: images now thread into ladder generation calls
  - Accepted Slice 4 WARN: fresh QA streams as one blob
  - Accepted Slice 4 WARN: fallback path can cost up to 3 LLM calls
  - Slice 5 must address the accepted heuristic note: alias lookup replaces noisy PascalCase identifier descent
---

## Problem Statement

Slice 4 added the two-rung conversational retrieval ladder, but rung 2 still uses the same semantic search depth as rung 1 and relies on a heuristic identifier detector. That leaves three quality gaps:

- Exact entity questions can still miss when embeddings do not retrieve the chunk whose `unit` payload matches the named job, component, table, endpoint, or config item.
- The heuristic identifier detector can descend unnecessarily for PascalCase/common technical words such as `JavaScript`, because it is not grounded in the per-project alias table Slice 3 created.
- Rung 2 retrieves adjacent chunks, not the complete owning section, so the LLM can still see an incomplete technical passage even when the right section was found.

Slice 5 should improve rung-2 recall without changing Slice 4's answer contract, doc-gap semantics, or default rung-1 latency.

## Proposed Solution

Add deep-rung retrieval quality as a retrieval-layer capability used only by Slice 4 rung 2:

1. Add an `entity_matcher` module over the existing `entity_aliases` table.
   - Normalize the query and alias rows deterministically.
   - Match aliases/canonical names/unit names per project, longest match first.
   - Return structured entity matches: alias, canonical name, unit name, entity type, project id, confidence, source artifact/path.
   - Use project scoping from the current conversation request; never match aliases from projects the user did not select or cannot access.

2. Replace the Slice 4 primary identifier heuristic with alias-backed entity matching.
   - Use alias matches for rung-1 entity-presence checks and doc-gap `detected_entities`.
   - Keep a guarded legacy heuristic fallback only when alias lookup is unavailable or a project has no alias rows, so older unsynced projects degrade gracefully.
   - Avoid spurious descent when no alias match exists for generic PascalCase terms.

3. Add guaranteed unit payload retrieval alongside semantic rung-2 search.
   - Add Qdrant payload indexes for `unit` and `section` in `ensure_collection()`.
   - Add a unit-match retrieval helper that scrolls Qdrant with the same RBAC/project/source-layer filter plus `unit in matched_units`.
   - Merge unit payload hits with semantic candidates before final ranking/expansion.
   - Deduplicate by `(document_id, chunk_index)` and preserve all `Source` metadata.

4. Add rung-2-only recall settings.
   - Add config fields with conservative defaults:
     - `rung2_prefetch_limit = 150`
     - `rung2_rerank_top_n = 75`
     - `rung2_query_expansion_enabled = True`
     - `rung2_entity_match_limit = 25`
     - `rung2_section_expansion_limit = 40`
   - Thread `prefetch_limit` and `rerank_candidates` through `search_and_rerank()` and `search_similar_hybrid()` without changing default calls.
   - `search_layered()` should use these rung-2 settings; rung 1 keeps existing defaults.

5. Enable query expansion on rung 2 only.
   - Extend `query_expander.expand_query()` or add a wrapper so callers can force expansion independent of global `settings.query_expansion_enabled`.
   - `search_layered()` may expand the query when `settings.rung2_query_expansion_enabled` is true.
   - Default `/query`, rung 1, metadata enrichment, and summary resolution must not pay expansion latency.

6. Add section-parent expansion for rung 2.
   - Generalize the existing neighbor expansion pattern with a new section expansion helper.
   - When a source has `section`, fetch all chunks from the same `document_id` and `section` under the same RBAC/project/layer filter.
   - Sort section chunks by `chunk_index`, cap by `rung2_section_expansion_limit`, and dedupe.
   - Fall back to existing neighbor expansion when section metadata is absent.

## Affected Files

- `RAG_pipeline/backend/src/retrieval/entity_matcher.py` (new)
- `RAG_pipeline/backend/src/retrieval/search.py`
- `RAG_pipeline/backend/src/retrieval/query_expander.py`
- `RAG_pipeline/backend/src/retrieval/vector_store.py`
- `RAG_pipeline/backend/src/generation/conversational_rag.py`
- `RAG_pipeline/backend/src/config.py`
- `RAG_pipeline/backend/tests/test_retrieval/test_entity_matcher.py` (new)
- `RAG_pipeline/backend/tests/test_retrieval/test_search.py`
- `RAG_pipeline/backend/tests/test_retrieval/test_query_expander.py`
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag.py`
- `RAG_pipeline/backend/tests/test_retrieval/test_vector_store.py` or nearest existing vector-store coverage

## Architecture Contracts

- Rung 2 must always pass explicit `source_layers`; never use `source_layers=None`.
- Every entity payload fetch and section expansion must reuse the same RBAC/project filter contract as semantic search.
- Default retrieval, metadata enrichment, map-reduce summary resolution, and `/query` must keep current recall settings and global query-expansion behavior.
- No new answer metadata fields; Slice 4's `primary_source_layer`, `fallback_used`, `doc_gap`, and `DocGapEvent` semantics remain the contract.
- No hardcoded entity, group, project, table, or job names.
- Do not add a new relational table; Slice 3's `entity_aliases` table is the source of truth.
- Any new generation call in `_prepare_rag_context()` must thread `images`, but this slice should not need additional LLM generation calls.

## Implementation Steps

1. Add `entity_matcher.py`.
   - Define a small typed result model/dataclass for matched entities.
   - Normalize query text and aliases with casefolding, punctuation-tolerant word boundaries, and whitespace collapse.
   - Query `EntityAlias` rows scoped to `project_ids`; if `project_ids is None`, allow admin-wide rows but keep match results project-tagged.
   - Dedupe matches by `(project_id, unit_name, canonical_name, alias_normalized)`, preferring higher confidence and longer alias text.
   - Add unit tests for exact alias, case-insensitive alias, unit-name alias, longest-match wins, per-project scoping, and no-match behavior.

2. Add Qdrant payload indexes.
   - Extend `ensure_collection()` to create `unit` and `section` keyword indexes idempotently.
   - Add/update a vector-store test proving the new indexes are requested without removing existing `allowed_groups`, `document_id`, and `source_layer` indexes.

3. Extend search helpers for rung-2 recall.
   - Add optional `prefetch_limit` to `search_and_rerank()` and pass it to `search_similar_hybrid()`.
   - Add optional `rerank_candidates` and entity/unit parameters to `search_layered()`.
   - Preserve reranker failure fallback; if reranking fails, return boosted entity hits plus semantic candidates in deterministic order.
   - Add tests that rung-2 calls use `rung2_prefetch_limit`/`rung2_rerank_top_n` while default `search_and_rerank()` still uses existing settings.

4. Add unit payload match retrieval.
   - Build a `_build_unit_filter()` helper from `_build_search_filter()` plus `unit MatchAny`.
   - Fetch matching chunks with `client.scroll(...)`, `with_payload=True`, `with_vectors=False`, and `limit=settings.rung2_entity_match_limit`.
   - Convert payloads via `_source_from_payload()` so friendly display and metadata stay centralized.
   - Merge entity hits into layered results and test that a unit hit is returned even when semantic search returns nothing.

5. Add rung-2 query expansion.
   - Add an explicit enable override to `expand_query()` or a small `expand_query_for_layered_search()` wrapper.
   - In `search_layered()`, when rung-2 expansion is enabled, search original plus expansions and dedupe by `(document_id, chunk_index)`.
   - Keep global `settings.query_expansion_enabled=False` behavior unchanged for rung 1.
   - Add tests proving rung-2 expansion can run while global expansion remains disabled.

6. Add section-parent expansion.
   - Add `_build_section_filter()` and `expand_section_chunks()` in `search.py`.
   - Fetch all same-document chunks with the same `section` under the same RBAC/project/layer constraints.
   - Sort by `chunk_index`; cap to `rung2_section_expansion_limit`; dedupe across matched sections.
   - Wire section expansion into `search_layered()` after semantic/entity merge and final ranking.
   - Add tests for complete section fetch, order preservation, fallback to neighbor expansion when `section` is missing, and RBAC/source-layer preservation.

7. Wire entity matching into the ladder.
   - In `_prepare_rag_context()`, call the matcher before the rung-1 entity-presence check when `db` is available.
   - Replace `_extract_query_identifiers()` as the primary path; keep the old heuristic as a fallback only for no-alias/legacy cases.
   - Pass matched unit names into `search_layered()`.
   - Store structured alias match names in doc-gap `detected_entities`.
   - Add regression tests for:
     - alias match descends when canonical sources omit the entity;
     - alias unit hit makes rung 2 answerable when semantic search misses;
     - generic PascalCase without alias does not force rung 2;
     - `images` still reaches ladder generation calls.

## Verification Strategy

Targeted backend tests:

```bash
cd RAG_pipeline/backend
uv run pytest \
  tests/test_retrieval/test_entity_matcher.py \
  tests/test_retrieval/test_search.py \
  tests/test_retrieval/test_query_expander.py \
  tests/test_generation/test_conversational_rag.py \
  tests/test_api/test_conversations.py
```

Syntax checks:

```bash
python3 -m py_compile \
  src/retrieval/entity_matcher.py \
  src/retrieval/search.py \
  src/retrieval/query_expander.py \
  src/retrieval/vector_store.py \
  src/generation/conversational_rag.py \
  src/config.py
```

Full validation before commit:

```bash
cd RAG_pipeline/backend && uv run pytest
```

Required gates after implementation:

```text
/inspect
/validate
```

## Acceptance Criteria

- A query naming an alias from `entity_aliases` detects the entity for the correct project.
- Rung 1 does not descend solely because of generic PascalCase terms when no alias match exists.
- Rung 2 returns a matching `unit` chunk even when semantic retrieval returns no candidates.
- Rung 2 uses widened recall settings; rung 1 and `/query` keep existing settings.
- Query expansion runs for rung 2 only and remains disabled elsewhere unless the global setting is explicitly enabled.
- Section expansion returns complete owning sections in `chunk_index` order under the same RBAC/project/source-layer filter.
- Reranker failures degrade to deterministic entity-hit-plus-semantic ordering rather than dropping exact unit hits.
- Doc-gap events continue to log only when rung 2 answers; their `detected_entities` come from alias matches when available.
- No answer contract, citation display, or default canonical retrieval regression.

## Out of Scope

- Slice 6 golden evaluation harness and threshold tuning.
- UI or admin reporting for doc-gap events.
- Changing the Slice 4 streaming behavior where fresh QA emits a single blob.
- Reducing the accepted fallback-path LLM-call count.
- New ingestion/MCP alias schema changes.
- Raw `source_evidence` ingestion or source-code-level retrieval.
- Bulk reindex or operational alias backfill beyond existing Slice 3 push/manual sync paths.
