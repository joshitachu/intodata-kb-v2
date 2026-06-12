---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
pipeline_step: 3_slices
total_slices: 6
current_slice: 1
---

# Slice ordering notes

- Slice 1 must ship before Slice 3 (the PRD ordering hazard: the layer-aware
  default filter must be live before any internal artifact enters the corpus).
- Slice 2 is independent of Slices 1/3 and can be built in parallel; Slice 3
  needs both.
- Slices 1, 2, 3, 4, 5 all touch critical areas — each requires `/inspect`
  before commit.
- Re-ingestion of existing projects' canonical docs is an operational step at
  the end of Slice 1 (delete-by-project + re-push via existing endpoints).

## Slice 1: Structure-aware chunking, chunk metadata, and layer-aware filtering
**Delivers**: Canonical docs are re-ingested as heading-path-prefixed structured
chunks carrying full metadata (`source_layer`, `group`, `unit`, `section`, ...),
and default retrieval filters to canonical-only with is-empty tolerance for
legacy chunks — retrieval quality improves on the existing corpus immediately,
and the corpus is partition-ready before any internal layer exists.
**Expected files**:
- `RAG_pipeline/backend/src/ingestion/markdown_chunker.py` (new)
- `RAG_pipeline/backend/src/ingestion/pipeline.py` (metadata passthrough in `ingest_text`)
- `RAG_pipeline/backend/src/retrieval/vector_store.py` (`upsert_chunks` payload)
- `RAG_pipeline/backend/src/retrieval/search.py` (`_build_search_filter` layer condition)
- `RAG_pipeline/backend/src/schemas.py` (`Source`/`DocumentSource` layer + display fields)
- `RAG_pipeline/backend/src/config.py` (layer filter settings)
- tests: chunker structure, metadata assignment, filter legacy tolerance
- small retrieval-eval utility (recall@k baseline on canonical corpus, reused in Slice 6)
**Critical area**: yes — RAG retrieval chain (`/inspect` required)
**Depends on**: none

## Slice 2: Harness `_rag_support/` persistence and analysis_cache restore
**Delivers**: A successful generation leaves `docs/_rag_support/` containing the
manifest-driven Phase 2 group analyses plus manifest.json, visible docs are
byte-identical, and `analysis_cache/` is populated again so incremental webhook
regeneration stops silently falling back to full regen.
**Expected files**:
- `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/phases/phase4_cleanup.py`
- `knowledge_base/mcp-server/src/mcp_server/providers/custom/pipeline/helpers.py` (if selection helper lands there)
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` (repoint `analysis_cache` copy to the persisted package)
- tests: package allowlist (manifest-driven groups only, no `_doc_pack_*`/`_bundle_*`), manifest contents, swap unaffected
**Critical area**: yes — harness pipeline (`/inspect` required)
**Depends on**: none (parallel to Slice 1)

## Slice 3: Layered ingestion push with entity index
**Delivers**: After generation, internal layers (`technical_analysis` from
`_rag_support/`, all `_facts/*.md` as `support_maps`, scorecard, doc slices)
are pushed to RAG with full metadata and sanitized layer-prefixed filenames,
internal layers are delete+re-pushed per generation (no stale groups), the
entity index is ingested as a per-project alias lookup, and default user
retrieval still returns canonical-only.
**Expected files**:
- `knowledge_base/mcp-server/src/mcp_server/services/rag_bridge.py` (metadata-carrying push, internal-layer delete, filename sanitization)
- `knowledge_base/mcp-server/src/mcp_server/api/routes/projects.py` (`_run_generate_background` internal push step)
- `RAG_pipeline/backend/src/api/routes/ingest.py` (accept layer metadata; internal-layer delete; entity-alias ingest)
- `RAG_pipeline/backend/src/db/models/` (entity alias storage, if Postgres-backed)
- tests both sides: payload correctness, sanitization (`Training|Jasmijn.md`), idempotent re-push, stale-group removal, default retrieval unchanged
**Critical area**: yes — MCP API routes (`/inspect` required)
**Depends on**: Slice 1, Slice 2

## Slice 4: Retrieval ladder and doc-gap logging
**Delivers**: A question final docs cannot answer descends to rung 2
(technical_analysis + query-type-routed support layers), answers with internal
support and friendly citations, records
`primary_source_layer`/`fallback_used`/`doc_gap`, logs a doc-gap event, and the
reuse path (`get_previous_sources`) preserves layer metadata; unsupported
questions still refuse cleanly and the happy path pays nothing extra.
**Expected files**:
- `RAG_pipeline/backend/src/generation/conversational_rag.py` (ladder in `_prepare_rag_context` QA path; insufficiency detection extending `[NEED_SEARCH]`/`needs_new_search` + entity-presence check)
- `RAG_pipeline/backend/src/generation/prompt_builder.py` (rung-2 prompt, display names)
- `RAG_pipeline/backend/src/retrieval/search.py` (layer-scoped search helpers)
- `RAG_pipeline/backend/src/schemas.py` (answer metadata)
- doc-gap event storage (query-log metadata field or one new model + migration — decide in `/plan`)
- tests: rung-1-sufficient, rung-2-answerable, unsupported, reuse-path metadata, doc-gap event shape, RBAC on rung 2
**Critical area**: yes — RAG retrieval chain (`/inspect` required)
**Depends on**: Slice 3

## Slice 5: Deep-rung retrieval quality
**Delivers**: Rung 2 reliably finds what exists — entity-aware retrieval
(query-time alias detection → guaranteed `unit` payload match alongside
semantic search), widened recall (`prefetch_limit` ~150 / `rerank_top_n` ~75 on
rung 2), query expansion enabled on rung 2 only, and section-parent expansion
returning complete owning sections.
**Expected files**:
- `RAG_pipeline/backend/src/retrieval/search.py` (entity match path, section-parent expansion generalizing `expand_neighbor_chunks`)
- `RAG_pipeline/backend/src/retrieval/entity_matcher.py` (new)
- `RAG_pipeline/backend/src/retrieval/query_expander.py` (rung-scoped enablement)
- `RAG_pipeline/backend/src/config.py` (rung-2 recall settings)
- tests: entity hit when embeddings miss, section-parent completeness, rung-1 latency unchanged, Voyage rate-limit degradation
**Critical area**: yes — RAG retrieval chain (`/inspect` required)
**Depends on**: Slice 4

## Slice 6: Evaluation harness and tuning
**Delivers**: A golden evaluation set from at least one real generated project
(final-doc-answerable, Phase-2-only, unsupported, entity-lookup,
risk/config/dependency questions) with measured recall@30 pre-rerank, recall@8
post-rerank, source-layer selection accuracy, and refusal correctness — plus
threshold/recall tuning and the data-driven verdicts on the LLM verifier and
`_doc_pack_*` questions.
**Expected files**:
- `RAG_pipeline/backend/scripts/retrieval_eval.py` (extends Slice 1 utility)
- golden set fixtures (per test project, operator-curated)
- `RAG_pipeline/backend/src/config.py` (tuned values)
- eval report committed alongside (results, not intermediate artifacts)
**Critical area**: no
**Depends on**: Slice 4 (Slice 5 improves results; run final numbers after it)
