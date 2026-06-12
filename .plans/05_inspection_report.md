---
inspected_at: 2026-06-12
verdict: PASS
---

## Verdict Summary

PASS. Slice 1 matches the approved scope for structure-aware markdown chunking,
chunk metadata plumbing, source-layer default filtering, source payload mapping,
map-reduce resolver reuse of the shared filter, and the starter retrieval eval
utility.

Two inspection findings were fixed before this report was finalized:

- `resolve_document_id` accepted `project_ids` but summary callers were not
  passing them through. Fixed in `RAG_pipeline/backend/src/generation/rag_chain.py`
  and `RAG_pipeline/backend/src/generation/conversational_rag.py`.
- `resolve_document_id` temporarily bypassed source-layer filtering with
  `source_layers=[]`. Fixed in `RAG_pipeline/backend/src/generation/map_reduce.py`
  so the default canonical-docs plus legacy layer filter applies consistently.

The clean backend suite now passes: `348 passed, 2 warnings`.

## Check Results

| Check | Result | Notes |
|---|---|---|
| Plan conformance | PASS | Implemented the Slice 1 affected files plus a necessary `chunker.py` guard found by tests. No MCP or frontend code was changed. |
| Root cause | PASS | Context-blind markdown chunks, dropped metadata, missing corpus partitioning, duplicated source mapping, and custom map-reduce filter construction were addressed at shared boundaries. |
| Generalized fix | PASS | Search filter logic remains centralized in `_build_search_filter`; source payload mapping is centralized in `_source_from_payload`; mechanical chunk progress is guarded in the base chunker. |
| Design patterns | PASS | Changes follow existing ingestion pipeline, Qdrant payload/index, Pydantic schema, and retrieval strategy patterns. |
| Contract respect | PASS | Default search paths include `canonical_docs` plus legacy chunks via `IsEmptyCondition`; explicit `source_layers` is available for later slices; legacy source schemas remain optional. |
| No special-casing | PASS | No project-, document-, or fixture-specific branches were introduced. |
| Edge cases | PASS | Covered headingless markdown, code fences, oversized sections, stale CSRF state-changing API tests, legacy payloads, explicit layers, empty layer list, project assignments, and map-reduce project scoping. |
| RBAC | PASS | `allowed_groups` filtering is preserved. Admin selected-project scope and non-admin project assignments continue to flow through Qdrant filters. |
| Credential safety | PASS | No credentials or raw secrets added. Eval fixture contains only question/expected-file pairs. |

## Findings

No open findings.

Resolved during inspection:

1. **BLOCK - Summary resolution did not thread project scope**:
   `RAG_pipeline/backend/src/generation/rag_chain.py:260` and
   `RAG_pipeline/backend/src/generation/conversational_rag.py:587` now pass
   `project_ids` into `resolve_document_id`. Without this, summary document
   resolution could ignore a selected project even though normal search paths
   were correctly project-scoped.

2. **BLOCK - Summary resolution bypassed default source-layer partitioning**:
   `RAG_pipeline/backend/src/generation/map_reduce.py:76` now calls
   `_build_search_filter(...)` with default `source_layers=None`. This restores
   the planned canonical-docs plus legacy layer filter for document resolution.

3. **WARN - Oversized markdown sections could hang when `overlap >= chunk_size`**:
   `RAG_pipeline/backend/src/ingestion/chunker.py:66` now rejects non-positive
   chunk sizes and caps effective overlap below chunk size. The regression is
   covered by `RAG_pipeline/backend/tests/test_ingestion/test_chunker.py`.

4. **WARN - Older API tests omitted CSRF headers on state-changing requests**:
   `RAG_pipeline/backend/tests/test_api/test_admin.py`,
   `RAG_pipeline/backend/tests/test_api/test_documents.py`, and
   `RAG_pipeline/backend/tests/test_api/test_query.py` now use the same
   double-submit test headers already used by conversation tests.

## Required Actions

None. Proceed to `/validate`.
