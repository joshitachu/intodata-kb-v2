---
feature: Track B -- Phase 3 input quality
slice: Slice 1 -- Phase 2 section parser fix (FR-B1)
sub_project: knowledge_base
critical_area: yes -- harness pipeline phases (/inspect required)
verdict: PENDING
approved_at:
---

## Problem Statement

`_parse_phase2_sections` (harness.py:1616) leaks 104,477 bytes per run into
`UNCLASSIFIED_OR_EXTRA_FINDINGS` via three defects:

1. **Heading level (biggest)**: The regex `r"^#{1,6}\s+([A-Z][A-Z0-9_ ]+)\s*$"` matches
   h1-h6 headings. Inside a RISK_FLAGS section, `#### HIGH`, `#### CRITICAL`, `#### MEDIUM`,
   `#### LOW` and unit-name headings like `#### UPLOAD_CRM_JDE_PDF` all match and
   trigger a section boundary change, booting the parser into ADDITIONAL_FINDINGS
   mid-section. ~6.8 KB per run.

2. **CROSS_CHUNK_FINDINGS not registered (73% of the leak)**: The harness itself generates
   `### CROSS_CHUNK_FINDINGS` in `_phase2_cross_chunk_synthesis` (harness.py:1733-1764).
   This is intentional output appended to the merged analysis for split groups. But the
   section name is not in `PHASE2_SECTION_NAMES`, so the parser routes all 75,950 bytes
   of cross-chunk synthesis content to ADDITIONAL_FINDINGS.

3. **Plugin ANALYSIS_GUIDE extra sections**: The lobster plugin's ANALYSIS_GUIDE mandates
   sections like `DEBUG_TESTING_EVIDENCE`, `CHANGE_IMPACT_EXTRACTION`,
   `ASSISTANT_DOC_OBLIGATIONS` that the harness has never registered. Content after these
   headers falls into ADDITIONAL_FINDINGS. ~21 KB per run.

Because `UNCLASSIFIED_OR_EXTRA_FINDINGS` is in `BASELINE_BUNDLE_SECTIONS`, all 104 KB
reaches every doc pack. This must be fixed BEFORE Slice 2 (BASELINE reduction), otherwise
removing UNCLASSIFIED from BASELINE would silently discard 76 KB of legitimate
cross-chunk synthesis and ~21 KB of plugin guide analysis.

## Proposed Solution

Three targeted fixes to `_parse_phase2_sections`, all additive:

**Fix 1 (heading level)**: Restrict the section boundary trigger to h3 only. Change
`r"^#{1,6}\s+..."` to `r"^### ([A-Z][A-Z0-9_ ]+)\s*$"`. Sub-headings at h4+ inside a
known section no longer break the owning section's parse context. Phase 2 prompts
(`prompts.py:88`) already instruct the LLM to use exactly `### NAME` for top-level
sections, so this correctly matches the intended protocol.

**Fix 2 (CROSS_CHUNK_FINDINGS)**: Add `"CROSS_CHUNK_FINDINGS"` to `PHASE2_SECTION_NAMES`
in `harness.py:112`. It is a real section intentionally produced by the harness. Adding
it to the tuple means the parser buckets its content correctly. Since no `doc_catalog.yaml`
entry lists it in `phase2_sections`, it will never appear in any doc pack — but it will
no longer bloat ADDITIONAL_FINDINGS.

**Fix 3 (plugin guide sections)**: Add an `extra_section_names: frozenset[str] = frozenset()`
parameter to `_parse_phase2_sections`. Add a module-level helper
`_guide_section_names(guide_path: Path) -> frozenset[str]` that reads the ANALYSIS_GUIDE
file and extracts all-caps `### HEADING` patterns. Thread this through the two classmethod
callers (`_deterministic_phase2_merge` and `_targeted_analysis_bundle_parts`) with a new
optional parameter, and populate it in the instance-method callers where `ctx` is available.

## Affected Files

- `mcp-server/src/mcp_server/providers/custom/harness.py`:
  - `PHASE2_SECTION_NAMES` (harness.py:112) -- add `"CROSS_CHUNK_FINDINGS"`
  - `_parse_phase2_sections` (harness.py:1616-1636) -- fix heading regex; add
    `extra_section_names` param; merge extra names into `section_names` set
  - `_deterministic_phase2_merge` (harness.py:1597-1613) -- add `extra_section_names`
    param; forward to `_parse_phase2_sections`
  - `_targeted_analysis_bundle_parts` (harness.py:2452-2473) -- add `extra_section_names`
    param; forward to `_parse_phase2_sections`
  - Instance method callers of `_targeted_analysis_bundle_parts` (`_phase28_bundles`
    harness.py:2335, and the repair context builder at harness.py:2448) -- compute guide
    section names from `ctx` and pass through
  - New module-level helper `_guide_section_names(guide_path: Path) -> frozenset[str]`
    (add near the top of `harness.py` or in `prompt_packs.py`)
- `mcp-server/tests/test_providers/test_custom_provider.py`:
  - New test `test_parse_phase2_sections_h4_heading_does_not_break_parent_section`
  - New test `test_parse_phase2_sections_cross_chunk_findings_is_named_section`
  - New test `test_parse_phase2_sections_extra_section_names_accepted`
  - Update existing `test_deterministic_phase2_merge_preserves_canonical_sections`
    (line 2274) if it needs to account for `CROSS_CHUNK_FINDINGS` in PHASE2_SECTION_NAMES

## Architecture Contracts

- `knowledge_base/CLAUDE.md` "Current Custom Harness Notes": parser behavior is not
  documented there; no update needed for this slice.
- `knowledge_base/.agent/system/mcp-workflow.md`: Phase 2.8 doc-pack assembly description
  is unchanged by this slice (BASELINE change is Slice 2).
- **Parser fix is strictly additive**: content that already parses into a named section
  must still parse into the same section after the fix. Only content that was landing in
  ADDITIONAL_FINDINGS should move to a named section.
- **`PHASE2_SECTION_NAMES` is iterated by `_deterministic_phase2_merge`** — adding
  CROSS_CHUNK_FINDINGS to it means the merge output loop will check for it. It will be
  empty for individual chunk inputs (which never have CROSS_CHUNK_FINDINGS), so the merge
  output is unchanged.
- **No change to `BASELINE_BUNDLE_SECTIONS`** in this slice — that is Slice 2.
- **No change to doc_catalog.yaml** -- that is Slice 2.
- **Track A pre-condition**: harness.py has uncommitted Track A FR-8 changes in
  `_phase3_context_files`. Track A must be committed before this slice starts.

## Implementation Steps

1. **Add `CROSS_CHUNK_FINDINGS` to `PHASE2_SECTION_NAMES`** (harness.py:112):
   ```python
   PHASE2_SECTION_NAMES = tuple(ANALYSIS_SECTION_NAMES) + (
       GROUP_SUMMARY_SECTION,
       "CROSS_CHUNK_FINDINGS",
       ADDITIONAL_FINDINGS_SECTION,
   )
   ```

2. **Fix heading level regex in `_parse_phase2_sections`** (harness.py:1621):
   Change `r"^#{1,6}\s+([A-Z][A-Z0-9_ ]+)\s*$"` to `r"^### ([A-Z][A-Z0-9_ ]+)\s*$"`.
   This is a one-line change. The `match.group(1)` reference stays the same.

3. **Add `extra_section_names` param to `_parse_phase2_sections`** (harness.py:1616):
   ```python
   @staticmethod
   def _parse_phase2_sections(
       text: str, extra_section_names: frozenset[str] = frozenset()
   ) -> dict[str, str]:
       section_names = set(PHASE2_SECTION_NAMES) | extra_section_names
       sections: dict[str, list[str]] = {name: [] for name in section_names}
       ...
   ```
   The `sections` dict initialization must also cover the extra names.

4. **Add `_guide_section_names` helper** (new function near `_parse_phase2_sections`):
   ```python
   def _guide_section_names(guide_path: Path) -> frozenset[str]:
       if not guide_path.exists():
           return frozenset()
       heading_re = re.compile(r"^### ([A-Z][A-Z0-9_ ]+)\s*$")
       names: set[str] = set()
       for line in guide_path.read_text(encoding="utf-8", errors="replace").splitlines():
           m = heading_re.match(line.strip())
           if m:
               candidate = re.sub(r"[^A-Z0-9_]+", "_", m.group(1).strip().upper()).strip("_")
               if candidate not in PHASE2_SECTION_NAMES:
                   names.add(candidate)
       return frozenset(names)
   ```

5. **Thread `extra_section_names` through `_deterministic_phase2_merge`**:
   Add `extra_section_names: frozenset[str] = frozenset()` param; pass to each
   `cls._parse_phase2_sections(text, extra_section_names)` call.

6. **Thread `extra_section_names` through `_targeted_analysis_bundle_parts`**:
   Add `extra_section_names: frozenset[str] = frozenset()` param; pass to each
   `cls._parse_phase2_sections(text, extra_section_names)` call.

7. **Compute guide section names in instance callers of `_targeted_analysis_bundle_parts`**:
   In `_phase28_bundles` (harness.py:2335) and any other instance-method caller of
   `_targeted_analysis_bundle_parts`, compute:
   ```python
   preprocess_dir = ctx.staging_dir / "_preprocess"
   guide_file = preprocess_dir / ctx.analysis_guide_file if ctx.analysis_guide_file else None
   extra = _guide_section_names(guide_file) if guide_file else frozenset()
   ```
   Pass `extra_section_names=extra` to the method call.

8. **Compute guide section names in `_phase2_analysis_group` caller of
   `_deterministic_phase2_merge`** (harness.py:1599): same pattern.

9. **Run pytest** — expect existing 590 tests to pass (CROSS_CHUNK_FINDINGS added to
   PHASE2_SECTION_NAMES may affect `test_deterministic_phase2_merge_preserves_canonical_sections`
   at line 2274 — update if needed).

10. **Add new tests** (see Verification Strategy).

## Verification Strategy

**New tests in `test_custom_provider.py`:**

- `test_parse_phase2_sections_h4_heading_does_not_break_parent_section`:
  Input: `"### RISK_FLAGS\n\nSome risk.\n\n#### HIGH\n\nHigh risk detail.\n#### LOW\n\nLow detail."`
  Assert: `result["RISK_FLAGS"]` contains `"High risk detail"` and `"Low detail"`;
  `result.get(ADDITIONAL_FINDINGS_SECTION, "")` is empty.

- `test_parse_phase2_sections_cross_chunk_findings_is_named_section`:
  Input: `"### COMPONENTS\n\ncomp content\n\n### CROSS_CHUNK_FINDINGS\n\ncross content"`
  Assert: `result["CROSS_CHUNK_FINDINGS"] == "cross content"`;
  `result.get(ADDITIONAL_FINDINGS_SECTION, "")` is empty;
  `result["COMPONENTS"] == "comp content"`.

- `test_parse_phase2_sections_extra_section_names_accepted`:
  Input: `"### COMPONENTS\n\ncomp\n\n### DEBUG_TESTING_EVIDENCE\n\ndebug content"`
  Call with `extra_section_names=frozenset({"DEBUG_TESTING_EVIDENCE"})`.
  Assert: `result["DEBUG_TESTING_EVIDENCE"] == "debug content"`;
  `result.get(ADDITIONAL_FINDINGS_SECTION, "")` is empty.

**Manual check on reference run artifacts:**
If `_analysis/` files are accessible from the last Koopmans run, parse them with
the updated `_parse_phase2_sections` and measure the UNCLASSIFIED size. Target:
< 5 KB per group file (from ~15 KB/group current).

## Out of Scope

- `BASELINE_BUNDLE_SECTIONS` change — Slice 2.
- `doc_catalog.yaml` 01/02 differentiation — Slice 2.
- Stopping pack 09 from being built — Slice 2.
- `_complete_core_generation` targeted dispatch — Slice 3.
- Manifest and CSV index removal from context — Slice 3.
- Track A uncommitted changes (FR-8 fallback removal) — must land before this slice.
