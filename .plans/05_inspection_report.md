---
inspected_at: 2026-06-13
verdict: WARN
slice: 5 — Deep-rung retrieval quality
---

## Verdict Summary
Implementation matches the approved Slice 5 plan, preserves every RBAC/layer
contract on the two new Qdrant access paths, and replaces the noisy heuristic
as required; warnings are performance-scaling concerns, not correctness
defects.

## Check Results
| Check              | Result | Notes |
|--------------------|--------|-------|
| Plan conformance   | PASS   | All 7 steps: entity_matcher, unit/section payload indexes, widened rung-2 settings, prefetch threading, unit payload fetch, forced rung-2 expansion, section expansion, ladder wiring with guarded heuristic fallback. |
| Root cause         | PASS   | Addresses the actual failure (embeddings can't do exact-name lookup) with a deterministic alias→unit-payload path instead of tuning thresholds. |
| Generalized        | PASS   | Unit and section filters both derive from `_build_search_filter` (one RBAC contract); `_source_from_payload` remains the single payload→Source mapping; expansion reuses the established neighbor-expansion pattern. |
| Design patterns    | PASS   | Matcher is a pure service over the alias table returning a typed result; `EntityMatchResult.alias_rows_present` cleanly separates "legacy project" from "no match". |
| Contract respect   | PASS   | Rung 2 still requires explicit `source_layers`; rung 1, `/query`, metadata enrichment, and summary resolution keep default recall and global expansion behavior (tested); no new answer metadata; `entity_aliases` remains the only table. |
| No special-casing  | PASS   | Rung-2 widening lives in settings, not branches; the heuristic fallback is a documented degradation path for unsynced projects, not a per-project shim. |
| Edge cases         | WARN   | Findings 1–2. Covered well otherwise: boundary matching rejects `tDBOutput_12` for alias `tDBOutput_1`, longest-match-first, per-project scoping, no-alias legacy fallback, unit hit surviving semantic miss, section order/cap, forced expansion with global off, images regression test still present. |
| RBAC               | PASS   | `fetch_unit_chunks` and `expand_section_chunks` build on `_build_search_filter` (groups + projects + layers); alias matching is project-scoped; admin-wide matching with `project_ids=None` is plan-authorized and chunk retrieval stays group-filtered. |
| Credential safety  | PASS   | No credentials; alias rows carry names/paths only. |

## Findings

1. **WARN — matcher loads every project alias row per QA query**
   `entity_matcher.py::match_query_entities` does `select(EntityAlias)` scoped
   only by project and matches in Python. Fine at current scale (phase2 = 567
   rows); a 50-group enterprise project could hold tens of thousands of rows,
   making this a per-question full fetch. Follow-up options: SQL-side reverse
   `LIKE` prefilter or a per-project in-memory cache with invalidation on
   alias replace. Not blocking at current deployment scale.

2. **WARN — rung-2 worst-case latency budget grew**
   Deep rung now: 1 expansion LLM call + up to 3 hybrid searches at
   prefetch=150 + 3 Voyage reranks at 75 candidates + unit/section scrolls.
   Only paid on descent (the rare path by design), but worth watching the
   `latency_ms` of fallback answers once real usage accumulates.

3. **Note — metadata bleed with no project selected**
   With `project_ids=None`, alias matching is admin-wide (plan-authorized);
   `detected_entities` on a gap event could name entities from another
   project. Chunk retrieval itself remains RBAC-filtered, so no content leaks.

4. **Note — unit hits depend on the `unit` payload being populated**
   Chunks without `unit` metadata simply never match the payload fetch;
   degradation is to semantic-only behavior (Slice 4 level), never worse.

5. **Note — common-word aliases can cause spurious descent**
   `MIN_ALIAS_LENGTH = 4` filters the worst offenders, but aliases like
   `master` (a DB name) can match prose. Cost is one wasted descent; gap
   events only log when rung 2 actually answers.

## Required Actions

No blocking changes.

WARN (follow-ups, log for Slice 6 / ops):
- Finding 1: alias lookup scaling strategy before onboarding large projects.
- Finding 2: monitor fallback-path latency in usage metrics.
