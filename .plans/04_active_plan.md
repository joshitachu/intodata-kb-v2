---
feature: Track B -- Phase 3 input quality
slice: Slice 3 -- Targeted dispatch + waste removal + docs (FR-B4, FR-B5, FR-B6, FR-B8)
sub_project: knowledge_base
critical_area: yes -- harness pipeline phases (`/inspect` required)
verdict: PASS
approved_at: 2026-06-11
---

## Problem Statement

After Slices 1 and 2, UNCLASSIFIED leakage is fixed and doc packs are differentiated.
Three remaining waste sources drive Phase 3 input token counts:

1. **Hint-only dispatch (non-plugin runs only)**: When Phase 3 runs with a
   tool-capable provider (non-plugin mode), `_complete_core_generation` sets
   `context_files = []` (line 2840), discarding the targeted doc pack built by
   Phase 2.8. The model must reconstruct all context via tool calls (O(n) tool
   round-trips). The targeted doc pack — now differentiated and correctly parsed
   after Slices 1+2 — should be pushed as a starter so the model begins grounded.
   Note: plugin mode (Koopmans, lobster) disables Phase 3 tools at line 727, so
   this change does NOT apply to the Koopmans acceptance run.

2. **Manifest in every Phase 3 call (all runs)**: `_phase3_context_files`
   appends `_doc_pack_manifest.json` (155 KB) to every Phase 3 call (lines
   4375-4377). The manifest is a debug index of what went into each pack. The
   model does not need it to write a document; the doc pack itself is the input.
   Removing it saves ~155 KB × 61 calls = ~9.4 MB per reference run.

3. **CSV indexes in every Phase 3 call (all runs where source_coverage absent)**:
   `_engineering_fact_context` (lines 4430 and 4435) adds `entity_index.csv` and
   `usage_index.csv` (combined ~3.1 MB) to Phase 3 context when
   `_source_coverage.json` is absent. These large index files are not needed in
   static context when the doc pack already contains the relevant Phase 2 evidence.
   Removing the CSVs from static context lets the model tool-read them on demand
   if it needs to; they remain on disk.

## Proposed Solution

**Change 1 — Targeted starter pack for tool-capable Phase 3 (line 2838-2840)**:
In `_complete_core_generation`, when `use_tools=True`, replace `context_files = []`
with a filter that keeps doc-pack files (`_doc_pack_` in name) up to a 100 KB cap.
If the doc pack exceeds 100 KB (unlikely after Slice 2 differentiation), fall back to
empty (preserve existing hint-only behavior). Phase 2 calls are unaffected because
their `context_files` never contain `_doc_pack_` files.

```python
_STARTER_PACK_BYTE_CAP = 100 * 1024
doc_packs = [f for f in context_files if "_doc_pack_" in f.name]
total_bytes = sum(f.stat().st_size for f in doc_packs if f.exists())
context_files = doc_packs if total_bytes <= _STARTER_PACK_BYTE_CAP else []
```

**Change 2 — Remove manifest from `_phase3_context_files` (lines 4375-4377)**:
Delete the three-line block that appends `_doc_pack_manifest.json`. The manifest
is a build-artifact index, not evidence. Its removal has no effect on doc quality.

**Change 3 — Remove CSV indexes from `_engineering_fact_context` (lines 4430, 4435)**:
Remove `entity_index.csv` and `usage_index.csv` from both list assignments:
- Line 4430: `names = ["engineering_facts.md", "sql_usage.md", "entity_index.csv", "usage_index.csv"]`
  → `names = ["engineering_facts.md", "sql_usage.md"]`
- Line 4435 (`03_DATA_FLOWS_AND_LINEAGE`): `names = ["engineering_facts.md", "data_flow_map.md", "entity_index.csv", "usage_index.csv"]`
  → `names = ["engineering_facts.md", "data_flow_map.md"]`

The `doc_name is None` branch (Phase 2 calls) is not touched — it already excludes
CSV files.

**Change 4 — Docs**: Update `knowledge_base/CLAUDE.md` (Current Custom Harness Notes)
and `knowledge_base/.agent/system/mcp-workflow.md` to document:
- BASELINE_BUNDLE_SECTIONS is GROUP_SUMMARY only (from Slice 2)
- Section parser scope (from Slice 1)
- Targeted starter pack dispatch + tool drill-down for non-plugin Phase 3
- Manifest and CSV indexes removed from static Phase 3 context

**Change 5 — Acceptance run**: Run LobsterProfileExportKoopmansTest on deepseek/deepseek-v4-pro.
Compare Phase 3 input token total against 5,126,576 baseline. Target: ≥40% reduction
(≤3,075,946 tokens). Spot-check at least 2 generated docs for quality regression.

## Affected Files

- `mcp-server/src/mcp_server/providers/custom/harness.py`
  - `_complete_core_generation` (lines 2838-2840): Change 1 (targeted dispatch)
  - `_phase3_context_files` (lines 4375-4377): Change 2 (manifest removal)
  - `_engineering_fact_context` (lines 4430, 4435): Change 3 (CSV removal)
- `mcp-server/tests/test_providers/test_custom_provider.py`
  - 4 new tests (see Verification Strategy)
- `knowledge_base/CLAUDE.md`
  - "Current Custom Harness Notes" section: document Slice 1+2+3 changes
- `knowledge_base/.agent/system/mcp-workflow.md`
  - Phase 3 row in Phase Table, and Phase 2.8 dispatch description

## Architecture Contracts

- **`knowledge_base/CLAUDE.md` "Current Custom Harness Notes"** (deferred from Slices 1+2):
  this slice must document BASELINE_BUNDLE_SECTIONS = GROUP_SUMMARY only, section
  parser scope (`PHASE2_SECTION_NAMES` + extra_section_names), targeted dispatch, and
  manifest/CSV removal.
- **`knowledge_base/.agent/system/mcp-workflow.md`** Phase Table: Phase 3 description
  should say "Pushes targeted doc pack (≤100 KB) as starter context; uses tools for
  evidence drill-down (non-plugin only)."
- **No intermediate files rule (global CLAUDE.md)**: CSV files are removed from static
  context, not deleted. They remain on disk for tool access. This is correct.
- **Plugin mode tool gate (harness.py line 727)**: Change 1 is unreachable for plugin
  runs — `_tool_policy` returns `enabled=False` for `self._plugin_mode and
  phase.startswith("phase3")`. The starter pack selection block will never execute for
  plugin mode. This is intentional — document in code comment.
- **Phase 2 tool path not regressed**: The `_doc_pack_` filter in Change 1 returns
  empty list for Phase 2 calls (no `_doc_pack_` in Phase 2 context_files), preserving
  existing Phase 2 hint-only behavior.
- **Slices 1 and 2 are pre-conditions**: Change 1 is only meaningful once doc packs
  are correctly parsed (Slice 1) and differentiated (Slice 2).

## Implementation Steps

1. **Change `_complete_core_generation` (lines 2838-2840)** — replace hint-only:
   ```python
   if use_tools:
       prompt = _with_agent_contract(prompt, task_contract)
       _STARTER_PACK_BYTE_CAP = 100 * 1024
       doc_packs = [f for f in context_files if "_doc_pack_" in f.name]
       total_bytes = sum(f.stat().st_size for f in doc_packs if f.exists())
       context_files = doc_packs if total_bytes <= _STARTER_PACK_BYTE_CAP else []
   ```
   The constant can be a module-level `_PHASE3_STARTER_PACK_BYTE_CAP = 100 * 1024`
   if preferred for testability. Either is acceptable.

2. **Remove manifest block from `_phase3_context_files` (lines 4375-4377)**:
   Delete:
   ```python
   manifest = analysis_dir / "_doc_pack_manifest.json"
   if manifest.exists():
       files.append(manifest)
   ```

3. **Remove CSV indexes from `_engineering_fact_context` (lines 4430 and 4435)**:
   - Line 4430: `names = ["engineering_facts.md", "sql_usage.md"]`
   - Line 4435 (03_DATA_FLOWS_AND_LINEAGE branch): `names = ["engineering_facts.md", "data_flow_map.md"]`

4. **Write 4 tests** in `test_custom_provider.py` (see Verification Strategy).

5. **Update `knowledge_base/CLAUDE.md`** "Current Custom Harness Notes":
   Add bullet points for BASELINE = GROUP_SUMMARY only, parser coverage
   (CROSS_CHUNK_FINDINGS registered, plugin guide headers via extra_section_names),
   Phase 3 targeted dispatch (non-plugin), and manifest/CSV removal.

6. **Update `knowledge_base/.agent/system/mcp-workflow.md`**:
   - Phase Table Phase 3 row: update "LLM calls" description to note starter pack.
   - Phase 2.8 row description: confirm "no LLM calls" still accurate.
   - Add a short note in Key Config about the targeted dispatch approach.

7. **Run pytest** — expect all 595 existing tests + 4 new tests passing.

8. **Acceptance run** on LobsterProfileExportKoopmansTest with deepseek/deepseek-v4-pro.
   Measure total Phase 3 input tokens and compare to 5,126,576 baseline.
   Spot-check docs 01 and 02 for quality (verify catalog section changes from Slice 2
   did not degrade content; see Slice 2 inspection WARN 2).

## Verification Strategy

**Tests (all in `test_custom_provider.py`):**

1. `test_complete_core_generation_tool_path_pushes_starter_pack`:
   Create a mock provider that supports tools. Call `_complete_core_generation` with
   `context_files` containing a small `_doc_pack_testdoc.md` temp file (< 100 KB) and
   a non-pack file. Assert the call to the underlying provider receives only the doc_pack
   file (non-pack file excluded). Use `unittest.mock.AsyncMock` for the provider.

2. `test_complete_core_generation_tool_path_empty_when_pack_too_large`:
   Same setup but doc_pack temp file is > 100 KB. Assert the provider call receives
   `context_files=[]` (fallback to hint-only).

3. `test_phase3_context_files_excludes_manifest`:
   Create a staging dir with a real `_doc_pack_manifest.json` present. Call
   `_phase3_context_files(ctx, "01_ARCHITECTURE_MAP")`. Assert no returned path has
   `_doc_pack_manifest.json` as its name.

4. `test_engineering_fact_context_excludes_csv_indexes`:
   Create a staging dir with both `entity_index.csv` and `usage_index.csv` present.
   Call `_engineering_fact_context(ctx, "01_ARCHITECTURE_MAP")`. Assert neither CSV
   appears in the returned list. Also assert `engineering_facts.md` IS returned when
   present (to verify the method still functions).

**Acceptance run gate** (Koopmans, deepseek): Token total ≤ 3,075,946 (≥40% reduction
from 5,126,576 baseline). Quality: at least docs 01, 02, 03 spot-checked for evidence
coverage.

## Out of Scope

- Phase 2 analysis context changes — untouched.
- Plugin pack evidence_profiles — untouched.
- Any changes to Phase 1 investigation or Phase 2.6 scorecard.
- Adding CSV files back via tools — they remain on disk for tool access, no code needed.
- Changing `_BUNDLE_CATEGORY_TO_PHASE2_SECTIONS` or evidence profile mappings.
- Changing how manifest is written (Phase 2.8 still writes it for debug/forensic use).
