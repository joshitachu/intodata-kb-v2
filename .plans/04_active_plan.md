---
feature: Layered RAG Over Final Docs and Phase 2 Analysis
slice: Slice 6 - Evaluation harness and tuning
sub_project: rag_pipeline
critical_area: no - evaluation harness, fixtures, committed eval report, and data-backed config tuning only
verdict: PASS
approved_at: 2026-06-13
depends_on:
  - Slice 5 committed as 1a34f96: deep-rung retrieval quality
inspection_basis:
  - Slice 5 inspection WARN: alias matcher currently loads every project alias row per QA query
  - Slice 5 inspection WARN: rung-2 worst-case latency grew because fallback can run expansion, several searches, reranks, unit fetches, section expansion, and extra LLM calls
---

## Problem Statement

Slices 1-5 implemented the layered RAG path: canonical markdown chunking, internal-layer ingestion, alias/entity indexing, conversational retrieval ladder, doc-gap logging, and deeper rung-2 recall. What is still missing is a repeatable evidence gate proving the retrieval ladder answers the right questions from the right layer and refuses unsupported questions.

The current `scripts/retrieval_eval.py` is still a Slice 1 baseline. It loads a simple fixture of `{question, expected_filename}` and reports filename recall before and after rerank. It does not measure:

- whether final-doc-answerable questions stay on `canonical_docs`;
- whether Phase-2-only, entity-lookup, risk, config, and dependency questions descend to the correct internal layer;
- refusal correctness for unsupported questions;
- `fallback_used`, `primary_source_layer`, and doc-gap behavior from the conversational ladder;
- the Slice 5 inspection follow-ups: alias lookup scale and fallback-path latency;
- data-backed verdicts on whether an LLM verifier or `_doc_pack_*` indexing is actually needed.

Slice 6 should turn the starter utility into an operator-facing golden-set harness and commit the first report from at least one real generated project.

## Proposed Solution

Extend `RAG_pipeline/backend/scripts/retrieval_eval.py` into a structured evaluation runner with two scopes:

1. Retrieval metrics.
   - Load a richer golden fixture schema with typed cases.
   - Measure recall@30 pre-rerank using hybrid candidates.
   - Measure recall@8 post-rerank using the configured rerank path.
   - Measure expected source-layer presence and top-result layer selection.
   - For internal cases, measure rung-2 retrieval through `search_layered()` with explicit layer routes and matched units when entity expectations are present.

2. Conversational ladder metrics.
   - Exercise `_prepare_rag_context()` against a transient conversation so the same ladder contract used by chat is evaluated.
   - Record `fallback_used`, `primary_source_layer`, `doc_gap`, returned sources, refusal outcome, and end-to-end elapsed time.
   - Score unsupported cases as pass only when the answer refuses cleanly and does not make fixture-defined factual claims.
   - Record alias-table row count and alias-match elapsed time per evaluated project to make the Slice 5 scaling warning visible.

Add an operator-curated fixture for the existing Koopmans generated project, expanding the starter fixture beyond canonical filename checks. The fixture should cover at least these categories:

- `final_doc_answerable`
- `phase2_only`
- `unsupported`
- `entity_lookup`
- `risk_config_dependency`

Commit a stable Markdown evaluation report with the Slice 6 results and verdicts. If the data supports a config adjustment, tune only existing settings in `src/config.py` and record before/after values in the report. Do not implement a verifier or `_doc_pack_*` indexing in this slice; the report should say whether the data justifies either as follow-up work.

## Affected Files

- `RAG_pipeline/backend/scripts/retrieval_eval.py`
- `RAG_pipeline/backend/scripts/eval_fixtures/koopmans.json` or a new `RAG_pipeline/backend/scripts/eval_fixtures/layered_rag_koopmans.json`
- `RAG_pipeline/backend/scripts/eval_reports/layered_rag_slice6.md` (new committed result)
- `RAG_pipeline/backend/scripts/eval_reports/layered_rag_slice6.json` (optional machine-readable summary, if useful and small)
- `RAG_pipeline/backend/src/config.py` (only if the evaluation justifies threshold or recall tuning)
- `RAG_pipeline/backend/tests/test_scripts/test_retrieval_eval.py` (new)

## Architecture Contracts

- This slice is evaluation-first. Do not change `src/retrieval/`, `src/generation/conversational_rag.py`, API routes, auth, or RBAC in the Slice 6 implementation unless a new active plan is written or this plan is explicitly amended and re-approved.
- Every live Qdrant query in the eval runner must still pass `user_groups` and project scope. Admin-level evaluation may use `["admin"]`, but the script must make that visible in the report.
- `source_layers=None` keeps the established default contract: `canonical_docs` plus legacy chunks with empty source layer. Internal-layer checks must pass explicit source layers through `search_layered()`.
- Fixture data may include questions, expected filenames, sections, units, layers, and expected answer snippets. It must not include credentials, raw source-code dumps, generated prompt traces, or large copied document bodies.
- The committed report is the durable artifact. Do not commit temporary traces, raw LLM prompts, full retrieved chunks, or local stack logs.
- Doc-gap semantics remain unchanged: a gap is valid only when rung 2 answers what final docs could not. Unsupported refusals are not doc gaps.
- Alias lookup scaling is measured in the eval report through project alias-row counts and matcher elapsed time. Do not optimize the matcher in this slice unless the plan is amended, because that would touch critical retrieval code.
- Fallback-path latency is measured and reported by category. The report should flag p95 or worst-case timings that make Slice 5's deep-rung path operationally risky.

## Implementation Steps

1. Define the expanded fixture schema.
   - Support fields such as `id`, `category`, `question`, `project_id`, `expected_behavior`, `expected_filename`, `expected_section`, `expected_unit`, `expected_source_layers`, `expected_primary_source_layer`, `expected_answer_contains`, `forbidden_answer_contains`, `must_pass`, and `notes`.
   - Keep backward compatibility with the current starter fixture so existing `{question, expected_filename}` items still evaluate as canonical retrieval cases.
   - Add validation that required fields are present for each category and that unsupported cases cannot require an expected source.

2. Refactor `retrieval_eval.py` around small typed helpers.
   - Add dataclasses or Pydantic models for fixture cases, per-case result rows, aggregate metrics, and report output.
   - Split loading, search execution, ladder execution, scoring, and report rendering into separately testable functions.
   - Keep the script runnable from either repo root or `RAG_pipeline/backend/`, matching the existing utility.

3. Add retrieval metric execution.
   - Keep recall@30 pre-rerank using `search_similar_hybrid(..., top_k=30, threshold=0.0)`.
   - Keep recall@8 post-rerank using `search_and_rerank(..., top_k=8)`.
   - Add source-layer scoring from returned `Source.source_layer`.
   - For internal-layer cases, call `search_layered()` with explicit route layers and expected matched units when the fixture defines entity expectations.
   - Score matches by filename first, then optional section/unit/layer expectations when supplied.

4. Add conversational ladder execution.
   - Build a transient `Conversation` object with no prior messages for each case.
   - Call `_prepare_rag_context()` with the real Qdrant client, async DB session, project scope, `precompute=True`, and admin evaluation groups.
   - Capture `precomputed_answer`, `document_sources`, `primary_source_layer`, `fallback_used`, `doc_gap`, and elapsed time.
   - For answerable cases, check expected source metadata and optional answer substrings.
   - For unsupported cases, require no factual answer according to the fixture's forbidden snippets and require no doc-gap event semantics.

5. Surface Slice 5 inspection metrics.
   - Count `EntityAlias` rows for the evaluated project before the run.
   - Time `match_query_entities()` for entity cases and include per-case and aggregate timings.
   - Track ladder elapsed time for rung-1 successes, rung-2 answers, and unsupported rung-2 refusals separately.
   - Add report warnings when alias rows or fallback latency exceed configured reporting thresholds.

6. Add the golden Koopmans fixture.
   - Preserve the existing final-doc questions from `koopmans.json`.
   - Add Phase-2-only, entity-lookup, unsupported, and risk/config/dependency questions against the real generated project currently used for local validation.
   - Mark the minimum operator smoke set with `must_pass: true`.
   - Keep the fixture compact and curated rather than trying to exhaustively encode every generated document.

7. Add report generation.
   - Write a Markdown report with environment metadata, timestamp, git commit, config values, fixture path, project id, category tables, aggregate pass/fail gates, latency summary, and notes.
   - Include data-backed decisions for:
     - whether current thresholds/recall settings should change;
     - whether an LLM verifier is warranted;
     - whether `_doc_pack_*` questions justify indexing additional artifacts.
   - Optionally write a small JSON summary for later CI consumption.

8. Tune config only if the report justifies it.
   - Candidate fields are existing retrieval settings such as `default_threshold`, `reranker_min_score`, `rung2_prefetch_limit`, `rung2_rerank_top_n`, `rung2_entity_match_limit`, and `rung2_section_expansion_limit`.
   - Make one minimal tuning pass, rerun the eval, and record before/after metrics.
   - If no tuning is justified, leave `src/config.py` untouched and state that in the report.

9. Add focused unit tests for the harness.
   - Fixture parsing and backward compatibility.
   - Category validation.
   - Filename/section/unit/layer scoring.
   - Unsupported/refusal scoring.
   - Aggregate threshold verdict calculation.
   - Markdown report rendering without large raw context leakage.

## Verification Strategy

Targeted unit tests:

```bash
cd RAG_pipeline/backend
uv run pytest tests/test_scripts/test_retrieval_eval.py
```

Syntax checks:

```bash
cd RAG_pipeline/backend
python3 -m py_compile scripts/retrieval_eval.py
python3 -m py_compile src/config.py
```

Live evaluation run against the local stack:

```bash
cd RAG_pipeline/backend
uv run python scripts/retrieval_eval.py \
  --fixture scripts/eval_fixtures/layered_rag_koopmans.json \
  --project-id LobsterProfileExportKoopmansTest \
  --mode both \
  --output-md scripts/eval_reports/layered_rag_slice6.md \
  --output-json scripts/eval_reports/layered_rag_slice6.json
```

Backend regression validation:

```bash
cd RAG_pipeline/backend
uv run pytest
```

Workflow gates:

- `/inspect` is not required if implementation stays within the planned non-critical files.
- If implementation touches `RAG_pipeline/backend/src/retrieval/`, `RAG_pipeline/backend/src/generation/conversational_rag.py`, API routes, auth, or RBAC, stop and run `/inspect` before `/validate`.
- Run `/validate` before commit.

## Acceptance Criteria

- The fixture contains at least one real generated project and covers final-doc-answerable, Phase-2-only, unsupported, entity-lookup, and risk/config/dependency cases.
- The eval reports recall@30 pre-rerank and recall@8 post-rerank.
- The eval reports source-layer selection accuracy and refusal correctness.
- The operator must-pass smoke set passes 100%.
- Indexed answer cases reach at least 90% recall@8, or the report identifies the specific misses and the config tuning attempted.
- Source-layer selection accuracy is at least 90%, or the report identifies the specific routing failures.
- Unsupported questions have zero factual answers according to the fixture's forbidden-claim checks.
- The report includes alias-row count, alias-match timing, and rung-2/fallback latency metrics from the Slice 5 inspection warnings.
- The report includes explicit verdicts on LLM verifier need and `_doc_pack_*` indexing need.
- No raw internal `_internal/` paths are presented as user-facing display names in report excerpts.

## Out of Scope

- Implementing an LLM verifier.
- Indexing `_doc_pack_*`, `_bundle_*`, or raw source artifacts.
- Changing retrieval ladder semantics, doc-gap persistence, citations, streaming behavior, auth, or RBAC.
- Building UI dashboards for eval results.
- Large-scale alias matcher optimization; this slice measures and reports the scaling issue unless the plan is amended.
- Re-ingesting production projects or modifying MCP generation output.
