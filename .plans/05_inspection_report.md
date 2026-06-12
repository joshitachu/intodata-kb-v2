---
inspected_at: 2026-06-12
verdict: BLOCK
slice: 4 — Retrieval ladder and doc-gap logging
block_cycle: 1
---

## Verdict Summary
The ladder matches the approved plan and preserves RBAC/layer contracts, but
the rung-1 precompute silently drops the `images` vision parameter for every
fresh QA question — a functional regression that must be fixed before commit.

## Check Results
| Check              | Result | Notes |
|--------------------|--------|-------|
| Plan conformance   | PASS   | All 8 plan steps implemented: schemas, source metadata preservation, DocGapEvent + migration 029, search_layered, deterministic routing + identifier checks, ladder wiring, persistence/SSE, frontend display_name. |
| Root cause         | PASS   | Two-rung ladder with explicit layer search addresses the actual gap (default partition never descends); doc-gap events stored in a dedicated table, not bolted onto QueryLog. |
| Generalized        | PASS   | Display-name logic is one helper (`friendly_display_name`) populated at payload mapping; all context builders use `display_name or legacy-format`; `merge_sources_by_document` propagates all new fields once. |
| Design patterns    | PASS   | Strategy selection reused via `search_layered`; deterministic routing table (data, not branches); RAGContext carries ladder metadata as a value object. |
| Contract respect   | PASS   | Threshold 0.3 default untouched; no LangChain; anti-hallucination prompts extended, not weakened (rung 1 uses the existing [NEED_SEARCH] contract); raw `_internal/` paths never rendered (tested). |
| No special-casing  | PASS   | Ladder applies uniformly to the QA search path; METADATA/RESPOND/SUMMARY paths untouched; no per-project or per-layer branches. |
| Edge cases         | BLOCK  | Finding 1 (images dropped). Otherwise well-covered: rung-1 sufficient (1 LLM call, no rung-2 search), need_search + missing-identifier descent, unsupported refusal without gap event, DocGapEvent roundtrip incl. nullables, layer routing units. |
| RBAC               | PASS   | `search_layered` requires explicit `source_layers` and threads `user_groups`/`project_ids` into the same `_build_search_filter`; tested (`test_passes_explicit_layers_and_rbac`). Conversation routes still enforce ownership. |
| Credential safety  | PASS   | No credentials; doc-gap events store query text, layer names, artifact paths, chunk ids — no secret material beyond what the corpus already holds. |

## Findings

1. **BLOCK — `images` dropped for fresh QA questions**
   `_prepare_rag_context` has no `images` parameter, but now precomputes the
   rung-1 answer (conversational_rag.py, rung-1 `generate_answer(rung1_messages)`).
   Before this change, fresh-search QA returned `precomputed_answer=None` and
   the caller generated with `images=images`
   (`run_conversational_rag`:1045, `run_conversational_rag_stream`:1214). Now a
   user attaching a screenshot to a question gets an answer that never saw the
   image. Fix: thread `images` into `_prepare_rag_context` and pass it to the
   rung-1 and rung-2 `generate_answer` calls.

2. **WARN — streaming UX: fresh QA answers now arrive as one blob**
   The ladder gate precomputes the answer, so the SSE stream emits a single
   token for every fresh QA question, not just follow-up reuse. Plan-authorized
   ("Streaming may emit a precomputed answer as a single token for ladder
   decisions"), but it changes perceived latency on the most common path.
   Acceptable for Slice 4; revisit if users notice.

3. **WARN — fallback path costs up to 3 LLM calls**
   Rung-1 gate + rung-2 answer (+ refusal generation when unsupported).
   The happy path stays at one call (tested). Acceptable; the fallback path is
   by definition the rare path, but worth watching in usage metrics.

4. **Note — identifier heuristic can descend spuriously**
   PascalCase words like "JavaScript" count as identifiers; if canonical text
   lacks them, rung 2 runs unnecessarily. Failure mode is graceful (extra
   search + LLM call; gap events only log when rung 2 actually answers).
   Slice 5's alias-table lookup replaces this heuristic.

## Required Actions

BLOCK:
- Finding 1: add `images` to `_prepare_rag_context` and pass through to the
  rung-1/rung-2 generation calls; keep caller behavior for non-precomputed
  paths unchanged.

## Resolution (post-inspection, same session)

- Finding 1: FIXED — `images` threaded into `_prepare_rag_context` and both
  ladder generation calls; callers pass it; regression test added
  (`test_rung1_passes_images_to_generation`). Full suite re-run green.
- Findings 2-4: documented above as accepted for Slice 4.

Re-inspection after fix: verdict WARN (findings 2-3 stand as accepted).
