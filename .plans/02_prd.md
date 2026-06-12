---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
pipeline_step: 2_prd
revision: 2
revision_note: >
  Rewritten 2026-06-12 after code-level verification of the generation pipeline,
  ingestion path, and retrieval stack. Adds NotebookLM-level retrieval quality as
  a first-class goal, promotes structure-aware chunking and entity-aware retrieval
  from optional to load-bearing, and encodes eight verified code gaps as binding
  requirements.
---

## Executive Summary

Phase 3 final docs remain the canonical, user-facing documentation. RAG gains a
hidden layered corpus built from Phase 2 analysis and support artifacts, plus a
retrieval-quality upgrade so that any fact present in the indexed corpus is
reliably found — the NotebookLM bar: the answer engine should never miss
something that exists in its sources.

Two workstreams, one feature:

1. **Layered corpus** — persist and index selected Phase 2 analysis and support
   artifacts (currently deleted by Phase 4 cleanup) as internal answer layers
   behind the visible docs, with a retrieval ladder that descends only when the
   shallower layer cannot support the answer.
2. **Retrieval quality** — structure-aware contextual chunking, entity-aware
   retrieval driven by the scanner's entity index, widened recall on the deep
   rung, and a measurable retrieval evaluation (recall@k), because indexing
   richer content is wasted if retrieval cannot surface it.

Done means: representative technical questions answer correctly when the answer
exists in Phase 3 docs **or** Phase 2 analysis; unsupported questions fail
cleanly; retrieval recall is measured, not assumed; final docs remain the only
visible documentation product.

## Problem

The current RAG corpus is only the ~10 numbered Phase 3 docs
(`RagBridge._read_md_files` digit-prefix gate). Phase 3 docs are compressed by
design. The richer material — per-group, per-unit Phase 2 analysis — is
verified to be ~1.5x the volume of the final docs (Koopmans run: 547KB+ of
group analyses vs 391KB of final docs) with per-unit subsections, and it is
**deleted at the end of every successful run** by Phase 4 cleanup
(`pipeline/phases/phase4_cleanup.py:76-79` removes `_preprocess`, `_analysis`,
`_transient` before the staging swap).

Two compounding problems:

- **Corpus**: deep technical facts exist upstream but never reach RAG.
- **Retrieval**: chunks are context-blind 512-token paragraph windows
  (`chunk_text_semantic`) with no heading, group, unit, or section signal, so
  even indexed facts about named entities (jobs, tables, endpoints) are found
  only when embedding similarity happens to cooperate.

A verified side-effect of the same root cause: `projects.py:871-878` tries to
copy `docs/_analysis/` to `analysis_cache/` for incremental webhook
regeneration, but `_analysis` is already deleted, so `analysis_cache` exists in
zero output projects and `git_reingestion.py` silently falls back to full regen
every time. Fixing Phase 2 persistence fixes this dead path too.

## Goals

1. Index selected Phase 2 analysis and support artifacts as internal layers,
   with final docs remaining the only visible documentation.
2. Any fact present in an indexed layer is retrievable: measured retrieval
   recall@8 (post-rerank) >= 90% on a golden evaluation set per test project.
3. Correct source-layer selection >= 90% on the evaluation set; zero
   unsupported evaluation questions answered as factual claims.
4. Every answer records which layer supplied it; Phase 2 fallback that fills a
   final-doc omission emits a doc-gap event.
5. RBAC filtering on every retrieval path, including all fallback rungs.
6. The happy path (final docs answer) pays no Phase 2 retrieval or extra LLM
   cost.
7. Incremental webhook regeneration works again (`analysis_cache` populated).

## Non-Goals

- Do not replace or de-emphasize final docs in any UI.
- Do not index `_model_inputs/*`, `_agent_tasks/*`, `_debug/*`, `_transient/*`,
  `_analysis/_doc_pack_*.md`, `_analysis/_bundle_*.md`, prompt snapshots,
  provider traces, or repair scratch files.
- Do not index raw source evidence (scan cache) in this feature. Reserve the
  `source_evidence` value in the `source_layer` enum so the metadata schema
  supports it later without migration, but ship nothing for it.
- Do not build an LLM answerability verifier with calibrated confidence scores
  in the first pass — extend the existing deterministic `[NEED_SEARCH]`
  mechanism first (decision: previously an open question, now resolved).
- Do not swap embedding models, add graph RAG, or build agentic multi-hop
  loops — the existing hybrid + rerank stack is sufficient once chunks carry
  structure.
- Do not add RAG push instructions to LLM prompts; server-side ingest remains
  responsible.
- Do not weaken RBAC or bypass Qdrant payload filtering.
- Do not guarantee literally every question is answerable — only that what
  exists in indexed layers is found, and what does not exist fails cleanly.

## User Stories

- As a user reading generated docs, I want the visible documentation unchanged
  and clean, so internal analysis never clutters my view.
- As a user asking a deep technical question about a specific job, table,
  mapping, endpoint, or config, I want the system to find the exact subsection
  of Phase 2 analysis that covers it, so I get a precise grounded answer
  instead of "the documentation does not cover this."
- As a user asking about a named entity (e.g. `API_Contracts_Backup2`), I want
  retrieval to treat the exact name as a first-class signal, so identifier
  questions never miss due to embedding fuzziness.
- As an operator, I want doc-gap events whenever Phase 2 supplied facts the
  final docs lacked, so repeated gaps drive doc improvements.
- As an admin/security owner, I want internal layers to enforce the same
  project access controls as visible docs on every retrieval path.
- As a developer, I want retrieval quality measured by recall@k on a golden
  set, so tuning is evidence-driven instead of vibes.

## Functional Requirements

### FR1 — Source layers

| Layer | Source | Visibility | Purpose |
|---|---|---|---|
| `canonical_docs` | Phase 3 final `NN_*.md` + pack docs | public | Visible docs, first-rung answers |
| `technical_analysis` | `_analysis/{group}.md`, one per group in `facts_manifest.groups` | internal | Deep per-unit technical answers |
| `support_maps` | all `*.md` in `_facts/` (dependency, config-runtime, known-risks, engineering-facts, architecture, data-flow, api-surface, test-coverage, change-impact maps) | internal | Dependency, config, risk, system-fact support |
| `scorecard` | `_assessment/scorecard.md` | internal | Risk/quality answers |
| `evidence_slices` | `_evidence_registry/doc_slices/*.md`, if present | internal | Source grounding, citation backup |
| `source_evidence` | reserved enum value only | — | Future scan-cache layer; nothing ships |

Exclusions (must-not-index) are the Non-Goals list above. `doc_slices`
presence varies by run mode; absence is not an error.

### FR2 — Artifact persistence (`_rag_support/`)

Phase 4 cleanup must build `docs_staging/_rag_support/` before deleting
intermediates: copy `_analysis/{group}.md` for each group listed in
`state.facts_manifest.groups` (a positive, manifest-driven allowlist — never a
"everything except `_doc_pack_*`/`_bundle_*`" pattern, so future runtime
artifacts can't leak in) into
`_rag_support/phase2_analysis/`, and write a manifest
(`_rag_support/manifest.json`) listing artifact path, layer, group, and byte
size. The underscore prefix means existing push (`_read_md_files`) and UI
logic already ignore it, and it survives the atomic swap into `docs/` the same
way `_facts/` and `_assessment/` do.

The post-swap `analysis_cache` copy in `projects.py` must be repointed at the
persisted group analyses so incremental webhook regen works again.

### FR3 — Chunk metadata (new plumbing, verified absent today)

`ingest_text` currently discards the request `metadata` dict (only
`author`/`department` reach the Document row) and `upsert_chunks` writes a
fixed five-field payload. Required changes:

- Thread a metadata dict per document through `ingest_text` → `upsert_chunks`
  into the Qdrant payload.
- Required chunk payload fields: `project_id`, `source_layer`, `artifact_type`,
  `visibility`, `artifact_path`, `group`, `unit`, `section` (heading path),
  `doc_name`, `chunk_index`, `allowed_groups`, plus `run_id` when available.
- Extend the `Source` schema (currently five fields) to carry `source_layer`
  and display metadata through retrieval and into answer/citation handling.

### FR4 — Structure-aware contextual chunking (load-bearing)

Phase 2 analyses and final docs are rigidly heading-structured
(`# Phase 2 Analysis: {group}` → `### SECTION_NAME` → `#### {unit}`). A new
markdown-structure chunker must:

- split on heading boundaries (falling back to paragraph merging within a
  section, ≤512 tokens per chunk);
- prepend the heading path to every chunk's text before embedding, e.g.
  `[{project} > {group} > {SECTION} > {unit}]`;
- emit `section`, `unit`, and `group` metadata per chunk;
- apply to both internal layers and canonical docs (re-ingested).

This is the single highest-impact retrieval change and is required, not
optional. Plain `chunk_text_semantic` remains for uploaded user files.

### FR5 — Layer-aware retrieval and the ladder

- `_build_search_filter` gains a `source_layer` condition. **Default retrieval
  is canonical-only.** Chunks lacking the `source_layer` payload field (all
  pre-existing chunks) must be treated as canonical via an is-empty-tolerant
  condition, so nothing breaks before re-ingestion.
- Query flow is a retrieval ladder: rung 1 `canonical_docs`; rung 2
  `technical_analysis` + query-type-targeted support layers. Descend only on
  insufficiency. The ladder generalizes to N rungs; only two ship now.
- Insufficiency detection extends the existing `[NEED_SEARCH]` contract and
  `needs_new_search()` regex safety net in `conversational_rag.py`, plus a
  deterministic entity-presence check (question entities found in retrieved
  chunks). No new LLM verifier call on the happy path.
- Query-type routing for rung 2 (deterministic heuristics first):

| Query type | Rung-2 layers |
|---|---|
| Job/unit/component detail | technical_analysis |
| Field/table/mapping/endpoint | technical_analysis + evidence_slices |
| Runtime/config | technical_analysis + support_maps |
| Dependency/lineage | technical_analysis + support_maps |
| Risk/security/quality | scorecard + support_maps + technical_analysis |
| Overview/summary | canonical_docs (rung 2 only if incomplete) |

### FR6 — Entity-aware retrieval

`entity_index.csv` / `usage_index.csv` (persisted in `docs/_facts/`) define the
canonical entity vocabulary per project. Requirements:

- Ingest the entity index into a per-project alias lookup (not the vector
  corpus) at push time.
- At query time, detect exact/alias entity mentions; when found, add a
  guaranteed lexical/payload match on the `unit` field alongside semantic
  search, so identifier questions cannot miss an indexed chunk.
- Never hardcode entity or group names.

### FR7 — Deep-rung recall settings

- Rung 2 uses widened recall: `prefetch_limit` ~150 and `rerank_top_n` ~75
  (configurable), reranker remains the precision gate.
- Query expansion (`query_expansion_enabled`, currently False) is enabled for
  rung 2 only; rung 1 keeps current latency.
- Section-parent retrieval: when chunks carry `section` metadata, fetch the
  owning section's sibling chunks (generalizing `expand_neighbor_chunks`) so
  the LLM reads complete passages.

### FR8 — Ingestion lifecycle

- Internal artifacts ingest with layer-prefixed scoped filenames
  (`{project_id}/_analysis/{group}.md` pattern) to avoid collisions with
  visible docs.
- Filenames must be sanitized/URL-encoded: real group names contain pipes and
  spaces (`Training|Jasmijn.md`), and `delete_document` puts filenames in URL
  paths.
- Idempotency: per-filename replace already exists (`_replace_existing_document`).
  For internal layers, each generation push must first delete the project's
  internal-layer documents and re-push the full set, because
  `_diff_generated_docs` only diffs visible docs and removed groups would
  otherwise leave stale chunks forever.
- **Ordering requirement**: the layer-aware default filter (FR5) must be
  deployed before or with the first internal-artifact push. Internal chunks
  entering an unfiltered corpus pollute every retrieval path (QA, metadata
  enrichment, summary resolution, map-reduce).

### FR9 — Answer metadata, citations, and the reuse path

- Every answer records `primary_source_layer`, `fallback_used`, `doc_gap`.
- Internal-layer citations must use friendly display names (e.g. "Technical
  analysis: Intodata") — `DocumentSource.filename` is rendered in the UI today
  and raw internal paths must not leak there.
- Conversation messages persist sources, and `get_previous_sources` replays
  them on follow-ups, bypassing search. Persisted sources must therefore carry
  `source_layer`, and the context-first reuse path must apply the same display
  policy. Reusing internal sources for follow-ups is allowed (the gate already
  passed when they were first retrieved).
- If no indexed layer supports an answer, the system says the available
  documentation/analysis does not contain enough information. The existing
  anti-hallucination prompt rules and context-boundary footer remain.

### FR10 — Doc-gap logging

When rung 2 supplies material facts missing from final docs, log an event
with: project_id, run_id (when available), query text, detected entities,
rung-1 insufficiency reason, layers used, supporting artifact paths/chunk ids,
timestamp. Storage may reuse the existing query-log table with a typed
metadata field; a separate table is acceptable if query logs cannot hold it.
Doc-gap events must be queryable for repeated-omission analysis.

## Non-Functional Requirements

- RBAC (`allowed_groups`) on every Qdrant query, all rungs, including
  neighbor/section expansion fetches.
- Internal layers retrievable only by users with project access.
- Rung 1 latency unchanged (no extra LLM calls, no wider retrieval).
- Rung 2 adds bounded latency: target < 2x rung-1 retrieval+generation time;
  acceptable because deep questions trade speed for correctness.
- Re-ingestion safe: chunking/metadata changes require only delete-by-project +
  re-push; no schema migration for chunk payloads (Qdrant payloads are
  schemaless), at most one Postgres migration if doc-gap events get their own
  table.
- ASCII-compatible markdown throughout; no hardcoded project/group names.
- Retrieval behavior testable without a live generation run (fixtures from a
  persisted `_rag_support/` package).
- Voyage rerank free tier is 3 RPM — rung 2's second rerank call must respect
  the existing retry/backoff and degrade gracefully to RRF order.

## Architecture Impact

- **Harness pipeline** (critical area, `/inspect` required):
  `phase4_cleanup.py` builds `_rag_support/` before deleting `_analysis`;
  selection allowlist lives next to the phase. No changes to Phases 1-3 or the
  enterprise gates.
- **MCP API** (critical area, `/inspect` required): `projects.py`
  `_run_generate_background` pushes internal layers after the visible-doc push,
  with metadata; `analysis_cache` copy repointed. `RagBridge` gains
  metadata-carrying push and internal-layer delete.
- **RAG backend ingestion**: `ingest_text`/`upsert_chunks` metadata
  passthrough; new markdown-structure chunker module alongside
  `semantic_chunker.py`; entity-index ingestion.
- **RAG backend retrieval** (critical area, `/inspect` required):
  `_build_search_filter` layer condition with legacy tolerance; ladder
  orchestration in `_prepare_rag_context`'s QA path; deep-rung recall config;
  section-parent expansion; entity detection.
- **Schemas**: `Source`/`DocumentSource` gain layer + display fields; persisted
  message sources include them.
- **Frontend**: no redesign; citations show display names. Optional later:
  admin-only layer marker, doc-gap report panel.
- **Data model**: chunk metadata is Qdrant payload (no migration). Doc-gap
  events: query-log metadata field or one new table.

## Implementation Phases

### Phase A — Retrieval foundation (chunking + metadata + filter)
Markdown-structure chunker with heading-path prefixes; metadata plumbing
through ingest → Qdrant payload → `Source`; layer-aware default filter with
is-empty tolerance; re-ingest canonical docs for existing projects.
Validation: legacy queries unchanged; structured chunks carry
section/unit/group; recall measured on canonical corpus as baseline.

### Phase B — Artifact persistence in the harness
`_rag_support/` package + manifest in Phase 4; `analysis_cache` restored;
visible docs byte-identical to before. Validation: run generation, confirm
package contents match allowlist, swap unaffected, `/inspect` pass.

### Phase C — Layered ingestion
Push internal layers with metadata after doc push; filename sanitization;
internal-layer delete + full re-push per generation; entity-index ingestion.
Validation: internal chunks present with correct payloads; default retrieval
still returns canonical only; repeated pushes idempotent.

### Phase D — Retrieval ladder
Insufficiency detection (extended `[NEED_SEARCH]` + entity-presence check);
rung-2 retrieval with query-type routing, widened recall, query expansion,
section-parent fetch; answer/citation metadata incl. reuse path; doc-gap
logging. Validation: answerable-from-docs, Phase-2-only, and unsupported
question fixtures behave per acceptance criteria.

### Phase E — Evaluation and tuning
Golden set from >= 1 real generated project: final-doc-answerable,
Phase-2-only, unsupported, entity-lookup, and risk/config/dependency
questions. Measure recall@30 pre-rerank, recall@8 post-rerank, source-layer
selection accuracy, refusal correctness. Tune thresholds/recall settings.
Decide (data-driven) whether an LLM verifier or `_doc_pack_*` indexing is ever
needed.

## Risks and Open Questions

### Risks

- Phase 2 prose may contain intermediate wording or duplication that degrades
  precision — mitigated by reranker gate and excluding bundles/doc packs.
- Insufficiency detection may under-fire (final docs give a shallow but
  confident answer) — mitigated by entity-presence check and eval set; the
  regex safety net already catches "couldn't find" answers under 200 chars.
- Re-ingesting canonical docs for all projects is a one-time operational step;
  missing it leaves mixed chunking generations in one corpus.
- Group filenames with unusual characters may surface more edge cases than
  pipes/spaces; sanitization must be allowlist-based, not blocklist.
- Doc-gap noise if every rung-2 use is logged — only material-fact fallback
  (rung 1 insufficient) logs an event.
- Voyage dependency on both embed and rerank: rung 2 doubles rerank calls;
  rate-limit degradation path must keep answers flowing.

### Open Questions

- Should doc-gap events get their own table or live in query-log metadata?
  (Decide in slices; both acceptable per FR10.)
- Should admins see a "answered from technical analysis" marker in v1, or is
  answer metadata in the API response enough until the operator panel exists?
- Exact golden-set composition and size per project (operator picks during
  Phase E; minimum categories are fixed by FR/AC).

Resolved since revision 1: gate starts deterministic (no LLM verifier);
`rag_support` location is `docs_staging/_rag_support/` (underscore-prefixed,
survives swap); Phase 1/source evidence is enum-reserved but out of scope;
`_doc_pack_*` stays excluded pending Phase E data.

## Acceptance Criteria

- [ ] Final visible docs are byte-identical in content and location to
      pre-feature behavior; `_rag_support/` never appears in doc lists or UI.
- [ ] A successful generation leaves `docs/_rag_support/` with manifest, only
      allowlisted artifacts, and a populated `analysis_cache/`.
- [ ] `_model_inputs/*`, `_agent_tasks/*`, `_debug/*`, `_transient/*`,
      `_doc_pack_*.md`, `_bundle_*.md` are not indexed.
- [ ] Every new chunk payload carries `source_layer`, `artifact_type`,
      `visibility`, `project_id`, `group`/`unit`/`section` where applicable,
      and `allowed_groups`; chunk text begins with its heading path.
- [ ] Default retrieval returns only canonical chunks, including for legacy
      chunks lacking `source_layer`.
- [ ] A question answerable from final docs answers from rung 1 with no rung-2
      retrieval and no added latency.
- [ ] A question answerable only from Phase 2 descends the ladder and answers
      with internal-layer support, friendly citations, `fallback_used=true`,
      and a doc-gap event.
- [ ] A question naming an indexed entity retrieves that entity's chunks via
      the entity-aware path even when pure embedding search misses.
- [ ] An unsupported question yields a clean "not enough information" answer
      with no fabricated claims.
- [ ] Follow-up questions reusing previous internal sources keep layer
      metadata and display policy; raw internal paths never render in the UI.
- [ ] RBAC verified on every rung: a user without project access retrieves
      nothing from any layer of that project.
- [ ] Repeated generation pushes are idempotent; removing a scanner group
      removes its internal chunks on the next push.
- [ ] Tests cover: chunker structure/heading paths, metadata assignment,
      allowlist/exclusions, filter legacy tolerance, ladder routing,
      insufficiency detection, entity matching, sanitized filenames, doc-gap
      events, RBAC on fallback paths.
- [ ] Evaluation set passes: 100% on operator must-pass smoke set; >= 90%
      recall@8 for golden questions whose answer is indexed; >= 90% correct
      source-layer selection; zero unsupported questions answered factually;
      no known case where a materially better indexed source existed but was
      not used.
