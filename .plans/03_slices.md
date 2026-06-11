---
feature: Track B -- Phase 3 input quality (parser fix, BASELINE reduction, targeted context dispatch)
pipeline_step: 3_slices
total_slices: 3
current_slice: 1
# Pre-condition: Track A (Slices 1-5) must be committed before any Track B slice
# starts. Track B Slice 1 touches harness.py which has uncommitted Track A FR-8
# changes. Hard sequencing dependency -- do not start Track B until Track A lands.
---

## Slice 1: Phase 2 section parser fix (FR-B1)
**Delivers**: `_parse_phase2_sections` correctly classifies all Phase 2 output --
merge headers, plugin guide sections, and all-caps sub-headings stop leaking 104 KB
into UNCLASSIFIED per run; UNCLASSIFIED bytes per group file drop to <= 5 KB.
**Expected files**:
- EDIT `providers/custom/harness.py` (`_parse_phase2_sections` :1615-1636;
  `PHASE2_SECTION_NAMES` or a new `MERGE_SECTION_NAMES` tuple to register
  `CROSS_CHUNK_FINDINGS` and other merge-phase headers; sub-heading boundary fix)
- EDIT `providers/custom/prompt_packs.py` or `harness.py` to accept optional extra
  section names derived from the active plugin pack's ANALYSIS_GUIDE (do not
  hardcode lobster-specific names)
- EDIT `mcp-server/tests/test_providers/test_custom_provider.py` (new tests:
  CROSS_CHUNK_FINDINGS parsed as named section not UNCLASSIFIED; `####` sub-heading
  inside a known section does not break the owning section; unknown plugin guide
  section name registered via extra_sections arg parses correctly)
**Critical area**: yes -- harness pipeline phases (`/inspect` required)
**Depends on**: none (Track A must be committed first -- see pre-condition)

## Slice 2: Pack differentiation -- BASELINE reduction, catalog 01/02 fix, drop pack 09 (FR-B2, FR-B3, FR-B7)
**Delivers**: Per-doc evidence packs are genuinely differentiated -- BASELINE reduced
to GROUP_SUMMARY only; packs 01 and 02 differ by >= 30% by byte count; Phase 2.8 no
longer builds the 349 KB pack 09 that no code path reads.
**Expected files**:
- EDIT `providers/custom/harness.py` (`BASELINE_BUNDLE_SECTIONS` :113-120 ->
  `{GROUP_SUMMARY_SECTION}` only; `_phase28_bundles` :2277-2301 to skip building
  `_doc_pack_09_EXECUTIVE_SUMMARY.md`)
- EDIT `mcp-server/data/doc_catalog.yaml` (`phase2_sections` for
  `01_ARCHITECTURE_MAP` and `02_COMPONENT_INVENTORY` -- add distinct sections so
  they no longer resolve to the same set)
- EDIT `mcp-server/tests/test_providers/test_custom_provider.py` (new tests:
  pack-size differentiation assertion for docs 01 vs 02; assert pack 09 absent;
  BASELINE constant has only GROUP_SUMMARY)
**Critical area**: yes -- harness pipeline phases (`/inspect` required)
**Depends on**: Slice 1 (BASELINE removal silently discards 76 KB of CROSS_CHUNK_FINDINGS
content unless the parser correctly classifies merge headers first)

## Slice 3: Targeted dispatch + waste removal + docs (FR-B4, FR-B5, FR-B6, FR-B8)
**Delivers**: Phase 3 tool-capable path pushes a targeted starter pack (capped at
100 KB) instead of hint-only dispatch; manifest and CSV indexes removed from context;
docs updated; before/after acceptance run shows >= 40% reduction in Phase 3 input
tokens with no quality regression on human spot-check.
**Expected files**:
- EDIT `providers/custom/harness.py`:
  - `_complete_core_generation` (:3144-3150) -- replace hint-only branch with
    targeted pack push (capped at 100 KB; take GROUP_SUMMARY + doc's phase2_sections
    only if pack exceeds cap)
  - `_phase3_context_files` (:4685-4687) -- remove `_doc_pack_manifest.json` from
    returned file list
  - `_engineering_fact_context` (:4734-4754) -- add nothing when
    `_source_coverage.json` is absent (model can use tools to read CSV files)
- EDIT `knowledge_base/CLAUDE.md` (Current Custom Harness Notes -- document targeted
  dispatch, GROUP_SUMMARY-only BASELINE, parser scope)
- EDIT `knowledge_base/.agent/system/mcp-workflow.md` (remove hint-only dispatch
  claim; document starter pack + tool drill-down behaviour)
- EDIT `mcp-server/tests/test_providers/test_custom_provider.py` (new tests:
  tool-capable path has context_file_count >= 1; manifest absent from starter files;
  CSV indexes absent when source_coverage absent; pack size regression guard <= 100 KB
  for the pushed starter pack)
**Critical area**: yes -- harness pipeline phases (`/inspect` required)
**Depends on**: Slices 1 and 2 (targeted pack must be differentiated and correctly
parsed before this change is meaningful; acceptance run measures the cumulative result)
