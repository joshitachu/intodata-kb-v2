---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
slice: Slice 1 -- Structure-aware chunking, chunk metadata, and layer-aware filtering
sub_project: rag_pipeline
critical_area: yes -- RAG retrieval chain (`/inspect` required)
verdict: PASS
approved_at: 2026-06-12
---

## Problem Statement

Three verified defects block the layered-RAG feature and degrade retrieval today:

1. **Context-blind chunks.** `chunk_text_semantic` splits markdown into
   paragraph windows (<=512 tokens) carrying no heading, section, or document
   context. A chunk about `API_Contracts` retry behavior embeds with no signal
   of which document or section it came from, so both the embedder and the
   Voyage reranker judge it blind.
2. **Metadata dropped at ingestion.** `/ingest/md` accepts a `metadata` dict
   but `ingest_text` uses only `author`/`department`; `upsert_chunks` writes a
   fixed five-field payload. There is no vehicle for `source_layer`,
   `visibility`, `section`, or any routing metadata.
3. **No corpus partitioning.** `_build_search_filter` filters on
   `allowed_groups` only. The moment Slice 3 ingests internal Phase 2
   artifacts, they would pollute every retrieval path. The layer-aware default
   filter must exist *before* any internal artifact is pushed (PRD ordering
   requirement, FR8).

Generalization findings that this slice must fix at the root, not per-callsite:

- `Source` objects are built field-by-field in 4 places (`search.py:256, 344,
  446, 487`); adding payload fields naively would mean 4 divergent copies.
- `resolve_document_id` (`map_reduce.py:60-80`) builds its own Qdrant filter,
  bypassing `_build_search_filter` — it applies `allowed_groups` but **omits
  the project filter entirely** (latent RBAC inconsistency) and would miss the
  layer default.
- Both ingestion entry points (`ingest_document` for uploads, `ingest_text`
  for generated docs) call `upsert_chunks`; metadata defaulting must cover
  both, not just the MCP push path.

## Proposed Solution

Four coordinated changes inside `RAG_pipeline/backend`, no MCP-server changes:

**1. Markdown structure chunker** (new `src/ingestion/markdown_chunker.py`).
Parses the heading tree (`#`..`####`), emits one or more chunks per leaf
section with the heading path prepended to the text
(`[{doc_name} > {H2} > {H3} > {H4}]\n...`) and per-chunk metadata
(`section` = heading path string, `unit` = deepest heading title). Small
sibling sections merge up to 512 tokens (within the same parent); oversized
sections fall back to paragraph merge/mechanical split, reusing the existing
`semantic_chunker` helpers. Text without headings delegates to
`chunk_text_semantic` unchanged. Returns `list[MarkdownChunk]` (Pydantic v2:
`text`, `section`, `unit`). Used for `file_type == "md"` in both ingestion
entry points; all other file types keep the current chunker.

**2. Chunk metadata plumbing.** `upsert_chunks` gains
`chunk_metadatas: list[dict] | None` merged into each point's payload on top
of the existing five fields. A single helper in `pipeline.py` builds the
document-level metadata dict (`project_id`, `source_layer`, `artifact_type`,
`visibility`, `doc_name`, `group`, `artifact_path`, `run_id` — absent keys
omitted) from the ingest request, applying defaults when the caller sends
none: `source_layer="canonical_docs"`, `visibility="public"`,
`artifact_type="final_doc"` (`uploaded_file` for `ingest_document`). Slice 3
will later send internal-layer values through the same path — no new endpoint.
`ensure_collection` additionally creates a keyword payload index on
`source_layer` (online operation, no recreation).

**3. Layer-aware default filter.** `_build_search_filter` gains
`source_layers: list[str] | None = None`. `None` means default: a nested
`should` group requiring `source_layer` to match
`settings.default_source_layers` (`["canonical_docs"]`) **or** be absent
(`IsEmptyCondition`) so every legacy chunk keeps working without re-ingestion.
An explicit list scopes to those layers (used by Slice 4's rung 2); the
neighbor filter and all search functions thread the parameter through with
`None` default, so current behavior is preserved everywhere by default.
`resolve_document_id` is rewired through `_build_search_filter` (fixing the
missing project filter at the same time — root-cause fix, not a patch).

**4. Source schema + single payload mapping.** `Source` and `DocumentSource`
gain optional fields (`source_layer`, `section`, `unit`, `group`, defaults
`None` so persisted message sources and old payloads deserialize unchanged). A
single `_source_from_payload(payload, score)` helper replaces the two
payload→Source constructions (`_process_results`, `expand_neighbor_chunks`);
the two rerank re-constructions switch to `model_copy(update={"score": ...})`.
Future payload fields then touch exactly one mapping site.

Plus the **eval utility** (`scripts/retrieval_eval.py`): loads a JSON fixture
of `{question, expected_filename, expected_section?}` items, runs
`search_and_rerank` against the running stack, reports recall@30 pre-rerank
and recall@8 post-rerank. Establishes the canonical-corpus baseline now;
Slice 6 extends it.

**Operational step (end of slice):** re-ingest canonical docs for existing
projects via the existing MCP per-project RAG push route (delete-by-project +
re-push), so the corpus is uniformly structured before Slice 3.

## Affected Files

- `RAG_pipeline/backend/src/ingestion/markdown_chunker.py` — NEW
- `RAG_pipeline/backend/src/ingestion/pipeline.py` — metadata helper; md routing to new chunker in `ingest_text` + `ingest_document`; pass `chunk_metadatas`
- `RAG_pipeline/backend/src/retrieval/vector_store.py` — `upsert_chunks` payload merge; `ensure_collection` payload index
- `RAG_pipeline/backend/src/retrieval/search.py` — `source_layers` param + default layer condition in `_build_search_filter`; `_source_from_payload`; `model_copy` in rerank paths; param threading
- `RAG_pipeline/backend/src/generation/map_reduce.py` — `resolve_document_id` via `_build_search_filter`
- `RAG_pipeline/backend/src/schemas.py` — optional `Source`/`DocumentSource` fields
- `RAG_pipeline/backend/src/config.py` — `default_source_layers`
- `RAG_pipeline/backend/scripts/retrieval_eval.py` — NEW
- `RAG_pipeline/backend/tests/test_ingestion/test_markdown_chunker.py` — NEW
- `RAG_pipeline/backend/tests/test_ingestion/test_pipeline.py` — metadata defaulting cases
- `RAG_pipeline/backend/tests/test_retrieval/test_search.py` — filter layer/legacy/RBAC cases, payload mapping

## Architecture Contracts

- Root + RAG `CLAUDE.md`: RBAC filter on every vector query (the layer change
  must never weaken `allowed_groups` conditions); type hints everywhere;
  Pydantic v2; async I/O; no bare except; no workarounds — hence the
  `resolve_document_id` rewire and single payload-mapping helper instead of
  per-callsite patches.
- `RAG_pipeline/.claude/PROJECT_MEMORY.md`: no LangChain (direct clients);
  threshold 0.3 rationale (do not change defaults); anti-hallucination prompt
  rules untouched by this slice.
- PRD FR3 (metadata fields), FR4 (chunker is load-bearing, heading-path
  prefix required), FR5 (default canonical + is-empty tolerance), FR8
  (ordering: this slice ships before Slice 3), NFR (re-ingestion safe, no
  Postgres migration — chunk metadata lives in Qdrant payloads only).
- Backward compatibility: legacy chunks (no `source_layer`) and persisted
  conversation sources (no new fields) must work unchanged — enforced by
  optional fields + `IsEmptyCondition`.

## Implementation Steps

1. **Schemas + config.** Add optional `source_layer`/`section`/`unit`/`group`
   to `Source` and `DocumentSource`; add `default_source_layers` setting.
   Verify: existing tests pass untouched; `DocumentSource(**old_dict)` works.
2. **Markdown chunker + tests.** Implement `markdown_chunker.py`
   (`MarkdownChunk` model, heading-tree walk, sibling merge, oversized
   fallback, no-heading delegation, heading-path prefix). Tests: nested
   h1–h4 structure, `### SECTION` + `#### unit` shape from a real Phase 2
   sample, oversized section split, headingless text delegation, prefix
   format, ASCII passthrough.
3. **`upsert_chunks` payload merge + payload index.** `chunk_metadatas`
   parameter (length-matched, merged after base fields so base keys win);
   `source_layer` keyword index in `ensure_collection`. Verify: unit test
   payload contents; index creation idempotent.
4. **Ingestion plumbing.** Metadata helper with defaults; route md through the
   new chunker in both `ingest_text` and `ingest_document`; pass per-chunk
   metadata. Verify: pipeline tests assert defaults
   (`canonical_docs`/`public`), override via request metadata, md vs non-md
   routing, chunk/metadata list alignment.
5. **Layer-aware filter + payload mapping.** `_build_search_filter`
   `source_layers` param with default canonical-or-absent `should` group;
   thread through `search_similar`, `search_similar_hybrid`,
   `search_and_rerank`, `search_multi_query`, `_build_neighbor_filter`;
   `_source_from_payload` helper; `model_copy` for rerank rebuilds. Verify:
   filter unit tests — default includes legacy (is-empty) chunks, explicit
   layers scope correctly, `allowed_groups` conditions unchanged in every
   branch (admin, role-groups, project-only, none).
6. **`resolve_document_id` rewire.** Use `_build_search_filter` with
   `project_ids` threaded from callers where available. Verify: summary
   resolution test with project scoping; no own-filter construction remains
   (`grep "Filter(" src/generation/map_reduce.py` shows only the
   document-scoped scroll).
7. **Eval utility.** `scripts/retrieval_eval.py` + fixture schema + small
   starter fixture for one existing project. Verify: runs against local
   stack, prints recall@30/recall@8 + per-question hits.
8. **Operational re-ingest + baseline.** Re-push canonical docs for local
   projects; run the eval before/after to record the chunking improvement as
   the Slice 1 baseline. Verify: `/query/debug` shows new payload fields;
   answers on existing projects remain correct.

## Verification Strategy

- `cd RAG_pipeline/backend && pytest` — full suite, plus new tests above.
- Filter edge cases to exercise explicitly: admin with project selected,
  non-admin with no role groups, empty project assignments (`__none__`
  blocking), `document_ids` restriction combined with layer default.
- Mixed-corpus test: one legacy-style point (no `source_layer`) + one new
  point in a test collection — default search returns both; explicit
  `source_layers=["technical_analysis"]` returns neither.
- Chunker property: re-chunking the same input is deterministic; every chunk
  begins with `[` heading-path prefix when headings exist.
- Manual: `docker-compose up -d`, re-push one project, ask a known question in
  the chat UI — answer quality unchanged or better; citations still render
  (filename format untouched this slice).
- Baseline eval numbers recorded in the slice log for Slice 6 comparison.

## Out of Scope

- Internal-layer ingestion, entity-index ingestion, filename sanitization,
  MCP server / `RagBridge` changes (Slice 3).
- Harness `_rag_support/` persistence (Slice 2).
- Retrieval ladder, insufficiency gate, doc-gap logging, citation display
  names (Slice 4).
- Entity-aware query path, recall widening, query expansion enablement,
  section-parent expansion (Slice 5) — the `unit` payload index also waits
  for Slice 5.
- Golden-set curation beyond the starter fixture (Slice 6).
- Frontend changes — none.
