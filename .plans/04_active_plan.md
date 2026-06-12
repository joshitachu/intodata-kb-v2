---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
slice: Slice 4 - Retrieval ladder and doc-gap logging
sub_project: rag_pipeline
critical_area: yes - RAG retrieval chain, conversational QA path, answer schemas; /inspect required before validation/commit
verdict: PASS
approved_at: 2026-06-12
depends_on:
  - Slice 3 committed as 39895b1: internal-layer ingestion push + entity index
---

## Problem Statement

Slice 3 puts internal support layers into RAG, but the conversational QA path still performs one default search. Because `source_layers=None` intentionally searches only canonical docs plus legacy chunks, questions that final docs cannot answer never descend to the new `technical_analysis`, `support_maps`, `scorecard`, or `evidence_slices` layers.

The current path also lacks the Slice 4 answer contract:

- no `primary_source_layer`, `fallback_used`, or `doc_gap` metadata on answers;
- no doc-gap event storage when internal analysis fills a final-doc omission;
- previous-source reuse and conversation reload do not preserve all layer/display metadata;
- internal citations would currently render raw scoped filenames such as `_internal/...` in the chat source list.

## Proposed Solution

Add a two-rung retrieval ladder to the conversational QA path only:

1. Rung 1 searches the existing default corpus partition (`source_layers=None`), which remains canonical docs plus legacy chunks.
2. Rung 1 attempts one context-grounded answer using the existing `[NEED_SEARCH]` contract. If it answers cleanly, return that answer with no rung-2 search and no extra LLM call.
3. If rung 1 has no sources, emits `[NEED_SEARCH]`, trips the existing `needs_new_search()` safety net, or misses identifier-like entities from the query, run rung 2.
4. Rung 2 searches exact internal layers selected by deterministic query-type routing:
   - component/job/unit detail: `technical_analysis`
   - field/table/mapping/endpoint/API: `technical_analysis`, `evidence_slices`
   - runtime/config/connection: `technical_analysis`, `support_maps`
   - dependency/lineage/calls: `technical_analysis`, `support_maps`
   - risk/security/quality: `scorecard`, `support_maps`, `technical_analysis`
   - generic technical fallback: `technical_analysis`
5. If rung 2 supplies sources, answer from those sources with friendly citations, mark `fallback_used=true`, set `primary_source_layer`, and log a doc-gap event.
6. If rung 2 also cannot support the answer, refuse cleanly from the no-results/support-gap prompt and do not log a doc-gap event.

Use a dedicated `DocGapEvent` table instead of overloading `QueryLog`. The existing `QueryLog` model is sparse, is not written by conversational RAG, and does not have the fields needed for repeated-omission analysis.

Add friendly source display metadata while preserving raw scoped filenames for storage and project filtering. Backend context builders and frontend citation rendering should use `display_name` when present and fall back to `filename` for legacy sources.

## Affected Files

Backend:

- `RAG_pipeline/backend/src/generation/conversational_rag.py`
- `RAG_pipeline/backend/src/generation/prompt_builder.py`
- `RAG_pipeline/backend/src/retrieval/search.py`
- `RAG_pipeline/backend/src/schemas.py`
- `RAG_pipeline/backend/src/db/models/doc_gap_event.py` (new)
- `RAG_pipeline/backend/src/db/models/conversation.py`
- `RAG_pipeline/backend/src/db/models/__init__.py`
- `RAG_pipeline/backend/alembic/versions/029_add_doc_gap_events.py` (new)
- `RAG_pipeline/backend/tests/test_generation/test_conversational_rag.py`
- `RAG_pipeline/backend/tests/test_generation/test_prompt_builder.py`
- `RAG_pipeline/backend/tests/test_retrieval/test_search.py`
- `RAG_pipeline/backend/tests/test_db/test_doc_gap_event.py` (new)
- `RAG_pipeline/backend/tests/test_api/test_conversations.py`

Frontend, only if needed for citation display:

- `RAG_pipeline/frontend/types/index.ts`
- `RAG_pipeline/frontend/components/SourceCitation.tsx`
- `RAG_pipeline/frontend/tests/components/MessageList.test.tsx` or a focused citation test

## Architecture Contracts

- RBAC filtering remains mandatory on every Qdrant query, including rung 2.
- Rung 2 must pass explicit `source_layers`; it must not use the default legacy-tolerant layer condition.
- Default retrieval behavior remains unchanged for all non-ladder paths.
- The happy path pays no rung-2 retrieval cost and no second LLM generation.
- Internal source reuse is allowed only after a previous answer already passed the ladder gate.
- Raw `_internal/...` paths must not be used as user-facing citation names.
- Doc-gap events are logged only when rung 2 supplies answer-supporting internal material after rung 1 is insufficient.
- Do not hardcode project, group, job, table, or entity names.

## Implementation Steps

1. Extend schemas and source mapping.
   - Add optional `display_name`, `artifact_path`, `visibility`, `run_id`, and `project_id` to `Source` and `DocumentSource`.
   - Add `primary_source_layer`, `fallback_used`, and `doc_gap` to `MessageResponse`; add the same optional fields to `QueryResponse` only as compatibility defaults if needed.
   - Update `_source_from_payload()` and `merge_sources_by_document()` to preserve the new fields.
   - Add a shared friendly display-name helper for canonical and internal layers.

2. Preserve source metadata across conversations.
   - Update `get_previous_sources()` to filter by explicit `project_id` when available and fall back to scoped filename prefixes for legacy data.
   - Update `build_context_from_document_sources()` and prompt context builders to use `display_name`.
   - Update `api/routes/conversations.py::_deserialize_sources()` so GET conversation responses keep layer, display, artifact, and project metadata.

3. Add doc-gap persistence.
   - Add `DocGapEvent` model and migration `029_add_doc_gap_events.py` with `down_revision="028"`.
   - Store `project_id`, optional `run_id`, `user_id`, `conversation_id`, optional `message_id`, `query_text`, `detected_entities`, `insufficiency_reason`, `primary_source_layer`, `layers_used`, `supporting_artifacts`, and `supporting_chunk_ids`.
   - Use generic `JSON().with_variant(JSONB(), "postgresql")` for SQLite-compatible JSON fields.

4. Add layer-scoped retrieval helpers.
   - Add a small helper in `retrieval/search.py` that runs the existing search strategy with explicit `source_layers` and the same `user_groups`/`project_ids`.
   - Keep reranker failure fallback and neighbor expansion behavior unchanged.
   - Add tests that rung-2 searches carry both RBAC/project filters and the explicit layer filter.

5. Add deterministic ladder routing and insufficiency checks.
   - Add query keyword routing for rung-2 layer selection.
   - Add identifier-like entity extraction from the user query for the Slice 4 entity-presence check; do not query the alias table yet.
   - Treat rung 1 as insufficient when identifier-like query terms are absent from canonical source text.
   - Keep the existing `[NEED_SEARCH]` and `needs_new_search()` checks as the primary insufficiency signal.

6. Wire the ladder into `_prepare_rag_context()`.
   - Apply only to QA searches, after previous-source reuse and before no-results fallback.
   - For rung-1 sufficient answers, return the precomputed answer with canonical sources and metadata `fallback_used=false`, `doc_gap=false`.
   - For rung-2 answers, return internal sources and metadata `fallback_used=true`, `doc_gap=true`.
   - For unsupported answers after rung 2, return a clean support-gap answer with no doc-gap event.
   - Streaming may emit a precomputed answer as a single token for ladder decisions, matching the existing context-first reuse behavior.

7. Persist and return answer metadata.
   - Add nullable/default fields to `ConversationMessage` for `primary_source_layer`, `fallback_used`, and `doc_gap`.
   - Fill those fields in non-streaming and streaming conversation paths.
   - Include the fields in SSE `done` payload if practical; at minimum include them in persisted message retrieval and `MessageResponse`.
   - Insert `DocGapEvent` after the assistant message has an id.

8. Add frontend citation display support if backend exposes `display_name`.
   - Extend `DocumentSourceSchema` with optional layer/display fields.
   - Render `source.display_name ?? source.filename` in `SourceCitation`.
   - Keep existing source rendering layout unchanged.

## Verification Strategy

Targeted backend tests:

```bash
cd RAG_pipeline/backend
uv run pytest \
  tests/test_generation/test_conversational_rag.py \
  tests/test_generation/test_prompt_builder.py \
  tests/test_retrieval/test_search.py \
  tests/test_db/test_doc_gap_event.py \
  tests/test_api/test_conversations.py
```

Targeted frontend tests if citation rendering changes:

```bash
cd RAG_pipeline/frontend
npx tsc --noEmit
npm test -- SourceCitation
```

Syntax checks for every changed Python file:

```bash
python3 -m py_compile <changed-file>
```

Full validation before commit:

```bash
cd RAG_pipeline/backend && uv run pytest
cd RAG_pipeline/frontend && npx tsc --noEmit && npm test
```

Required gates after implementation:

```text
/inspect
/validate
```

## Acceptance Criteria

- A final-doc-answerable question answers from rung 1 with no rung-2 search.
- A Phase-2-only/internal answer descends to rung 2 and answers with internal support.
- Unsupported questions refuse cleanly after rung 2 instead of fabricating.
- Rung-2 Qdrant calls apply RBAC, project scoping, and explicit source-layer filtering.
- Answer responses and persisted assistant messages include `primary_source_layer`, `fallback_used`, and `doc_gap`.
- Rung-2 answers log a queryable `DocGapEvent` with insufficiency reason, layers used, artifacts, chunk ids, and detected identifiers.
- Previous-source reuse preserves layer/display metadata and can reuse internal sources.
- Internal citations use friendly names; raw `_internal/...` filenames do not render in chat citations.
- Default canonical retrieval and non-conversation query paths do not regress.

## Out of Scope

- Query-time alias lookup from `entity_aliases` and guaranteed `unit` payload matching; that is Slice 5.
- Widened rung-2 recall, query expansion on rung 2, and section-parent expansion; those are Slice 5.
- Evaluation harness tuning and golden-set scoring; that is Slice 6.
- MCP ingestion changes; Slice 3 already shipped internal push and alias ingest.
- Admin document-list filtering for `_internal/` rows; this remains the Slice 3 inspection follow-up unless separately scheduled.
- Raw `source_evidence` ingestion.
