---
feature: Track B -- Phase 3 input quality (parser fix, BASELINE reduction, targeted context dispatch)
pipeline_step: 2_prd
---

## Executive Summary

Phase 3 consumes 5.1M input tokens per run on the Koopmans reference project. Four
compounding root causes: hint-only dispatch (O(n^2) conversation replay), BASELINE
too broad (51% of bytes in every pack), parser 3 defects (104 KB routed to UNCLASSIFIED),
and 3 waste sources (manifest, CSV indexes, unused pack 09). Fix these in three slices
(parser fix → pack differentiation → targeted dispatch) and reduce Phase 3 input tokens
by >= 40% with no quality regression.

## Problem

Phase 3 dispatches 61 LLM calls per run on Koopmans, consuming 5,126,576 input tokens
(deepseek/deepseek-v4-pro). The cost compounds from 4 causes:

**Hint-only dispatch**: On tool-capable providers, `_complete_core_generation`
(harness.py:3144-3150) drops all context files (`context_files = []`) and relies on
the model paging 348-535 KB evidence packs via 50 KB `read_artifact` reads. Each tool
turn appends to the conversation, replaying the full prior context O(n^2). This is the
dominant driver.

**BASELINE too broad**: `BASELINE_BUNDLE_SECTIONS` contains 6 sections covering 51.1%
of all Phase 2 bytes. The shared floor (~349 KB) is 87-89% of median pack size.
Packs 01 and 02 resolve to identical section sets — there is no differentiation.

**Parser defects**: `_parse_phase2_sections` routes 104 KB per run to UNCLASSIFIED:
75,950 bytes of CROSS_CHUNK_FINDINGS (harness-generated but unregistered), ~21 KB of
plugin guide sections (ANALYSIS_GUIDE mandates sections not registered in
PHASE2_SECTION_NAMES), ~6.8 KB from h4 sub-headings inside known sections breaking
the parser. Because UNCLASSIFIED is in BASELINE, this inflates every pack.

**Waste**: 155 KB manifest in Phase 3 context (harness bookkeeping, not needed by
model), 3.1 MB CSV indexes pushed even when `_source_coverage.json` is absent, 349 KB
pack 09 built by Phase 2.8 but never consumed.

## Goals

1. Phase 3 input tokens per run drop by >= 40% (Koopmans re-run: from ~5.1M).
2. No quality regression on human spot-check of all 10 generated docs.
3. UNCLASSIFIED bytes per group analysis file drop to <= 5 KB.
4. Doc packs 01 and 02 differ by >= 30% by byte count.
5. Pack 09 is no longer built.
6. Manifest and CSV indexes removed from Phase 3 context.
7. All 590 existing tests pass; new tests cover each fix.

## Non-Goals

- Phase 1 investigation, Phase 2 analysis, Phase 2.6 scorecard.
- RAG pipeline, dashboard, scanner CLI.
- Provider-specific optimizations (caching, batching).
- Phase 2 repair (`_phase2_targeted_repair`) — separate track.
- The 4 legacy out-of-scope bugs (validator parentheses, incremental-regen cache,
  preamble stripping, scorecard weight-format mismatch).

## User Stories

1. As the platform owner, I want Phase 3 to push a targeted evidence pack to the model
   (not rely on hint-only tool paging) so I pay for one full read, not O(n^2) replay.
2. As the platform owner, I want doc packs to contain only the sections relevant to
   each document so the model has focused context, not 349 KB of shared floor.
3. As a developer debugging UNCLASSIFIED bloat, I want the parser to correctly route
   all Phase 2 output to named sections so UNCLASSIFIED stays near zero.

## Functional Requirements

**FR-B1 (parser fix)**: Fix `_parse_phase2_sections` (harness.py:1616):
- Change heading regex from `r"^#{1,6}\s+..."` to `r"^### ([A-Z][A-Z0-9_ ]+)\s*$"` so
  h4+ sub-headings inside known sections do not break the parse context.
- Add `"CROSS_CHUNK_FINDINGS"` to `PHASE2_SECTION_NAMES` so harness-generated
  cross-chunk synthesis content routes to its own bucket, not UNCLASSIFIED.
- Add `extra_section_names: frozenset[str] = frozenset()` param to
  `_parse_phase2_sections` + a `_guide_section_names(guide_path)` helper that extracts
  all-caps `### HEADING` names from the plugin ANALYSIS_GUIDE. Thread through
  `_deterministic_phase2_merge` and `_targeted_analysis_bundle_parts`; compute from
  `ctx.analysis_guide_file` in instance callers.

**FR-B2 (BASELINE reduction)**: Change `BASELINE_BUNDLE_SECTIONS` (harness.py:113-120)
from 6 sections to `{GROUP_SUMMARY_SECTION}` only. Each doc pack's section set comes
entirely from its `doc_catalog.yaml` `phase2_sections` list plus this single baseline.
Depends on FR-B1 (parser must correctly classify CROSS_CHUNK_FINDINGS before UNCLASSIFIED
is removed from BASELINE, otherwise 76 KB of synthesis content is silently discarded).

**FR-B3 (catalog differentiation)**: Update `data/doc_catalog.yaml` so docs
`01_ARCHITECTURE_MAP` and `02_COMPONENT_INVENTORY` have distinct `phase2_sections` lists
that differ by >= 30% in resolved pack size. Currently both resolve to the same set.

**FR-B4 (targeted dispatch)**: Replace the hint-only branch in
`_complete_core_generation` (harness.py:3144-3150) with a targeted pack push: when
`ctx` has a doc pack for the target document, pass it as a context file (capped at
100 KB; if the pack exceeds cap, include only GROUP_SUMMARY + the doc's `phase2_sections`
worth of content). Tools remain available for drill-down.

**FR-B5 (manifest removal)**: Remove `_doc_pack_manifest.json` from the Phase 3 context
file list in `_phase3_context_files` (harness.py:~4685-4687). The manifest is harness
bookkeeping; the model uses the pack files directly.

**FR-B6 (CSV index fix)**: In `_engineering_fact_context` (harness.py:~4734-4754), do
not add CSV index entries when `_source_coverage.json` is absent. The current code
builds a fallback CSV list regardless. When absent, the model can use tools to discover
CSV files if needed.

**FR-B7 (drop pack 09)**: In `_phase28_bundles` (harness.py:2277-2301), skip building
`_doc_pack_09_EXECUTIVE_SUMMARY.md`. Doc 09 is not in `required_docs`; no Phase 3 code
path reads this 349 KB file.

**FR-B8 (docs update)**: Update `knowledge_base/CLAUDE.md` (Current Custom Harness
Notes) and `knowledge_base/.agent/system/mcp-workflow.md` to document: targeted
dispatch (not hint-only), GROUP_SUMMARY-only BASELINE, parser scope, pack 09 removal.

## Non-Functional Requirements

- Phase 3 input tokens >= 40% reduction on Koopmans re-run (acceptance criterion).
- No regression in doc obligation pass rate vs the pre-Track-B baseline.
- No new config flags.
- No DB or API schema changes.
- Parser fix is strictly additive: content that already parses into a named section
  must still parse into the same section after the fix.

## Architecture Impact

- **Harness pipeline (critical area — `/inspect` mandatory for each slice)**:
  `harness.py` (PHASE2_SECTION_NAMES, BASELINE_BUNDLE_SECTIONS, _parse_phase2_sections,
  _deterministic_phase2_merge, _targeted_analysis_bundle_parts, _phase28_bundles,
  _complete_core_generation, _phase3_context_files, _engineering_fact_context).
- **Doc catalog**: `data/doc_catalog.yaml` (phase2_sections for 01, 02).
- **Docs**: `knowledge_base/CLAUDE.md`, `knowledge_base/.agent/system/mcp-workflow.md`.
- **Tests**: `test_custom_provider.py` (new tests per slice).
- **No impact on**: Phase 1, Phase 2 LLM calls, Phase 2.6 scorecard, evidence quality
  gate, Phase 4 cleanup, MCP API routes, RAG retrieval, auth/RBAC, dashboard, scanner.

## Implementation Phases

**Slice 1 — Parser fix (FR-B1)**: Fix `_parse_phase2_sections`, register
CROSS_CHUNK_FINDINGS, add extra_section_names mechanism. No BASELINE change.
Acceptance: UNCLASSIFIED <= 5 KB per group file. Tests: 3 new.

**Slice 2 — Pack differentiation (FR-B2, FR-B3, FR-B7)**: BASELINE → GROUP_SUMMARY
only; 01/02 catalog differentiation; drop pack 09. Depends on Slice 1 (UNCLASSIFIED
must be correctly classified before it leaves BASELINE).
Acceptance: 01 vs 02 packs differ >= 30%; pack 09 absent. Tests: 3 new.

**Slice 3 — Targeted dispatch + waste removal + docs (FR-B4, FR-B5, FR-B6, FR-B8)**:
Replace hint-only with 100 KB targeted push; remove manifest and CSV indexes from
context; update docs. Depends on Slices 1 and 2 (targeted pack must be differentiated
and correctly parsed before this is meaningful).
Acceptance: >= 40% Phase 3 token reduction on Koopmans re-run; no quality regression.
Tests: 4 new.

## Risks and Open Questions

- If a doc's `phase2_sections` in `doc_catalog.yaml` is underspecified, the targeted
  pack will be thin and the model will over-use tools. Mitigated by the acceptance run.
- If CROSS_CHUNK_FINDINGS removal from UNCLASSIFIED (via parser fix) reduces pack
  quality for docs that were implicitly benefiting from UNCLASSIFIED content, those docs
  may need explicit `phase2_sections` entries. Mitigated by the acceptance run spot-check.
- Parser fix is additive and has tests; no regression risk.
- Pack 09 drop is safe (confirmed by grep: no caller reads it).

## Acceptance Criteria

- [ ] `_parse_phase2_sections` h4 sub-headings do not break owning section.
- [ ] `CROSS_CHUNK_FINDINGS` in PHASE2_SECTION_NAMES; UNCLASSIFIED <= 5 KB/group.
- [ ] `extra_section_names` mechanism works; plugin guide sections classified correctly.
- [ ] `BASELINE_BUNDLE_SECTIONS` = `{GROUP_SUMMARY_SECTION}` only.
- [ ] `01_ARCHITECTURE_MAP` and `02_COMPONENT_INVENTORY` packs differ by >= 30% bytes.
- [ ] `_doc_pack_09_EXECUTIVE_SUMMARY.md` no longer built.
- [ ] `_complete_core_generation` passes a targeted starter pack (>= 1 context file)
      on tool-capable providers.
- [ ] `_doc_pack_manifest.json` absent from Phase 3 context file list.
- [ ] CSV indexes absent from starter files when `_source_coverage.json` is absent.
- [ ] >= 40% Phase 3 token reduction on Koopmans re-run; no quality regression.
- [ ] All existing tests pass (590 baseline); new tests per slice added and green.
- [ ] `/inspect` PASS or WARN for each slice before commit.
- [ ] `CLAUDE.md` and `mcp-workflow.md` updated.
